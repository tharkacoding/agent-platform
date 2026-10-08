import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight, Square } from "lucide-react";
import { api } from "../api";
import { useRunEvents } from "../ws";
import { useApp } from "../state";
import { buildRunView } from "./runModel";
import { StepList } from "./StepList";
import { Markdown } from "./Markdown";
import { ReportCard, ImageArtifacts } from "./ReportCard";
import { ApprovalCard } from "./ApprovalCard";

/** Live plan checklist: the latest plan, each step ticking ○ pending → ◐ running → ✓ done (⚠ unverified, ✗ failed). */
function PlanChecklist({ events, names }: { events: any[]; names: Record<string, string> }) {
  const plans = events.filter((e) => e.type === "plan" && e.agent_id === "main_agent" && e.data?.plan?.steps?.length);
  if (!plans.length) return null;
  const steps: any[] = plans[plans.length - 1].data.plan.steps;
  const done = steps.filter((s) => s.status === "done").length;
  const icon: Record<string, [string, string]> = {
    pending: ["○", "text-muted"], running: ["◐", "text-blue-500 animate-pulse"], done: ["✓", "text-green-600"],
    unverified: ["⚠", "text-amber-600"], failed: ["✗", "text-red-600"],
  };
  return (
    <div className="rounded-xl border border-border px-3 py-2" data-testid="plan-checklist">
      <div className="flex items-center justify-between text-xs text-muted mb-1">
        <span className="font-medium">Plan</span>
        <span>{done}/{steps.length} done</span>
      </div>
      <div className="h-1 rounded bg-hover mb-2 overflow-hidden">
        <div className="h-full bg-green-600 transition-all" style={{ width: `${(100 * done) / Math.max(1, steps.length)}%` }} />
      </div>
      <ul className="space-y-1">
        {steps.map((s) => {
          const [ic, cls] = icon[s.status] || icon.pending;
          return (
            <li key={s.id} className="flex items-start gap-2 text-sm">
              <span className={`w-4 shrink-0 text-center ${cls}`}>{ic}</span>
              <span className={s.status === "done" ? "text-muted" : ""}>
                <span className="font-medium">{names[s.agent_id] || s.name || s.agent_id}</span>
                <span className="text-muted"> — {String(s.instruction || "").split("\n")[0].slice(0, 120)}</span>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Questions the agent asks YOU mid-run (what to search, which option…). Quick-reply buttons or your own text. */
function QuestionCard({ ev }: { ev: any }) {
  const qs: { q: string; options?: string[]; default?: string }[] = ev.data?.questions || [];
  const [answers, setAnswers] = useState<string[]>(qs.map(() => ""));
  const [sent, setSent] = useState(false);
  const send = (vals: string[]) => {
    setSent(true);
    fetch(`/api/questions/${ev.data.question_id}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers: vals.map((v, i) => v || qs[i]?.default || "") }),
    }).catch(() => setSent(false));
  };
  if (sent) return null;
  return (
    <div className="rounded-xl border-2 border-blue-400/60 p-3 space-y-3" data-testid="question-card">
      <div className="text-xs font-medium text-blue-600">❓ A quick question before I continue</div>
      {qs.map((q, i) => (
        <div key={i} className="space-y-1.5">
          <div className="text-sm font-medium">{q.q}</div>
          <div className="flex flex-wrap gap-1.5">
            {(q.options || []).map((o) => (
              <button key={o} className={`chip text-xs ${answers[i] === o ? "bg-hover font-medium" : "hover:bg-hover"}`}
                      onClick={() => setAnswers((a) => a.map((x, j) => (j === i ? o : x)))}>{o}</button>
            ))}
          </div>
          <input className="input text-sm" placeholder={`Or type your answer${q.default ? ` (default: ${q.default})` : ""}`}
                 value={answers[i]} onChange={(e) => setAnswers((a) => a.map((x, j) => (j === i ? e.target.value : x)))}
                 onKeyDown={(e) => { if (e.key === "Enter" && qs.length === 1) send(answers); }} />
        </div>
      ))}
      <div className="flex gap-2">
        <button className="btn-primary text-sm" onClick={() => send(answers)}>Answer</button>
        <button className="btn text-sm" onClick={() => send(qs.map((q) => q.default || ""))}>Use defaults</button>
      </div>
    </div>
  );
}

/** Terminal commands the agents ran in this turn, with exit code and output — always visible. */
function CommandsRun({ events }: { events: any[] }) {
  const cmds = events.filter((e) => e.type === "tool" && e.status !== "running" &&
    (e.message === "run_command" || e.message === "run_python"));
  if (!cmds.length) return null;
  return (
    <div className="rounded-lg border border-border overflow-hidden text-xs">
      <div className="px-3 py-1.5 text-muted bg-[var(--subtle,transparent)]">Terminal · {cmds.length} command{cmds.length > 1 ? "s" : ""}</div>
      {cmds.map((e, i) => {
        const a = e.data?.args || {};
        const out = e.data?.output;
        const o = typeof out === "object" && out ? out : {};
        const text = typeof out === "string" ? out : [o.stdout, o.stderr].filter(Boolean).join("\n");
        return (
          <div key={i} className="px-3 py-2 border-t border-border mono whitespace-pre-wrap break-all">
            <div><span className="text-muted">$ </span>{a.cmd || (a.code ? "python <script>" : e.message)}
              {o.exit_code !== undefined && <span className={o.exit_code === 0 ? " text-muted" : " text-red-600"}> · exit {o.exit_code}</span>}
              {e.data?.ok === false && <span className="text-red-600"> · failed</span>}</div>
            {text && <div className="text-muted mt-1">{String(text).slice(0, 2000)}</div>}
          </div>
        );
      })}
    </div>
  );
}
import { Orb, ThinkingIndicator } from "./ui";
import { orbFromEvents } from "./orbState";

/** A finished (or interrupted) run must never show live animations: freeze any step left "running". */
function settle(view: any, done: boolean) {
  if (!done) return view;
  const fix = (s: string) => (s === "running" || s === "waiting" ? "stopped" : s);
  return {
    ...view,
    status: view.status === "running" ? "done" : view.status,
    agents: view.agents.map((a: any) => ({ ...a, status: fix(a.status), tools: (a.tools || []).map((x: any) => ({ ...x, status: fix(x.status) })) })),
    steps: view.steps.map((s: any) => ({ ...s, status: fix(s.status) })),
  };
}

/** Assistant turn for one run: thinking orb → collapsible "Working…" steps → final markdown + report cards. */
export function RunBlock({ runId, fallbackText, onDone }: { runId: string; fallbackText?: string; onDone?: () => void }) {
  const { events, done, loaded } = useRunEvents(runId);
  const { names, approvals } = useApp();
  const waiting = approvals.filter((a) => a.run_id === runId && a.status === "pending");
  const view = useMemo(() => settle(buildRunView(events), done), [events, done]);
  const running = loaded && !done && view.status === "running";
  const orb = useMemo(() => orbFromEvents(events, running), [events, running]);
  const [open, setOpen] = useState<boolean | null>(null);
  const [showReports, setShowReports] = useState(false);
  const expanded = open ?? running;

  useEffect(() => {
    if (done) onDone?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done]);

  const answer = view.final || (running ? view.streaming : "") || (!running ? fallbackText || "" : "");
  const elapsed = events.length > 1 ? events[events.length - 1].ts - events[0].ts : 0;
  const hasSteps = view.steps.length > 0;

  return (
    <div className="space-y-3">
      <PlanChecklist events={events} names={names} />
      {hasSteps && (
        <div>
          <button className="flex items-center gap-1.5 text-sm text-muted hover:text-fg" onClick={() => setOpen(!expanded)}>
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            {running ? (
              <span className="flex items-center gap-1.5">
                <Orb spec={orb} size={20} />
                Working{view.currentNode ? ` · ${view.currentNode}` : "…"}
              </span>
            ) : (
              <span>
                Worked for {elapsed < 60 ? `${Math.max(1, Math.round(elapsed))}s` : `${Math.round(elapsed / 60)}m`} · {view.agents.length} agent
                {view.agents.length === 1 ? "" : "s"}
              </span>
            )}
          </button>
          {expanded && (
            <div className="mt-2 ml-1 pl-3 border-l border-border">
              <StepList view={view} names={names} />
            </div>
          )}
        </div>
      )}
      {events.filter((e: any) => e.type === "steer" || e.type === "status").map((e: any, i: number) =>
        e.type === "steer" ? (
          <div key={"steer" + i} className="flex justify-end" data-testid="steer-msg">
            <div className="bg-hover rounded-3xl px-4 py-2 max-w-[80%] whitespace-pre-wrap text-sm">
              <span className="text-muted text-xs">↪ while working · </span>{e.message}
            </div>
          </div>
        ) : (   // a question asked while it works: shown with its live status answer (the plan is unchanged)
          <div key={"status" + i} className="space-y-1" data-testid="status-answer">
            <div className="flex justify-end">
              <div className="bg-hover rounded-3xl px-4 py-2 max-w-[80%] text-sm">{e.data?.question}</div>
            </div>
            <div className="text-sm whitespace-pre-wrap rounded-xl border border-border px-3 py-2">
              <span className="text-muted text-xs">ℹ️ status (plan unchanged)</span>{"\n"}{e.message}
            </div>
          </div>
        ))}
      {(() => {   // open questions: asked but not yet answered/defaulted
        const done = new Set(events.filter((e: any) => e.type === "question" && e.status === "done").map((e: any) => e.data?.question_id));
        return events.filter((e: any) => e.type === "question" && e.status === "waiting" && !done.has(e.data?.question_id))
          .map((e: any) => <QuestionCard key={e.data?.question_id} ev={e} />);
      })()}
      {waiting.map((a) => (
        <div key={a.id} className="rounded-xl border-2 border-amber-400/70 bg-amber-50/40 dark:bg-amber-500/5 p-1">
          <div className="px-3 pt-2 text-xs font-medium text-amber-700 dark:text-amber-400">🔐 Your approval is needed to continue</div>
          <ApprovalCard a={a} />
        </div>
      ))}
      {running && !answer && <ThinkingIndicator spec={orb} size={64} />}
      {answer && <Markdown text={answer} />}
      <CommandsRun events={events} />
      <ImageArtifacts artifacts={view.reports.flatMap((r: any) => r.artifacts || [])} />
      {running && answer && <ThinkingIndicator spec={orb} size={20} />}
      {view.status === "failed" && view.error && <div className="text-sm text-red-600">{view.error}</div>}
      <div className="flex items-center gap-3 text-xs text-muted">
        {running && (
          <button className="flex items-center gap-1 hover:text-fg" onClick={() => api.cancelRun(runId).catch(() => {})}>
            <Square size={11} /> Stop
          </button>
        )}
        {!running && view.reports.length > 0 && (
          <button className="hover:text-fg" onClick={() => setShowReports(!showReports)}>
            {showReports ? "Hide" : "Show"} {view.reports.length} report{view.reports.length === 1 ? "" : "s"}
          </button>
        )}
        {(hasSteps || done) && (
          <Link to={`/runs/${runId}`} className="hover:text-fg">
            Run details
          </Link>
        )}
      </div>
      {showReports && (
        <div className="space-y-2">
          {view.reports.map((r: any, i: number) => (
            <ReportCard key={r.task_id + i} report={r} agentName={names[r.agent_id]} />
          ))}
        </div>
      )}
    </div>
  );
}
