import { useEffect, useState } from "react";
import { BookMarked, Copy, Globe, Hand, MonitorPlay, Puzzle, RefreshCw, Server, Trash2, UserRound } from "lucide-react";
import { api, type BrowserStatus, type ExtStatus, type RealProfiles, type SiteInfo, type VmBrowser } from "../api";
import { ErrorBox, Field, Page, PageHeader } from "../components/ui";

export default function Browser() {
  const [st, setSt] = useState<BrowserStatus | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [profile, setProfile] = useState("default");
  const [newProfile, setNewProfile] = useState("");
  const [frameKey, setFrameKey] = useState(0);

  const load = () =>
    api
      .browserStatus()
      .then((s) => {
        setSt(s);
        setProfile(s.active_profile || "default");
        setErr(null);
      })
      .catch(setErr);
  useEffect(() => {
    load();
    const t = window.setInterval(load, 10000);
    return () => window.clearInterval(t);
  }, []);

  const apply = (p: string, mode: string) => {
    if (mode !== "sandbox" && mode !== st?.mode && !confirm("Attach agents to a REAL Chrome profile on your computer? Every session needs approval and agents will act with your logged-in accounts.")) return;
    api.browserProfile(p, mode).then(setSt).catch(setErr);
  };

  const host = st?.mode === "host" || st?.mode === "extension";
  return (
    <Page wide>
      {host && <div className="bg-red-600 text-white text-sm font-semibold px-4 py-2 rounded-xl mb-4 text-center">HOST BROWSER — agents are controlling a real Chrome profile on your computer</div>}
      <PageHeader
        title="Browser"
        subtitle="Agents drive a sandboxed Chromium in Docker. Watch live, take over to log in, then hand back."
        actions={
          <button className="btn" onClick={() => { load(); setFrameKey((k) => k + 1); }}>
            <RefreshCw size={14} /> Refresh
          </button>
        }
      />
      <ErrorBox error={err} />
      <div className="grid lg:grid-cols-[280px_1fr] gap-6">
        <div className="space-y-4">
          <div className="card p-4 space-y-4">
            <Field label="Mode">
              <div className="flex gap-1">
                {(["sandbox", "host", "extension"] as const).map((m) => (
                  <button key={m} className={`btn flex-1 justify-center text-xs ${st?.mode === m ? (m !== "sandbox" ? "bg-red-600! text-white border-red-600" : "btn-primary") : ""}`} onClick={() => apply(profile, m)}>
                    {m === "sandbox" ? "Sandbox" : m === "host" ? "AgentOS Chrome" : "My tabs"}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Profile">
              <select className="input" value={profile} onChange={(e) => { setProfile(e.target.value); apply(e.target.value, st?.mode || "sandbox"); }}>
                {(st?.profiles?.length ? st.profiles : ["default"]).map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </Field>
            <div className="flex gap-2">
              <input className="input" placeholder="new profile" value={newProfile} onChange={(e) => setNewProfile(e.target.value)} />
              <button className="btn" disabled={!newProfile.trim()} onClick={() => { apply(newProfile.trim(), st?.mode || "sandbox"); setNewProfile(""); }}>
                Add
              </button>
            </div>
          </div>
          <div className="card p-4 space-y-3 text-sm">
            <div className="text-muted">Current page</div>
            <div className="mono text-xs break-all">{st?.url || "—"}</div>
            <button className={`btn w-full justify-center ${st?.takeover ? "btn-primary" : ""}`} onClick={() => api.takeover(!st?.takeover).then(setSt).catch(setErr)}>
              <Hand size={14} /> {st?.takeover ? "Hand back to agent" : "Take over"}
            </button>
            <p className="text-xs text-muted">While you have control, agent browser actions pause.</p>
          </div>
        </div>
        <div className="card overflow-hidden min-h-[520px] flex">
          {st?.vnc_url && !host ? (
            <iframe key={frameKey} src={st.vnc_url} title="Browser" className="w-full min-h-[520px] h-[70vh] border-0 bg-black" />
          ) : (
            <div className="m-auto text-center text-sm text-muted p-8">
              <MonitorPlay size={32} className="mx-auto mb-2" />
              {host ? "Host Chrome runs in its own window on your desktop." : "Browser service not running. Start the stack with agentos start."}
            </div>
          )}
        </div>
      </div>
      <div className="grid lg:grid-cols-3 gap-6 mt-6">
        <VmBrowserCard vm={st?.vm} onChanged={load} onError={setErr} />
        <ExtensionCard onError={setErr} />
        <RealProfilesCard onChanged={load} onError={setErr} />
        <SiteMemoryCard onError={setErr} />
      </div>
    </Page>
  );
}

/** M7 — the test VM's own Chrome: log in ONCE per site there; agents then use that profile without your screen. */
function VmBrowserCard({ vm, onChanged, onError }: { vm?: VmBrowser; onChanged: () => void; onError: (e: unknown) => void }) {
  const [prof, setProf] = useState("asdf");
  const [url, setUrl] = useState("https://leetcode.com/accounts/login/");
  const [sid, setSid] = useState("");
  if (!vm?.configured) return null;
  const open = () =>
    api.vmLogin(prof, url).then((r) => { setSid(r.session_id); window.open(r.view_url, "_blank"); onChanged(); }).catch(onError);
  const done = () => api.vmLoginDone(sid).then(() => { setSid(""); onChanged(); }).catch(onError);
  return (
    <div className="card p-4 space-y-3 text-sm">
      <div className="flex items-center gap-2 font-semibold"><Server size={16} /> VM browser
        <span className={`ml-auto text-xs ${vm.available ? "text-green-500" : "text-muted"}`}>{vm.available ? "running" : "off"}</span>
      </div>
      <p className="text-xs text-muted">
        A separate Chrome inside the test VM. Agents use it for web work in these profiles — never your mouse or screen.
        Log in once per site; the login stays in the VM.
      </p>
      <div className="text-xs">Logged-in profiles: <span className="mono">{vm.profiles.length ? vm.profiles.join(", ") : "none yet"}</span></div>
      <div className="flex gap-2">
        <input className="input w-28" placeholder="profile" value={prof} onChange={(e) => setProf(e.target.value)} />
        <input className="input flex-1" placeholder="login page URL" value={url} onChange={(e) => setUrl(e.target.value)} />
      </div>
      <div className="flex gap-2">
        {!sid ? (
          <button className="btn btn-primary flex-1 justify-center" disabled={!vm.available || !prof.trim()} onClick={open}>Log in on the VM screen</button>
        ) : (
          <button className="btn btn-primary flex-1 justify-center" onClick={done}>I'm logged in — done</button>
        )}
        <a className="btn" href={vm.view_url} target="_blank" rel="noreferrer"><MonitorPlay size={14} /> Watch</a>
      </div>
    </div>
  );
}

/** §10 — pair the AgentOS Chrome extension, see your live tabs, pick the agent's tab, edit the allowlist. */
function ExtensionCard({ onError }: { onError: (e: unknown) => void }) {
  const [ext, setExt] = useState<ExtStatus | null>(null);
  const [token, setToken] = useState("");
  const [show, setShow] = useState(false);
  const [allow, setAllow] = useState("");
  const load = () => api.extStatus().then((s) => { setExt(s); setAllow((a) => a || s.allowlist.join(", ")); }).catch(onError);
  useEffect(() => {
    load();
    const t = window.setInterval(load, 4000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const reveal = (regen = false) => (regen ? api.extRegenToken() : api.extToken()).then((r) => { setToken(r.token); setShow(true); }).catch(onError);
  return (
    <div className="card p-4 space-y-3 text-sm">
      <div className="flex items-center gap-2 font-medium">
        <Puzzle size={15} /> Chrome extension
        <span className={`ml-auto chip ${ext?.connected ? "text-green-600" : "text-muted"}`}>{ext?.connected ? "connected" : "not connected"}</span>
      </div>
      {!ext?.connected && (
        <p className="text-xs text-muted">
          Load <span className="mono">agent-platform\extension</span> in <span className="mono">chrome://extensions</span> (Developer mode → Load unpacked), then paste this token in its popup.
        </p>
      )}
      <div className="flex gap-2">
        <input className="input mono text-xs" readOnly value={show ? token : "••••••••••••"} />
        <button className="btn" onClick={() => (show ? navigator.clipboard?.writeText(token) : reveal())}>{show ? <Copy size={13} /> : "Show"}</button>
        <button className="btn" title="New token (unpairs the extension)" onClick={() => reveal(true)}><RefreshCw size={13} /></button>
      </div>
      {ext?.connected && (
        <div className="space-y-1 max-h-64 overflow-y-auto">
          <div className="text-xs text-muted">Your tabs ({ext.tabs.length})</div>
          {ext.tabs.map((t) => (
            <div key={t.id} className={`flex items-center gap-2 rounded-lg px-2 py-1 ${ext.active_tab === t.id ? "bg-hover" : ""}`}>
              {t.favIconUrl ? <img src={t.favIconUrl} className="w-4 h-4" alt="" /> : <Globe size={14} />}
              <span className="truncate flex-1" title={t.url}>{t.title || t.url}</span>
              <button className="btn text-xs py-0.5" onClick={() => api.extUseTab(ext.active_tab === t.id ? null : t.id).then(setExt).catch(onError)}>
                {ext.active_tab === t.id ? "In use" : "Use this tab"}
              </button>
            </div>
          ))}
        </div>
      )}
      <ProfileList ext={ext} onChange={setExt} onError={onError} />
      <Field label="Allowlist (agents may click/type here without asking)">
        <div className="flex gap-2">
          <input className="input text-xs" placeholder="leetcode.com, github.com" value={allow} onChange={(e) => setAllow(e.target.value)} />
          <button className="btn" onClick={() => api.extAllowlist(allow).then(setExt).catch(onError)}>Save</button>
        </div>
      </Field>
    </div>
  );
}

/** One extension per Chrome profile: the owner decides which profiles AgentOS may use (default off) + their allowlists. */
function ProfileList({ ext, onChange, onError }: { ext: ExtStatus | null; onChange: (s: ExtStatus) => void; onError: (e: unknown) => void }) {
  const [allow, setAllow] = useState<Record<string, string>>({});
  const ps = ext?.profiles || [];
  if (!ps.length) return <p className="text-xs text-muted">Install the extension in each Chrome profile you want AgentOS to use (name it in the popup, e.g. asdf). Connected profiles appear here.</p>;
  return (
    <div className="space-y-2" data-testid="ext-profiles">
      <div className="text-xs text-muted">Chrome profiles ({ps.length})</div>
      {ps.map((p) => (
        <div key={p.key} className="rounded-lg border border-border p-2 space-y-1" data-testid="ext-profile">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${p.connected ? "bg-green-500" : "bg-gray-400"}`} title={p.connected ? "connected" : "not connected"} />
            <span className="font-medium truncate">{p.profile}</span>
            <span className="text-xs text-muted truncate flex-1">{p.email || (p.connected ? `${p.tabs} tabs` : "not connected")}</span>
            <label className="flex items-center gap-1 text-xs" title="AgentOS may open, read and (with approval) act in this profile's tabs and logins">
              <input type="checkbox" data-testid="ext-profile-wanted" checked={p.wanted}
                     onChange={(e) => api.extProfile(p.profile, { wanted: e.target.checked }).then(onChange).catch(onError)} />
              Allow AgentOS to use this profile
            </label>
          </div>
          {p.wanted && (
            <div className="flex gap-2">
              <input className="input text-xs" placeholder="allowlist for this profile: leetcode.com, github.com"
                     value={allow[p.key] ?? p.allowlist.join(", ")} onChange={(e) => setAllow({ ...allow, [p.key]: e.target.value })} />
              <button className="btn text-xs" onClick={() => api.extProfile(p.profile, { allowlist: allow[p.key] ?? p.allowlist.join(", ") }).then(onChange).catch(onError)}>Save</button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** §11 — copy one of your real Chrome profiles (logins) into a dedicated AgentOS profile. */
function RealProfilesCard({ onChanged }: { onChanged: () => void; onError?: (e: unknown) => void }) {
  const [rp, setRp] = useState<RealProfiles | null>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const load = () => api.realProfiles().then(setRp).catch((e) => setMsg(String((e as Error)?.message || e)));
  useEffect(() => { load(); }, []);
  const doImport = (dir: string, name: string) => {
    setBusy(dir);
    setMsg("");
    api.importProfile(dir, name)
      .then((r) => { setMsg(`Imported "${r.name}" (${r.copied.length} items${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}). Agents now use it.`); load(); onChanged(); })
      .catch((e) => setMsg(String((e as Error)?.message || e)))
      .finally(() => setBusy(""));
  };
  return (
    <div className="card p-4 space-y-3 text-sm">
      <div className="flex items-center gap-2 font-medium"><UserRound size={15} /> Use my Chrome logins</div>
      <p className="text-xs text-muted">Copies a real Chrome profile (cookies, saved logins, site data) into an AgentOS profile so agents start logged in. Your original profile is never changed. Close Chrome first.</p>
      {rp?.in_use && <div className="text-xs text-amber-600">Chrome is open on your profiles — close all Chrome windows to import.</div>}
      {(rp?.profiles || []).map((p) => (
        <div key={p.dir} className="flex items-center gap-2">
          <span className="flex-1 truncate">{p.name} <span className="text-muted text-xs">{p.user_name || p.dir}</span></span>
          <button className="btn text-xs py-0.5" disabled={!!busy || rp?.in_use} onClick={() => doImport(p.dir, p.name)}>
            {busy === p.dir ? "Copying…" : "Import"}
          </button>
        </div>
      ))}
      {rp && !rp.profiles.length && <div className="text-xs text-muted">{rp.error || "No Chrome profiles found."}</div>}
      {rp && <div className="text-xs text-muted">Active AgentOS profile: <b>{rp.active_profile}</b>{rp.agentos_profiles.length ? ` · available: ${rp.agentos_profiles.join(", ")}` : ""}</div>}
      {msg && <div className="text-xs">{msg}</div>}
    </div>
  );
}

/** §12 — what agents learned about the sites they use (known after 3 visits). */
function SiteMemoryCard({ onError }: { onError: (e: unknown) => void }) {
  const [sites, setSites] = useState<SiteInfo[]>([]);
  const load = () => api.siteMemory().then(setSites).catch(onError);
  useEffect(() => { load(); }, []);
  return (
    <div className="card p-4 space-y-2 text-sm">
      <div className="flex items-center gap-2 font-medium"><BookMarked size={15} /> Site memory</div>
      <p className="text-xs text-muted">Agents remember useful buttons and the steps that worked on each site, so repeat visits are faster and cheaper.</p>
      {!sites.length && <div className="text-xs text-muted">Nothing learned yet.</div>}
      {sites.map((s) => (
        <div key={s.domain} className="flex items-center gap-2">
          <span className="flex-1 truncate">{s.domain} {s.known && <span className="chip text-[10px]">known</span>}</span>
          <span className="text-xs text-muted">{s.visits} visits · {s.elements} hints · {s.paths} paths</span>
          <button className="btn text-xs py-0.5" title="Forget" onClick={() => api.deleteSite(s.domain).then(load).catch(onError)}><Trash2 size={12} /></button>
        </div>
      ))}
    </div>
  );
}
