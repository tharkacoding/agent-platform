"""`agentos` — host CLI for the AgentOS platform (docs/CONTRACTS.md, plan §11)."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from . import __version__, mounts
from .api import Gateway, GatewayError, describe_event
from .paths import GATEWAY_URL, SHARED_DIR, load_config, repo_dir, save_config

app = typer.Typer(name="agentos", help="AgentOS — your personal AI agent platform (sandboxed in Docker).",
                  no_args_is_help=True, add_completion=False, rich_markup_mode="rich")
mount_app = typer.Typer(help="Share host folders with the sandbox as /mounts/<name>.", no_args_is_help=True)
agents_app = typer.Typer(help="Inspect the agent registry.", no_args_is_help=True)
skills_app = typer.Typer(help="Import OpenClaw/Hermes-style skills (SKILL.md).", no_args_is_help=True)
bridge_app = typer.Typer(help="Host bridge: desktop control + your real Chrome (off by default).", no_args_is_help=True)
app.add_typer(mount_app, name="mount")
app.add_typer(agents_app, name="agents")
app.add_typer(skills_app, name="skills")
app.add_typer(bridge_app, name="bridge")
souls_app = typer.Typer(help="Souls (personalities) + OpenClaw/Hermes persona files.", no_args_is_help=True)
app.add_typer(souls_app, name="souls")

console = Console()
OK, BAD, WARN = "[green]✓[/]", "[red]✗[/]", "[yellow]![/]"


# ------------------------------------------------------------------------------------------------ helpers
def _run(cmd: list[str], capture: bool = False, timeout: float | None = None) -> subprocess.CompletedProcess:
    kw = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"} if capture else {}
    return subprocess.run(cmd, timeout=timeout, **kw)


def _have(exe: str) -> bool:
    return shutil.which(exe) is not None


def _docker_ok() -> tuple[bool, str]:
    if not _have("docker"):
        return False, "docker CLI not found (install Docker Desktop)"
    try:
        r = _run(["docker", "info", "--format", "{{.ServerVersion}}"], capture=True, timeout=20)
    except subprocess.TimeoutExpired:
        return False, "docker info timed out (is Docker Desktop starting?)"
    if r.returncode != 0:
        return False, "Docker engine not running — start Docker Desktop"
    return True, f"engine {r.stdout.strip()}"


def _compose_cmd(*args: str) -> list[str]:
    repo = repo_dir()
    cmd = ["docker", "compose", "--project-directory", str(repo), "-f", str(repo / "docker-compose.yml")]
    override = mounts.render_override(repo)
    if override:
        cmd += ["-f", str(override)]
    return cmd + list(args)


def _compose(*args: str) -> int:
    ok, why = _docker_ok()
    if not ok:
        console.print(f"{BAD} {why}")
        raise typer.Exit(1)
    if not (repo_dir() / "docker-compose.yml").exists():
        console.print(f"{BAD} docker-compose.yml not found in {repo_dir()} — run `agentos setup` from the repo folder")
        raise typer.Exit(1)
    return _run(_compose_cmd(*args)).returncode


def _gw() -> Gateway:
    return Gateway()


def _fail(e: Exception) -> None:
    console.print(f"{BAD} {e}")
    raise typer.Exit(1)


def _wait_health(timeout: float = 120) -> Optional[dict]:
    gw, end = _gw(), time.time() + timeout
    with console.status("waiting for the gateway…"):
        while time.time() < end:
            try:
                return gw.health()
            except GatewayError:
                time.sleep(2)
    return None


def _port_state(port: int) -> str:
    """'free' | 'ours' | 'busy'"""
    with socket.socket() as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return "free"
    if port == 7860:
        try:
            if httpx.get(f"{GATEWAY_URL}/api/health", timeout=15).status_code == 200:
                return "ours"
        except httpx.HTTPError:
            pass
        return "busy"
    if port == 6080:
        try:
            if "novnc" in httpx.get("http://127.0.0.1:6080/vnc.html", timeout=2).text.lower():
                return "ours"
        except httpx.HTTPError:
            pass
    return "busy"


# ------------------------------------------------------------------------------------------------ lifecycle
@app.command()
def version() -> None:
    """Print the CLI version."""
    console.print(f"agentos {__version__}")


@app.command()
def setup(start_now: bool = typer.Option(True, "--start/--no-start", help="Start the stack after building."),
          base_url: str = typer.Option("", help="LLM endpoint (OpenAI-compatible). Prompted if empty."),
          model: str = typer.Option("", help="Default model ('' = first model the endpoint lists)."),
          api_key: str = typer.Option("", help="API key for the endpoint (optional)."),
          yes: bool = typer.Option(False, "-y", help="Accept defaults without prompting.")) -> None:
    """Check WSL/Docker, create the shared folder, build images, configure the LLM endpoint."""
    repo = repo_dir()
    console.rule("[bold]AgentOS setup")
    wsl = _have("wsl") and _run(["wsl", "--status"], capture=True, timeout=30).returncode == 0
    console.print(f"{OK if wsl else BAD} WSL2 {'installed' if wsl else 'missing — run: wsl --install (admin), then reboot'}")
    ok, why = _docker_ok()
    console.print(f"{OK if ok else BAD} Docker: {why}")
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    console.print(f"{OK} shared folder: {SHARED_DIR}")
    if not (repo / "docker-compose.yml").exists():
        console.print(f"{BAD} no docker-compose.yml in {repo}. Run setup from the agent-platform folder "
                      "(or put agentos.exe there).")
        raise typer.Exit(1)
    save_config({"repo_dir": str(repo)})
    env_file = repo / ".env"
    lines = [ln for ln in (env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else [])
             if not ln.startswith("AOS_SHARED=")]
    lines.append(f"AOS_SHARED={SHARED_DIR}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"{OK} wrote {env_file}")

    if not base_url:
        base_url = "http://host.docker.internal:20128/v1" if yes else Prompt.ask(
            "LLM endpoint (OpenAI-compatible; OmniRoute default)", default="http://host.docker.internal:20128/v1")
    if not model and not yes:
        model = Prompt.ask("Default model (blank = first listed by the endpoint)", default="")
    if not api_key and not yes:
        api_key = Prompt.ask("API key (blank = none)", default="", password=True)
    save_config({"pending_settings": {"llm_base_url": base_url, "llm_model": model,
                                      **({"llm_api_key": api_key} if api_key else {})}})
    if not ok:
        console.print(f"{WARN} Docker isn't ready: start Docker Desktop, then run `agentos setup` again.")
        raise typer.Exit(1)
    console.print("[bold]Building images (first time takes a while)…")
    if _compose("build") != 0:
        _fail(RuntimeError("docker compose build failed"))
    if start_now:
        start()


@app.command()
def start(open_ui: bool = typer.Option(False, "--open", help="Open the web UI when ready.")) -> None:
    """Start the stack (docker compose up -d) and wait until the gateway is healthy."""
    if _compose("up", "-d") != 0:
        _fail(RuntimeError("docker compose up failed"))
    h = _wait_health()
    if not h:
        _fail(RuntimeError("gateway did not become healthy in time — see `agentos logs gateway`"))
    pending = load_config().get("pending_settings")
    if pending:
        try:
            _gw().put("/api/settings", pending)
            save_config({"pending_settings": None})
            console.print(f"{OK} LLM settings applied")
        except GatewayError as e:
            console.print(f"{WARN} could not apply LLM settings: {e}")
    console.print(Panel.fit(f"[bold green]AgentOS is running[/]\nWeb UI: {GATEWAY_URL}\nTUI: agentos tui",
                            border_style="green"))
    if open_ui:
        webbrowser.open(GATEWAY_URL)


@app.command()
def stop() -> None:
    """Stop every container (kill switch)."""
    raise typer.Exit(_compose("down"))


@app.command()
def restart() -> None:
    """Restart the stack."""
    _compose("down")
    start()


@app.command()
def status() -> None:
    """Gateway health, sandbox services and the active LLM."""
    try:
        h = _gw().health()
    except GatewayError as e:
        _fail(e)
    t = Table(show_header=False, box=None)
    t.add_row("gateway", f"{OK} v{h.get('version', '?')}  {GATEWAY_URL}")
    for name, up in (h.get("services") or {}).items():
        t.add_row(name, OK if up else BAD)
    llm = h.get("llm") or {}
    t.add_row("llm", f"{llm.get('mode')}  {llm.get('model') or ''}  {llm.get('base_url') or ''}")
    if llm.get("error"):
        t.add_row("", f"[red]{llm['error']}[/]")
    console.print(t)


@app.command()
def logs(service: str = typer.Argument("", help="gateway | runner | browser | egress (default: all)"),
         follow: bool = typer.Option(False, "-f", "--follow")) -> None:
    """Show container logs."""
    args = ["logs", "--tail", "200"] + (["-f"] if follow else []) + ([service] if service else [])
    raise typer.Exit(_compose(*args))


@app.command()
def doctor() -> None:
    """Diagnose the environment (Docker, WSL, ports, OmniRoute, disk)."""
    rows: list[tuple[str, str, str]] = []
    wsl = _have("wsl")
    if wsl:
        try:
            wsl = _run(["wsl", "--status"], capture=True, timeout=20).returncode == 0
        except subprocess.TimeoutExpired:
            wsl = False
    rows.append(("WSL2", OK if wsl else BAD, "ok" if wsl else "run `wsl --install` as admin + reboot"))
    ok, why = _docker_ok()
    rows.append(("Docker", OK if ok else BAD, why))
    repo = repo_dir()
    has_compose = (repo / "docker-compose.yml").exists()
    rows.append(("Repo", OK if has_compose else BAD, str(repo)))
    for port, label in ((7860, "port 7860 (web UI)"), (6080, "port 6080 (noVNC)")):
        st = _port_state(port)
        rows.append((label, {"free": OK, "ours": OK, "busy": BAD}[st],
                     {"free": "free", "ours": "in use by AgentOS", "busy": "in use by another program"}[st]))
    try:
        r = httpx.get("http://localhost:20128/v1/models", timeout=4)
        n = len((r.json() or {}).get("data", [])) if r.status_code == 200 else 0
        rows.append(("OmniRoute", OK if r.status_code == 200 else WARN,
                     f"{n} models at :20128" if r.status_code == 200 else f"HTTP {r.status_code} (API key needed?)"))
    except (httpx.HTTPError, ValueError):
        rows.append(("OmniRoute", WARN, "not reachable at http://localhost:20128 (start it with `omniroute`, "
                                         "or set another endpoint in Settings)"))
    free_gb = shutil.disk_usage(Path.home().anchor).free / 1e9
    rows.append(("Disk", OK if free_gb > 15 else WARN, f"{free_gb:.0f} GB free (images need ~8 GB)"))
    try:
        h = _gw().health()
        rows.append(("Gateway", OK, f"running, llm={h.get('llm', {}).get('mode')}"))
    except GatewayError:
        rows.append(("Gateway", WARN, "not running (agentos start)"))
    rows.append(("Shared folder", OK if SHARED_DIR.exists() else WARN, str(SHARED_DIR)))
    t = Table(title="agentos doctor", show_lines=False)
    t.add_column("check")
    t.add_column("")
    t.add_column("detail")
    for r in rows:
        t.add_row(*r)
    console.print(t)


@app.command("open")
def open_ui() -> None:
    """Open the web UI in your browser."""
    webbrowser.open(GATEWAY_URL)
    console.print(GATEWAY_URL)


@app.command()
def tui() -> None:
    """Terminal UI: agents, chat with live steps, approvals."""
    from .tui import AgentOSApp
    AgentOSApp().run()


# ------------------------------------------------------------------------------------------------ chat
def stream_run(gw: Gateway, run_id: str) -> str:
    """Render a run's events live; returns the final markdown."""
    final, tokens = "", []
    frames = ["●", "◉", "○", "◉"]
    status_text = Text("● thinking…", style="bold")

    with Live(status_text, console=console, refresh_per_second=6, transient=True) as live:
        start_t = time.time()
        for ev in gw.stream_events(run_id):
            t = ev.get("type")
            if t == "token":
                tokens.append((ev.get("data") or {}).get("text", ""))
            elif t == "final":
                final = (ev.get("data") or {}).get("text") or ev.get("message") or "".join(tokens)
                break
            line = describe_event(ev)
            if line:
                style, text = line
                live.console.print(Text(text, style=style))
            frame = frames[int((time.time() - start_t) * 3) % len(frames)]
            label = ev.get("agent_id") or "main_agent"
            live.update(Text(f"{frame} {label} working…", style="bold"))
    return final or "".join(tokens)


