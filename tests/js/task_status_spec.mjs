/**
 * Behaviour spec for the client-side task-status vocabulary.
 *
 * Run: node tests/js/task_status_spec.mjs  (or via tests/test_task_status.py)
 *
 * `frontend/js/task-status.js` is a hand-maintained mirror of the status block
 * in `schemas.py` — there is no build step to share one definition (rule 13),
 * so this is exactly the kind of thing that drifts silently. The consequence is
 * specific and expensive: a batch status the client does not know about is a
 * batch nobody can tick in the export filter, which is the entire point of the
 * feature.
 *
 * This file checks the client's internal consistency and its behaviour. The
 * Python-side half of the guard (client list == server list) lives in
 * tests/test_task_status.py, so the pair cannot drift by both being edited to a
 * new but different vocabulary.
 */
const url = new URL('../../frontend/js/task-status.js', import.meta.url);
const s = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// 1. The vocabulary itself, asserted against a literal so a careless edit to
//    the module is caught here rather than in a browser.
ok('approved group matches the server',
   eq(s.APPROVED_STATUSES, ['Approved', 'Verified', 'Checked', 'Passed']));
ok('working statuses match the server',
   eq(s.WORKING_STATUSES, ['New', 'In Progress', 'Completed']));
ok('full vocabulary is working + approved + rejected',
   eq(s.TASK_STATUSES,
      ['New', 'In Progress', 'Completed', 'Approved', 'Verified', 'Checked', 'Passed', 'Rejected']));

// 2. The derived sets. These are the ones that actually gate UI, and the whole
//    modularity claim rests on them being derived rather than re-listed.
ok('every approved status is a review status',
   s.APPROVED_STATUSES.every((x) => s.REVIEW_STATUSES.includes(x)));
ok('Rejected is a review status', s.REVIEW_STATUSES.includes('Rejected'));
ok('Completed is NOT a review status — an annotator sets it themselves',
   !s.REVIEW_STATUSES.includes('Completed'));
ok('every approved status is terminal',
   s.APPROVED_STATUSES.every((x) => s.TERMINAL_STATUSES.includes(x)));
ok('Completed is terminal', s.TERMINAL_STATUSES.includes('Completed'));
ok('Rejected is NOT terminal — it means there is more work to do',
   !s.TERMINAL_STATUSES.includes('Rejected'));
ok('In Progress is not terminal', !s.TERMINAL_STATUSES.includes('In Progress'));

// 3. isApproved: every batch synonym behaves exactly like Approved. This is the
//    feature's core promise.
for (const status of ['Approved', 'Verified', 'Checked', 'Passed']) {
  ok(`isApproved('${status}')`, s.isApproved(status) === true);
  ok(`'${status}' is a review status`, s.isReviewStatus(status) === true);
  ok(`'${status}' is terminal`, s.isTerminal(status) === true);
  ok(`'${status}' pills as approved`, s.statusClass(status) === 'is-approved');
  ok(`'${status}' selects as approved`, s.statusSelectClass(status) === 'status-approved');
}

for (const status of ['New', 'In Progress', 'Completed', 'Rejected', null, undefined, '']) {
  ok(`isApproved(${JSON.stringify(status)}) is false`, s.isApproved(status) === false);
}

// 4. Non-approval statuses keep their own colours — a rejection must never read
//    as an approval.
ok('Rejected is not coloured as approved', s.statusClass('Rejected') === 'is-rejected');
ok('Completed keeps its own colour', s.statusClass('Completed') === 'is-completed');
ok('In Progress keeps its own colour', s.statusClass('In Progress') === 'is-progress');
ok('unknown status gets no class rather than a wrong one', s.statusClass('Zzz') === '');
ok('In Progress select class is status-inprogress',
   s.statusSelectClass('In Progress') === 'status-inprogress');

// 5. Review verbs. Every approval status needs a verb, or the endpoint 422s on
//    the batch and the reviewer cannot approve at all.
for (const status of s.APPROVED_STATUSES) {
  const verb = status.toLowerCase();
  ok(`verb '${verb}' maps back to '${status}'`, s.statusForReviewAction(verb) === status);
}
ok("'rejected' maps to Rejected", s.statusForReviewAction('rejected') === 'Rejected');
ok("'reopened' maps to In Progress — not a status of its own",
   s.statusForReviewAction('reopened') === 'In Progress');
ok('an unknown verb maps to null rather than a wrong status',
   s.statusForReviewAction('nonsense') === null);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
