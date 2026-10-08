"""P2b: the per-agent test table covers every agent, and its verifiers judge real outcomes (pure parts tested here)."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_acase_"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import agent_cases as ac  # noqa: E402


def test_one_case_per_agent_and_all_agents_covered():
    from aap.agents.prebuilt.catalog import PREBUILT
    ids = [c["agent"] for c in ac.CASES]
    assert len(ids) == len(set(ids)), "duplicate agent in the table"
    assert set(ids) == {a.id for a in PREBUILT}, set(a.id for a in PREBUILT) ^ set(ids)
    for c in ac.CASES:
        assert c["instruction"] and callable(c["verify"]) and c["budget"] > 0, c["agent"]


def test_prose_and_code_helpers():
    assert len(ac.sentences("- It is a bottle. **Great** for hikes!\n\n```x. y.```")) == 2
    assert ac.code_block("hi\n```python\nprint(1)\n```\nbye") == "print(1)\n"
    assert ac.words("don't re-run it") == 3
    assert ac.lis_len([3, 1, 2, 5, 4, 6]) == 4 and ac.lis_len([5]) == 1
    assert ac.urls_in("see (https://a.io/x). and https://b.org/y,") == ["https://a.io/x", "https://b.org/y"]


def _t(tmp_path, final="", events=None, case=None, H=None):
    return ac.T(H or SimpleNamespace(), case or {}, "r1", {"final": final, "events": events or []}, tmp_path)


def test_verifiers_run_the_real_thing(tmp_path):
    (tmp_path / "stats.py").write_text(ac.STATS_SEED, encoding="utf-8")
    (tmp_path / "test_stats.py").write_text(ac.STATS_TEST, encoding="utf-8")
    assert ac.v_debug(_t(tmp_path))[0] is False                       # the seeded bug really fails the seeded tests
    (tmp_path / "stats.py").write_text(ac.STATS_SEED.replace("s[n // 2 + 1]", "s[n // 2 - 1]"), encoding="utf-8")
    assert ac.v_debug(_t(tmp_path))[0] is True
    (tmp_path / "test_stats.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    assert ac.v_debug(_t(tmp_path))[0] is False                       # editing the tests is not a fix

    d2 = tmp_path / "sql"
    d2.mkdir()
    (d2 / "schema.sql").write_text("CREATE TABLE users(id integer primary key, email text unique not null);\n"
                                   "CREATE TABLE orders(id integer primary key, user_id integer not null references "
                                   "users(id), total real not null);", encoding="utf-8")
    assert ac.v_sql(_t(d2))[0] is True
    (d2 / "schema.sql").write_text("CREATE TABLE users(id integer primary key, email text);\n"
                                   "CREATE TABLE orders(id integer primary key, user_id integer, total real);", encoding="utf-8")
    assert ac.v_sql(_t(d2))[0] is False                                # no constraints → rejected


def test_competitive_verifier_executes_the_answer(tmp_path):
    good = "```python\nn=int(input())\na=list(map(int,input().split()))\nb=[1]*n\nfor i in range(n):\n    for j in range(i):\n" \
           "        if a[j]<a[i]: b[i]=max(b[i],b[j]+1)\nprint(max(b))\n```"
    bad = "```python\nprint(1)\n```"
    assert ac.v_competitive(_t(tmp_path, good))[0] is True
    assert ac.v_competitive(_t(tmp_path, bad))[0] is False
    assert ac.v_competitive(_t(tmp_path, "no code"))[0] is False


def test_testing_verifier_demands_tests_that_catch_a_mutant(tmp_path):
    (tmp_path / "mathx.py").write_text(ac.MATHX_SEED, encoding="utf-8")
    weak = "from mathx import clamp, is_prime\n\n\ndef test_a():\n    assert clamp(5, 0, 10) == 5\n\n\ndef test_b():\n    assert is_prime(7)\n" \
           "\n\ndef test_c():\n    assert clamp(1, 0, 3) == 1\n\n\ndef test_d():\n    assert is_prime(11)\n"
    (tmp_path / "test_mathx.py").write_text(weak, encoding="utf-8")
    ok, ev = ac.v_testing(_t(tmp_path))
    assert ok is False and "caught=False" in ev                        # passes on the real code, misses the mutant
    strong = weak + "\n\ndef test_e():\n    assert not is_prime(0)\n    assert not is_prime(1)\n    assert clamp(99, 0, 3) == 3\n"
    (tmp_path / "test_mathx.py").write_text(strong, encoding="utf-8")
    assert ac.v_testing(_t(tmp_path))[0] is True


def test_markdown_report_and_history():
    rows = {"17.1": {"status": "pass", "run_tokens": 900, "steps": 0, "seconds_run": 6.1, "prompt": "ab12cd34"},
            "17.8": {"status": "fail", "evidence": "slug.py failed", "run_tokens": 90000, "steps": 12, "prompt": "ff00ee11"}}
    hist = ac.update_history(rows, {})
    assert hist["main_agent"][0]["tokens"] == 900 and hist["coding_agent"][0]["status"] == "fail"
    assert ac.update_history(rows, hist) == hist                       # unchanged run → no duplicate entry
    rows["17.8"] = {"status": "pass", "run_tokens": 30000, "steps": 5, "prompt": "99aa88bb"}
    hist = ac.update_history(rows, hist)
    md = ac.render_markdown(rows, hist)
    assert "90,000 → 30,000" in md and "`main_agent`" in md and "✅" in md
