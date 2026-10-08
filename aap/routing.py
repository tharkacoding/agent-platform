"""Dynamic model routing: pick a model chain from (role, seriousness tier), fail over on quota/errors.

* The seriousness tier of the running task (core/effort.py) is carried in a contextvar set by the runtime, so every
  LLM call made while an agent runs is routed by that tier — trivial work → cheap/fast models, deep/critical work →
  strong reasoning/coding models. LangGraph copies contextvars into its worker threads.
* Chains deliberately mix providers (Gemini, GPT, GLM, DeepSeek, Grok, Kimi, Claude...) so one provider's quota
  running out (HTTP 429) or an outage never stops the platform: the failing model is cooled down for its
  `retry_after` and the next model in the chain is tried within the same call.
* Users can override chains in Settings → `routing.chains` (same keys as DEFAULT_CHAINS), or pin a model per role
  in `role_models` (a pinned model is tried first).
"""
from __future__ import annotations

import contextvars
import re
import threading
import time

from . import settings

_tier: contextvars.ContextVar[int] = contextvars.ContextVar("aap_tier", default=2)
_run: contextvars.ContextVar[str] = contextvars.ContextVar("aap_run", default="")
_run_tokens: dict[str, int] = {}
# Hard token budget per user request (whole run, all agents) by seriousness tier
RUN_BUDGET = [3_000, 12_000, 40_000, 120_000, 250_000]


_run_tier: dict[str, int] = {}   # run_id -> tier of the user's request (the budget is per request)


def set_run(run_id: str, tier: int | None = None):
    if tier is not None and run_id not in _run_tier:
        _run_tier[run_id] = max(0, min(4, int(tier)))   # first agent of a run = the top-level request
    return _run.set(run_id)


def run_tier(run_id: str) -> int:
    return _run_tier.get(run_id, current_tier())


def reset_run(token) -> None:
    _run.reset(token)


def run_tokens(run_id: str = "") -> int:
    return _run_tokens.get(run_id or _run.get(), 0)


_run_budget: dict[str, int] = {}     # per-run override (batch requests: budget scales with the number of items)
PER_ITEM_BUDGET = [2_000, 6_000, 15_000, 30_000, 50_000]


