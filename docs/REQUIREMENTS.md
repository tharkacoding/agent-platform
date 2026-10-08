# AgentOS — high-level requirements (owner request 2026-10-08)

> Not the inner details: the big picture of **why AgentOS fails / wastes tokens / loops**, what it must do instead,
> and how it **improves itself from its own mistakes** ("anything you are doing should be done by itself while it
> improves with mistakes"). Detailed incidents: MISTAKES.txt (M1–M45). Ranked work: TODO.txt §8.

## 0. The guiding rule — a self-improving loop
Every run goes through: **act → check the real result → post-mortem → write the lesson → next run reads it**.
Nobody (not the owner, not Claude) should have to find the same mistake twice.
- Built: post-mortem after every run (core/postmortem.py) → lessons per site/agent (execstate) → planner + agents
  read them before acting; self-learned site aliases (site_aliases.py); nightly self-test (Tests page, 03:00);
  supervisor during runs (core/supervisor.py).
- Next: the nightly self-test failures are diagnosed by AgentOS itself (Self-Improvement Agent), which proposes the
  fix (prompt/rule/tool change) for the owner to approve, re-runs the failed case, and keeps it only if it passes.

## 1. Why it fails — the 7 root causes we actually saw
| # | Root cause | What it looked like | Requirement |
|---|---|---|---|
| 1 | **Acting without understanding the page** | read the whole page (15k chars) every step, chased closed menus, clicked "Debug" for "Run" | Observe only what is visible and named; act on named elements; one deterministic path for known flows (code judges, forms) |
| 2 | **No sense of progress** | 30+ steps repeating, bouncing between pages, wandering to invented sites | Supervisor checkpoints every 5 steps: loop/ping-pong/stray/no-progress → redirect or stop; never guess URLs |
| 3 | **Claiming instead of verifying** | "Accepted" read from the page's statistics; chat answer pretending to delegate | A result counts only if the real target shows it *after* the action (new_only verdicts, LeetCode's own list) |
| 4 | **Wrong format for the target** | stdin code for LeetCode, Python in a C++ editor, template overwrote the code | Know the target's format (judge signature, editor language) and assert it right before submitting |
| 5 | **Environment mistaken for logic bugs** | 200k tokens "debugging" correct code because pytest was missing | Classify failures: environment → repair once (install/login/start); logic → debug |
| 6 | **Plans too big / steps fighting** | 8 parallel research agents for a weekend trip; two agents in one tab; follow-ups out of order | Step cap per seriousness, merge same-agent work, one tab per step, keep follow-up order |
| 7 | **Not learning across runs** | the same error on the next run of the same site | Post-mortem lessons per site, read before planning (built) |

## 2. Token & time budget requirements
- Every request has an estimate; > 2× the estimate is a failure category (OVER_BUDGET) that teaches the planner.
- Sub-agents of a team lead work one tier lower; polish follow-ups only within the remaining budget.
- Observations are compact (visible, named elements; page text capped); screenshots/vision only when the tree can't
  show it.
- Model outages are reported honestly ("models not answering") and retried later — never fake answers.

## 3. Memory requirements ("improve its memory understanding")
- **Lessons** (what failed + what to do instead) keyed by site/agent — written automatically by the post-mortem.
- **Experience notes** per task kind (do / don't) — written after each task.
- **Preferences** scoped correctly ("asdf for leetcode" ≠ global default).
- **Site knowledge**: aliases learned from visits; site memory of working element paths.
- Memory is read **before** planning and **before** each agent acts; stale or wrong lessons can be deleted on the
  Memory page (Lessons tab).

## 4. New skills that would improve AgentOS (proposed)
1. **Code-judge skill** (LeetCode/Codeforces/AtCoder): find problem → solve with stress test → convert to judge
   format → submit → read verdict → on Wrong Answer, use the failing test to fix and resubmit. (Most of this is now
   built; package it as one reusable skill.)
2. **Login-state skill**: detect "not logged in" early on any site and ask the owner once (VM login window).
3. **Form-filling skill**: label → field mapping, validation messages, confirmation page check.
4. **Page-understanding skill**: summarize a page into a small task-relevant model (forms, primary actions, results)
   instead of sending the whole DOM every step.
5. **Command-cookbook skill** (built: find_command): learn terminal recipes once, reuse from memory.
6. **Self-test skill**: write a new capability test case from any failed owner request, add it to the nightly suite.
7. **Cost-guard skill**: predict tokens before running, pick the cheapest plan that meets the goal, report spend.
8. **Media skill** (YouTube/Spotify): search → play → next, verified by the player state, not the URL.
