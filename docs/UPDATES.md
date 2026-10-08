# What changed in AgentOS (2026-10-07 → 10-08) — plain-language list

Every item came from a REAL failure seen in a test (details: MISTAKES.txt M14–M45). From now on the same kind of
learning happens inside AgentOS itself (see "Self-repair" at the end).

## Testing setup
1. Test VM (WSL2 `agentos-vm`, max 6 GB, hidden — never touches your screen); watch it at localhost:6081.
2. Tests page + 41 automatic capability tests, nightly at 03:00, one phone summary.
3. Your real tests T1–T5 added, checked against LeetCode's / Codeforces' own records (not the agent's claims).
4. Tool self-check (Tools page → "Check all tools"): 32 tools work, 0 broken in the VM.

## New tools (so agents stop writing throwaway code)
5. chrome_profiles / chrome_profile_path (where is "asdf", which folder, which email).
6. find_command (searches the web for terminal commands and saves them to memory) + known_commands.
7. fs_find, http_request, download_file, page_text, now, calc.

## Smarter planning (fewer tokens)
8. Budget-aware review: no endless "polish" steps after success (284k → 137k tokens for a trip plan).
9. Step cap per seriousness; same-agent steps merged; team members work one tier lower.
10. Follow-up steps keep their order (paste before submit).
11. "I need an agent that…" really builds the agent.
12. Acting on a website is never answered as small talk (no more fake "let me delegate…").

## Browser agent (the biggest fixes)
13. Supervisor: every 5 steps the Main Agent checks for loops / bouncing / wandering / no progress and
    redirects or stops the agent.
14. Reads only what is visible (hidden menus were making it "close" menus that weren't open).
15. Icon buttons get real names (it pressed Debug instead of Submit).
16. One tab per step (two agents were fighting in one tab).
17. Waits for single-page apps to render (it was reading blank pages).
18. Never invents websites (it went to www.in.com, www.problem.com…).
19. Code judges: puts the code in, switches the editor language (it was C++), waits for the site's template,
    submits, and reads the verdict that appears AFTER submitting (not the page's statistics).
20. Opens files it wrote (/workspace/site/index.html) directly.

## Coding / solving
21. Solver names the problem first (title, number, URL) so the submit step uses the right one.
22. LeetCode code is converted to `class Solution` (stdin code is refused).
23. Missing python/pytest is repaired instead of "debugging" correct code (200k tokens saved).
24. Research extraction runs in parallel (270 s → 57 s); only major critique issues trigger a rewrite.

## Safety & honesty
25. Stop works instantly at any point.
26. If all AI models are down it says so (no fake answers marked success).
27. "Always use asdf for leetcode" applies to LeetCode only (it had become the default for every site).
28. Log-in windows in the VM are plain Chrome (Google refused sign-in under automation).

## Self-learning / self-repair (AgentOS does this by itself now)
29. Post-mortem after every run: it works out WHY a run failed or wasted effort (loop, wandered, not verified,
    wrong format, environment, model outage, over budget) and saves the lesson + what to do instead.
30. The next run on that site reads those lessons before planning.
31. Site names learned from every site it visits (no hand-made list needed).
32. Daily 21:00 summary to your phone.

## Results so far (VM, your account ChallaAjay)
- T1 ✅ Two Sum Accepted (123 s) · F1 ✅ Valid Parentheses Accepted · Daily question ✅ Remove Outermost Parentheses.
- T2 ❌ 1 of 5 (Add Two Numbers) · T3 waiting for Codeforces login check · T4 fixed, re-run pending ·
  T5 needs 3 unsolved failed problems in the account (it has 1).

## N9 — the AgentOS extension per Chrome profile (2026-10-08)
33. One extension install per Chrome profile; the hub keeps several connected at once (socket `?profile=`; the
    extension reports profile name + Google email; name set in its popup or bundled `config.json`).
34. Owner chooses which profiles AgentOS may use: Browser page → Chrome extension → per-profile switch **Allow
    AgentOS to use this profile** (default OFF) + its own allowlist (setting `extension_profiles`).
35. `browser_open(profile=X)` goes through X's extension when it is connected AND allowed (before the VM copy or
    screen control), with the usual approval; fresh tab, never the owner's current one.
36. Linux VM: the login window loads the extension pre-paired (config.json: token + gateway url + profile), so the
    profile appears on the Browser page by itself. New tool `chrome_extension_profiles` (all agents).
    On Windows the owner still does the one-time "Load unpacked" per profile (extension/README.md).

## 2026-10-08 later
37. **Mascot** (page-mascot, MIT): a fox in the bottom-right corner that follows your cursor; its label shows what AgentOS
    is doing ("Working on your task…", "Checking: 16.39") or "Ready". Settings → Appearance switch.
38. **Superpowers** (obra/superpowers, MIT): its 15 skills are imported; coding / debugging / testing / review agents get a
    short index and read the full skill before real work. Settings → Methodology switch.
39. **Model failover**: if the first models fail (rate limits, "model not supported"), AgentOS now tries EVERY other model it
    knows (other roles' chains + everything the gateway lists), waits once for the earliest recovery, and only then says
    the models are down.
40. **Schedules in plain English**: pick "Every week · Wednesday and Friday · 6:30 PM" — the cron is built for you; the list
    shows "Every day at 9:00 AM". Raw cron stays under "Advanced".
41. Fixes: Cloudflare/captcha pages → AgentOS asks you (phone + VM screen) and waits instead of fighting; the text UI no
    longer crashes on a timer race; a test no longer depends on test order.

## LangSmith + agent inventory (2026-10-08, top priority)
42. **docs/AGENTS.md** — all 37 agents with their real LangGraph nodes + Mermaid diagrams, generated from the code
    (`python scripts/gen_agent_docs.py`).
43. **LangSmith tracing** (`aap/tracing.py`, Settings → Tracing): switch + API key; every run named by agent, graph steps,
    LangChain models and every OpenAI-SDK model call traced; off by default (prompts go to the cloud).
44. **Prompts editable in LangSmith**: "Push agent prompts" creates `agentos-<agent>` in the Prompt Hub; with "Use prompts
    edited in LangSmith" on, agents use your edited text within 2 minutes (code prompt = fallback).
45. **langsmith_runs tool** for the Self-Improvement Agent (recent failing runs, seconds, tokens).
46. **"Play N songs on YouTube"** flow: search box → click result → read the player (really playing, not an ad) → click
    the next recommended song → repeat; verified ids listed in the answer.
