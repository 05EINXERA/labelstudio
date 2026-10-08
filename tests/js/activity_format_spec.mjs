/**
 * Spec for the team Activity formatter.
 *
 * Run: node tests/js/activity_format_spec.mjs  (or via
 * tests/test_team_activity_js.py)
 *
 * Covers .devnotes/feature/team-monitoring/03_EDGE_CASES.md E-05, E-21, E-26.
 */
const url = new URL('../../frontend/js/pages/team/activity-format.js', import.meta.url);
const F = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};
const TZ = 'Asia/Kathmandu';

// durations
ok('seconds only', F.formatDuration(45) === '45s');
ok('minutes and seconds', F.formatDuration(1830) === '30m 30s');
ok('hours and minutes', F.formatDuration(3900) === '1h 05m');
ok('zero', F.formatDuration(0) === '0s');
ok('null and negative are zero', F.formatDuration(null) === '0s' && F.formatDuration(-9) === '0s');

// clock in the site zone (+05:45 - not a whole-hour offset)
ok('04:00Z is 09:45 in Kathmandu', F.formatClock('2026-10-08T04:00:00Z', TZ) === '09:45');
ok('18:30Z is 00:15 the next day', F.formatClock('2026-10-07T18:30:00Z', TZ) === '00:15');
ok('bad input is a dash', F.formatClock('nope', TZ) === '—');

// periods
ok('same day shows clock only',
   F.formatPeriod('2026-10-08T03:27:00Z', '2026-10-08T06:02:00Z', TZ) === '09:12 → 11:47');
ok('crossing local midnight adds dates',
   F.formatPeriod('2026-10-08T17:00:00Z', '2026-10-08T19:00:00Z', TZ) === '08 Oct 22:45 → 09 Oct 00:45');
ok('UTC dates that are one local day stay clock-only',
   F.formatPeriod('2026-10-07T18:30:00Z', '2026-10-07T20:00:00Z', TZ) === '00:15 → 01:45');

// counts
ok('null count is a dash, never 0', F.formatCount(null) === '—' && F.formatCount(undefined) === '—');
ok('zero count is 0', F.formatCount(0) === '0');
ok('positive delta has a plus', F.formatDelta(12, 31) === '+19');
ok('zero delta is 0', F.formatDelta(5, 5) === '0');
ok('negative delta has a minus', F.formatDelta(10, 7) === '-3');
ok('unknown end gives a dash', F.formatDelta(5, null) === '—' && F.formatDelta(null, 5) === '—');
ok('zero start is a real number', F.formatDelta(0, 4) === '+4');

// labels
ok('singular', F.sessionsLabel(1) === '1 session');
ok('plural', F.sessionsLabel(3) === '3 sessions');

// empty states
ok('off is named', /off/i.test(F.emptyMessage({ enabled: false, rows: [] }, TZ)));
ok('rows means no message', F.emptyMessage({ enabled: true, rows: [{}] }, TZ) === null);
ok('never recorded says so', /No activity has been recorded yet/.test(F.emptyMessage({ enabled: true, rows: [], monitoring_since: null }, TZ)));
ok('empty period names the start of recording',
   /Recording began 08 Oct 09:45/.test(F.emptyMessage({ enabled: true, rows: [], monitoring_since: '2026-10-08T04:00:00Z' }, TZ)));
ok('missing body is an error message', /Could not load/.test(F.emptyMessage(null, TZ)));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
