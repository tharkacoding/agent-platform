"""Data tools — pandas runs inside the runner sandbox."""
from ..clients import ServiceUnavailable, runner
from ..core.sdk import tool

_PROFILE = r'''
import io, json
import pandas as pd

def _load(src):
    if isinstance(src, str) and src.startswith("/") and "\n" not in src:
        p = src.lower()
        if p.endswith((".xlsx", ".xls")): return pd.read_excel(src)
        if p.endswith(".json"): return pd.read_json(src)
        return pd.read_csv(src, sep=None, engine="python")
    s = str(src).strip()
    if s.startswith(("[", "{")): return pd.read_json(io.StringIO(s))
    return pd.read_csv(io.StringIO(s), sep=None, engine="python")

def run(inp):
    df = _load(inp["src"])
    num = df.select_dtypes("number")
    out = {"rows": int(len(df)), "columns": [{"name": c, "dtype": str(df[c].dtype),
            "nulls": int(df[c].isna().sum()), "unique": int(df[c].nunique())} for c in df.columns],
           "head": json.loads(df.head(8).to_json(orient="records")),
           "describe": json.loads(num.describe().to_json()) if not num.empty else {}}
    cats = [c for c in df.columns if df[c].dtype == object and df[c].nunique() <= 30]
    out["value_counts"] = {c: json.loads(df[c].value_counts().head(10).to_json()) for c in cats[:5]}
    if inp.get("code"):
        env = {"df": df, "pd": pd}
        exec(inp["code"], env)
        r = env.get("result")
        out["result"] = json.loads(r.to_json()) if hasattr(r, "to_json") else r
    return out
'''


@tool("data_profile", "Profile a dataset (CSV/JSON/XLSX path under /workspace or /shared, or raw CSV/JSON text).",
      {"path_or_text": {"type": "string"}})
def data_profile(ctx, path_or_text: str):
    try:
        return runner("POST", "/python", json={"code": _PROFILE, "input": {"src": path_or_text}, "timeout": 60,
                                                   "path_keys": ["src"]},
                      timeout=80)
    except ServiceUnavailable as e:
        return {"error": str(e)}


@tool("data_query", "Run a pandas snippet against a dataset loaded as `df`; assign the answer to `result`.",
      {"path": {"type": "string"}, "code": {"type": "string"}})
def data_query(ctx, path: str, code: str):
    try:
        return runner("POST", "/python", json={"code": _PROFILE, "input": {"src": path, "code": code}, "timeout": 90,
                                                   "path_keys": ["src"]},
                      timeout=110)
    except ServiceUnavailable as e:
        return {"error": str(e)}
