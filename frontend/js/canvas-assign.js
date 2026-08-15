/**
 * Task assignment from the annotation canvas (toolbar → Assign).
 *
 * ## Why this exists on the canvas at all
 *
 * A manager reviewing work walks the gallery with prev/next, and that walk
 * crosses task-list *pages*. Previously the only way to assign was the Tasks
 * table, so acting on what you just looked at meant going back and re-finding
 * the row — on a page you may no longer be on. This puts the same decision where
 * the evidence for it is.
 *
 * It replaces the toolbar's Export link, which was only ever a link to the
 * project's Exports tab (still reachable there, and from the project nav).
 *
 * ## Division of labour
 *
 * `components/assign-popover.js` is a pure picker: it asks the question and
 * resolves with an answer. This module owns everything with a consequence — the
 * role gate, the fetches, the PATCH, and the refresh afterwards. That sequencing
 * is the part with sharp edges, so it lives in one file.
 *
 * ## The refresh that must not be forgotten
 *
 * `assigned_team_id` / `assignee_user_id` are exactly the fields
 * `taskWriteBlock()` reads to decide whether the canvas is read-only and whether
 * the save menu appears (canvas-permissions.js, mirroring `can_write_task`).
 * Changing an assignment therefore changes the save gate, so every successful
 * write ends in `onAssigned()` → `refreshTaskPermissionUI()`. A manager cannot
 * lock *themselves* out (managers are never partitioned by assignment), but the
 * banner would otherwise keep describing the old assignee.
 *
 * ⚠️ Like canvas-permissions.js, this module never touches the draft, the
 * offline queue or the conflict machinery (rules 11 and 18). Reassigning a task
 * must not disturb unsaved annotations on it.
 */
import { apiFetch } from "./api.js?v=3";
import { canManage } from "./permissions.js?v=1";
import { describeAssignment } from "./pages/project/assign-options.js?v=1";
import { openAssignPopover, closeAssignPopover } from "./components/assign-popover.js?v=1";

const ctx = {
  projectId: null,
  button: null,
  getTask: () => null,
  onAssigned: () => {},
  notify: null,
};

/** Granted teams and the assignable roster, fetched once per page load. */
let teams = null;
let members = null;
let loadPromise = null;

/**
 * Load the granted teams and the roster, once.
 *
 * Deliberately lazy — triggered by the first click, not by boot. Most canvas
 * sessions are annotators who never see this button, and two requests every
 * annotator pays for to serve a control they cannot use is the kind of cost
 * that only shows up under 25 concurrent users.
 *
 * Failure leaves `teams`/`members` null and clears the memoised promise so a
 * second click retries rather than failing forever on a transient error.
 */
async function loadRoster() {
  if (teams && members) return true;
  if (loadPromise) return loadPromise;

  loadPromise = (async () => {
    try {
      const pid = encodeURIComponent(ctx.projectId);
      const [grantsRes, membersRes] = await Promise.all([
        apiFetch(`/api/projects/${pid}/grants`),
        apiFetch(`/api/projects/${pid}/assignable-members`),
      ]);

      if (!grantsRes?.ok || !membersRes?.ok) return false;

      const grants = await grantsRes.json();
      // One entry per granted team. The grants list is the authority on which
      // teams may be assigned to — `_validate_assignment` hard-rejects (422) a
      // team without a grant, so offering one would build a picker whose
      // options the server refuses.
      const byId = new Map();
      for (const g of grants) byId.set(g.team_id, g.team_name);
      teams = [...byId].map(([id, name]) => ({ id, name }));

      members = await membersRes.json();
      return true;
    } catch (err) {
      console.error("Could not load assignment options", err);
      return false;
    } finally {
      loadPromise = null;
    }
  })();

  return loadPromise;
}

/** Report a problem to the user. Falls back to alert() if no notifier is wired. */
function report(message) {
  if (ctx.notify) ctx.notify(message);
  else window.alert(message);
}

/**
 * Redraw the button for the task now open.
 *
 * Called on every task switch, so walking the gallery keeps the label truthful.
 * Hidden outright below `manager` — mirroring the endpoint's
 * `minimum=ProjectRole.MANAGER` (rule 18b: rendering only, the server still
 * refuses).
 */
