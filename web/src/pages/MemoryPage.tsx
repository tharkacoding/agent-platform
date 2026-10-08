import { useEffect, useState } from "react";
import { Plus, Search, Trash2 } from "lucide-react";
import { api, fmtAgo, type Memory, type Skill } from "../api";
import { useApp } from "../state";
import { Empty, ErrorBox, Page, PageHeader } from "../components/ui";

export default function MemoryPage() {
  const [items, setItems] = useState<Memory[]>([]);
  const [q, setQ] = useState("");
  const [text, setText] = useState("");
  const [kind, setKind] = useState("fact");
  const [tags, setTags] = useState("");
  const [scope, setScope] = useState("");          // "" = all · "global" · "<agent_id>"
  const [addScope, setAddScope] = useState("global");
  const [scopes, setScopes] = useState<Record<string, number>>({});
  const [err, setErr] = useState<unknown>(null);
  const { names } = useApp();
  const load = (qs = q, sc = scope) => {
    api.memory(qs || undefined, sc || undefined).then(setItems).catch(setErr);
    api.memoryScopes().then(setScopes).catch(() => {});
  };
  useEffect(() => {
    load("", "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const pick = (sc: string) => {
    setScope(sc);
    load(q, sc);
  };
  const label = (sc: string) => (sc === "global" ? "Global" : names[sc] || sc);
  const total = Object.values(scopes).reduce((a, b) => a + b, 0);
  const agentScopes = Object.keys(scopes).filter((s) => s !== "global").sort();

  return (
    <Page>
      <PageHeader
        title="Memory"
        subtitle="One global memory shared by all agents (facts about you, preferences) plus each agent's own memory (its methods and notes)."
      />
      <ErrorBox error={err} />
      <div className="card p-4 mb-6 space-y-2">
        <textarea className="input min-h-16" placeholder="Something the agents should remember…" value={text} onChange={(e) => setText(e.target.value)} />
        <div className="flex flex-wrap gap-2">
          <select className="input w-auto!" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="fact">fact / preference</option>
            <option value="episode">episode</option>
            <option value="project">project note</option>
          </select>
          <select className="input w-auto!" value={addScope} onChange={(e) => setAddScope(e.target.value)} data-testid="memory-scope-select">
            <option value="global">Global (all agents)</option>
            {agentScopes.map((s) => (
              <option key={s} value={s}>
                {label(s)} only
              </option>
            ))}
          </select>
          <input className="input flex-1 min-w-40" placeholder="tags, comma separated" value={tags} onChange={(e) => setTags(e.target.value)} />
          <button
            className="btn btn-primary"
            disabled={!text.trim()}
            onClick={() =>
              api
                .addMemory({ kind, text, scope: addScope, tags: tags.split(",").map((t) => t.trim()).filter(Boolean) })
                .then(() => {
                  setText("");
                  setTags("");
                  load();
                })
                .catch(setErr)
            }
          >
            <Plus size={14} /> Remember
          </button>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5 mb-3" data-testid="memory-scope-tabs">
        {[["", `All · ${total}`], ["global", `Global · ${scopes.global || 0}`], ...agentScopes.map((s) => [s, `${label(s)} · ${scopes[s]}`])].map(
          ([sc, text]) => (
            <button key={sc || "all"} className={`chip ${scope === sc ? "bg-hover font-medium" : ""}`} onClick={() => pick(sc)}>
              {text}
            </button>
          ),
        )}
      </div>
      <div className="relative mb-4">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
        <input className="input pl-9" placeholder="Search memory…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} />
      </div>
      {items.length === 0 && <Empty>No memories yet.</Empty>}
      <div className="space-y-2">
        {items.map((m) => (
          <div key={m.id} className="card p-3 flex gap-3 text-sm">
            <span className="chip h-fit">{m.kind}</span>
            <div className="flex-1 min-w-0">
              <div className="whitespace-pre-wrap">{m.text}</div>
              <div className="text-xs text-muted mt-1 flex flex-wrap gap-1">
                <span className={`chip text-[10px] ${(m.scope || "global") === "global" ? "" : "bg-blue-500/10 text-blue-600"}`}>
                  {label(m.scope || "global")}
                </span>
                {m.tags.map((t) => (
                  <span key={t}>#{t}</span>
                ))}
                <span>· {fmtAgo(m.created_at)}</span>
                {m.source && <span>· {m.source}</span>}
              </div>
            </div>
            <button className="text-muted hover:text-red-600 self-start" onClick={() => api.deleteMemory(m.id).then(() => load()).catch(setErr)} title="Forget">
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
      <ExperienceNotes />
      <Lessons />
    </Page>
  );
}

type Lesson = { scope: string; kind: string; text: string; strategy?: string; why?: string; count?: number; ts: number };

/** What the agents learned while working: strategies that worked/failed, things to avoid, your preferences. */
function Lessons() {
  const [items, setItems] = useState<Lesson[]>([]);
  const load = () => fetch("/api/lessons").then((r) => r.json()).then(setItems).catch(() => {});
  useEffect(() => {
    load();
  }, []);
  const del = (i: number) => fetch(`/api/lessons/${i}`, { method: "DELETE" }).then(load);
  const tone: Record<string, string> = { worked: "text-green-600", avoid: "text-red-600", failed: "text-amber-600", preference: "text-blue-600" };
  return (
    <div className="mt-8">
      <h2 className="text-lg font-semibold mb-1">Lessons learned</h2>
      <p className="text-sm text-muted mb-3">Agents read these before acting, so they don't repeat mistakes.</p>
      {items.length === 0 && <div className="text-sm text-muted">Nothing learned yet.</div>}
      <div className="space-y-1.5">
        {items.map((l, i) => (
          <div key={i} className="card px-3 py-2 flex items-start gap-3 text-sm">
            <span className={`text-xs font-medium uppercase w-20 shrink-0 ${tone[l.kind] || ""}`}>{l.kind}</span>
            <div className="flex-1 min-w-0">
              <div>{l.text}{l.strategy ? <span className="text-muted"> → {l.strategy}</span> : null}</div>
              <div className="text-xs text-muted">{l.scope}{l.count && l.count > 1 ? ` · seen ${l.count}×` : ""}</div>
            </div>
            <button className="p-1 rounded hover:bg-hover" title="Forget" onClick={() => del(i)}>
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Experience notes: the do / don't / how playbooks agents write after each task and read before similar ones. */
function ExperienceNotes() {
  const [items, setItems] = useState<Skill[]>([]);
  const [open, setOpen] = useState<Record<string, string>>({});
  useEffect(() => {
    api
      .skills()
      .then((s) => setItems(s.filter((x) => x.id.startsWith("exp-"))))
      .catch(() => {});
  }, []);
  const show = (s: Skill) => {
    if (open[s.id] !== undefined) {
      const { [s.id]: _x, ...rest } = open;
      setOpen(rest);
      return;
    }
    fetch(`/api/skills/${encodeURIComponent(s.id)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((j) => setOpen({ ...open, [s.id]: j.body || j.content || s.path }))
      .catch(() => setOpen({ ...open, [s.id]: s.path }));
  };
  return (
    <div className="mt-8" data-testid="experience-notes">
      <h2 className="text-lg font-semibold mb-1">Experience notes</h2>
      <p className="text-sm text-muted mb-3">
        Written after each task: what worked (DO), what failed and why (DON'T), how. Checked before similar tasks.
      </p>
      {items.length === 0 && <div className="text-sm text-muted">No experience yet.</div>}
      <div className="space-y-1.5">
        {items.map((s) => (
          <div key={s.id} className="card px-3 py-2 text-sm">
            <button className="w-full text-left" onClick={() => show(s)}>
              <div className="font-medium mono text-xs">{s.id}</div>
              <div className="text-muted text-xs">{s.description}</div>
            </button>
            {open[s.id] !== undefined && (
              <pre className="mono text-xs whitespace-pre-wrap mt-2 p-2 rounded bg-[var(--subtle,transparent)]">{open[s.id]}</pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}


