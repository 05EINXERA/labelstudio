/**
 * Pure formatting for the team Activity table.
 *
 * No DOM and no imports, so it runs under plain node
 * (`tests/js/activity_format_spec.mjs`) like `assignment-history-format.js`.
 * Every function returns plain text; the caller escapes it before it goes into
 * markup. See .devnotes/feature/team-monitoring/02_DESIGN.md § 6.
 *
 * Times are rendered in the zone the SERVER names (`Asia/Kathmandu`, +05:45) with
 * Intl - never a numeric offset, which is wrong by 45 minutes.
 */

const DASH = "—";

/** `1h 05m`, `30m 30s`, `45s`. Whole seconds in, text out. */
export function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

function parts(iso, timeZone) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const f = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  const o = {};
  for (const p of f.formatToParts(d)) o[p.type] = p.value;
  return { day: `${o.year}-${o.month}-${o.day}`, dd: o.day, mon: o.month, clock: `${o.hour}:${o.minute}` };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** `09:12` in the site zone, or an em dash. */
export function formatClock(iso, timeZone) {
  const p = parts(iso, timeZone);
  return p ? p.clock : DASH;
}

/** `08 Oct 09:12` - used when a period crosses a local midnight. */
export function formatDayClock(iso, timeZone) {
  const p = parts(iso, timeZone);
  return p ? `${p.dd} ${MONTHS[Number(p.mon) - 1]} ${p.clock}` : DASH;
}

/** `09:12 → 11:47`, with dates added only when the two ends are on different local days. */
export function formatPeriod(firstIso, lastIso, timeZone) {
  const a = parts(firstIso, timeZone);
  const b = parts(lastIso, timeZone);
  if (!a || !b) return DASH;
  if (a.day === b.day) return `${a.clock} → ${b.clock}`;
  return `${formatDayClock(firstIso, timeZone)} → ${formatDayClock(lastIso, timeZone)}`;
}

/** An object count, or an em dash when it was never measured. Never "0" for null. */
export function formatCount(n) {
  return n === null || n === undefined ? DASH : String(n);
}

/** Signed change `+19`, `0`, `-3`; an em dash if either end is unknown. */
export function formatDelta(start, end) {
  if (start === null || start === undefined || end === null || end === undefined) return DASH;
  const d = end - start;
  return d > 0 ? `+${d}` : String(d);
}

export function sessionsLabel(n) {
  return n === 1 ? "1 session" : `${n} sessions`;
}

/**
 * What to show instead of a table, or null when there are rows.
 *
 * "Off", "nothing recorded yet" and "nobody worked" are three different facts;
 * an empty table that reads as the last one when it is really the first is the
 * quiet failure this exists to prevent.
 */
export function emptyMessage(body, timeZone) {
  if (!body) return "Could not load activity.";
  if (body.enabled === false) {
    return "Monitoring is off on this server, so nothing is being recorded.";
  }
  if (body.rows && body.rows.length) return null;
  if (!body.monitoring_since) {
    return "No activity has been recorded yet. Recording starts when monitoring is switched on; nothing earlier is available.";
  }
  const since = formatDayClock(body.monitoring_since, timeZone);
  return `No activity in this period. Recording began ${since}; nothing earlier is available.`;
}
