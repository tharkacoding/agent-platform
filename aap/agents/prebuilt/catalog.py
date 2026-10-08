"""The prebuilt agent library + system agents. Seeded into the registry at startup (runtime.py).

To add an agent: append an `_agent(...)` entry. `graph` picks the LangGraph archetype (aap/graphs/*), `config`
tunes it, `tools` is the allow-list and `permissions` the least-privilege grants enforced on every tool call.
"""
from __future__ import annotations

from ...models import AgentSpec, FsGrant, Permissions

WS_RW = [FsGrant(path="/workspace", mode="rw"), FsGrant(path="/mounts", mode="rw"), FsGrant(path="/shared", mode="rw")]
WS_RO = [FsGrant(path="/workspace", mode="ro"), FsGrant(path="/mounts", mode="ro"), FsGrant(path="/shared", mode="ro")]

CODE_TOOLS = ["fs_list", "fs_read", "fs_write", "fs_patch", "search_code", "run_command", "git_status", "git_diff",
              "git_log", "git_branch", "git_commit", "memory_recall", "memory_remember"]
READ_TOOLS = ["fs_list", "fs_read", "search_code"]
WEB_TOOLS = ["web_search", "web_fetch"]
DOC_DOMAINS = ["docs.python.org", "developer.mozilla.org", "react.dev", "developer.android.com", "nodejs.org",
               "pypi.org", "npmjs.com", "github.com", "raw.githubusercontent.com", "stackoverflow.com",
               "duckduckgo.com", "html.duckduckgo.com"]

ENGINEER_RULES = ("Work like a careful senior engineer: read before you edit, keep changes minimal and consistent with the "
                  "codebase, run the tests/build after editing, never commit secrets or debug prints, and explain what "
                  "you changed and how you verified it.")


def _with_helpers(tools: list[str]) -> list[str]:
    """Every agent lists the universal helpers; browser agents get page_text, web agents the API/download tools."""
    from ...core.sdk import UNIVERSAL_TOOLS
    extra = list(UNIVERSAL_TOOLS)
    if any(t in tools for t in ("run_command", "fs_patch", "git_diff", "search_code")):
        extra.append("skill_read")   # coding-type agents can read the Superpowers methodology skills
    if "browser_snapshot" in tools:
        extra.append("page_text")
    if "web_fetch" in tools:
        extra += ["http_request", "download_file"]
    return tools + [t for t in extra if t not in tools]


def _agent(id: str, name: str, icon: str, category: str, description: str, capabilities: list[str], graph: str,
           tools: list[str] | None = None, uses: list[str] | None = None, perms: Permissions | None = None,
           prompt: str = "", config: dict | None = None, type: str = "prebuilt") -> AgentSpec:
    return AgentSpec(id=id, name=name, icon=icon, category=category, description=description,
                     capabilities=capabilities, graph=graph, tools=_with_helpers(tools or []), uses=uses or [],
                     permissions=perms or Permissions(fs=WS_RO), system_prompt=prompt, config=config or {},
                     type=type, created_by="platform", location=f"builtin:{id}")


def _coder(id: str, name: str, icon: str, description: str, caps: list[str], stack: str, conventions: str,
           test_cmd: str = "", lint_cmd: str = "", build_cmd: str = "", extra_uses: list[str] | None = None) -> AgentSpec:
    cfg = {"stack": stack} if stack else {}
    for k, v in (("test_cmd", test_cmd), ("lint_cmd", lint_cmd), ("build_cmd", build_cmd)):
        if v:
            cfg[k] = v
    return _agent(id, name, icon, "Development" if id != "coding_agent" else "Programming", description, caps, "coding",
                  tools=CODE_TOOLS + ["web_fetch"], uses=["debugging_agent"] + (extra_uses or []),
                  perms=Permissions(fs=WS_RW, shell=True, network=DOC_DOMAINS),
                  prompt=f"You are the {name}. {ENGINEER_RULES}\nStack conventions: {conventions}", config=cfg)


