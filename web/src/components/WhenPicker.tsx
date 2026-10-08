import { useState } from "react";
import { DAY_NAMES, DEFAULT_WHEN, buildCron, describeWhen, parseCron, type Every, type When } from "../cron";

const CHOICES: [Every, string][] = [
  ["minutes", "Every few minutes"],
  ["hour", "Every hour"],
  ["day", "Every day"],
  ["weekdays", "Every weekday (Mon–Fri)"],
  ["week", "Every week"],
  ["month", "Every month"],
];

/** "When should it run?" in plain words. Emits a cron string; the raw cron is only under "Advanced". */
export default function WhenPicker({ cron, onChange }: { cron: string; onChange: (cron: string) => void }) {
  const parsed = parseCron(cron);
  const [w, setW] = useState<When>(parsed || DEFAULT_WHEN);
  const [advanced, setAdvanced] = useState(!parsed);
  const set = (patch: Partial<When>) => {
    const next = { ...w, ...patch };
    setW(next);
    onChange(buildCron(next));
  };
  const timed = w.every === "day" || w.every === "weekdays" || w.every === "week" || w.every === "month";
  return (
    <div className="space-y-2" data-testid="when-picker">
      <div className="flex flex-wrap items-center gap-2">
        <select className="input w-auto" value={w.every} onChange={(e) => set({ every: e.target.value as Every })} data-testid="when-every">
          {CHOICES.map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
        {w.every === "minutes" && (
          <label className="flex items-center gap-1 text-sm">
            every
            <input className="input w-20" type="number" min={1} max={59} value={w.minutes} onChange={(e) => set({ minutes: +e.target.value })} data-testid="when-minutes" />
            minutes
          </label>
        )}
        {w.every === "hour" && (
          <label className="flex items-center gap-1 text-sm">
            at
            <input className="input w-20" type="number" min={0} max={59} value={+w.time.split(":")[1] || 0}
              onChange={(e) => set({ time: `00:${String(Math.max(0, Math.min(59, +e.target.value || 0))).padStart(2, "0")}` })} />
            minutes past
          </label>
        )}
        {w.every === "month" && (
          <label className="flex items-center gap-1 text-sm">
            on day
            <input className="input w-20" type="number" min={1} max={28} value={w.dom} onChange={(e) => set({ dom: +e.target.value })} data-testid="when-dom" />
          </label>
        )}
        {timed && (
          <label className="flex items-center gap-1 text-sm">
            at
            <input className="input w-auto" type="time" value={w.time} onChange={(e) => set({ time: e.target.value || "09:00" })} data-testid="when-time" />
          </label>
        )}
      </div>
      {w.every === "week" && (
        <div className="flex flex-wrap gap-1" data-testid="when-days">
          {DAY_NAMES.map((n, d) => {
            const on = w.days.includes(d);
            return (
              <button key={d} type="button" aria-pressed={on} data-testid={`when-day-${d}`}
                className={`btn text-xs ${on ? "btn-primary" : ""}`}
                onClick={() => set({ days: on ? w.days.filter((x) => x !== d) : [...w.days, d] })}>
                {n.slice(0, 3)}
              </button>
            );
          })}
        </div>
      )}
      <div className="text-xs text-muted" data-testid="when-summary">
        {parseCron(cron) ? describeWhen(parseCron(cron)!) : "Custom schedule"}
        {" · "}
        <button type="button" className="underline" onClick={() => setAdvanced(!advanced)}>Advanced</button>
      </div>
      {advanced && (
        <input className="input mono" value={cron} onChange={(e) => { onChange(e.target.value); const p = parseCron(e.target.value); if (p) setW(p); }}
          placeholder="minute hour day month weekday" data-testid="when-cron" />
      )}
    </div>
  );
}
