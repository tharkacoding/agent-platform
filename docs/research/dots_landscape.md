# Dots and the persistent-agent landscape (research for AgentOS)

Date: 2026-10-05. Method: web search plus GitHub API/raw-file reads. "UNVERIFIED" marks claims taken only from secondary blogs or not checked in code. Product details are from press and blog coverage, not OpenAI docs.

## 1. Commercial and reference products

**OpenAI Dots** (announced about Sept 29, 2026; secondary sources say powered by "GPT-6 Astra", unverified). Sources: [DataCamp](https://www.datacamp.com/blog/openai-dots), [MacStories](https://www.macstories.net/stories/openais-dots-a-first-look/), [ContentGrip](https://www.contentgrip.com/openai-dots-work-layer/).
- Persistent goals: one long-lived agent per user (capped at one for now). It keeps working after the chat ends and carries context day to day.
- Triggers: schedules and events such as recurring customer feedback. It also does "proactive research" in the background with read-only tools, forming its own memories from connected apps.
- Check-ins and approvals: a four-tier action model: act without asking / act if pre-approved / ask first / hand off to the human. An "auto-review" layer checks account-affecting actions against user rules. Sensitive operations such as password changes always need a human.
- Connectors: "4,000+ apps" through the ChatGPT plugin ecosystem. Surfaces are ChatGPT, Slack and Teams, with limited texting. Context persists across channels.
- Computer use: each Dot has its own cloud Linux VM with a browser. Local machine access (files, fallback browsing) is opt-in through the desktop app.
- Memory: inherits ChatGPT memories plus its own. Per DataCamp, deleting memories means deleting the whole Dot.
- Effort tiering: conversations don't count against usage limits, but "deeper work draws on a plan allowance" (terms unpublished). No technical token-efficiency detail is public.
- Limits: one Dot per user, one connected Mac at a time, no search inside Dot conversations. The direction is teams of Dots under an orchestrator.

**Meta Muse**: cloud-only, runs on Meta's servers and model; free tier plus $20 and $100 plans. Can book, shop and negotiate (secondary sources, UNVERIFIED). No architecture details found.
**Manus**: Meta-acquired cloud agent with a full virtual computer; roughly $20/month; limited persistent memory (secondary source).
**Claude Cowork**: Anthropic's desktop agent working on the local machine through OS accessibility/computer-use APIs, with persistent contextual memory (secondary source).
**ChatGPT agent**: task-scoped agent with browser and tools. Not researched in depth.
**OpenClaw**: self-hosted, runs on your own machine, continuous persistent memory, chat-app front ends (secondary source).
**Hermes Agent** (Nous Research): runs anywhere on any model. Its "Curator" reflects on finished tasks and writes Markdown skills it loads next time, so recurring work gets cheaper and better. Agent-curated memory, Telegram/Slack front ends (secondary: [Composio comparison](https://composio.dev/content/hermes-agent-alternatives), [aimultiple](https://aimultiple.com/always-on-agents)). Claims such as star counts are UNVERIFIED.

## 2. Open-source clones (verified from repo files and the GitHub API)

### 2.1 Anil-matcha/open-dots - https://github.com/Anil-matcha/open-dots
- **LICENSE**: file begins "MIT License / Copyright (c) 2026 Anil-matcha". GitHub reports MIT. About 5.4k stars. Self-described "early prototype".
- **Architecture**: Next.js client over HTTP+SSE to a FastAPI server with SQLite. Optional Docker/Playwright computer runtime (`runtime/Dockerfile`, `runtime/driver.mjs`). Closest to AgentOS's stack.
- **Concepts**: deny-by-default Action Gateway. Actions are classified read / write / external. Unknown actions are rejected, a preview text is mandatory, and `requires_approval` actions park in an ApprovalBroker that waits for allow/deny/expired. Every lifecycle event is audited with `redact_sensitive()` stripping keys and tokens. Connectors go through Composio OAuth. Web search is user-initiated only (`/search`, You.com MCP). Computer containers are bot-scoped with a read-only root filesystem, dropped capabilities and resource limits. Credentials are encrypted at rest. No persistent goals or triggers: it is a chat agent with approvals.
- **UX**: `ApprovalCard.jsx`, `AuditPanel.jsx`, `ComputerPanel.jsx`, `ModelPicker.jsx`, a mascot avatar, a connector marketplace.
- **Reusable (MIT)**: `server/app/services/action_gateway.py`, `approval_broker.py`, `secret_store.py`, `docker_computer_provider.py` and the `computer_provider.py` abstraction (fake / docker / remote adapters), `routers/approvals.py`, `routers/audit.py`, `client/components/ApprovalCard.jsx`.

### 2.2 Shashankss1205/OpenDots - https://github.com/Shashankss1205/OpenDots
- **LICENSE**: "MIT License / Copyright (c) 2026 Shashank Shekhar Singh". The repo also has a NOTICE file (not read). Small (about 20 stars), Python package `opendots/`.
- **Architecture**: engine plus SQLite store, local web dashboard (`opendots/web`), `service.py`, systemd/Docker deploy. Plugin system (`plugins.py`, `docs/PLUGINS.md`), pluggable model providers, notification channels.
- **Concepts**: the best reference for persistent goals and event triggers.
  - A goal is persistent owner config, re-evaluated on heartbeats.
  - Event sources are heartbeat, manual, JSONL feed, HTTP POST and GitHub polling. Subscriptions filter by type, source and repo and route events to targets.
  - Before accepting a change the agent runs owner-selected check commands with a 30s timeout ("evidence", `evidence.py`).
  - Writes need `/review` + `/approve`. Reads and notes run automatically.
  - Hard budgets: 60 planning calls per target per day, 120 global, queue cap 100, one active task per target. Budget exhaustion blocks work until the owner reviews.
  - The planner receives the goal, the event, a snapshot, recent notes and prior results, and returns concise decision summaries, not raw reasoning.
- **Reusable**: `opendots/goals.py`, `engine.py`, `sources.py`, `evidence.py`, `sandbox.py`, `process_guard.py`. Read `docs/GOALS_AND_EVENTS.md`, `docs/EVENTS.md`, `docs/NOTIFICATIONS.md` first.

### 2.3 composio-community/open-dot - https://github.com/composio-community/open-dot
- **LICENSE**: NO LICENSE FILE (raw `/LICENSE` returns 404), the GitHub API reports no SPDX license, and the README shows no license statement. Treat it as all rights reserved: study it, do not copy code. Confirm with the authors before reuse.
- **Architecture**: Electron Mac app wrapping a local Next.js server. Uses the OpenAI Responses API stream, plus OpenRouter, Composio, E2B or Docker computers, SQLite, and a Keychain-backed vault (`src/server/vault.ts`).
- **Concepts**:
  - Runtime (`src/server/agent/runtime.ts`): an inbox `pump()` batches messages, `turn()` calls `drive()`, which loops at most 60 steps. History is trimmed to 80 items or 160KB, always cutting at a user message. Reasoning models get `effort: "medium"` for main runs and `"low"` for dot-to-dot queries. This is explicit effort-tiering.
  - Review (`agent/review.ts`): a small, cheap model matches a proposed action against user rules of the form "When your dot wants to X, it should allow|ask|never". It returns only rule indices, so output is tiny. Precedence is never > ask > allow, and it fails closed to "ask" on error.
  - Triggers: Composio triggers wake a dot on app events (email, issues) within seconds (`triggers.ts`). Cron-style routines run through `scheduler.ts`, for example a weekday 8am inbox brief.
  - Computer (`computer/browser.ts`, `cdp-helper.ts`, `dom-actions.ts`, `shell.ts`, `cloud.ts`): persistent logged-in browser with live view (`/api/dots/[id]/live` and `/screen`). Computer actions require acknowledging safety checks.
  - Memory: the dot remembers facts and saves reusable skills for tasks it repeats.
  - Also: `bus.ts` (event bus), `snapshot.ts`, and `AGENTS.md` / `CLAUDE.md` in the repo.

### 2.4 CopilotKit/OpenDots - https://github.com/CopilotKit/OpenDots
- **LICENSE**: "The MIT License / Copyright (c) Atai Barkai". `deployment/computers/LICENSE.openbot` indicates vendored OpenBot code under its own license (not read; check before reuse). About 3.3k stars.
- **Architecture**: React/TypeScript front end on the AG-UI protocol and CopilotKit runtime, TanStack AI with OpenAI-compatible providers, Docker compose for "computers". Computer supervisor in `deployment/computers/` (`supervisor.Dockerfile`, `harden-supervisor.mjs`).
- **Concepts**: Spaces (shared document homes with nested pages the Dots read and write); Specialist Dots (named roles with own instructions and per-tool permissions); per-Dot computer (persistent browser profile, files, shell, server-side credentials); WebRTC voice calls that delegate to background agents so work continues during the call; Slack via a Channels SDK with workspace/user allowlists; human-in-the-loop card that pauses the conversation (for example before saving a page); user-managed memory.
- **UX files**: `src/client/ComputerPanel.tsx`, `ComputerToolCard.tsx`, `CallView.tsx`, `ChatTranscript.tsx`, `Mascot.tsx`, `src/browser/security.ts`.
- **Reusable**: the AG-UI / HITL card pattern, Spaces, per-agent permission model.

### 2.5 demeyer1/awesome-dots - https://github.com/demeyer1/awesome-dots
- **LICENSE**: "MIT License / Copyright (c) 2026 feder-cr". Only three files (README, CONTRIBUTING, LICENSE). Curated list, no code. Categories: open-source dots, always-on agents, browser, memory, tools/skills/plugins.
- Pointers from the list: browser-use, mem0, Playwright MCP, Secure MCP Tunnel, "opendot" (Claude Code in containers with approval gates, Slack/CLI), "dots" (planner/executor/watcher runtime), AgentForEach (one serverless agent per user). Repo links for those entries were not verified.

## 3. Prioritized features for AgentOS (LangGraph + FastAPI)

1. **Action gateway with risk classes, approval and audit (P0).** Pattern: open-dots `action_gateway.py` and `approval_broker.py`. Route every tool call (browser, desktop, shell, connectors) through one FastAPI service, `gateway.authorize(action)`, returning allow / ask / deny / handoff from a registered-action table with risk class read / write / external. In LangGraph put a `gate` node before each tool node. On "ask", call `interrupt()` with an approval payload (preview, args, risk), persist it in an `approvals` table, and push it to the UI via SSE and to Telegram buttons. Resume with `Command(resume=decision)`. Add expiry and a redacted audit row per lifecycle event.

2. **Four-tier autonomy policy plus user rules (P0).** Map Dots' tiers (auto / pre-approved / ask / handoff) to per-agent, per-tool policy stored as data. Let users write rules like "when the agent wants to send email, ask". Precedence never > ask > allow, fail closed to "ask". Prebuilt agents ship defaults; the Agent Builder exposes the policy matrix.

3. **Cheap rule-matching reviewer (P0, token efficiency).** Copy the open-dot `review.ts` idea: a small model receives the action summary plus a numbered rule list and returns only matching rule indices as JSON. Run deterministic matchers first (tool name, domain, amount regex) and call the model only when ambiguous. Cache decisions by (rule-set hash, normalized action). Use structured output with a tiny `max_tokens`.

4. **Persistent goals as first-class objects (P0).** Pattern: Shashankss1205/OpenDots `goals.py`. Table `goals(id, owner, text, status, budget, next_review)`. Each goal has a LangGraph thread_id with a SQLite/Postgres checkpointer so a run can be suspended for days. Each wake-up loads goal, new events, recent notes and last result, makes at most one focused step, writes a short decision summary, and sleeps. UI shows a goal timeline.

5. **Unified event bus: triggers, schedules, webhooks (P0).** Define `Event{source,type,payload,dedupe_key}` and an `events` table, with subscriptions that filter and route to an agent or goal. Producers: APScheduler cron jobs, a `POST /events` webhook, Telegram messages, file watchers, polling connectors (GitHub, email). One worker (asyncio queue or arq) dequeues, enforces one active run per goal and a dedupe window, then invokes the graph. Heartbeat events cover "check in on this goal".

6. **Budgets and effort tiers (P1, token efficiency).** Combine OpenDots daily caps (60 per target, 120 global, queue 100) with open-dot effort routing (medium for main work, low for sub-queries, small model for review/titles). Implement `ModelRouter.pick(task_kind, goal_tier)` plus a `usage` table. Check per-goal and per-day budget in the gateway and pause in a "needs owner review" state when exhausted. Default background wake-ups to a cheap model and escalate only when the cheap pass finds something to act on (triage-then-act).

7. **Context hygiene (P1, token efficiency).** Trim by turn boundary to a cap (open-dot: 80 items or 160KB, cutting at a user message). Add a summarize-old-turns node, tool-result truncation with the full result stored as a file reference, and persist decision summaries rather than raw reasoning. Load tool schemas lazily per agent. Keep static system-prompt prefixes stable for prompt caching.

8. **Self-written skills and curated memory (P1).** From Hermes (Curator) and open-dot ("saves skills for tasks it repeats"). After a successful run, a low-effort curator node proposes a Markdown skill (steps, tools, pitfalls) and shows it for approval. Skills live in `skills/*.md` with a short description index; the agent loads only the index and fetches a body on demand. Memory is split into profile facts, episodic notes and skills, each with a viewer and per-item delete. Dots reportedly cannot delete single memories, so this is an AgentOS advantage.

9. **Proactive read-only research (P1).** A scheduled background pass with a read-only tool whitelist (gateway-enforced) that updates memory and produces a digest. Writes are impossible in this mode, so no approval is needed. Cheap model, per-day budget.

10. **Computer provider abstraction (P1).** Use the open-dots `computer_provider` interface with fake / docker / remote adapters, one container per agent, a persistent browser-profile volume, read-only root filesystem, dropped capabilities and resource limits. Add a live screen view and take-over button in React (like `ComputerPanel`), streamed over SSE or WebSocket. Browser steps run through Playwright (or browser-use) behind the gateway. Desktop control is opt-in on the host through a local companion with its own allowlist.

11. **Evidence checks before accepting work (P2).** From OpenDots `evidence.py`. A goal or agent definition can carry check commands or assertions (tests, output schema validation, lint) run in the sandbox with a timeout. Failing evidence blocks "done" and triggers a retry or a check-in.

12. **Check-in channel with approval cards everywhere (P1).** One notification service with channel adapters (web UI, Telegram inline buttons, later Slack) and a shared card schema: what, why, preview, Approve / Deny / Edit. Quiet hours and digest batching keep check-ins rare. Key threads on the goal, not the channel, so context is shared across channels (the Dots cross-channel behavior).

13. **Specialist agents with scoped permissions (P2).** From CopilotKit OpenDots: each agent definition (Builder output) has instructions, a tool allowlist, a policy, a model tier and an optional computer. The Main Agent delegates to specialists as LangGraph subgraphs or tools and receives summaries, not transcripts. This is the "team of Dots" direction.

14. **Secrets vault and redaction (P1).** From open-dots `secret_store.py` (encrypted at rest) and its audit redaction. Store connector tokens in an encrypted table or the OS keyring, inject them server-side at tool execution, never into model context. Apply redaction to logs, traces and approval previews.

15. **Connector layer via MCP and OAuth (P2).** Do not build thousands of integrations. Support MCP servers plus an optional Composio-style broker (open-dots uses Composio OAuth; open-dot uses Composio triggers). Every connector action is registered in the gateway table with a risk class and preview template. Trigger-based wake-ups reuse the event bus (item 5).

## 4. Caveats
- Dots details come from third-party coverage within days of launch; verify against OpenAI docs before copying product claims.
- open-dot (Composio) has no license: patterns only.
- CopilotKit OpenDots vendors OpenBot material under a separate license file; review it before reusing `deployment/computers` code.
- Several clones describe themselves as prototypes. The open-dots README says its runtime "is not a hardened sandbox for hostile websites".
- Meta Muse, Manus, ChatGPT agent and OpenClaw internals were not verified beyond blog summaries.
