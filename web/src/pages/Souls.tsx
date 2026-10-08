import { useEffect, useState } from "react";
import { Download, Plus, Save, Star, Trash2, X } from "lucide-react";
import { api, type Soul, type WorkspaceFile } from "../api";
import { useApp } from "../state";
import { ErrorBox, Field, Page, PageHeader } from "../components/ui";

const BLANK: Partial<Soul> = { name: "", icon: "✨", description: "", tone: "", best_for: [], body: "# Soul\n- " };

function IdentityCard() {
  const { identity, setIdentity, toast } = useApp();
  const [name, setName] = useState(identity.name);
  const [emoji, setEmoji] = useState(identity.emoji);
  const [vibe, setVibe] = useState(identity.vibe);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    setName(identity.name);
    setEmoji(identity.emoji);
    setVibe(identity.vibe);
  }, [identity.name, identity.emoji, identity.vibe]);
  const save = () =>
    api
      .saveIdentity({ name, emoji, vibe })
      .then((i) => {
        setIdentity(i);
        toast({ title: `Assistant is now ${i.emoji} ${i.name}`, tone: "ok" });
      })
      .catch(setErr);
  return (
    <div className="card p-4 mb-6">
      <div className="font-medium mb-3">Assistant identity <span className="text-muted text-xs font-normal">(IDENTITY.md)</span></div>
      <ErrorBox error={err} />
      <div className="grid sm:grid-cols-[90px_1fr_1.4fr_auto] gap-3 items-end">
        <Field label="Emoji">
          <input className="input text-center text-lg" value={emoji} maxLength={8} onChange={(e) => setEmoji(e.target.value)} />
        </Field>
        <Field label="Assistant name">
          <input className="input" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} data-testid="identity-name" />
        </Field>
        <Field label="Vibe">
          <input className="input" value={vibe} onChange={(e) => setVibe(e.target.value)} placeholder="calm, capable, quietly proactive" />
        </Field>
        <button className="btn btn-primary" disabled={!name.trim()} onClick={save}>
          <Save size={14} /> Save
        </button>
      </div>
    </div>
  );
}

function SoulEditor({ soul, onClose, onSaved }: { soul: Partial<Soul>; onClose: () => void; onSaved: () => void }) {
  const [s, setS] = useState<Partial<Soul>>(soul);
  const [err, setErr] = useState<unknown>(null);
  const isNew = !soul.id;
  const save = () => {
    const body = { ...s, best_for: typeof s.best_for === "string" ? String(s.best_for).split(",").map((x) => x.trim()).filter(Boolean) : s.best_for };
    (isNew ? api.createSoul(body) : api.updateSoul(soul.id!, body)).then(onSaved).catch(setErr);
  };
  return (
    <div className="card p-4 mb-6">
      <div className="flex items-center mb-3">
        <div className="font-medium flex-1">{isNew ? "New soul" : `Edit ${soul.name}`}</div>
        <button className="text-muted hover:text-fg" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <ErrorBox error={err} />
      <div className="grid sm:grid-cols-[90px_1fr_1fr] gap-3">
        <Field label="Icon">
          <input className="input text-center text-lg" value={s.icon || ""} onChange={(e) => setS({ ...s, icon: e.target.value })} />
        </Field>
        <Field label="Name">
          <input className="input" value={s.name || ""} onChange={(e) => setS({ ...s, name: e.target.value })} />
        </Field>
        <Field label="Tone">
          <input className="input" value={s.tone || ""} onChange={(e) => setS({ ...s, tone: e.target.value })} />
        </Field>
      </div>
      <div className="grid sm:grid-cols-2 gap-3 mt-3">
        <Field label="Description">
          <input className="input" value={s.description || ""} onChange={(e) => setS({ ...s, description: e.target.value })} />
        </Field>
        <Field label="Best for (comma separated)">
          <input
            className="input"
            value={Array.isArray(s.best_for) ? s.best_for.join(", ") : String(s.best_for || "")}
            onChange={(e) => setS({ ...s, best_for: e.target.value as unknown as string[] })}
          />
        </Field>
      </div>
      <div className="mt-3">
        <Field label="SOUL.md (markdown — personality, tone, boundaries)">
          <textarea className="input mono min-h-56" value={s.body || ""} onChange={(e) => setS({ ...s, body: e.target.value })} />
        </Field>
      </div>
      <div className="flex justify-end gap-2 mt-3">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={!s.name?.trim()} onClick={save}>
          <Save size={14} /> Save soul
        </button>
      </div>
    </div>
  );
}

