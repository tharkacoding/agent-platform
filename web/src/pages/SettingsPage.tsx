import { useEffect, useState, type ReactNode } from "react";
import { CheckCircle2, Loader2, RefreshCw, Save, XCircle } from "lucide-react";
import { api, type Mount, type Settings } from "../api";
import { useApp } from "../state";
import { ErrorBox, Field, Page, PageHeader } from "../components/ui";
import { AUTO_MODE_TIP } from "../components/Layout";

const PRESETS: { label: string; url: string }[] = [
  { label: "OmniRoute (local)", url: "http://host.docker.internal:20128/v1" },
  { label: "OpenAI", url: "https://api.openai.com/v1" },
  { label: "OpenRouter", url: "https://openrouter.ai/api/v1" },
  { label: "Ollama", url: "http://host.docker.internal:11434/v1" },
  { label: "LM Studio", url: "http://host.docker.internal:1234/v1" },
];
const ROLES: [string, string][] = [
  ["main", "Main Agent / planning"],
  ["coder", "Coding Agent"],
  ["builder", "Agent Builder"],
  ["worker", "Worker agents (cheap)"],
  ["vision", "Vision (browser screenshots)"],
  ["embed", "Embeddings (memory)"],
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card p-5 space-y-4">
      <h2 className="font-medium">{title}</h2>
      {children}
    </section>
  );
}

/** Assistant identity (IDENTITY.md): user-changeable name / emoji / vibe shown everywhere in the app. */
function AssistantSection() {
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
        toast({ title: `Assistant renamed to ${i.emoji} ${i.name}`, tone: "ok" });
      })
      .catch(setErr);
  return (
    <Section title="Assistant">
      <ErrorBox error={err} />
      <div className="grid sm:grid-cols-[90px_1fr_1.4fr] gap-3">
        <Field label="Emoji">
          <input className="input text-center text-lg" maxLength={8} value={emoji} onChange={(e) => setEmoji(e.target.value)} />
        </Field>
        <Field label="Assistant name" hint="Shown in the sidebar, tab title, chat, Telegram and the TUI.">
          <input className="input" maxLength={60} value={name} onChange={(e) => setName(e.target.value)} data-testid="settings-assistant-name" />
        </Field>
        <Field label="Vibe">
          <input className="input" value={vibe} onChange={(e) => setVibe(e.target.value)} />
        </Field>
      </div>
      <div className="flex justify-end">
        <button className="btn" disabled={!name.trim()} onClick={save}>
          <Save size={14} /> Save assistant
        </button>
      </div>
    </Section>
  );
}

