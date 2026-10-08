import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Blocks, Bot, Brain, CalendarClock, Globe, Hammer, History, Home, Menu, MessageSquare, Moon, PenSquare,
  Settings, ShieldCheck, Smile, Sun, Trash2, Wrench, X, Zap, ChevronDown, ChevronRight, FlaskConical,
} from "lucide-react";
import { useApp } from "../state";
import WorkingMascot from "./WorkingMascot";
import { api } from "../api";

const PRIMARY = [
  { to: "/", label: "Chat", icon: MessageSquare, end: true },
  { to: "/feed", label: "Home feed", icon: Home },
  { to: "/agents", label: "Agents", icon: Bot },
];
const NAV = [
  { to: "/builder", label: "Builder", icon: Hammer },
  { to: "/runs", label: "Runs", icon: History },
  { to: "/approvals", label: "Approvals", icon: ShieldCheck, badge: true },
  { to: "/browser", label: "Browser", icon: Globe },
  { to: "/memory", label: "Memory", icon: Brain },
  { to: "/schedules", label: "Schedules", icon: CalendarClock },
  { to: "/tests", label: "Tests", icon: FlaskConical },
  { to: "/skills", label: "Skills", icon: Blocks },
  { to: "/tools", label: "Tools", icon: Wrench },
  { to: "/souls", label: "Souls", icon: Smile },
  { to: "/settings", label: "Settings", icon: Settings },
];

function useTheme() {
  const [theme, setTheme] = useState<string>(() => {
    try {
      return localStorage.getItem("theme") || "";
    } catch {
      return "";
    }
  });
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    try {
      if (theme) localStorage.setItem("theme", theme);
      else localStorage.removeItem("theme");
    } catch {
      /* ignore */
    }
  }, [theme]);
  const isDark = theme ? theme === "dark" : window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  return { isDark, toggle: () => setTheme(isDark ? "light" : "dark") };
}

/** Collapsible sidebar section; open/closed is remembered per browser. */
function useCollapsed(key: string, initial: boolean) {
  const [open, setOpen] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem("sb:" + key);
      return v === null ? initial : v === "1";
    } catch {
      return initial;
    }
  });
  const toggle = () =>
    setOpen((o) => {
      try {
        localStorage.setItem("sb:" + key, o ? "0" : "1");
      } catch {
        /* ignore */
      }
      return !o;
    });
  return [open, toggle] as const;
}

