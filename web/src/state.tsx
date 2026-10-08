// Global app state: agent registry (live), pending approvals, health, conversations, toasts.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type AgentSpec, type Approval, type Conversation, type Health, type Identity } from "./api";
import { socket, useChannel, useSocketStatus } from "./ws";

export interface Toast {
  id: number;
  title: string;
  body?: string;
  link?: string;
  tone?: "info" | "warn" | "error" | "ok";
}

interface AppState {
  agents: AgentSpec[];
  names: Record<string, string>;
  refreshAgents: () => void;
  approvals: Approval[];
  refreshApprovals: () => void;
  health: Health | null;
  backendUp: boolean;
  wsUp: boolean;
  conversations: Conversation[];
  refreshConversations: () => void;
  toasts: Toast[];
  toast: (t: Omit<Toast, "id">) => void;
  dismiss: (id: number) => void;
  /** user-changeable assistant identity (IDENTITY.md) — shown instead of the product name */
  identity: Identity;
  setIdentity: (i: Identity) => void;
}

const DEFAULT_IDENTITY: Identity = { name: "AgentOS", emoji: "✨", vibe: "" };

const Ctx = createContext<AppState | null>(null);

export function useApp() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useApp outside provider");
  return c;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [agents, setAgents] = useState<AgentSpec[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [backendUp, setBackendUp] = useState(true);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [identity, setIdentity] = useState<Identity>(DEFAULT_IDENTITY);
  const wsUp = useSocketStatus();

  useEffect(() => {
    if (health?.identity?.name) setIdentity(health.identity);
  }, [health?.identity?.name, health?.identity?.emoji, health?.identity?.vibe]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    document.title = identity.name || "AgentOS";
  }, [identity.name]);

  useChannel("identity", (i: Identity) => i?.name && setIdentity(i));

  const toast = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((p) => [...p, { ...t, id }]);
    window.setTimeout(() => setToasts((p) => p.filter((x) => x.id !== id)), 7000);
  }, []);
  const dismiss = useCallback((id: number) => setToasts((p) => p.filter((x) => x.id !== id)), []);

  const refreshAgents = useCallback(() => {
    api.agents().then(setAgents).catch(() => {});
  }, []);
  const refreshApprovals = useCallback(() => {
    api.approvals("pending").then(setApprovals).catch(() => {});
  }, []);
  const refreshConversations = useCallback(() => {
    api.conversations().then(setConversations).catch(() => {});
  }, []);

  useEffect(() => {
    socket.start();
    let alive = true;
    const poll = () =>
      api
        .health()
        .then((h) => {
          if (!alive) return;
          setHealth(h);
          setBackendUp(true);
        })
        .catch(() => alive && setBackendUp(false));
    poll();
    refreshAgents();
    refreshApprovals();
    refreshConversations();
    const t = window.setInterval(poll, 15000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [refreshAgents, refreshApprovals, refreshConversations]);

  // when the socket (re)connects, resync everything
  useEffect(() => {
    if (wsUp) {
      setBackendUp(true);
      refreshAgents();
      refreshApprovals();
    }
  }, [wsUp, refreshAgents, refreshApprovals]);

  useChannel("registry", (d: { action: string; agent: AgentSpec }) => {
    setAgents((prev) => {
      if (d.action === "deleted") return prev.filter((a) => a.id !== d.agent.id);
      const i = prev.findIndex((a) => a.id === d.agent.id);
      if (i === -1) return [...prev, d.agent];
      const copy = [...prev];
      copy[i] = { ...copy[i], ...d.agent };
      return copy;
    });
    if (d.action === "created") toast({ title: `New agent: ${d.agent.icon} ${d.agent.name}`, body: d.agent.description, link: `/agents/${d.agent.id}`, tone: "ok" });
  });

  useChannel("approval", (a: Approval) => {
    setApprovals((prev) => {
      const rest = prev.filter((x) => x.id !== a.id);
      return a.status === "pending" ? [a, ...rest] : rest;
    });
    if (a.status === "pending") toast({ title: "Approval needed", body: a.title + " — approve it in the chat or Approvals page", link: "/approvals", tone: "warn" });
  });

  const names = useMemo(() => {
    const m: Record<string, string> = { main_agent: "Main Agent", agent_builder: "Agent Builder" };
    agents.forEach((a) => (m[a.id] = a.name));
    return m;
  }, [agents]);

  const value: AppState = {
    agents,
    names,
    refreshAgents,
    approvals,
    refreshApprovals,
    health,
    backendUp,
    wsUp,
    conversations,
    refreshConversations,
    toasts,
    toast,
    dismiss,
    identity,
    setIdentity,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
