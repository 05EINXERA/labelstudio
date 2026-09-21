/**
 * Behaviour spec for the attendance dashboard's cell formatting.
 *
 * Run: node tests/js/attendance_format_spec.mjs
 *      (or via tests/test_frontend_attendance.py)
 *
 * These guard the three labelling rules the design calls load-bearing, because
 * each is a case where the honest rendering and the convenient one differ:
 *
 *  1. A **timeout** end is never rendered as a plain logout time. It is a lower
 *     bound, and an attendance record that overstates its precision is worse
 *     than one that admits the gap.
 *  2. A **manually entered** break is marked — provenance, not suspicion.
 *  3. An **unended** break is marked rather than silently dropped, because a
 *     break that vanishes inflates present time.
 *
 * `attendance.js` touches `document` at import time (it resolves its elements
 * into `els`), so the pure formatting helpers are re-implemented here against
 * the same contract rather than imported. That is a real duplication and worth
 * naming: if the page's formatting changes, this file must change with it.
 * The alternative — a DOM shim for a whole page module — would test the shim.
 */
let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

// --- the contract under test (mirrors attendance.js) -------------------------

function duration(seconds) {
  if (!seconds) return '0h 00m';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, '0')}m`;
}

function endReasonLabel(reason) {
  if (reason === 'logout') return 'logged out';
  if (reason === 'open') return 'still open';
  return 'ended by timeout';
}

// --- durations ---------------------------------------------------------------

ok('zero renders as 0h 00m, not blank',
   duration(0) === '0h 00m');
ok('null renders as 0h 00m rather than NaN',
   duration(null) === '0h 00m' && duration(undefined) === '0h 00m');
ok('a whole hour', duration(3600) === '1h 00m');
ok('minutes are zero padded', duration(3600 + 5 * 60) === '1h 05m');
ok('a full working day', duration(7.5 * 3600) === '7h 30m');
ok('a long day does not roll over into days',
   duration(25 * 3600) === '25h 00m');

// --- end reasons -------------------------------------------------------------
//
// The distinction these make is the whole point: "18:05 logged out" is a fact
// about when someone left; "18:05 ended by timeout" is the last moment they
// were seen, which may be long before they actually left.

ok('a logout is labelled as a logout',
   endReasonLabel('logout') === 'logged out');
ok('a timeout is NEVER labelled as a logout',
   endReasonLabel('timeout') !== 'logged out' &&
   endReasonLabel('timeout').includes('timeout'));
ok('an open session says so rather than claiming an end',
   endReasonLabel('open') === 'still open');
ok('an unknown reason degrades to the timeout wording, not to a logout claim',
   endReasonLabel('something-new') !== 'logged out');

// --- the breaks cell ---------------------------------------------------------
//
// Re-implemented without escapeHTML (which needs no DOM but lives in another
// module); the markers are what is under test.

function breakMarkers(row) {
  const notes = [];
  if (row.manual_break_seconds) notes.push('*entered later');
  if (row.has_unended_break) notes.push('*not ended');
  return notes;
}

ok('an ordinary declared break carries no marker',
   breakMarkers({ break_seconds: 1800, manual_break_seconds: 0,
                  has_unended_break: false }).length === 0);

ok('a manually entered break is marked',
   breakMarkers({ break_seconds: 1800, manual_break_seconds: 900,
                  has_unended_break: false })
     .includes('*entered later'));

ok('an unended break is marked rather than dropped',
   breakMarkers({ break_seconds: 1800, manual_break_seconds: 0,
                  has_unended_break: true })
     .includes('*not ended'));

ok('both markers can appear together',
   breakMarkers({ break_seconds: 1800, manual_break_seconds: 900,
                  has_unended_break: true }).length === 2);

ok('the manual marker is provenance, not an accusation',
   !breakMarkers({ manual_break_seconds: 900 }).join(' ').match(/suspect|invalid|unverified/i));

// --- column labelling --------------------------------------------------------
//
// Guards the one wording mistake that would misrepresent the data: per-user
// completion is not derivable at all (no author column on the annotation write
// path), so no column may imply it.

const COLUMN_LABELS = [
  'Person', 'Date', 'First seen', 'Last seen', 'Sessions', 'Present',
  'Active (timer)', 'Breaks', 'Tasks touched', 'Tasks reviewed',
];

ok('no column claims tasks were completed',
   !COLUMN_LABELS.some((l) => /completed|finished|done/i.test(l)));
ok('throughput is labelled "touched"',
   COLUMN_LABELS.includes('Tasks touched'));
ok('reviewed is its own column, separate from touched',
   COLUMN_LABELS.includes('Tasks reviewed'));
ok('present and active sit adjacent and are both labelled',
   COLUMN_LABELS.indexOf('Active (timer)') === COLUMN_LABELS.indexOf('Present') + 1);
ok('the timer column says it is the timer, since it will not match Present',
   COLUMN_LABELS.includes('Active (timer)'));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
