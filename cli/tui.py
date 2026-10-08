"""Textual TUI: agents (left) · chat with live agent steps (center) · approvals (right). Gateway HTTP API only."""
from __future__ import annotations

import time

from rich.markdown import Markdown
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, RichLog, Static

from .api import Gateway, GatewayError, describe_event

TYPE_BADGE = {"prebuilt": "🟢", "custom": "🔵", "system": "🟣", "user": "🟠", "imported": "⚪"}


class ApprovalItem(ListItem):
    def __init__(self, approval: dict) -> None:
        self.approval = approval
        super().__init__(Label(f"⏸ {approval.get('title', '')[:60]}\n   [dim]{approval.get('agent_id', '')}[/]"))


class AgentItem(ListItem):
    def __init__(self, agent: dict) -> None:
        self.agent = agent
        super().__init__(Label(f"{TYPE_BADGE.get(agent.get('type'), '•')} {agent.get('icon', '')} {agent.get('name')}"))


class ApprovalList(ListView):
    BINDINGS = [Binding("a", "decide('approve')", "Approve"), Binding("d", "decide('deny')", "Deny")]

    def action_decide(self, decision: str) -> None:
        item = self.highlighted_child
        if isinstance(item, ApprovalItem):
            self.app.decide(item.approval["id"], decision)  # type: ignore[attr-defined]


