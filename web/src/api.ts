// Typed client for the AgentOS gateway (docs/CONTRACTS.md §1)

export type AgentType = "prebuilt" | "custom" | "system" | "user" | "imported";
export type AgentStatus = "active" | "pending_approval" | "draft" | "disabled" | "failed";

export interface Permissions {
  fs: { path: string; mode: "ro" | "rw" }[];
  network: string[];
  shell: boolean;
  browser: "none" | "sandbox" | "host";
  desktop: boolean;
}

export interface AgentSpec {
  id: string;
  name: string;
  type: AgentType;
  category: string;
  icon: string;
  description: string;
  capabilities: string[];
  tools: string[];
  uses: string[];
  skills: string[];
  permissions: Permissions;
  graph: string;
  system_prompt: string;
  config: Record<string, unknown>;
  models: Record<string, string>;
  status: AgentStatus;
  version: string;
  created_by: string;
  location: string;
  workflow: string[];
  stats?: { runs: number; success: number; failed: number };
}

export interface WorkflowGraph {
  nodes: { id: string; label: string }[];
  edges: { source: string; target: string; conditional?: boolean }[];
}

export interface AgentDetail extends AgentSpec {
  versions: { version: string; created_at: number }[];
  workflow_graph?: WorkflowGraph;
}

export interface Artifact {
  kind: "diff" | "image" | "file" | "table" | "link" | string;
  title?: string;
  content?: string;
  path?: string;
}

export interface Report {
  agent_id: string;
  task_id: string;
  status: "success" | "partial" | "failed";
  summary: string;
  result: unknown;
  files_created: string[];
  files_modified: string[];
  errors: string[];
  recommendations: string[];
  artifacts?: Artifact[];
  tool_calls?: Record<string, unknown>[];
  delegated_to?: string[];
  duration_s?: number;
  tokens?: number;
  run_id?: string;
  created_at?: number;
}

export interface RunEvent {
  seq: number;
  ts: number;
  run_id: string;
  type: "run" | "node" | "agent" | "tool" | "plan" | "build" | "approval" | "log" | "report" | "final" | "token" | string;
  agent_id: string;
  status: "running" | "done" | "failed" | "info" | "waiting" | string;
  message: string;
  data: Record<string, any>;
}

export interface Run {
  id: string;
  kind: string;
  request: string;
  status: string;
  final: string;
  created_at: number;
  finished_at: number | null;
}

export interface RunDetail extends Run {
  events: RunEvent[];
  reports: Report[];
}

export interface Approval {
  id: string;
  run_id: string;
  agent_id: string;
  title: string;
  detail: Record<string, any>;
  status: "pending" | "approved" | "denied";
  created_at: number;
  decided_at: number | null;
}

export interface Memory {
  id: string;
  kind: "fact" | "episode" | "project";
  text: string;
  tags: string[];
  created_at: number;
  source?: string;
  scope?: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  needs: string;
  params: Record<string, any>;
  custom: boolean;
  created_by: string;
  code: string;
}

export interface Schedule {
  id: string;
  name: string;
  cron: string;
  prompt: string;
  enabled: boolean;
  last_run?: number | null;
  next_run?: number | null;
  channel?: string;
  kind?: string;
}

export interface TestRow {
  status: "pass" | "fail" | "skipped" | "error";
  title?: string;
  evidence?: string;
  note?: string;
  run_id?: string;
  tokens?: number;
  seconds?: number;
}

export interface Selftest {
  state: { running: boolean; sections: string[]; started?: number | null; progress: { current?: string; done?: string[]; total?: number; finished?: boolean } };
  summary: { pass: number; fail: number; skipped: number; error: number; total: number; seconds: number; tokens: number; failures: { id: string; title: string }[] };
  rows: Record<string, TestRow>;
}

export interface FeedCard {
  id: string;
  title: string;
  body: string;
  run_id?: string;
  created_at: number;
  source?: string;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  source: string;
  path: string;
}

