"""Data Analysis — ingest → profile → plan_analyses → compute ⇄ (repair) → interpret → report.

Heavy computation (pandas) runs in the runner via data_query/run_python; a pure-python profiler handles
inline data so simple analyses work even without the runner or an LLM.
"""
from __future__ import annotations

import csv
import io
import json
import re
import statistics

from langgraph.graph import END, START, StateGraph

from ..core.sdk import archetype, get_ctx
from . import _common as c

MAX_REPAIRS = 2


class DataState(c.BaseState, total=False):
    sources: list
    raw: str
    table: list
    profile: dict
    analyses: list
    computed: list
    repairs: int
    compute_errors: list
    insights: str
    max_repairs: int


# ------------------------------------------------------------------------------------------- local parsing
ITEM_RE = re.compile(r"^\s*([A-Za-z][\w +#./()-]*?)\s*[:=\-–,]\s*(-?\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?\s*(%)?",
                     re.M)


def parse_table(text: str) -> list[dict]:
    """CSV/TSV with header → rows; else 'label: value[/total]' lines → rows."""
    lines = [l for l in text.strip().splitlines() if l.strip()]
    if len(lines) >= 2:
        for delim in (",", "\t", ";", "|"):
            if lines[0].count(delim) >= 1 and all(l.count(delim) == lines[0].count(delim) for l in lines[1:6]):
                rows = list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delim))
                if rows:
                    return [{k.strip(): _num(v) for k, v in r.items() if k} for r in rows]
    line_rows = [_row(m.group(1), m.group(2), m.group(3)) for m in ITEM_RE.finditer(text)]
    if any(re.search(r"\d", r["label"]) for r in line_rows):   # digits in a label = mis-split sentence
        line_rows = []
    inline = _inline_rows(text)
    return line_rows if len(line_rows) >= len(inline) else inline


