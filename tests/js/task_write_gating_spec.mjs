/**
 * Behaviour spec for per-task write gating in the sidebar.
 *
 * Run: node tests/js/task_write_gating_spec.mjs
 *
 * Guards the rule established by the Objects-panel incident: an annotator who
 * is NOT assigned the open task could still use the panel's "Edit object
 * class" pencil. That control mints a project-wide class through
 * `ensureLabel()` → `POST /api/labels`, which is a *different* endpoint and a
 * *different* permission (MANAGER) from the task save. The server refused both
 * — nothing was written — but the client kept the refused class in
 * `state.labels` and repointed the annotation at its id, so the class panel and
 * the canvas disagreed with the server for the rest of the session, and any
 * annotation pointing at the phantom id rendered as the fallback "Object".
 *
 * The distinction that matters, and the reason the bug existed:
 *
 *   isReadOnly()     — "can this ROLE annotate this PROJECT at all"
 *   taskWriteBlock() — "can this USER write THIS TASK"
 *
 * The second is strictly wider: it is also true for a perfectly ordinary
 * annotator who is simply not the assignee. The sidebar consulted neither, and
 * the stylesheet's read-only rules named only the toolbar and the canvas. Every
 * mutating sidebar affordance is now gated on taskWriteBlock() (via
 * workspace.js's editBlockReason wrapper).
 *
 * Rendering only (rule 18b) — the server refuses regardless, as
 * tests/test_label_write_authz.py pins. What is under test here is that the UI
 * does not offer an action that cannot work.
 */
globalThis.window = { location: { origin: 'http://test', search: '' } };
globalThis.document = {
  cookie: '',
  body: { classList: { toggle() {}, add() {}, remove() {} } },
  getElementById: () => null,
  querySelector: () => null,
};
globalThis.localStorage = {
  _d: new Map([['logged_in', '1']]),
  getItem(k) { return this._d.has(k) ? this._d.get(k) : null; },
  setItem(k, v) { this._d.set(k, String(v)); },
  removeItem(k) { this._d.delete(k); },
};

// loadProjectPermissions is the only way in to `state.role`; drive it with a
// stubbed fetch so each role can be exercised through the public surface.
let nextRole = 'annotator';
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ id: 1, my_role: nextRole }),
});

const url = new URL('../../frontend/js/canvas-permissions.js', import.meta.url);
const cp = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const MY_ID = 7;
const MY_TEAM = 100;

const task = (over = {}) => ({
  id: 1,
  assignee_user_id: null,
  assignee_name: null,
  assigned_team_id: null,
  assigned_team_name: null,
  ...over,
});

async function asRole(role) {
  nextRole = role;
  await cp.loadProjectPermissions(1);
  cp.setMyUserId(MY_ID);
  cp.setMyTeams([{ id: MY_TEAM }]);
}

// --- 1. The two checks are NOT interchangeable ----------------------------

// This is the heart of the bug. For an annotator on a task assigned to someone
// else, the project-level check says "writable" while the per-task check says
// "blocked". Anything gated on the former was left live for exactly the user
// who must not use it.
await asRole('annotator');
const assignedElsewhere = task({ assignee_user_id: 999, assignee_name: 'Carol' });

ok('project-level check says the ANNOTATOR may annotate', cp.isReadOnly() === false);
ok('per-task check blocks the same user on someone else\'s task',
   cp.taskWriteBlock(assignedElsewhere) !== null);
ok('the two disagree — which is why the panel needed the per-task one',
   cp.isReadOnly() !== cp.isTaskReadOnly(assignedElsewhere));
ok('and the reason names the assignee',
   /Carol/.test(cp.taskWriteBlock(assignedElsewhere)));

// --- 2. The assignee themselves is not blocked ----------------------------

ok('the assigned user may write their own task',
   cp.taskWriteBlock(task({ assignee_user_id: MY_ID })) === null);

// --- 3. Unassigned is blocked for an annotator ----------------------------

// Unassigned is not a shared pool (api/permissions.py can_write_task): work
// nobody has been handed is work nobody below manager may start.
ok('an unassigned task is blocked for an annotator',
   cp.taskWriteBlock(task()) !== null);

// --- 4. Team assignment ---------------------------------------------------

ok('a task assigned to my team is writable',
   cp.taskWriteBlock(task({ assigned_team_id: MY_TEAM })) === null);
ok('a task assigned to another team is blocked',
   cp.taskWriteBlock(task({ assigned_team_id: 555, assigned_team_name: 'Beta' })) !== null);

// --- 5. Managers and owners are never partitioned by assignment -----------

for (const role of ['manager', 'owner']) {
  await asRole(role);
  ok(`a ${role} may write a task assigned to someone else`,
     cp.taskWriteBlock(assignedElsewhere) === null);
  ok(`a ${role} may write an unassigned task`, cp.taskWriteBlock(task()) === null);
}

// --- 6. A viewer is blocked everywhere ------------------------------------

await asRole('viewer');
ok('a viewer is blocked on their own-looking task',
   cp.taskWriteBlock(task({ assignee_user_id: MY_ID })) !== null);
ok('and isReadOnly agrees at the project level', cp.isReadOnly() === true);

// --- 7. The server\'s can_write wins when present -------------------------

// GET /api/tasks/{id} returns the resolver's own answer. When it is present it
// is the truth, and the client mirror only explains *why* — so a stale
// assignment field on the gallery row cannot re-enable a control.
await asRole('annotator');
ok('an explicit can_write:false blocks even when assigned to me',
   cp.taskWriteBlock(task({ assignee_user_id: MY_ID, can_write: false })) !== null);
ok('an explicit can_write:true unblocks a task that looks assigned elsewhere',
   cp.taskWriteBlock(task({ assignee_user_id: 999, can_write: true })) === null);

// --- 8. The approved freeze outranks assignment ---------------------------

// Sign-off ends the annotator's claim on the work. This is the one block that
// beats "assigned to me" — the whole point is that the state a reviewer
// accepted cannot be changed afterwards, and the sidebar's Objects panel gates
// its class/comment controls on exactly this call.
await asRole('annotator');
for (const status of ['Approved', 'Verified', 'Checked', 'Passed']) {
  const mine = task({ assignee_user_id: MY_ID, status });
  ok(`${status} blocks the assignee themselves`, cp.taskWriteBlock(mine) !== null);
  ok(`${status} says so in the reason`, new RegExp(status).test(cp.taskWriteBlock(mine)));
  // A server "yes" must not thaw it: an older bundle's can_write:true against a
  // task approved since is exactly the stale-row case this ordering guards.
  ok(`${status} outranks a stale can_write:true`,
     cp.taskWriteBlock(task({ assignee_user_id: MY_ID, status, can_write: true })) !== null);
}

// Rejected is rework, not sign-off — the assignee must still be able to act.
ok('a Rejected task assigned to me stays writable',
   cp.taskWriteBlock(task({ assignee_user_id: MY_ID, status: 'Rejected' })) === null);
ok('a Completed task assigned to me stays writable',
   cp.taskWriteBlock(task({ assignee_user_id: MY_ID, status: 'Completed' })) === null);

// Reviewers and above keep write access, or an approval could never be undone.
for (const role of ['reviewer', 'manager', 'owner']) {
  await asRole(role);
  ok(`a ${role} may still edit an Approved task`,
     cp.taskWriteBlock(task({ assignee_user_id: 999, status: 'Approved' })) === null);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
