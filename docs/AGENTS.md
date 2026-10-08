# AgentOS — all agents and their LangGraph code

> **Generated** by `python scripts/gen_agent_docs.py` from the real agent catalog and graph files — don't edit by hand.
> Debug any of them step by step in **LangSmith**: Settings → Tracing (switch + API key), then open
> smith.langchain.com → project `agentos` — every run is named by its agent and shows each graph node, model
> call (prompt, answer, tokens, latency) and tool step. See `aap/tracing.py`. Improvement ideas: TODO.txt §0.

**37 agents** run on **20 LangGraph archetypes**. An archetype is a graph (nodes + edges); an agent is an archetype + prompt + tools + permissions (`aap/agents/prebuilt/catalog.py`).

## How a request flows
`Main Agent` (`aap/graphs/main.py`): load_context → classify_intent → plan_dag → run_step ⇉ (parallel agents) → collect → review → synthesize → remember (post-mortem). Each sub-agent below is its own compiled LangGraph, started by `aap/core/runtime.py::run_agent` (one traced run per agent step). During runs the Main Agent supervises (`aap/core/supervisor.py`).

## Graph archetypes (the LangGraph code)
| Archetype | Code | Nodes | Used by |
|---|---|---|---|
| `browser` | `aap/graphs/browser.py` | start, observe, decide, gate, act, verify, finalize | `browser_agent` |
| `builder` | `aap/graphs/builder.py` | understand_spec, inspect_registry, select_components, design, generate_prompts, generate_code, generate_tests, static_validation, run_tests, debug, security_review, approval_gate, register, smoke_run, report | `agent_builder` |
| `coding` | `aap/graphs/coding.py` | intake, repo_map, explore, plan, reproduce, implement, verify, diagnose, self_review, finalize | `coding_agent`, `python_agent`, `javascript_agent`, `react_agent`, `android_agent`, `database_agent` … |
| `competitive` | `aap/graphs/competitive.py` | parse_problem, pick_algorithm, solve, brute, stress_test, complexity, finalize | `competitive_programming_agent` |
| `composite` | `aap/graphs/composite.py` | plan, delegate, merge, review, finalize | `research_agent`, `software_dev_agent` |
| `data` | `aap/graphs/data.py` | ingest, profile, plan_analyses, compute, interpret, finalize | `data_analysis_agent` |
| `debug` | `aap/graphs/debug.py` | reproduce, hypothesize, investigate, fix, confirm, finalize | `debugging_agent` |
| `desktop` | `aap/graphs/browser.py` | start, observe, decide, gate, act, verify, finalize | `desktop_agent` |
| `generic` | `aap/graphs/generic.py` | prepare, plan, act, reflect, logic, synthesize, finalize | `registry_manager`, `memory_agent`, `scheduler_agent`, `github_agent`, `cleanup_agent` |
| `improve` | `aap/graphs/improve.py` | survey, compare, propose, suggest_build, finalize | `self_improvement_agent` |
| `main` | `aap/graphs/main.py` | load_context, classify_intent, respond, schedule_task, manage_agents, analyze_capabilities, ask_user, discover_agents, plan_dag, refine_plan, build_agent, risk_gate, dispatch, run_step, collect, review … | `main_agent` |
| `planner` | `aap/graphs/planner.py` | goal, constraints, decompose, schedule, risks, validate, finalize | `planning_agent` |
| `research` | `aap/graphs/research.py` | decompose, gather, extract, synthesize, critique, finalize | `web_research_agent`, `document_research_agent` |
| `review` | `aap/graphs/review.py` | collect, static_checks, review_dimension, rank, finalize | `code_review_agent` |
| `skillsmith` | `aap/graphs/toolsmith.py` | draft, check, save, finalize | `skill_writer_agent` |
| `teacher` | `aap/graphs/teacher.py` | assess_level, explain, examples, quiz, check_quiz, feedback | `teacher_agent`, `curriculum_agent`, `question_generator_agent` |
| `terminal` | `aap/graphs/terminal.py` | intake, operate, verify, finalize | `terminal_agent` |
| `testing` | `aap/graphs/testing.py` | discover, design_tests, write_tests, run, analyze, finalize | `testing_agent` |
| `toolsmith` | `aap/graphs/toolsmith.py` | design, write_code, test, create, finalize | `tool_builder_agent` |
| `writer` | `aap/graphs/writer.py` | outline, draft, critique, finalize | `documentation_agent`, `summarization_agent`, `writing_agent`, `writer_agent` |