export function renderAssignButton(role) {
  const btn = ctx.button;
  if (!btn) return;

  const task = ctx.getTask();
  if (!canManage(role) || !task) {
    btn.hidden = true;
    // A hidden button must not keep an open popover alive under it.
    closeAssignPopover();
    return;
  }

  btn.hidden = false;

  const now = describeAssignment(task, members || []);
  if (now.state === "none") {
    btn.textContent = "Assign";
    btn.classList.remove("is-assigned");
    btn.title = "Assign this task to a team or a person";
  } else {
    // The name goes *on* the button rather than behind the click: "who has
    // this?" is the question a manager asks while walking the gallery, and
    // making them open a panel to read one word defeats the purpose.
    btn.textContent = `Assigned · ${now.label}`;
    btn.classList.add("is-assigned");
    btn.title =
      now.state === "user"
        ? `Assigned to ${now.label} — click to change`
        : `Assigned to ${now.label} (anyone in the team) — click to change`;
  }
}

/** Send the chosen assignment for the open task. */
async function applyAssignment(task, choice) {
  const res = await apiFetch(`/api/tasks/${encodeURIComponent(task.id)}/assignment`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(choice),
  });
  if (!res) return; // apiFetch already handled a 401 redirect

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    // 403/422 both arrive here with a server-written message that names the
    // actual problem ("Team X does not have access to this project"), which is
    // more useful than anything this layer could compose.
    report(body?.detail || `Could not assign this task (${res.status}).`);
    return;
  }

  const result = await res.json();

  // Update the gallery row in place instead of reloading the gallery: a reload
  // would re-enter the task-open path and disturb the canvas — and the fields
  // that changed are all in this response.
  task.assigned_team_id = result.assigned_team_id;
  task.assignee_user_id = result.assignee_user_id;
  task.assigned_team_name =
    teams?.find((t) => String(t.id) === String(result.assigned_team_id))?.name ?? null;
  task.assignee_name =
    members?.find((m) => String(m.user_id) === String(result.assignee_user_id))
      ?.username ?? null;

  // Warnings are not failures: assigning someone outside the chosen team is
  // allowed by design (E-10), but the manager should know they did it.
  if (result.warnings?.length) report(result.warnings.join(" "));

  ctx.onAssigned(task);
}

async function onClick() {
  const task = ctx.getTask();
  if (!task) return;

  const ok = await loadRoster();
  if (!ok) {
    report("Could not load the list of teams and members. Please try again.");
    return;
  }

  if (!teams.length) {
    report(
      "No teams have access to this project yet. Grant one on the project's Access tab."
    );
    return;
  }

  const choice = await openAssignPopover({
    anchor: ctx.button,
    teams,
    members,
    task,
  });
  if (!choice) return;

  // Re-read the open task: the popover is async, and prev/next may have moved
  // the gallery while it was open. Writing the answer to a different task than
  // the one it was asked about is the bug this guards.
  const stillOpen = ctx.getTask();
  if (!stillOpen || stillOpen.id !== task.id) {
    report("The open task changed before that assignment was applied. Nothing was changed.");
    return;
  }

  await applyAssignment(task, choice);
}

/**
 * Wire the toolbar's Assign button.
 *
 * @param {object} opts
 * @param {string|number} opts.projectId
 * @param {HTMLElement} opts.button
 * @param {Function} opts.getTask    returns the currently open gallery row
 * @param {Function} opts.onAssigned called with the task after a successful write
 * @param {Function} [opts.notify]   surface a message to the user
 */
export function initCanvasAssign({ projectId, button, getTask, onAssigned, notify }) {
  ctx.projectId = projectId;
  ctx.button = button;
  ctx.getTask = getTask || (() => null);
  ctx.onAssigned = onAssigned || (() => {});
  ctx.notify = notify || null;

  if (!button) return;
  button.hidden = true; // stays hidden until a role confirms manager
  button.addEventListener("click", (e) => {
    e.stopPropagation(); // the popover's outside-click listener must not see this
    onClick();
  });
}