export interface RealProfiles {
  user_data: string;
  profiles: { dir: string; name: string; user_name: string; gaia_name?: string }[];
  in_use: boolean;
  agentos_profiles: string[];
  active_profile: string;
  mode: string;
  error?: string;
}
export interface SiteInfo { domain: string; visits: number; days: number; last_visit: number; elements: number; paths: number; known: boolean }
export interface ExtTab { id: number; windowId: number; index: number; url: string; title: string; active: boolean; favIconUrl?: string }
export interface ExtProfile { profile: string; key: string; connected: boolean; wanted: boolean; email: string; allowlist: string[]; tabs: number }
export interface ExtStatus { profiles?: ExtProfile[]; connected: boolean; info: Record<string, string>; tabs: ExtTab[]; active_tab: number | null; allowlist: string[] }

export interface VmBrowser { configured: boolean; available: boolean; url: string; view_url: string; profiles: string[] }

export interface ToolCheckRow { tool: string; status: "ok" | "broken" | "skipped" | "unchecked"; detail?: string; seconds?: number }
export interface ToolCheck { counts: Record<string, number>; results: ToolCheckRow[]; at: number | null }

export interface BrowserStatus {
  mode: "sandbox" | "host" | "extension" | "vm";
  vm?: VmBrowser;
  profiles: string[];
  active_profile: string;
  url: string;
  vnc_url: string;
  takeover?: boolean;
}

export interface Health {
  ok: boolean;
  version: string;
  llm: { mode: string; base_url: string; model: string | null; error: string };
  services: Record<string, boolean>;
  identity?: Identity;
}

export interface Identity {
  name: string;
  emoji: string;
  vibe: string;
  notes?: string;
}

export interface Soul {
  id: string;
  name: string;
  icon: string;
  description: string;
  tone: string;
  best_for: string[];
  builtin: boolean;
  source?: string;
  body: string;
  default?: boolean;
}

export interface WorkspaceFile {
  name: string;
  filename: string;
  content: string;
  max_chars: number;
}

export interface Conversation {
  id: string;
  title: string;
  updated_at: number;
  soul?: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  run_id?: string;
  ts: number;
}

export interface AuditEntry {
  ts: number;
  run_id: string;
  agent_id: string;
  kind: string;
  data: Record<string, any>;
}

export interface Mount {
  name: string;
  container_path: string;
  mode: string;
}

export type Settings = Record<string, any>;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new ApiError(0, "Backend unreachable");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = j.detail ? (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)) : JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

const q = (o: Record<string, string | number | undefined>) => {
  const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== "");
  return p.length ? "?" + new URLSearchParams(p.map(([k, v]) => [k, String(v)])).toString() : "";
};

