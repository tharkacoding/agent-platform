"""LLM access layer (any OpenAI-compatible endpoint: OmniRoute, OpenAI, Ollama, LM Studio, ...).

Every call site passes a deterministic `fallback`, so the platform still works offline (mock mode)
and degrades gracefully when the endpoint is down or replies with garbage.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Callable, Optional

from . import settings

log = logging.getLogger("aap.llm")


class LLM:
    def __init__(self) -> None:
        self._client = None
        self._client_key: tuple = ()
        self._models: list[str] = []
        self._down_until = 0.0
        self.last_error = ""
        self.calls = 0
        self.fallbacks = 0
        self.tokens = 0
        self.last_model = ""
        self._probe_at = 0.0
        self._probe_err = ""
        self._cache: dict[int, tuple[float, str]] = {}
        self._lock = threading.Lock()

    # ---- connection ----------------------------------------------------
    def _cfg(self) -> tuple:
        return (settings.get("llm_base_url"), settings.get("llm_api_key") or "none", float(settings.get("llm_timeout") or 120))

    def _get_client(self):
        cfg = self._cfg()
        with self._lock:
            if self._client is None or cfg != self._client_key:
                from openai import OpenAI
                from . import tracing
                self._client = tracing.wrap_client(OpenAI(base_url=cfg[0], api_key=cfg[1], timeout=cfg[2], max_retries=0))
                self._client_key, self._models, self._down_until = cfg, [], 0.0
            return self._client

    def reset(self) -> None:
        with self._lock:
            self._client, self._models, self._down_until, self.last_error = None, [], 0.0, ""

    def list_models(self) -> list[str]:
        ids = sorted(m.id for m in self._get_client().models.list().data)
        self._models = ids
        return ids

    def _known(self) -> list[str]:
        if not self._models:
            try:
                self.list_models()
            except Exception:  # noqa: BLE001 - unknown list: don't filter
                return []
        return self._models

    def chain(self, role: str = "main") -> list[str]:
        """Models to try for this call, routed by role + current seriousness tier (aap/routing.py)."""
        from . import routing
        c = routing.chain(role, known=self._known())
        if not c:
            known = self._known()
            if not known:
                raise RuntimeError("endpoint lists no models; choose one in Settings")
            c = [known[0]]
        return c

    def model(self, role: str = "main") -> str:
        return self.chain(role)[0]

    def _create(self, role: str, exclude: tuple = (), **kw):
        """chat.completions.create with dynamic model routing + cross-provider failover on 429/5xx/unknown model."""
        from openai import APIConnectionError, APIStatusError, APITimeoutError
        from . import routing
        last: Exception | None = None
        tried: list[str] = []
        sweeps = 0
        while True:
            sweeps += 1
            primary = [x for x in self.chain(role) if x not in exclude and x not in tried][:8]   # healthy first
            # the whole chain failed (rate limits / a provider outage): keep going with EVERY other known model
            # before giving up — the owner saw "none of my AI models are answering" with dozens still configured
            cands = primary or routing.fallback_pool(list(exclude) + tried, self._known())
            if not cands:
                break
            for m in cands:
                tried.append(m)
                r = self._try_model(m, kw, role)
                if isinstance(r, tuple):
                    return r
                last = r or last
            if sweeps >= 3 or not (last and getattr(last, "status_code", 0) in (429, 500, 502, 503, 504, 529)):
                break
            if not primary and sweeps == 2:      # everything tried once: one short wait for the earliest recovery
                time.sleep(min(15.0, max(2.0, routing.earliest_recovery())))
                tried = []
        raise last or RuntimeError("no model available")

    def _try_model(self, m: str, kw: dict, role: str):
        """One model: (response, model) on success, else the exception (after parking the model)."""
        from openai import APIConnectionError, APIStatusError, APITimeoutError
        from . import routing
        args = dict(kw)
        if m in routing.NO_TEMPERATURE:
            args.pop("temperature", None)
        try:
            self.calls += 1
            try:
                r = self._get_client().chat.completions.create(model=m, **args)
            except APIStatusError as e:
                # some models (e.g. newest Claude) reject `temperature`: retry the SAME model without it
                if e.status_code == 400 and "temperature" in str(e) and "temperature" in args:
                    routing.NO_TEMPERATURE.add(m)
                    args.pop("temperature", None)
                    r = self._get_client().chat.completions.create(model=m, **args)
                else:
                    raise
            self.last_model = m
            routing.mark_ok(m)
            return r, m
        except APITimeoutError as e:                      # this model hangs: park it, try the next one
            routing.cool_down(m, 300, "timeout")
            log.warning("model %s timed out — switching model", m)
            return e
        except APIConnectionError:
            raise                                         # endpoint itself is down → caller falls back
        except APIStatusError as e:
            routing.cool_down(m, routing.retry_after(e), f"{e.status_code}: {e}")
            log.warning("model %s failed (%s) — switching model", m, e.status_code)
            return e

    @property
    def mode(self) -> str:
        if settings.get("llm_provider") == "mock":
            return "mock"
        return "mock" if time.time() < self._down_until else "openai"

    def health(self) -> dict:
        info = {"mode": self.mode, "base_url": settings.get("llm_base_url"), "model": settings.get("llm_model") or None,
                "error": self.last_error, "calls": self.calls, "fallbacks": self.fallbacks, "tokens": self.tokens}
        if settings.get("llm_provider") != "mock":
            from openai import APIConnectionError, APITimeoutError
            try:
                if time.time() - self._probe_at > 15:      # really reach the endpoint (cached: the UI polls health)
                    self._probe_at = time.time()
                    try:
                        self.list_models()
                        self._probe_err = ""
                    except (APIConnectionError, APITimeoutError):
                        raise
                    except Exception as e:  # noqa: BLE001 - e.g. 401 on /models while completions still work
                        self._probe_err = f"{type(e).__name__}: {e}"[:200]
                elif self._down_until > time.time():
                    raise RuntimeError(self.last_error or "endpoint down")
                info["model"] = self.model()
                self._down_until, self.last_error = 0.0, ""
                info.update(mode="openai", error=self._probe_err)
            except Exception as e:  # noqa: BLE001
                self._probe_at = 0.0
                self._mark_down(e)
                info.update(mode="mock", error=self.last_error)
        return info

    def test(self) -> dict:
        """Used by Settings -> 'Test connection': one tiny real completion."""
        self.reset()
        try:
            t = time.time()
            r = self._get_client().chat.completions.create(
                model=self.model(), messages=[{"role": "user", "content": "Reply with the single word: pong"}],
                max_tokens=16)
            out = {"ok": True, "model": self.model(), "reply": (r.choices[0].message.content or "").strip(),
                   "latency_s": round(time.time() - t, 2), "models": self._models}
            # some gateways (e.g. OmniRoute) don't check the key on completions — /models does: surface it
            try:
                out["models"] = self.list_models()
            except Exception as e:  # noqa: BLE001
                out["warning"] = (f"Completions work, but the endpoint rejected the API key for /models "
                                  f"({type(e).__name__}: {e})"[:300])
            return out
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"[:400]}

    def _mark_down(self, e: Exception) -> None:
        self.last_error = f"{type(e).__name__}: {e}"[:300]
        self._down_until = time.time() + 30
        log.warning("LLM unavailable, using offline heuristics for 30s: %s", self.last_error)

    # ---- calls ---------------------------------------------------------
    def complete(self, system: str, user: str, fallback: Callable[[], str], role: str = "main",
                 max_tokens: Optional[int] = None) -> str:
        _check_cancel()
        if self.mode == "mock":
            return fallback()
        from . import routing
        rid = routing._run.get()
        if rid and routing.over_budget(rid, routing.run_tier(rid)):   # HARD cap per user request
            log.warning("run %s hit its token budget — no more model calls", rid)
            self.fallbacks += 1
            return fallback()
        try:
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            text = ""
            for _ in range(3):   # a reply cut off by its length limit is CONTINUED, never silently truncated
                mt = routing.clamp_max_tokens(max_tokens)   # the user's own token limit always wins
                kw = {"max_tokens": mt} if mt else {}
                r, used = self._create(role, messages=msgs, temperature=0.2, **kw)
                u = getattr(r, "usage", None)
                n = (u.total_tokens or 0) if u else 0
                self.tokens += n
                part = r.choices[0].message.content or ""
                routing.record(used, n, (getattr(u, "completion_tokens", 0) or 0) if u else len(part) // 4)
                text += part
                if getattr(r.choices[0], "finish_reason", "") != "length" or not part:
                    break
                if routing.cap_left() == 0:    # stopped by the user's limit: do not continue
                    break
                log.info("reply hit max_tokens — asking the model to continue")
                msgs = msgs + [{"role": "assistant", "content": part},
                               {"role": "user", "content": "Continue exactly where you stopped. Do not repeat."}]
            text = text.strip()
            if text:
                return text
        except Exception as e:  # noqa: BLE001
            self._fail(e)
        self.fallbacks += 1
        return fallback()

    def _fail(self, e: Exception) -> None:
        """Only an unreachable endpoint switches the platform to offline mode; a model/provider error (every model
        of this call failed) just falls back for THIS call."""
        from openai import APIConnectionError
        from . import routing
        routing.note_all_failed()
        if isinstance(e, APIConnectionError):
            self._mark_down(e)
        else:
            self.last_error = f"{type(e).__name__}: {e}"[:300]
            log.warning("all models failed for this call: %s", self.last_error)

    def stream(self, system: str, user: str, on_token: Callable[[str], None], fallback: Callable[[], str],
               role: str = "main", max_tokens: Optional[int] = None) -> str:
        """Streaming completion; calls on_token for each chunk. Returns the full text."""
        _check_cancel()
        if self.mode == "mock":
            text = fallback()
            for i in range(0, len(text), 40):
                on_token(text[i:i + 40])
            return text
        parts: list[str] = []
        from . import routing
        max_tokens = routing.clamp_max_tokens(max_tokens)   # the user's own token limit always wins
        if routing.cap_left() == 0:
            text = fallback()
            on_token(text)
            return text
        tried: list[str] = []
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for _attempt in range(4):   # a model that errors MID-STREAM (gateways send 200 then a 429) → next model
            used = ""
            try:
                stream, used = self._create(role, exclude=tuple(tried), stream=True, temperature=0.3,
                                            **({"max_tokens": max_tokens} if max_tokens else {}), messages=msgs)
                finish = ""
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                        parts.append(chunk.choices[0].delta.content)
                        on_token(parts[-1])
                    if chunk.choices and getattr(chunk.choices[0], "finish_reason", None):
                        finish = chunk.choices[0].finish_reason
            except Exception as e:  # noqa: BLE001
                from openai import APIConnectionError
                if parts:            # partial stream: keep what we have
                    break
                if isinstance(e, APIConnectionError):
                    self._mark_down(e)
                    break
                if used:
                    routing.cool_down(used, routing.retry_after(e), str(e))
                    tried.append(used)
                    log.warning("model %s failed mid-stream — switching model", used)
                    continue
                self._fail(e)
                break
            out = "".join(parts)
            est = max(1, len(out) // 4)   # streams carry no usage: estimate, never above the requested limit
            routing.record(used, est + len(system + user) // 4, min(est, max_tokens) if max_tokens else est)
            routing.mark_ok(used)
            if not parts:            # empty reply (e.g. a reasoning model spent a tiny limit thinking) → next model
                tried.append(used)
                continue
            if finish == "length" and routing.cap_left() is None:   # cut off → continue, don't truncate
                more = self.complete(system, user + "\n\nYou already wrote:\n" + out[-6000:]
                                     + "\n\nContinue exactly where it stopped. Do not repeat.",
                                     lambda: "", role=role, max_tokens=max_tokens)
                if more:
                    on_token(more)
                    parts.append(more)
            break
        if parts:
            return "".join(parts)
        self.fallbacks += 1
        text = fallback()
        on_token(text)
        return text

    def vision_json(self, system: str, user: str, png_b64: str, fallback: Callable[[], dict],
                    max_tokens: int = 500) -> dict:
        """Ask a vision model about a screenshot; returns parsed JSON (or fallback). The image is downscaled first."""
        if self.mode == "mock" or not png_b64:
            return fallback()
        from . import routing
        try:
            png_b64 = _shrink_png(png_b64)
            msgs = [{"role": "system", "content": system},
                    {"role": "user", "content": [{"type": "text", "text": user},
                                                 {"type": "image_url",
                                                  "image_url": {"url": "data:image/png;base64," + png_b64}}]}]
            r, used = self._create("vision", messages=msgs, max_tokens=max_tokens, temperature=0)
            u = getattr(r, "usage", None)
            text = r.choices[0].message.content or ""
            routing.record(used, (u.total_tokens or 0) if u else 0, (u.completion_tokens or 0) if u else len(text) // 4)
            data = _extract_json(text)
            if isinstance(data, dict):
                return data
        except Exception as e:  # noqa: BLE001
            self._fail(e)
        return fallback()

    def json(self, system: str, user: str, fallback: Callable[[], dict],
             validate: Optional[Callable[[dict], bool]] = None, role: str = "main",
             max_tokens: Optional[int] = None) -> dict:
        """Ask for a JSON object; fall back if the model is down, replies with non-JSON, or fails `validate`."""
        _check_cancel()
        if self.mode == "mock":
            return fallback()
        sys2 = system + "\n\nRespond with ONE valid JSON object only. No markdown fences, no commentary."
        from . import routing
        key = hash((role, routing.current_tier(), sys2, user, max_tokens))
        hit = self._cache.get(key)
        if hit and hit[0] > time.time():           # token saver: identical structured calls within 10 min
            return json.loads(hit[1])
        text = self.complete(sys2, user, lambda: "", role=role, max_tokens=max_tokens)
        try:
            data = _extract_json(text)
        except Exception:  # noqa: BLE001
            data = None
        if data is None and text and max_tokens:   # likely cut off: one retry with much more room
            text = self.complete(sys2, user, lambda: "", role=role, max_tokens=max_tokens * 3)
        try:
            data = _extract_json(text)
            if isinstance(data, dict) and (validate is None or validate(data)):
                if len(self._cache) > 500:
                    self._cache.clear()
                self._cache[key] = (time.time() + 600, json.dumps(data))
                return data
            log.warning("LLM JSON rejected, using fallback. Raw: %.200s", text)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM JSON parse error: %s", e)
        self.fallbacks += 1
        return fallback()


    def embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        """Embeddings via the endpoint's /v1/embeddings (role 'embed'). None if unavailable."""
        model = settings.model_for("embed") if (settings.get("role_models") or {}).get("embed") else ""
        if self.mode == "mock" or not model:
            return None
        try:
            r = self._get_client().embeddings.create(model=model, input=texts)
            return [d.embedding for d in r.data]
        except Exception as e:  # noqa: BLE001
            log.info("embeddings unavailable: %s", e)
            return None


