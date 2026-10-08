# AgentOS — interface contracts (all parallel workstreams code against this)

Ports: gateway `7860` (published on 127.0.0.1 only), runner `8001` (internal), browser_svc `8002` (internal) + noVNC `6080`
(published on 127.0.0.1, proxied by gateway at `/vnc/`), egress proxy `3128` (internal), host bridge `8765` (127.0.0.1 on Windows).
Python package names: `aap` (gateway backend), `runner` (runner svc), `browser_svc`, `bridge`, `cli`.
All JSON. Times are unix seconds (float). IDs are short hex strings.

## 1. Gateway REST API (`/api`)
| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | /api/health | | `{ok, version, llm:{mode,base_url,model,error}, services:{runner,browser,bridge}: bool}` |
| GET | /api/settings | | settings dict (api key masked) |
| PUT | /api/settings | partial settings dict | settings dict |
| POST | /api/settings/test | | `{ok, model, reply, latency_s, models[] }` or `{ok:false,error}` |
| GET | /api/models | | `{models: string[]}` from the endpoint |
| GET | /api/agents | `?type=&q=` | `AgentSpec[]` (+ `stats:{runs,success,failed}`) |
| GET | /api/agents/{id} | | `AgentSpec + {stats, versions[], workflow_graph:{nodes:[{id,label}],edges:[{source,target,conditional}]}}` |
| PATCH | /api/agents/{id} | `{status?, system_prompt?, ...}` | AgentSpec |
| DELETE | /api/agents/{id} | | `{ok}` (prebuilt/system cannot be deleted → 400) |
| POST | /api/agents/{id}/run | `{instruction}` | `{run_id}` |
| GET | /api/agents/{id}/reports | | Report[] |
| GET | /api/agents/{id}/files | | `{files:[{path, content}]}` custom agent code |
| POST | /api/chat | `{message, conversation_id?}` | `{run_id, conversation_id}` |
| GET | /api/conversations | | `[{id,title,updated_at}]` |
| GET | /api/conversations/{id} | | `{id, messages:[{role:'user'|'assistant', content, run_id?, ts}]}` |
| POST | /api/builder | `{description, name?}` | `{run_id}` (runs Agent Builder directly) |
| GET | /api/runs | `?limit=` | `[{id,kind,request,status,final,created_at,finished_at}]` |
| GET | /api/runs/{id} | | run + `events[]` + `reports[]` |
| POST | /api/runs/{id}/cancel | | `{ok}` |
| GET | /api/approvals | `?status=pending` | `Approval[]` |
| POST | /api/approvals/{id} | `{decision:'approve'|'deny', remember_minutes?}` | `{ok}` |
| GET/POST/DELETE | /api/memory[/{id}] | `{kind:'fact'|'episode'|'project', text, tags[]}` ; GET `?q=` | Memory[] |
| GET/POST/DELETE | /api/schedules[/{id}] | `{name, cron, prompt, enabled, channel?}` | Schedule[] |
| POST | /api/schedules/{id}/run | | `{run_id}` |
| GET | /api/feed | | `[{id, title, body, run_id, created_at, source}]` Home cards |
| GET | /api/skills | | `[{id,name,description,source,path}]` |
| POST | /api/skills/import | `{path? , git_url?}` | `{imported:[ids]}` |
| GET | /api/browser/status | | `{mode:'sandbox'|'host', profiles[], active_profile, url, vnc_url}` |
| POST | /api/browser/profile | `{profile, mode}` | status |
| POST | /api/browser/takeover | `{on: bool}` | status |
| GET | /api/audit | `?run_id=` | `[{ts, run_id, agent_id, kind, data}]` |
| GET | /api/mounts | | `[{name, container_path, mode}]` |

