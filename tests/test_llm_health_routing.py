"""Regressions found by the live test run (docs/TEST_RESULTS.md): 2.3 pinned model, 2.4 health, 2.5 key warning."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_lh_"))

import httpx  # noqa: E402
import openai  # noqa: E402

from aap import routing, settings  # noqa: E402
from aap.llm import LLM  # noqa: E402


def test_pinned_unlisted_model_is_kept():
    settings.update({"role_models": {"main": "my/unlisted-model"}})
    try:
        ch = routing.chain("main", 2, known=["agy/gemini-3.7-flash-medium", "zc/glm-5.2"])
        assert ch[0] == "my/unlisted-model"
        assert "zc/glm-5.2" in ch                       # failover candidates still present
    finally:
        settings.update({"role_models": {"main": ""}})


def test_batch_budget_scales_with_items():
    base = routing.budget_for("r_plain", 2)
    b = routing.set_batch_budget("r_batch", 2, 5)
    assert b > base and routing.budget_for("r_batch", 2) == b
    routing._run_tokens["r_batch"] = base + 1           # over the plain budget...
    assert not routing.over_budget("r_batch", 2)        # ...but still inside the batch budget


class _Models:
    def __init__(self, exc=None):
        self.exc = exc

    def list(self):
        if self.exc:
            raise self.exc
        return type("R", (), {"data": [type("M", (), {"id": "x/model"})()]})()


class _Client:
    def __init__(self, exc=None):
        self.models = _Models(exc)


def _llm(exc=None) -> LLM:
    l = LLM()
    l._get_client = lambda: _Client(exc)       # noqa: E731
    return l


def test_health_reports_offline_when_endpoint_unreachable():
    settings.update({"llm_provider": "auto"})
    try:
        req = httpx.Request("GET", "http://127.0.0.1:9/v1/models")
        h = _llm(openai.APIConnectionError(request=req)).health()
        assert h["mode"] == "mock" and h["error"]
    finally:
        settings.update({"llm_provider": "mock"})


def test_health_auth_error_on_models_keeps_online_with_error():
    settings.update({"llm_provider": "auto"})
    try:
        req = httpx.Request("GET", "http://x/v1/models")
        err = openai.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)
        h = _llm(err).health()
        assert h["mode"] == "openai" and "AuthenticationError" in h["error"]
    finally:
        settings.update({"llm_provider": "mock"})