def set_batch_budget(run_id: str, tier: int, items: int) -> int:
    t = max(0, min(4, tier))
    b = max(RUN_BUDGET[t], items * PER_ITEM_BUDGET[t] + RUN_BUDGET[t] // 2)
    _run_budget[run_id] = min(b, 400_000)
    return _run_budget[run_id]


def budget_for(run_id: str, tier: int) -> int:
    return _run_budget.get(run_id) or RUN_BUDGET[max(0, min(4, tier))]


_user_cap: dict[str, int] = {}   # run_id -> token limit the USER gave for this request ("use only 500 tokens")
_run_out: dict[str, int] = {}    # run_id -> output (completion) tokens generated so far

_CAP_RE = re.compile(
    r"(?i)(?:\b(?:only|just|max(?:imum)?|at most|within|under|below|limit(?:ed)?(?: it)?(?: to| of| at)?|budget(?: of)?|"
    r"use|using|spend|up ?to|no more than|less than|cap(?:ped)?(?: at| of)?)\s+(?:of\s+|about\s+)?"
    r"(\d[\d,]*(?:\.\d+)?)\s*(k|m)?\s*tokens?\b)|(?:\b(\d[\d,]*(?:\.\d+)?)\s*(k|m)?\s*tokens?\s+"
    r"(?:only|max(?:imum)?|limit|budget|or less|at most)\b)")


def parse_user_cap(text: str) -> int:
    """'use only 10 tokens' / 'within 2k tokens' / '500 tokens max' → 10 / 2000 / 500. 0 = no limit given."""
    m = _CAP_RE.search(text or "")
    if not m:
        return 0
    num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    try:
        n = float(num.replace(",", ""))
    except ValueError:
        return 0
    n *= {"k": 1_000, "m": 1_000_000}.get((unit or "").lower(), 1)
    return max(1, int(n))


def set_user_cap(run_id: str, n: int) -> None:
    if run_id and n > 0:
        _user_cap[run_id] = int(n)


def user_cap(run_id: str = "") -> int:
    return _user_cap.get(run_id or _run.get(), 0)


def cap_left(run_id: str = "") -> int | None:
    """Output tokens still allowed under the user's own limit (None = the user gave no limit)."""
    rid = run_id or _run.get()
    if rid not in _user_cap:
        return None
    return max(0, _user_cap[rid] - _run_out.get(rid, 0))


def clamp_max_tokens(max_tokens: int | None, run_id: str = "") -> int | None:
    left = cap_left(run_id)
    if left is None:
        return max_tokens
    return max(1, min(max_tokens or left, left))


def run_out_tokens(run_id: str = "") -> int:
    return _run_out.get(run_id or _run.get(), 0)


def over_budget(run_id: str, tier: int) -> bool:
    """A limit the USER gave ("use only 10 tokens") is always obeyed (counted in generated tokens).
    Otherwise token caps NEVER stop requested work unless the owner turns enforcement on (Settings →
    token_budget_mode = 'enforce'). Default 'off': tiers only choose cheaper/stronger models."""
    if run_id in _user_cap:
        return _run_out.get(run_id, 0) >= _user_cap[run_id]
    if (settings.get("token_budget_mode") or "off") != "enforce":
        return False
    return run_tokens(run_id) >= budget_for(run_id, tier)

# band = cheap (tiers 0-1) | standard (2) | deep (3) | critical (4); group = general | coding | vision
DEFAULT_CHAINS: dict[str, list[str]] = {
    # Concrete models only — WE decide (OmniRoute's auto/* routes are never used unless the user pins one).
    # Non-Claude first: Claude subscriptions are shared with the owner's coding sessions and hit quota often.
    "general:cheap": ["agy/gemini-3.7-flash-low", "zc/glm-4.7-flash", "cxa/gpt-5.5-low", "dva/deepseek-v4-flash-low",
                      "agy/gemini-3.5-flash-lite", "cc/claude-haiku-4-5-20251001"],
    "general:standard": ["agy/gemini-3.7-flash-medium", "zc/glm-5.2", "cxa/gpt-5.5-medium", "dva/deepseek-v4-pro-low",
                         "dva/kimi-k2-7", "cc/claude-sonnet-5-5"],
    "general:deep": ["cxa/gpt-5.6-sol-medium", "agy/gemini-3.1-pro-high", "zc/glm-5.3", "dva/kimi-k3-low",
                     "dva/deepseek-v4-pro-high", "cc/claude-sonnet-5-5-high"],
    "general:critical": ["cxa/gpt-6-sol-high", "agy/gemini-3.1-pro-high", "zc/glm-5.3-max", "dva/deepseek-v4-pro-max",
                         "cc/claude-opus-5-5"],
    "coding:cheap": ["agy/gemini-3.7-flash-low", "zc/glm-4.7-flash", "cxa/gpt-5.5-low", "dva/swe-1-7-lightning"],
    "coding:standard": ["cxa/gpt-5.5-medium", "dva/swe-1-7", "zc/glm-5.2", "agy/gemini-3.7-flash-high",
                        "dva/kimi-k2-7", "cc/claude-sonnet-5-5"],
    "coding:deep": ["cxa/gpt-5.6-sol-medium", "dva/swe-1-7-medium", "zc/glm-5.3", "dva/kimi-k3-low",
                    "agy/gemini-3.1-pro-high", "cc/claude-sonnet-5-5-high"],
    "coding:critical": ["cxa/gpt-6-sol-high", "zc/glm-5.3-max", "dva/deepseek-v4-pro-max", "agy/gemini-3.1-pro-high",
                        "cc/claude-opus-5-5"],
    "vision:any": ["agy/gemini-3.7-flash-medium", "cxa/gpt-5.5-medium", "zc/glm-4.6v", "agy/gemini-3.1-pro-low"],
}

RELIABLE_TAIL = ["agy/gemini-3.7-flash-medium", "antigravity/gemini-3.7-flash-tiered", "agy/gemini-3.7-flash-low",
                 "agy/claude-sonnet-4-6", "oc/deepseek-v4-flash-free"]

_cool: dict[str, float] = {}          # model -> cooled-down until
_stats: dict[str, dict] = {}          # model -> {calls, tokens, errors, last_error}
_lock = threading.Lock()


def set_tier(level: int):
    return _tier.set(max(0, min(4, int(level))))


def reset_tier(token) -> None:
    _tier.reset(token)


def current_tier() -> int:
    return _tier.get()


def _band(t: int) -> str:
    return "cheap" if t <= 1 else ("standard" if t == 2 else ("deep" if t == 3 else "critical"))


def _group(role: str) -> str:
    return "coding" if role in ("coder", "builder") else ("vision" if role == "vision" else "general")


def enabled() -> bool:
    r = settings.get("routing") or {}
    return r.get("enabled", True) if isinstance(r, dict) else True


_run_model: dict[str, str] = {}   # run_id -> model the user picked in the chat box for that message


def set_run_model(run_id: str, model: str) -> None:
    _run_model[run_id] = model


def chain(role: str = "main", tier: int | None = None, known: list[str] | None = None) -> list[str]:
    """Ordered candidate models for this call. `known` = models the endpoint lists (unknown ones are skipped)."""
    t = current_tier() if tier is None else tier
    pinned = _run_model.get(_run.get(), "") or (settings.get("role_models") or {}).get(role) or ""
    if not enabled():
        base = [pinned or settings.get("llm_model") or ""]
    else:
        g = _group(role)
        key = f"{g}:any" if g == "vision" else f"{g}:{_band(t)}"
        custom = ((settings.get("routing") or {}).get("chains") or {}) if isinstance(settings.get("routing"), dict) else {}
        base = list(custom.get(key) or DEFAULT_CHAINS.get(key) or DEFAULT_CHAINS["general:standard"])
        if pinned:
            base.insert(0, pinned)
        default = settings.get("llm_model")
        if default:
            base.append(default)
        # our router decides: drop the gateway's auto/* routes unless the user explicitly pinned that one
        base = [m for m in base if not m.startswith("auto/") or m == pinned]
        base += RELIABLE_TAIL   # always end with models from other providers so a chain can't be all-broken
    seen, out = set(), []
    for m in base:
        # a model the user pinned is always tried (endpoints don't always list every model); failover handles errors
        if m and m not in seen and (not known or m in known or (m == pinned and pinned)):
            seen.add(m)
            out.append(m)
    _load_health()
    now = time.time()
    ready = [m for m in out if _cool.get(m, 0) <= now]
    if pinned and pinned in ready:
        ready.remove(pinned)
    # learned health: models that answered recently first, untried next, models that keep failing last
    # (stable sort keeps the tier's intended order inside each group)
    ready.sort(key=lambda m: 0 if now - _ok_at.get(m, 0) < 3600 else (2 if _fails.get(m) else 1))
    if pinned and _cool.get(pinned, 0) <= now:
        ready.insert(0, pinned)      # the user's choice always goes first
    cooling = sorted((m for m in out if _cool.get(m, 0) > now), key=lambda m: _cool[m])
    return ready + cooling  # if everything is cooling, still try the one that recovers first


def earliest_recovery() -> float:
    """Seconds until the first parked model may be tried again (0 when some model is ready)."""
    now = time.time()
    waits = [c - now for c in _cool.values() if c > now]
    return min(waits) if waits else 0.0


def fallback_pool(exclude: list[str], known: list[str] | None = None, limit: int = 16) -> list[str]:
    """Models OUTSIDE this call's chain, for when the whole chain failed: every model in the other role/tier chains,
    then whatever the gateway lists (never the gateway's auto/* routes). Healthy ones first."""
    pool: list[str] = []
    for chain_ in DEFAULT_CHAINS.values():
        pool += chain_
    pool += list(known or [])
    seen = set(exclude)
    out = []
    for m in pool:
        if m and m not in seen and not m.startswith("auto/") and (not known or m in known):
            seen.add(m)
            out.append(m)
    _load_health()
    now = time.time()
    ready = [m for m in out if _cool.get(m, 0) <= now]
    ready.sort(key=lambda m: 0 if now - _ok_at.get(m, 0) < 3600 else (2 if _fails.get(m) else 1))
    return ready[:limit]


_fails: dict[str, int] = {}     # model -> consecutive failures (cool-downs escalate: x2 each time)
_ok_at: dict[str, float] = {}   # model -> last time it answered
_health_loaded = False


def _health_file():
    from . import config
    return config.DATA_DIR / "model_health.json"


def _load_health() -> None:
    global _health_loaded
    if _health_loaded:
        return
    _health_loaded = True
    try:
        import json
        d = json.loads(_health_file().read_text(encoding="utf-8"))
        now = time.time()
        for m, h in d.items():
            if h.get("cool", 0) > now:
                _cool[m] = h["cool"]
            _fails[m] = int(h.get("fails", 0))
            if h.get("ok_at"):
                _ok_at[m] = h["ok_at"]
    except Exception:  # noqa: BLE001
        pass


def _save_health() -> None:
    try:
        import json
        ms = set(_cool) | set(_fails) | set(_ok_at)
        _health_file().write_text(json.dumps({m: {"cool": _cool.get(m, 0), "fails": _fails.get(m, 0),
                                                  "ok_at": _ok_at.get(m, 0)} for m in ms}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def cool_down(model: str, seconds: float, error: str = "") -> None:
    """Park a failing model. Repeated failures escalate (x2 each, up to 12 h for a misconfigured provider) and are
    remembered across restarts, so dead providers stop costing 10-60 s on every call."""
    _load_health()
    with _lock:
        n = _fails.get(model, 0) + 1
        _fails[model] = n
        cap = 43200 if seconds >= 1800 else 7200
        _cool[model] = time.time() + max(5.0, min(seconds * (2 ** min(n - 1, 6)), cap))
        s = _stats.setdefault(model, {"calls": 0, "tokens": 0, "errors": 0, "last_error": ""})
        s["errors"] += 1
        s["last_error"] = error[:200]
    _save_health()


_run_llm: dict[str, list[int]] = {}   # run_id -> [successful model calls, calls where EVERY model failed]


def note_all_failed() -> None:
    rid = _run.get()
    if rid:
        with _lock:
            _run_llm.setdefault(rid, [0, 0])[1] += 1


def models_down(run_id: str = "") -> bool:
    """This run's model calls have ALL failed so far (provider outage / rate limits) — nothing it says is real."""
    ok, bad = _run_llm.get(run_id or _run.get(), [0, 0])
    return bad > 0 and ok == 0


def record(model: str, tokens: int, out: int = 0) -> None:
    with _lock:
        s = _stats.setdefault(model, {"calls": 0, "tokens": 0, "errors": 0, "last_error": ""})
        s["calls"] += 1
        s["tokens"] += tokens
        rid = _run.get()
        if rid:
            _run_llm.setdefault(rid, [0, 0])[0] += 1
            _run_tokens[rid] = _run_tokens.get(rid, 0) + tokens
            _run_out[rid] = _run_out.get(rid, 0) + (out or 0)


def stats() -> dict:
    now = time.time()
    with _lock:
        return {"tier": current_tier(), "enabled": enabled(),
                "models": {m: dict(s, cooling_s=max(0, round(_cool.get(m, 0) - now))) for m, s in _stats.items()},
                "chains": DEFAULT_CHAINS}


def retry_after(err: Exception) -> float:
    """Seconds to cool a model down after an error (honours retry_after / 'reset after Nm')."""
    import re
    text = str(err)
    m = re.search(r"retry_after['\"]?\s*[:=]\s*(\d+)", text) or re.search(r"reset after (\d+)\s*m", text)
    if m:
        v = float(m.group(1))
        return v * 60 if "reset after" in m.group(0) else v
    status = getattr(err, "status_code", 0) or 0
    # provider misconfigured on the gateway (not a temporary blip): park it for 30 min so it stops wasting calls
    if re.search(r"(?i)not configured|ENOENT|must be an absolute path|no such file|not installed|unauthori[sz]ed|"
                 r"invalid api key|model not found|does not exist|unknown model", text):
        return 1800
    return 120 if status == 429 else (600 if status in (400, 404) else 90)


NO_TEMPERATURE: set[str] = set()   # models that reject the `temperature` parameter (learned at runtime)


def mark_ok(model: str) -> None:
    """A model answered: clear any cool-down so it moves back to its normal place in the chain."""
    with _lock:
        _cool.pop(model, None)
        changed = _fails.pop(model, None) is not None or time.time() - _ok_at.get(model, 0) > 600
        _ok_at[model] = time.time()
    if changed:
        _save_health()
