"""2026-10-07 improvements: same-agent chains collapse into one step; a verified wait_for ends the UI loop at once."""
import os
import re
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_fewer_"))

from aap.graphs import browser as bg  # noqa: E402
from aap.graphs.main import _collapse_chains  # noqa: E402


def _s(i, agent, deps=(), instr=None):
    return {"id": i, "agent_id": agent, "instruction": instr or f"do {i}", "depends_on": list(deps)}


def test_same_agent_chain_collapses():
    out = _collapse_chains([_s("s1", "browser_agent", instr="open youtube"),
                            _s("s2", "browser_agent", ["s1"], "search a song"),
                            _s("s3", "browser_agent", ["s2"], "play it")])
    assert len(out) == 1 and out[0]["id"] == "s1"
    assert "open youtube" in out[0]["instruction"] and "THEN: play it" in out[0]["instruction"]


def test_different_agents_and_fan_out_are_kept():
    solve_then_paste = [_s("s1", "competitive_programming_agent"), _s("s2", "desktop_agent", ["s1"])]
    assert len(_collapse_chains(solve_then_paste)) == 2
    fan = [_s("s1", "browser_agent"), _s("s2", "browser_agent", ["s1"]), _s("s3", "writer_agent", ["s1"])]
    assert len(_collapse_chains(fan)) == 3            # s1 feeds two steps: merging would hide it from s3
    parallel = [_s("s1", "browser_agent"), _s("s2", "browser_agent")]
    assert len(_collapse_chains(parallel)) == 2       # independent → stay parallel


def test_dependents_of_merged_step_are_rewired():
    out = _collapse_chains([_s("s1", "browser_agent"), _s("s2", "browser_agent", ["s1"]),
                            _s("s3", "writer_agent", ["s2"])])
    assert [s["id"] for s in out] == ["s1", "s3"] and out[1]["depends_on"] == ["s1"]


def test_verified_wait_for_finishes_without_another_look(monkeypatch):
    class Ctx:
        def node(self, *a, **k): pass
        def emit(self, *a, **k): pass
    monkeypatch.setattr(bg, "get_ctx", lambda config: Ctx())
    monkeypatch.setattr(bg, "_kind", lambda ctx: "browser")
    monkeypatch.setattr(bg.c, "tool", lambda ctx, name, **kw: {"ok": True, "found": "Accepted", "verified": True,
                                                                "details": "Accepted 65/65 testcases passed"})
    st = {"action": {"action": "wait_for", "texts": ["Accepted", "Wrong Answer"]}, "history": [], "steps": 3}
    out = bg.act(st, {})
    assert out["done"] and "Accepted" in out["answer"] and bg.after_act({**st, **out}) == "verify"
    v = bg.verify({**st, **out}, {})
    assert v["verified"] is True


def test_failed_verdict_does_not_finish(monkeypatch):
    class Ctx:
        def node(self, *a, **k): pass
    monkeypatch.setattr(bg, "get_ctx", lambda config: Ctx())
    monkeypatch.setattr(bg, "_kind", lambda ctx: "browser")
    monkeypatch.setattr(bg.c, "tool", lambda ctx, name, **kw: {"ok": True, "found": "Wrong Answer", "verified": True})
    out = bg.act({"action": {"action": "wait_for", "texts": ["Accepted", "Wrong Answer"]}, "history": []}, {})
    assert not out.get("done") and bg.after_act(out) == "observe"


def _rev_ctx(run_id):
    class Run:
        pass
    Run.run_id = run_id

    class Ctx:
        run = Run()
        msgs = []
        def emit(self, m, **k): self.msgs.append(m)
    return Ctx()


def test_polish_followups_capped_by_budget(monkeypatch):
    from aap import routing
    from aap.graphs import main as m
    monkeypatch.setattr(m, "_est", lambda a: 10_000)
    monkeypatch.setattr(m, "P", lambda st: {"level": 2})
    ok = {"s1": {"status": "success"}, "s2": {"status": "success"}}
    follow = [_s(f"f{i}", "web_research_agent") for i in range(6)]
    monkeypatch.setattr(routing, "budget_for", lambda rid, lvl: 40_000)
    monkeypatch.setattr(routing, "run_tokens", lambda rid="": 35_000)    # nearly spent: no polish at all
    ctx = _rev_ctx("r1")
    assert m._affordable_polish(ctx, {"request": "plan a weekend trip"}, ok, follow) == []
    assert any("optional follow-up" in x for x in ctx.msgs)
    monkeypatch.setattr(routing, "run_tokens", lambda rid="": 0)         # plenty left: still at most 2
    assert len(m._affordable_polish(ctx, {"request": "plan a weekend trip"}, ok, follow)) == m.MAX_POLISH_STEPS