## The graphs (Mermaid — paste into any viewer; GitHub/VS Code render them)

### `browser` — Web operator: open → observe (DOM snapshot) → decide → sensitive-action gate → act loop
Code: `aap/graphs/browser.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	start(start)
	observe(observe)
	decide(decide)
	gate(gate)
	act(act)
	verify(verify)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> start;
	act -.-> observe;
	act -.-> verify;
	decide -.-> finalize;
	decide -.-> gate;
	decide -.-> verify;
	gate -.-> act;
	gate -.-> finalize;
	observe --> decide;
	start -.-> finalize;
	start -.-> observe;
	verify -.-> finalize;
	verify -.-> observe;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `builder` — Agent Builder: spec → design → code → tests → validate/debug → approve → register
Code: `aap/graphs/builder.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	understand_spec(understand_spec)
	inspect_registry(inspect_registry)
	select_components(select_components)
	design(design)
	generate_prompts(generate_prompts)
	generate_code(generate_code)
	generate_tests(generate_tests)
	static_validation(static_validation)
	run_tests(run_tests)
	debug(debug)
	security_review(security_review)
	approval_gate(approval_gate)
	register(register)
	smoke_run(smoke_run)
	report(report)
	__end__([<p>__end__</p>]):::last
	__start__ --> understand_spec;
	approval_gate -.-> register;
	debug --> static_validation;
	design --> generate_prompts;
	generate_code --> generate_tests;
	generate_prompts --> generate_code;
	generate_tests --> static_validation;
	inspect_registry --> select_components;
	register --> smoke_run;
	run_tests -.-> debug;
	run_tests -.-> security_review;
	security_review --> approval_gate;
	select_components --> design;
	smoke_run --> report;
	static_validation -.-> debug;
	static_validation -.-> run_tests;
	static_validation -.-> security_review;
	understand_spec --> inspect_registry;
	report --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `coding` — Codex-style engineer: repo map → explore → plan → (reproduce) → edit loop → verify ⇄ diagnose/fix → self-review → commit
Code: `aap/graphs/coding.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	intake(intake)
	repo_map(repo_map)
	explore(explore)
	plan(plan)
	reproduce(reproduce)
	implement(implement)
	verify(verify)
	diagnose(diagnose)
	self_review(self_review)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> intake;
	diagnose --> implement;
	explore --> plan;
	implement --> verify;
	intake --> repo_map;
	plan --> reproduce;
	repo_map --> explore;
	reproduce --> implement;
	self_review -.-> finalize;
	self_review -.-> implement;
	verify -.-> diagnose;
	verify -.-> self_review;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `competitive` — Competitive programmer: parse → algorithm → solve → brute+generator → stress test ⇄ fix → complexity
Code: `aap/graphs/competitive.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	parse_problem(parse_problem)
	pick_algorithm(pick_algorithm)
	solve(solve)
	brute(brute)
	stress_test(stress_test)
	complexity(complexity)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> parse_problem;
	brute --> stress_test;
	complexity --> finalize;
	parse_problem --> pick_algorithm;
	pick_algorithm --> solve;
	solve --> brute;
	stress_test -.-> complexity;
	stress_test -.-> solve;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `composite` — Team lead over sub-agents: plan → parallel delegation → merge → review/retry
Code: `aap/graphs/composite.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	plan(plan)
	delegate(delegate)
	merge(merge)
	review(review)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> plan;
	delegate --> merge;
	merge --> review;
	plan --> delegate;
	review -.-> delegate;
	review -.-> finalize;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `data` — Data analyst: ingest → profile → plan analyses → compute (sandboxed) ⇄ repair → interpret
Code: `aap/graphs/data.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	ingest(ingest)
	profile(profile)
	plan_analyses(plan_analyses)
	compute(compute)
	interpret(interpret)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> ingest;
	compute -.-> interpret;
	ingest --> profile;
	interpret --> finalize;
	plan_analyses --> compute;
	profile --> plan_analyses;
	finalize --> __end__;
	compute -.-> compute;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `debug` — Debugger: reproduce → hypothesize → investigate loop → fix (delegates) → confirm ⇄
