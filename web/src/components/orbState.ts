// Maps live run activity (events, graph nodes, tools, agents) → a thinking-orbs animation state.
// One place for the whole mapping so every indicator in the app tells the same story.
import type { OrbState } from "thinking-orbs";
import type { RunEvent } from "../api";
import type { AgentStep } from "./runModel";

export interface OrbSpec {
  state: OrbState;
  speed: number;
  label: string;
}

const LABEL: Record<OrbState, string> = {
  solving: "Planning…",
  searching: "Searching…",
  connecting: "Delegating to agents…",
  working: "Working…",
  weaving: "Reviewing…",
  composing: "Writing the answer…",
  shaping: "Building a new agent…",
  breathing: "Idle",
  listening: "Listening…",
};

const spec = (state: OrbState, label?: string, speed = 1): OrbSpec => ({ state, speed, label: label || LABEL[state] });

/** Waiting for the user's approval — slow, calm breathing. */
export const WAITING: OrbSpec = spec("breathing", "Waiting for your approval…", 0.6);
export const IDLE: OrbSpec = spec("breathing", "Idle", 1);

const NODE_RULES: [RegExp, OrbState][] = [
  [/^(classify_intent|analyze_capabilities|plan_dag|refine_plan|risk_gate|plan|intake|decompose|hypothesize|parse_problem|pick_algorithm|goal|constraints|schedule_task|manage_agents|assess_level|plan_analyses|design_tests|start|decide|gate)$/, "solving"],
  [/^(discover_agents|load_context|explore|repo_map|gather|survey|extract|ingest|profile|collect_code|discover|reproduce|investigate|observe|prepare)$/, "searching"],
  [/^(dispatch|run_step|delegate|merge)$/, "connecting"],
  [/^(implement|verify|diagnose|fix|compute|repair|act|logic|write_tests|run|solve|brute|stress_test|complexity|finalize|confirm|analyze)$/, "working"],
  [/^(review|collect|self_review|critique|reflect|security_review|static_checks|rank|validate|check_quiz|compare)$/, "weaving"],
  [/^(synthesize|respond|remember|draft|outline|explain|examples|quiz|feedback|interpret|propose|risks|suggest_build)$/, "composing"],
];

const BUILDER_NODES = /^(understand_spec|inspect_registry|select_components|design|generate_prompts|generate_code|generate_tests|static_validation|run_tests|debug|security_review|approval_gate|register|smoke_run|report)$/;

const SEARCH_TOOLS = /^(web_search|web_fetch|search_code|memory_recall|fs_read|fs_list|browser_snapshot|skill_read|git_status|git_diff|git_log|desktop_tree)$/;
const WORK_TOOLS = /^(run_command|run_python|fs_write|fs_patch|git_commit|git_branch|custom_logic|data_profile|data_query|browser_open|browser_act|browser_screenshot|desktop_act|desktop_screenshot|memory_remember)$/;

export function orbForTool(name: string): OrbSpec {
  if (SEARCH_TOOLS.test(name)) return spec("searching", `Using ${name}…`);
  if (WORK_TOOLS.test(name)) return spec("working", name === "run_command" ? "Running a command…" : `Using ${name}…`);
  return spec("working", `Using ${name}…`);
}

export function orbForNode(agentId: string, node: string): OrbSpec {
  const n = (node || "").trim();
  if (agentId === "agent_builder" && BUILDER_NODES.test(n)) return spec("shaping", `Builder · ${n.replace(/_/g, " ")}…`);
  for (const [re, st] of NODE_RULES) if (re.test(n)) return spec(st, `${n.replace(/_/g, " ")}…`);
  return orbForAgentId(agentId);
}

export function orbForAgentId(agentId: string): OrbSpec {
  const id = agentId || "";
  if (id === "agent_builder") return spec("shaping");
  if (/research|document|summar|improve/.test(id)) return spec("searching", "Researching…");
  if (/writ|teacher|curriculum|question/.test(id)) return spec("composing");
  if (/review|testing/.test(id)) return spec("weaving");
  if (/plann/.test(id)) return spec("solving");
  if (id === "main_agent" || id === "main") return spec("solving");
  return spec("working");
}

/** State for one running agent row: latest running tool > latest node > agent kind. */
export function orbForAgent(a: AgentStep): OrbSpec {
  const tool = [...a.tools].reverse().find((t) => t.status === "running");
  if (tool) return orbForTool(tool.name);
  const node = a.nodes[a.nodes.length - 1];
  if (node) return orbForNode(a.agent_id, node.node);
  return orbForAgentId(a.agent_id);
}

/** Overall state of a run from its event stream (newest relevant event wins). */
export function orbFromEvents(events: RunEvent[], running = true): OrbSpec {
  if (!running) return IDLE;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    switch (e.type) {
      case "approval":
        if (e.status === "waiting") return WAITING;
        continue;
      case "token":
        return spec("composing");
      case "tool":
        if (e.status === "running") return orbForTool(e.message);
        continue;
      case "node":
        return orbForNode(e.agent_id, e.data?.node || e.message);
      case "agent":
        if (e.status === "running") {
          if (e.data?.parent_agent) return e.agent_id === "agent_builder" ? spec("shaping") : spec("connecting");
          return orbForAgentId(e.agent_id);
        }
        continue;
      case "plan":
        return spec("solving");
      case "build":
        return spec("shaping");
      default:
        continue;
    }
  }
  return spec("solving", "Thinking…");
}
