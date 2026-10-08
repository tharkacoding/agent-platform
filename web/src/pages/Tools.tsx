import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, ShieldCheck, Sparkles, Trash2, Wrench } from "lucide-react";
import { api, type ToolCheckRow, type ToolInfo } from "../api";
import { useApp } from "../state";
import { Collapsible, Empty, ErrorBox, Page, PageHeader } from "../components/ui";

function NeedsBadge({ needs }: { needs: string }) {
  if (!needs) return <span className="chip text-[11px]">pure</span>;
  const tone: Record<string, string> = {
    desktop: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
    shell: "bg-red-500/10 text-red-600",
    browser: "bg-blue-500/10 text-blue-600",
    network: "bg-violet-500/10 text-violet-600",
  };
  return <span className={`chip text-[11px] ${tone[needs] || ""}`}>{needs}</span>;
}

function Params({ params }: { params: Record<string, any> }) {
  const keys = Object.keys(params || {});
  if (!keys.length) return <span className="text-xs text-muted">no parameters</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {keys.map((k) => (
        <span key={k} className="chip mono text-[11px]" title={JSON.stringify(params[k])}>
          {k}
          {params[k]?.type ? `: ${params[k].type}` : ""}
          {"default" in (params[k] || {}) ? "?" : ""}
        </span>
      ))}
    </div>
  );
}

const CHECK_TONE: Record<string, string> = {
  ok: "bg-green-500/10 text-green-700 dark:text-green-400", broken: "bg-red-500/10 text-red-600",
  skipped: "bg-zinc-500/10 text-muted", unchecked: "bg-amber-500/10 text-amber-600",
};

function ToolRow({ t, onDelete, check }: { t: ToolInfo; onDelete?: () => void; check?: ToolCheckRow }) {
  return (
    <div className="card p-3" data-testid="tool-row">
      <div className="flex items-start gap-2">
        <Wrench size={15} className="mt-0.5 text-muted shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium mono text-sm">{t.name}</span>
            <NeedsBadge needs={t.needs} />
            {check && (
              <span className={`chip text-[11px] ${CHECK_TONE[check.status] || ""}`} title={check.detail || ""} data-testid="tool-check">
                {check.status === "ok" ? "✓ works" : check.status}
              </span>
            )}
            {t.custom && <span className="chip text-[11px] bg-green-500/10 text-green-700 dark:text-green-400">custom</span>}
            {t.created_by && <span className="text-xs text-muted">by {t.created_by}</span>}
          </div>
          <div className="text-sm text-muted mt-1">{t.description}</div>
          <div className="mt-2">
            <Params params={t.params} />
          </div>
          {t.custom && t.code && (
            <div className="mt-2">
              <Collapsible title={<span className="text-xs">Show code</span>}>
                <pre className="mono text-xs whitespace-pre-wrap bg-[var(--subtle,transparent)] p-2 rounded">{t.code}</pre>
              </Collapsible>
            </div>
          )}
        </div>
        {onDelete && (
          <button className="p-1.5 rounded hover:bg-hover" title="Delete this custom tool" onClick={onDelete}>
            <Trash2 size={14} />
          </button>
        )}
      </div>
    </div>
  );
}

export default function Tools() {
  const [items, setItems] = useState<ToolInfo[]>([]);
  const [q, setQ] = useState("");
  const [want, setWant] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<Record<string, ToolCheckRow>>({});
  const [checking, setChecking] = useState(false);
  const byTool = (rows: ToolCheckRow[]) => Object.fromEntries(rows.map((r) => [r.tool, r]));
  useEffect(() => { api.toolCheck().then((c) => setCheck(byTool(c.results))).catch(() => {}); }, []);
  const runCheck = () => {
    setChecking(true);
    api.runToolCheck()
      .then((c) => { setCheck(byTool(c.results)); toast({ title: `Tools: ${c.counts.ok || 0} work, ${c.counts.broken || 0} broken`, tone: c.counts.broken ? "error" : "ok" }); })
      .catch(setErr)
      .finally(() => setChecking(false));
  };
  const { toast } = useApp();
  const nav = useNavigate();
  const load = () => api.tools().then(setItems).catch(setErr);
  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? items.filter((t) => (t.name + " " + t.description + " " + t.needs).toLowerCase().includes(s)) : items;
  }, [items, q]);
  const custom = filtered.filter((t) => t.custom);
  const builtin = filtered.filter((t) => !t.custom);

  const create = () => {
    const text = want.trim();
    if (!text) return;
    setBusy(true);
    const msg = /^create a tool/i.test(text) ? text : `create a tool that ${text}`;
    api
      .chat(msg)
      .then((r) => nav(`/?c=${r.conversation_id}`))
      .catch(setErr)
      .finally(() => setBusy(false));
  };

  const del = (name: string) => {
    if (!confirm(`Delete the custom tool "${name}"?`)) return;
    api
      .deleteTool(name)
      .then(() => {
        toast({ title: `Deleted ${name}`, tone: "ok" });
        load();
      })
      .catch(setErr);
  };

  return (
    <Page>
      <PageHeader
        title="Tools"
        subtitle={`Everything your agents can use (${items.length}). Agents can also create new tools for themselves with the Tool Builder.`}
        actions={
          <button className="btn" disabled={checking} onClick={runCheck} data-testid="check-tools">
            <ShieldCheck size={14} /> {checking ? "Checking…" : "Check all tools"}
          </button>
        }
      />
      <ErrorBox error={err} />

      <div className="card p-4 mb-6">
        <div className="flex items-center gap-2 font-medium mb-2">
          <Sparkles size={16} /> Create a tool
        </div>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="e.g. converts currencies using a fixed rate table"
            value={want}
            onChange={(e) => setWant(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
            data-testid="create-tool-input"
          />
          <button className="btn btn-primary" disabled={busy || !want.trim()} onClick={create}>
            Create
          </button>
        </div>
        <div className="text-xs text-muted mt-2">
          The Tool Builder designs it, self-tests it in the sandbox, registers it and verifies it by calling it.
        </div>
      </div>

      <div className="relative mb-4">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
        <input className="input pl-8" placeholder="Search tools…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      <h2 className="text-sm font-semibold mb-2">Custom (created by agents) · {custom.length}</h2>
      <div className="space-y-2 mb-6">
        {custom.length === 0 && <Empty>No custom tools yet — ask "create a tool that …".</Empty>}
        {custom.map((t) => (
          <ToolRow key={t.name} t={t} check={check[t.name]} onDelete={() => del(t.name)} />
        ))}
      </div>

      <h2 className="text-sm font-semibold mb-2">Built-in · {builtin.length}</h2>
      <div className="space-y-2">
        {builtin.map((t) => (
          <ToolRow key={t.name} t={t} check={check[t.name]} />
        ))}
      </div>
    </Page>
  );
}
