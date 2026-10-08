import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Play, Power, Trash2 } from "lucide-react";
import { api, fmtTime, type AgentDetail as AD, type Report } from "../api";
import { useApp } from "../state";
import { useRunEvents } from "../ws";
import { buildRunView } from "../components/runModel";
import { ErrorBox, Page, StatusPill, TypeBadge } from "../components/ui";
import { WorkflowGraph } from "../components/WorkflowGraph";
import { ReportCard } from "../components/ReportCard";
import { StepList } from "../components/StepList";
import { Markdown } from "../components/Markdown";
import { useChannel } from "../ws";

const TABS = ["Overview", "Workflow", "Try it", "History", "Memory", "Versions", "Files"] as const;
type Tab = (typeof TABS)[number];

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3 py-2 border-b border-border last:border-0 text-sm">
      <div className="text-muted">{k}</div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function TryIt({ agentId }: { agentId: string }) {
  const [text, setText] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const { events } = useRunEvents(runId);
  const { names } = useApp();
  const view = useMemo(() => buildRunView(events), [events]);
  const ownReport = view.reports.find((r) => r.agent_id === agentId) || view.reports[view.reports.length - 1];
  return (
    <div className="space-y-4">
      <ErrorBox error={err} />
      <textarea className="input min-h-24" placeholder="Give this agent a task…" value={text} onChange={(e) => setText(e.target.value)} />
      <button
        className="btn btn-primary"
        disabled={!text.trim()}
        onClick={() => {
          setErr(null);
          api.runAgent(agentId, text).then((r) => setRunId(r.run_id)).catch(setErr);
        }}
      >
        <Play size={14} /> Run
      </button>
      {runId && (
        <div className="card p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <StatusPill status={view.status === "running" ? "running" : view.status} />
            <Link to={`/runs/${runId}`} className="text-xs text-muted hover:underline">
              run {runId}
            </Link>
          </div>
          <StepList view={view} names={names} />
          {view.final && <Markdown text={view.final} />}
          {ownReport && <ReportCard report={ownReport} agentName={names[ownReport.agent_id]} />}
        </div>
      )}
    </div>
  );
}

