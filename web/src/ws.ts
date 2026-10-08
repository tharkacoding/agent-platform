// WebSocket client with exponential-backoff reconnect + SSE fallback for single runs (CONTRACTS §2).
import { useEffect, useRef, useState } from "react";
import { api, type RunEvent } from "./api";

export type Channel = "run" | "registry" | "approval" | "feed" | "identity";
type Handler = (data: any) => void;

class Socket {
  private ws: WebSocket | null = null;
  private handlers = new Map<Channel, Set<Handler>>();
  private statusHandlers = new Set<(up: boolean) => void>();
  private delay = 1000;
  private timer: number | undefined;
  connected = false;

  start() {
    if (this.ws) return;
    this.connect();
  }

  private connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let ws: WebSocket;
    try {
      ws = new WebSocket(`${proto}://${location.host}/ws`);
    } catch {
      this.schedule();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.delay = 1000;
      this.setStatus(true);
    };
    ws.onmessage = (m) => {
      try {
        const msg = JSON.parse(m.data);
        this.handlers.get(msg.channel)?.forEach((h) => h(msg.data));
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = () => {
      this.ws = null;
      this.setStatus(false);
      this.schedule();
    };
    ws.onerror = () => ws.close();
  }

  private schedule() {
    window.clearTimeout(this.timer);
    this.timer = window.setTimeout(() => this.connect(), this.delay);
    this.delay = Math.min(this.delay * 2, 30000);
  }

  private setStatus(up: boolean) {
    this.connected = up;
    this.statusHandlers.forEach((h) => h(up));
  }

  on(ch: Channel, h: Handler) {
    if (!this.handlers.has(ch)) this.handlers.set(ch, new Set());
    this.handlers.get(ch)!.add(h);
    return () => {
      this.handlers.get(ch)!.delete(h);
    };
  }

  onStatus(h: (up: boolean) => void) {
    this.statusHandlers.add(h);
    return () => {
      this.statusHandlers.delete(h);
    };
  }
}

export const socket = new Socket();

export function useChannel(ch: Channel, h: Handler) {
  const ref = useRef(h);
  ref.current = h;
  useEffect(() => socket.on(ch, (d) => ref.current(d)), [ch]);
}

export function useSocketStatus() {
  const [up, setUp] = useState(socket.connected);
  useEffect(() => socket.onStatus(setUp), []);
  return up;
}

/**
 * Live events for one run: loads history via REST, then follows WS. If the WS is down it
 * falls back to SSE (/api/runs/{id}/events). Deduplicates by seq.
 */
export function useRunEvents(runId: string | null | undefined) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [done, setDone] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const seen = useRef(new Set<number>());

  useEffect(() => {
    seen.current = new Set();
    setEvents([]);
    setDone(false);
    setLoaded(false);
    if (!runId) return;
    let cancelled = false;
    let es: EventSource | null = null;

    const add = (evs: RunEvent[]) => {
      const fresh = evs.filter((e) => e.run_id === runId && !seen.current.has(e.seq));
      if (!fresh.length) return;
      fresh.forEach((e) => seen.current.add(e.seq));
      setEvents((prev) => [...prev, ...fresh].sort((a, b) => a.seq - b.seq));
      if (fresh.some((e) => e.type === "final" || (e.type === "run" && (e.status === "done" || e.status === "failed")))) setDone(true);
    };

    const off = socket.on("run", (e: RunEvent) => add([e]));

    api
      .run(runId)
      .then((r) => {
        if (cancelled) return;
        add(r.events || []);
        if (r.status !== "running") setDone(true);
        setLoaded(true);
      })
      .catch(() => setLoaded(true));

    const startSse = () => {
      if (es || cancelled) return;
      es = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
      es.onmessage = (m) => {
        try {
          add([JSON.parse(m.data)]);
        } catch {
          /* ignore */
        }
      };
      es.addEventListener("end", () => {
        setDone(true);
        es?.close();
      });
      es.onerror = () => {
        es?.close();
        es = null;
      };
    };
    if (!socket.connected) startSse();
    const offStatus = socket.onStatus((up) => {
      if (!up) startSse();
    });

    return () => {
      cancelled = true;
      off();
      offStatus();
      es?.close();
    };
  }, [runId]);

  return { events, done, loaded };
}