Code: `aap/graphs/debug.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	reproduce(reproduce)
	hypothesize(hypothesize)
	investigate(investigate)
	fix(fix)
	confirm(confirm)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> reproduce;
	confirm -.-> finalize;
	confirm -.-> hypothesize;
	fix --> confirm;
	hypothesize --> investigate;
	investigate -.-> fix;
	reproduce --> hypothesize;
	finalize --> __end__;
	investigate -.-> investigate;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `desktop` — Desktop operator via Host Bridge: observe UIA tree → decide → approve every action → act loop
Code: `aap/graphs/browser.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	start(start)
	observe(observe)
	decide(decide)
	gate(gate)
	act(act)
	verify(verify)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> start;
	act -.-> observe;
	act -.-> verify;
	decide -.-> finalize;
	decide -.-> gate;
	decide -.-> verify;
	gate -.-> act;
	gate -.-> finalize;
	observe --> decide;
	start -.-> finalize;
	start -.-> observe;
	verify -.-> finalize;
	verify -.-> observe;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `generic` — General worker: plan → tools/delegation loop → optional sandboxed logic → answer
Code: `aap/graphs/generic.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	prepare(prepare)
	plan(plan)
	act(act)
	reflect(reflect)
	logic(logic)
	synthesize(synthesize)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> prepare;
	act --> reflect;
	logic --> synthesize;
	plan -.-> act;
	plan -.-> logic;
	prepare --> plan;
	reflect -.-> logic;
	reflect -.-> plan;
	synthesize --> finalize;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `improve` — Self-improvement: survey similar products → compare → propose → approval-gated build suggestion
Code: `aap/graphs/improve.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	survey(survey)
	compare(compare)
	propose(propose)
	suggest_build(suggest_build)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> survey;
	compare --> propose;
	propose --> suggest_build;
	suggest_build --> finalize;
	survey --> compare;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `main` — Main Agent: intent → capabilities → registry discovery → DAG plan → (build missing agent) → parallel delegation → review loop → synthesis → 
Code: `aap/graphs/main.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	load_context(load_context)
	classify_intent(classify_intent)
	respond(respond)
	schedule_task(schedule_task)
	manage_agents(manage_agents)
	analyze_capabilities(analyze_capabilities)
	ask_user(ask_user)
	discover_agents(discover_agents)
	plan_dag(plan_dag)
	refine_plan(refine_plan)
	build_agent(build_agent)
	risk_gate(risk_gate)
	dispatch(dispatch)
	run_step(run_step)
	collect(collect)
	review(review)
	synthesize(synthesize)
	remember(remember)
	__end__([<p>__end__</p>]):::last
	__start__ --> load_context;
	analyze_capabilities --> ask_user;
	ask_user --> discover_agents;
	build_agent --> discover_agents;
	classify_intent -.-> analyze_capabilities;
	classify_intent -.-> manage_agents;
	classify_intent -.-> remember;
	classify_intent -.-> respond;
	classify_intent -.-> schedule_task;
	collect -.-> dispatch;
	collect -.-> review;
	discover_agents --> plan_dag;
	dispatch -.-> review;
	dispatch -.-> run_step;
	load_context --> classify_intent;
	manage_agents --> remember;
	plan_dag --> refine_plan;
	refine_plan -.-> build_agent;
	refine_plan -.-> risk_gate;
	refine_plan -.-> synthesize;
	respond --> remember;
	review -.-> dispatch;
	review -.-> synthesize;
	risk_gate --> dispatch;
	run_step --> collect;
	schedule_task --> remember;
	synthesize --> remember;
	remember --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `planner` — Planner: goal → constraints → tasks → schedule → risks → validate ⇄ repair
Code: `aap/graphs/planner.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	goal(goal)
	constraints(constraints)
	decompose(decompose)
	schedule(schedule)
	risks(risks)
	validate(validate)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> goal;
	constraints --> decompose;
	decompose --> schedule;
	goal --> constraints;
	risks --> validate;
	schedule --> risks;
	validate -.-> decompose;
	validate -.-> finalize;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `research` — Researcher: decompose → parallel search/fetch → extract claims → synthesize → critique/revise
Code: `aap/graphs/research.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	decompose(decompose)
	gather(gather)
	extract(extract)
	synthesize(synthesize)
	critique(critique)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> decompose;
	critique -.-> finalize;
	critique -.-> synthesize;
	decompose --> gather;
	extract --> synthesize;
	gather --> extract;
	synthesize --> critique;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `review` — Code reviewer: collect → static checks → per-dimension LLM review loop → rank
Code: `aap/graphs/review.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	collect(collect)
	static_checks(static_checks)
	review_dimension(review_dimension)
	rank(rank)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> collect;
	collect --> static_checks;
	rank --> finalize;
	review_dimension -.-> rank;
	static_checks -.-> rank;
	static_checks -.-> review_dimension;
	finalize --> __end__;
	review_dimension -.-> review_dimension;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `skillsmith` — Skill Writer: draft SKILL.md → check sections (fix) → save → verify it loads