def _inline_rows(text: str) -> list[dict]:
    rows = []
    # inline forms within a sentence, split on , ; "and": "topic: a/b", "12 DP", "greedy 30"
    for chunk in re.split(r"[,;\n]|\band\b", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.search(r"([A-Za-z][\w+#.-]*(?:\s+[A-Za-z][\w+#.-]*)?)\s*[:=]\s*(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?",
                      chunk)
        if m:
            rows.append(_row(m.group(1), m.group(2), m.group(3)))
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?\s+([A-Za-z][\w+#.-]*)", chunk)   # "12 DP"
        if m and m.group(3).lower() not in _UNITS:
            rows.append(_row(m.group(3), m.group(1), m.group(2)))
            continue
        m = re.search(r"([A-Za-z][\w+#.-]*)\s+(\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?", chunk)   # "greedy 30"
        if m and m.group(1).lower() not in _VERBS:
            rows.append(_row(m.group(1), m.group(2), m.group(3)))
    return rows


_UNITS = {"problems", "problem", "times", "hours", "hrs", "minutes", "mins", "days", "weeks", "of", "out", "in", "x"}
_VERBS = {"solved", "did", "done", "completed", "attempted", "made", "got", "scored", "total", "about", "around"}


def _row(label: str, value: str, total: str | None) -> dict:
    row = {"label": label.strip(), "value": float(value)}
    if total:
        row["total"] = float(total)
        row["ratio"] = round(row["value"] / row["total"], 4) if row["total"] else None
    return row


def _num(v):
    if v is None:
        return None
    s = str(v).strip()
    try:
        return float(s) if re.fullmatch(r"-?\d+(?:\.\d+)?", s) else s
    except ValueError:
        return s


def profile_rows(rows: list[dict]) -> dict:
    cols: dict = {}
    for r in rows:
        for k, v in r.items():
            cols.setdefault(k, []).append(v)
    prof = {"rows": len(rows), "columns": {}}
    for k, vals in cols.items():
        nums = [v for v in vals if isinstance(v, (int, float))]
        if nums and len(nums) >= len(vals) * 0.6:
            prof["columns"][k] = {"type": "numeric", "count": len(nums), "mean": round(statistics.mean(nums), 4),
                                  "median": statistics.median(nums), "min": min(nums), "max": max(nums),
                                  "stdev": round(statistics.stdev(nums), 4) if len(nums) > 1 else 0.0}
        else:
            uniq = {str(v) for v in vals}
            prof["columns"][k] = {"type": "text", "unique": len(uniq), "top": sorted(uniq)[:5]}
    return prof


def local_compute(rows: list[dict], prof: dict) -> list[dict]:
    out = []
    numeric = [k for k, v in prof["columns"].items() if v["type"] == "numeric"]
    label = next((k for k, v in prof["columns"].items() if v["type"] == "text"), None)
    sort_key = "ratio" if "ratio" in numeric else (numeric[0] if numeric else None)
    if label and sort_key:
        ranked = sorted([r for r in rows if isinstance(r.get(sort_key), (int, float))], key=lambda r: r[sort_key])
        out.append({"name": f"lowest {sort_key}", "value": [{label: r[label], sort_key: r[sort_key]} for r in ranked[:3]]})
        out.append({"name": f"highest {sort_key}", "value": [{label: r[label], sort_key: r[sort_key]} for r in ranked[-3:][::-1]]})
    for k in numeric:
        col = prof["columns"][k]
        out.append({"name": f"{k} summary", "value": {x: col[x] for x in ("mean", "median", "min", "max", "stdev")}})
    if "value" in numeric and "total" in numeric:
        tv = sum(r.get("value", 0) for r in rows if isinstance(r.get("value"), (int, float)))
        tt = sum(r.get("total", 0) for r in rows if isinstance(r.get("total"), (int, float)))
        out.append({"name": "overall completion", "value": round(tv / tt, 4) if tt else None})
    return out


# ------------------------------------------------------------------------------------------- nodes
def ingest(state: DataState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("ingest", "Loading data")
    task = state.get("task") or {}
    paths = list(task.get("files") or []) + [f for f in c.files_in(c.instruction(state)) if f.endswith((".csv", ".json", ".txt", ".tsv"))]
    texts, srcs = [], []
    for p in paths[:5]:
        r = c.tool(ctx, "fs_read", path=p if p.startswith("/") else f"/workspace/{p}")
        if c.ok(r):
            texts.append(str(r.get("content", "")))
            srcs.append(p)
    inline = c.instruction(state)
    blocks = c.code_blocks(inline)
    texts.extend(blocks or [inline])
    if task.get("context"):
        texts.append(c.context_text(state))
        srcs.append("upstream reports")
    raw = "\n".join(texts)
    rows = []
    for t in texts:
        if t.strip().startswith(("[", "{")):
            try:
                js = json.loads(t)
                js = js if isinstance(js, list) else js.get("data", [js])
                rows = [r for r in js if isinstance(r, dict)]
                if rows:
                    break
            except Exception:  # noqa: BLE001
                pass
        rows = parse_table(t)
        if rows:
            break
    ctx.emit(f"Loaded {len(rows)} row(s) from {', '.join(srcs) or 'the request'}")
    return {"sources": srcs or ["inline"], "raw": raw[:50000], "table": rows[:5000], "repairs": 0,
            "max_repairs": min(MAX_REPAIRS, int(c.budget(ctx).get("fix_attempts", MAX_REPAIRS)))}


def profile(state: DataState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("profile", "Profiling the dataset")
    prof = profile_rows(state.get("table", [])) if state.get("table") else {"rows": 0, "columns": {}}
    file_src = next((s for s in state.get("sources", []) if s.startswith("/") or "." in s), None)
    if file_src and c.can(ctx, "data_profile"):
        r = c.tool(ctx, "data_profile", path_or_text=file_src)
        if c.ok(r):
            prof["pandas"] = r
    return {"profile": prof}


def plan_analyses(state: DataState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("plan_analyses", "Choosing analyses")
    fb = {"analyses": ["rank items", "summary statistics", "find weak areas"]}
    out = c.ask_json(ctx, "Choose 2-5 analyses that answer the user's question. Return {\"analyses\": [short descriptions]}.",
                     f"Question: {c.instruction(state)}\nProfile: {json.dumps(state['profile'], default=str)[:5000]}",
                     fallback=lambda: fb, validate=lambda d: isinstance(d.get("analyses"), list) and d["analyses"])
    c.emit_plan(ctx, {"analyses": out["analyses"][:5]}, "Analysis plan")
    return {"analyses": [str(a) for a in out["analyses"][:5]]}


def compute(state: DataState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("compute", "Computing" + (f" (repair {state['repairs']})" if state.get("repairs") else ""))
    rows, prof = state.get("table", []), state["profile"]
    computed = local_compute(rows, prof) if rows else []
    errors = []
    if not c.is_mock(ctx) and c.can(ctx, "run_python") and rows:
        code_txt = c.ask_text(ctx, (
            "Write Python `def run(input) -> dict` that performs the analyses on input['rows'] (list of dicts) using "
            "only the standard library (statistics, collections, math). Return a dict of named results. One ```python block."),
            f"Analyses: {state['analyses']}\nColumns: {json.dumps(prof.get('columns'), default=str)[:3000]}\n"
            f"Previous errors: {state.get('compute_errors')}", fallback=lambda: "", role="coder", min_tokens=1500)
        blocks = c.code_blocks(code_txt)
        if blocks:
            r = c.tool(ctx, "run_python", code=blocks[0], input={"rows": rows[:5000]}, timeout=60)
            if c.ok(r) and r.get("ok", True) and isinstance(r.get("output"), dict):
                computed.append({"name": "custom analysis", "value": r["output"]})
            else:
                errors.append(str(r.get("error") or r.get("stderr", ""))[-800:])
    return {"computed": computed, "compute_errors": errors, "repairs": state.get("repairs", 0) + (1 if errors else 0)}


def interpret(state: DataState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("interpret", "Interpreting results")
    comp = state.get("computed", [])

    def fb() -> str:
        if not comp:
            return ("I couldn't find structured data. Provide CSV/JSON or lines like `topic: solved/total`, "
                    "or attach a file in /workspace.")
        lines = [f"**Dataset**: {state['profile'].get('rows', 0)} rows; columns: "
                 f"{', '.join(state['profile'].get('columns', {}).keys())}"]
        for item in comp:
            lines.append(f"- **{item['name']}**: {json.dumps(item['value'], default=str)}")
        return "\n".join(lines)

    text = c.ask_text(ctx, "Explain what the results mean for the user's question: key insights, then concrete "
                           "recommendations as a bullet list. Markdown.",
                      f"Question: {c.instruction(state)}\nResults: {json.dumps(comp, default=str)[:10000]}",
                      fallback=fb)
    return {"insights": text}


def finalize(state: DataState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    comp = state.get("computed", [])
    recs = c.bullets(state["insights"], 6)
    if not recs:
        low = next((x for x in comp if x["name"].startswith("lowest")), None)
        if low:
            recs = [f"Focus on {list(v.values())[0]}" for v in low["value"]]
    rows = state.get("profile", {}).get("rows", 0)
    status = "success" if comp else "partial"
    return {"report": ctx.report(f"Analysed {rows} row(s); {len(comp)} result(s)", result=state["insights"],
                                 status=status, recommendations=recs, errors=state.get("compute_errors", []),
                                 artifacts=[{"kind": "table", "title": x["name"], "content": x["value"]} for x in comp][:8])}


def after_compute(state: DataState) -> str:
    if state.get("compute_errors") and state.get("repairs", 0) < state.get("max_repairs", MAX_REPAIRS):
        return "compute"
    return "interpret"


@archetype("data", "Data analyst: ingest → profile → plan analyses → compute (sandboxed) ⇄ repair → interpret")
def build(spec):
    g = StateGraph(DataState)
    for n, fn in [("ingest", ingest), ("profile", profile), ("plan_analyses", plan_analyses), ("compute", compute),
                  ("interpret", interpret), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "profile")
    g.add_edge("profile", "plan_analyses")
    g.add_edge("plan_analyses", "compute")
    g.add_conditional_edges("compute", after_compute, ["compute", "interpret"])
    g.add_edge("interpret", "finalize")
    g.add_edge("finalize", END)
    return g
