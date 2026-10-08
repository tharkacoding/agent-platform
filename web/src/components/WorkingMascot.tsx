import { useEffect, useState } from "react";
import { Mascot } from "page-mascot";
import { api } from "../api";

/** page-mascot (MIT, nilbuild/page-mascot): a fox that watches the cursor in the corner; the label shows what AgentOS is doing (a run, the
 *  self-test or a tool check) or 'Ready'. Settings → "Show the mascot while AgentOS works" (`mascot_enabled`) turns it off. */
export default function WorkingMascot() {
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState("");

  useEffect(() => {
    const load = () => api.settings().then((s) => setEnabled(s.mascot_enabled !== false)).catch(() => setEnabled(false));
    load();
    window.addEventListener("aos:settings", load);
    return () => window.removeEventListener("aos:settings", load);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const poll = async () => {
      let what = "";
      try {
        const [runs, st] = await Promise.all([api.runs(5), api.selftest().catch(() => null)]);
        const running = runs.filter((r) => r.status === "running").length;
        if (st?.state?.running) what = `Checking: ${st.state.progress?.current || "self-test"}`;
        else if (running) what = running === 1 ? "Working on your task…" : `Working on ${running} tasks…`;
      } catch {
        /* gateway restarting: just hide */
      }
      if (alive) setBusy(what);
    };
    poll();
    const t = window.setInterval(poll, 4000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [enabled]);

  if (!enabled) return null;
  return (
    <div className="fixed bottom-28 right-3 z-50 hidden sm:flex flex-col items-center pointer-events-auto" data-testid="working-mascot">
      <Mascot directions="/mascots/fox-directions.webp" reactions="/mascots/fox-reactions.webp" size={96} label="AgentOS is working" />
      <div className="text-[11px] text-muted bg-[var(--panel,transparent)] rounded px-2 py-0.5 mt-1 max-w-40 truncate">{busy || "Ready"}</div>
    </div>
  );
}