Code: `aap/graphs/toolsmith.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	draft(draft)
	check(check)
	save(save)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> draft;
	check -.-> draft;
	check -.-> save;
	draft --> check;
	save --> finalize;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `teacher` — Teacher/Curriculum/Question generator: assess → explain → examples → quiz ⇄ validate → lesson
Code: `aap/graphs/teacher.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	assess_level(assess_level)
	explain(explain)
	examples(examples)
	quiz(quiz)
	check_quiz(check_quiz)
	feedback(feedback)
	__end__([<p>__end__</p>]):::last
	__start__ --> assess_level;
	assess_level -.-> explain;
	assess_level -.-> quiz;
	check_quiz -.-> feedback;
	check_quiz -.-> quiz;
	examples --> quiz;
	explain -.-> examples;
	explain -.-> quiz;
	quiz --> check_quiz;
	feedback --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `terminal` — Terminal operator: intake → ReAct shell/file loop (approval-gated) → verify → finalize
Code: `aap/graphs/terminal.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	intake(intake)
	operate(operate)
	verify(verify)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> intake;
	intake --> operate;
	operate --> verify;
	verify --> finalize;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `testing` — Tester: discover → design cases → write tests → run → analyze ⇄ repair broken tests
Code: `aap/graphs/testing.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	discover(discover)
	design_tests(design_tests)
	write_tests(write_tests)
	run(run)
	analyze(analyze)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> discover;
	analyze -.-> finalize;
	analyze -.-> write_tests;
	design_tests --> write_tests;
	discover --> design_tests;
	run --> analyze;
	write_tests --> run;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `toolsmith` — Tool Builder: design → code → validate → sandbox self-test → fix → approve → register → verify
Code: `aap/graphs/toolsmith.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	design(design)
	write_code(write_code)
	test(test)
	create(create)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> design;
	create --> finalize;
	design --> write_code;
	test -.-> create;
	test -.-> finalize;
	test -.-> write_code;
	write_code --> test;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

### `writer` — Writer: outline → draft → critique ⇄ revise
Code: `aap/graphs/writer.py`

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	outline(outline)
	draft(draft)
	critique(critique)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> outline;
	critique -.-> draft;
	critique -.-> finalize;
	draft --> critique;
	outline --> draft;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```


## Agents by category

