import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowUp, ChevronDown, Square } from "lucide-react";
import { api, type ChatMessage, type Soul } from "../api";
import { useApp } from "../state";
import { RunBlock } from "../components/RunBlock";
import { ErrorBox } from "../components/ui";

const SUGGESTIONS = [
  "Analyze this programming data: solved 12 DP, 30 greedy, 4 graph problems this month",
  "I need an agent that analyzes my programming learning progress and makes a weekly plan",
  "Fix the failing tests in /workspace/demo and explain what you changed",
  "Research the best way to learn segment trees and summarize it",
];

/** Per-conversation soul picker chip (Hermes-style /personality, OpenClaw SOUL.md). */
function SoulChip({ souls, value, onChange }: { souls: Soul[]; value: string; onChange: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const cur = souls.find((s) => s.id === value) || souls.find((s) => s.default) || souls[0];
  if (!cur) return null;
  return (
    <div className="relative">
      <button className="chip hover:bg-hover text-xs" onClick={() => setOpen((o) => !o)} title="Soul (personality) for this conversation" data-testid="soul-chip">
        <span>{cur.icon}</span> {cur.name} <ChevronDown size={12} />
      </button>
      {open && (
        <div className="absolute bottom-8 left-0 z-20 card shadow-lg p-1 w-64 max-h-72 overflow-y-auto">
          {souls.map((s) => (
            <button
              key={s.id}
              className={`w-full text-left rounded-lg px-2 py-1.5 text-sm hover:bg-hover ${s.id === cur.id ? "bg-hover" : ""}`}
              onClick={() => {
                onChange(s.id);
                setOpen(false);
              }}
            >
              <div>
                {s.icon} {s.name}
              </div>
              <div className="text-[11px] text-muted line-clamp-1">{s.description}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Chat() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const convId = params.get("c") || "";
  const { refreshConversations, identity } = useApp();
  const [souls, setSouls] = useState<Soul[]>([]);
  const [soul, setSoul] = useState("");
  const [model, setModel] = useState<string>(() => {
    try {
      return localStorage.getItem("chat-model") || "auto";
    } catch {
      return "auto";
    }
  });
  const [models, setModels] = useState<string[]>([]);
  const [modes, setModes] = useState<{ id: string; name: string; group: string; description: string }[]>([]);
  const [modeIdx, setModeIdx] = useState(0);
  useEffect(() => {
    fetch("/api/modes").then((r) => r.json()).then((d) => setModes(Array.isArray(d) ? d : [])).catch(() => {});
  }, []);
  const [effort, setEffort] = useState<number>(-1);   // -1 = AI decides; set by YOU at the start, never changed mid-run
  useEffect(() => {
    fetch("/api/models").then((r) => r.json()).then((d) => setModels(d.models || [])).catch(() => {});
  }, []);
  const pickModel = (m: string) => {
    setModel(m);
    try {
      localStorage.setItem("chat-model", m);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    api.souls().then(setSouls).catch(() => {});
  }, []);

  useEffect(() => {
    if (!convId) {
      setSoul("");
      return;
    }
    api.conversation(convId).then((c) => setSoul(c.soul || "")).catch(() => {});
  }, [convId]);

  const changeSoul = (id: string) => {
    setSoul(id);
    if (convId) api.setConversationSoul(convId, id).catch(() => {});
  };
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const skipLoad = useRef(false);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (skipLoad.current) {
      skipLoad.current = false;
      return;
    }
    setError(null);
    if (!convId) {
      setMessages([]);
      return;
    }
    api
      .conversation(convId)
      .then((c) => {
        setMessages(c.messages);
        setBusy(c.messages.some((m: any) => m.live));   // re-attach to a run that is still working
        // jump to the latest message; repeat while run blocks finish loading their content
        [0, 150, 500, 1200].forEach((ms) =>
          setTimeout(() => bottom.current?.scrollIntoView({ behavior: "auto", block: "end" }), ms));
      })
      .catch(setError);
  }, [convId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  }, [input]);

  const send = async (text?: string) => {
    const msg = (text ?? input).trim();
    if (!msg) return;
    if (busy) {   // talk to the running task: it changes course instead of starting a new one
      const live = [...messages].reverse().find((m: any) => m.run_id);
      if (live?.run_id) {
        setInput("");
        const r = await api.steerRun(live.run_id, msg).catch(() => ({ ok: false }));
        if (r.ok) return;
        setBusy(false);   // it had already finished: send as a normal new message below
      } else return;
    }
    setBusy(true);
    setError(null);
    setInput("");
    const now = Date.now() / 1000;
    setMessages((m) => [...m, { role: "user", content: msg, ts: now }]);
    try {
      const r = await api.chat(msg, convId || undefined, !convId ? soul : undefined, model, effort);
      setMessages((m) => [...m, { role: "assistant", content: "", run_id: r.run_id, ts: now }]);
      if (!convId) {
        skipLoad.current = true;
        nav(`/?c=${r.conversation_id}`, { replace: true });
      }
      refreshConversations();
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  const empty = messages.length === 0;
  // `/` autocomplete: the word being typed when it starts with "/" (modes can be stacked: /plan /risk-assessment ...)
  const slashWord = (/(?:^|\s)\/([\w-]*)$/.exec(input) || [])[1];
  const modeHits = slashWord === undefined ? [] : [
    ...(("modes".startsWith(slashWord) || "souls".startsWith(slashWord))
      ? [{ id: "modes", name: "List all modes", group: "", description: "Show the 100 modes" },
         { id: "souls", name: "List souls", group: "", description: "Personalities for this chat" }] : []),
    ...modes.filter((m) => m.id.includes(slashWord.toLowerCase()) || m.name.toLowerCase().includes(slashWord.toLowerCase())),
  ].slice(0, 8);
  const pickMode = (id: string) => {
    setInput((t) => t.replace(/\/[\w-]*$/, "/" + id + " "));
    setModeIdx(0);
    taRef.current?.focus();
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto">
        {empty ? (
          <div className="h-full flex flex-col items-center justify-center px-4">
            <h1 className="text-3xl font-semibold mb-2 text-center">What should your agents do?</h1>
            <p className="text-muted text-sm mb-8 text-center">
              {identity.emoji} {identity.name} plans, delegates to specialists, and builds new agents when needed.
            </p>
            <div className="grid sm:grid-cols-2 gap-2 max-w-2xl w-full">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="card p-3 text-left text-sm hover:bg-hover" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="max-w-3xl mx-auto px-4 py-8 space-y-8">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="bg-hover rounded-3xl px-4 py-2.5 max-w-[80%] whitespace-pre-wrap">{m.content}</div>
                </div>
              ) : (
                <div key={i}>
                  {m.run_id ? (
                    <RunBlock runId={m.run_id} fallbackText={m.content} onDone={() => i === messages.length - 1 && setBusy(false)} />
                  ) : (
                    <div className="whitespace-pre-wrap">{m.content}</div>
                  )}
                </div>
              ),
            )}
            <div ref={bottom} />
          </div>
        )}
      </div>
      <div className="px-4 pb-4">
        <div className="max-w-3xl mx-auto">
          <ErrorBox error={error} />
          {modeHits.length > 0 && (
            <div className="card shadow-lg mb-2 p-1 max-h-72 overflow-y-auto" data-testid="mode-menu">
              {modeHits.map((m, i) => (
                <button key={m.id} onMouseDown={(e) => { e.preventDefault(); pickMode(m.id); }}
                        className={`w-full text-left rounded-lg px-3 py-1.5 text-sm flex gap-3 items-baseline ${i === modeIdx ? "bg-hover" : "hover:bg-hover"}`}>
                  <span className="mono text-xs shrink-0">/{m.id}</span>
                  <span className="text-muted text-xs truncate">{m.description}</span>
                  <span className="ml-auto text-[10px] text-muted shrink-0">{m.group}</span>
                </button>
              ))}
            </div>
          )}
          <div className="card rounded-3xl flex items-end gap-2 p-2 pl-4 bg-bg">
            <textarea
              ref={taRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (modeHits.length) {
                  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                    e.preventDefault();
                    setModeIdx((i) => (i + (e.key === "ArrowDown" ? 1 : modeHits.length - 1)) % modeHits.length);
                    return;
                  }
                  if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
                    e.preventDefault();
                    pickMode(modeHits[Math.min(modeIdx, modeHits.length - 1)].id);
                    return;
                  }
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder={busy ? "Working… type to change its course (e.g. \"make the header blue, skip the tests\")" : `Message ${identity.name}…`}
              className="flex-1 resize-none bg-transparent outline-none py-2 text-[15px]"
            />
            {busy && input.trim() && (
              <button
                className="h-9 shrink-0 rounded-full px-3 flex items-center gap-1.5 justify-center bg-accent text-accent-fg text-sm"
                onClick={() => send()}
                title="Send to the running task — it changes course without starting over"
                data-testid="steer-button"
              >
                <ArrowUp size={16} /> Tell it
              </button>
            )}
            {busy ? (
              <button
                className="h-9 shrink-0 rounded-full px-3 flex items-center gap-1.5 justify-center bg-red-600 text-white text-sm font-medium"
                onClick={() => {
                  const live = [...messages].reverse().find((m: any) => m.run_id);
                  if (live?.run_id) api.cancelRun(live.run_id).catch(() => {});
                  setBusy(false);
                }}
                title="Stop the running task"
                data-testid="stop-button"
              >
                <Square size={12} fill="currentColor" /> Stop
              </button>
            ) : (
              <button
                className="h-9 w-9 shrink-0 rounded-full flex items-center justify-center bg-accent text-accent-fg disabled:opacity-30"
                disabled={!input.trim()}
                onClick={() => send()}
                title="Send"
              >
                <ArrowUp size={18} />
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 mt-2">
            <SoulChip souls={souls} value={soul} onChange={changeSoul} />
            <select className="chip text-xs bg-transparent" value={effort} onChange={(e) => setEffort(Number(e.target.value))}
                    title="Effort for this request — you set it at the start; the AI never changes it mid-run. Auto = AI decides."
                    data-testid="effort-picker">
              <option value={-1}>⚡ Effort: Auto</option>
              <option value={0}>Quick</option>
              <option value={1}>Light</option>
              <option value={2}>Standard</option>
              <option value={3}>Deep</option>
              <option value={4}>Max</option>
            </select>
            <select
              className="chip text-xs bg-transparent max-w-48"
              value={model}
              onChange={(e) => pickModel(e.target.value)}
              title="Model for your messages — Auto = the smart router picks per step; or choose one yourself"
              data-testid="model-picker"
            >
              <option value="auto">🤖 Auto (smart router)</option>
              {models.filter((m) => !m.startsWith("aihorde/") && !m.startsWith("veo")).map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <div className="text-[11px] text-muted flex-1 text-center">Auto mode approves routine actions; deleting, payments and passwords always ask.</div>
          </div>
        </div>
      </div>
    </div>
  );
}
