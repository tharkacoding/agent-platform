import { useState } from "react";
import { Link } from "react-router-dom";
import { Bot, CheckCircle2, ChevronDown, ChevronRight, CircleDashed, ListChecks, ShieldAlert, Hammer, Wrench, XCircle } from "lucide-react";
import type { AgentStep, RunView, Step } from "./runModel";
import { DiffView } from "./DiffView";
import { Orb } from "./ui";
import { type OrbSpec, WAITING, orbForAgent, orbForAgentId, orbForTool } from "./orbState";

function Icon({ status, orb }: { status: string; orb?: OrbSpec }) {
  if (status === "running") return <Orb spec={orb ?? orbForAgentId("")} size={20} style={{ margin: "-3px" }} />;
  if (status === "waiting") return <Orb spec={WAITING} size={20} style={{ margin: "-3px" }} />;
  if (status === "done" || status === "success") return <CheckCircle2 size={14} className="text-green-600" />;
  if (status === "failed") return <XCircle size={14} className="text-red-600" />;
  return <CircleDashed size={14} className="text-muted" />;
}

function preview(v: unknown, n = 2000): string {
  if (v === undefined || v === null) return "";
  const s = typeof v === "string" ? v : JSON.stringify(v, null, 2);
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function ToolRow({ t }: { t: AgentStep["tools"][number] }) {
  const [open, setOpen] = useState(false);
  const isCmd = t.name === "run_command" || t.name === "run_python";
  const args = t.args as Record<string, any> | undefined;
  const label = isCmd && args?.cmd ? `$ ${args.cmd}` : t.name;
  const out = t.output as any;
  return (
    <div className="ml-5">
      <button className="flex items-center gap-1.5 text-xs text-muted hover:text-fg w-full text-left" onClick={() => setOpen(!open)}>
        <Icon status={t.status} orb={orbForTool(t.name)} />
        <Wrench size={11} />
        <span className="mono truncate">{label}</span>
        {t.duration_s !== undefined && <span>· {t.duration_s}s</span>}
      </button>
      {open && (
        <div className="mt-1 mb-2 space-y-1">
          {args && !isCmd && <pre className="block mono">{preview(args)}</pre>}
          {out && typeof out === "object" && (out.stdout || out.stderr) ? (
            <pre className="block mono">
              {out.stdout}
              {out.stderr ? "\n" + out.stderr : ""}
              {out.exit_code !== undefined ? `\n[exit ${out.exit_code}]` : ""}
            </pre>
          ) : out ? (
            typeof out === "string" && out.startsWith("diff ") ? <DiffView diff={out} /> : <pre className="block mono">{preview(out)}</pre>
          ) : null}
        </div>
      )}
    </div>
  );
}

function AgentRow({ a, names }: { a: AgentStep; names: Record<string, string> }) {
  const [open, setOpen] = useState(a.status === "running");
  const lastNode = a.nodes[a.nodes.length - 1];
  return (
    <div>
      <button className="flex items-center gap-2 text-sm w-full text-left" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={13} className="text-muted" /> : <ChevronRight size={13} className="text-muted" />}
        <Icon status={a.status} orb={a.status === "running" ? orbForAgent(a) : undefined} />
        <Bot size={14} className="text-muted" />
        <Link to={`/agents/${a.agent_id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
          {names[a.agent_id] || a.agent_id}
        </Link>
        {a.parent_agent && a.parent_agent !== "main_agent" && <span className="text-xs text-muted">via {names[a.parent_agent] || a.parent_agent}</span>}
        <span className="text-xs text-muted truncate flex-1">
          {a.status === "running" && lastNode ? `· ${lastNode.message}` : a.ended ? `· ${(a.ended - a.started).toFixed(1)}s` : ""}
        </span>
      </button>
      {open && (
        <div className="ml-4 mt-1 mb-2 border-l border-border pl-3 space-y-1">
          {a.instruction && <div className="text-xs text-muted italic">{a.instruction}</div>}
          {a.nodes.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {a.nodes.map((n, i) => (
                <span key={i} className="chip mono text-[11px]" title={n.message}>
                  {n.node}
                </span>
              ))}
            </div>
          )}
          {a.tools.map((t) => (
            <ToolRow key={t.key} t={t} />
          ))}
          {a.logs.map((l) => (
            <div key={l.seq} className="text-xs text-muted">
              {l.message}
            </div>
          ))}
          {a.report?.summary && <div className="text-xs">↳ {a.report.summary}</div>}
          {(a.report?.artifacts || [])
            .filter((x) => x.kind === "diff" && x.content)
            .map((x, i) => (
              <DiffView key={i} diff={x.content!} title={x.title} />
            ))}
        </div>
      )}
    </div>
  );
}

/** Live TODO status: pending · running · ✓ verified · ⚠ not verified · ✗ failed */
function TodoBadge({ status, verified }: { status?: string; verified?: boolean }) {
  const map: Record<string, [string, string]> = {
    pending: ["○", "text-muted"],
    running: ["◐", "text-blue-500"],
    done: [verified ? "✓" : "•", "text-green-600"],
    unverified: ["⚠", "text-amber-600"],
    failed: ["✗", "text-red-600"],
  };
  const [icon, cls] = map[status || "pending"] || map.pending;
  const title = status === "done" && verified ? "done · verified" : status === "unverified" ? "attempted · not verified" : status;
  return <span className={`${cls} font-medium`} title={title}>{icon}</span>;
}

function PlanRow({ s, names }: { s: Extract<Step, { kind: "plan" }>; names: Record<string, string> }) {
  const plan = s.plan || {};
  return (
    <div className="text-sm">
      <div className="flex items-center gap-2">
        <ListChecks size={14} className="text-muted" />
        <span className="font-medium">Plan</span>
        {plan.intent && <span className="text-muted text-xs truncate">· {plan.intent}</span>}
      </div>
      <ol className="ml-6 mt-1 space-y-0.5 text-xs text-muted list-decimal">
        {(plan.steps || []).map((st: any) => (
          <li key={st.id}>
            <TodoBadge status={st.status} verified={st.verified} />{" "}
            <span className="text-fg">{names[st.agent_id] || (st.agent_id === "spawn" ? `⚡ ${st.name || "worker"}` : st.agent_id)}</span> — {st.instruction}
            {st.depends_on?.length ? <span> (after {st.depends_on.join(", ")})</span> : null}
          </li>
        ))}
      </ol>
      {plan.missing && (
        <div className="ml-6 mt-1 text-xs text-amber-600">
          Missing capability: {plan.missing.name} → asking Agent Builder
        </div>
      )}
    </div>
  );
}

export function StepList({ view, names }: { view: RunView; names: Record<string, string> }) {
  // the plan is re-emitted on every status change: show it ONCE (first position) with the LATEST statuses
  const plans = view.steps.filter((s) => s.kind === "plan");
  const latestByAgent: Record<string, any> = {};
  plans.forEach((p: any) => (latestByAgent[p.agent_id || "main"] = p));
  const firstKey: Record<string, string> = {};
  plans.forEach((p: any) => {
    const k = p.agent_id || "main";
    if (!firstKey[k]) firstKey[k] = p.key;
  });
  return (
    <div className="space-y-2">
      {view.steps.map((s) => {
        switch (s.kind) {
          case "agent":
            return <AgentRow key={s.key} a={s} names={names} />;
          case "plan": {
            const k = (s as any).agent_id || "main";
            if (firstKey[k] !== s.key) return null;
            return <PlanRow key={s.key} s={latestByAgent[k]} names={names} />;
          }
          case "node":
            return (
              <div key={s.key} className="flex items-center gap-2 text-sm text-muted">
                <CircleDashed size={14} />
                <span>{s.message || s.node}</span>
              </div>
            );
          case "build":
            return (
              <div key={s.key} className="flex items-center gap-2 text-sm">
                <Hammer size={14} className="text-muted" />
                <Icon status={s.status} orb={orbForAgentId("agent_builder")} />
                <span>{s.message}</span>
              </div>
            );
          case "approval":
            if (s.data?.auto) {
              // auto mode: approved without a click — show it quietly, no "review" link
              return (
                <div key={s.key} className="flex items-center gap-2 text-xs text-muted" data-testid="auto-approved">
                  <ShieldAlert size={13} />
                  <span>auto-approved · {String(s.message).replace(/^auto-approved \(auto mode\):\s*/, "")}</span>
                </div>
              );
            }
            return (
              <div key={s.key} className="flex items-center gap-2 text-sm text-amber-600">
                {s.status === "waiting" ? <Icon status="waiting" /> : <ShieldAlert size={14} />}
                <span>{s.message}</span>
                <Link to="/approvals" className="underline text-xs">
                  review
                </Link>
              </div>
            );
          default:
            return (
              <div key={s.key} className="flex items-center gap-2 text-xs text-muted">
                <Icon status={s.status} />
                <span className="truncate">{s.message}</span>
              </div>
            );
        }
      })}
    </div>
  );
}