### Automation
| Agent | Graph (code) | Tools | Delegates to |
|---|---|---|---|
| **Browser Agent** (`browser_agent`) — Operates a sandboxed Chrome (Playwright): navigates, clicks, fills forms, extracts info. C | `browser` · `aap/graphs/browser.py` | browser_open, browser_act, browser_snapshot, browser_screenshot, phone_update, page_text | — |
| **Cleanup Agent** (`cleanup_agent`) — Frees disk space safely: scans caches, old temp files, old AgentOS logs/screenshots and te | `generic` · `aap/graphs/generic.py` | disk_scan, disk_clean | — |
| **Computer Agent** (`desktop_agent`) — Uses your computer like a person: opens sites in your REAL Chrome profiles (where you're l | `desktop` · `aap/graphs/browser.py` | desktop_screenshot, desktop_tree, desktop_act, chrome_open_profile, desktop_wait_for, desktop_locate, phone_update | — |
| **Terminal Agent** (`terminal_agent`) — Runs terminal commands for you like a CLI coding assistant: inspects folders, runs scripts | `terminal` · `aap/graphs/terminal.py` | run_command, fs_list, fs_read, fs_write, fs_patch, search_code, skill_read | — |

### Development
| Agent | Graph (code) | Tools | Delegates to |
|---|---|---|---|
| **Android Agent** (`android_agent`) — Android/Kotlin specialist: Jetpack Compose, Gradle, app architecture. | `coding` · `aap/graphs/coding.py` | fs_list, fs_read, fs_write, fs_patch, search_code, run_command, git_status, git_diff, git_log, git_branch, git_commit, memory_recall, memory_remember, | debugging_agent |
| **C++ Agent** (`cpp_agent`) — C++ specialist: modern C++17/20, CMake, performance and correctness. | `coding` · `aap/graphs/coding.py` | fs_list, fs_read, fs_write, fs_patch, search_code, run_command, git_status, git_diff, git_log, git_branch, git_commit, memory_recall, memory_remember, | debugging_agent |
| **Database Agent** (`database_agent`) — SQL and database specialist: schemas, migrations, query tuning. | `coding` · `aap/graphs/coding.py` | fs_list, fs_read, fs_write, fs_patch, search_code, run_command, git_status, git_diff, git_log, git_branch, git_commit, memory_recall, memory_remember, | debugging_agent |
| **JavaScript Agent** (`javascript_agent`) — JavaScript/TypeScript and Node.js specialist. | `coding` · `aap/graphs/coding.py` | fs_list, fs_read, fs_write, fs_patch, search_code, run_command, git_status, git_diff, git_log, git_branch, git_commit, memory_recall, memory_remember, | debugging_agent |
| **Python Agent** (`python_agent`) — Python specialist: scripts, packages, FastAPI/Django, data tooling. | `coding` · `aap/graphs/coding.py` | fs_list, fs_read, fs_write, fs_patch, search_code, run_command, git_status, git_diff, git_log, git_branch, git_commit, memory_recall, memory_remember, | debugging_agent |
| **React Agent** (`react_agent`) — React + Vite front-end specialist: components, hooks, styling, state. | `coding` · `aap/graphs/coding.py` | fs_list, fs_read, fs_write, fs_patch, search_code, run_command, git_status, git_diff, git_log, git_branch, git_commit, memory_recall, memory_remember, | debugging_agent |
| **Software Dev Agent** (`software_dev_agent`) — Dev team lead: implements with the Coding Agent, then tests, reviews and documents the cha | `composite` · `aap/graphs/composite.py` | — | coding_agent, testing_agent, code_review_agent, documentation_agent |

### Education
| Agent | Graph (code) | Tools | Delegates to |
|---|---|---|---|
| **Curriculum Agent** (`curriculum_agent`) — Designs week-by-week curricula with objectives, practice and assessments. | `teacher` · `aap/graphs/teacher.py` | — | — |
| **Question Generator Agent** (`question_generator_agent`) — Generates validated multiple-choice quizzes and practice questions. | `teacher` · `aap/graphs/teacher.py` | — | — |
| **Teacher Agent** (`teacher_agent`) — Explains topics at the right level with worked examples and a quiz. | `teacher` · `aap/graphs/teacher.py` | — | — |

### Productivity
| Agent | Graph (code) | Tools | Delegates to |
|---|---|---|---|
| **Data Analysis Agent** (`data_analysis_agent`) — Profiles and analyses CSV/JSON/text data, computes statistics and explains insights. | `data` · `aap/graphs/data.py` | fs_read, data_profile, data_query, run_python | — |
| **Planning Agent** (`planning_agent`) — Turns goals into scheduled, risk-checked plans (study plans, project plans, weekly plans). | `planner` · `aap/graphs/planner.py` | memory_recall | — |
| **Prompt Writer** (`writer_agent`) — Polishes prompts and short texts (used by the Agent Builder for system prompts). | `writer` · `aap/graphs/writer.py` | — | — |
| **Writing Agent** (`writing_agent`) — Writes and edits emails, essays, posts and documents with a critique-and-revise loop. | `writer` · `aap/graphs/writer.py` | fs_read | — |

### Programming
| Agent | Graph (code) | Tools | Delegates to |
|---|---|---|---|
| **Code Review Agent** (`code_review_agent`) — Reviews code and diffs for correctness, security, performance and readability with ranked  | `review` · `aap/graphs/review.py` | fs_list, fs_read, search_code, run_command, skill_read | — |
| **Coding Agent** (`coding_agent`) — Autonomous Codex-style engineer: explores a repo, plans, edits files, runs tests, fixes an | `coding` · `aap/graphs/coding.py` | fs_list, fs_read, fs_write, fs_patch, search_code, run_command, git_status, git_diff, git_log, git_branch, git_commit, memory_recall, memory_remember, | debugging_agent |
| **Competitive Programming Agent** (`competitive_programming_agent`) — Solves algorithmic problems (Codeforces/CSES style) and stress-tests solutions against bru | `competitive` · `aap/graphs/competitive.py` | run_python | — |
| **Debugging Agent** (`debugging_agent`) — Reproduces bugs, forms and tests hypotheses, finds the root cause and gets it fixed. | `debug` · `aap/graphs/debug.py` | fs_list, fs_read, search_code, run_command, skill_read | coding_agent |
| **Documentation Agent** (`documentation_agent`) — Writes READMEs, API docs, docstrings and guides from code and context. | `writer` · `aap/graphs/writer.py` | fs_read, fs_list | — |
| **GitHub Agent** (`github_agent`) — Reads GitHub repositories, issues and READMEs, and works with local git history. | `generic` · `aap/graphs/generic.py` | web_fetch, web_search, git_status, git_log, git_diff, fs_list, fs_read, skill_read, http_request, download_file | — |
| **Testing Agent** (`testing_agent`) — Designs and writes unit/edge-case tests, runs them and reports coverage gaps. | `testing` · `aap/graphs/testing.py` | fs_list, fs_read, fs_write, search_code, run_command, skill_read | — |

### Research
| Agent | Graph (code) | Tools | Delegates to |
|---|---|---|---|
| **Document Research Agent** (`document_research_agent`) — Answers questions from local documents in your workspace or shared folders, with citations | `research` · `aap/graphs/research.py` | fs_read, fs_list, search_code, skill_read | — |
| **Research Agent** (`research_agent`) — Research team lead: combines web research, document research and summarisation. | `composite` · `aap/graphs/composite.py` | — | web_research_agent, document_research_agent, summarization_agent |
| **Summarization Agent** (`summarization_agent`) — Condenses long text, documents or reports into TL;DRs and key points. | `writer` · `aap/graphs/writer.py` | fs_read | — |
| **Web Research Agent** (`web_research_agent`) — Searches and reads the web, extracts claims and writes cited answers. | `research` · `aap/graphs/research.py` | web_search, web_fetch, http_request, download_file | — |

### System
| Agent | Graph (code) | Tools | Delegates to |
|---|---|---|---|
| **Agent Builder** (`agent_builder`) — AI software engineer that designs, codes, tests and registers new agents, reusing existing | `builder` · `aap/graphs/builder.py` | run_python, fs_read, fs_list, list_tools | tool_builder_agent, skill_writer_agent |
| **Main Agent** (`main_agent`) — Orchestrator: understands requests, plans across the agent library, delegates, reviews and | `main` · `aap/graphs/main.py` | memory_recall, memory_remember | — |
| **Memory Agent** (`memory_agent`) — Recalls and consolidates long-term memory: facts, preferences, past outcomes and project n | `generic` · `aap/graphs/generic.py` | memory_recall, memory_remember | — |
| **Registry Manager** (`registry_manager`) — Keeps the agent directory tidy: finds duplicates, describes agents, answers 'which agent c | `generic` · `aap/graphs/generic.py` | memory_recall | — |
| **Scheduler Agent** (`scheduler_agent`) — Turns recurring requests into scheduled jobs and summarises their results for the Home fee | `generic` · `aap/graphs/generic.py` | memory_recall | — |
| **Skill Writer** (`skill_writer_agent`) — Writes reusable SKILLs (SKILL.md: when to use, steps, do/don't, verification) from what wo | `skillsmith` · `aap/graphs/toolsmith.py` | create_skill, skill_read | — |
| **Tool Builder** (`tool_builder_agent`) — Creates NEW tools for the platform when agents need a capability no tool has: designs, cod | `toolsmith` · `aap/graphs/toolsmith.py` | create_tool, run_python, list_tools | — |

### System/Meta
| Agent | Graph (code) | Tools | Delegates to |
|---|---|---|---|
| **Self-Improvement Agent** (`self_improvement_agent`) — Studies similar products (OpenAI Dots, open-dots clones, OpenClaw, Hermes, Manus) and prop | `improve` · `aap/graphs/improve.py` | web_search, web_fetch, browser_open, browser_act, browser_snapshot, langsmith_runs, page_text, http_request, download_file | agent_builder |

## Tools every agent has
now, calc, chrome_profiles, chrome_profile_path, find_command, known_commands, fs_find (`aap/core/sdk.py::UNIVERSAL_TOOLS`); web agents also http_request / download_file; browser agents page_text; coding-type agents skill_read (Superpowers methodology).

## Improving an agent with LangSmith
1. Run the task (or a capability case from the Tests page).
2. LangSmith → project `agentos` → filter by tag `agent:<id>` or metadata `run_id` (shown in the run URL).
3. Open the slow/failed node: read the exact prompt and answer, fix the prompt/graph in the file listed above.
4. Re-run the case; the nightly self-test scores it (TODO P3/P4 link traces + datasets).
