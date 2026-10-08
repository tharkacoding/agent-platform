"""Run the per-agent tests (scripts/agent_cases.py, section 17) and regenerate docs/AGENT_TESTS.md.

    python scripts/run_agent_tests.py                  # all 37 agents (heavy ones are skipped)
    python scripts/run_agent_tests.py coding_agent browser_agent
    python scripts/run_agent_tests.py --heavy          # also the 4 expensive ones (builder, research, software dev, self-improvement)
    python scripts/run_agent_tests.py --report         # only rebuild the markdown from the stored results
Env as for run_test_cases.py (AOS_API, AOS_HOME, AOS_DATA, AOS_RESULTS, AOS_DESKTOP=0 in the Linux VM).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
DOCS = HERE.parent / "docs"


def main(argv: list[str]) -> None:
    if "--heavy" in argv:
        os.environ["AOS_HEAVY"] = "1"
    names = [a for a in argv if not a.startswith("--")]
    import agent_cases as ac
    import run_test_cases as rtc
    ids = [f"17.{i}" for i, c in enumerate(ac.CASES, 1) if not names or c["agent"] in names]
    if "--report" not in argv:
        if not ids:
            raise SystemExit(f"unknown agent(s): {names}")
        rtc.main(ids)
    rows = {k: v for k, v in rtc.load().items() if k.startswith("17.")}
    hist_file = DOCS / "agent_tests_history.json"
    history = json.loads(hist_file.read_text(encoding="utf-8")) if hist_file.exists() else {}
    history = ac.update_history(rows, history)
    hist_file.write_text(json.dumps(history, indent=1), encoding="utf-8")
    (DOCS / "AGENT_TESTS.md").write_text(ac.render_markdown(rows, history), encoding="utf-8")
    ok = sum(1 for r in rows.values() if r["status"] == "pass")
    print(f"docs/AGENT_TESTS.md written: {ok}/{len(rows)} pass")


if __name__ == "__main__":
    main(sys.argv[1:])