@app.command()
def chat(message: str = typer.Argument(..., help="What you want done."),
         conversation: str = typer.Option("", "--conversation", "-c", help="Continue a conversation id.")) -> None:
    """Send a message to the Main Agent and stream its work."""
    gw = _gw()
    try:
        res = gw.chat(message, conversation or None)
        final = stream_run(gw, res["run_id"])
    except GatewayError as e:
        _fail(e)
    console.print(Panel(Markdown(final or "_(no answer)_"), title="Main Agent", border_style="cyan"))
    console.print(f"[dim]run {res['run_id']} · conversation {res.get('conversation_id', '')}[/]")


# ------------------------------------------------------------------------------------------------ mounts
@mount_app.command("add")
def mount_add(path: str, rw: bool = typer.Option(False, "--rw", help="Allow writes (default read-only)."),
              name: str = typer.Option("", "--name")) -> None:
    """Share a host folder as /mounts/<name> (read-only unless --rw). Restart to apply."""
    try:
        m = mounts.add(path, rw=rw, name=name or None)
    except ValueError as e:
        _fail(e)
    mounts.render_override(repo_dir())
    console.print(f"{OK} {m['host_path']} → {m['container_path']} ({m['mode']}). Run `agentos restart` to apply.")


@mount_app.command("list")
def mount_list() -> None:
    """List shared folders."""
    t = Table("name", "host path", "container path", "mode")
    t.add_row("shared", str(SHARED_DIR), "/shared", "rw")
    for m in mounts.load():
        t.add_row(m["name"], m["host_path"], m["container_path"], m["mode"])
    console.print(t)


