// Derives a structured view (steps, agents, final answer) from a flat run event stream.
import type { Report, RunEvent } from "../api";

export interface ToolStep {
  key: string;
  name: string;
  status: string;
  args?: unknown;
  output?: unknown;
  duration_s?: number;
}

export interface AgentStep {
  kind: "agent";
  key: string;
  task_id: string;
  agent_id: string;
  parent_agent?: string;
  instruction?: string;
  status: string;
  started: number;
  ended?: number;
  nodes: { node: string; message: string; ts: number }[];
  tools: ToolStep[];
  logs: RunEvent[];
  report?: Report;
}

export type Step =
  | { kind: "node"; key: string; agent_id: string; node: string; message: string; status: string; ts: number }
  | { kind: "plan"; key: string; plan: any; message: string; ts: number }
  | { kind: "build"; key: string; agent_id: string; message: string; status: string; data: any; ts: number }
  | { kind: "approval"; key: string; message: string; status: string; data: any; ts: number }
  | { kind: "log"; key: string; agent_id: string; message: string; status: string; ts: number }
  | AgentStep;

export interface RunView {
  steps: Step[];
  agents: AgentStep[];
  reports: Report[];
  final: string;
  streaming: string;
  status: "running" | "done" | "failed";
  currentNode?: string;
  error?: string;
}

const MAIN_IDS = new Set(["main_agent", "main", ""]);

export function buildRunView(events: RunEvent[]): RunView {
  const steps: Step[] = [];
  const agents = new Map<string, AgentStep>();
  const reports: Report[] = [];
  let final = "";
  let streaming = "";
  let status: RunView["status"] = "running";
  let currentNode: string | undefined;
  let error: string | undefined;

  const agentFor = (e: RunEvent): AgentStep | undefined => {
    const tid = e.data?.task_id;
    if (tid && agents.has(tid)) return agents.get(tid);
    return undefined;
  };

  for (const e of events) {
    const d = e.data || {};
    switch (e.type) {
      case "agent": {
        const tid = d.task_id || `${e.agent_id}-${e.seq}`;
        let a = agents.get(tid);
        if (!a) {
          if (MAIN_IDS.has(e.agent_id) && !d.parent_agent) {
            // the main agent's own bracket; only status matters
            if (e.status === "failed") error = e.message;
            break;
          }
          a = {
            kind: "agent",
            key: "a-" + tid,
            task_id: tid,
            agent_id: e.agent_id,
            parent_agent: d.parent_agent,
            instruction: d.instruction || e.message,
            status: e.status,
            started: e.ts,
            nodes: [],
            tools: [],
            logs: [],
          };
          agents.set(tid, a);
          steps.push(a);
        } else {
          a.status = e.status;
          if (e.status === "done" || e.status === "failed") a.ended = e.ts;
          if (d.report) a.report = d.report;
        }
        break;
      }
      case "node": {
        const a = agentFor(e);
        if (a && !MAIN_IDS.has(e.agent_id)) a.nodes.push({ node: d.node || e.message, message: e.message, ts: e.ts });
        else {
          currentNode = e.message || d.node;
          steps.push({ kind: "node", key: "n-" + e.seq, agent_id: e.agent_id, node: d.node || "", message: e.message, status: e.status, ts: e.ts });
        }
        break;
      }
      case "tool": {
        const a = agentFor(e);
        if (!a) {
          steps.push({ kind: "log", key: "t-" + e.seq, agent_id: e.agent_id, message: `🔧 ${e.message}`, status: e.status, ts: e.ts });
          break;
        }
        if (e.status === "running") {
          a.tools.push({ key: "t-" + e.seq, name: e.message, status: "running", args: d.args });
        } else {
          const t = [...a.tools].reverse().find((x) => x.name === e.message && x.status === "running");
          if (t) {
            t.status = e.status;
            t.output = d.output;
            t.duration_s = d.duration_s;
          } else a.tools.push({ key: "t-" + e.seq, name: e.message, status: e.status, args: d.args, output: d.output, duration_s: d.duration_s });
        }
        break;
      }
      case "plan":
        steps.push({ kind: "plan", key: "p-" + e.seq, plan: d.plan, message: e.message, ts: e.ts });
        break;
      case "build":
        steps.push({ kind: "build", key: "b-" + e.seq, agent_id: e.agent_id, message: e.message, status: e.status, data: d, ts: e.ts });
        break;
      case "approval":
        steps.push({ kind: "approval", key: "ap-" + e.seq, message: e.message, status: e.status, data: d, ts: e.ts });
        break;
      case "report": {
        if (d.report) {
          reports.push(d.report);
          const a = d.report.task_id ? agents.get(d.report.task_id) : undefined;
          if (a) a.report = d.report;
        }
        break;
      }
      case "token":
        streaming += d.text || "";
        break;
      case "final":
        final = d.text ?? e.message ?? "";
        status = e.status === "failed" ? "failed" : "done";
        break;
      case "run":
        if (e.status === "done") status = "done";
        if (e.status === "failed") {
          status = "failed";
          error = e.message;
        }
        break;
      default: {
        const a = agentFor(e);
        if (a) a.logs.push(e);
        else if (e.message) steps.push({ kind: "log", key: "l-" + e.seq, agent_id: e.agent_id, message: e.message, status: e.status, ts: e.ts });
      }
    }
  }
  return { steps, agents: [...agents.values()], reports, final, streaming, status, currentNode, error };
}
