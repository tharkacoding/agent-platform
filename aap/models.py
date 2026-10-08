"""Shared data models: agent manifests, tasks, reports, plans. This file is a CONTRACT shared by all modules."""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

AgentType = Literal["prebuilt", "custom", "system", "user", "imported"]
AgentStatus = Literal["active", "pending_approval", "draft", "disabled", "failed"]


class FsGrant(BaseModel):
    path: str                     # container path prefix, e.g. /workspace, /shared, /mounts/myproj
    mode: Literal["ro", "rw"] = "ro"


class Permissions(BaseModel):
    fs: list[FsGrant] = Field(default_factory=lambda: [FsGrant(path="/workspace", mode="rw")])
    network: list[str] = Field(default_factory=list)    # allowed domains ("*" = any, still via egress proxy)
    shell: bool = False
    browser: Literal["none", "sandbox", "host"] = "none"
    desktop: bool = False


class AgentSpec(BaseModel):
    """= manifest.yaml. Stored in the registry; the UI renders it."""
    id: str
    name: str
    type: AgentType = "prebuilt"
    category: str = "General"
    icon: str = "🤖"
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)       # tool names (see aap/tools/__init__.py TOOLS)
    uses: list[str] = Field(default_factory=list)        # agent ids this agent may delegate to
    skills: list[str] = Field(default_factory=list)      # skill ids (SKILL.md format)
    permissions: Permissions = Field(default_factory=Permissions)
    graph: str = "generic"                               # archetype key, or "custom" -> location/graph.py
    system_prompt: str = ""
    soul: str = ""                                       # default soul id for this agent's user-facing text
    config: dict[str, Any] = Field(default_factory=dict) # archetype-specific knobs (e.g. stack, test_cmd)
    models: dict[str, str] = Field(default_factory=dict) # role overrides, e.g. {"main": "gpt-x"}
    status: AgentStatus = "active"
    version: str = "1.0"
    created_by: str = "platform"
    location: str = ""                                   # folder with code for custom agents
    workflow: list[str] = Field(default_factory=list)    # filled at runtime from the compiled graph


class AgentTask(BaseModel):
    task_id: str
    agent_id: str
    instruction: str
    context: list[dict] = Field(default_factory=list)    # upstream Report dicts
    files: list[str] = Field(default_factory=list)       # paths the task refers to
    run_id: str = ""
    parent_agent: str = ""
    depth: int = 0
    effort: int = -1                                     # seriousness tier 0-4 (aap/core/effort.py); -1 = auto


class Report(BaseModel):
    agent_id: str
    task_id: str
    status: Literal["success", "partial", "failed"] = "success"
    summary: str = ""
    result: Any = ""
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    artifacts: list[dict] = Field(default_factory=list)  # {kind: diff|image|file|table|link, title, content|path}
    tool_calls: list[dict] = Field(default_factory=list)
    delegated_to: list[str] = Field(default_factory=list)
    duration_s: float = 0.0
    tokens: int = 0


class PlanStep(BaseModel):
    id: str
    agent_id: str
    instruction: str
    depends_on: list[str] = Field(default_factory=list)
    expected_output: str = ""
    role: str = ""          # for agent_id == "spawn": system prompt of a temporary on-the-fly worker
    est_tokens: int = 0     # planner estimate for this step (used to spread the budget evenly)
    effort: int = -1        # per-step effort after budget allocation (-1 = the request tier)
    name: str = ""


class MissingCapability(BaseModel):
    name: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    suggested_uses: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    intent: str = ""
    required_capabilities: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    missing: Optional[MissingCapability] = None
    direct_answer: Optional[str] = None
