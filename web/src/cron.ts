/** Human-friendly schedules: build a cron expression from plain choices, and turn one back into English.
 *  Only the common shapes are understood; anything else is shown as its raw cron (never guessed). */

export type Every = "minutes" | "hour" | "day" | "weekdays" | "week" | "month";
export interface When {
  every: Every;
  minutes: number; // every N minutes
  time: string; // "HH:MM" (24 h) for day / weekdays / week / month; minute-of-hour for "hour"
  days: number[]; // 0 = Sunday … 6 = Saturday, for "week"
  dom: number; // day of the month, for "month"
}

export const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
export const DEFAULT_WHEN: When = { every: "day", minutes: 30, time: "09:00", days: [1], dom: 1 };

const hm = (t: string): [number, number] => {
  const m = /^(\d{1,2}):(\d{2})$/.exec(t || "");
  return m ? [Math.min(23, +m[1]), Math.min(59, +m[2])] : [9, 0];
};

export function buildCron(w: When): string {
  const [h, m] = hm(w.time);
  switch (w.every) {
    case "minutes":
      return `*/${Math.max(1, Math.min(59, Math.round(w.minutes) || 1))} * * * *`;
    case "hour":
      return `${m} * * * *`;
    case "day":
      return `${m} ${h} * * *`;
    case "weekdays":
      return `${m} ${h} * * 1-5`;
    case "week":
      return `${m} ${h} * * ${(w.days.length ? [...w.days].sort() : [1]).join(",")}`;
    case "month":
      return `${m} ${h} ${Math.max(1, Math.min(28, w.dom || 1))} * *`;
  }
}

/** cron → the picker's state, or null when the expression isn't one of the shapes above. */
export function parseCron(cron: string): When | null {
  const p = (cron || "").trim().split(/\s+/);
  if (p.length !== 5) return null;
  const [mi, ho, dom, mo, dow] = p;
  const num = (s: string) => (/^\d+$/.test(s) ? +s : NaN);
  if (mo !== "*") return null;
  const r = /^\*\/(\d+)$/.exec(mi);
  if (r && ho === "*" && dom === "*" && dow === "*") return { ...DEFAULT_WHEN, every: "minutes", minutes: +r[1] };
  if (!Number.isNaN(num(mi)) && ho === "*" && dom === "*" && dow === "*")
    return { ...DEFAULT_WHEN, every: "hour", time: `00:${String(num(mi)).padStart(2, "0")}` };
  if (Number.isNaN(num(mi)) || Number.isNaN(num(ho))) return null;
  const time = `${String(num(ho)).padStart(2, "0")}:${String(num(mi)).padStart(2, "0")}`;
  if (dom === "*" && dow === "*") return { ...DEFAULT_WHEN, every: "day", time };
  if (dom === "*" && dow === "1-5") return { ...DEFAULT_WHEN, every: "weekdays", time };
  if (dom === "*" && /^[0-7](,[0-7])*$/.test(dow))
    return { ...DEFAULT_WHEN, every: "week", time, days: dow.split(",").map((d) => +d % 7) };
  if (dow === "*" && !Number.isNaN(num(dom))) return { ...DEFAULT_WHEN, every: "month", time, dom: num(dom) };
  return null;
}

export function clock(time: string): string {
  const [h, m] = hm(time);
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

const ordinal = (n: number) => `${n}${["th", "st", "nd", "rd"][n % 100 > 10 && n % 100 < 14 ? 0 : Math.min(n % 10, 4) % 4]}`;

export function describeWhen(w: When): string {
  switch (w.every) {
    case "minutes":
      return w.minutes === 1 ? "Every minute" : `Every ${w.minutes} minutes`;
    case "hour":
      return hm(w.time)[1] === 0 ? "Every hour, on the hour" : `Every hour at ${hm(w.time)[1]} minutes past`;
    case "day":
      return `Every day at ${clock(w.time)}`;
    case "weekdays":
      return `Every weekday (Mon–Fri) at ${clock(w.time)}`;
    case "week": {
      const names = [...w.days].sort().map((d) => DAY_NAMES[d]);
      const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0];
      return `Every ${list} at ${clock(w.time)}`;
    }
    case "month":
      return `On the ${ordinal(w.dom)} of every month at ${clock(w.time)}`;
  }
}

/** Plain-English text for any cron string (falls back to the raw expression). */
export function describeCron(cron: string): string {
  const w = parseCron(cron);
  return w ? describeWhen(w) : cron;
}