def test_repairs_and_batches_are_not_capped(monkeypatch):
    from aap import routing
    from aap.graphs import main as m
    monkeypatch.setattr(routing, "run_tokens", lambda rid="": 10**9)
    follow = [_s(f"f{i}", "web_research_agent") for i in range(4)]
    bad = {"s1": {"status": "success"}, "s2": {"status": "failed"}}
    assert m._affordable_polish(_rev_ctx("r2"), {"request": "x"}, bad, follow) == follow
    ok = {"s1": {"status": "success"}}
    assert m._affordable_polish(_rev_ctx("r3"), {"request": "solve 5 leetcode problems"}, ok, follow) == follow


def test_step_cap_merges_sibling_research_steps():
    from aap.graphs.main import _fit_step_cap
    steps = [_s(f"r{i}", "web_research_agent", instr=f"research topic {i}") for i in range(6)] + \
            [_s("p", "planning_agent", [f"r{i}" for i in range(6)]), _s("w", "writing_agent", ["p"])]
    out = _fit_step_cap(steps, 4)
    assert len(out) == 4 and sum(s["agent_id"] == "web_research_agent" for s in out) == 2
    research = [s for s in out if s["agent_id"] == "web_research_agent"]
    assert all(f"research topic {i}" in "\n".join(s["instruction"] for s in research) for i in range(6))  # nothing lost
    plan = next(s for s in out if s["id"] == "p")
    assert set(plan["depends_on"]) == {s["id"] for s in research}       # rewired to the merged steps


def test_step_cap_never_drops_distinct_work():
    from aap.graphs.main import _fit_step_cap
    steps = [_s("a", "web_research_agent"), _s("b", "planning_agent"), _s("c", "writing_agent")]
    assert len(_fit_step_cap(steps, 2)) == 3                          # different agents: nothing to merge, keep all


def test_long_goals_never_invent_sites():
    long_goal = ("On the LeetCode Two Sum page already open from s2, open in the editor the code, go to problem "
                 "submissions at the top and check from the list that it was accepted")
    assert not any(u for u in bg.offline_plan(long_goal) if re.search(r"www\.(in|problem|at|from)\.com", u))
    assert "https://www.youtube.com" in " ".join(bg.offline_plan("open youtube"))       # short + known: still works
    assert any("leetcode.com/problems/two-sum" in u for u in bg.offline_plan(long_goal + " https://leetcode.com/problems/two-sum/"))


def test_rejected_action_is_reasked_not_replaced_by_a_guess(monkeypatch):
    calls = []

    def fake_ask(ctx, sys_p, user, fallback, validate=None, role=None, **k):
        calls.append(user)
        cand = {"action": "goto", "url": "https://leetcode.com/problems/two-sum/submissions/"} if len(calls) == 1 \
            else {"action": "click", "ref": "9", "why": "Submit"}
        return cand if validate(cand) else fallback()

    class Ctx:
        class run:
            run_id = "r"

        class task:
            task_id = "t"

        def node(self, *a, **k): pass
        def emit(self, *a, **k): pass

        def llm(self):
            class L:
                mode = "openai"
            return L()
    monkeypatch.setattr(bg, "get_ctx", lambda config: Ctx())
    monkeypatch.setattr(bg, "_kind", lambda ctx: "browser")
    monkeypatch.setattr(bg.c, "ask_json", fake_ask)
    from aap.core import execstate
    banned = execstate.action_sig({"action": "goto", "url": "https://leetcode.com/problems/two-sum/submissions/"})
    st = {"goal": "open in the editor the code, go to problem submissions at the top and submit it", "steps": 1,
          "history": [], "observation": {"url": "https://leetcode.com/problems/two-sum/", "elements": [], "text": "x"},
          "avoid": [banned]}
    out = bg._decide(st, {})
    assert len(calls) == 2 and "YOUR LAST PROPOSAL WAS REJECTED" in calls[1]
    assert out["action"]["action"] == "click"                       # the model's 2nd answer, not www.in.com


