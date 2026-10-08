"""Headless end-to-end UI tests (Playwright, Chromium) for every AgentOS page.

Runs against E2E_BASE_URL if set; otherwise starts its own gateway (mock LLM, temp data dir) on port 7862.
Requires `web/dist` (npm run build). Run:  python -m pytest tests/e2e -q
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

pw = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]
MOJIBAKE = re.compile(r"â[\x80-\xbf€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]|Ã[\x80-\xbf]|ï¸|Â[\xa0-\xbf]")


def _free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture(scope="session")
def base_url():
    if os.environ.get("E2E_BASE_URL"):
        yield os.environ["E2E_BASE_URL"].rstrip("/")
        return
    if not (ROOT / "web" / "dist" / "index.html").exists():
        pytest.skip("web/dist missing — run `npm run build` in web/")
    port = 7862
    assert _free(port), f"port {port} busy"
    env = dict(os.environ, AAP_DATA_DIR=tempfile.mkdtemp(prefix="aos_e2e_"), AAP_LLM_PROVIDER="mock",
               PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "aap.api.app:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            if httpx.get(url + "/api/health", timeout=5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("gateway did not start")
    yield url
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:   # background threads (scheduler, phone listener) can delay a graceful stop
        proc.kill()
        proc.wait(10)


@pytest.fixture(scope="session")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    ctx = browser.new_context(base_url=base_url, viewport={"width": 1280, "height": 860})
    pg = ctx.new_page()
    pg.errors = []  # type: ignore[attr-defined]
    pg.on("console", lambda m: m.type == "error" and pg.errors.append(m.text))
    pg.on("pageerror", lambda e: pg.errors.append(f"pageerror: {e}"))
    pg.on("dialog", lambda d: d.accept())  # confirm() on deletes
    yield pg
    text = pg.inner_text("body")
    bad = MOJIBAKE.findall(text)
    errs = [e for e in pg.errors if "Failed to load resource" not in e]
    ctx.close()
    assert not bad, f"mojibake in rendered text: {bad[:5]}"
    assert not errs, f"console errors: {errs[:5]}"


def main_text(pg) -> str:
    return pg.inner_text("main")


# ------------------------------------------------------------------------------------------------ chat
def test_chat_suggestion_runs_and_shows_steps_and_reports(page):
    page.goto("/")
    page.get_by_text("Analyze this programming data", exact=False).first.click()
    page.wait_for_selector("text=/Worked for/", timeout=30000)
    txt = main_text(page)
    assert "Data Analysis Agent" in txt
    assert "DP" in txt and "greedy" in txt          # offline parser understood "12 DP, 30 greedy"
    page.get_by_text("Worked for", exact=False).first.click()   # expand steps
    page.wait_for_timeout(500)
    assert re.search(r"Recalling|Understanding|Delegating", main_text(page))   # node progress is visible
    page.get_by_text(re.compile(r"Show \d+ report")).first.click()
    page.wait_for_timeout(300)
    assert re.search(r"success|partial", main_text(page), re.I)


# ------------------------------------------------------------------------------------------------ agents
def test_agent_library_filter_and_detail(page):
    page.goto("/agents")
    page.wait_for_selector("text=/agents in your organization/")
    n = int(re.search(r"(\d+) agents in your organization", main_text(page)).group(1))
    assert n >= 30
    page.get_by_role("button", name=re.compile("System")).click()
    page.wait_for_timeout(300)
    t = main_text(page)
    assert "Main Agent" in t and "Python Agent" not in t
    page.get_by_role("button", name="All").click()
    page.get_by_text("Coding Agent", exact=True).first.click()
    page.wait_for_url(re.compile("/agents/coding_agent"))
    page.get_by_role("button", name="Workflow").click()
    page.wait_for_selector(".react-flow__node", timeout=10000)
    assert page.locator(".react-flow__node").count() >= 8


def test_agent_try_it_completes(page):
    page.goto("/agents/writer_agent")
    page.get_by_role("button", name="Try it").click()
    page.get_by_placeholder("Give this agent a task…").fill("Write a two-line note about sorting")
    page.get_by_role("button", name="Run", exact=True).click()
    page.wait_for_selector("text=/run [0-9a-f]{12}/", timeout=10000)
    page.wait_for_function("() => /success|partial|failed|done/i.test(document.querySelector('main').innerText)",
                           timeout=60000)


# ------------------------------------------------------------------------------------------------ builder
def test_builder_creates_agent_that_appears_in_library(page, base_url):
    before = {a["id"] for a in httpx.get(base_url + "/api/agents").json()}
    page.goto("/builder")
    page.get_by_placeholder("Describe goals, inputs, outputs, data sources, schedule…").fill(
        "An agent that tracks my reading list progress and suggests what to read next")
    page.get_by_placeholder("e.g. Programming Learning Agent").fill("Reading Progress Agent")
    page.get_by_role("button", name="Build agent").click()
    page.wait_for_selector("text=/understand|spec/i", timeout=20000)
    deadline = time.time() + 120
    new = set()
    while time.time() < deadline and not new:
        new = {a["id"] for a in httpx.get(base_url + "/api/agents").json()} - before
        time.sleep(1)
    assert new, "builder did not register a new agent"
    page.goto("/agents")
    page.get_by_role("button", name=re.compile("Custom")).click()
    page.wait_for_timeout(500)
    assert "Reading" in main_text(page)


# ------------------------------------------------------------------------------------------------ runs
def test_runs_list_and_detail_tabs(page, base_url):
    rid = httpx.post(base_url + "/api/chat", json={"message": "summarize: agents are composable workers"}).json()["run_id"]
    for _ in range(60):
        if httpx.get(f"{base_url}/api/runs/{rid}").json()["status"] != "running":
            break
        time.sleep(0.5)
    page.goto("/runs")
    page.wait_for_selector(f"a[href='/runs/{rid}']", timeout=10000)
    page.goto(f"/runs/{rid}")
    for tab in ("reports", "events", "audit", "steps"):
        page.get_by_role("button", name=re.compile(tab, re.I)).first.click()
        page.wait_for_timeout(300)
    assert "Main Agent" in main_text(page) or "main_agent" in main_text(page)


def test_approvals_page(page):
    page.goto("/approvals")
    page.wait_for_selector("text=Approvals")
    assert "approval" in main_text(page).lower()


# ------------------------------------------------------------------------------------------------ memory / schedules
def test_memory_add_and_delete(page):
    page.goto("/memory")
    page.get_by_placeholder("Something the agents should remember…").fill("My favourite language is C++")
    page.get_by_role("button", name="Remember").click()
    page.wait_for_selector("text=My favourite language is C++")
    page.get_by_title("Forget").first.click()
    page.wait_for_selector("text=My favourite language is C++", state="detached", timeout=10000)


def test_schedule_create_run_delete(page, base_url):
    page.goto("/schedules")
    page.get_by_placeholder("Weekly learning plan").fill("E2E schedule")
    page.locator("[data-testid=when-every]").select_option("hour")
    page.get_by_placeholder("Analyze my progress this week and create next week's learning plan").fill("say hi")
    page.get_by_role("button", name="Add schedule").click()
    page.wait_for_selector("text=E2E schedule")
    # scope to our row: a built-in (disabled) "Heartbeat" schedule is listed first
    own = page.locator("text=E2E schedule").first.locator("xpath=ancestor::*[.//button[contains(., 'Run now')]][1]")
    own.get_by_role("button", name=re.compile("Run now")).first.click()
    deadline = time.time() + 60
    while time.time() < deadline and not httpx.get(base_url + "/api/feed").json():
        time.sleep(1)
    assert httpx.get(base_url + "/api/feed").json(), "scheduled run produced no feed card"
    page.goto("/feed")
    page.wait_for_selector("text=/E2E schedule/")
    page.goto("/schedules")
    page.wait_for_selector("text=E2E schedule")
    row = page.locator("text=E2E schedule").first.locator("xpath=ancestor::*[.//button][1]")
    row.locator("button").last.click()
    page.wait_for_selector("text=E2E schedule", state="detached", timeout=10000)


# ------------------------------------------------------------------------------------------------ skills
def test_skill_import_from_path(page, tmp_path):
    d = tmp_path / "pack" / "pdf-notes"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: pdf-notes\ndescription: Turn PDFs into study notes\n---\n# Steps\n",
                                encoding="utf-8")
    page.goto("/skills")
    page.get_by_placeholder("/mounts/skills").fill(str(tmp_path / "pack"))
    page.get_by_placeholder("/mounts/skills").locator("xpath=following-sibling::button[1]").click()
    page.wait_for_selector("text=pdf-notes", timeout=10000)
    assert "Turn PDFs into study notes" in main_text(page)


# ------------------------------------------------------------------------------------------------ settings
def test_settings_roundtrip_and_failed_test_connection(page, base_url):
    page.goto("/settings")
    page.wait_for_selector("text=Language model endpoint")
    page.wait_for_function("() => !/Current mode: \\?/.test(document.querySelector('main').innerText)", timeout=10000)
    base = page.locator("label:has-text('Base URL') input, input[value*='/v1']").first
    old = base.input_value()
    base.fill("http://127.0.0.1:9/v1")
    page.get_by_role("button", name="Save", exact=True).click()
    for _ in range(20):
        if httpx.get(base_url + "/api/settings").json()["llm_base_url"] == "http://127.0.0.1:9/v1":
            break
        time.sleep(0.25)
    assert httpx.get(base_url + "/api/settings").json()["llm_base_url"] == "http://127.0.0.1:9/v1"
    page.get_by_role("button", name="Test connection").click()
    page.wait_for_selector("text=/error|failed|refused|connect/i", timeout=30000)
    base.fill(old)
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_timeout(500)


# ------------------------------------------------------------------------------------------------ browser / feed
def test_browser_page_unavailable_gracefully(page):
    page.goto("/browser")
    page.wait_for_selector("text=Take over")
    assert "Sandbox" in main_text(page)


def test_home_feed(page):
    page.goto("/feed")
    page.wait_for_selector("text=Home")


# ------------------------------------------------------------------------------------------------ tools / auto mode
def test_tools_page_lists_tools(page, base_url):
    page.goto("/tools")
    page.wait_for_selector("[data-testid=tool-row]")
    assert page.locator("[data-testid=tool-row]").count() >= 30
    text = main_text(page)
    assert "Custom (created by agents)" in text and "Built-in" in text and "run_command" in text


def test_auto_mode_toggle_roundtrip(page, base_url):
    page.goto("/")
    label = page.locator("[data-testid=auto-mode-label]")
    label.wait_for()
    page.wait_for_function("() => !/…/.test(document.querySelector('[data-testid=auto-mode-label]').innerText)")
    before = httpx.get(base_url + "/api/settings").json().get("auto_mode", True)
    page.locator("[data-testid=auto-mode-toggle]").click()
    for _ in range(20):
        if httpx.get(base_url + "/api/settings").json().get("auto_mode") is (not before):
            break
        time.sleep(0.25)
    assert httpx.get(base_url + "/api/settings").json().get("auto_mode") is (not before)
    assert ("on" if not before else "off") in label.inner_text()
    page.locator("[data-testid=auto-mode-toggle]").click()          # restore
    for _ in range(20):
        if httpx.get(base_url + "/api/settings").json().get("auto_mode") is before:
            break
        time.sleep(0.25)
    assert httpx.get(base_url + "/api/settings").json().get("auto_mode") is before


def test_memory_scope_tabs(page, base_url):
    httpx.post(base_url + "/api/memory", json={"text": "e2e scoped note", "kind": "episode", "scope": "e2e_agent"})
    page.goto("/memory")
    page.wait_for_selector("[data-testid=memory-scope-tabs]")
    page.locator("[data-testid=memory-scope-tabs] button", has_text="e2e_agent").click()
    page.wait_for_selector("text=e2e scoped note")
    page.locator("[data-testid=memory-scope-tabs] button", has_text="Global").click()
    page.wait_for_timeout(500)
    assert "e2e scoped note" not in main_text(page)
    assert page.locator("[data-testid=experience-notes]").count() == 1


# ------------------------------------------------------------------------------------------------ tests page / VM browser
def test_tests_page_shows_suite_and_run_button(page, base_url):
    page.goto("/tests")
    page.wait_for_selector("text=Run all now")
    assert "Capability suite" in main_text(page)
    st = httpx.get(base_url + "/api/selftest", timeout=10).json()
    assert {"state", "summary", "rows"} <= set(st)
    sched = [s for s in httpx.get(base_url + "/api/schedules", timeout=10).json() if s["id"] in ("selftest", "daily_summary")]
    assert {s["id"] for s in sched} == {"selftest", "daily_summary"}


def test_browser_page_vm_card_when_configured(page, base_url):
    httpx.put(base_url + "/api/settings", json={"vm_browser_url": "http://127.0.0.1:9"}, timeout=10)   # nothing there
    try:
        page.goto("/browser")
        page.wait_for_selector("text=VM browser")
        assert "off" in main_text(page) and page.locator("text=Log in on the VM screen").is_disabled()
    finally:
        httpx.put(base_url + "/api/settings", json={"vm_browser_url": ""}, timeout=10)


def test_mascot_switch_in_settings(page, base_url):
    page.goto("/settings")
    box = page.locator("[data-testid=settings-mascot]")
    box.wait_for()
    assert box.is_checked()                                   # on by default
    box.uncheck()
    page.wait_for_timeout(600)
    assert httpx.get(base_url + "/api/settings", timeout=10).json().get("mascot_enabled") is False
    box.check()
    page.wait_for_timeout(600)
    assert httpx.get(base_url + "/api/settings", timeout=10).json().get("mascot_enabled") is True
    assert httpx.get(base_url + "/mascots/fox-directions.webp", timeout=10).status_code == 200   # sheets served


def test_schedule_picker_is_plain_english_and_builds_the_cron(page, base_url):
    page.goto("/schedules")
    page.get_by_placeholder("Weekly learning plan").fill("Picker schedule")
    page.get_by_placeholder("Analyze my progress this week and create next week's learning plan").fill("say hi")
    page.locator("[data-testid=when-every]").select_option("week")
    page.locator("[data-testid=when-time]").fill("18:30")
    page.locator("[data-testid=when-day-1]").click()      # Monday is preselected → now off
    page.locator("[data-testid=when-day-3]").click()      # Wednesday
    page.locator("[data-testid=when-day-5]").click()      # Friday
    assert "Every Wednesday and Friday at 6:30 PM" in page.locator("[data-testid=when-summary]").inner_text()
    page.get_by_role("button", name="Add schedule").click()
    page.wait_for_selector("text=Picker schedule")
    mine = [x for x in httpx.get(base_url + "/api/schedules", timeout=10).json() if x["name"] == "Picker schedule"][0]
    assert mine["cron"] == "30 18 * * 3,5"
    row = page.locator("text=Picker schedule").first.locator("xpath=ancestor::div[contains(@class,'card')][1]")
    assert "Every Wednesday and Friday at 6:30 PM" in row.inner_text() and "30 18" not in row.inner_text()
    # the built-in ones read as English too (no computer language on the page)
    assert "Every day at 3:00 AM" in page.inner_text("body") and "0 3 * * *" not in page.inner_text("body")
    httpx.delete(base_url + f"/api/schedules/{mine['id']}", timeout=10)
