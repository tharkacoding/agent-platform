# Agent tests & prompt tuning (TODO P2b)

Scaffold created by Claude (2026-10-10). Rows below get filled as each agent's test
runs. Running the tests executes the agents and needs the LangSmith key set
(AgentOS Settings -> Tracing), so the owner runs this step. See TODO.txt P2b.

## Procedure (from TODO P2b)
1. Write one small test per agent, each with a real-outcome verifier.
2. Run it in the VM; trace to LangSmith (run name = agent id, so each agent gets
   its own trace list).
3. Read the trace (wasted steps, tokens, wrong answers), then rewrite that agent's
   prompt: `aap/agents/prebuilt/catalog.py` system_prompt + the graph role texts.
   Prompts are also editable in LangSmith Prompt Hub (`agentos-<agent>`).
4. Re-run until the test passes cheaply, then record the row below.
5. Link the LangSmith trace for any failing case in the Tests page and in the
   post-mortem lesson (`core/postmortem.py`).

## Results
| Agent | Archetype | Test | Pass/Fail | Tokens | Seconds | Prompt version | Trace |
|-------|-----------|------|-----------|--------|---------|----------------|-------|
| _example_ | generic | smoke: returns a valid answer | - | - | - | v1 | - |

37 agents total, across 20 graph archetypes. Add one row per agent.

## Graph archetypes (reference, from docs/AGENTS.md)
`browser`, `builder`, `coding`, `competitive`, `composite`, `data`, `debug`,
`desktop`, `generic`, `improve`, `main`, `planner`, `research`, `review`,
`skillsmith`, `teacher`, `terminal`, `testing`, `toolsmith`, `writer`
