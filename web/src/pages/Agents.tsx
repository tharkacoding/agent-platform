import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Search } from "lucide-react";
import type { AgentType } from "../api";
import { useApp } from "../state";
import { Empty, Page, PageHeader, StatusPill, TYPE_BADGE, TypeBadge } from "../components/ui";

const TYPES: ("all" | AgentType)[] = ["all", "prebuilt", "custom", "system", "user", "imported"];

export default function Agents() {
  const { agents } = useApp();
  const [q, setQ] = useState("");
  const [type, setType] = useState<"all" | AgentType>("all");

  const groups = useMemo(() => {
    const ql = q.toLowerCase();
    const list = agents.filter(
      (a) =>
        (type === "all" || a.type === type) &&
        (!ql || [a.name, a.description, a.category, ...a.capabilities].join(" ").toLowerCase().includes(ql)),
    );
    const g = new Map<string, typeof list>();
    list.forEach((a) => {
      const c = a.category || "General";
      if (!g.has(c)) g.set(c, []);
      g.get(c)!.push(a);
    });
    return [...g.entries()].sort(([a], [b]) => (a === "System" ? 1 : b === "System" ? -1 : a.localeCompare(b)));
  }, [agents, q, type]);

  return (
    <Page wide>
      <PageHeader
        title="Agent Library"
        subtitle={`${agents.length} agents in your organization`}
        actions={
          <Link to="/builder" className="btn btn-primary">
            <Plus size={15} /> New agent
          </Link>
        }
      />
      <div className="flex flex-wrap gap-2 mb-6">
        <div className="relative flex-1 min-w-56">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input className="input pl-9" placeholder="Search agents, capabilities…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex flex-wrap gap-1">
          {TYPES.map((t) => (
            <button key={t} onClick={() => setType(t)} className={`chip cursor-pointer ${type === t ? "bg-accent! text-accent-fg border-accent" : ""}`}>
              {t === "all" ? "All" : `${TYPE_BADGE[t].dot} ${TYPE_BADGE[t].label}`}
            </button>
          ))}
        </div>
      </div>
      {groups.length === 0 && <Empty>No agents match. Ask the Main Agent to build one, or open the Builder.</Empty>}
      <div className="space-y-8">
        {groups.map(([cat, list]) => (
          <section key={cat}>
            <h2 className="text-sm font-medium text-muted mb-3">{cat}</h2>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {list.map((a) => (
                <Link key={a.id} to={`/agents/${a.id}`} className="card p-4 hover:bg-hover transition-colors flex flex-col">
                  <div className="flex items-start gap-3">
                    <div className="text-2xl leading-none">{a.icon}</div>
                    <div className="min-w-0 flex-1">
                      <div className="font-medium truncate">{a.name}</div>
                      <div className="text-xs text-muted">v{a.version} · {a.graph}</div>
                    </div>
                  </div>
                  <p className="text-sm text-muted mt-2 line-clamp-2 flex-1">{a.description}</p>
                  <div className="flex flex-wrap gap-1.5 mt-3 items-center">
                    <TypeBadge type={a.type} />
                    {a.status !== "active" && <StatusPill status={a.status} />}
                    {a.stats && a.stats.runs > 0 && <span className="text-xs text-muted">{a.stats.runs} runs</span>}
                  </div>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </Page>
  );
}
