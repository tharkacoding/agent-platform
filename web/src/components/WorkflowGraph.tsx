import { useMemo } from "react";
import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { WorkflowGraph as WG } from "../api";

/** Top-down layered layout (BFS depth from __start__; loop edges drawn as amber back-edges) — no dagre needed. */
function layout(g: WG): { nodes: Node[]; edges: Edge[] } {
  const ids = g.nodes.map((n) => n.id);
  const out = new Map<string, string[]>();
  ids.forEach((id) => out.set(id, []));
  g.edges.forEach((e) => out.get(e.source)?.push(e.target));

  const start = ids.find((i) => i === "__start__") ?? ids[0];
  const level = new Map<string, number>();
  // BFS gives shortest depth; then relax forward edges (bounded) to push nodes below their predecessors
  const queue: string[] = start ? [start] : [];
  if (start) level.set(start, 0);
  while (queue.length) {
    const cur = queue.shift()!;
    for (const nx of out.get(cur) || []) {
      if (!level.has(nx)) {
        level.set(nx, level.get(cur)! + 1);
        queue.push(nx);
      }
    }
  }
  let maxL = Math.max(0, ...level.values());
  ids.forEach((id) => {
    if (!level.has(id)) level.set(id, ++maxL);
  });
  const end = ids.find((i) => i === "__end__");
  if (end) level.set(end, Math.max(...[...level.entries()].filter(([k]) => k !== end).map(([, v]) => v)) + 1);

  const rows = new Map<number, string[]>();
  ids.forEach((id) => {
    const l = level.get(id)!;
    if (!rows.has(l)) rows.set(l, []);
    rows.get(l)!.push(id);
  });
  const W = 190, H = 80;
  const label = new Map(g.nodes.map((n) => [n.id, n.label]));
  const nodes: Node[] = [];
  [...rows.entries()].forEach(([l, row]) => {
    row.forEach((id, i) => {
      const special = id === "__start__" || id === "__end__";
      nodes.push({
        id,
        position: { x: (i - (row.length - 1) / 2) * W, y: l * H },
        data: { label: special ? (id === "__start__" ? "START" : "END") : label.get(id) || id },
        style: {
          fontSize: 12,
          padding: "6px 10px",
          borderRadius: special ? 999 : 10,
          border: "1px solid var(--border)",
          background: special ? "var(--accent)" : "var(--panel)",
          color: special ? "var(--accent-fg)" : "var(--text)",
          width: special ? 90 : 160,
        },
      });
    });
  });
  const edges: Edge[] = g.edges.map((e, i) => {
    const back = (level.get(e.target) ?? 0) <= (level.get(e.source) ?? 0);
    return {
      id: `e${i}`,
      source: e.source,
      target: e.target,
      animated: !!e.conditional,
      type: back ? "smoothstep" : "default",
      style: { stroke: back ? "#d97706" : "var(--muted)", strokeDasharray: e.conditional ? "4 3" : undefined },
      markerEnd: { type: MarkerType.ArrowClosed, color: back ? "#d97706" : "#888" },
    };
  });
  return { nodes, edges };
}

export function WorkflowGraph({ graph, height = 460 }: { graph: WG; height?: number }) {
  const { nodes, edges } = useMemo(() => layout(graph), [graph]);
  return (
    <div className="card overflow-hidden" style={{ height }}>
      <ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable nodesConnectable={false} proOptions={{ hideAttribution: true }}>
        <Background gap={16} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
