"""One small, real-outcome test per AgentOS agent (TODO P2b) — the data table + runner used by
scripts/run_test_cases.py (section 17.x → Tests page / nightly) and scripts/run_agent_tests.py (CLI + docs/AGENT_TESTS.md).

Rules for every case:
- the agent is run DIRECTLY (POST /api/agents/<id>/run) on a cheap task with seeded files under /workspace/agent_tests/<id>;
- the verdict comes from the REAL result (code is executed/compiled, SQL is run, tests are mutation-checked, URLs are
  fetched, files are read) — never from what the agent says about itself;
- a case also has a token budget: passing the verifier but burning far more tokens than the task needs is a FAIL, so a
  slow/wasteful prompt shows up exactly like a wrong answer;
- cases that need the owner's accounts/desktop or cost a lot are marked skip / heavy (run with AOS_HEAVY=1).
Runs are traced to LangSmith automatically when tracing is on (run name = agent id, see aap/tracing.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


# ----------------------------------------------------------------------------------------------- pure helpers
def sentences(text: str) -> list[str]:
    """Sentences of a plain-prose answer (bullets/markdown markers and code ignored)."""
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    t = re.sub(r"(?m)^\s*(?:[#>*\-]+|\d+[.)])\s*", "", t)
    return [s for s in re.split(r"(?<=[.!?])\s+", t.strip()) if re.search(r"\w", s)]


def code_block(text: str, langs: str = "python|py") -> str:
    m = re.search(rf"```(?:{langs})?\s*\n(.*?)```", text or "", re.S)
    return m.group(1) if m else ""


def words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def lis_len(a: list[int]) -> int:
    best = [1] * len(a)
    for i in range(len(a)):
        for j in range(i):
            if a[j] < a[i]:
                best[i] = max(best[i], best[j] + 1)
    return max(best, default=0)


def urls_in(text: str) -> list[str]:
    return list(dict.fromkeys(u.rstrip(").,]>*'\"") for u in re.findall(r"https?://[^\s)\]>\"']+", text or "")))


def prompt_hash(spec: dict) -> str:
    return hashlib.sha1((spec.get("system_prompt") or "").encode("utf-8")).hexdigest()[:8]


# ----------------------------------------------------------------------------------------------- verifier toolkit
class T:
    """What a verifier gets: the final answer, the run's events, the agent's folder, and ways to RUN things."""

    def __init__(self, H, case: dict, rid: str, run: dict, folder: Path):
        self.H, self.case, self.rid, self.run, self.dir = H, case, rid, run, folder
        self.final = run.get("final") or ""
        self.events = run.get("events") or []

    def read(self, rel: str) -> str:
        p = self.dir / rel
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

    def exists(self, rel: str) -> bool:
        return (self.dir / rel).exists()

    def sh(self, cmd: list[str], cwd: Path | None = None, inp: str | None = None, timeout: int = 60) -> tuple[int, str]:
        try:
            r = subprocess.run(cmd, cwd=str(cwd or self.dir), input=inp, capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8", errors="replace")
            return r.returncode, (r.stdout + r.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            return 99, f"{type(e).__name__}: {e}"

    def py(self, code: str, timeout: int = 60) -> tuple[int, str]:
        return self.sh([sys.executable, "-c", code], timeout=timeout)

    def tool_calls(self, prefix: str = "") -> list[dict]:
        return [e for e in self.events if e["type"] == "tool" and e["status"] != "running"
                and e["message"].startswith(prefix)]

    def agents_ran(self) -> list[str]:
        return [e["agent_id"] for e in self.events if e["type"] == "agent" and e["status"] == "running"]

    def api(self, path: str, **kw):
        return self.H.c.get(path, **kw).json()


def v_file_py(rel: str, check: str):
    """Verifier: run `check` (python) inside the agent's folder; it must exit 0 and print nothing alarming."""
    def v(t: T):
        if not t.exists(rel):
            return False, f"{rel} was not created"
        rc, out = t.py(check)
        return rc == 0, (f"{rel} works: {out[-80:]}" if rc == 0 else f"{rel} failed: {out[-200:]}")
    return v


# ----------------------------------------------------------------------------------------------- the verifiers
def v_main(t: T):
    ok = "51" in t.final and len(t.final) < 400 and not [a for a in t.agents_ran() if a != "main_agent"]
    return ok, f"final={t.final[:60]!r} agents={sorted(set(t.agents_ran()))}"