def test_followups_keep_their_order():
    from aap.graphs.main import _order_followups
    fs = [{"id": "f1", "agent_id": "competitive_programming_agent", "instruction": "rewrite in class Solution form"},
          {"id": "f2", "agent_id": "browser_agent", "instruction": "paste the corrected function from step f1"},
          {"id": "f3", "agent_id": "browser_agent", "instruction": "click Submit and wait for the verdict"}]
    out = {f["id"]: f["depends_on"] for f in _order_followups(fs, {"s1", "s2", "s3"})}
    assert out["f2"] == ["f1"] and out["f3"] == ["f2"] and out["f1"] == []
    clash = _order_followups([{"id": "s1", "agent_id": "browser_agent", "instruction": "x"}], {"s1"})
    assert clash[0]["id"] != "s1" and clash[0]["id"].startswith("s1_")


def test_paste_uses_upstream_solver_code_not_memory():
    old = "```python\nimport sys\n" + "x = 1\n" * 30 + "print(sys.stdin.read())\n```"     # big stale block
    new = "```python\nclass Solution:\n    def twoSum(self, nums, target):\n        return [0, 1]\n```"
    st = {"goal": "paste the code into the editor on leetcode\nMEMORY: " + old,
          "task": {"context": [{"agent_id": "competitive_programming_agent", "result": "Problem: Two Sum\n" + new}]}}
    assert bg.solution_code(st).startswith("class Solution")
    assert bg.wrong_judge_format("import sys\nprint(sum(map(int, input().split())))", "leetcode two sum")
    assert not bg.wrong_judge_format(bg.solution_code(st), "leetcode two sum")


def test_code_language_detection():
    assert bg.code_language("class Solution:\n    def twoSum(self, nums, target):\n        return []") == "Python3"
    assert bg.code_language("#include <vector>\nclass Solution { public: };") == "C++"
    assert bg.code_language("class Solution {\n    public int[] twoSum(int[] n, int t) { return n; }\n}") == "Java"
    assert bg.code_language("const f = (a) => a;") == "JavaScript"
    assert bg.code_language("hello world") == ""


def _judge_state(history, elements=None):
    code = "```python\nclass Solution:\n    def twoSum(self, nums, target):\n        return [0, 1]\n```"
    return {"goal": "on leetcode click Submit and get it accepted", "steps": len(history), "history": history,
            "task": {"context": [{"agent_id": "competitive_programming_agent", "result": code}]},
            "observation": {"url": "https://leetcode.com/problems/two-sum/", "text": "Two Sum Submit",
                            "elements": elements or [{"ref": "8", "role": "button", "name": "", "text": "Submit"}]}}


def test_submit_step_reasserts_code_then_submits_then_waits(monkeypatch):
    class Ctx:
        def node(self, *a, **k): pass
        def emit(self, *a, **k): pass
    monkeypatch.setattr(bg, "get_ctx", lambda config: Ctx())
    monkeypatch.setattr(bg, "_kind", lambda ctx: "browser")
    a1 = bg._decide(_judge_state([]), {})["action"]
    assert a1["action"] == "set_code" and a1["language"] == "Python3"         # even a "just submit" follow-up
    set_ok = {"action": {"action": "set_code"}, "result": {"ok": True, "verified": True}, "status": "succeeded"}
    a2 = bg._decide(_judge_state([set_ok]), {})["action"]
    assert a2 == {"action": "click", "ref": "8", "why": "submit the verified code"}
    clicked = {"action": {"action": "click", "ref": "8"}, "result": {"ok": True}, "status": "succeeded"}
    a3 = bg._decide(_judge_state([set_ok, clicked]), {})["action"]
    assert a3["action"] == "wait_for" and "Accepted" in a3["texts"] and a3["new_only"] is True
    bad_lang = {"action": {"action": "set_code"}, "result": {"ok": False, "language": {"current": "C++"},
                                                             "error": "could not switch to Python3"}}
    out = bg._decide(_judge_state([bad_lang]), {})
    assert out["done"] and "Not submitted" in out["answer"]


def test_acting_on_a_site_is_never_chat():
    from aap.graphs.main import _ACT_ON_SITE_RE, _names_site
    t4 = "play some english songs on youtube in an order you choose, at least 3, one after another"
    assert _names_site(t4) and _ACT_ON_SITE_RE.search(t4)
    assert not (_names_site("what is the capital of france") and _ACT_ON_SITE_RE.search("what is the capital of france"))
    assert _names_site("submit two sum on leetcode") and not _names_site("explain recursion")


