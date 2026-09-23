/**
 * Behaviour spec for retroactive break entry's time handling.
 *
 * Run: node tests/js/manual_break_spec.mjs
 *      (or via tests/test_frontend_manual_break.py)
 *
 * The function under test converts a local date plus a wall time in the SITE
 * zone into a UTC instant. It is the whole +05:45 problem in one place:
 * `new Date("2026-09-20T13:00")` reads those digits in the *browser's* zone,
 * which is wrong for anyone not sitting in the office and silently 45 minutes
 * out even for someone who is.
 *
 * `profile.js` touches `document` at import time, so the function is mirrored
 * here against the same contract rather than imported — the same trade named
 * in `attendance_format_spec.mjs`. The round-trip assertions are what make
 * the mirror worth having: they would fail for any offset error, including
 * the whole-hour one this zone exists to catch.
 */
const SITE_TZ = 'Asia/Kathmandu';

/** Mirrors siteWallTimeToUtc in frontend/js/pages/profile.js. */
function siteWallTimeToUtc(dateStr, timeStr, siteTimezone = SITE_TZ) {
  if (!dateStr || !timeStr) return null;
  const naive = new Date(`${dateStr}T${timeStr}:00Z`);
  if (Number.isNaN(naive.getTime())) return null;
  if (!siteTimezone) return naive;
  const asSite = new Date(naive.toLocaleString('en-US', { timeZone: siteTimezone }));
  const asUtc = new Date(naive.toLocaleString('en-US', { timeZone: 'UTC' }));
  return new Date(naive.getTime() - (asSite.getTime() - asUtc.getTime()));
}

/** The wall time an instant reads as in the site zone, as "YYYY-MM-DD HH:MM". */
function readBackInSiteZone(instant) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: SITE_TZ,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(instant);
  const get = (t) => parts.find((p) => p.type === t).value;
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
}

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

// --- The offset is real and not a whole hour --------------------------------

ok('the site zone is on a 45-minute offset, which is the whole point',
   (() => {
     const probe = new Date('2026-09-20T12:00:00Z');
     const site = new Date(probe.toLocaleString('en-US', { timeZone: SITE_TZ }));
     const utc = new Date(probe.toLocaleString('en-US', { timeZone: 'UTC' }));
     const minutes = (site - utc) / 60000;
     return minutes === 345 && minutes % 60 !== 0; // +5h45m
   })());

// --- Conversion -------------------------------------------------------------

ok('midday converts to the right instant',
   siteWallTimeToUtc('2026-09-20', '13:00').toISOString()
     === '2026-09-20T07:15:00.000Z');

ok('a morning arrival converts to the right instant',
   siteWallTimeToUtc('2026-09-20', '09:50').toISOString()
     === '2026-09-20T04:05:00.000Z');

ok('just after local midnight lands on the PREVIOUS UTC day',
   siteWallTimeToUtc('2026-09-20', '00:15').toISOString()
     === '2026-09-19T18:30:00.000Z');

ok('just before local midnight stays on its own UTC day',
   siteWallTimeToUtc('2026-09-20', '23:45').toISOString()
     === '2026-09-20T18:00:00.000Z');

// --- Round trips, which is what an offset bug cannot survive ----------------

for (const [date, time] of [
  ['2026-09-20', '00:00'],
  ['2026-09-20', '00:15'],
  ['2026-09-20', '09:50'],
  ['2026-09-20', '13:00'],
  ['2026-09-20', '18:05'],
  ['2026-09-20', '23:45'],
  ['2026-01-01', '08:30'],
  ['2026-06-15', '17:20'],
]) {
  ok(`${date} ${time} round-trips to itself`,
     readBackInSiteZone(siteWallTimeToUtc(date, time)) === `${date} ${time}`);
}

// --- A whole-hour implementation would fail these ---------------------------

ok('the result is NOT what a naive UTC reading would give',
   siteWallTimeToUtc('2026-09-20', '13:00').toISOString()
     !== new Date('2026-09-20T13:00:00Z').toISOString());

ok('the offset applied is 5h45m, not 5h or 6h',
   (() => {
     const naive = new Date('2026-09-20T13:00:00Z');
     const got = siteWallTimeToUtc('2026-09-20', '13:00');
     const minutes = (naive - got) / 60000;
     return minutes === 345;
   })());

// --- Degenerate input --------------------------------------------------------

ok('a missing date yields null', siteWallTimeToUtc('', '13:00') === null);
ok('a missing time yields null', siteWallTimeToUtc('2026-09-20', '') === null);
ok('a malformed date yields null', siteWallTimeToUtc('not-a-date', '13:00') === null);

// --- The page contract ------------------------------------------------------

import { readFileSync } from 'node:fs';
const pageSrc = readFileSync(
  new URL('../../frontend/profile.html', import.meta.url), 'utf8'
);
const moduleSrc = readFileSync(
  new URL('../../frontend/js/pages/profile.js', import.meta.url), 'utf8'
);

ok('the form exists on the page', pageSrc.includes('id="manualBreakForm"'));

// Whitespace-normalised: the warning wraps across lines in the markup, and a
// line break inside the sentence must not read as the sentence being absent.
const pageText = pageSrc.replace(/\s+/g, ' ');

ok('the page warns that an entry cannot be undone',
   /cannot be edited or removed/i.test(pageText));

ok('the page explains the "entered later" marker as provenance',
   /entered later/i.test(pageText) &&
   /provenance|not a mark against/i.test(pageText));

ok('the module converts through the site zone rather than the browser zone',
   moduleSrc.includes('siteWallTimeToUtc') &&
   moduleSrc.includes('timeZone: siteTimezone'));

// Comments stripped: the module's prose names +05:45 precisely because that is
// the trap it avoids, so asserting against raw source fails on an explanation
// that is doing its job. This assertion is about code, so it reads code.
const moduleCode = moduleSrc
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|[^:"'`])\/\/.*$/gm, '$1');

ok('no hardcoded offset appears in the module code',
   !/\+?05:?45/.test(moduleCode) &&
   !/345/.test(moduleCode) &&
   !/5\.75/.test(moduleCode));

ok('the date picker is bounded to the backdating window',
   moduleCode.includes('breakDate.max') && moduleCode.includes('breakDate.min'));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