def v_tool_builder(t: T):
    tools = [x for x in t.api("/api/tools") if x["custom"] and re.search(r"km|mile", x["name"] + x["description"], re.I)]
    if not tools:
        return False, "no km→miles tool registered"
    x = tools[-1]
    arg = next(iter(x["params"]), "km")
    out = t.H.py_run(x["code"], {arg: 10})
    val = json.dumps(out.get("output"))
    t.case["_cleanup_tool"] = x["name"]
    return bool(re.search(r"6\.2137", val)), f"tool={x['name']} 10 km → {val[:60]}"


def cleanup_tool(t: T):
    if t.case.get("_cleanup_tool"):
        t.H.c.delete(f"/api/tools/{t.case['_cleanup_tool']}")


def v_skill_writer(t: T):
    skills = t.api("/api/skills")
    mine = [s for s in skills if re.search(r"commit", s["id"] + s["name"], re.I)]
    md = ""
    for s in mine:
        p = t.H.DATA / "skills" / s["id"] / "SKILL.md"
        if p.exists():
            md = p.read_text(encoding="utf-8")
            t.case["_cleanup_skill"] = s["id"]
    ok = md.startswith("---") and re.search(r"^name:", md, re.M) and re.search(r"^description:", md, re.M) \
        and re.search(r"imperative", md, re.I) and re.search(r"72", md)
    return bool(ok), f"skills={[s['id'] for s in mine]} frontmatter={md.startswith('---')} len={len(md)}"


def cleanup_skill(t: T):
    if t.case.get("_cleanup_skill"):
        shutil.rmtree(t.H.DATA / "skills" / t.case["_cleanup_skill"], ignore_errors=True)


def v_registry(t: T):
    want = [a["id"] for a in t.api("/api/agents") if a.get("category") == "Education"]
    missing = [w for w in want if w not in t.final]
    return bool(want) and not missing, f"want={want} missing={missing}"


def v_memory(t: T):
    hits = t.H.c.get("/api/memory", params={"q": "blue-otter"}).json()
    items = hits if isinstance(hits, list) else hits.get("items", [])
    mine = [m for m in items if "blue-otter-42" in json.dumps(m)]
    t.case["_cleanup_mem"] = [m.get("id") for m in mine if m.get("id")]
    return bool(mine), f"memory rows with the passphrase: {len(mine)}"


def cleanup_memory(t: T):
    for mid in t.case.get("_cleanup_mem") or []:
        t.H.c.delete(f"/api/memory/{mid}")


def v_scheduler(t: T):
    mine = [s for s in t.api("/api/schedules") if "agent-test-ping" in s["name"]]
    t.case["_cleanup_sched"] = [s["id"] for s in mine]
    ok = any(re.fullmatch(r"15 7 \* \* \*", s.get("cron", "")) for s in mine)
    return ok, f"schedules={[(s['name'], s.get('cron')) for s in mine]}"


def cleanup_schedule(t: T):
    for sid in t.case.get("_cleanup_sched") or []:
        t.H.c.delete(f"/api/schedules/{sid}")


REVIEW_SEED = '''import sqlite3


def get_user(conn, name):
    q = "SELECT * FROM users WHERE name = '" + name + "'"
    return conn.execute(q).fetchall()


def average(xs):
    return sum(xs) / len(xs)
'''


def v_review(t: T):
    inj = re.search(r"inject", t.final, re.I)
    div = re.search(r"empty|zero|ZeroDivision|division", t.final, re.I)
    return bool(inj and div), f"sql-injection found={bool(inj)} empty-list division found={bool(div)}"


STATS_SEED = '''def median(xs):
    s = sorted(xs)
    n = len(s)
    if n % 2:
        return s[n // 2]
    return (s[n // 2] + s[n // 2 + 1]) / 2
'''
STATS_TEST = '''from stats import median


def test_odd():
    assert median([3, 1, 2]) == 2


def test_even():
    assert median([1, 2, 3, 4]) == 2.5


def test_single():
    assert median([5]) == 5


def test_two():
    assert median([10, 20]) == 15
'''


