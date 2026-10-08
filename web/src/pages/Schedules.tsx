import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Play, Plus, Trash2 } from "lucide-react";
import { api, fmtTime, type Schedule } from "../api";
import { useApp } from "../state";
import { Empty, ErrorBox, Field, Page, PageHeader, StatusPill } from "../components/ui";
import WhenPicker from "../components/WhenPicker";
import { describeCron } from "../cron";

export default function Schedules() {
  const [items, setItems] = useState<Schedule[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const [form, setForm] = useState({ name: "", cron: "0 9 * * *", prompt: "", channel: "" });
  const [pickerKey, setPickerKey] = useState(0);
  const [lastRun, setLastRun] = useState<Record<string, string>>({});
  const { toast } = useApp();
  const load = () => api.schedules().then(setItems).catch(setErr);
  useEffect(() => {
    load();
  }, []);

  return (
    <Page>
      <PageHeader title="Schedules" subtitle="Recurring tasks the Main Agent runs on its own. Results land in the Home feed and your channels." />
      <ErrorBox error={err} />
      <div className="card p-4 mb-6 space-y-3">
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Name">
            <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Weekly learning plan" />
          </Field>
          <Field label="When should it run?">
            <WhenPicker key={pickerKey} cron={form.cron} onChange={(cron) => setForm((f) => ({ ...f, cron }))} />
          </Field>
        </div>
        <Field label="Prompt">
          <textarea className="input min-h-20" value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} placeholder="Analyze my progress this week and create next week's learning plan" />
        </Field>
        <Field label="Channel (optional)">
          <select className="input" value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
            <option value="">Home feed only</option>
            <option value="telegram">Telegram</option>
            <option value="discord">Discord</option>
          </select>
        </Field>
        <button
          className="btn btn-primary"
          disabled={!form.name.trim() || !form.prompt.trim() || !form.cron.trim()}
          onClick={() =>
            api
              .addSchedule({ ...form, enabled: true, channel: form.channel || undefined })
              .then(() => {
                setForm({ name: "", cron: "0 9 * * *", prompt: "", channel: "" });
                setPickerKey((k) => k + 1);
                load();
              })
              .catch(setErr)
          }
        >
          <Plus size={14} /> Add schedule
        </button>
      </div>
      {items.length === 0 && <Empty>No scheduled tasks. You can also ask in chat: “every Monday at 8 make my learning plan”.</Empty>}
      <div className="space-y-2">
        {items.map((s) => (
          <div key={s.id} className="card p-4 text-sm">
            <div className="flex items-center gap-2">
              <span className="font-medium flex-1">{s.name}</span>
              <button title={s.enabled ? "Click to disable" : "Click to enable"}
                      onClick={() => api.updateSchedule({ ...s, enabled: !s.enabled }).then(load).catch(setErr)}>
                <StatusPill status={s.enabled ? "active" : "disabled"} />
              </button>
              <button
                className="btn"
                onClick={() =>
                  api
                    .runSchedule(s.id)
                    .then((r) => {
                      setLastRun({ ...lastRun, [s.id]: r.run_id });
                      toast({ title: `Started ${s.name}`, link: s.kind === "selftest" ? "/tests" : `/runs/${r.run_id}` });
                    })
                    .catch(setErr)
                }
              >
                <Play size={13} /> Run now
              </button>
              <button className="text-muted hover:text-red-600" onClick={() => confirm(`Delete schedule ${s.name}?`) && api.deleteSchedule(s.id).then(load).catch(setErr)}>
                <Trash2 size={14} />
              </button>
            </div>
            <div className="text-muted mt-1">{s.prompt}</div>
            <div className="text-xs text-muted mt-2 flex flex-wrap gap-3">
              <span title={s.cron} data-testid="schedule-when">{describeCron(s.cron)}</span>
              <span>next: {fmtTime(s.next_run)}</span>
              <span>last: {fmtTime(s.last_run)}</span>
              {s.channel && <span>→ {s.channel}</span>}
              {s.kind === "selftest" && <Link className="underline" to="/tests">results</Link>}
              {lastRun[s.id] && s.kind !== "selftest" && <Link className="underline" to={`/runs/${lastRun[s.id]}`}>latest run</Link>}
            </div>
          </div>
        ))}
      </div>
    </Page>
  );
}