@mount_app.command("remove")
def mount_remove(name: str) -> None:
    """Stop sharing a folder. Restart to apply."""
    if not mounts.remove(name):
        _fail(ValueError(f"no mount named {name}"))
    mounts.render_override(repo_dir())
    console.print(f"{OK} removed {name}. Run `agentos restart` to apply.")


# ------------------------------------------------------------------------------------------------ agents/skills/approvals
TYPE_BADGE = {"prebuilt": "🟢", "custom": "🔵", "system": "🟣", "user": "🟠", "imported": "⚪"}


@agents_app.command("list")
def agents_list(type_: str = typer.Option("", "--type")) -> None:
    """List agents in the registry."""
    try:
        items = _gw().get("/api/agents", **({"type": type_} if type_ else {}))
    except GatewayError as e:
        _fail(e)
    t = Table("", "id", "name", "category", "status", "ver", "runs")
    for a in items:
        st = a.get("stats") or {}
        t.add_row(TYPE_BADGE.get(a.get("type"), "•"), a["id"], f"{a.get('icon', '')} {a['name']}",
                  a.get("category", ""), a.get("status", ""), a.get("version", ""), str(st.get("runs", "")))
    console.print(t)


@skills_app.command("import")
def skills_import(source: str = typer.Argument(..., help="Local skill folder (with SKILL.md files) or a git URL.")) -> None:
    """Import skills. Local folders are copied into the shared folder so the sandbox can read them."""
    gw = _gw()
    if source.startswith(("http://", "https://", "git@")):
        body = {"git_url": source}
    else:
        src = Path(source).expanduser().resolve()
        if not src.is_dir():
            _fail(ValueError(f"not a folder: {src}"))
        dest = SHARED_DIR / "skills_import" / mounts.slug(src.name)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
        body = {"path": f"/shared/skills_import/{dest.name}"}
    try:
        res = gw.post("/api/skills/import", body)
    except GatewayError as e:
        _fail(e)
    console.print(f"{OK} imported: {', '.join(res.get('imported', [])) or '(nothing found)'}")