export const api = {
  health: () => req<Health>("GET", "/api/health"),
  settings: () => req<Settings>("GET", "/api/settings"),
  saveSettings: (s: Settings) => req<Settings>("PUT", "/api/settings", s),
  testSettings: () => req<{ ok: boolean; model?: string; reply?: string; latency_s?: number; models?: string[]; error?: string }>("POST", "/api/settings/test"),
  models: () => req<{ models: string[] }>("GET", "/api/models"),

  agents: (type?: string, qs?: string) => req<AgentSpec[]>("GET", "/api/agents" + q({ type, q: qs })),
  agent: (id: string) => req<AgentDetail>("GET", `/api/agents/${encodeURIComponent(id)}`),
  patchAgent: (id: string, patch: Partial<AgentSpec>) => req<AgentSpec>("PATCH", `/api/agents/${encodeURIComponent(id)}`, patch),
  deleteAgent: (id: string) => req<{ ok: boolean }>("DELETE", `/api/agents/${encodeURIComponent(id)}`),
  runAgent: (id: string, instruction: string) => req<{ run_id: string }>("POST", `/api/agents/${encodeURIComponent(id)}/run`, { instruction }),
  agentReports: (id: string) => req<Report[]>("GET", `/api/agents/${encodeURIComponent(id)}/reports`),
  agentFiles: (id: string) => req<{ files: { path: string; content: string }[] }>("GET", `/api/agents/${encodeURIComponent(id)}/files`),

  chat: (message: string, conversation_id?: string, soul?: string, model?: string, effort?: number) =>
    req<{ run_id: string; conversation_id: string }>("POST", "/api/chat", { message, conversation_id, soul: soul || undefined, model: model || undefined, effort: effort === undefined || effort < 0 ? undefined : effort, source: "ui" }),
  conversations: () => req<Conversation[]>("GET", "/api/conversations"),
  conversation: (id: string) =>
    req<{ id: string; messages: ChatMessage[]; soul?: string; soul_explicit?: string }>("GET", `/api/conversations/${encodeURIComponent(id)}`),
  setConversationSoul: (id: string, soul: string) =>
    req<{ ok: boolean; soul: string }>("PUT", `/api/conversations/${encodeURIComponent(id)}/soul`, { soul }),

  souls: () => req<Soul[]>("GET", "/api/souls"),
  createSoul: (s: Partial<Soul>) => req<Soul>("POST", "/api/souls", s),
  updateSoul: (id: string, s: Partial<Soul> & { default?: boolean }) => req<Soul>("PUT", `/api/souls/${encodeURIComponent(id)}`, s),
  deleteSoul: (id: string) => req<{ ok: boolean }>("DELETE", `/api/souls/${encodeURIComponent(id)}`),
  importSouls: (path: string) =>
    req<{ source: string; kind: string; soul: string | null; files: string[] }>("POST", "/api/souls/import", { path }),
  workspaceFiles: () => req<WorkspaceFile[]>("GET", "/api/workspace-files"),
  saveWorkspaceFile: (name: string, content: string) =>
    req<{ name: string; content: string }>("PUT", `/api/workspace-files/${encodeURIComponent(name)}`, { content }),
  identity: () => req<Identity>("GET", "/api/identity"),
  saveIdentity: (i: Partial<Identity>) => req<Identity>("PUT", "/api/identity", i),

  build: (description: string, name?: string) => req<{ run_id: string }>("POST", "/api/builder", { description, name }),

  runs: (limit = 50) => req<Run[]>("GET", "/api/runs" + q({ limit })),
  run: (id: string) => req<RunDetail>("GET", `/api/runs/${encodeURIComponent(id)}`),
  cancelRun: (id: string) => req<{ ok: boolean }>("POST", `/api/runs/${encodeURIComponent(id)}/cancel`),
  steerRun: (id: string, text: string) =>
    req<{ ok: boolean }>("POST", `/api/runs/${encodeURIComponent(id)}/message`, { text }),

  approvals: (status?: string) => req<Approval[]>("GET", "/api/approvals" + q({ status })),
  decide: (id: string, decision: "approve" | "deny", remember_minutes?: number) =>
    req<{ ok: boolean }>("POST", `/api/approvals/${encodeURIComponent(id)}`, { decision, remember_minutes }),

  memory: (qs?: string, scope?: string) => req<Memory[]>("GET", "/api/memory" + q({ q: qs, scope })),
  memoryScopes: () => req<Record<string, number>>("GET", "/api/memory/scopes"),
  addMemory: (m: { kind: string; text: string; tags: string[]; scope?: string }) => req<Memory>("POST", "/api/memory", m),
  tools: () => req<ToolInfo[]>("GET", "/api/tools"),
  toolCheck: () => req<ToolCheck>("GET", "/api/tools/check"),
  runToolCheck: () => req<ToolCheck>("POST", "/api/tools/check", {}),
  deleteTool: (name: string) => req<{ ok: boolean }>("DELETE", `/api/tools/${encodeURIComponent(name)}`),
  deleteMemory: (id: string) => req<{ ok: boolean }>("DELETE", `/api/memory/${encodeURIComponent(id)}`),

  schedules: () => req<Schedule[]>("GET", "/api/schedules"),
  addSchedule: (s: Partial<Schedule>) => req<Schedule>("POST", "/api/schedules", s),
  deleteSchedule: (id: string) => req<{ ok: boolean }>("DELETE", `/api/schedules/${encodeURIComponent(id)}`),
  runSchedule: (id: string) => req<{ run_id: string }>("POST", `/api/schedules/${encodeURIComponent(id)}/run`),
  updateSchedule: (s: Schedule) => req<Schedule>("PUT", `/api/schedules/${encodeURIComponent(s.id)}`, s),

  tracingStatus: () => req<{ enabled: boolean; has_key: boolean; active: boolean; project: string; url: string }>("GET", "/api/tracing/status"),
  tracingPushPrompts: () => req<{ ok: boolean; pushed?: number; failed?: string[]; error?: string }>("POST", "/api/tracing/push-prompts"),
  tracingTest: () => req<{ ok: boolean; message?: string; error?: string }>("POST", "/api/tracing/test"),
  selftest: () => req<Selftest>("GET", "/api/selftest"),
  runSelftest: (sections?: string[]) => req<{ pid: number; started: number }>("POST", "/api/selftest/run", { sections: sections ?? ["16"] }),

  feed: () => req<FeedCard[]>("GET", "/api/feed"),

  skills: () => req<Skill[]>("GET", "/api/skills"),
  importSkills: (b: { path?: string; git_url?: string }) => req<{ imported: string[] }>("POST", "/api/skills/import", b),

  browserStatus: () => req<BrowserStatus>("GET", "/api/browser/status"),
  browserProfile: (profile: string, mode: string) => req<BrowserStatus>("POST", "/api/browser/profile", { profile, mode }),
  takeover: (on: boolean) => req<BrowserStatus>("POST", "/api/browser/takeover", { on }),
  vmLogin: (profile: string, url: string) =>
    req<{ session_id: string; profile: string; view_url: string }>("POST", "/api/browser/vm-login", { profile, url }),
  vmLoginDone: (sid: string) => req<{ ok: boolean; vm: VmBrowser }>("POST", `/api/browser/vm-login/${sid}/done`),
  realProfiles: () => req<RealProfiles>("GET", "/api/browser/real-profiles"),
  importProfile: (profile_dir: string, name: string) =>
    req<{ ok: boolean; name: string; copied: string[]; skipped: string[] }>("POST", "/api/browser/import-profile", { profile_dir, name }),
  siteMemory: () => req<SiteInfo[]>("GET", "/api/site-memory"),
  deleteSite: (domain: string) => req<{ ok: boolean }>("DELETE", `/api/site-memory/${encodeURIComponent(domain)}`),
  extStatus: () => req<ExtStatus>("GET", "/api/extension/status"),
  extToken: () => req<{ token: string; ws_url: string }>("GET", "/api/extension/token"),
  extRegenToken: () => req<{ token: string; ws_url: string }>("POST", "/api/extension/token"),
  extUseTab: (tab_id: number | null) => req<ExtStatus>("POST", "/api/extension/use-tab", { tab_id }),
  extProfile: (profile: string, patch: { wanted?: boolean; allowlist?: string }) =>
    req<ExtStatus>("PUT", "/api/extension/profile", { profile, ...patch }),
  extAllowlist: (allowlist: string) => req<ExtStatus>("PUT", "/api/extension/allowlist", { allowlist }),

  audit: (run_id?: string) => req<AuditEntry[]>("GET", "/api/audit" + q({ run_id })),
  mounts: () => req<Mount[]>("GET", "/api/mounts"),
};

export function fmtTime(ts?: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

export function fmtAgo(ts?: number | null): string {
  if (!ts) return "—";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return `${Math.max(0, Math.floor(s))}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
