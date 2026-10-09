// Pure formatting for the team Activity view. No DOM and no imports, so it can
// be exercised under node (tests/test_team_activity_format.py).
//
// Times are rendered at a fixed offset in minutes east of UTC -- the same
// `tz_offset` the page sends to the server -- so the table and the server's day
// boundaries can never disagree.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const EM_DASH = "—";

function pad(n) {
  return String(n).padStart(2, "0");
}

/** A Date whose UTC getters read as local wall-clock time at `offsetMinutes`. */
function shifted(iso, offsetMinutes) {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return null;
  return new Date(ms + (offsetMinutes || 0) * 60000);
}

/** 45 -> "45s", 1830 -> "30m 30s", 3900 -> "1h 05m". */
export function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m ${pad(total % 60)}s`;
  return `${Math.floor(total / 3600)}h ${pad(Math.floor((total % 3600) / 60))}m`;
}

/** "08:05" at the offset, or an em dash when the value is unusable. */
export function formatClock(iso, offsetMinutes) {
  const d = shifted(iso, offsetMinutes);
  if (!d) return EM_DASH;
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

/** "09 Oct 08:05". */
export function formatDayClock(iso, offsetMinutes) {
  const d = shifted(iso, offsetMinutes);
  if (!d) return EM_DASH;
  return `${pad(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${formatClock(iso, offsetMinutes)}`;
}

function localDay(iso, offsetMinutes) {
  const d = shifted(iso, offsetMinutes);
  return d ? `${d.getUTCFullYear()}-${d.getUTCMonth()}-${d.getUTCDate()}` : null;
}

/** "08:00 - 10:05"; dates appear only when the ends fall on different local days. */
export function formatPeriod(startIso, endIso, offsetMinutes) {
  if (localDay(startIso, offsetMinutes) === localDay(endIso, offsetMinutes)) {
    return `${formatClock(startIso, offsetMinutes)} – ${formatClock(endIso, offsetMinutes)}`;
  }
  return `${formatDayClock(startIso, offsetMinutes)} – ${formatDayClock(endIso, offsetMinutes)}`;
}

/** A count, or an em dash when unknown. Never "0" for a missing value. */
export function formatCount(value) {
  return value === null || value === undefined ? EM_DASH : String(value);
}

/** "+19" / "0" / "-3"; an em dash when either end is unknown. */
export function formatDelta(start, end) {
  if (start === null || start === undefined || end === null || end === undefined) return EM_DASH;
  const delta = end - start;
  return delta > 0 ? `+${delta}` : String(delta);
}

export function sessionsLabel(count) {
  return `${count} session${count === 1 ? "" : "s"}`;
}

/**
 * Why the table is empty. Four situations that must not look alike:
 * monitoring off, a failed load, nothing ever recorded, and a quiet period.
 */
export function emptyMessage({ enabled, error, monitoringSince, filtered, offsetMinutes } = {}) {
  if (error) return "Could not load activity. Try again.";
  if (enabled === false) return "Activity monitoring is switched off.";
  if (!monitoringSince) return "No activity has been recorded yet.";
  if (filtered) return "No activity matches these filters in this period.";
  return `No activity in this period. Recording began ${formatDayClock(monitoringSince, offsetMinutes)}.`;
}
