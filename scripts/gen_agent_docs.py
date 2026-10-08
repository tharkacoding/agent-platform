"""Generate docs/AGENTS.md — every agent AgentOS uses and the LangGraph code behind it (TODO P1).

Read from the REAL code (agent catalog + the graph files), never typed by hand:
    python scripts/gen_agent_docs.py          # rewrites docs/AGENTS.md
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AAP_LLM_PROVIDER", "mock")
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_docs_"))

from aap.agents.prebuilt.catalog import PREBUILT  # noqa: E402

GRAPHS = ROOT / "aap" / "graphs"


def scan_graphs() -> dict[str, dict]:
    """archetype key → {file, description, nodes[], mermaid}: the graphs are COMPILED from the real code and read back
    from LangGraph (nodes, edges), not regex-scanned."""
    import aap.graphs  # noqa: F401  registers every @archetype
    from aap.core.sdk import ARCHETYPES
    files: dict[str, str] = {}
    for f in sorted(GRAPHS.glob("*.py")):
        for m in re.finditer(r'@archetype\(\s*"([\w-]+)"', f.read_text(encoding="utf-8")):
            files[m.group(1)] = f"aap/graphs/{f.name}"
    sample = {}
    for a in PREBUILT:
        sample.setdefault(a.graph, a)
    out: dict[str, dict] = {}
    for key, arch in sorted(ARCHETYPES.items()):
        info = {"file": files.get(key, "aap/graphs/"), "description": arch.description, "nodes": [], "mermaid": ""}
        try:
            g = arch.build(sample[key]).compile().get_graph() if key in sample else None
            if g is not None:
                info["nodes"] = [n for n in g.nodes if not n.startswith("__")]
                info["mermaid"] = g.draw_mermaid()
        except Exception as e:  # noqa: BLE001 - a graph that can't compile standalone is still listed
            info["description"] += f" (diagram unavailable: {type(e).__name__})"
        out[key] = info
    return out


def main() -> None:
    graphs = scan_graphs()
    by_graph: dict[str, list] = defaultdict(list)
    for a in PREBUILT:
        by_graph[a.graph].append(a)
    cats: dict[str, list] = defaultdict(list)
    for a in PREBUILT:
        cats[a.category].append(a)
    L = ["# AgentOS — all agents and their LangGraph code",
         "",
         "> **Generated** by `python scripts/gen_agent_docs.py` from the real agent catalog and graph files — don't edit by hand.",
         "> Debug any of them step by step in **LangSmith**: Settings → Tracing (switch + API key), then open",
         "> smith.langchain.com → project `agentos` — every run is named by its agent and shows each graph node, model",
         "> call (prompt, answer, tokens, latency) and tool step. See `aap/tracing.py`. Improvement ideas: TODO.txt §0.",
         "",
         f"**{len(PREBUILT)} agents** run on **{len(graphs)} LangGraph archetypes**. An archetype is a graph (nodes + edges); "
         "an agent is an archetype + prompt + tools + permissions (`aap/agents/prebuilt/catalog.py`).",
         "",
         "## How a request flows",
         "`Main Agent` (`aap/graphs/main.py`): load_context → classify_intent → plan_dag → run_step ⇉ (parallel agents) → "
         "collect → review → synthesize → remember (post-mortem). Each sub-agent below is its own compiled LangGraph, started "
         "by `aap/core/runtime.py::run_agent` (one traced run per agent step). During runs the Main Agent supervises "
         "(`aap/core/supervisor.py`).",
         "",
         "## Graph archetypes (the LangGraph code)",
         "| Archetype | Code | Nodes | Used by |", "|---|---|---|---|"]
    for key, g in sorted(graphs.items()):
        users = ", ".join(f"`{a.id}`" for a in by_graph.get(key, [])[:6]) + (" …" if len(by_graph.get(key, [])) > 6 else "")
        L.append(f"| `{key}` | `{g['file']}` | {', '.join(g['nodes'][:16])}{' …' if len(g['nodes']) > 16 else ''} | {users or '—'} |")
    L += ["", "## The graphs (Mermaid — paste into any viewer; GitHub/VS Code render them)", ""]
    for key, g in sorted(graphs.items()):
        if g["mermaid"]:
            L += [f"### `{key}` — {g['description'][:140]}", f"Code: `{g['file']}`", "", "```mermaid", g["mermaid"].strip(), "```", ""]
    L += ["", "## Agents by category", ""]
    for cat in sorted(cats):
        L += [f"### {cat}", "| Agent | Graph (code) | Tools | Delegates to |", "|---|---|---|---|"]
        for a in sorted(cats[cat], key=lambda x: x.id):
            g = graphs.get(a.graph, {}).get("file", f"graph `{a.graph}`")
            tools = ", ".join(t for t in a.tools if t not in ("now", "calc", "chrome_profiles", "chrome_profile_path",
                                                               "find_command", "known_commands", "fs_find",
                                                               "chrome_extension_profiles"))[:150]
            L.append(f"| **{a.name}** (`{a.id}`) — {a.description[:90]} | `{a.graph}` · `{g}` | {tools or '—'} | "
                     f"{', '.join(a.uses) or '—'} |")
        L.append("")
    L += ["## Tools every agent has", "now, calc, chrome_profiles, chrome_profile_path, find_command, known_commands, fs_find "
          "(`aap/core/sdk.py::UNIVERSAL_TOOLS`); web agents also http_request / download_file; browser agents page_text; "
          "coding-type agents skill_read (Superpowers methodology).", "",
          "## Improving an agent with LangSmith",
          "1. Run the task (or a capability case from the Tests page).",
          "2. LangSmith → project `agentos` → filter by tag `agent:<id>` or metadata `run_id` (shown in the run URL).",
          "3. Open the slow/failed node: read the exact prompt and answer, fix the prompt/graph in the file listed above.",
          "4. Re-run the case; the nightly self-test scores it (TODO P3/P4 link traces + datasets).", ""]
    (ROOT / "docs" / "AGENTS.md").write_text("\n".join(L), encoding="utf-8")
    print(f"docs/AGENTS.md: {len(PREBUILT)} agents, {len(graphs)} archetypes")


if __name__ == "__main__":
    main()
