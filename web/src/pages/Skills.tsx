import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { api, type Skill } from "../api";
import { useApp } from "../state";
import { Empty, ErrorBox, Field, Page, PageHeader } from "../components/ui";

export default function Skills() {
  const [items, setItems] = useState<Skill[]>([]);
  const [path, setPath] = useState("");
  const [git, setGit] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const { toast } = useApp();
  const load = () => api.skills().then(setItems).catch(setErr);
  useEffect(() => {
    load();
  }, []);

  const doImport = (b: { path?: string; git_url?: string }) => {
    setBusy(true);
    setErr(null);
    api
      .importSkills(b)
      .then((r) => {
        toast({ title: `Imported ${r.imported.length} skill(s)`, tone: "ok" });
        setPath("");
        setGit("");
        load();
      })
      .catch(setErr)
      .finally(() => setBusy(false));
  };

  return (
    <Page>
      <PageHeader title="Skills" subtitle="Agent Skills (SKILL.md folders) — compatible with OpenClaw / Hermes skill packs. Attach them to agents or use them as standalone agents." />
      <ErrorBox error={err} />
      <div className="card p-4 mb-6 grid sm:grid-cols-2 gap-4">
        <Field label="Import from mounted folder" hint="A container path, e.g. /mounts/openclaw-skills (mounted read-only).">
          <div className="flex gap-2">
            <input className="input mono" value={path} onChange={(e) => setPath(e.target.value)} placeholder="/mounts/skills" />
            <button className="btn" disabled={!path.trim() || busy} onClick={() => doImport({ path })}>
              <Download size={14} />
            </button>
          </div>
        </Field>
        <Field label="Import from git URL" hint="Cloned inside the sandbox runner.">
          <div className="flex gap-2">
            <input className="input mono" value={git} onChange={(e) => setGit(e.target.value)} placeholder="https://github.com/…" />
            <button className="btn" disabled={!git.trim() || busy} onClick={() => doImport({ git_url: git })}>
              <Download size={14} />
            </button>
          </div>
        </Field>
      </div>
      {items.length === 0 && <Empty>No skills imported yet.</Empty>}
      <div className="grid sm:grid-cols-2 gap-3">
        {items.map((s) => (
          <div key={s.id} className="card p-4 text-sm">
            <div className="font-medium">{s.name}</div>
            <div className="text-muted mt-1 line-clamp-3">{s.description}</div>
            <div className="text-xs text-muted mt-2 flex gap-2">
              <span className="chip">{s.source}</span>
              <span className="mono truncate">{s.path}</span>
            </div>
          </div>
        ))}
      </div>
    </Page>
  );
}
