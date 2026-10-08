import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, CircleDashed, Hammer, XCircle } from "lucide-react";
import { api } from "../api";
import { useApp } from "../state";
import { useRunEvents } from "../ws";
import { buildRunView } from "../components/runModel";
import { ApprovalCard } from "../components/ApprovalCard";
import { ErrorBox, Field, Orb, Page, PageHeader, ThinkingIndicator } from "../components/ui";
import { orbForNode, orbFromEvents } from "../components/orbState";
import { StepList } from "../components/StepList";
import { Markdown } from "../components/Markdown";

// Builder graph nodes (plan §5.2) in order — drawn as a pipeline; live status from node events.
const PIPELINE: [string, string][] = [
  ["understand_spec", "Understand spec"],
  ["inspect_registry", "Inspect existing agents"],
  ["select_components", "Select reusable components"],
  ["design", "Design agent"],
  ["generate_prompts", "Generate prompts"],
  ["generate_code", "Generate code"],
  ["generate_tests", "Generate tests"],
  ["static_validation", "Static validation"],
  ["run_tests", "Run tests"],
  ["debug", "Debug"],
  ["security_review", "Security review"],
  ["approval_gate", "Approval"],
  ["register", "Register"],
  ["smoke_run", "Smoke run"],
  ["report", "Report"],
];

const EXAMPLES = [
  "An agent that analyzes my programming learning progress (solved problems by topic) and creates a weekly learning plan.",
  "An agent that analyzes a GitHub repository: structure, tech stack, code quality hotspots, and a README summary.",
  "An agent that turns my lecture notes into flashcards and a short quiz.",
];

export default function Builder() {
  const [desc, setDesc] = useState("");
  const [name, setName] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const { events, done } = useRunEvents(runId);
  const { approvals, names } = useApp();
  const view = useMemo(() => buildRunView(events), [events]);

  const nodeState = useMemo(() => {
    const st: Record<string, string> = {};
    let last = "";
    for (const e of events) {
      const n = e.data?.node as string | undefined;
      if (e.type === "node" && n && (e.agent_id === "agent_builder" || PIPELINE.some(([k]) => k === n))) {
        if (last && st[last] === "running") st[last] = "done";
        st[n] = "running";
        last = n;
      }
    }
    if ((done || view.status !== "running") && last) st[last] = view.status === "failed" ? "failed" : "done";
    return st;
  }, [events, done, view.status]);

  const runApprovals = approvals.filter((a) => a.run_id === runId);
  const newAgent = events.map((e) => e.data?.agent_id_created || e.data?.registered).find(Boolean) as string | undefined;

  return (
    <Page wide>
      <PageHeader title="Agent Builder" subtitle="Describe a new specialist. The Builder designs it, writes code + tests in the sandbox, reuses existing agents, and asks for your approval before activating it." />
      {!runId && (
        <div className="card p-5 space-y-4 max-w-3xl">
          <ErrorBox error={err} />
          <Field label="What should the agent do?">
            <textarea className="input min-h-32" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Describe goals, inputs, outputs, data sources, schedule…" />
          </Field>
          <Field label="Name (optional)">
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Programming Learning Agent" />
          </Field>
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((x) => (
              <button key={x} className="chip cursor-pointer hover:bg-hover text-left" onClick={() => setDesc(x)}>
                {x.slice(0, 60)}…
              </button>
            ))}
          </div>
          <button
            className="btn btn-primary"
            disabled={!desc.trim()}
            onClick={() => {
              setErr(null);
              api.build(desc, name || undefined).then((r) => setRunId(r.run_id)).catch(setErr);
            }}
          >
            <Hammer size={15} /> Build agent
          </button>
        </div>
      )}

      {runId && (
        <div className="grid lg:grid-cols-[280px_1fr] gap-6">
          <div className="card p-4 h-fit">
            <div className="text-sm font-medium mb-3">Build pipeline</div>
            <ol className="space-y-2">
              {PIPELINE.map(([k, label]) => {
                const s = nodeState[k];
                return (
                  <li key={k} className={`flex items-center gap-2 text-sm ${s ? "" : "text-muted"}`}>
                    {s === "running" ? <Orb spec={orbForNode("agent_builder", k)} size={20} style={{ margin: "-3px" }} /> : s === "done" ? <CheckCircle2 size={14} className="text-green-600" /> : s === "failed" ? <XCircle size={14} className="text-red-600" /> : <CircleDashed size={14} />}
                    {label}
                  </li>
                );
              })}
            </ol>
          </div>
          <div className="space-y-4 min-w-0">
            {runApprovals.map((a) => (
              <ApprovalCard key={a.id} a={a} />
            ))}
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-3 text-sm">
                <span className="font-medium">Live log</span>
                <Link to={`/runs/${runId}`} className="text-xs text-muted hover:underline">
                  run {runId}
                </Link>
              </div>
              <StepList view={view} names={names} />
              {view.status === "running" && (
                <div className="mt-3">
                  <ThinkingIndicator spec={orbFromEvents(events)} size={20} />
                </div>
              )}
            </div>
            {view.final && (
              <div className="card p-4">
                <Markdown text={view.final} />
                {newAgent && (
                  <Link to={`/agents/${newAgent}`} className="btn btn-primary mt-3">
                    Open new agent
                  </Link>
                )}
              </div>
            )}
            {(done || view.status !== "running") && (
              <button className="btn" onClick={() => { setRunId(null); setDesc(""); setName(""); }}>
                Build another
              </button>
            )}
          </div>
        </div>
      )}
    </Page>
  );
}
