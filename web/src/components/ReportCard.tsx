import { useState } from "react";
import { Link } from "react-router-dom";
import { FileText, Lightbulb, TriangleAlert } from "lucide-react";
import type { Report } from "../api";
import { JsonView, StatusPill } from "./ui";
import { DiffView } from "./DiffView";
import { Markdown } from "./Markdown";

export function ReportCard({ report, agentName }: { report: Report; agentName?: string }) {
  const [json, setJson] = useState(false);
  const files = [...(report.files_created || []).map((f) => ["+", f]), ...(report.files_modified || []).map((f) => ["~", f])];
  return (
    <div className="card p-4 text-sm">
      <div className="flex items-center gap-2 mb-2">
        <Link to={`/agents/${report.agent_id}`} className="font-medium hover:underline">
          {agentName || report.agent_id}
        </Link>
        <StatusPill status={report.status} />
        {report.duration_s ? <span className="text-xs text-muted">{report.duration_s.toFixed(1)}s</span> : null}
        <div className="flex-1" />
        <button className="text-xs text-muted hover:text-fg" onClick={() => setJson(!json)}>
          {json ? "Hide JSON" : "JSON"}
        </button>
      </div>
      {report.summary && <div className="mb-2">{report.summary}</div>}
      {typeof report.result === "string" && report.result && report.result !== report.summary && !json && (
        <details className="mb-2">
          <summary className="text-xs text-muted cursor-pointer">Result</summary>
          <div className="mt-2">
            <Markdown text={report.result} />
          </div>
        </details>
      )}
      {report.recommendations?.length > 0 && (
        <ul className="mb-2 space-y-1">
          {report.recommendations.map((r, i) => (
            <li key={i} className="flex gap-2">
              <Lightbulb size={14} className="mt-0.5 shrink-0 text-amber-500" />
              <span>{r}</span>
            </li>
          ))}
        </ul>
      )}
      {report.errors?.length > 0 && (
        <ul className="mb-2 space-y-1 text-red-600">
          {report.errors.map((r, i) => (
            <li key={i} className="flex gap-2">
              <TriangleAlert size={14} className="mt-0.5 shrink-0" />
              <span className="break-all">{r}</span>
            </li>
          ))}
        </ul>
      )}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {files.map(([k, f]) => (
            <span key={k + f} className="chip mono">
              <FileText size={11} />
              {k} {f}
            </span>
          ))}
        </div>
      )}
      {!json &&
        (report.artifacts || [])
          .filter((a) => a.kind === "diff" && a.content)
          .map((a, i) => (
            <details key={i} className="mb-2">
              <summary className="text-xs text-muted cursor-pointer">Diff{a.title ? `: ${a.title}` : ""}</summary>
              <div className="mt-2">
                <DiffView diff={a.content!} />
              </div>
            </details>
          ))}
      {!json && <ImageArtifacts artifacts={report.artifacts} />}
      {json && <JsonView value={report} />}
    </div>
  );
}

/** Screenshots / generated pictures, shown inline; click opens full size. */
export function ImageArtifacts({ artifacts }: { artifacts?: { kind: string; title?: string; content?: string; path?: string }[] }) {
  const imgs = (artifacts || []).filter((a) => a.kind === "image" && a.content);
  const seen = new Set<string>();
  // same screenshot reported by several agents/steps: show it once (by file path, else by content tail)
  const keyOf = (a: { content?: string; path?: string; title?: string }) =>
    a.path || (a.title && /\/shared\/screenshots\//.test(a.title) ? a.title : "") || a.content!.slice(-64);
  const uniq = imgs.filter((a) => (seen.has(keyOf(a)) ? false : (seen.add(keyOf(a)), true)));
  if (!uniq.length) return null;
  return (
    <div className="flex flex-col gap-2 my-2">
      {uniq.map((a, i) => (
        <figure key={i} className="m-0">
          <a href={a.content} target="_blank" rel="noreferrer">
            <img src={a.content} alt={a.title || "screenshot"} loading="lazy"
                 className="max-w-full rounded-lg border border-[var(--border,#3333)]" />
          </a>
          {(a.title || a.path) && <figcaption className="text-xs text-muted mt-1 mono">{a.title || a.path}</figcaption>}
        </figure>
      ))}
    </div>
  );
}
