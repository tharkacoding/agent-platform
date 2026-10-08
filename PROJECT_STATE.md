# AgentOS — PROJECT STATE (single source of truth for any AI/human picking this up)

> Read this file first. It holds the vision, the decisions, the architecture, the plan, and **where we are now**.
> Keep it updated at the end of every work session (section 7 "Current position" + section 8 "Next steps").
> Detailed interface contracts: `docs/CONTRACTS.md`. Full original plan: `C:\Users\raman\.claude\plans\abstract-tinkering-floyd.md`.
> Research on similar products: `docs/research/dots_landscape.md`.
> **Real-world test checklist with edge cases: `docs/TEST_CASES.md`** — run it before calling anything done.

## 1. Vision (from the owner)
A **personal AI agent platform** in the spirit of OpenClaw / Hermes / OpenAI Dots, but built around the owner's idea of an
**expandable AI organization**:
- **Main Agent** = manager (understands, plans, delegates, reviews, answers).
- **Agent Builder** = AI software engineer that *creates new agents*, reusing existing agents/tools/skills, tests them,
  and registers them so they appear in the Agent Library and are used immediately.
- **Library of prebuilt agents**, each a real multi-node LangGraph (coding, research, data, review, testing, writing,
  teaching, planning, browser, desktop, …). Agents are composable (they delegate to each other via `uses`).
- **Codex/Muse-style Coding Agent**: explore repo → plan → edit many files → run → test → fix → self-review → report.
- **Browser & computer control** with Playwright: sandboxed Chromium in Docker by default; the owner's real Chrome
  profile only on request (approval-gated); real-desktop control through a host bridge (approval per action).