function SectionHeader({ label, open, onToggle, extra }: { label: string; open: boolean; onToggle: () => void; extra?: any }) {
  return (
    <button onClick={onToggle} className="w-full flex items-center gap-1 px-4 py-1 text-xs font-medium text-muted hover:text-fg">
      {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      <span className="flex-1 text-left">{label}</span>
      {extra}
    </button>
  );
}

function Sidebar({ onNavigate }: { onNavigate: () => void }) {
  const { approvals, conversations, health, identity, refreshConversations } = useApp();
  const nav = useNavigate();
  const loc = useLocation();
  const { isDark, toggle } = useTheme();
  const activeConv = new URLSearchParams(loc.search).get("c");
  const [toolsOpen, toggleTools] = useCollapsed("workspace", false);
  const [convOpen, toggleConv] = useCollapsed("conversations", true);
  const Item = ({ n }: { n: any }) => (
    <NavLink
      to={n.to}
      end={n.end}
      onClick={onNavigate}
      className={({ isActive }) => `flex items-center gap-2.5 rounded-lg px-3 py-1.5 text-sm ${isActive ? "bg-hover font-medium" : "hover:bg-hover"}`}
    >
      <n.icon size={16} />
      <span className="flex-1">{n.label}</span>
      {n.badge && approvals.length > 0 && <span className="rounded-full bg-amber-500 text-white text-[11px] px-1.5 min-w-5 text-center">{approvals.length}</span>}
    </NavLink>
  );
  return (
    <aside className="flex h-full w-64 shrink-0 flex-col bg-sidebar border-r border-border">
      <div className="flex items-center gap-2 px-4 h-14">
        <Link to="/souls" onClick={onNavigate} className="flex items-center gap-2 min-w-0" title={identity.vibe || "Edit identity"}>
          <span className="text-lg leading-none">{identity.emoji}</span>
          <span className="font-semibold truncate" data-testid="assistant-name">{identity.name}</span>
        </Link>
        <div className="flex-1" />
        <button className="p-1.5 rounded-lg hover:bg-hover" title="New chat" onClick={() => { nav("/"); onNavigate(); }}>
          <PenSquare size={17} />
        </button>
      </div>
      <nav className="px-2 space-y-0.5">
        {PRIMARY.map((n) => <Item key={n.to} n={n} />)}
      </nav>
      <div className="mt-2">
        <SectionHeader label="Workspace" open={toolsOpen} onToggle={toggleTools}
          extra={!toolsOpen && approvals.length > 0 && <span className="rounded-full bg-amber-500 text-white text-[10px] px-1.5">{approvals.length}</span>} />
        {toolsOpen && <nav className="px-2 space-y-0.5">{NAV.map((n) => <Item key={n.to} n={n} />)}</nav>}
      </div>
      <div className="mt-2">
        <SectionHeader label={`Conversations${conversations.length ? ` (${conversations.length})` : ""}`} open={convOpen} onToggle={toggleConv} />
      </div>
      <div className="flex-1 overflow-y-auto px-2 mt-1 space-y-0.5">
        {convOpen && conversations.length === 0 && <div className="px-3 py-2 text-xs text-muted">No conversations yet</div>}
        {convOpen && conversations.map((c) => (
          <div key={c.id} className={`group flex items-center rounded-lg ${activeConv === c.id ? "bg-hover" : "hover:bg-hover"}`}>
            <Link to={`/?c=${c.id}`} onClick={onNavigate} className="flex-1 min-w-0 truncate px-3 py-1.5 text-sm">
              {c.title || "Untitled"}
            </Link>
            <button
              className="p-1.5 mr-1 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 text-muted hover:text-red-600"
              title="Delete chat"
              aria-label={`Delete chat ${c.title || ""}`}
              onClick={(e) => {
                e.preventDefault();
                if (!window.confirm(`Delete "${c.title || "Untitled"}"?`)) return;
                fetch(`/api/conversations/${c.id}`, { method: "DELETE" })
                  .then(() => {
                    refreshConversations();
                    if (activeConv === c.id) nav("/");
                  })
                  .catch(() => {});
              }}
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
      </div>
      <AutoModeToggle />
      <div className="border-t border-border p-3 flex items-center gap-2 text-xs text-muted">
        <Zap size={13} className={health?.llm?.mode === "openai" ? "text-green-600" : "text-amber-500"} />
        <span className="truncate flex-1" title={health?.llm?.error || ""}>
          {health ? (health.llm.mode === "openai" ? health.llm.model || "LLM connected" : "Offline mode (no LLM)") : "…"}
        </span>
        <button className="p-1.5 rounded-lg hover:bg-hover" onClick={toggle} title="Toggle theme">
          {isDark ? <Sun size={14} /> : <Moon size={14} />}
        </button>
      </div>
    </aside>
  );
}

export const AUTO_MODE_TIP =
  "Approves actions automatically; dangerous actions (delete/push/payments/passwords) still ask; never controls your screen while you're using the PC";

/** Auto mode switch (setting `auto_mode`). Shared with Settings; re-reads on focus so both stay in sync. */
export function AutoModeToggle({ compact = true }: { compact?: boolean }) {
  const [on, setOn] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () =>
    api
      .settings()
      .then((s) => setOn(s.auto_mode !== false))
      .catch(() => setOn(null));
  useEffect(() => {
    load();
    const f = () => load();
    window.addEventListener("focus", f);
    window.addEventListener("aos:settings", f);
    return () => {
      window.removeEventListener("focus", f);
      window.removeEventListener("aos:settings", f);
    };
  }, []);
  const flip = () => {
    if (on === null) return;
    setBusy(true);
    api
      .saveSettings({ auto_mode: !on })
      .then((s) => {
        setOn(s.auto_mode !== false);
        window.dispatchEvent(new Event("aos:settings"));
      })
      .finally(() => setBusy(false));
  };
  return (
    <div className={compact ? "border-t border-border px-3 py-2" : ""}>
      <button
        className="w-full flex items-center gap-2 text-xs rounded-lg px-2 py-1.5 hover:bg-hover disabled:opacity-50"
        onClick={flip}
        disabled={busy || on === null}
        title={AUTO_MODE_TIP}
        data-testid="auto-mode-toggle"
        aria-pressed={!!on}
      >
        <span className={`relative inline-block w-7 h-4 rounded-full transition ${on ? "bg-green-600" : "bg-gray-400"}`}>
          <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-all ${on ? "left-3.5" : "left-0.5"}`} />
        </span>
        <span className="flex-1 text-left" data-testid="auto-mode-label">
          Auto mode: {on === null ? "…" : on ? "on" : "off"}
        </span>
      </button>
    </div>
  );
}

function Toasts() {
  const { toasts, dismiss } = useApp();
  return (
    <div className="fixed bottom-4 right-4 z-50 space-y-2 w-80 max-w-[calc(100vw-2rem)]">
      {toasts.map((t) => (
        <div key={t.id} className="card p-3 shadow-lg flex gap-2 text-sm" style={{ borderColor: t.tone === "warn" ? "#d97706" : t.tone === "error" ? "#dc2626" : undefined }}>
          <div className="flex-1 min-w-0">
            <div className="font-medium">{t.link ? <Link to={t.link} onClick={() => dismiss(t.id)} className="hover:underline">{t.title}</Link> : t.title}</div>
            {t.body && <div className="text-muted text-xs mt-0.5 line-clamp-2">{t.body}</div>}
          </div>
          <button onClick={() => dismiss(t.id)} className="text-muted hover:text-fg self-start">
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function Layout() {
  const { backendUp, wsUp, identity } = useApp();
  const [mobileOpen, setMobileOpen] = useState(false);
  return (
    <div className="flex h-full">
      <div className="hidden md:block h-full">
        <Sidebar onNavigate={() => {}} />
      </div>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden flex">
          <Sidebar onNavigate={() => setMobileOpen(false)} />
          <div className="flex-1 bg-black/40" onClick={() => setMobileOpen(false)} />
        </div>
      )}
      <main className="flex-1 min-w-0 flex flex-col h-full">
        <div className="md:hidden flex items-center gap-2 px-4 h-12 border-b border-border">
          <button onClick={() => setMobileOpen(true)}>
            <Menu size={18} />
          </button>
          <span className="font-semibold">{identity.emoji} {identity.name}</span>
        </div>
        {!backendUp && (
          <div className="bg-red-600 text-white text-sm px-4 py-1.5 text-center">
            Backend unreachable at /api. Start it with <span className="mono">agentos start</span>. Retrying…
          </div>
        )}
        {backendUp && !wsUp && <div className="bg-amber-500 text-white text-xs px-4 py-1 text-center">Live updates reconnecting…</div>}
        <div className="flex-1 min-h-0 overflow-y-auto">
          <Outlet />
        </div>
      </main>
      <Toasts />
      <WorkingMascot />
    </div>
  );
}
