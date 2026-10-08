import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Play, RefreshCw } from "lucide-react";
import { api, fmtTime, type Selftest } from "../api";
import { useApp } from "../state";
import { Empty, ErrorBox, Page, PageHeader, StatusPill } from "../components/ui";

const keyOf = (id: string) => id.split(".").map((x) => parseInt(x, 10) || 0);
const byId = (a: string, b: string) => {
  const x = keyOf(a), y = keyOf(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] ?? 0) !== (y[i] ?? 0)) return (x[i] ?? 0) - (y[i] ?? 0);
  return 0;
};

/** Capability self-test (docs/TEST_CASES.md §16): every case is checked against the REAL result by the harness. */
export default function Tests() {
  const [data, setData] = useState<Selftest | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const { toast } = useApp();
  const load = () => api.selftest().then(setData).catch(setErr);
  const running = !!data?.state.running;

  useEffect(() => {
    load();
  }, []);
  useEffect(() => {   // live progress while a run is going
    if (!running) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [running]);

  const start = (sections?: string[]) =>
    api
      .runSelftest(sections)
      .then(() => {
        toast({ title: sections ? `Running ${sections.join(", ")}` : "Running the capability suite" });
        load();
      })
      .catch(setErr);

  const s = data?.summary;
  const p = data?.state.progress;
  const ids = Object.keys(data?.rows ?? {}).sort(byId);
  return (
    <Page wide>
      <PageHeader
        title="Tests"
        subtitle="Capability suite: owner-style prompts, each verified against the real outcome (files, pages, schedules, screen). Runs nightly at 03:00 and on demand."
        actions={
          <div className="flex gap-2">
            <button className="btn" onClick={load} title="Refresh"><RefreshCw size={14} /></button>
            <button className="btn btn-primary" disabled={running} onClick={() => start()}>
              <Play size={14} /> {running ? "Running…" : "Run all now"}
            </button>
          </div>
        }
      />
      <ErrorBox error={err} />
      {s && (
        <div className="card p-4 mb-4 text-sm flex flex-wrap gap-4 items-center">
          <span><b className="text-green-600">{s.pass}</b> pass</span>
          <span><b className="text-red-600">{s.fail}</b> fail</span>
          <span><b className="text-red-600">{s.error}</b> error</span>
          <span><b>{s.skipped}</b> skipped</span>
          <span className="text-muted">{s.total} cases · {Math.round(s.seconds / 60)} min · ~{s.tokens.toLocaleString()} tokens</span>
          {running && (
            <span className="ml-auto flex items-center gap-2">
              <StatusPill status="running" />
              {p?.current && <span className="mono">{p.current}</span>}
              {p?.total ? <span className="text-muted">{p.done?.length ?? 0}/{p.total}</span> : null}
              {data?.state.started && <span className="text-muted">since {fmtTime(data.state.started)}</span>}
            </span>
          )}
        </div>
      )}
      {data && ids.length === 0 && <Empty>No results yet. Press “Run all now” — the suite takes a while and costs tokens.</Empty>}
      {ids.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-muted text-xs">
              <tr>
                <th className="p-2">Case</th><th className="p-2">Title</th><th className="p-2">Result</th>
                <th className="p-2">Evidence</th><th className="p-2 text-right">Time</th><th className="p-2 text-right">Tokens</th><th className="p-2" />
              </tr>
            </thead>
            <tbody>
              {ids.map((id) => {
                const r = data!.rows[id];
                const ev = [r.evidence, r.note].filter(Boolean).join(" — ");
                return (
                  <tr key={id} className="border-t border-[var(--border,#3333)] align-top">
                    <td className="p-2 mono whitespace-nowrap">{id}</td>
                    <td className="p-2">{r.title}</td>
                    <td className="p-2"><StatusPill status={p?.current === id && running ? "running" : r.status} /></td>
                    <td className="p-2 text-xs text-muted max-w-[32rem] break-words" title={ev}>
                      {ev.length > 220 ? ev.slice(0, 220) + "…" : ev}
                      {r.run_id && <> · <Link className="underline" to={`/runs/${r.run_id}`}>run</Link></>}
                    </td>
                    <td className="p-2 text-right whitespace-nowrap">{r.seconds != null ? `${Math.round(r.seconds)}s` : ""}</td>
                    <td className="p-2 text-right">{r.tokens ? r.tokens.toLocaleString() : ""}</td>
                    <td className="p-2">
                      <button className="btn" disabled={running} title={`Run ${id} again`} onClick={() => start([id])}>
                        <Play size={12} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