PREBUILT: list[AgentSpec] = [
    # ------------------------------------------------------------------ system
    _agent("main_agent", "Main Agent", "🧭", "System",
           "Orchestrator: understands requests, plans across the agent library, delegates, reviews and answers.",
           ["orchestration", "planning", "delegation", "review"], "main",
           tools=["memory_recall", "memory_remember"], type="system",
           prompt="You are the Main Agent of AgentOS — the manager of an AI workforce. Prefer delegating to specialist "
                  "agents; create new agents through the Agent Builder only when no existing agent fits."),
    _agent("agent_builder", "Agent Builder", "🛠️", "System",
           "AI software engineer that designs, codes, tests and registers new agents, reusing existing ones.",
           ["agent_creation", "code_generation", "testing", "validation"], "builder",
           tools=["run_python", "fs_read", "fs_list", "list_tools"], perms=Permissions(fs=WS_RO), type="system",
           uses=["tool_builder_agent", "skill_writer_agent"],
           prompt="You are the Agent Builder. You create new specialised agents with least-privilege permissions, "
                  "reuse existing agents and tools wherever possible, and never activate untested code."),
    _agent("tool_builder_agent", "Tool Builder", "🔧", "System",
           "Creates NEW tools for the platform when agents need a capability no tool has: designs, codes, self-tests "
           "in the sandbox, asks your approval, registers and verifies the tool.",
           ["tool_creation", "create_tool", "new_tool", "self_extension", "tool_coding"], "toolsmith",
           tools=["create_tool", "run_python", "list_tools"], type="system",
           prompt="You build small, safe, well-tested tools. Pure logic unless network is truly needed."),
    _agent("skill_writer_agent", "Skill Writer", "📝", "System",
           "Writes reusable SKILLs (SKILL.md: when to use, steps, do/don't, verification) from what worked.",
           ["skill_creation", "create_skill", "documentation_of_methods", "playbook"], "skillsmith",
           tools=["create_skill", "skill_read"], type="system",
           prompt="You turn experience into concise, reusable playbooks."),
    _agent("registry_manager", "Registry Manager", "🗂️", "System",
           "Keeps the agent directory tidy: finds duplicates, describes agents, answers 'which agent can…' questions.",
           ["registry", "agent_search", "catalog"], "generic", tools=["memory_recall"], type="system"),
    _agent("memory_agent", "Memory Agent", "🧠", "System",
           "Recalls and consolidates long-term memory: facts, preferences, past outcomes and project notes.",
           ["memory", "recall", "consolidation", "preferences"], "generic",
           tools=["memory_recall", "memory_remember"], type="system"),
    _agent("scheduler_agent", "Scheduler Agent", "⏰", "System",
           "Turns recurring requests into scheduled jobs and summarises their results for the Home feed.",
           ["scheduling", "cron", "reminders", "recurring_tasks"], "generic", tools=["memory_recall"], type="system"),

    # ------------------------------------------------------------------ programming
    _coder("coding_agent", "Coding Agent", "🧑‍💻",
           "Autonomous Codex-style engineer: explores a repo, plans, edits files, runs tests, fixes and commits.",
           ["coding", "python", "javascript", "cpp", "refactoring", "feature_implementation", "bug_fixing", "git"],
           "", "Detect the stack from the repository and follow its existing style and tooling."),
    _agent("code_review_agent", "Code Review Agent", "🔍", "Programming",
           "Reviews code and diffs for correctness, security, performance and readability with ranked findings.",
           ["code_review", "security_review", "static_analysis", "best_practices"], "review",
           tools=READ_TOOLS + ["run_command"], perms=Permissions(fs=WS_RO, shell=True),
           prompt="You are a meticulous code reviewer. Report only real, specific problems with file:line and a fix."),
    _agent("debugging_agent", "Debugging Agent", "🐞", "Programming",
           "Reproduces bugs, forms and tests hypotheses, finds the root cause and gets it fixed.",
           ["debugging", "root_cause_analysis", "troubleshooting", "error_analysis"], "debug",
           tools=READ_TOOLS + ["run_command"], uses=["coding_agent"], perms=Permissions(fs=WS_RO, shell=True),
           prompt="You are an expert debugger. Reproduce first, reason from evidence, and confirm the fix."),
    _agent("testing_agent", "Testing Agent", "🧪", "Programming",
           "Designs and writes unit/edge-case tests, runs them and reports coverage gaps.",
           ["testing", "unit_tests", "test_generation", "qa", "pytest", "jest"], "testing",
           tools=["fs_list", "fs_read", "fs_write", "search_code", "run_command"],
           perms=Permissions(fs=WS_RW, shell=True),
           prompt="You are a QA engineer. Write focused, deterministic tests that catch real bugs."),
    _agent("competitive_programming_agent", "Competitive Programming Agent", "🏆", "Programming",
           "Solves algorithmic problems (Codeforces/CSES style) and stress-tests solutions against brute force.",
           ["competitive_programming", "algorithms", "data_structures", "cses", "codeforces", "complexity_analysis"],
           "competitive", tools=["run_python"],
           prompt="You are a competitive programmer. Choose algorithms by constraints and prove correctness by testing."),
    _agent("documentation_agent", "Documentation Agent", "📚", "Programming",
           "Writes READMEs, API docs, docstrings and guides from code and context.",
           ["documentation", "readme", "api_docs", "technical_writing"], "writer", tools=["fs_read", "fs_list"],
           config={"mode": "write"},
           prompt="You are a technical writer. Be accurate to the code, concise, and example-driven."),
    _agent("github_agent", "GitHub Agent", "🐙", "Programming",
           "Reads GitHub repositories, issues and READMEs, and works with local git history.",
           ["github", "git", "repository_analysis", "issues"], "generic",
           tools=["web_fetch", "web_search", "git_status", "git_log", "git_diff", "fs_list", "fs_read"],
           perms=Permissions(fs=WS_RO, network=["github.com", "api.github.com", "raw.githubusercontent.com"])),

    # ------------------------------------------------------------------ development specialists (coding archetype)
    _coder("python_agent", "Python Agent", "🐍", "Python specialist: scripts, packages, FastAPI/Django, data tooling.",
           ["python", "fastapi", "django", "pandas", "packaging", "pytest"], "python",
           "PEP 8, type hints, small pure functions, pytest tests in tests/, use pathlib and f-strings, no bare except.",
           test_cmd="python -m pytest -q", lint_cmd="ruff check ."),
    _coder("javascript_agent", "JavaScript Agent", "🟨", "JavaScript/TypeScript and Node.js specialist.",
           ["javascript", "typescript", "nodejs", "npm", "express"], "javascript",
           "Modern ES modules, async/await, strict TypeScript where present, no var, prefer const; jest/vitest tests.",
           test_cmd="npm test --silent", lint_cmd="npx eslint ."),
    _coder("react_agent", "React Agent", "⚛️", "React + Vite front-end specialist: components, hooks, styling, state.",
           ["react", "frontend", "ui", "vite", "tailwind", "components", "typescript"], "react",
           "Function components + hooks, TypeScript props, colocated styles/Tailwind, accessible markup (labels, roles, "
           "keyboard), no inline business logic in JSX; verify with `npm run build`.",
           test_cmd="npm test --silent", lint_cmd="npm run lint", build_cmd="npm run build"),
    _coder("android_agent", "Android Agent", "📱", "Android/Kotlin specialist: Jetpack Compose, Gradle, app architecture.",
           ["android", "kotlin", "jetpack_compose", "gradle", "mobile"], "android",
           "Kotlin, Jetpack Compose, MVVM with ViewModel + StateFlow, coroutines, resources in res/, no blocking main thread.",
           test_cmd="./gradlew test", lint_cmd="./gradlew lint", build_cmd="./gradlew assembleDebug"),
    _coder("database_agent", "Database Agent", "🗄️", "SQL and database specialist: schemas, migrations, query tuning.",
           ["database", "sql", "sqlite", "postgres", "schema_design", "migrations", "query_optimization"], "python",
           "Normalised schemas, explicit migrations, parameterised queries only, indexes for hot paths, test with SQLite.",
           test_cmd="python -m pytest -q"),
    _coder("cpp_agent", "C++ Agent", "➕", "C++ specialist: modern C++17/20, CMake, performance and correctness.",
           ["cpp", "c++", "cmake", "performance", "algorithms", "stl"], "cpp",
           "Modern C++ (RAII, smart pointers, STL algorithms, const-correctness), -Wall -Wextra clean, CMake targets.",
           build_cmd="cmake -S . -B build && cmake --build build", test_cmd="ctest --test-dir build --output-on-failure"),

    # ------------------------------------------------------------------ research
    _agent("web_research_agent", "Web Research Agent", "🌐", "Research",
           "Searches and reads the web, extracts claims and writes cited answers.",
           ["web_research", "search", "fact_finding", "citations"], "research", tools=WEB_TOOLS,
           perms=Permissions(fs=WS_RO, network=["*"]),
           prompt="You are a careful researcher. Cite sources, separate facts from inference, note uncertainty."),
    _agent("document_research_agent", "Document Research Agent", "📄", "Research",
           "Answers questions from local documents in your workspace or shared folders, with citations.",
           ["document_analysis", "document_qa", "pdf", "notes", "knowledge_base"], "research",
           tools=["fs_read", "fs_list", "search_code"]),
    _agent("summarization_agent", "Summarization Agent", "📝", "Research",
           "Condenses long text, documents or reports into TL;DRs and key points.",
           ["summarization", "tldr", "key_points", "condensing"], "writer", tools=["fs_read"],
           config={"mode": "summarize"}),
    _agent("research_agent", "Research Agent", "🔎", "Research",
           "Research team lead: combines web research, document research and summarisation.",
           ["research", "investigation", "analysis", "report_writing"], "composite",
           uses=["web_research_agent", "document_research_agent", "summarization_agent"],
           config={"pipeline": ["web_research_agent", "document_research_agent", "summarization_agent"],
                   "parallel": ["web_research_agent", "document_research_agent"]}),

    # ------------------------------------------------------------------ productivity
    _agent("planning_agent", "Planning Agent", "🗓️", "Productivity",
           "Turns goals into scheduled, risk-checked plans (study plans, project plans, weekly plans).",
           ["planning", "scheduling", "task_breakdown", "roadmaps", "time_management"], "planner",
           tools=["memory_recall"]),
    _agent("writing_agent", "Writing Agent", "✍️", "Productivity",
           "Writes and edits emails, essays, posts and documents with a critique-and-revise loop.",
           ["writing", "editing", "emails", "essays", "copywriting"], "writer", tools=["fs_read"]),
    _agent("writer_agent", "Prompt Writer", "🖋️", "Productivity",
           "Polishes prompts and short texts (used by the Agent Builder for system prompts).",
           ["prompt_writing", "rewriting", "polishing"], "writer", config={"mode": "write"}),
    _agent("data_analysis_agent", "Data Analysis Agent", "📊", "Productivity",
           "Profiles and analyses CSV/JSON/text data, computes statistics and explains insights.",
           ["data_analysis", "statistics", "csv", "json", "progress_analysis", "visualization", "pandas"], "data",
           tools=["fs_read", "data_profile", "data_query", "run_python"],
           prompt="You are a data analyst. Quantify, rank, and turn numbers into specific recommendations."),

    # ------------------------------------------------------------------ education
    _agent("teacher_agent", "Teacher Agent", "🏫", "Education",
           "Explains topics at the right level with worked examples and a quiz.",
           ["teaching", "explanations", "tutoring", "lessons"], "teacher", config={"mode": "teach"}),
    _agent("curriculum_agent", "Curriculum Agent", "🧭", "Education",
           "Designs week-by-week curricula with objectives, practice and assessments.",
           ["curriculum_design", "syllabus", "course_outline", "learning_path"], "teacher", config={"mode": "curriculum"}),
    _agent("question_generator_agent", "Question Generator Agent", "❓", "Education",
           "Generates validated multiple-choice quizzes and practice questions.",
           ["question_generation", "quiz", "assessment", "practice_questions"], "teacher",
           config={"mode": "questions", "questions": 8}),

    # ------------------------------------------------------------------ automation
    _agent("browser_agent", "Browser Agent", "🧭", "Automation",
           "Operates a sandboxed Chrome (Playwright): navigates, clicks, fills forms, extracts info. "
           "Can use your own Chrome profile when you allow it.",
           ["browser_automation", "web_navigation", "form_filling", "scraping", "playwright"], "browser",
           tools=["browser_open", "browser_act", "browser_snapshot", "browser_screenshot", "phone_update"],
           perms=Permissions(fs=WS_RO, browser="host", network=["*"])),
    _agent("desktop_agent", "Computer Agent", "🖥️", "Automation",
           "Uses your computer like a person: opens sites in your REAL Chrome profiles (where you're logged in), reads "
           "windows (UI Automation), clicks, types, pastes, presses keys, and verifies results on screen — e.g. "
           "submitting on a site that needs your login. One approval per task; never while you're using the PC.",
           ["desktop_automation", "computer_use", "ui_automation", "windows", "clicking", "screen_screenshot",
            "screenshot", "real_chrome_profile", "logged_in_sites", "submit_on_website"], "desktop",
           tools=["desktop_screenshot", "desktop_tree", "desktop_act", "chrome_open_profile", "desktop_wait_for",
                  "desktop_locate", "phone_update"],
           perms=Permissions(fs=WS_RO, desktop=True),
           prompt="You operate the user's real computer carefully: observe, act, observe, verify on screen."),
    _agent("cleanup_agent", "Cleanup Agent", "🧹", "Automation",
           "Frees disk space safely: scans caches, old temp files, old AgentOS logs/screenshots and test browser "
           "profiles, shows what can go, and deletes only those after you confirm. Never your files or programs.",
           ["disk_cleanup", "free_space", "storage", "cache_cleanup", "delete_junk", "disk_space"], "generic",
           tools=["disk_scan", "disk_clean"],
           prompt="First run disk_scan and show a table of what can be freed. Then call disk_clean with the "
                  "categories worth cleaning (skip tiny ones). Report freed GB and free space now."),
    _agent("terminal_agent", "Terminal Agent", "⌨️", "Automation",
           "Runs terminal commands for you like a CLI coding assistant: inspects folders, runs scripts and tools, "
           "installs packages, creates small files. Commands that change things ask for approval.",
           ["terminal", "shell", "command_line", "run_commands", "scripts", "file_operations", "package_install"],
           "terminal", tools=["run_command", "fs_list", "fs_read", "fs_write", "fs_patch", "search_code"],
           perms=Permissions(fs=WS_RW, shell=True),
           prompt="You are the Terminal Agent. Be precise and safe: inspect before changing, prefer small reversible "
                  "steps, show the important output, and never run destructive commands."),

    # ------------------------------------------------------------------ composites
    _agent("software_dev_agent", "Software Dev Agent", "🏗️", "Development",
           "Dev team lead: implements with the Coding Agent, then tests, reviews and documents the change.",
           ["software_development", "full_stack", "feature_delivery", "end_to_end"], "composite",
           uses=["coding_agent", "testing_agent", "code_review_agent", "documentation_agent"],
           config={"pipeline": ["coding_agent", "testing_agent", "code_review_agent", "documentation_agent"],
                   "parallel": []}),

    # ------------------------------------------------------------------ meta
    _agent("self_improvement_agent", "Self-Improvement Agent", "🌱", "System/Meta",
           "Studies similar products (OpenAI Dots, open-dots clones, OpenClaw, Hermes, Manus) and proposes features, "
           "prompt changes and new agents. Suggests builds to the Agent Builder only with your approval.",
           ["self_improvement", "competitive_analysis", "product_research", "proposals"], "improve",
           tools=WEB_TOOLS + ["browser_open", "browser_act", "browser_snapshot", "langsmith_runs"], uses=["agent_builder"],
           perms=Permissions(fs=WS_RO, browser="sandbox",
                             network=["github.com", "raw.githubusercontent.com", "openai.com", "duckduckgo.com",
                                      "html.duckduckgo.com"])),
]

# Default soul (aap/souls) for each agent's user-facing text; the conversation's chosen soul still wins.
SOUL_MAP = {
    "teacher": ["teacher_agent", "curriculum_agent", "question_generator_agent"],
    "engineer": ["coding_agent", "python_agent", "javascript_agent", "react_agent", "android_agent", "database_agent",
                 "cpp_agent", "code_review_agent", "debugging_agent", "software_dev_agent", "github_agent"],
    "researcher": ["web_research_agent", "document_research_agent", "research_agent", "self_improvement_agent"],
    "coach": ["planning_agent"],
}

for _s in PREBUILT:
    if _s.id in ("main_agent", "agent_builder", "registry_manager", "memory_agent", "scheduler_agent",
                 "tool_builder_agent", "skill_writer_agent"):
        _s.type = "system"
    for _soul, _ids in SOUL_MAP.items():
        if _s.id in _ids:
            _s.soul = _soul