- **OpenClaw/Hermes compatibility**: import SKILL.md skills, use their files; reuse their code only after a license check.
- **Personal features**: long-term memory, scheduled tasks (Home feed), chat channels (Telegram, Discord), approvals.
- **Interfaces**: web control UI (React+Vite, ChatGPT/Codex-style, pulsing dot, live step timeline) + terminal TUI/CLI
  (`agentos.exe`, like OpenClaw's CLI + dashboard).
- **Works with any OpenAI-compatible endpoint** (default: local OmniRoute `http://localhost:20128/v1`); base URL, API
  key, per-role models are set in the UI.
- **Everything runs in Docker** so agents can't touch the laptop except explicitly shared folders.
- **Token-efficient**: every request gets a *seriousness tier* (0 trivial … 4 critical) that sets model, token budget,
  review/fix loops and whether optional LLM passes run. (`aap/core/effort.py`)
- **Self-improving**: a Self-Improvement Agent researches similar products (Dots, open-dots clones, OpenClaw, Hermes,
  Manus) via browser/web and proposes upgrades (never auto-builds without approval).

## 2. Owner's decisions (do not re-ask)
| Topic | Decision |
|---|---|
| Isolation | Docker Desktop + WSL2 (installed 2026-10-05). Only `C:\Users\raman\AgentOS\shared` + `agentos mount add` folders are visible to agents. |
| Browser | Container browser by default; owner's Chrome profile "when I ask" (host bridge + CDP, approval, red banner). |
| Shell/UI | Terminal-based CLI/TUI + web UI (like OpenClaw). No Electron for now. Windows `.exe` = CLI launcher (PyInstaller). |
| v1 personal features | Long-term memory, scheduled tasks, chat channels, real-desktop control — all in v1. |
| Coding agent | Must be Codex/Muse level (see list in plan §5.3). Main + Coder graphs must be the most complex/useful. |
| UI inspiration | ChatGPT/Codex minimal style; OpenAI Dots-like personal agent. |
| Reuse | May reuse OpenClaw / Hermes / open-dots code **after verifying license** (Anil-matcha/open-dots is MIT per its README). |
| Work style | Use parallel sub-agents to go faster; minimize tokens; keep this file current. |
| LLM | OmniRoute at `http://localhost:20128/v1` (from Docker: `http://host.docker.internal:20128/v1`). It currently listens on 0.0.0.0 without API key — recommend `REQUIRE_API_KEY=true`. |

## 3. Architecture (short)
```
Windows host: agentos.exe (CLI+TUI) · Host Bridge :8765 (desktop + Chrome CDP, OFF by default) · OmniRoute :20128
Docker (WSL2):
  gateway  :7860  FastAPI + LangGraph runtime + registry + memory + scheduler + channels + approvals + web UI (only service holding secrets; never runs generated code)
  runner   :8001  untrusted exec (shell/python/node/git, fs jail /workspace /shared /mounts /tmp), non-root, limits, no secrets
  browser  :8002  Playwright Chromium on Xvfb + noVNC :6080, persistent profiles; CDP attach to host Chrome via bridge
  egress   :3128  tinyproxy with domain allowlist — runner/browser have no direct internet
```
Data: SQLite (`/data/registry.db` agents/versions/runs/events/reports/approvals/audit/conversations/messages/feed/schedules,
`/data/memory.db` FTS5 + optional embeddings, `/data/checkpoints.db` LangGraph checkpoints), `/data/custom_agents/<id>/v<N>/`,
`/data/skills/<id>/SKILL.md`, `/data/settings.json`.

## 4. Code map
| Path | What | Owner/status |
|---|---|---|
| `aap/config.py` | paths, service URLs, limits (container vs dev auto-detect) | done |
| `aap/settings.py` | runtime settings (endpoint, key, per-role models, channels, bridge token), masked secrets | done |
| `aap/llm.py` | OpenAI-compatible client: `complete/json/stream/embed`, `chat_model(role)`, offline fallback, token counter | done |
| `aap/models.py` | AgentSpec(manifest)/Permissions/AgentTask(effort)/Report/Plan — **contract** | done |
| `aap/core/sdk.py` | Agent SDK: `@tool`, `@archetype`, `AgentContext` (call_tool, delegate, approve, budget, trim, report) — **contract** | done |
| `aap/core/effort.py` | seriousness tiers 0-4 → budgets; heuristic classifier; `trim()` | done |
| `aap/core/runtime.py` | bootstrap registry, compile graphs w/ checkpointer, run_agent, start_chat/agent/builder, cancel, graph_json | done |
| `aap/core/permissions.py · approvals.py · audit.py` | least-privilege checks, blocking approval queue (+standing grants), audit log | done |
| `aap/registry.py · events.py · bus.py` | SQLite store; RunContext.emit → DB + SSE + WebSocket bus | done |
| `aap/tools/*` | fs, code(run_command w/ safe-list+danger patterns→approval, run_python, search, custom_logic), git, web (egress), browser, desktop, memory, data(pandas in runner), skills | done (needs Docker to execute) |
| `aap/memory/store.py` | FTS5 + embeddings memory, markdown mirror | done |
| `aap/skills/loader.py` | SKILL.md import (path/git), wrap skill as `imported` agent | done |
| `aap/scheduler/jobs.py` | cron (5-field) scheduler thread → Main runs → Home feed + channels | done |
| `aap/channels/*` | Telegram (long-poll, inline approval buttons), Discord (discord.py optional) | done (untested live) |
| `aap/api/app.py` | all REST routes, SSE, `/ws`, serves `web/dist` | done |
| `aap/graphs/main.py` | **Main Agent graph** (17 nodes, Send fan-out, build-agent detour, critic, review loop, streaming, tiers) | done |
| `aap/graphs/{coding,builder,research,data,review,debug,testing,browser,desktop,writer,planner,teacher,competitive,composite,generic}.py` + `aap/agents/prebuilt/catalog.py` | archetypes + ~25 prebuilt + system agents + self_improvement_agent | sub-agent "graphs" (in progress) |
| `web/` | React+Vite+TS+Tailwind UI | sub-agent "web" (in progress) |
| `runner/ browser_svc/ egress/ docker/ docker-compose.yml requirements-*.txt` | sandbox services + images | sub-agent "infra" (in progress) |
| `cli/ bridge/ agentos.spec build_exe.ps1` | CLI/TUI, host bridge, exe | sub-agent "cli" (in progress) |
| `docs/research/dots_landscape.md` | research on OpenAI Dots & clones | sub-agent "research" (in progress) |
| `tests/` | runner, bridge, graphs, main-flow tests (mock LLM) | partial |

## 5. Key workflows
- **Main** (`aap/graphs/main.py`): load_context → classify_intent(+seriousness, one cheap call; greetings skip the LLM) →
  {respond | clarify | schedule_task | manage_agents | analyze_capabilities → discover_agents → plan_dag → refine_plan →
  (build_agent → discover again) | risk_gate → dispatch ⇒Send⇒ run_step (parallel) → collect → (dispatch | review →
  (dispatch | synthesize(streamed))) } → remember → END.
- **Builder** (`aap/graphs/builder.py`): understand_spec → inspect_registry → select_components → design → prompts →
  code (logic.py, runs only in runner) → tests → static validation (AST policy) → run tests → debug loop → security
  review → approval gate (if risky perms / require_approval) → register (+hot reload) → smoke run → report.
- **Coding** (`aap/graphs/coding.py`): intake → explore → repo_map → plan(todo) → implement(ReAct) → verify →
  diagnose/fix loop → self_review → finalize(branch+commit+diff).
- **Token policy**: tier from classify_intent travels in `AgentTask.effort`; graphs read `ctx.budget`; context passed
  between agents is slimmed + trimmed; cheap tiers skip critic/review/memory-extraction/synthesis rewrite.

## 6. How to run (target)
`agentos setup` → `agentos start` → open http://127.0.0.1:7860 (Settings → endpoint/model → Test connection).
Dev without Docker: `pip install -r requirements-gateway.txt` then `uvicorn aap.api.app:app --port 7860`
(tools needing runner/browser report "service unavailable"; set `AAP_LLM_PROVIDER=mock` for offline heuristics);
UI dev: `cd web && npm run dev` (proxy to :7860).

## 7. Current position (update every session)
**2026-10-05**
- Plan approved (M0–M10). WSL2 installed; Docker Desktop installed via winget, first launch pending (may need reboot /
  license accept by owner).
- Gateway core written (all of §4 marked done). Main graph upgraded with seriousness tiers, critic, streaming.
- DONE by sub-agents: **web UI** (`web/`, `npm run build` green, dist built), **infra** (runner/browser_svc/egress/
  Dockerfiles/compose; runner+browser_svc tests 9/9 pass locally; images not built yet), **research**
  (`docs/research/dots_landscape.md`).
- `tests/test_main_flow.py` 5/5 pass (mock LLM): greeting fast path, parallel Send delegation, build-missing-agent-then-use,
  schedule intent, effort propagation.
- Later the same day: graphs+catalog (17 archetypes, 28 prebuilt + 5 system agents), CLI/TUI/bridge + `dist/agentos.exe`
  (21 MB), headless UI e2e suite (`tests/e2e`, 12/12), host-Chrome dev mode (`scripts/dev_up.ps1` → bridge+browser_svc+
  gateway; verified: opened example.com in the owner's Chrome (dedicated profile) and screenshotted it) — all DONE.
- **LLM connected**: OmniRoute key set via UI; `/v1` needs a key (create in OmniRoute dashboard). Claude accounts often hit
  quota → **dynamic model routing** added (`aap/routing.py`): chains per role group (general/coding/vision) × tier band
  (cheap/standard/deep/critical), multi-provider (Gemini, GPT, GLM, DeepSeek, Kimi, Grok, Claude), 429/5xx → cool-down +
  next model in the same call; tool-calling models fail over too; JSON-call response cache (10 min). Tier is a contextvar
  set per agent run. `GET /api/routing` shows per-model calls/tokens/cool-downs. Settings `routing.chains` overrides;
  `role_models` pins a model first. Default `llm_model=auto/smart`.
- In progress (background): Souls/persona files (SOUL.md, AGENTS.md, USER.md, IDENTITY.md (assistant name changeable),
  TOOLS.md, HEARTBEAT.md; multiple souls: teacher/engineer/researcher/coach/...; import from OpenClaw/Hermes).
- **Computer use verified live (dev/host mode, `scripts/verify_computer_use.py`)**: terminal commands, write+run files,
  desktop screenshot, owner's Chrome (HN top-3 + screenshot). Screenshots in `C:\Users\raman\AgentOS\shared\verification\`.
  ⚠ **HOST MODE = NO SANDBOX.** `dev_up.ps1` now also starts the runner on 127.0.0.1:8001 executing directly on Windows
  (Git Bash if installed, else PowerShell) with `/workspace`→`C:\Users\raman\AgentOS\workspace`, `/shared`→`…\shared`,
  `/tmp`→`…\tmp` (`RUNNER_PATH_MAP`; fs ops jailed to those roots, host paths rewritten back in output; gateway env
  vars/secrets stripped from commands). Because commands can reach the whole machine, `config.RUNNER_ON_HOST` makes
  `run_command` auto-allow ONLY read-only commands (ls/dir/cat/git status|diff|log/rg/python --version…); everything
  else — python, pip/npm install, git commit, redirection, `$(…)` — asks for approval ("host mode (no sandbox)").
  `run_python` asks too unless the code passes the Builder's pure-logic AST policy. Pre-approve with autonomy_rules or
  "allow for N minutes". Approvals are asked once per run for identical requests (desktop_act: every time).
  Bridge runs with `desktop=True`: screen screenshots are read-only (no approval); every desktop click/type is approved.
  New: `terminal_agent` (graph `terminal`: intake → ReAct shell/file loop → verify → finalize), Main fast routes
  (`_direct_route`: screen screenshot → desktop_agent, page/site → browser_agent, commands → terminal_agent; no planner
  LLM call). browser_svc screenshot brings the tab to front + CDP fallback (background tabs in the user's Chrome hung).
  Screenshots (browser + desktop) are saved via runner `/fs/write_b64` to `/shared/screenshots/<run_id>_<kind>_<n>.png`
  (host: `C:\Users\raman\AgentOS\shared\screenshots`), returned as `{path, png_base64}`, put in `files_created` + the
  image artifact, and the Main synthesize prompt must cite those paths (verified live: runs 52ba46c18722, 2ccf1abc4ab2).
- Docker Desktop installed but engine returned HTTP 500 → needs a **reboot** after WSL install (owner told to wait until
  sub-agents finish). Docker Desktop was closed at owner's request.
- NOT yet done/verified: end-to-end test of gateway with real graphs (waiting for graphs sub-agent), docker compose
  build/up, live OmniRoute run, Telegram live test, host bridge live test, Self-Improvement Agent run.

## 8. Next steps (in order)
1. Integrate sub-agent outputs; run `pytest` (mock LLM) for runner/bridge/graphs/main flow; fix.
2. Start gateway locally (dev) against OmniRoute; smoke: chat greeting (tier 0), data analysis (tier 2), build-agent flow.
3. Docker: `docker compose build && up`; isolation checks (plan §15); real coding task in runner.
4. Build web UI into gateway image; Playwright e2e of UI.
5. Build `agentos.exe`; `agentos setup/start/doctor` against real Docker.
6. Research backlog (from dots_landscape.md; MIT sources: Anil-matcha/open-dots, Shashankss1205/OpenDots,
   CopilotKit/OpenDots, awesome-dots — composio open-dot has NO license: imitate only):
   a. Persistent **Goals** with heartbeats/check-ins + owner-defined evidence checks (OpenDots goals.py/engine.py/evidence.py).
   b. **Action gateway** risk classes read/write/external + 4-tier autonomy (auto / pre-approved / ask / handoff) with
      user rules; cheap rule-reviewer model returning matched rule indices, fail-closed to "ask" (open-dot review.ts idea).
   c. Unified event triggers (webhooks, file changes, schedules) → Main runs.
   d. Hermes-style Curator: after successful tasks, write reusable SKILL.md skills (deletable, reviewed).
   e. Secrets vault + redaction in audit; MCP/OAuth connectors later.
   f. Per-goal budgets (calls/day) on top of seriousness tiers.
7. Live tests: Telegram, host Chrome attach, desktop approval + kill hotkey.

## 9. Conventions
- Contracts first (`docs/CONTRACTS.md`, `models.py`, `sdk.py`); additive changes only.
- Every LLM call has a deterministic fallback (offline/mock mode must run end-to-end).
- Every graph node starts with `ctx.node(...)`; every agent ends with `state["report"] = ctx.report(...)`.
- Generated code never runs in the gateway. Side effects outside the sandbox always need approval.

## 10. Future: control any tab from the AgentOS web page (Claude-in-Chrome style) — owner request 2026-10-05
Goal: from the AgentOS web UI (or chat), see and drive the owner's other open tabs: list tabs, take a screenshot of
any tab, read its HTML/DOM, click/type/scroll, open/close tabs — without the CDP debug-port profile.
Design:
- **AgentOS Chrome extension** (Manifest V3, `extension/`): service worker connects to the gateway over WebSocket
  (`ws://127.0.0.1:7860/ws/extension`, pairing token shown in Settings). Uses `chrome.tabs` (list/open/activate),
  `chrome.tabs.captureVisibleTab` (screenshots), `chrome.scripting.executeScript` (DOM snapshot with element refs,
  click/type/scroll by ref — same snapshot format as browser_svc so the browser graph works unchanged),
  `chrome.debugger` optional for full-page shots.
- **Gateway**: new browser mode `extension` beside `sandbox`/`host`; `aap/tools/browser.py` routes browser_* calls to
  the extension socket; per-site permissions (allowlist in Settings), sensitive-action approvals, audit log.
- **UI**: "Tabs" panel on the Browser page — live tab list with thumbnails, click a tab to let the agent work in it,
  Stop button; chat command "use my current tab".
- Safety: extension only acts for the paired gateway, read-only by default per site, red banner in the page being
  controlled (content-script overlay), every write action on non-allowlisted sites asks approval.
- Order: DOM-first navigation (already the default), screenshot only when asked or the page lacks readable HTML.

**STATUS: DONE (gateway + extension code), owner must load it.** `extension/` (MV3: background.js service worker,
page.js snapshot/act/banner, popup to paste token, README). Gateway: `aap/extension.py` (Hub, token, allowlist),
`aap/api/routes_ext.py` (`/ws/extension?token=`, `/api/extension/status|token|use-tab|allowlist`), browser mode
`extension` in `aap/tools/browser.py` (tab_new/switch/close, snapshot, screenshot, act; write actions on non-allowlisted
sites → approval + extension refuses without `approved` flag). UI: Browser page → "Chrome extension" card (token,
live tabs, Use this tab, allowlist) + mode button "My tabs". Chat wording "this/current tab", "extension" → extension.
Left: owner loads unpacked + pastes token (extension/README.md); no live test yet (needs the loaded extension).

## 11. Future: use the owner's REAL Chrome profiles (logged-in sessions) — owner request 2026-10-05
Current behaviour (known limitation): "my chrome" mode launches Chrome with a SEPARATE, dedicated AgentOS profile
(`%APPDATA%\AgentOS\chrome-profiles\<name>`) — a fresh, normal browser with none of the owner's logins, cookies,
bookmarks or extensions. It does NOT take over the owner's existing Chrome profiles (Default, Profile 1, ...).
Reason: Chrome blocks `--remote-debugging-port` on the default user-data-dir, so CDP can't attach to real profiles.
Options to implement:
1. **Chrome extension** (preferred, see §10): runs inside the owner's real profile → full access to logged-in tabs,
   no debug port, works with every profile the extension is installed in. Profile picker = whichever Chrome window
   the extension is paired from.
2. **Profile clone**: one-time copy of a chosen real profile (Default / Profile N, read from `Local State`) into the
   AgentOS profile dir, so logins carry over (cookies are DPAPI-encrypted per user, so this works for the same
   Windows user). Needs Chrome closed for that profile during copy; re-sync on demand. UI: "Import my profile".
3. **Attach to an already-running Chrome** the owner starts with a debug port on a non-default user-data-dir.
Acceptance: agent can open a site the owner is logged into (e.g. Gmail/GitHub) and see it logged in, from the
profile the owner picks in the Browser page; red HOST banner + approvals stay.

**STATUS: DONE.** `bridge/profiles.py` lists real profiles from Local State and COPIES one (cookies, logins, local
storage, IndexedDB, prefs + root Local State) into `%APPDATA%\AgentOS\chrome-profiles\<name>\Default`; source never
modified; refuses while Chrome runs on the default User Data (lockfile + process check) or if the AgentOS Chrome is
open. Bridge `GET /chrome/profiles`, `POST /chrome/import`; gateway `GET /api/browser/real-profiles`,
`POST /api/browser/import-profile` (then sets browser_profile + mode host). UI: Browser page → "Use my Chrome logins".
Verified listing live (5 profiles). Real import NOT done: Chrome was open — owner closes Chrome, clicks Import.
Caveat: Chrome 127+ app-bound cookie encryption — copies work for same Windows user/Chrome install; some sites may
still ask to log in. Switching AgentOS profile needs the AgentOS Chrome window closed first (one CDP Chrome at a time).

## 12. Future: learn & cache frequently visited pages ("site memory") — owner request 2026-10-05
Already done: browser agent navigates by HTML/DOM; screenshots only when asked or when a page has no readable HTML.
Next:
- **Site maps per domain** (`/data/site_memory/<domain>.json`): after each visit store URL patterns, page purpose,
  stable selectors/roles for key elements (search box, login button, nav links), and the action path that worked
  (e.g. "codeforces.com → Problemset → filter by tag"). Next time the agent replays the known path without asking
  the LLM to re-plan every step → far fewer tokens and faster.
- **Snapshot cache**: cache DOM snapshots/extracted text keyed by URL + content hash with a TTL per site (news: 10 min,
  docs: 1 day); reuse when unchanged.
- **Daily-sites detection**: count visits per domain; sites used ≥3 days/week become "known sites", listed on the
  Browser page with what the agent learned (editable/deletable).
- **Self-healing**: if a remembered selector fails, fall back to normal snapshot reasoning, then update the site map.
- **Skills**: a well-learned site flow can be saved as a SKILL.md ("check my Codeforces rating") and scheduled.

**STATUS: DONE.** `aap/site_memory.py`: per-domain JSON (visits, days, pages by URL pattern, elements that worked
(role+name+uses), successful action path per goal pattern), `hint()` ≤400 chars injected into the browser decide
prompt, in-memory snapshot cache (news-like 10 min, default 1 h) reused after tab_switch/wait to an unchanged page.
Recorded in browser finalize. API `GET/DELETE /api/site-memory[/{domain}]`; UI: Browser page → "Site memory" with
Forget buttons ("known" after 3 visits). Verified live on example.com. Left: auto-save learned flows as SKILL.md.

## 13. Session hand-off (2026-10-05, usage limit reached)
Done last: tab fix (agent used Ctrl+T which doesn't open real tabs → browser_svc maps Ctrl+T/Ctrl+W to tab_new/tab_close,
new `tab_close` action, tool description tells model to use tab_new and verify `tabs` in snapshot; no screenshot on blank
tabs). Per-run token budgets, parallel-first planner, on-the-fly `spawn` workers, inline chat approvals, batch fan-out.
NOT yet done (owner asked): restart stack (`scripts\dev_down.ps1` + `dev_up.ps1`) and re-test "open 5 tabs in chrome";
run ALL of docs/TEST_CASES.md live and record results; implement future plan §10 (Chrome extension), §11 (real profiles),
§12 (site memory), Docker (§13 of TEST_CASES, needs reboot + disk space).

## 14. Stateful execution engine (owner spec 2026-10-05) — DONE
Problems seen: profile:"" / wrong profile, loops (repeated clicks/waits/opens), "success" without proof, agents
restarting work after handoffs, 242k-token LeetCode run. Implemented:
- **Task state** `aap/core/execstate.py`: request, goal, success criteria, live TODO (pending/running/done/
  unverified/failed + verified flag + evidence), browser {profile, mode, url, title, tabs}, completed, failed,
  observations, actions, lessons. Main creates it (load_context), syncs TODO after plan/collect/review, passes it to
  every agent (`AgentTask.context` kind=exec_state + a compact **handoff packet** appended to the instruction), and
  merges each agent's returned state (report artifact kind=state). Saved in the Main report → **resume**: "continue /
  retry / finish it" in the same chat restarts from that state (runtime.start_chat).
- **Explicit profile**: `parse_profile()` ("Use Chrome profile asdf", "my Work profile", 'profile "Profile 2"');
  `tools/browser.resolve_profile()` order = argument → task state → request → saved preference (never ''); explicit
  choice saved as preference + lesson; session reused only for the SAME profile; bridge `chrome.launch` no longer
  silently reuses a Chrome on another profile (switches, or refuses an unknown one); `GET /chrome/active` verified
  before acting; browser agent stops immediately (start→finalize) if the profile can't be opened.
- **Observe→Act→Observe→Verify** in the browser graph: every action is `attempted`, then the next observation marks
  it `succeeded` / `no_effect` / `failed`; new **verify** node checks the real page (LLM verifier at tier≥2 or for
  submit/send/buy goals; deterministic for tabs) and loops back once with a different next step if not verified;
  report status = success ONLY when verified, otherwise partial + "Not verified: …".
- **Loop detection**: browser AVOID list (actions that failed/had no effect are never repeated, LLM output
  validated against it), no two waits in a row, stop after 3 identical actions or 4 stalls; **global loop guard** in
  `AgentContext.call_tool` (identical call failed 2x or repeated 4x/8x snapshots → refused with "do something
  different").
- **Replanning**: reviewer sees failed attempts, must give WHY + a DIFFERENT strategy, keeps verified work;
  fallback retries carry the failure reason.
- **Verification in answers**: synthesis gets the TODO with verified flags; only status=success may be called done;
  Main report = partial if any TODO unverified/failed; never "should I continue?" for requested work.
- **Learning**: `data/lessons.json` (worked / avoid / failed / preference, per site or agent), injected into the
  browser agent's prompt; Memory page → "Lessons learned" (delete); API `GET/DELETE /api/lessons`.
- Tests: `tests/test_exec_engine.py` (9). Live: "Use Chrome profile Challa and open HN, top title" → profile Challa
  end-to-end, verified, 7.7k tokens.
Next for this area: Computer/desktop agent uses the same verify node already (shared graph); add TODO panel in the
chat UI (events `TODO:` already emitted with data.todo); re-run TEST_CASES 5.11/5.15/5.16 (see TEST_RESULTS.md).

### 14b. Model-provider health (found 2026-10-05 during TEST_CASES 5.16)
Owner's OmniRoute providers status: cxa (Codex GPT) 503 "transport not configured"; dva (Devin swe/kimi/deepseek)
500 "DEVIN_AGENTIC_HOME must be an absolute path"; zc (GLM) 502 "spawn zcode ENOENT"; gemini-3.7-flash-high 429
quota; cc Claude 5.x 400 on `temperature` (our bug → fixed). Working: agy gemini-3.7-flash low/medium.
Fixes: misconfig errors park a model 30 min (routing.retry_after), success clears cool-down (mark_ok), chains try
up to 8 models and always end with RELIABLE_TAIL (other providers), models rejecting `temperature` are retried
without it and remembered (routing.NO_TEMPERATURE, also for tool-calling ChatOpenAI). Owner action: fix those
providers in the OmniRoute dashboard to get GPT/GLM/Devin/Claude-5 back.
Also fixed: loop guard no longer blocks observation tools (snapshots repeat legitimately); LangGraph
recursion_limit 80→400 (25-tab task needs ~100 steps; agents self-bound via max_steps). TEST_CASES 5.11 ✅, 5.15 ✅.

### 14c. Batch-site tasks (LeetCode) fixes
- **Cheapest fit**: planner prompt + hard rule `_cheapest_fit` → algorithm/LeetCode problems go to
  competitive_programming_agent, never the repo-editing coding agents (they burned 105k tokens on one-file solutions).
- **Inputs reach the browser agent**: browser goal now includes upstream step results (`_common.context_text`, which
  skips history/exec_state items) — before, paste steps never received the solved code.
- **Deterministic paste**: `_PASTE_RE` + `extract_code` → open the URL, `set_code`, then done; browser_svc set_code
  reads the editor back (Monaco / CodeMirror 5/6 / Ace / textarea) and returns `verified` + `editor_text`; act() keeps
  structured results (ok/verified/editor) instead of stringifying them. Verified directly on leetcode.com (cm6).
- **No repeated plans**: `_no_repeat_plans` drops follow-ups that redo verified steps and rewrites repeats of failed
  steps with the failure reason + "different strategy".

### 14d. Real-profile computer use: LeetCode submit → Accepted (2026-10-06) ✅
"Use Chrome profile asdf and submit Two Sum on leetcode and get it accepted" → **Accepted 65/65** in the owner's REAL
Chrome profile asdf (Profile 6, logged in), 2,066 tokens, evidence screenshot saved.
- Real profiles can't be automated over CDP, so: bridge `POST /chrome/open_real {profile, url}` (display name or dir →
  `--profile-directory`), `GET /desktop/tree?window=…&contains=- <profile>` (Chrome puts the profile on the window's
  inner pane, not the title — picks the right window when several profiles show the same page), desktop action
  `paste` (clipboard + Ctrl+V, keeps formatting).
- Tool `leetcode_submit` (aap/tools/leetcode.py): one approval → reuse open tab or open in the profile → verify the
  window belongs to that profile → ensure editor language Python3 (LeetCode remembers C++ → "Compile Error") →
  click editor, Ctrl+A, paste → Submit → poll UIA for a visible verdict → screenshot. Only "Accepted" = verified.
- Agent `leetcode_agent` (graph `leetcode`): LeetCode GraphQL for statement + exact python3 starter signature →
  solve with the coder model in LeetCode format → local check (`_check_code`: compiles, class Solution + method,
  no stdin) → submit → on Wrong Answer/Runtime/Compile error feed the verdict back and retry (3x) → lessons.
  Main routes "leetcode … submit/accepted" directly to it.
- Name collision note: an AgentOS dedicated profile can share a name with a real profile ("asdf"); real-profile
  tools resolve by the REAL profile list.

### 14e. Status 2026-10-06 (end of session)
Green: 143 unit tests, 12 UI e2e tests, web build. Live verified: profile Challa end-to-end + verify (7.7k tok);
25 tabs (5.15); sandbox browser (5.11); **LeetCode Two Sum Accepted in real profile asdf** (2.1k tok).
Fixed today: loop guard vs observations/browser_open; recursion limit 80→400; per-run browser session cleanup
(memory leak: sessions/tabs were never closed → browser_svc /session/close + runtime close_run); tab_count only for
*opening* tabs; tool args kept structured (_short_args); planner-written profile names must be real
(_known_profile) — the "logged" bug; explicit profile saved as default by Main; paste-only vs submit mode;
DESKTOP_LOCK (computer use is serial); Python3 editor language; tab reuse; starter-signature solving + local check.
Known limits / needs owner:
- Real-desktop control shares the owner's screen: if the owner is using the PC, Windows may refuse to bring Chrome to
  the front and LeetCode's editor header isn't rendered → tool re-focuses 4x then reports honestly. Run such tasks
  while not using mouse/keyboard. 5.16 (5 problems in the real profile) failed for this reason — re-run when idle.
- Docker (TEST_CASES §13): needs reboot + ~20 GB free disk (8 GB now).
- Chrome extension (§10): owner must "Load unpacked" once. Telegram/Discord: owner tokens.
- OmniRoute providers cxa/dva/zc broken on the owner's side (see 14b).

### 14f. LeetCode: never rewrite code that was already written (2026-10-06)
- Failures are classified (`aap/graphs/leetcode.py: classify`): CODE (Wrong Answer, Runtime/Compile Error, TLE/MLE,
  local check) → back to `solve` with verdict + previous code; EXECUTION (paste, focus, language, timeout, no verdict,
  loop guard…) → retry ONLY paste/submit with the SAME code (EXEC_RETRIES=2); non-retryable (owner using the PC, not
  logged in, denied, wrong profile) → stop the run, keep the code, answer says "say continue to resume".
- Solutions persisted per problem {code, status solved→pasted/submitted→accepted|failed, verdict, verified, tries}
  in the task state (`execstate` key `solutions`; `merge` keeps the most advanced record, `handoff` lists them) and
  on disk `data/solutions/<slug>.json + .py`. `plan` checks task state → disk: verified-done problems are skipped,
  saved code goes straight to submit (no model call); "continue/retry" without slugs reuses the saved problem list.
- Main review follow-ups already carry exec_state in context, so a fresh LeetCode agent continues from it.
- Tests: `tests/test_leetcode_reuse.py` (5) — 152 unit tests green.
  **SUPERSEDED (2026-10-06):** the owner rejected task-specific Python (no hard-coded LeetCode agent/tool). Removed
  `aap/graphs/leetcode.py`, `aap/tools/leetcode.py`, `leetcode_agent`, its tests. Generic replacements below.

## 15. Self-extending, general agents (2026-10-06) — owner direction
Principle: NO task-specific Python agents. General LangGraph agents plan + act dynamically; the platform extends
itself (tools, agents, skills) and learns from experience.
- **General computer-use tools** (`aap/tools/desktop.py`): `chrome_open_profile(profile,url)` (REAL Chrome profile,
  normal Chrome), `desktop_tree(window, contains='- <profile>')`, `desktop_act` (click/type/paste/hotkey/focus;
  ONE approval per task), `desktop_wait_for(texts)` (observe → verify). Bridge safety: refuses input while the owner
  is using the PC (`/desktop/idle`), forces + verifies the target window is foreground, DESKTOP_LOCK = serial.
  "Computer Agent" (`desktop_agent`, generic desktop graph) uses them; Main composes e.g. solve → computer agent.
- **Self-extension**: `create_tool` (validate AST policy → sandbox self-test → approve → `data/custom_tools/` →
  registered for ALL agents, reloaded at startup — `core/toolfactory.py`), `create_skill`, `list_tools`.
  System agents `tool_builder_agent` (graph `toolsmith`: design → code → validate/test → fix ≤3 → create → VERIFY
  by calling it) and `skill_writer_agent` (graph `skillsmith`). Agent Builder spec may list `new_tools` → it orders
  them from the Tool Builder. Main direct-routes "create a tool / skill". Live: built `convert_temperature`,
  verified (98.6°F→37°C), 4.8k tokens. API `GET/DELETE /api/tools`.
- **Experience Curator** (`core/experience.py`): after each task writes/merges `data/skills/exp-<topic>/SKILL.md`
  (When / DO / DON'T / HOW from the real task state); before a task, Main recalls matching notes (no model call)
  into the planner prompt + handoffs.
- **Auto mode** (setting `auto_mode`, default ON): approvals auto-granted and recorded; `_ALWAYS_ASK` (destructive,
  git push, purchases/payments, passwords…) still asks. Verified live (python command auto-approved, 0 clicks).
- **Memory scopes**: each agent has its own memory (scope=agent id) + one global memory; recall = own + global;
  `GET /api/memory?scope=`, `/api/memory/scopes`.
- **Request-scoped instructions**: "use X" applies to that request only; persists only with "always / from now on /
  by default / remember" (`execstate.is_standing`).
- **Loop/similarity stop**: review rounds whose outcome fingerprint is ≥0.95 similar (case-insensitive, e.g. "hi"
  vs "Hi") stop replanning; non-retryable failures (user busy, denied, not logged in, budget, loop guard) are never
  retried; hard per-request token cap at the model-call level (budget follows the classified tier).
- `/laptop` = owner's home folder, READ-ONLY for agents (write access was declined by the safety classifier — the
  owner can decide to grant it himself).
- Website: Tools page, auto-mode toggle, memory scopes, experience notes (UI work in progress → see §15 UI).

## 16. Owner controls added 2026-10-06 (all DONE, tests: tests/test_steer_modes_cap.py)
- **Questions only as cards** — `classify_intent` turns "clarify" into a card (`seed_question`); `ask_user` forced by
  "ask me…", a seed question or a `kind=ask` mode. Synthesis/respond prompts forbid questions in text.
- **Stop button + live plan checklist** in chat (web/src/pages/Chat.tsx, components/RunBlock.tsx PlanChecklist).
- **Model picker + effort picker** in the chat box (effort is the user's; the AI never changes it mid-run).
- **Token limits**:
  - `token_budget_mode="off"` (default) → work never stops for tokens.
  - Estimates are ×1.5; if one exceeds the normal budget, a card asks "Use more tokens / Keep it lighter" (default: use more).
  - A limit the USER writes ("use only 10 tokens", "within 2k tokens", "500 tokens max") → `routing.parse_user_cap`. It is HARD:
    - counted in generated tokens and clamped on every call (complete/stream/tool loops);
    - never asks "use more";
    - caps < 400 answer directly with no planning;
    - the answer ends with "Used X of your N-token limit" / "Stopped at your N-token limit".
- **Talk while it works** (change of track):
  - The chat input stays live while busy ("Tell it" button) → `POST /api/runs/{id}/message` → `RunContext.add_steer`.
  - Every agent sees it at once: `_common.system()` appends `steer_block`, and `call_tool` attaches `USER_UPDATE` to tool results.
  - Main Agent `_apply_steer` (in dispatch; `after_collect`/`after_review` route back to dispatch when new messages arrive) re-plans the pending steps and adds redo steps for finished work. Messages show inline as "↪ while working".
- **100 slash modes** (`aap/modes.py`):
  - Typed as `/deep-dive /fact-check topic`; `/modes [filter]` lists them; unknown modes get a "did you mean"; there is `/` autocomplete in the chat box (`GET /api/modes`).
  - Each mode has a directive (goes to the Main Agent and every agent via `RunContext.directives`), a preferred agent (offered to the planner), an effort hint and a kind: task / chat / ask / schedule.
- **Learned model health** (`routing.py`):
  - Escalating cool-downs (×2 per consecutive failure, up to 12 h for misconfigured providers), persisted in `data/model_health.json`.
  - The chain order puts recently-OK models first, untried next, and failing ones last.
  - A timeout fails over to the next model.
  - A mid-stream error (OmniRoute sends 200 then a 429) fails over too.
  - Only a real connection error switches to offline mode.
- **Seriousness**: "critical" only when the request mentions money/security/production/delete/etc. (`_CRITICAL_RE`); otherwise max "deep".
- **Planner**: deliverable files (websites/apps/scripts) → coding_agent, never a spawned worker (spawned workers can't write files).
- **Shared OPERATING_RULES** in every agent prompt (`_common.system`): no inventing, verify before claiming success, assume and continue, change strategy after a failure, lead with the result.
- **Phone push** (`aap/notify.py`, ntfy):
  - Settings: `notify_ntfy_url` (default https://ntfy.sh/omreddyenduri), `notify_on` (all | finish | off), `notify_priority`.
  - Sent on every run that finishes, fails or is stopped, and on every question card/approval that waits for the owner.
  - Settings → Notifications has a test button (`POST /api/notify/test`). Mock/test runs never push.
  - **The AI working on this repo should also push to that topic whenever it stops.**
- `leetcode_agent` removed from the registry (owner: no task-specific Python).
- OmniRoute state (2026-10-06): many providers are broken on the gateway side (zcode ENOENT, Codex 503, Devin 500, Gemini/Claude quota 429). Only a few models answer. Fixing providers in OmniRoute is the owner's job. Health learning keeps us fast meanwhile.
- **Push control (update)**:
  - `notify_enabled` master switch, default **OFF** (owner turned it off after test chats spammed the phone).
  - Pushes only for runs the owner started in the UI (`ChatBody.source="ui"` → `run.notify`) and for scheduled jobs. API/test runs never push.
- **Scheduled jobs / reminders / heartbeats are RUNS, not chats**: `runtime.start_run()` (no conversation). Results go to Runs and the Home feed. The 14 old reminder chats were removed; their runs are kept.

## 17. Final owner test PASSED (2026-10-06 15:57): vague prompt → LeetCode Accepted in real profile asdf
- Prompt was only: "open my asdf chrome profile and submit a leetcode problem and get it accepted". The agents did the rest themselves:
  - picked Two Sum (via question-card defaults);
  - solved and stress-tested it;
  - opened Chrome profile asdf (Profile 6);
  - pasted the code and submitted it with Ctrl+Enter;
  - verified the result.
- Result: **Accepted, 65/65**, submission 2164122351. I checked it independently with a screenshot.
- Generic fixes this needed (no task-specific code):
  - Bridge `_enable_web_a11y`: asks Chromium render widgets for their MSAA object on every tree read, so the page's accessibility tree exists. Before this, only the browser frame was visible.
  - Bridge tree includes the `value` of Edit/Document fields (e.g. the address bar).
  - Desktop observation lists page content first, up to 300 elements.
  - `done_unseen` status: desktop clicks/pastes the bridge executed OK don't count as "no effect" (editor focus and text are invisible in UIA).
  - New `paste_at{x,y,text}` (click + select-all + paste in one step) and `locate{what}` (`desktop_locate`: a vision model finds x,y of things that aren't in the tree).
  - `llm.vision_json`, plus screenshot verification as the Computer Agent verifier's second opinion.
  - Computer Agent steps never run in parallel (one screen).
  - Raw `launch chrome` is refused; `chrome_open_profile` reuses a tab when the address bar already shows the URL.
  - Question-card options and defaults must be concrete choices.
  - `_direct_route`: real profile + solve/submit/login goes to the planner (solver → Computer Agent), not browser_agent.
  - Pillow added (`requirements-gateway.txt`) for screenshot downscaling.
- Known leftover: the final check called `wait_for 'Accepted'` several times after it was already seen. That's harmless but wasteful; next improvement: stop once a verdict is verified.

## 18. 2026-10-07 — test VM, capability suite, tools that really work (owner: "do all tests in a VM, don't disturb me")
**Test VM** = WSL2 distro `agentos-vm` (Ubuntu 24.04), capped at **6 GB RAM** (`%USERPROFILE%\.wslconfig`), mirrored
networking. Code copy `/opt/agentos`, data `/opt/agentos-data`, venv `/opt/agentos-venv`.
- Start/refresh: `wsl -d agentos-vm -u root -- bash /mnt/c/Users/raman/agent-platform/scripts/vm_start.sh` (rsync from
  Windows → `vm_up.sh` → keeps the WSL session alive). Also step 3 of `scripts/autostart.ps1` (log-on).
- Ports: gateway **:7870**, runner :8011, browser :8012; the VM's own screen = Xvfb `:99` + x11vnc (localhost) + noVNC
  **http://localhost:6081/vnc.html** (watch only when wanted; nothing ever opens on the owner's desktop). No desktop
  bridge, phone listener + pushes OFF inside the VM.
- Unit tests in the VM without touching the running copy: rsync to `/opt/agentos-next` + pytest (mock LLM).

**Capability suite (§16 of docs/TEST_CASES.md, 41 cases)**: `scripts/run_test_cases.py 16` (env AOS_API/AOS_HOME/
AOS_DATA/AOS_RUNNER/AOS_RESULTS/AOS_PROGRESS/AOS_DESKTOP=0 in the VM). `aap/selftest.py` runs it detached; API
`GET/POST /api/selftest(/run)`; **Tests page**; built-in schedule `selftest` 03:00; one phone summary at the end;
open failures → `data/selftest/OPEN_FAILURES.md`. 16.37 = tool self-check. 16.38–16.41 = owner tests T1/T2/T3/T5,
verified through the owner's VM profile (LeetCode `/api/submissions`, Codeforces mashup pages).

**VM browser for logged-in web work (M7)**: `aap/vmbrowser.py`. Settings `vm_browser_url` (Windows default
http://127.0.0.1:8012 = the VM's browser_svc), `vm_view_url`. A profile that exists in the VM browser counts as
"logged in there": `browser_open` routes the session to the VM (`svc="vm"`), `_is_real_profile` is False for it, the
planner gets a VM rule and `_prefer_vm_browser` moves Computer-Agent web steps for that profile to browser_agent.
Owner logs in once: Browser page → "VM browser" card → "Log in on the VM screen" (`POST /api/browser/vm-login`).

**Tools (TODO N2–N4)**: `aap/tools/everyday.py` (chrome_profiles, chrome_profile_path, fs_find, http_request,
download_file, page_text, now, calc), `aap/tools/commands.py` (find_command = web crawler for terminal commands,
memory-first, saves to memory scope `commands`; known_commands). `sdk.UNIVERSAL_TOOLS` = read-only helpers every agent
may call. `aap/toolcheck.py` + `GET/POST /api/tools/check` + Tools page "Check all tools": live in the VM
**32 ok / 0 broken / 15 skipped (screen/side effects)**.

**Engine improvements**: budget-aware review (M14), parallel research extraction (M15), browser `wait_for` + stop after
a verified verdict (M16), same-agent chain collapse (M17), daily 21:00 phone summary (`aap/daily_summary.py`,
schedule `daily_summary`). Runner fixes: roots split (M19), `path_keys` mapping for sandboxed Python (M20),
`output`/`result` variables returned, search_code glob (M21). See MISTAKES.txt M14–M21.

## 19. 2026-10-08 — CURRENT POSITION (read this first)
- **T1 PASSED** in the test VM (LeetCode Two Sum → Accepted for ChallaAjay, verified in LeetCode's own submission
  list, 123 s / 21k tokens). VM browser profile `asdf` is logged in to LeetCode + Codeforces (owner did it once via
  noVNC; login windows are a PLAIN Chrome — Google refuses sign-in under automation, see M24).
- T2–T5 / F1 / daily question: run via the Tests page (`POST /api/selftest/run {"sections":["16.35",...]}`).
- Big changes since §18 (details: MISTAKES.txt M22–M45):
  - **Supervisor** (aap/core/supervisor.py): agents report every 5 steps; Main Agent redirects/stops loops.
  - Browser: one tab per plan step (release/close), hidden elements skipped, icon buttons named by test ids,
    SPA settle-wait, code-judge sequence (set_code+language → Submit → wait_for{new_only}), language picker
    switching with template-settle, no invented sites, rejected actions re-asked, workspace files open as file://.
  - Planning: step cap per tier with sibling merge, follow-ups keep order, budget-aware polish, explicit agent
    builds, VM-browser routing (also on machines without a desktop bridge), team members one tier lower.
  - Runtime: Stop works between any nodes / before any model call; honest "models not answering" on outages.
  - Solver: report starts with "Problem: title (#n) — url"; LeetCode code converted to `class Solution`.
- Unit tests: 229 pass (VM, mock LLM); UI e2e 17 pass. Next steps: TODO.txt §8.
