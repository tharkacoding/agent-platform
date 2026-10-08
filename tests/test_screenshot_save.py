"""Screenshots are saved to /shared/screenshots/<run_id>_*.png and reported with their path."""
import base64
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_shot_"))

from types import SimpleNamespace  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from aap.tools import desktop, shots  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def test_desktop_screenshot_saved_with_path(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("RUNNER_ROOTS", str(shared))
    monkeypatch.setenv("RUNNER_PATH_MAP", f"/shared={shared}")
    from runner import app as runner_app
    client = TestClient(runner_app.app)

    def fake_runner(method, path, json=None, **kw):
        return client.request(method, path, json=json).json()

    monkeypatch.setattr(shots, "runner", fake_runner)
    monkeypatch.setattr(desktop, "bridge", lambda *a, **k: PNG)
    ctx = SimpleNamespace(run=SimpleNamespace(run_id="run123"), files_created=[])
    out = desktop.desktop_screenshot(ctx)
    assert out["path"].startswith("/shared/screenshots/run123_screen_") and out["png_base64"]
    assert ctx.files_created == [out["path"]]
    saved = shared / "screenshots" / out["path"].rsplit("/", 1)[1]
    assert saved.read_bytes() == PNG