def test_human_check_asks_owner_then_waits_instead_of_fighting(monkeypatch):
    class Ctx:
        def node(self, *a, **k): pass
        def emit(self, *a, **k): pass
    monkeypatch.setattr(bg, "get_ctx", lambda config: Ctx())
    monkeypatch.setattr(bg, "_kind", lambda ctx: "browser")
    st = {"goal": "make a codeforces mashup", "steps": 1, "history": [],
          "observation": {"url": "https://codeforces.com/", "elements": [],
                          "text": "codeforces.com Performing security verification This website uses a security service"}}
    assert bg._decide(st, {})["action"]["action"] == "human_check"
    st["history"] = [{"action": {"action": "human_check"}}] * 2
    out = bg._decide(st, {})
    assert out["done"] and "human check" in out["answer"]


def _els(*links, box=False):
    els = [{"ref": str(100 + i), "role": "link", "name": "", "text": f"Song {v}", "inview": True, "y": 10 * i,
            "href": f"https://www.youtube.com/watch?v={v}"} for i, v in enumerate(links)]
    els.append({"ref": "9", "role": "link", "name": "", "text": "Mix", "inview": True, "y": 0,
                "href": "https://www.youtube.com/watch?v=MIXMIXMIX00&list=RDabc"})   # playlists are never picked
    if box:
        els.append({"ref": "3", "role": "combobox", "name": "Search", "text": "", "inview": True, "y": 0})
    return els


def _playing(url, vid):
    import json as _j
    return {"action": {"action": "eval_readonly"}, "status": "succeeded",
            "result": {"ok": True, "url": url, "value": _j.dumps({"paused": False, "t": 7.5, "ended": False, "ad": False})}}


def test_play_three_songs_sequence_uses_clicks_and_reads_the_player():
    goal = "play some english songs on youtube in an order you choose, at least 3, one after another"
    A, B, C = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"
    st = {"goal": goal, "history": []}
    home = {"url": "https://www.youtube.com/", "elements": _els(box=True)}
    a = bg.media_next(st, home)
    assert a["action"] == "type" and a["ref"] == "3" and a["submit"] is True and "english" in a["text"]
    st["history"] = [{"action": a, "status": "succeeded"}]
    res = {"url": "https://www.youtube.com/results?search_query=english+songs", "elements": _els(A, B, C)}
    a = bg.media_next(st, res)
    assert a["action"] == "click" and a["ref"] == "100"                       # first real song, not the mix
    hist = st["history"] + [{"action": a, "status": "succeeded"}]
    watch = lambda v: {"url": f"https://www.youtube.com/watch?v={v}", "elements": _els(A, B, C)}  # noqa: E731
    a = bg.media_next({"goal": goal, "history": hist}, watch(A))
    assert a["action"] == "wait"
    hist += [{"action": a, "status": "succeeded"}]
    a = bg.media_next({"goal": goal, "history": hist}, watch(A))
    assert a["action"] == "eval_readonly" and "video" in a["expr"]
    hist += [_playing(f"https://www.youtube.com/watch?v={A}", A)]
    assert bg.played_videos(hist) == [A]
    a = bg.media_next({"goal": goal, "history": hist}, watch(A))
    assert a["action"] == "click" and a["ref"] == "101" and "next song" in a["why"]       # B, a real click
    hist += [_playing(f"https://www.youtube.com/watch?v={B}", B)]
    hist += [_playing(f"https://www.youtube.com/watch?v={C}", C)]
    done = bg.media_next({"goal": goal, "history": hist}, watch(C))
    assert done["action"] == "done" and done["verified"] and done["answer"].count("watch?v=") == 3


def test_paused_or_ad_player_does_not_count_as_played():
    import json as _j
    h = [{"action": {"action": "eval_readonly"}, "result": {"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
                                                            "value": _j.dumps({"paused": True, "t": 0, "ad": False})}},
         {"action": {"action": "eval_readonly"}, "result": {"url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
                                                            "value": _j.dumps({"paused": False, "t": 9, "ad": True})}}]
    assert bg.played_videos(h) == []
    assert bg.media_next({"goal": "open github", "history": []}, {"url": "https://github.com"}) is None
