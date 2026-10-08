import { type CSSProperties, type ReactNode, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { ThinkingOrb } from "thinking-orbs";
import type { AgentType } from "../api";
import { type OrbSpec, orbForAgentId } from "./orbState";

export const TYPE_BADGE: Record<AgentType, { dot: string; label: string }> = {
  prebuilt: { dot: "🟢", label: "Prebuilt" },
  custom: { dot: "🔵", label: "Custom" },
  system: { dot: "🟣", label: "System" },
  user: { dot: "🟠", label: "User created" },
  imported: { dot: "⚪", label: "Imported" },
};

export function TypeBadge({ type }: { type: AgentType }) {
  const b = TYPE_BADGE[type] ?? { dot: "⚪", label: type };
  return (
    <span className="chip">
      <span className="text-[9px]">{b.dot}</span>
      {b.label}
    </span>
  );
}

const STATUS_COLOR: Record<string, string> = {
  active: "#16a34a",
  success: "#16a34a",
  done: "#16a34a",
  approved: "#16a34a",
  running: "#2563eb",
  waiting: "#d97706",
  pending: "#d97706",
  pending_approval: "#d97706",
  partial: "#d97706",
  draft: "#6b7280",
  disabled: "#6b7280",
  info: "#6b7280",
  failed: "#dc2626",
  denied: "#dc2626",
  cancelled: "#dc2626",
  pass: "#16a34a",
  fail: "#dc2626",
  error: "#dc2626",
  skipped: "#6b7280",
};

export function StatusPill({ status }: { status: string }) {
  const c = STATUS_COLOR[status] ?? "#6b7280";
  return (
    <span className="chip" style={{ borderColor: c + "55", color: c }}>
      {status === "running" ? (
        <Orb spec={orbForAgentId("")} size={20} style={{ margin: "-4px -2px" }} />
      ) : (
        <span style={{ width: 6, height: 6, borderRadius: 9, background: c }} />
      )}
      {status.replace("_", " ")}
    </span>
  );
}

/** thinking-orbs indicator. 64 = chat-avatar scale, 20 = inline scale; theme follows data-theme / OS. */
export function Orb({ spec, size = 20, className, style }: { spec: OrbSpec; size?: 64 | 20; className?: string; style?: CSSProperties }) {
  return (
    <ThinkingOrb
      state={spec.state}
      size={size}
      speed={spec.speed}
      theme="auto"
      aria-label={spec.label}
      className={`orb shrink-0 ${className ?? ""}`}
      style={{ display: "inline-block", verticalAlign: "middle", ...style }}
      data-orb-state={spec.state}
    />
  );
}

/** Large orb + short status label shown while the assistant works (replaces the old pulsing dot). */
export function ThinkingIndicator({ spec, size = 64 }: { spec: OrbSpec; size?: 64 | 20 }) {
  return (
    <div className="flex items-center gap-3" role="status" aria-live="polite">
      <Orb spec={spec} size={size} />
      <span className="text-sm text-muted">{spec.label}</span>
    </div>
  );
}

export function Collapsible({ title, children, defaultOpen = false, right }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean; right?: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button className="flex w-full items-center gap-1.5 text-left text-sm text-muted hover:text-fg" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="flex-1 min-w-0">{title}</span>
        {right}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 mb-6">
      <div>
        <h1 className="text-2xl font-semibold">{title}</h1>
        {subtitle && <p className="text-sm text-muted mt-1">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

export function Page({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return <div className={`mx-auto w-full px-4 sm:px-8 py-8 ${wide ? "max-w-6xl" : "max-w-4xl"}`}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="card p-8 text-center text-sm text-muted">{children}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="card p-3 text-sm text-red-600 border-red-300 mb-4">{String((error as Error)?.message ?? error)}</div>;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <div className="text-sm font-medium mb-1">{label}</div>
      {children}
      {hint && <div className="text-xs text-muted mt-1">{hint}</div>}
    </label>
  );
}

export function JsonView({ value }: { value: unknown }) {
  return <pre className="block mono">{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>;
}