class AgentOSApp(App):
    TITLE = "AgentOS"
    SUB_TITLE = "personal agent platform"
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #agents { width: 30; border: round $primary-darken-2; }
    #center { width: 1fr; }
    #chat { height: 1fr; border: round $primary-darken-2; padding: 0 1; }
    #thinking { height: 1; padding: 0 2; color: $text-muted; }
    #prompt { margin: 0 1; }
    #side { width: 38; border: round $warning-darken-2; }
    #status { height: 1; background: $boost; padding: 0 1; color: $text-muted; }
    .title { padding: 0 1; text-style: bold; }
    """
    BINDINGS = [Binding("ctrl+q", "quit", "Quit"), Binding("ctrl+r", "refresh", "Refresh"),
                Binding("ctrl+l", "clear", "Clear chat"), Binding("ctrl+n", "new_chat", "New chat")]

    def __init__(self) -> None:
        super().__init__()
        self.gw = Gateway()
        self.conversation_id: str | None = None
        self.busy_since: float | None = None
        self.busy_label = "main_agent"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="agents"):
                yield Static("Agent Library", classes="title")
                yield ListView(id="agent_list")
            with Vertical(id="center"):
                yield RichLog(id="chat", wrap=True, markup=False, highlight=False)
                yield Static("", id="thinking")
                yield Input(placeholder="Ask the Main Agent…  (Enter to send)", id="prompt")
            with Vertical(id="side"):
                yield Static("Approvals  [a] approve  [d] deny", classes="title")
                yield ApprovalList(id="approvals")
        yield Static("connecting…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self.chat_log.write(Text("Welcome to AgentOS. Ask anything — the Main Agent plans and delegates to agents.",
                                 style="dim"))
        self.action_refresh()
        self.set_interval(4, self.poll_approvals)
        self.set_interval(15, self.poll_health)
        self.set_interval(0.35, self.pulse)

    @property
    def chat_log(self) -> RichLog:
        return self.query_one("#chat", RichLog)

    # -- periodic --------------------------------------------------------------------------------
    def pulse(self) -> None:
        try:
            w = self.query_one("#thinking", Static)
        except Exception:  # noqa: BLE001 - the timer can fire while the screen is starting up / shutting down
            return
        if self.busy_since is None:
            w.update("")
            return
        frames = ["●", "◉", "○", "◉"]
        f = frames[int((time.time() - self.busy_since) * 3) % 4]
        w.update(f"{f} {self.busy_label} working… {int(time.time() - self.busy_since)}s")

    def action_refresh(self) -> None:
        self.load_agents()
        self.poll_health()
        self.poll_approvals()

    def action_clear(self) -> None:
        self.chat_log.clear()

    def action_new_chat(self) -> None:
        self.conversation_id = None
        self.chat_log.clear()
        self.chat_log.write(Text("New conversation.", style="dim"))

    @work(thread=True, exclusive=True, group="agents")
    def load_agents(self) -> None:
        try:
            agents = self.gw.agents()
        except GatewayError as e:
            self.call_from_thread(self.set_status, f"✗ {e}")
            return
        self.call_from_thread(self._fill_agents, agents)

    def _fill_agents(self, agents: list[dict]) -> None:
        lv = self.query_one("#agent_list", ListView)
        lv.clear()
        for a in sorted(agents, key=lambda a: (a.get("type") != "system", a.get("category", ""), a.get("name", ""))):
            lv.append(AgentItem(a))

    @work(thread=True, exclusive=True, group="health")
    def poll_health(self) -> None:
        try:
            h = self.gw.health()
            llm = h.get("llm") or {}
            svc = " ".join(f"{k}:{'✓' if v else '✗'}" for k, v in (h.get("services") or {}).items())
            ident = h.get("identity") or {}
            soul = self._active_soul()
            msg = (f"● {ident.get('emoji', '')} {ident.get('name', 'AgentOS')} · soul {soul} · "
                   f"llm {llm.get('mode')} {llm.get('model') or ''} · {svc}")
            if ident.get("name"):
                self.call_from_thread(setattr, self, "title", ident["name"])
        except GatewayError as e:
            msg = f"✗ {e}"
        self.call_from_thread(self.set_status, msg)

    def _active_soul(self) -> str:
        """This conversation's soul, else the default soul (shown in the status bar)."""
        try:
            if self.conversation_id:
                return self.gw.get(f"/api/conversations/{self.conversation_id}").get("soul") or "default"
            return next((s["id"] for s in self.gw.get("/api/souls") if s.get("default")), "default")
        except Exception:  # noqa: BLE001 - older gateway without souls
            return "default"

    def set_status(self, msg: str) -> None:
        self.query_one("#status", Static).update(msg)

    @work(thread=True, exclusive=True, group="approvals")
    def poll_approvals(self) -> None:
        try:
            items = self.gw.approvals("pending")
        except GatewayError:
            return
        self.call_from_thread(self._fill_approvals, items)

    def _fill_approvals(self, items: list[dict]) -> None:
        lv = self.query_one("#approvals", ApprovalList)
        current = [c.approval["id"] for c in lv.children if isinstance(c, ApprovalItem)]
        if current == [a["id"] for a in items]:
            return
        lv.clear()
        for a in items:
            lv.append(ApprovalItem(a))
        if items:
            self.bell()

    @work(thread=True)
    def decide(self, approval_id: str, decision: str) -> None:
        try:
            self.gw.decide(approval_id, decision)
            self.call_from_thread(self.chat_log.write, Text(f"{'✓ approved' if decision == 'approve' else '✗ denied'} "
                                                            f"{approval_id}", style="yellow"))
        except GatewayError as e:
            self.call_from_thread(self.set_status, f"✗ {e}")
        self.poll_approvals()

    # -- agent detail ------------------------------------------------------------------------------
    @on(ListView.Selected, "#agent_list")
    def show_agent(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, AgentItem):
            return
        a = event.item.agent
        caps = ", ".join(a.get("capabilities", [])[:10])
        tools = ", ".join(a.get("tools", [])[:10])
        self.chat_log.write(Text.from_markup(
            f"\n[bold]{a.get('icon', '')} {a.get('name')}[/]  [dim]{a.get('type')} · v{a.get('version')} · "
            f"{a.get('status')}[/]\n{a.get('description', '')}\n[dim]capabilities:[/] {caps}\n[dim]tools:[/] {tools}\n"))

    # -- chat --------------------------------------------------------------------------------------
    @on(Input.Submitted, "#prompt")
    def send(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        if not msg:
            return
        if self.busy_since is not None:
            self.set_status("A run is in progress — wait for it to finish.")
            return
        event.input.value = ""
        self.chat_log.write(Text(f"\nYou: {msg}", style="bold"))
        self.busy_since = time.time()
        self.busy_label = "main_agent"
        self.run_chat(msg)

    @work(thread=True, exclusive=True, group="chat")
    def run_chat(self, msg: str) -> None:
        final, tokens = "", []
        try:
            res = self.gw.chat(msg, self.conversation_id)
            self.conversation_id = res.get("conversation_id") or self.conversation_id
            for ev in self.gw.stream_events(res["run_id"]):
                if ev.get("agent_id"):
                    self.busy_label = ev["agent_id"]
                if ev.get("type") == "token":
                    tokens.append((ev.get("data") or {}).get("text", ""))
                    continue
                if ev.get("type") == "final":
                    final = (ev.get("data") or {}).get("text") or ev.get("message") or ""
                    break
                line = describe_event(ev)
                if line:
                    self.call_from_thread(self.chat_log.write, Text(line[1], style=line[0]))
                if ev.get("type") == "approval":
                    self.poll_approvals()
        except GatewayError as e:
            self.call_from_thread(self.chat_log.write, Text(f"✗ {e}", style="red"))
        finally:
            self.busy_since = None
        final = final or "".join(tokens)
        if final:
            self.call_from_thread(self.chat_log.write, Text("\nMain Agent:", style="bold cyan"))
            self.call_from_thread(self.chat_log.write, Markdown(final))
        self.load_agents()  # the Builder may have created a new agent


if __name__ == "__main__":
    AgentOSApp().run()