def chat_model(role: str = "worker", override: Optional[str] = None, temperature: float = 0.2):
    """LangChain ChatOpenAI on the configured endpoint (used for tool-calling ReAct loops)."""
    from langchain_openai import ChatOpenAI
    base, key, timeout = llm._cfg()
    from . import routing
    models = [override] if override else llm.chain(role)[:5]

    def make(m: str, retries: int):
        kw = {} if m in routing.NO_TEMPERATURE else {"temperature": temperature}
        return ChatOpenAI(model=m, base_url=base, api_key=key or "none", timeout=timeout, max_retries=retries, **kw)
    primary = make(models[0], 0 if len(models) > 1 else 1)
    # cross-provider failover for tool-calling loops too (bind_tools is applied to every fallback)
    return _FailoverChat(primary, [make(m, 0) for m in models[1:]])


class _FailoverChat:
    """Minimal wrapper: bind_tools on all models, invoke tries them in order."""

    def __init__(self, primary, alts):
        self.primary, self.alts = primary, alts

    def bind_tools(self, tools, **kw):
        return _FailoverChat(self.primary.bind_tools(tools, **kw), [a.bind_tools(tools, **kw) for a in self.alts])

    def invoke(self, *a, **kw):
        from . import routing
        rid = routing._run.get()
        if rid and routing.cap_left(rid) == 0:      # the user's own token limit is used up: end the tool loop
            from langchain_core.messages import AIMessage
            return AIMessage(content="(Stopped: the token limit you set for this request is used up.)")
        err = None
        for m in [self.primary, *self.alts]:
            try:
                out = m.invoke(*a, **kw)
                um = getattr(out, "usage_metadata", None) or {}
                name = getattr(m, "model_name", None) or getattr(getattr(m, "bound", None), "model_name", "?")
                routing.record(str(name), int(um.get("total_tokens", 0) or 0), int(um.get("output_tokens", 0) or 0))
                routing.mark_ok(str(name))
                return out
            except Exception as e:  # noqa: BLE001
                err = e
                name = getattr(m, "model_name", None) or getattr(getattr(m, "bound", None), "model_name", "?")
                if "temperature" in str(e):
                    routing.NO_TEMPERATURE.add(str(name))   # next chat_model() builds it without temperature
                routing.cool_down(str(name), routing.retry_after(e), str(e))
        raise err

    def __getattr__(self, item):
        return getattr(self.primary, item)


def _shrink_png(b64: str, max_w: int = 1280) -> str:
    """Downscale a screenshot for vision calls (fewer tokens, faster); unchanged if PIL is missing."""
    try:
        import base64 as _b
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(_b.b64decode(b64)))
        if im.width > max_w:
            im = im.resize((max_w, int(im.height * max_w / im.width)))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "PNG", optimize=True)
        return _b.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return b64


def _extract_json(text: str):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    return None


llm = LLM()


def _check_cancel() -> None:
    """A cancelled run makes no further model calls (raises events.RunCancelled; never swallowed by fallbacks)."""
    from . import events, routing
    rid = routing._run.get()
    r = events.get(rid) if rid else None
    if r is not None:
        r.check_cancel()