def v_debug(t: T):
    untouched = t.read("test_stats.py") == STATS_TEST
    rc, out = t.sh([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    return rc == 0 and untouched, f"pytest rc={rc} tests_untouched={untouched} {out[-90:]}"


MATHX_SEED = '''def clamp(x, lo, hi):
    if lo > hi:
        raise ValueError("lo > hi")
    return max(lo, min(x, hi))


def is_prime(n):
    if n < 2:
        return False
    i = 2
    while i * i <= n:
        if n % i == 0:
            return False
        i += 1
    return True
'''


def v_testing(t: T):
    tf = [p for p in t.dir.glob("test_*.py")]
    if not tf:
        return False, "no test_*.py written"
    n = sum(len(re.findall(r"(?m)^\s*def test_", p.read_text(encoding="utf-8"))) for p in tf)
    rc, out = t.sh([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    if rc != 0:
        return False, f"their own tests fail: {out[-120:]}"
    mut = t.dir.parent / (t.dir.name + "_mutant")      # the tests must CATCH a broken implementation (mutation check)
    shutil.rmtree(mut, ignore_errors=True)
    shutil.copytree(t.dir, mut, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (mut / "mathx.py").write_text(MATHX_SEED.replace("if n < 2:\n        return False", "if n < 0:\n        return False")
                                  .replace("max(lo, min(x, hi))", "min(x, hi)"), encoding="utf-8")
    rc2, _ = t.sh([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=mut)
    shutil.rmtree(mut, ignore_errors=True)
    return n >= 4 and rc2 != 0, f"{n} tests pass; mutant implementation caught={rc2 != 0}"


def v_competitive(t: T):
    code = code_block(t.final)
    if not code:
        return False, "no python code block in the answer"
    rnd = random.Random(7)
    bad = 0
    for _ in range(8):
        n = rnd.randint(1, 12)
        a = [rnd.randint(1, 9) for _ in range(n)]
        rc, out = t.sh([sys.executable, "-c", code], inp=f"{n}\n{' '.join(map(str, a))}\n", timeout=20)
        if rc != 0 or out.split()[-1:] != [str(lis_len(a))]:
            bad += 1
    return bad == 0, f"8 random stdin cases vs brute force: {bad} wrong"


GEOMETRY_SEED = '''import math


def area_circle(radius):
    return math.pi * radius ** 2


def perimeter_rect(width, height):
    return 2 * (width + height)
'''


def v_docs(t: T):
    need = ["area_circle", "perimeter_rect", "radius", "width", "height"]
    miss = [w for w in need if w not in t.final]
    return not miss, f"missing in the docs: {miss}"


def v_github(t: T):
    dirty = re.search(r"untracked|not clean|dirty|uncommitted|modified|changes", t.final, re.I)
    return "7f3a" in t.final and bool(dirty), f"commit id found={'7f3a' in t.final} dirty noticed={bool(dirty)}"


def setup_git(d: Path):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    (d / "a.txt").write_text("one\n", encoding="utf-8")
    for cmd in (["git", "init", "-q"], ["git", "add", "a.txt"], ["git", "commit", "-q", "-m", "agent-test: add a.txt 7f3a"]):
        subprocess.run(cmd, cwd=str(d), env=env, capture_output=True, timeout=30)
    (d / "scratch.txt").write_text("uncommitted\n", encoding="utf-8")


def v_js(t: T):
    if not t.exists("fizz.js"):
        return False, "fizz.js was not created"
    code = ("const f=require('./fizz.js'); const fn=(typeof f==='function')?f:f.fizzbuzz; const r=fn(15); "
            "if(JSON.stringify(r.slice(0,5))!==JSON.stringify(['1','2','Fizz','4','Buzz'])||r[14]!=='FizzBuzz'||r.length!==15)"
            "{console.log(JSON.stringify(r));process.exit(1)}")
    if not shutil.which("node"):
        src = t.read("fizz.js")
        return bool(re.search(r"fizzbuzz", src) and re.search(r"module\.exports", src)), "node missing: static check only"
    rc, out = t.sh(["node", "-e", code])
    return rc == 0, f"node fizzbuzz(15) rc={rc} {out[-100:]}"


def v_react(t: T):
    src = t.read("Counter.jsx")
    if not src:
        return False, "Counter.jsx was not created"
    checks = {"useState": "useState" in src, "export default": "export default" in src, "button": "<button" in src,
              "onClick": "onClick" in src, "plus label": re.search(r">\s*\+\s*<|\{\s*['\"]\+['\"]\s*\}", src) is not None,
              "balanced": src.count("{") == src.count("}") and src.count("(") == src.count(")")}
    bad = [k for k, v in checks.items() if not v]
    return not bad, f"missing: {bad}" if bad else "component has state, a + button, default export"


def v_kotlin(t: T):
    src = t.read("Greeter.kt")
    ok = re.search(r"class\s+Greeter\s*\(\s*(val\s+)?name\s*:\s*String", src) and re.search(r"fun\s+greet\s*\(\s*\)", src) \
        and re.search(r"Hello, \$\{?name\}?!", src)
    return bool(ok), "Greeter.kt matches the spec" if ok else f"Greeter.kt wrong/missing ({len(src)} chars)"


def v_sql(t: T):
    if not t.exists("schema.sql"):
        return False, "schema.sql was not created"
    code = (
        "import sqlite3\n"
        "c=sqlite3.connect(':memory:'); c.execute('PRAGMA foreign_keys=ON')\n"
        "c.executescript(open('schema.sql').read())\n"
        "c.execute(\"INSERT INTO users(email) VALUES ('a@x.io')\")\n"
        "uid=c.execute('SELECT id FROM users').fetchone()[0]\n"
        "c.execute('INSERT INTO orders(user_id,total) VALUES (?,?)',(uid,9.5))\n"
        "def fails(sql,args=()):\n"
        "    try: c.execute(sql,args); return False\n"
        "    except sqlite3.IntegrityError: return True\n"
        "assert fails(\"INSERT INTO users(email) VALUES ('a@x.io')\"), 'duplicate email accepted'\n"
        "assert fails('INSERT INTO orders(user_id,total) VALUES (999,1)'), 'orphan order accepted'\n"
        "print('schema ok')\n")
    rc, out = t.py(code)
    return rc == 0, out[-140:]


def v_cpp(t: T):
    if not t.exists("sum.cpp"):
        return False, "sum.cpp was not created"
    if not shutil.which("g++"):
        return bool(re.search(r"cin|scanf", t.read("sum.cpp"))), "g++ missing: static check only"
    rc, out = t.sh(["g++", "-O1", "-o", "sum_bin", "sum.cpp"], timeout=120)
    if rc != 0:
        return False, f"does not compile: {out[-160:]}"
    outs = [t.sh(["./sum_bin"], inp=i)[1].split()[-1:] for i in ("3 4\n", "-5 12\n", "100 200\n")]
    return outs == [["7"], ["7"], ["300"]], f"outputs={outs}"


def v_web_research(t: T):
    urls = urls_in(t.final)
    live = [u for u in urls[:3] if t.H.http_ok(u)]
    return "canberra" in t.final.lower() and bool(live), f"Canberra={'canberra' in t.final.lower()} live urls={len(live)}/{len(urls)}"


def v_docresearch(t: T):
    ok = "48,000" in t.final.replace(" ", "") or "48000" in t.final.replace(",", "")
    return ok and "Marta" in t.final, f"budget found={ok} lead found={'Marta' in t.final}"


ARTICLE = ("The Harbor Bridge was designed by Elena Ruiz in 1927. Construction began in 1929 and took three years. "
           "The bridge opened in 1932 and carried both trains and cars. It is 503 metres long. Maintenance closed one lane "
           "in 2019. The city plans a new walkway beside it. Locals call it the Iron Gate.")


def v_summary(t: T):
    n = len(sentences(t.final))
    return n <= 3 and "1932" in t.final, f"{n} sentences, 1932 present={'1932' in t.final}"


def v_research(t: T):
    bullets = len(re.findall(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S", t.final))
    live = [u for u in urls_in(t.final)[:3] if t.H.http_ok(u)]
    return bullets >= 3 and bool(live), f"bullets={bullets} live urls={len(live)}"


def v_planning(t: T):
    miss = [d for d in ("Day 1", "Day 2", "Day 3") if not re.search(d, t.final, re.I)]
    return not miss and "sql" in t.final.lower(), f"missing days={miss}"


def v_writing(t: T):
    n = len(sentences(t.final))
    return n == 2 and "bottle" in t.final.lower(), f"{n} sentences, 'bottle'={'bottle' in t.final.lower()}"


def v_writer(t: T):
    low = " " + t.final.lower() + " "
    bad = [w for w in (" gonna ", " u ", " asap") if w in low]
    return not bad and "file" in low and len(t.final) > 20, f"informal words left={bad}"


def v_data(t: T):
    scores = json.loads(t.read("scores.json"))
    mean = sum(scores) / len(scores)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t.final)]
    has = lambda v, tol: any(abs(n - v) <= tol for n in nums)  # noqa: E731
    found = [has(mean, 0.06), has(max(scores), 0), has(min(scores), 0)]
    return all(found), f"mean={mean:.2f} max={max(scores)} min={min(scores)} found={found}"


def v_teacher(t: T):
    n = words(t.final)
    return n <= 150 and re.search(r"itself|smaller|same", t.final, re.I) is not None, f"{n} words"


def v_curriculum(t: T):
    miss = [w for w in ("1", "2", "3", "4") if not re.search(rf"week\s*{w}\b", t.final, re.I)]
    return not miss and "git" in t.final.lower(), f"missing weeks={miss}"


def v_questions(t: T):
    q = len(re.findall(r"\?", t.final))
    opts = len(re.findall(r"(?m)^\s*[-*]?\s*\(?\**[Bb][.)]", t.final))
    ans = len(re.findall(r"(?i)answer", t.final))
    return q >= 3 and opts >= 3 and ans >= 3 and "photosynth" in t.final.lower(), f"questions={q} option-B lines={opts} answers={ans}"


def v_browser(t: T):
    used = t.tool_calls("browser")
    return "example domain" in t.final.lower() and bool(used), f"browser tool calls={len(used)} final={t.final[:60]!r}"


def v_cleanup(t: T):
    kept = all(t.exists(n) for n in ("big.bin", "notes.txt", "tiny.txt"))
    return "big.bin" in t.final and kept, f"names big.bin={'big.bin' in t.final} nothing deleted={kept}"


def setup_cleanup(d: Path):
    (d / "big.bin").write_bytes(b"\0" * 3_000_000)
    (d / "notes.txt").write_text("some notes\n" * 50, encoding="utf-8")
    (d / "tiny.txt").write_text("x", encoding="utf-8")


def v_terminal(t: T):
    ran = t.tool_calls("run_command")
    return "agentos-ok" in t.final and "hello.txt" in t.final and bool(ran), f"run_command calls={len(ran)}"


def v_softwaredev(t: T):
    if not t.exists("textutil.py"):
        return False, "textutil.py was not created"
    rc, out = t.sh([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    ok = rc == 0 and any(p.name.startswith("test_") for p in t.dir.glob("*.py"))
    return ok, f"pytest rc={rc} {out[-90:]}"


def v_selfimprove(t: T):
    bullets = len(re.findall(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S", t.final))
    return bullets >= 3, f"{bullets} bullets"


def v_main_desktop(t: T):
    return bool(re.search(r"screenshot", t.final, re.I)), f"final={t.final[:80]!r}"


# ----------------------------------------------------------------------------------------------- the table
D = "{dir}"
CASES: list[dict] = [
    # ---- System
    dict(agent="main_agent", title="answers a one-line question directly", budget=8000, timeout=180, verify=v_main,
         instruction="what is 17*3? reply with just the number"),
    dict(agent="agent_builder", title="designs + registers a miles→km agent", heavy=True, budget=250000, timeout=1500,
         instruction="Build an agent that converts miles to kilometres (1 mile = 1.609344 km). Keep it tiny.",
         verify=lambda t: (any("mile" in a["id"] or "km" in a["id"] for a in t.api("/api/agents")), "agent registered?")),
    dict(agent="tool_builder_agent", title="creates a km→miles tool that really computes", budget=25000, timeout=600,
         verify=v_tool_builder, cleanup=cleanup_tool,
         instruction="Create a tool named km_to_miles that converts kilometres to miles (1 km = 0.621371 miles). "
                     "Parameter: km (number)."),
    dict(agent="skill_writer_agent", title="writes a SKILL.md with frontmatter", budget=25000, timeout=600,
         verify=v_skill_writer, cleanup=cleanup_skill,
         instruction="Create a skill named agent-test-commit-style: how to write conventional commit messages "
                     "(type(scope): subject, imperative mood, subject at most 72 characters)."),
    dict(agent="registry_manager", title="lists the Education agents", budget=15000, timeout=300, verify=v_registry,
         instruction="List the ids of all agents whose category is Education."),
    dict(agent="memory_agent", title="stores a fact in long-term memory", budget=15000, timeout=300,
         verify=v_memory, cleanup=cleanup_memory,
         instruction="Remember this fact: the agent-test passphrase is 'blue-otter-42'."),
    dict(agent="scheduler_agent", title="creates a daily 07:15 schedule", budget=15000, timeout=300,
         verify=v_scheduler, cleanup=cleanup_schedule,
         instruction="Create a recurring schedule named agent-test-ping that runs every day at 07:15 with the prompt 'say hi'."),
    # ---- Programming
    dict(agent="coding_agent", title="writes slugify() that works", budget=60000, timeout=900,
         instruction=f"In {D} create slug.py defining slugify(s): lowercase, runs of non-alphanumeric characters become "
                     "a single '-', no leading/trailing '-'. Check it quickly.",
         verify=v_file_py("slug.py", "from slug import slugify\nassert slugify('Hello, World!')=='hello-world'\n"
                                     "assert slugify('  A--B  c ')=='a-b-c'\nassert slugify('!!!')==''\nassert slugify('x')=='x'")),
    dict(agent="code_review_agent", title="finds the SQL injection + empty-list bug", budget=40000, timeout=600,
         seed={"app.py": REVIEW_SEED}, verify=v_review,
         instruction=f"Review {D}/app.py. List the concrete bugs and security problems."),
    dict(agent="debugging_agent", title="fixes median() so the tests pass", budget=60000, timeout=900,
         seed={"stats.py": STATS_SEED, "test_stats.py": STATS_TEST}, verify=v_debug,
         instruction=f"The tests in {D} fail. Find the root cause and fix the code (not the tests)."),
    dict(agent="testing_agent", title="writes tests that catch a broken implementation", budget=60000, timeout=900,
         seed={"mathx.py": MATHX_SEED}, verify=v_testing,
         instruction=f"Write pytest tests in {D}/test_mathx.py for clamp and is_prime in {D}/mathx.py, including edge "
                     "cases (negatives, 0, 1, 2, lo > hi). Run them."),
    dict(agent="competitive_programming_agent", title="longest increasing subsequence (stdin) vs brute force",
         budget=60000, timeout=900, verify=v_competitive,
         instruction="Problem: the first line has n, the second line n integers. Print the length of the longest strictly "
                     "increasing subsequence. 1<=n<=2000. Read stdin, write stdout. Give the final Python solution in one "
                     "```python block."),
    dict(agent="documentation_agent", title="documents every function of geometry.py", budget=25000, timeout=600,
         seed={"geometry.py": GEOMETRY_SEED}, verify=v_docs,
         instruction=f"Write a short README section documenting every function in {D}/geometry.py (parameters, return value)."),
    dict(agent="github_agent", title="reads a git repo (last commit + dirty tree)", budget=30000, timeout=600,
         setup=setup_git, verify=v_github,
         instruction=f"In the git repository {D} report the message of the latest commit and whether the working tree is clean."),
    # ---- Development
    dict(agent="python_agent", title="writes count_words()", budget=60000, timeout=900,
         instruction=f"In {D} create wordcount.py defining count_words(text) -> dict mapping each lowercase word (letters "
                     "only) to its count. Check it quickly.",
         verify=v_file_py("wordcount.py", "from wordcount import count_words\n"
                                          "assert count_words('The cat, the DOG. the end')=={'the':3,'cat':1,'dog':1,'end':1}\n"
                                          "assert count_words('')=={}")),
    dict(agent="javascript_agent", title="writes fizzbuzz() as a CommonJS module", budget=60000, timeout=900,
         verify=v_js,
         instruction=f"In {D} create fizz.js exporting fizzbuzz(n) (CommonJS module.exports) that returns an array of the "
                     "strings for 1..n: 'Fizz' for multiples of 3, 'Buzz' for 5, 'FizzBuzz' for both, else the number as a string."),
    dict(agent="react_agent", title="writes a Counter component", budget=60000, timeout=900, verify=v_react,
         instruction=f"In {D} create Counter.jsx: a React function component Counter with useState for a count, showing "
                     "the count and a button labelled '+' that increments it. Default export."),
    dict(agent="android_agent", title="writes a Kotlin Greeter class", budget=60000, timeout=900, verify=v_kotlin,
         instruction=f"In {D} create Greeter.kt with class Greeter(val name: String) and fun greet(): String that returns "
                     "\"Hello, $name!\" (a Kotlin string template)."),
    dict(agent="database_agent", title="writes a schema with keys + constraints", budget=60000, timeout=900,
         verify=v_sql,
         instruction=f"In {D} create schema.sql (SQLite) with users(id integer primary key, email text unique not null) and "
                     "orders(id integer primary key, user_id integer not null references users(id), total real not null)."),
    dict(agent="cpp_agent", title="writes sum.cpp that compiles and adds", budget=60000, timeout=900, verify=v_cpp,
         instruction=f"In {D} create sum.cpp: read two integers from stdin and print their sum. It must compile with g++."),
    # ---- Research
    dict(agent="web_research_agent", title="capital of Australia with a live source", budget=60000, timeout=600,
         verify=v_web_research,
         instruction="What is the capital city of Australia? Answer in one sentence and cite one source URL."),
    dict(agent="document_research_agent", title="answers from two local documents", budget=30000, timeout=600,
         seed={"zephyr.md": "# Project Zephyr\nBudget: 48,000 euros. Lead: Marta Silva. Start: March.\n",
               "atlas.md": "# Project Atlas\nBudget: 12,500 euros. Lead: Joao Pires.\n"},
         verify=v_docresearch,
         instruction=f"Using the documents in {D}: what is Project Zephyr's budget and who leads it?"),
    dict(agent="summarization_agent", title="2-sentence summary keeps the key fact", budget=15000, timeout=300,
         seed={"article.txt": ARTICLE}, verify=v_summary,
         instruction=f"Summarize {D}/article.txt in at most 2 sentences."),
    dict(agent="research_agent", title="compares two languages with sources", heavy=True, budget=250000, timeout=1500,
         verify=v_research,
         instruction="Compare Python and Go for writing command-line tools: exactly 3 bullets, and cite one source link."),
    # ---- Productivity
    dict(agent="planning_agent", title="3-day SQL study plan", budget=25000, timeout=600, verify=v_planning,
         instruction="Plan a 3-day study schedule for learning SQL basics. Label the days Day 1, Day 2, Day 3, one task per line."),
    dict(agent="writing_agent", title="exactly two sentences of copy", budget=15000, timeout=300, verify=v_writing,
         instruction="Write exactly two sentences of marketing copy for a reusable steel water bottle."),
    dict(agent="writer_agent", title="formal rewrite", budget=12000, timeout=300, verify=v_writer,
         instruction="Rewrite formally: 'gonna send u the file asap'. Reply with the rewritten sentence only."),
    dict(agent="data_analysis_agent", title="mean/max/min of a csv", budget=40000, timeout=600, verify=v_data,
         instruction=f"Analyze {D}/scores.csv: report the mean, max and min of the score column."),
    # ---- Education
    dict(agent="teacher_agent", title="explains recursion in <120 words", budget=15000, timeout=300, verify=v_teacher,
         instruction="Explain recursion to a 10 year old in under 120 words."),
    dict(agent="curriculum_agent", title="4-week Git curriculum", budget=20000, timeout=300, verify=v_curriculum,
         instruction="Make a 4-week curriculum for learning Git: one line per week, labelled Week 1 to Week 4."),
    dict(agent="question_generator_agent", title="3 MCQs with options and answers", budget=20000, timeout=300,
         verify=v_questions,
         instruction="Generate exactly 3 multiple-choice questions about photosynthesis, each with options A-D and the answer."),
    # ---- Automation
    dict(agent="browser_agent", title="reads example.com's title", budget=40000, timeout=600, verify=v_browser,
         instruction="Open https://example.com and tell me the page title."),
    dict(agent="desktop_agent", title="screenshot of the real screen", needs_desktop=True, budget=30000, timeout=300,
         verify=v_main_desktop, instruction="Take a screenshot of my screen and tell me where it was saved."),
    dict(agent="cleanup_agent", title="finds the biggest file, deletes nothing", budget=30000, timeout=600,
         setup=setup_cleanup, verify=v_cleanup,
         instruction=f"In {D} find the largest file and report its name and size. Do NOT delete anything."),
    dict(agent="terminal_agent", title="runs commands and reports their output", budget=25000, timeout=600,
         seed={"hello.txt": "hi\n"}, verify=v_terminal,
         instruction=f"In {D} run `echo agentos-ok` and list the files, then report both outputs."),
    dict(agent="software_dev_agent", title="builds a tiny tested package", heavy=True, budget=300000, timeout=1800,
         verify=v_softwaredev,
         instruction=f"In {D} create textutil.py with reverse_words(s) (reverse the order of words) and a pytest file "
                     "test_textutil.py with 3 tests; run them."),
    dict(agent="self_improvement_agent", title="lists 3 platform features", heavy=True, budget=150000, timeout=900,
         verify=v_selfimprove,
         instruction="List 3 concrete features that personal AI agent platforms (OpenClaw, OpenAI Dots style) offer. Exactly "
                     "3 bullets. Do not build anything."),
]
for _c in CASES:
    _c.setdefault("seed", {})
    _c["title"] = _c.get("title", "")
SEEDS_DATA = {"data_analysis_agent": lambda d: _seed_scores(d)}


def _seed_scores(d: Path) -> None:
    rnd = random.Random(11)
    vals = [rnd.randint(40, 100) for _ in range(25)]
    (d / "scores.csv").write_text("student,score\n" + "\n".join(f"s{i},{v}" for i, v in enumerate(vals)) + "\n",
                                  encoding="utf-8")
    (d / "scores.json").write_text(json.dumps(vals), encoding="utf-8")


# ----------------------------------------------------------------------------------------------- the runner
def run_case(H, case: dict) -> dict:
    """Run ONE agent test against the live gateway; returns a harness result row (+ agent/prompt/steps metrics)."""
    aid = case["agent"]
    if case.get("skip"):
        return H.skip(case["skip"])
    if case.get("heavy") and not os.environ.get("AOS_HEAVY"):
        return H.skip("heavy (builds/researches a lot, ≈150-300k tokens): run with AOS_HEAVY=1")
    if case.get("needs_desktop") and not H.DESKTOP:
        return H.skip("needs the Windows desktop — the Linux test VM has none")
    folder = H.WS / "agent_tests" / aid
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    for rel, text in case["seed"].items():
        (folder / rel).write_text(text, encoding="utf-8")
    if aid in SEEDS_DATA:
        SEEDS_DATA[aid](folder)
    if case.get("setup"):
        case["setup"](folder)
    spec = H.c.get(f"/api/agents/{aid}").json()
    t0, tok0 = time.time(), H.tokens()
    rid = H.c.post(f"/api/agents/{aid}/run",
                   json={"instruction": case["instruction"].replace("{dir}", f"/workspace/agent_tests/{aid}")}).json()["run_id"]
    H.wait_q(rid, timeout=case.get("timeout", 600))
    run = H.c.get(f"/api/runs/{rid}").json()
    used = max(0, H.tokens() - tok0)
    t = T(H, case, rid, run, folder)
    try:
        ok, evidence = case["verify"](t)
    except Exception as e:  # noqa: BLE001 - a crashing verifier is a failure, with the reason
        ok, evidence = False, f"verifier error: {type(e).__name__}: {e}"
    finally:
        try:
            if case.get("cleanup"):
                case["cleanup"](t)
        except Exception:  # noqa: BLE001
            pass
    budget = case.get("budget", 60000)
    over = used > budget
    steps = len(t.tool_calls())
    if run.get("status") != "done" and ok:
        ok, evidence = False, f"run ended {run.get('status')}: {evidence}"
    if over and ok:
        evidence += f" · TOO EXPENSIVE: {used:,} tokens > budget {budget:,}"
    out = H.res(ok and not over, evidence[:400], rid=rid)
    out.update(agent=aid, prompt=prompt_hash(spec), steps=steps, run_tokens=used, budget=budget,
               seconds_run=round(time.time() - t0, 1))
    return out


# ----------------------------------------------------------------------------------------------- docs
def render_markdown(rows: dict, history: dict) -> str:
    """docs/AGENT_TESTS.md from the harness rows (17.x) + the per-agent history (first vs latest)."""
    order = {c["agent"]: i for i, c in enumerate(CASES, 1)}
    lines = ["# AgentOS — per-agent tests (TODO P2b)", "",
             "Generated by `python scripts/run_agent_tests.py` (also Tests page section 17). One cheap, real-outcome test per "
             "agent; a pass needs the verifier AND staying inside the token budget. `prompt` = hash of the agent's system "
             "prompt when it ran; `first → latest` shows what prompt changes did.", "",
             "| # | Agent | Test | Result | Tokens (budget) | Steps | Seconds | Prompt | First → latest tokens |",
             "|---|---|---|---|---|---|---|---|---|"]
    icon = {"pass": "✅", "fail": "❌", "skipped": "⏭", "error": "💥"}
    tot = {"pass": 0, "fail": 0, "skipped": 0, "error": 0}
    for c in CASES:
        i = order[c["agent"]]
        r = rows.get(f"17.{i}")
        h = history.get(c["agent"], [])
        if not r:
            lines.append(f"| 17.{i} | `{c['agent']}` | {c['title']} | — not run | | | | | |")
            continue
        tot[r["status"]] = tot.get(r["status"], 0) + 1
        first = next((x["tokens"] for x in h if x.get("tokens")), None)
        last = next((x["tokens"] for x in reversed(h) if x.get("tokens")), None)
        trend = f"{first:,} → {last:,}" if first and last and len(h) > 1 else (f"{last:,}" if last else "")
        why = "" if r["status"] == "pass" else f"<br><sub>{(r.get('evidence') or r.get('note') or '')[:150]}</sub>"
        lines.append(f"| 17.{i} | `{c['agent']}` | {c['title']} | {icon.get(r['status'], r['status'])}{why} | "
                     f"{r.get('run_tokens', 0):,} ({c.get('budget', 0):,}) | {r.get('steps', '')} | "
                     f"{r.get('seconds_run', r.get('seconds', ''))} | `{r.get('prompt', '')}` | {trend} |")
    lines += ["", f"**Totals:** {tot['pass']} pass · {tot['fail']} fail · {tot['skipped']} skipped · {tot['error']} error", ""]
    return "\n".join(lines)


def update_history(rows: dict, history: dict) -> dict:
    """Append (prompt hash, tokens, status) per agent when it changed since the last recorded run."""
    for i, c in enumerate(CASES, 1):
        r = rows.get(f"17.{i}")
        if not r or r["status"] == "skipped":
            continue
        entry = {"ts": r.get("at") or time.time(), "prompt": r.get("prompt", ""), "tokens": r.get("run_tokens", 0),
                 "status": r["status"]}
        h = history.setdefault(c["agent"], [])
        if not h or (h[-1]["prompt"], h[-1]["tokens"], h[-1]["status"]) != (entry["prompt"], entry["tokens"], entry["status"]):
            h.append(entry)
    return history

