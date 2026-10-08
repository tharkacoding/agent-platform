# Souls & workspace persona files — research notes (2026-10-05)

## OpenClaw (MIT, © 2026 OpenClaw Foundation) — docs.openclaw.ai/concepts/agent-workspace
Workspace bootstrap files, injected at session start:
| File | Purpose | Limit |
|---|---|---|
| AGENTS.md | operating instructions, rules, priorities | part of `bootstrapMaxChars` (20k per file) |
| SOUL.md | persona, tone, boundaries (loaded every session) | 20k |
| IDENTITY.md | agent name, vibe, emoji | 20k |
| USER.md | stable user prefs, communication style, projects | separate 4,000-char budget |
| TOOLS.md | tool notes/conventions (workspace) | 20k |
| HEARTBEAT.md | checklist run on periodic heartbeat | keep brief |
| MEMORY.md | curated long-term memory, main private sessions only | 20k |
| BOOT.md / BOOTSTRAP.md | startup checklist / one-time first-run ritual | — |
| memory/YYYY-MM-DD.md | daily logs (today + yesterday loaded) | — |
Total bootstrap cap `bootstrapTotalMaxChars` = 60k.

## Hermes Agent (MIT, © 2025 Nous Research) — hermes-agent docs "personality"
- `~/.hermes/SOUL.md` (or `$HERMES_HOME/SOUL.md`) = durable identity, slot #1 of the system prompt; starter created
  if missing, never overwritten; prompt-injection scanned before inclusion.
- AGENTS.md = project-specific instructions (not identity).
- `/personality <name>` = session overlay. Presets: helpful, concise, technical, creative, teacher, kawaii, catgirl,
  pirate, shakespeare, surfer, noir, uwu, philosopher, hype; reset with `none|default|neutral`. Custom ones in
  `~/.hermes/config.yaml` → `agent.personalities`.

## What AgentOS adopts (own templates written from scratch — no text copied)
- `data/workspace_md/{AGENTS,USER,IDENTITY,TOOLS,HEARTBEAT,MEMORY}.md` (OpenClaw layout; USER.md 4k cap).
- Multiple selectable souls `data/souls/<id>/SOUL.md` (Hermes-style switching): default, teacher, engineer,
  researcher, coach, concise, friend; `/soul <id>` and `/souls` in chat; per-conversation active soul.
- IDENTITY.md is the source of truth for the user-changeable assistant name/emoji/vibe.
- Token tiers: tier 0-1 get a 2-3 line soul summary + user/assistant name only; tier ≥3 full files capped.
- Importer copies SOUL/USER/AGENTS/IDENTITY/TOOLS/HEARTBEAT from an OpenClaw workspace (~/.openclaw/workspace) or
  Hermes home (~/.hermes); the imported SOUL becomes a selectable soul. Sources are never modified.
