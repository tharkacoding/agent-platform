import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Square } from "lucide-react";
import { api, fmtAgo, fmtTime, type AuditEntry, type Run } from "../api";
import { useApp } from "../state";
import { useChannel, useRunEvents } from "../ws";
import { buildRunView } from "../components/runModel";
import { Empty, ErrorBox, JsonView, Page, PageHeader, StatusPill, ThinkingIndicator } from "../components/ui";
import { orbFromEvents } from "../components/orbState";
import { Timeline } from "../components/Timeline";
import { StepList } from "../components/StepList";
import { ReportCard } from "../components/ReportCard";
import { Markdown } from "../components/Markdown";

export function RunsList() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const load = () => api.runs(100).then(setRuns).catch(setErr);
  useEffect(() => {
    load();
  }, []);
  useChannel("run", (e: { type: string; status: string }) => {
    if (e.type === "run" || e.type === "final") load();
  });
  return (
    <Page wide>
      <PageHeader title="Runs" subtitle="Every chat, agent run, build and scheduled job." />
      <ErrorBox error={err} />
      {runs.length === 0 && <Empty>No runs yet.</Empty>}
      <div className="card divide-y divide-border">
        {runs.map((r) => (
          <Link key={r.id} to={`/runs/${r.id}`} className="flex items-center gap-3 p-3 hover:bg-hover text-sm">
            <StatusPill status={r.status} />
            <span className="chip">{r.kind}</span>
            <span className="flex-1 truncate">{r.request}</span>
            <span className="text-xs text-muted whitespace-nowrap">{fmtAgo(r.created_at)}</span>
          </Link>
        ))}
      </div>
    </Page>
  );
}

export function RunDetail() {
  const { id = "" } = useParams();
  const { names } = useApp();
  const { events, done } = useRunEvents(id);
  const view = useMemo(() => buildRunView(events), [events]);
  const [run, setRun] = useState<Run | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [tab, setTab] = useState<"steps" | "reports" | "events" | "audit">("steps");
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    api.run(id).then(setRun).catch(setErr);
  }, [id, done]);
  useEffect(() => {
    if (tab === "audit") api.audit(id).then(setAudit).catch(setErr);
  }, [tab, id]);

  const running = !done && view.status === "running";
  return (
    <Page wide>
      <Link to="/runs" className="text-sm text-muted hover:text-fg inline-flex items-center gap-1 mb-4">
        <ArrowLeft size={14} /> Runs
      </Link>
      <ErrorBox error={err} />
      <div className="flex flex-wrap items-start gap-3 mb-6">
        <div className="flex-1 min-w-0">
          <h1 className="text-xl font-semibold break-words">{run?.request || id}</h1>
          <div className="text-xs text-muted mt-1 flex gap-2 flex-wrap">
            <span>run {id}</span>
            {run && <span>· {run.kind}</span>}
            {run && <span>· started {fmtTime(run.created_at)}</span>}
            {run?.finished_at && <span>· {(run.finished_at - run.created_at).toFixed(1)}s</span>}
          </div>
        </div>
        <StatusPill status={running ? "running" : run?.status || view.status} />
        {running && (
          <button className="btn btn-danger" onClick={() => api.cancelRun(id).catch(setErr)}>
            <Square size={13} /> Cancel
          </button>
        )}
      </div>
      <div className="grid lg:grid-cols-[300px_1fr] gap-6">
        <div className="card p-4 h-fit">
          <div className="text-sm font-medium mb-3">Timeline</div>
          <Timeline view={view} names={names} />
        </div>
        <div className="min-w-0">
          <div className="flex gap-1 border-b border-border mb-4">
            {(["steps", "reports", "events", "audit"] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)} className={`px-3 py-2 text-sm capitalize border-b-2 -mb-px ${tab === t ? "border-fg font-medium" : "border-transparent text-muted hover:text-fg"}`}>
                {t}
                {t === "reports" && view.reports.length ? ` (${view.reports.length})` : ""}
              </button>
            ))}
          </div>
          {tab === "steps" && (
            <div className="space-y-4">
              <StepList view={view} names={names} />
              {running && <ThinkingIndicator spec={orbFromEvents(events)} size={20} />}
              {(view.final || run?.final) && (
                <div className="card p-4">
                  <div className="text-xs text-muted mb-2">Final answer</div>
                  <Markdown text={view.final || run?.final || ""} />
                </div>
              )}
            </div>
          )}
          {tab === "reports" && (
            <div className="space-y-3">
              {view.reports.length === 0 && <div className="text-sm text-muted">No reports yet.</div>}
              {view.reports.map((r, i) => (
                <ReportCard key={r.task_id + i} report={r} agentName={names[r.agent_id]} />
              ))}
            </div>
          )}
          {tab === "events" && (
            <div className="space-y-1">
              {events.map((e) => (
                <details key={e.seq} className="text-xs">
                  <summary className="cursor-pointer mono">
                    <span className="text-muted">#{e.seq}</span> <b>{e.type}</b> {e.agent_id} <span className="text-muted">{e.status}</span> {e.message}
                  </summary>
                  <JsonView value={e} />
                </details>
              ))}
            </div>
          )}
          {tab === "audit" && (
            <div className="space-y-1">
              {audit.length === 0 && <div className="text-sm text-muted">No audit entries.</div>}
              {audit.map((a, i) => (
                <details key={i} className="text-xs">
                  <summary className="cursor-pointer mono">
                    <span className="text-muted">{new Date(a.ts * 1000).toLocaleTimeString()}</span> <b>{a.kind}</b> {a.agent_id}{" "}
                    {String(a.data?.tool ?? a.data?.cmd ?? "")}
                  </summary>
                  <JsonView value={a.data} />
                </details>
              ))}
            </div>
          )}
        </div>
      </div>
    </Page>
  );
}
