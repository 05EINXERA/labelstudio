/**
 * Source-level spec for how autosave pacing, the draft overflow and the
 * serialise-once change are WIRED into workspace.js and init.js.
 *
 * Run: node tests/js/autosave_wiring_spec.mjs  (or via tests/test_autosave_pacing.py)
 *
 * workspace.js and init.js need a real DOM at import time and there is no jsdom
 * here, so their behaviour cannot be exercised directly. The pure pieces they
 * use have their own behavioural specs (autosave_pacing, draft_overflow,
 * serialize_once, save_coalesce). What this adds is a tripwire on the *wiring*:
 * each assertion below is an invariant that, if a later edit broke it, would
 * silently reintroduce a data-loss or honesty bug, and none of the behavioural
 * specs could see it. It reads source text, so it is deliberately narrow.
 */
import fs from 'node:fs';

const read = (rel) => fs.readFileSync(new URL(`../../frontend/js/${rel}`, import.meta.url), 'utf8')
  .replace(/\r\n/g, '\n');
const workspace = read('components/workspace.js');
const init = read('init.js');

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

/** The body of `function name(...) { ... }` / `export function name(...)`. */
function body(src, name) {
  const start = src.search(new RegExp(String.raw`(?:export\s+)?(?:async\s+)?function\s+${name}\s*\(`));
  if (start < 0) return '';
  const open = src.indexOf('{', src.indexOf(')', start));
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(open, i + 1);
  }
  return '';
}

// --- the autosave path is paced, and ONLY that path -------------------------
{
  const save = body(workspace, 'save');
  ok('save() paces the autosave through the pacer', /pacer\.delayFor\(/.test(save));
  ok('save() starts from the 1 s debounce, not a longer constant', /DEBOUNCE_MS/.test(save));
  ok('save() schedules the draft on a debounce', /scheduleDraft\(\)/.test(save));
  ok('save() no longer serialises the draft synchronously per edit',
     !/\bsaveDraft\(\)/.test(save));
  ok('the pacer is never consulted by the flush paths in init.js', !/pacer/.test(init));
}

// --- the indicator never says Saved over unsent work -------------------------
{
  const resting = body(workspace, 'restingStatus');
  const guard = resting.indexOf('backendSyncTimeout');
  const saved = resting.indexOf('return "Saved"');
  ok('the resting status checks for a pending autosave', guard >= 0);
  ok('...before it can return "Saved"', guard >= 0 && saved > guard);
  ok('the resting status also checks for a save in flight', /isSaveInFlight\(/.test(resting));
}

// --- every send goes through the one place that paces and measures -----------
{
  const sync = body(workspace, 'syncToBackend');
  ok('syncToBackend records each save start for pacing', /pacer\.noteStart\(/.test(sync));
  ok('only confirmed, non-beacon saves feed the round-trip estimate',
     /ok === true && !useBeacon/.test(sync) && /pacer\.observe\(/.test(sync));
  ok('the follow-up save does not reuse a string serialised for the first',
     /immediate && canvasJson !== undefined/.test(sync));
  ok('the post-save fingerprint reuses the string that was sent',
     /noteHydratedAnnotations\(sentAnnotations, sentJson\)/.test(sync));
  ok('the payload carries the pre-serialised string', /annotationsJson:/.test(sync));
}

// --- drafts: nothing scheduled is lost when the page or the task goes away ----
{
  const flush = body(init, 'flushPendingSaves');
  const flushAt = flush.indexOf('flushDraft()');
  const syncAt = flush.indexOf('syncToBackend(');
  ok('flushPendingSaves writes a pending draft', flushAt >= 0);
  ok('...before it sends anything', flushAt >= 0 && syncAt > flushAt);
  ok('switching task cancels the outgoing task\'s scheduled draft',
     /cancelPendingDraft\(\)/.test(body(init, 'switchImage')));
  ok('opening a task also restores an overflow draft',
     /restoreOverflowDraft\(/.test(body(init, 'switchImage')));
  ok('...and abandons it if the annotator has moved on',
     /state\.gallery\[state\.galleryIndex\] === item/.test(body(init, 'switchImage')));
  ok('saveDraft stores through the overflow-aware path', /persistDraft\(/.test(body(workspace, 'saveDraft')));
  ok('a draft that fits nowhere is reported, not swallowed',
     /reportDraftUnavailable\(/.test(body(workspace, 'saveDraft')));
  ok('clearing a draft clears the overflow copy too',
     /clearOverflowDraft\(/.test(body(workspace, 'clearDraft')));
}

// --- leaving the workspace never strands a paced autosave --------------------
{
  const settle = body(init, 'settlePendingAutosave');
  ok('settlePendingAutosave sends the scheduled save', /syncToBackend\(/.test(settle));
  ok('...but never waits unboundedly', /Promise\.race\(/.test(settle) && /maxMs/.test(settle));
  ok('"back to project" settles a pending autosave before leaving',
     /await settlePendingAutosave\(\)/.test(init));
  ok('...and cancels the default navigation synchronously first',
     init.indexOf('e.preventDefault();   // must be synchronous') > 0
     && init.indexOf('e.preventDefault();   // must be synchronous') < init.indexOf('await settlePendingAutosave()'));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
