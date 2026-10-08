import { useState } from "react";
import { Link } from "react-router-dom";
import { Check, ShieldAlert, X } from "lucide-react";
import { api, fmtAgo, type Approval } from "../api";
import { useApp } from "../state";
import { DiffView } from "./DiffView";
import { JsonView } from "./ui";

/** Renders an approval with rich detail: manifest, files, test results, diffs, commands. */
export function ApprovalCard({ a, onDecided }: { a: Approval; onDecided?: () => void }) {
  const { names, refreshApprovals, toast } = useApp();
  const [busy, setBusy] = useState(false);
  const [remember, setRemember] = useState(0);
  const d = a.detail || {};
  const decide = (decision: "approve" | "deny") => {
    setBusy(true);
    api
      .decide(a.id, decision, remember || undefined)
      .then(() => {
        refreshApprovals();
        onDecided?.();
      })
      .catch((e) => toast({ title: "Decision failed", body: String(e.message || e), tone: "error" }))
      .finally(() => setBusy(false));
  };
  const known = new Set(["manifest", "files", "tests", "test_results", "diff", "command", "args", "tool", "permissions", "reason", "url"]);
  const rest = Object.fromEntries(Object.entries(d).filter(([k]) => !known.has(k)));

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-start gap-2">
        <ShieldAlert size={18} className={a.status === "pending" ? "text-amber-500 mt-0.5" : "text-muted mt-0.5"} />
        <div className="flex-1 min-w-0">
          <div className="font-medium">{a.title}</div>
          <div className="text-xs text-muted">
            {names[a.agent_id] || a.agent_id} · {fmtAgo(a.created_at)} ·{" "}
            <Link className="hover:underline" to={`/runs/${a.run_id}`}>
              run {a.run_id}
            </Link>
          </div>
        </div>
        {a.status !== "pending" && <span className={`chip ${a.status === "approved" ? "text-green-600" : "text-red-600"}`}>{a.status}</span>}
      </div>
      {d.reason && <div className="text-sm">{String(d.reason)}</div>}
      {d.command && <pre className="block mono">$ {String(d.command)}</pre>}
      {d.tool && (
        <div className="text-sm">
          Tool <span className="mono">{String(d.tool)}</span>
          {d.args && <JsonView value={d.args} />}
        </div>
      )}
      {d.url && <div className="text-sm mono break-all">{String(d.url)}</div>}
      {d.permissions && (
        <details open>
          <summary className="text-sm cursor-pointer">Requested permissions</summary>
          <JsonView value={d.permissions} />
        </details>
      )}
      {d.manifest && (
        <details open>
          <summary className="text-sm cursor-pointer">Manifest</summary>
          <JsonView value={d.manifest} />
        </details>
      )}
      {Array.isArray(d.files) && d.files.length > 0 && (
        <details>
          <summary className="text-sm cursor-pointer">Files ({d.files.length})</summary>
          <div className="space-y-2 mt-2">
            {d.files.map((f: any, i: number) => (
              <div key={i}>
                <div className="mono text-xs text-muted">{f.path ?? f.name ?? `file ${i + 1}`}</div>
                <pre className="block mono">{f.content ?? JSON.stringify(f, null, 2)}</pre>
              </div>
            ))}
          </div>
        </details>
      )}
      {(d.test_results || d.tests) && (
        <details>
          <summary className="text-sm cursor-pointer">Test results</summary>
          <JsonView value={d.test_results ?? d.tests} />
        </details>
      )}
      {d.diff && <DiffView diff={String(d.diff)} />}
      {Object.keys(rest).length > 0 && (
        <details>
          <summary className="text-xs text-muted cursor-pointer">More detail</summary>
          <JsonView value={rest} />
        </details>
      )}
      {a.status === "pending" && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button className="btn btn-primary" disabled={busy} onClick={() => decide("approve")}>
            <Check size={14} /> Approve
          </button>
          <button className="btn btn-danger" disabled={busy} onClick={() => decide("deny")}>
            <X size={14} /> Deny
          </button>
          <select className="input w-auto! text-xs py-1!" value={remember} onChange={(e) => setRemember(Number(e.target.value))}>
            <option value={0}>Just this once</option>
            <option value={5}>Allow similar for 5 min</option>
            <option value={30}>Allow similar for 30 min</option>
          </select>
        </div>
      )}
    </div>
  );
}