@souls_app.command("list")
def souls_list() -> None:
    """List souls (personalities); ★ marks the default for new conversations."""
    try:
        items = _gw().get("/api/souls")
        ident = _gw().get("/api/identity")
    except GatewayError as e:
        _fail(e)
    console.print(f"Assistant: {ident.get('emoji', '')} [bold]{ident.get('name', 'AgentOS')}[/] {ident.get('vibe', '')}")
    t = Table("", "id", "soul", "description", "")
    for s in items:
        t.add_row(s.get("icon", ""), s["id"], s["name"], (s.get("description") or "")[:70],
                  "★ default" if s.get("default") else ("imported" if s.get("source") else ""))
    console.print(t)


@souls_app.command("use")
def souls_use(soul_id: str = typer.Argument(..., help="Soul id, e.g. teacher")) -> None:
    """Make a soul the default for new conversations (in chat you can also type /soul <id>)."""
    try:
        s = _gw().put(f"/api/souls/{soul_id}", {"default": True})
    except GatewayError as e:
        _fail(e)
    console.print(f"{OK} default soul: {s.get('icon', '')} {s.get('name', soul_id)}")


@souls_app.command("import")
def souls_import(source: str = typer.Argument("", help="OpenClaw workspace or Hermes home folder "
                                                       "(default: ~/.openclaw/workspace, then ~/.hermes).")) -> None:
    """Import SOUL.md / USER.md / AGENTS.md / IDENTITY.md / TOOLS.md / HEARTBEAT.md (copied; originals untouched)."""
    cands = [Path(source).expanduser()] if source else [Path.home() / ".openclaw" / "workspace",
                                                          Path.home() / ".openclaw", Path.home() / ".hermes"]
    src = next((p.resolve() for p in cands if p.is_dir() and any((p / f).exists() for f in
                ("SOUL.md", "AGENTS.md", "USER.md", "IDENTITY.md"))), None)
    if src is None:
        _fail(ValueError("no SOUL.md/AGENTS.md/USER.md/IDENTITY.md found in " + ", ".join(map(str, cands))))
    gw = _gw()
    dest = SHARED_DIR / "souls_import" / mounts.slug(src.name.lstrip(".") or "souls")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for f in ("SOUL.md", "USER.md", "AGENTS.md", "IDENTITY.md", "TOOLS.md", "HEARTBEAT.md"):
        if (src / f).exists():
            shutil.copy2(src / f, dest / f)
    try:
        try:   # Docker gateway sees the shared folder as /shared
            res = gw.post("/api/souls/import", {"path": f"/shared/souls_import/{dest.name}"})
        except GatewayError:   # dev gateway runs on the host: give it the host path
            res = gw.post("/api/souls/import", {"path": str(dest)})
    except GatewayError as e:
        _fail(e)
    console.print(f"{OK} imported from {src} ({res.get('kind')}): soul={res.get('soul') or '—'} "
                  f"files={', '.join(res.get('files', [])) or '—'}")