export default function SettingsPage() {
  const [s, setS] = useState<Settings | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [mounts, setMounts] = useState<Mount[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<Awaited<ReturnType<typeof api.testSettings>> | null>(null);
  const { toast, health } = useApp();

  const loadModels = () => api.models().then((r) => setModels(r.models)).catch(() => setModels([]));
  useEffect(() => {
    api.settings().then(setS).catch(setErr);
    api.mounts().then(setMounts).catch(() => {});
    loadModels();
    // the sidebar Auto-mode switch saves on its own: mirror it here so Save never writes a stale value back
    const sync = () => api.settings().then((r) => setS((cur) => (cur ? { ...cur, auto_mode: r.auto_mode } : r))).catch(() => {});
    window.addEventListener("aos:settings", sync);
    return () => window.removeEventListener("aos:settings", sync);
  }, []);

  if (!s) return <Page><ErrorBox error={err} />{!err && <div className="text-sm text-muted">Loading…</div>}</Page>;

  const set = (k: string, v: unknown) => setS({ ...s, [k]: v });
  const setRole = (r: string, v: string) => setS({ ...s, role_models: { ...(s.role_models || {}), [r]: v } });
  const preset = PRESETS.find((p) => p.url === s.llm_base_url)?.label ?? "Custom";

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      const { auto_mode: _ignored, ...rest } = s || {};   // auto_mode is saved instantly by its own switch
      const r = await api.saveSettings(rest);
      setS(r);
      window.dispatchEvent(new Event("aos:settings"));
      toast({ title: "Settings saved", tone: "ok" });
      loadModels();
      return true;
    } catch (e) {
      setErr(e);
      return false;
    } finally {
      setSaving(false);
    }
  };
  const runTest = async () => {
    setTesting(true);
    setTest(null);
    if (await save()) {
      try {
        const r = await api.testSettings();
        setTest(r);
        if (r.models?.length) setModels(r.models);
      } catch (e) {
        setTest({ ok: false, error: String((e as Error).message) });
      }
    }
    setTesting(false);
  };

  const modelOptions = (value: string) => {
    const opts = models.includes(value) || !value ? models : [value, ...models];
    return opts.map((m) => <option key={m} value={m}>{m}</option>);
  };

  return (
    <Page>
      <PageHeader
        title="Settings"
        actions={
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? <Loader2 size={14} className="spin" /> : <Save size={14} />} Save
          </button>
        }
      />
      <ErrorBox error={err} />
      <div className="space-y-6">
        <AssistantSection />
        <Section title="Language model endpoint">
          <p className="text-sm text-muted -mt-2">Any OpenAI-compatible API: OmniRoute, OpenAI, OpenRouter, Ollama, LM Studio… Current mode: <b>{health?.llm.mode ?? "?"}</b></p>
          <Field label="Provider preset">
            <select className="input" value={preset} onChange={(e) => { const p = PRESETS.find((x) => x.label === e.target.value); if (p) set("llm_base_url", p.url); }}>
              {PRESETS.map((p) => <option key={p.label}>{p.label}</option>)}
              <option>Custom</option>
            </select>
          </Field>
          <Field label="Base URL">
            <input className="input mono" value={s.llm_base_url || ""} onChange={(e) => set("llm_base_url", e.target.value)} />
          </Field>
          <Field label="API key" hint="Stored only inside the gateway container. Leave as-is to keep the current key.">
            <input className="input mono" type="password" value={s.llm_api_key || ""} onChange={(e) => set("llm_api_key", e.target.value)} placeholder="none" />
          </Field>
          <Field label="Default model">
            <div className="flex gap-2">
              <select className="input" value={s.llm_model || ""} onChange={(e) => set("llm_model", e.target.value)}>
                <option value="">(first model the endpoint lists)</option>
                {modelOptions(s.llm_model || "")}
              </select>
              <button className="btn" onClick={loadModels} title="Reload models">
                <RefreshCw size={14} />
              </button>
            </div>
          </Field>
          <Field label="Provider mode">
            <select className="input" value={s.llm_provider || "auto"} onChange={(e) => set("llm_provider", e.target.value)}>
              <option value="auto">auto (use endpoint, fall back to offline heuristics)</option>
              <option value="openai">openai (endpoint only)</option>
              <option value="mock">mock (offline heuristics, no LLM)</option>
            </select>
          </Field>
          <div className="flex items-center gap-3 flex-wrap">
            <button className="btn" onClick={runTest} disabled={testing}>
              {testing ? <Loader2 size={14} className="spin" /> : <CheckCircle2 size={14} />} Test connection
            </button>
            {test &&
              (test.ok ? (
                <span className="text-sm text-green-600 flex items-center gap-1">
                  <CheckCircle2 size={14} /> {test.model} replied “{test.reply}” in {test.latency_s}s
                </span>
              ) : (
                <span className="text-sm text-red-600 flex items-center gap-1 break-all">
                  <XCircle size={14} className="shrink-0" /> {test.error}
                </span>
              ))}
          </div>
        </Section>

        <Section title="Models per role">
          <p className="text-sm text-muted -mt-2">Leave empty to use the default model. Useful with OmniRoute combos (e.g. a strong model for coding, a cheap one for workers).</p>
          <div className="grid sm:grid-cols-2 gap-3">
            {ROLES.map(([r, label]) => (
              <Field key={r} label={label}>
                <select className="input" value={s.role_models?.[r] || ""} onChange={(e) => setRole(r, e.target.value)}>
                  <option value="">(default)</option>
                  {modelOptions(s.role_models?.[r] || "")}
                </select>
              </Field>
            ))}
          </div>
        </Section>

        <Section title="Notifications">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" data-testid="settings-notify-enabled" checked={!!s.notify_enabled}
                   onChange={(e) => set("notify_enabled", e.target.checked)} />
            <span><span className="font-medium">Phone notifications</span> — {s.notify_enabled ? "ON" : "OFF"}. Only for chats you start here (never for test or background runs).</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" data-testid="settings-ntfy-inbound" checked={s.ntfy_inbound !== false}
                   onChange={(e) => set("ntfy_inbound", e.target.checked)} />
            <span><span className="font-medium">Messages from my phone</span> — text you send to the topic in the ntfy app
              becomes a task here (shows in the "📱 Phone" chat); start it with <span className="mono">claude</span> to send it to Claude Code instead.</span>
          </label>
          <Field label="Phone push (ntfy topic URL)" hint="Install the ntfy app on your phone and subscribe to the same topic. Leave empty to turn pushes off.">
            <input className="input mono" data-testid="settings-ntfy" placeholder="https://ntfy.sh/your-topic"
                   value={s.notify_ntfy_url ?? ""} onChange={(e) => set("notify_ntfy_url", e.target.value)} />
          </Field>
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Send a push when">
              <select className="input" value={s.notify_on || "all"} onChange={(e) => set("notify_on", e.target.value)}>
                <option value="all">a task finishes, fails, is stopped, or needs me</option>
                <option value="finish">a task finishes, fails or is stopped</option>
                <option value="off">never</option>
              </select>
            </Field>
            <Field label="Priority">
              <select className="input" value={s.notify_priority || "high"} onChange={(e) => set("notify_priority", e.target.value)}>
                {["min", "low", "default", "high", "urgent"].map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </Field>
          </div>
          <button className="btn text-sm w-fit" onClick={() => fetch("/api/notify/test", { method: "POST" })}>Send a test push</button>
        </Section>

        <Section title="Appearance">
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              data-testid="settings-mascot"
              checked={s.mascot_enabled !== false}
              onChange={(e) => {
                const v = e.target.checked;
                set("mascot_enabled", v);
                api.saveSettings({ mascot_enabled: v }).then(() => window.dispatchEvent(new Event("aos:settings")));
              }}
            />
            <span>
              <span className="font-medium">Show the mascot while AgentOS works</span> — a fox (page-mascot) appears
              in the corner while a task, the self-test or a tool check is running; it follows your cursor and blinks
              when you click it.
            </span>
          </label>
        </Section>
        <Section title="Methodology">
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              data-testid="settings-superpowers"
              checked={s.superpowers_enabled !== false}
              onChange={(e) => {
                const v = e.target.checked;
                set("superpowers_enabled", v);
                api.saveSettings({ superpowers_enabled: v }).then(() => window.dispatchEvent(new Event("aos:settings")));
              }}
            />
            <span>
              <span className="font-medium">Use the Superpowers methodology</span> — coding, debugging, testing and
              review agents are pointed to the Superpowers skills (test-first, root-cause debugging, evidence before
              claims). Import them once on the Skills page (git URL github.com/obra/superpowers).
            </span>
          </label>
        </Section>
        <Section title="Safety">
          <label className="flex items-start gap-2 text-sm" title={AUTO_MODE_TIP}>
            <input
              type="checkbox"
              className="mt-0.5"
              data-testid="settings-auto-mode"
              checked={s.auto_mode !== false}
              onChange={(e) => {
                const v = e.target.checked;
                set("auto_mode", v);
                api.saveSettings({ auto_mode: v }).then(() => window.dispatchEvent(new Event("aos:settings")));
              }}
            />
            <span>
              <span className="font-medium">Auto mode</span> — {AUTO_MODE_TIP}.
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={!!s.require_approval} onChange={(e) => set("require_approval", e.target.checked)} />
            Require my approval before any Builder-created agent is activated
          </label>
          <Field label="Network allowlist" hint="Comma-separated domains agents may reach through the egress proxy (e.g. api.github.com, codeforces.com). New domains ask for approval.">
            <input className="input mono" value={s.network_allowlist || ""} onChange={(e) => set("network_allowlist", e.target.value)} />
          </Field>
          <div>
            <div className="text-sm font-medium mb-1">Mounted folders</div>
            {mounts.length === 0 ? (
              <div className="text-xs text-muted">Only the shared folder. Add more with <span className="mono">agentos mount add &lt;path&gt;</span>.</div>
            ) : (
              <div className="space-y-1">
                {mounts.map((m) => (
                  <div key={m.container_path} className="text-xs mono flex gap-2">
                    <span className="chip">{m.mode}</span>
                    {m.name} → {m.container_path}
                  </div>
                ))}
              </div>
            )}
          </div>
        </Section>

        <Section title="Search">
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Provider">
              <select className="input" value={s.search_provider || "duckduckgo"} onChange={(e) => set("search_provider", e.target.value)}>
                <option value="duckduckgo">DuckDuckGo (no key)</option>
                <option value="tavily">Tavily</option>
              </select>
            </Field>
            <Field label="Search API key">
              <input className="input mono" type="password" value={s.search_api_key || ""} onChange={(e) => set("search_api_key", e.target.value)} />
            </Field>
          </div>
        </Section>

        <Section title="Tracing (LangSmith)">
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-0.5" data-testid="settings-langsmith" checked={!!s.langsmith_enabled}
              onChange={(e) => set("langsmith_enabled", e.target.checked)} />
            <span>
              <span className="font-medium">Send every agent step and model call to LangSmith</span> — see each run's
              graph, prompts, answers, tokens and timing at smith.langchain.com, so mistakes are easy to find and fix.
              Prompts and answers leave this computer while it is on.
            </span>
          </label>
          <div className="grid sm:grid-cols-2 gap-3 mt-2">
            <Field label="LangSmith API key">
              <input className="input mono" type="password" value={s.langsmith_api_key || ""} placeholder="lsv2_…"
                onChange={(e) => set("langsmith_api_key", e.target.value)} data-testid="settings-langsmith-key" />
            </Field>
            <Field label="Project name">
              <input className="input" value={s.langsmith_project || ""} placeholder="agentos"
                onChange={(e) => set("langsmith_project", e.target.value)} />
            </Field>
          </div>
          <label className="flex items-start gap-2 text-sm mt-2">
            <input type="checkbox" className="mt-0.5" data-testid="settings-langsmith-prompts" checked={!!s.langsmith_prompts}
              onChange={(e) => set("langsmith_prompts", e.target.checked)} />
            <span>
              <span className="font-medium">Use agent prompts edited in LangSmith</span> — push the current prompts once
              (button below), edit them at smith.langchain.com → Prompts (<span className="mono">agentos-&lt;agent&gt;</span>),
              and the agents use your version within 2 minutes. The prompt in code is the fallback.
            </span>
          </label>
          <div className="flex items-center gap-3 mt-2 text-sm">
            <button type="button" className="btn" data-testid="settings-langsmith-push"
              onClick={() =>
                api.saveSettings({ langsmith_enabled: !!s.langsmith_enabled, langsmith_api_key: s.langsmith_api_key || "",
                                   langsmith_project: s.langsmith_project || "agentos", langsmith_prompts: !!s.langsmith_prompts })
                  .then(() => api.tracingPushPrompts())
                  .then((r) => toast({ title: r.ok ? `Pushed ${r.pushed} agent prompts to LangSmith` : `LangSmith: ${r.error || (r.failed || []).join("; ")}`, tone: r.ok ? "ok" : "error" }))
                  .catch((e) => toast({ title: String(e), tone: "error" }))
              }>
              Push agent prompts to LangSmith
            </button>
          </div>
          <div className="flex items-center gap-3 mt-2 text-sm">
            <button type="button" className="btn" data-testid="settings-langsmith-test"
              onClick={() =>
                api.saveSettings({ langsmith_enabled: !!s.langsmith_enabled, langsmith_api_key: s.langsmith_api_key || "",
                                   langsmith_project: s.langsmith_project || "agentos" })
                  .then(() => api.tracingTest())
                  .then((r) => toast({ title: r.ok ? (r.message || "LangSmith key works") : `LangSmith: ${r.error}`, tone: r.ok ? "ok" : "error" }))
                  .catch((e) => toast({ title: String(e), tone: "error" }))
              }>
              Test key
            </button>
            <a className="underline text-muted" href="https://smith.langchain.com/settings" target="_blank" rel="noreferrer">
              Get a key (smith.langchain.com → Settings → API keys)
            </a>
          </div>
        </Section>

        <Section title="Channels">
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Telegram bot token">
              <input className="input mono" type="password" value={s.telegram_token || ""} onChange={(e) => set("telegram_token", e.target.value)} />
            </Field>
            <Field label="Telegram user id" hint="Only this user may talk to your agent.">
              <input className="input mono" value={s.telegram_user_id || ""} onChange={(e) => set("telegram_user_id", e.target.value)} />
            </Field>
            <Field label="Discord bot token">
              <input className="input mono" type="password" value={s.discord_token || ""} onChange={(e) => set("discord_token", e.target.value)} />
            </Field>
            <Field label="Discord user id">
              <input className="input mono" value={s.discord_user_id || ""} onChange={(e) => set("discord_user_id", e.target.value)} />
            </Field>
          </div>
        </Section>

        <Section title="Host bridge">
          <Field label="Bridge token" hint="Shown by `agentos bridge start` on Windows. Enables real Chrome profiles and desktop control (every action needs approval).">
            <input className="input mono" type="password" value={s.bridge_token || ""} onChange={(e) => set("bridge_token", e.target.value)} />
          </Field>
        </Section>
      </div>
    </Page>
  );
}
