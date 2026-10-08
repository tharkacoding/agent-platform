"""Slash modes — `/plan`, `/deep-dive`, `/red-team` ... (100 operational modes in 10 groups).

A message may start with one or more modes: `/research /fact-check is coffee bad for you?`.
Each mode carries:
  directive  how the work must be done (injected into the Main Agent AND every agent's system prompt for that run)
  agent      the agent the planner should prefer (a hint, not a hard route)
  effort     effort tier hint 0-4 (only used when the user did not pick an effort in the chat box)
  kind       task (normal plan → agents) | chat (answer directly in that style) | ask (question cards first) |
             schedule (create a scheduled job)
Modes apply to THIS message only (per-query, like every other instruction) unless the user says "always".
`/modes` lists them; `/modes <group or word>` filters.
"""
from __future__ import annotations

import difflib
import re

# (name, short description, directive, preferred agent, effort, kind)
_G = {
    "📋 Planning & Strategy": [
        ("Plan", "Generates step-by-step roadmaps",
         "Produce a numbered step-by-step roadmap: each step has an owner/tool, a concrete output and a done-check. "
         "Plan first; only execute if the user asked for execution too.", "planning_agent", 2, "task"),
        ("Deconstruct", "Breaks complex goals into bite-sized tasks",
         "Break the goal into a tree of small tasks (each doable in under an hour), with dependencies marked.",
         "planning_agent", 2, "task"),
        ("Prioritize", "Ranks actions by impact and urgency",
         "Rank every candidate action by impact x urgency / effort; output a ranked table with the reason for each "
         "rank and a clear 'do first' list.", "planning_agent", 1, "task"),
        ("Risk Assessment", "Identifies potential bottlenecks and failures",
         "List the risks/bottlenecks/failure modes with likelihood, impact, early warning sign and mitigation for "
         "each, highest first.", "planning_agent", 2, "task"),
        ("Contingency", "Creates backup plans for unexpected errors",
         "For each critical step give a Plan B: the trigger that activates it, the fallback action and how to "
         "recover state.", "planning_agent", 2, "task"),
        ("Milestone", "Establishes checkpoints to track progress",
         "Define milestones with measurable exit criteria and a date/order, so progress can be checked objectively.",
         "planning_agent", 1, "task"),
        ("Resource Allocation", "Estimates required time, tokens, or tools",
         "Estimate time, money, tokens, people and tools per step and in total; flag the scarcest resource.",
         "planning_agent", 1, "task"),
        ("Timeline", "Schedules execution across strict deadlines",
         "Lay the work on a dated timeline that respects the deadlines and dependencies; show slack and the "
         "critical path.", "planning_agent", 2, "task"),
        ("Goal Alignment", "Ensures actions match user intent",
         "Restate the user's real goal, then check every proposed action against it; drop or fix anything that "
         "does not serve it.", "", 1, "task"),
        ("Feasibility", "Checks if a requested task is realistic",
         "Judge whether the request is realistic with the available time/tools/data; give a verdict "
         "(feasible / feasible-with-changes / not feasible), the blockers and the cheapest path that works.",
         "", 2, "task"),
    ],
    "🔍 Research & Retrieval": [
        ("Browse", "Searches the live web for fresh data",
         "Use live web search and read the actual pages; prefer sources from the last 12 months and give the date "
         "and link for every fact.", "web_research_agent", 2, "task"),
        ("Scrape", "Extracts structured information from specific URLs",
         "Open the given URLs and extract the requested fields into a clean table/JSON; note fields that were "
         "missing. Do not invent values.", "browser_agent", 2, "task"),
        ("Deep Dive", "Conducts exhaustive, multi-layered topic analysis",
         "Go deep: cover background, mechanisms, evidence, competing views, edge cases and open questions, using "
         "many sources; end with a structured synthesis.", "research_agent", 3, "task"),   # deep, not critical (that tier = money/security/prod)
        ("Fact-Check", "Verifies claims against trusted sources",
         "Split the text into individual claims and verify each against trusted sources; mark each TRUE / FALSE / "
         "MISLEADING / UNVERIFIED with a source link.", "web_research_agent", 3, "task"),
        ("Synthesize", "Merges data from multiple documents into one summary",
         "Merge all provided sources into ONE coherent synthesis: agreements, disagreements and gaps; no "
         "source-by-source listing.", "research_agent", 2, "task"),
        ("Bibliography", "Compiles and formats source citations",
         "Compile the sources into a consistently formatted bibliography (APA unless the user names a style) with "
         "working links.", "document_research_agent", 1, "task"),
        ("Trend Analysis", "Tracks patterns in data over time",
         "Show how the topic/metric changed over time: direction, rate, inflection points and likely drivers, "
         "with dates.", "data_analysis_agent", 2, "task"),
        ("Competitive Intelligence", "Gathers market data on rivals",
         "Profile the competitors: offering, pricing, positioning, strengths, weaknesses and recent moves; finish "
         "with a comparison table.", "research_agent", 3, "task"),
        ("Keyword Harvest", "Pulls high-value search and SEO terms",
         "Collect high-value keywords grouped by intent (informational/commercial/transactional) with rough "
         "difficulty and suggested use.", "web_research_agent", 1, "task"),
        ("Archive", "Stores and catalogs retrieved data for future use",
         "Save the gathered facts to memory with clear titles and tags so they can be recalled later; report what "
         "was stored.", "memory_agent", 1, "task"),
    ],
    "✍️ Content & Writing": [
        ("Draft", "Generates raw, unedited text quickly",
         "Write a fast first draft: complete, but no polishing passes.", "writing_agent", 1, "task"),
        ("Copywrite", "Writes persuasive marketing and sales text",
         "Write persuasive copy: one clear promise, benefits over features, proof, and a strong call to action; "
         "give 2-3 headline variants.", "writing_agent", 2, "task"),
        ("Technical Write", "Creates clear manuals and documentation",
         "Write clear technical documentation: purpose, prerequisites, numbered steps, examples, troubleshooting.",
         "documentation_agent", 2, "task"),
        ("Paraphrase", "Rewrites existing text to change style or tone",
         "Rewrite the given text in the requested style/tone while keeping every fact and meaning.",
         "writing_agent", 1, "chat"),
        ("Ghostwrite", "Adopts a specific person's voice and style",
         "Write in the voice the user describes or shows (word choice, rhythm, humour); never mention that it is "
         "ghostwritten.", "writing_agent", 2, "task"),
        ("Localize", "Translates and adapts content for cultural context",
         "Translate AND adapt: idioms, units, currency, dates, cultural references and tone for the target "
         "audience.", "writing_agent", 2, "task"),
        ("Summarize", "Condenses long articles into key takeaways",
         "Condense to the key takeaways: a one-line TL;DR, then 3-7 bullets, then anything actionable.",
         "summarization_agent", 1, "task"),
        ("Script", "Formats dialogue for videos, podcasts, or plays",
         "Write a production-ready script with scene/segment headings, speaker labels, timing cues and stage or "
         "B-roll directions.", "writing_agent", 2, "task"),
        ("Micro-Copy", "Crafts short UI text, buttons, and alerts",
         "Write short UI text (buttons, labels, empty states, errors): clear, specific, friendly, under 8 words "
         "where possible; give alternatives.", "writing_agent", 1, "chat"),
        ("SEO Optimize", "Integrates target keywords naturally into text",
         "Optimize for search: keyword in title/H1/first paragraph, natural density, meta title + description, "
         "headings and internal-link ideas.", "writing_agent", 2, "task"),
    ],
    "💻 Coding & Engineering": [
        ("Code Generation", "Writes clean code from natural language prompts",
         "Write working, clean code as real files; run it or its tests before saying it works.", "coding_agent", 3,
         "task"),
        ("Debug", "Finds, isolates, and fixes runtime errors",
         "Reproduce the error first, find the root cause (not the symptom), fix it, and prove the fix by "
         "re-running.", "debugging_agent", 3, "task"),
        ("Refactor", "Optimizes existing code for speed and readability",
         "Refactor without changing behaviour: smaller functions, clearer names, less duplication; tests must pass "
         "before and after.", "coding_agent", 3, "task"),
        ("Review", "Critiques code architecture and style consistency",
         "Review like a senior engineer: correctness bugs first, then design, then style; each finding with "
         "file:line and a concrete fix.", "code_review_agent", 3, "task"),
        ("Translate Code", "Converts source code to a different programming language",
         "Port the code to the target language idiomatically (not line-by-line), keep behaviour identical, and "
         "test both.", "coding_agent", 3, "task"),
        ("Test Case", "Generates unit tests to verify functionality",
         "Write unit tests covering normal cases, edge cases and failure paths; run them and report the results.",
         "testing_agent", 2, "task"),
        ("API Integration", "Maps connections between different software systems",
         "Map the integration: endpoints, auth, data mapping between systems, error/retry handling; then implement "
         "and test a working call.", "coding_agent", 3, "task"),
        ("Schema Design", "Structures databases and JSON formats",
         "Design the schema: entities, fields with types, keys, relations, indexes and an example record; explain "
         "the trade-offs.", "database_agent", 2, "task"),
        ("Documentation", "Writes docstrings and inline code comments",
         "Add docstrings and comments that explain WHY (not what), plus a short README section for usage.",
         "documentation_agent", 2, "task"),
        ("Deploy", "Packages and prepares software for production",
         "Prepare for production: build, config/env, packaging (Docker if suitable), health check and a rollback "
         "plan. Never push or publish without approval.", "coding_agent", 4, "task"),
    ],
    "📊 Data Analysis": [
        ("Data Clean", "Removes duplicates and fixes formatting errors",
         "Clean the data: duplicates, types, formats, missing values, outliers; report every change with counts.",
         "data_analysis_agent", 2, "task"),
        ("Statistical", "Calculates averages, variance, and distributions",
         "Compute descriptive statistics (count, mean, median, std, quantiles, distribution shape) and state what "
         "they mean.", "data_analysis_agent", 2, "task"),
        ("Anomaly Detection", "Flags unusual spikes or outliers in datasets",
         "Find anomalies/outliers with a stated method and threshold; list each with its value, expected range "
         "and likely cause.", "data_analysis_agent", 2, "task"),
        ("Sentiment Analysis", "Scores text feedback as positive, negative, or neutral",
         "Score each text item positive/negative/neutral with confidence, then summarise the distribution and the "
         "main themes behind it.", "data_analysis_agent", 2, "task"),
        ("Predictive", "Forecasts future metrics using historical trends",
         "Forecast from the history with a stated method, give a range (not a single number) and the assumptions.",
         "data_analysis_agent", 3, "task"),
        ("Pivot", "Cross-tabulates data to reveal hidden correlations",
         "Cross-tabulate the data along the meaningful dimensions and point out the correlations it reveals.",
         "data_analysis_agent", 2, "task"),
        ("Audit", "Reviews financial logs or usage metrics for compliance",
         "Audit the records against the rules: list every violation or irregularity with evidence and severity.",
         "data_analysis_agent", 3, "task"),
        ("Categorize", "Tags and groups messy unorganized data",
         "Group the items into clear, non-overlapping categories with a tag per item and counts per category.",
         "data_analysis_agent", 1, "task"),
        ("Extract Entity", "Pulls specific names, dates, and locations from text",
         "Extract the entities (people, organisations, dates, places, amounts...) into a table with the source "
         "snippet.", "data_analysis_agent", 1, "task"),
        ("Benchmark", "Compares performance metrics against industry standards",
         "Compare the metrics against standards/peers with sources; show the gap and what would close it.",
         "research_agent", 2, "task"),
    ],
    "🗣️ Interaction & Communication": [
        ("Interview", "Asks targeted questions to gather user requirements",
         "Gather requirements first through question cards (goal, constraints, audience, success criteria), then "
         "do the work with the answers.", "", 2, "ask"),
        ("Consult", "Provides expert guidance and strategic recommendations",
         "Act as an expert consultant: diagnose, give 2-3 options with trade-offs and a clear recommendation.",
         "", 2, "chat"),
        ("Support", "Resolves customer issues with empathy and patience",
         "Acknowledge the problem with empathy, then give clear numbered steps to resolve it and what to do if it "
         "persists.", "", 1, "chat"),
        ("Negotiate", "Finds middle ground in multi-party conflicts",
         "Identify each party's interests (not positions), the zone of agreement, and propose fair options and a "
         "script.", "", 2, "chat"),
        ("Debate", "Argues counterpoints to stress-test an idea",
         "Argue the strongest case AGAINST the user's idea (steel-man the opposition), then say which points they "
         "must answer.", "", 2, "chat"),
        ("Socratic", "Guides users to answers using strategic questioning",
         "Teach by guiding questions one step at a time; give hints, not the full answer, unless the user asks.",
         "teacher_agent", 1, "chat"),
        ("Coaching", "Offers encouraging feedback and skill-building tips",
         "Coach: what is going well, the one most important thing to improve, and a concrete practice exercise.",
         "teacher_agent", 1, "chat"),
        ("Mediation", "Neutralizes heated or emotional language",
         "Rewrite/respond in calm, neutral, non-blaming language that keeps every legitimate point.", "", 1,
         "chat"),
        ("Onboarding", "Walks new users through setups and features",
         "Walk the user through setup step by step, from zero, checking each step works before the next.", "", 1,
         "chat"),
        ("Concierge", "Handles bookings, scheduling, and personal logistics",
         "Handle the logistics end to end (options, times, prices, links); ask before any booking or payment.",
         "browser_agent", 2, "task"),
    ],
    "⚙️ Task Execution & Automation": [
        ("Batch Process", "Runs repetitive tasks on hundreds of files at once",
         "Process every item the same way, in parallel where possible; report per-item success/failure counts.",
         "terminal_agent", 2, "task"),
        ("Workflow Automation", "Chains multiple tool actions together seamlessly",
         "Chain the tools/agents into one automated workflow; verify each hand-off and the end result.", "", 3,
         "task"),
        ("File Management", "Organizes, renames, and moves digital assets",
         "Organize/rename/move the files in the workspace with a dry-run list first; never delete without "
         "approval.", "terminal_agent", 2, "task"),
        ("Sync", "Keeps data identical across different platforms",
         "Compare both sides, list the differences, and sync them (newest wins unless told otherwise); report "
         "conflicts.", "terminal_agent", 2, "task"),
        ("Form Filler", "Inputs structured data into digital documents automatically",
         "Fill the form/document from the given data field by field; show what went where; ask before "
         "submitting personal data.", "browser_agent", 2, "task"),
        ("Schedule", "Triggers actions based on specific times or dates",
         "Create a scheduled job for this at the requested time/recurrence.", "scheduler_agent", 1, "schedule"),
        ("Monitor", "Watches active systems and alerts on changes",
         "Set up a recurring check of the target and report only when something changed (what, when, how much).",
         "scheduler_agent", 1, "schedule"),
        ("Scrub", "Anonymizes personally identifiable information (PII)",
         "Replace all PII (names, emails, phones, addresses, IDs, card numbers) with consistent placeholders; list "
         "the categories removed.", "", 1, "task"),
        ("Backup", "Mirrors critical files to prevent data loss",
         "Copy the important files to a dated backup folder in the workspace and verify the copies (count + "
         "size/hash).", "terminal_agent", 2, "task"),
        ("Format Convert", "Changes files from one extension to another",
         "Convert the file(s) to the target format with a script, then validate the output loads correctly.",
         "terminal_agent", 1, "task"),
    ],
    "🧠 Learning & Optimization": [
        ("Feedback Loop", "Adjusts behavior based on user corrections",
         "Apply the user's correction now AND save it as a lesson so future runs follow it.", "memory_agent", 1,
         "task"),
        ("Few-Shot", "Studies provided examples before starting a task",
         "First study the user's examples and state the pattern they follow, then produce output that matches it.",
         "", 1, "task"),
        ("Fine-Tune Preparation", "Curates clean QA pairs for training data",
         "Produce clean, diverse, deduplicated instruction/answer pairs in JSONL, with a quality check on each.",
         "data_analysis_agent", 2, "task"),
        ("Prompt Engineer", "Tests and refines instructions for better output",
         "Write the prompt, test it on sample inputs, find failure cases and iterate; deliver the final prompt "
         "plus why it works.", "", 2, "task"),
        ("Exploration", "Tries diverse strategies to discover new solutions",
         "Try several genuinely different approaches, compare their results, and keep the best.", "", 3, "task"),
        ("Exploitation", "Sticks strictly to the highest-performing known strategy",
         "Use only the approach that is known to work (check experience notes/memory first); no experiments.",
         "", 1, "task"),
        ("Self-Correction", "Reviews its own output and fixes mistakes before responding",
         "Before answering, review your own output against the request, find mistakes and fix them; say what "
         "you corrected.", "", 3, "task"),
        ("Memory Consolidate", "Saves core facts while discarding irrelevant chat history",
         "Extract the durable facts/preferences from this conversation and save them to memory; skip chit-chat.",
         "memory_agent", 1, "task"),
        ("Curate Knowledge", "Builds custom internal wikis from verified facts",
         "Build a structured wiki page (sections, definitions, links between topics) from verified facts only.",
         "document_research_agent", 2, "task"),
        ("Hyperparameter", "Simulates performance under varying temperature configurations",
         "Compare outputs/settings across a grid of parameters and recommend the best setting with evidence.",
         "data_analysis_agent", 2, "task"),
    ],
    "🛡️ Quality & Security": [
        ("Proofread", "Catches typos, grammar errors, and punctuation slips",
         "Fix typos, grammar and punctuation only; keep the author's wording and list the changes.",
         "writing_agent", 1, "chat"),
        ("Red Team", "Attacks its own system to find security vulnerabilities",
         "Think like an attacker against the user's OWN system: list attack paths, abuse cases and weaknesses with "
         "severity and fixes. Defensive analysis only.", "code_review_agent", 3, "task"),
        ("Compliance", "Checks outputs against legal and policy rules",
         "Check the content against the named rules/policies; list each issue with the rule it breaks and a fix.",
         "", 3, "task"),
        ("Bias Detection", "Scans text for unfair, skewed, or discriminatory angles",
         "Find biased, one-sided or exclusionary language/framing; explain each and suggest neutral wording.", "",
         2, "task"),
        ("Plagiarism Check", "Verifies text uniqueness against external sources",
         "Search distinctive sentences on the web and report matching sources with the overlapping text.",
         "web_research_agent", 2, "task"),
        ("Sanitize Input", "Blocks malicious code injections from raw prompts",
         "Inspect the input for prompt injection, script/SQL injection and hidden instructions; report and "
         "neutralise them.", "code_review_agent", 2, "task"),
        ("Access Control", "Restricts data access based on user permissions",
         "Design/check who can access what: roles, permissions, least privilege; list gaps.", "code_review_agent",
         3, "task"),
        ("Consistency Check", "Ensures the end of a document agrees with the beginning",
         "Check the document for contradictions, changed numbers/names/terms and unkept promises; list each with "
         "locations.", "writing_agent", 2, "task"),
        ("Vulnerability Scan", "Reviews open-source packages for known threats",
         "Check the project's dependencies for known vulnerabilities and outdated packages; give upgrade steps.",
         "code_review_agent", 3, "task"),
        ("Verify Link", "Tests hyperlinks to ensure none are broken or dead",
         "Fetch every link and report status (ok / redirect / broken) with the final URL.", "web_research_agent",
         1, "task"),
    ],
    "🎨 Creative & Brainstorming": [
        ("Ideation", "Generates a high volume of unique concepts",
         "Generate many distinct ideas (20+ unless told otherwise), grouped by theme, then star the top 3 and why.",
         "", 2, "chat"),
        ("Lateral Think", "Connects unrelated industries for novel solutions",
         "Borrow solutions from unrelated fields/industries and adapt them to the problem; explain each analogy.",
         "", 2, "chat"),
        ("Visual Prompt", "Crafts highly detailed descriptions for image generators",
         "Write detailed image-generation prompts: subject, composition, lens/camera, lighting, style, palette, "
         "mood, aspect ratio, negative prompt.", "", 1, "chat"),
        ("Worldbuild", "Invents deep lore, rules, and settings for stories",
         "Invent a coherent world: history, geography, factions, rules/magic/tech with costs, conflicts and story "
         "hooks.", "writing_agent", 2, "task"),
        ("Analogize", "Explains complex technical topics using simple metaphors",
         "Explain with simple everyday metaphors, then map each part of the metaphor back to the real thing.",
         "teacher_agent", 1, "chat"),
        ("Moodboard", "Curates themes, styles, and aesthetic directions",
         "Define 2-3 aesthetic directions: palette (hex), typography, textures, references and keywords for each.",
         "", 1, "chat"),
        ("Name Harvest", "Brainstorms catchy titles, brands, and product names",
         "Brainstorm many names in different styles (descriptive, invented, metaphor, compound); shortlist the "
         "best with reasons.", "", 1, "chat"),
        ("Improvise", "Operates with minimal constraints to explore abstract paths",
         "Take creative liberties and explore unexpected directions; label what is speculative.", "", 2, "chat"),
        ("Mashup", "Combines two contrasting styles into a hybrid concept",
         "Fuse the two named styles/concepts into one hybrid and describe it concretely.", "", 1, "chat"),
        ("Gamify", "Turns boring, dry tasks into engaging point-based systems",
         "Design a game layer: points, levels, streaks, rewards, challenges and a progress view for the task.",
         "", 2, "chat"),
    ],
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


MODES: dict[str, dict] = {}
for _group, _items in _G.items():
    for _name, _desc, _dir, _agent, _eff, _kind in _items:
        MODES[_slug(_name)] = {"id": _slug(_name), "name": f"{_name} Mode", "group": _group, "description": _desc,
                               "directive": _dir, "agent": _agent, "effort": _eff, "kind": _kind}

# a few short aliases people type
ALIASES = {"research": "browse", "web": "browse", "deep": "deep-dive", "factcheck": "fact-check", "code":
           "code-generation", "tests": "test-case", "test": "test-case", "redteam": "red-team", "ideas": "ideation",
           "brainstorm": "ideation", "eli5": "analogize", "summary": "summarize", "tldr": "summarize", "risks":
           "risk-assessment", "interview-me": "interview", "translate": "localize", "prompt": "prompt-engineer"}

RESERVED = {"soul", "souls", "modes", "mode"}   # handled elsewhere


def resolve(token: str) -> str | None:
    t = _slug(token)
    if t in MODES:
        return t
    if t in ALIASES:
        return ALIASES[t]
    t2 = t.replace("-mode", "")
    return t2 if t2 in MODES else None


def parse(message: str) -> tuple[list[dict], str, list[str]]:
    """Leading `/mode` tokens → (modes, remaining text, unknown tokens). Stops at the first non-slash word."""
    modes, unknown = [], []
    rest = message.lstrip()
    while True:
        m = re.match(r"/([A-Za-z][\w-]*)\s*", rest)
        if not m or m.group(1).lower() in RESERVED:
            break
        mid = resolve(m.group(1))
        if mid:
            if MODES[mid] not in modes:
                modes.append(MODES[mid])
        else:
            unknown.append(m.group(1))
        rest = rest[m.end():]
    return modes, rest.strip(), unknown


def suggest(token: str, n: int = 3) -> list[str]:
    return difflib.get_close_matches(_slug(token), list(MODES) + list(ALIASES), n=n, cutoff=0.5)


def directive_block(modes: list[dict]) -> str:
    if not modes:
        return ""
    return ("ACTIVE MODES chosen by the user for this request (follow them exactly):\n"
            + "\n".join(f"- {m['name']}: {m['directive']}" for m in modes))


def listing(filter_text: str = "") -> str:
    f = filter_text.strip().lower()
    out = []
    for group, items in _G.items():
        rows = [MODES[_slug(i[0])] for i in items]
        if f:
            rows = [r for r in rows if f in group.lower() or f in r["id"] or f in r["description"].lower()]
        if rows:
            out.append(f"**{group}**\n" + "\n".join(f"- `/{r['id']}` — {r['description']}" for r in rows))
    if not out:
        return f"No modes match '{filter_text}'. Type `/modes` to see all {len(MODES)}."
    return (f"**{len(MODES)} modes** — start a message with one or more, e.g. `/deep-dive /fact-check your topic`.\n\n"
            + "\n\n".join(out))


def catalog() -> list[dict]:
    return list(MODES.values())