@app.command()
def approvals(auto_list: bool = typer.Option(False, "--list", help="Only list, don't prompt.")) -> None:
    """Review pending approvals (approve / deny / skip)."""
    gw = _gw()
    try:
        items = gw.approvals("pending")
    except GatewayError as e:
        _fail(e)
    if not items:
        console.print("No pending approvals.")
        return
    for a in items:
        console.print(Panel(f"[bold]{a.get('title')}[/]\nagent: {a.get('agent_id')}  run: {a.get('run_id')}\n"
                            f"{_short_json(a.get('detail'))}", title=f"approval {a['id']}", border_style="yellow"))
        if auto_list:
            continue
        choice = Prompt.ask("approve / deny / skip", choices=["a", "d", "s"], default="s")
        if choice in ("a", "d"):
            gw.decide(a["id"], "approve" if choice == "a" else "deny")
            console.print(f"{OK} {'approved' if choice == 'a' else 'denied'}")


def _short_json(v) -> str:
    import json
    s = json.dumps(v, indent=2, default=str) if v else ""
    return s if len(s) < 1200 else s[:1200] + "…"


# ------------------------------------------------------------------------------------------------ bridge
@bridge_app.command("start")
def bridge_start(desktop: bool = typer.Option(False, "--desktop", help="Enable real-desktop control."),
                 chrome: bool = typer.Option(False, "--chrome", help="Enable launching your Chrome for agents."),
                 port: int = typer.Option(8765, "--port"),
                 gateway: str = typer.Option(GATEWAY_URL, "--gateway", help="Gateway URL for approval checks.")) -> None:
    """Run the host bridge (127.0.0.1). Kill switch: Ctrl+Alt+Esc."""
    from bridge.server import serve
    serve(desktop=desktop, chrome=chrome, port=port, gateway_url=gateway)


@bridge_app.command("token")
def bridge_token(rotate: bool = typer.Option(False, "--rotate")) -> None:
    """Show (or rotate) the bridge token to paste into Settings → Bridge."""
    from bridge.security import load_or_create_token
    console.print(load_or_create_token(rotate=rotate))


def main() -> None:  # console_scripts entry
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    app()


if __name__ == "__main__":
    main()
