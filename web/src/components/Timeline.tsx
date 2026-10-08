import type { ReactNode } from "react";
import { Bot, CheckCircle2, CircleDashed, Flag, Sparkles, XCircle } from "lucide-react";
import type { RunView } from "./runModel";
import { Orb } from "./ui";
import { type OrbSpec, orbForAgent, orbForAgentId, orbForNode } from "./orbState";

function Dot({ status, orb }: { status: string; orb?: OrbSpec }) {
  if (status === "running") return <Orb spec={orb ?? orbForAgentId("")} size={20} style={{ margin: "-2px" }} />;
  if (status === "done" || status === "success") return <CheckCircle2 size={16} className="text-green-600" />;
  if (status === "failed") return <XCircle size={16} className="text-red-600" />;
  return <CircleDashed size={16} className="text-muted" />;
}

/** Vertical live timeline: Main Agent → agents → review → final. */
export function Timeline({ view, names }: { view: RunView; names: Record<string, string> }) {
  const mainNodes = view.steps.filter((s) => s.kind === "node");
  const firstMain = mainNodes[0];
  const reviewNode = [...mainNodes].reverse().find((s) => s.kind === "node" && /review|synth/i.test(s.node || s.message));
  const items: { key: string; icon: ReactNode; title: string; sub?: string; status: string; orb?: OrbSpec }[] = [];
  const lastMain = [...mainNodes].reverse()[0];
  items.push({
    key: "main",
    icon: <Sparkles size={14} />,
    title: "Main Agent",
    sub: firstMain && firstMain.kind === "node" ? view.currentNode || firstMain.message : "Planning task…",
    status: view.agents.length || view.status !== "running" ? "done" : "running",
    orb: lastMain && lastMain.kind === "node" ? orbForNode("main_agent", lastMain.node) : orbForAgentId("main_agent"),
  });
  for (const a of view.agents) {
    items.push({
      key: a.key,
      icon: <Bot size={14} />,
      title: names[a.agent_id] || a.agent_id,
      sub: a.status === "running" ? a.nodes[a.nodes.length - 1]?.message || "Running" : a.report?.summary || (a.status === "failed" ? "Failed" : "Completed"),
      status: a.status,
      orb: orbForAgent(a),
    });
  }
  if (reviewNode || view.status !== "running") {
    items.push({
      key: "review",
      icon: <Sparkles size={14} />,
      title: "Main Agent",
      sub: "Reviewing results…",
      status: view.status === "running" ? "running" : "done",
      orb: reviewNode && reviewNode.kind === "node" ? orbForNode("main_agent", reviewNode.node) : orbForNode("main_agent", "review"),
    });
  }
  items.push({
    key: "final",
    icon: <Flag size={14} />,
    title: "Final report",
    sub: view.status === "failed" ? view.error || "Run failed" : view.status === "done" ? "Delivered" : "Pending",
    status: view.status === "done" ? "done" : view.status === "failed" ? "failed" : "pending",
  });

  return (
    <ol className="relative">
      {items.map((it, i) => (
        <li key={it.key} className="flex gap-3 pb-4 relative">
          {i < items.length - 1 && <span className="absolute left-[7px] top-5 bottom-0 w-px bg-border" />}
          <div className="mt-0.5 z-10 bg-bg rounded-full">
            <Dot status={it.status} orb={it.orb} />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium flex items-center gap-1.5">
              <span className="text-muted">{it.icon}</span>
              {it.title}
            </div>
            {it.sub && <div className="text-xs text-muted truncate max-w-[260px]">{it.sub}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}