export default function AgentDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const { names, refreshAgents } = useApp();
  const [agent, setAgent] = useState<AD | null>(null);
  const [tab, setTab] = useState<Tab>("Overview");
  const [reports, setReports] = useState<Report[]>([]);
  const [files, setFiles] = useState<{ path: string; content: string }[] | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const load = useCallback(() => {
    api.agent(id).then(setAgent).catch(setErr);
  }, [id]);
  useEffect(() => {
    setAgent(null);
    setFiles(null);
    setTab("Overview");
    load();
  }, [load]);
  useChannel("registry", (d: { agent: { id: string } }) => d.agent?.id === id && load());

  useEffect(() => {
    if (tab === "History") api.agentReports(id).then(setReports).catch(setErr);
    if (tab === "Files" && files === null) api.agentFiles(id).then((r) => setFiles(r.files)).catch(() => setFiles([]));
  }, [tab, id, files]);

  if (err && !agent) return <Page><ErrorBox error={err} /></Page>;
  if (!agent) return <Page><div className="text-muted text-sm">Loading…</div></Page>;

  const canDelete = agent.type !== "prebuilt" && agent.type !== "system";
  const toggle = () =>
    api
      .patchAgent(agent.id, { status: agent.status === "disabled" ? "active" : "disabled" })
      .then(() => {
        load();
        refreshAgents();
      })
      .catch(setErr);
  const del = () => {
    if (!confirm(`Delete ${agent.name}? This cannot be undone.`)) return;
    api
      .deleteAgent(agent.id)
      .then(() => {
        refreshAgents();
        nav("/agents");
      })
      .catch(setErr);
  };
  const p = agent.permissions;

  return (
    <Page wide>
      <Link to="/agents" className="text-sm text-muted hover:text-fg inline-flex items-center gap-1 mb-4">
        <ArrowLeft size={14} /> Agent Library
      </Link>
      <ErrorBox error={err} />
      <div className="flex flex-wrap items-start gap-4 mb-6">
        <div className="text-4xl">{agent.icon}</div>
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-semibold">{agent.name}</h1>
          <p className="text-muted text-sm mt-1">{agent.description}</p>
          <div className="flex flex-wrap gap-1.5 mt-2">
            <TypeBadge type={agent.type} />
            <StatusPill status={agent.status} />
            <span className="chip">v{agent.version}</span>
            <span className="chip mono">{agent.graph}</span>
          </div>
        </div>
        <div className="flex gap-2">
          <button className="btn" onClick={toggle}>
            <Power size={14} /> {agent.status === "disabled" ? "Enable" : "Disable"}
          </button>
          {canDelete && (
            <button className="btn btn-danger" onClick={del}>
              <Trash2 size={14} /> Delete
            </button>
          )}
        </div>
      </div>

      <div className="flex gap-1 border-b border-border mb-6 overflow-x-auto">
        {TABS.filter((t) => t !== "Files" || agent.type === "custom" || agent.type === "user" || agent.type === "imported").map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px ${tab === t ? "border-fg font-medium" : "border-transparent text-muted hover:text-fg"}`}>
            {t}
          </button>
        ))}
      </div>

      {tab === "Overview" && (
        <div className="grid lg:grid-cols-[1fr_320px] gap-6">
          <div className="card p-4">
            <Row k="Type">{agent.type}</Row>
            <Row k="Category">{agent.category}</Row>
            <Row k="Created by">{agent.created_by}</Row>
            <Row k="Status">{agent.status}</Row>
            <Row k="Version">{agent.version}</Row>
            <Row k="Capabilities">
              <div className="flex flex-wrap gap-1">{agent.capabilities.map((c) => <span key={c} className="chip">{c}</span>)}</div>
            </Row>
            <Row k="Tools">
              <div className="flex flex-wrap gap-1">{agent.tools.length ? agent.tools.map((c) => <span key={c} className="chip mono">{c}</span>) : <span className="text-muted">none</span>}</div>
            </Row>
            <Row k="Uses agents">
              <div className="flex flex-wrap gap-1">
                {agent.uses.length ? agent.uses.map((u) => <Link key={u} to={`/agents/${u}`} className="chip hover:bg-hover">{names[u] || u}</Link>) : <span className="text-muted">none</span>}
              </div>
            </Row>
            {agent.skills.length > 0 && (
              <Row k="Skills">
                <div className="flex flex-wrap gap-1">{agent.skills.map((s) => <span key={s} className="chip">{s}</span>)}</div>
              </Row>
            )}
            {agent.location && <Row k="Location"><span className="mono text-xs">{agent.location}</span></Row>}
            {agent.system_prompt && (
              <Row k="System prompt">
                <pre className="block mono text-xs">{agent.system_prompt}</pre>
              </Row>
            )}
          </div>
          <div className="space-y-4">
            <div className="card p-4">
              <div className="font-medium text-sm mb-2">Permissions</div>
              <div className="text-sm space-y-1.5">
                <div>📁 {p.fs.length ? p.fs.map((f) => `${f.path} (${f.mode})`).join(", ") : "no file access"}</div>
                <div>🌐 {p.network.length ? p.network.join(", ") : "no network"}</div>
                <div>💻 shell: {p.shell ? "yes" : "no"}</div>
                <div className={p.browser === "host" ? "text-red-600" : ""}>🧭 browser: {p.browser}</div>
                <div className={p.desktop ? "text-red-600" : ""}>🖱️ desktop: {p.desktop ? "yes (approval per action)" : "no"}</div>
              </div>
            </div>
            {agent.stats && (
              <div className="card p-4 text-sm">
                <div className="font-medium mb-2">Stats</div>
                <div className="grid grid-cols-3 text-center">
                  <div><div className="text-xl font-semibold">{agent.stats.runs}</div><div className="text-xs text-muted">runs</div></div>
                  <div><div className="text-xl font-semibold text-green-600">{agent.stats.success}</div><div className="text-xs text-muted">success</div></div>
                  <div><div className="text-xl font-semibold text-red-600">{agent.stats.failed}</div><div className="text-xs text-muted">failed</div></div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {tab === "Workflow" &&
        (agent.workflow_graph && agent.workflow_graph.nodes.length ? (
          <WorkflowGraph graph={agent.workflow_graph} height={560} />
        ) : agent.workflow.length ? (
          <ol className="card p-4 list-decimal ml-4 text-sm space-y-1">{agent.workflow.map((w) => <li key={w}>{w}</li>)}</ol>
        ) : (
          <div className="text-sm text-muted">No workflow graph available.</div>
        ))}

      {tab === "Try it" && <TryIt agentId={agent.id} />}

      {tab === "Memory" && <AgentMemory agentId={id} />}
      {tab === "History" && (
        <div className="space-y-3">
          {reports.length === 0 && <div className="text-sm text-muted">No runs yet.</div>}
          {reports.map((r, i) => (
            <div key={r.task_id + i}>
              <div className="text-xs text-muted mb-1">
                {fmtTime(r.created_at)} {r.run_id && <Link to={`/runs/${r.run_id}`} className="hover:underline">· run {r.run_id}</Link>}
              </div>
              <ReportCard report={r} agentName={agent.name} />
            </div>
          ))}
        </div>
      )}

      {tab === "Versions" && (
        <div className="card divide-y divide-border">
          {(agent.versions || []).length === 0 && <div className="p-4 text-sm text-muted">No version history.</div>}
          {[...(agent.versions || [])].reverse().map((v) => (
            <div key={v.version + v.created_at} className="p-3 flex items-center gap-3 text-sm">
              <span className="chip">v{v.version}</span>
              <span className="text-muted">{fmtTime(v.created_at)}</span>
              {v.version === agent.version && <span className="text-xs text-green-600">current</span>}
            </div>
          ))}
        </div>
      )}

      {tab === "Files" && (
        <div className="space-y-4">
          {files === null && <div className="text-sm text-muted">Loading…</div>}
          {files?.length === 0 && <div className="text-sm text-muted">No files.</div>}
          {files?.map((f) => (
            <div key={f.path}>
              <div className="mono text-xs text-muted mb-1">{f.path}</div>
              <pre className="block mono">{f.content}</pre>
            </div>
          ))}
        </div>
      )}
    </Page>
  );
}


/** This agent's OWN memory (it reads it before each task and writes a note after each one). */
function AgentMemory({ agentId }: { agentId: string }) {
  const [items, setItems] = useState<any[]>([]);
  const load = () =>
    fetch(`/api/memory?scope=${encodeURIComponent(agentId)}`).then((r) => r.json()).then(setItems).catch(() => {});
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);
  return (
    <div className="space-y-2">
      <p className="text-sm text-muted">
        This agent's own memory — it reads these notes before every task and adds one after each task. Facts about
        you live in the shared Global memory (Memory page).
      </p>
      {items.length === 0 && <div className="text-sm text-muted">No notes yet — they appear after this agent runs.</div>}
      {items.map((m) => (
        <div key={m.id} className="card px-3 py-2 text-sm flex items-start gap-2">
          <span className="chip text-[10px]">{(m.tags || [])[0] || m.kind}</span>
          <span className="flex-1">{m.text}</span>
          <button className="text-xs text-muted hover:text-red-600"
                  onClick={() => fetch(`/api/memory/${m.id}`, { method: "DELETE" }).then(load)}>forget</button>
        </div>
      ))}
    </div>
  );
}