### Souls, workspace persona files, identity (aap/souls — OpenClaw/Hermes-style)
| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | /api/souls | | `Soul[]` (+ `default: bool`) |
| GET | /api/souls/{id} | | `Soul` |
| POST | /api/souls | `{id?, name, icon, description, tone, best_for[], body}` | `Soul` (409 if exists) |
| PUT | /api/souls/{id} | partial Soul and/or `{default: true}` | `Soul` |
| DELETE | /api/souls/{id} | | `{ok}` (400 for built-ins) |
| POST | /api/souls/import | `{path?}` (empty → ~/.openclaw/workspace, ~/.hermes) | `{source, kind, soul, files[]}` |
| GET | /api/workspace-files | | `[{name, filename, content, max_chars}]` for AGENTS, USER, IDENTITY, TOOLS, HEARTBEAT, MEMORY |
| GET/PUT | /api/workspace-files/{name} | PUT `{content}` | `{name, content}` |
| GET/PUT | /api/identity | PUT `{name?, emoji?, vibe?}` | `{name, emoji, vibe, notes}` (also in /api/health `identity`; ws channel `identity`) |
| PUT | /api/conversations/{id}/soul | `{soul}` | `{ok, soul}` ; GET /api/conversations/{id} also returns `soul` |
`POST /api/chat` accepts optional `soul` (applied to a new conversation before the run). Chat commands: `/souls`, `/soul <id>`.
`Soul = {id, name, icon, description, tone, best_for[], builtin, source, body}`. Prompt assembly: `souls.system_prefix(soul_id, effort)`
(tier 0-1 → 2-3 lines; tier ≥3 → full files with caps; USER.md 4k cap). AgentSpec has optional `soul` (agent default).

`Approval = {id, run_id, agent_id, title, detail, status:'pending'|'approved'|'denied', created_at, decided_at}`
`Memory = {id, kind, text, tags[], created_at, source}` ; `Schedule = {id, name, cron, prompt, enabled, last_run, next_run, channel}`

## 2. WebSocket `/ws`
Server → client messages: `{"channel": "run"|"registry"|"approval"|"feed", "data": {...}}`
* `run`: an Event (below). Client may send `{"subscribe": run_id}`; with no subscription it receives all runs.
* `registry`: `{action:'created'|'updated'|'deleted', agent: AgentSpec}`
* `approval`: Approval
* `feed`: feed card

Also SSE fallback: `GET /api/runs/{id}/events` (`text/event-stream`, each `data:` = Event; ends with `event: end`).

### Event
```json
{"seq":3,"ts":1.0,"run_id":"ab12","type":"run|node|agent|tool|plan|build|approval|log|report|final|token",
 "agent_id":"main_agent","status":"running|done|failed|info|waiting","message":"text",
 "data":{"node":"plan_dag","task_id":"t1","plan":{...},"report":{...},"text":"...", "args":{}, "output":"..."}}
```
Conventions: `agent` running/done brackets every agent invocation (data.task_id, data.parent_agent, data.instruction);
`node` = a graph node started (data.node); `plan` carries data.plan (Plan model); `report` carries data.report;
`final` carries data.text (markdown answer) and ends the run; `token` (optional) streams data.text chunks of the final answer.

## 3. Runner service (container `runner`, http://runner:8001) — untrusted execution
| POST /exec | `{cmd: str, cwd: str='/workspace', timeout: int=120, env?: {}}` | `{exit_code, stdout, stderr, duration_s, timed_out}` |
| POST /python | `{code, input?: any, timeout}` | `{ok, output (json), stdout, stderr}` — runs `run(input)` if defined |
| GET /fs/list | `?path=&depth=2` | `{entries:[{path, type:'file'|'dir', size}]}` |
| GET /fs/read | `?path=&start=&end=` | `{path, content, lines}` |
| POST /fs/write | `{path, content}` | `{ok, created: bool}` |
| POST /fs/patch | `{path, old, new, count=1}` | `{ok, replacements}` (exact-string replace, like an editor) |
| POST /search | `{pattern, path='/workspace', glob?, max=200}` | `{matches:[{path,line,text}]}` (ripgrep) |
| GET /health | | `{ok}` |
Rules: paths must resolve under `/workspace`, `/shared`, `/mounts/*`, `/tmp`; read-only mounts reject writes (403).
Output capped at 64 KB per stream. Runs as uid 1000. HTTP(S)_PROXY=http://egress:3128.