function WorkspaceFiles() {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [active, setActive] = useState("AGENTS");
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const { toast } = useApp();
  const load = () =>
    api
      .workspaceFiles()
      .then((f) => {
        setFiles(f);
        const cur = f.find((x) => x.name === active);
        if (cur) setDraft(cur.content);
      })
      .catch(setErr);
  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const cur = files.find((f) => f.name === active);
  const pick = (n: string) => {
    setActive(n);
    setDraft(files.find((f) => f.name === n)?.content || "");
  };
  const save = () =>
    api
      .saveWorkspaceFile(active, draft)
      .then(() => {
        toast({ title: `${active}.md saved`, tone: "ok" });
        load();
      })
      .catch(setErr);
  return (
    <div className="card p-4">
      <div className="font-medium mb-1">Workspace files</div>
      <div className="text-xs text-muted mb-3">
        OpenClaw-style persona files injected into user-facing replies (sized by seriousness to save tokens). USER.md is capped at 4,000 chars.
      </div>
      <ErrorBox error={err} />
      <div className="flex flex-wrap gap-1 mb-3">
        {files.map((f) => (
          <button key={f.name} className={`chip ${f.name === active ? "bg-hover font-medium" : ""}`} onClick={() => pick(f.name)}>
            {f.filename}
          </button>
        ))}
      </div>
      <textarea className="input mono min-h-72 w-full" value={draft} onChange={(e) => setDraft(e.target.value)} />
      <div className="flex items-center justify-between mt-2 text-xs text-muted">
        <span>
          {draft.length.toLocaleString()} / {(cur?.max_chars || 20000).toLocaleString()} chars
        </span>
        <button className="btn btn-primary" onClick={save} disabled={draft === cur?.content}>
          <Save size={14} /> Save {active}.md
        </button>
      </div>
    </div>
  );
}

export default function Souls() {
  const [items, setItems] = useState<Soul[]>([]);
  const [editing, setEditing] = useState<Partial<Soul> | null>(null);
  const [importPath, setImportPath] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const { toast } = useApp();
  const load = () => api.souls().then(setItems).catch(setErr);
  useEffect(() => {
    load();
  }, []);

  const doImport = () =>
    api
      .importSouls(importPath)
      .then((r) => {
        toast({ title: `Imported from ${r.kind}`, body: [r.soul && `soul: ${r.soul}`, ...r.files].filter(Boolean).join(", "), tone: "ok" });
        setImportPath("");
        load();
      })
      .catch(setErr);

  return (
    <Page wide>
      <PageHeader
        title="Souls"
        subtitle="Personalities your assistant can switch between — per conversation (chip in the chat box, or /soul teacher). Agents like Teacher or Engineer default to their own soul."
        actions={
          <button className="btn btn-primary" onClick={() => setEditing({ ...BLANK })}>
            <Plus size={14} /> New soul
          </button>
        }
      />
      <ErrorBox error={err} />
      <IdentityCard />
      {editing && (
        <SoulEditor
          soul={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      )}
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
        {items.map((s) => (
          <div key={s.id} className="card p-4 text-sm flex flex-col" data-testid={`soul-${s.id}`}>
            <div className="flex items-start gap-2">
              <span className="text-2xl leading-none">{s.icon}</span>
              <div className="flex-1 min-w-0">
                <div className="font-medium flex items-center gap-1.5">
                  {s.name}
                  {s.default && <span className="chip text-[11px]">default</span>}
                  {s.source && <span className="chip text-[11px]">imported</span>}
                </div>
                <div className="mono text-muted text-[11px]">{s.id}</div>
              </div>
            </div>
            <div className="text-muted mt-2 line-clamp-3 flex-1">{s.description}</div>
            {s.best_for?.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {s.best_for.slice(0, 4).map((b) => (
                  <span key={b} className="chip text-[11px]">{b}</span>
                ))}
              </div>
            )}
            <div className="flex gap-1.5 mt-3">
              <button className="btn" onClick={() => setEditing(s)}>Edit</button>
              {!s.default && (
                <button className="btn" title="Use for new conversations" onClick={() => api.updateSoul(s.id, { default: true }).then(load).catch(setErr)}>
                  <Star size={13} /> Default
                </button>
              )}
              {!s.builtin && (
                <button className="btn btn-danger ml-auto" title="Delete" onClick={() => api.deleteSoul(s.id).then(load).catch(setErr)}>
                  <Trash2 size={13} />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="card p-4 mb-6">
        <Field
          label="Import from OpenClaw / Hermes"
          hint="Folder with SOUL.md / USER.md / AGENTS.md / IDENTITY.md / TOOLS.md / HEARTBEAT.md. Leave empty to auto-detect ~/.openclaw/workspace or ~/.hermes. Files are copied; the originals are never changed."
        >
          <div className="flex gap-2">
            <input className="input mono" value={importPath} onChange={(e) => setImportPath(e.target.value)} placeholder="~/.openclaw/workspace" />
            <button className="btn" onClick={doImport}>
              <Download size={14} /> Import
            </button>
          </div>
        </Field>
      </div>
      <WorkspaceFiles />
    </Page>
  );
}