## 4. Browser service (container `browser`, http://browser:8002)
| POST /session | `{profile='default', mode:'sandbox'|'cdp', cdp_url?}` | `{session_id, url}` |
| POST /act | `{session_id, action, ...}` action ∈ goto{url}, click{selector|ref|x,y}, type{selector|ref,text,submit?}, press{key}, select{selector,value}, scroll{dy}, back, wait{ms}, tab_new{url}, tab_switch{index}, eval_readonly{expr} | `{ok, url, title, error?}` |
| GET /snapshot | `?session_id=` | `{url, title, elements:[{ref, role, name, tag, text}], text (visible, capped)}` |
| GET /screenshot | `?session_id=` | `image/png` |
| POST /takeover | `{on}` | `{ok}` (agent actions return 423 while on) |
| GET /profiles | | `{profiles[]}` |
noVNC at `http://browser:6080/vnc.html`.

## 4b. Tool names (implemented in `aap/tools/*.py` via `@tool`, called with `ctx.call_tool(name, **args)`)
fs: `fs_list(path, depth=2)`, `fs_read(path, start=0, end=0)`, `fs_write(path, content)`, `fs_patch(path, old, new)`
code: `search_code(pattern, path='/workspace', glob='')`, `run_command(cmd, cwd='/workspace', timeout=120)`,
      `run_python(code, input=None, timeout=60)` (runner; `run(input)` convention), `custom_logic(input)` (runs the agent's own logic.py in runner)
git: `git_status(repo)`, `git_diff(repo, staged=False)`, `git_branch(repo, name)`, `git_commit(repo, message)`, `git_log(repo, n=10)`
web: `web_search(query, n=5)`, `web_fetch(url, max_chars=20000)`
browser: `browser_open(profile='default', mode='sandbox')`, `browser_act(action, **kw)`, `browser_snapshot()`, `browser_screenshot()`
desktop: `desktop_screenshot()`, `desktop_tree(window='')`, `desktop_act(action, **kw)`
memory: `memory_recall(query, k=5)`, `memory_remember(text, kind='fact', tags=[])`
data: `data_profile(path_or_text)` (pandas profile in runner), `data_query(path, code)` (pandas snippet in runner)
skills: `skill_read(skill_id)` (full SKILL.md body + resource list)
Every tool returns a JSON-serialisable dict. Errors are returned as `{"error": "..."}` (not raised) except PermissionDenied/ApprovalDenied.

## 4c. Agent runtime facts (for graph authors)
* Archetype module: `aap/graphs/<key>.py` with `@archetype("<key>")` `def build(spec) -> StateGraph`. State is a TypedDict that
  includes `task: dict` (AgentTask) and `report: dict` (Report). Every node starts with `ctx = get_ctx(config); ctx.node("<name>")`.
* LLM: `ctx.llm().json(system, user, fallback=..., validate=...)`, `ctx.llm().complete(system, user, fallback=...)`;
  tool-calling model: `ctx.role_model("coder").bind_tools(ctx.lc_tools([...]))`. When `ctx.llm().mode == "mock"` (no endpoint),
  nodes MUST take a deterministic heuristic path so the whole platform still runs offline and tests pass.
* Delegation: `ctx.delegate(agent_id, instruction, context=[report dicts]) -> Report` (agent must list it in `uses`).
* Custom agents (Builder output) live in `data/custom_agents/<id>/v<N>/`: `manifest.yaml`, `prompt.md`, optional `logic.py`
  (pure function `run(input: dict) -> dict`, executed ONLY in the runner via `custom_logic`), `tests.json` (cases).
  Generated code never executes inside the gateway. After writing files call `ctx.runtime.register(spec)` (upsert + hot reload).
* Graph diagram for UI: `runtime.graph_json(agent_id)` uses `compiled.get_graph()`.

## 5. Host Bridge (Windows, http://host.docker.internal:8765, header `X-Bridge-Token`)
| GET /health | | `{ok, features:{desktop, chrome}}` |
| POST /chrome/launch | `{profile_dir, profile_name?}` | `{cdp_url}` (Chrome started with --remote-debugging-port and a NON-default --user-data-dir) |
| GET /desktop/screenshot | | `image/png` |
| GET /desktop/tree | `?window=` | `{windows:[...], elements:[{ref, name, control_type, rect}]}` (UI Automation) |
| POST /desktop/act | `{action: click|double_click|type|hotkey|launch|focus, ref?|x,y, text?, keys?, app?}` | `{ok}` |
Bridge shows its own local confirmation for every /desktop/act unless an approval token from the gateway is supplied
(`X-Approval-Id` that the gateway marked approved). Global kill hotkey Ctrl+Alt+Esc stops the bridge.
