/**
 * The canvas's permission surface: read-only mode, the assignment banner and
 * the reviewer's Approve / Reject controls (04_UI_UX.md § 7).
 *
 * A separate module rather than more code in `init.js` (already 1,000+ lines,
 * and rule 13 says stop growing it). Everything permission-shaped on the canvas
 * lives here, so "why can't this user draw?" is one file to read.
 *
 * ⚠️ This module must not touch the autosave, draft or conflict machinery
 * (rules 11 and 18, .devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md).
 * It reports and it disables inputs; it never clears a draft, never cancels a
 * queued write, and never changes what a save sends. A user who lacks
 * permission still has unsaved work, and that work must survive.
 */
import { apiFetch } from "./api.js?v=1";
import { escapeHTML } from "./utils.js?v=1";
import { canAnnotate, canReview } from "./permissions.js?v=1";

const state = {
  project: null,
  role: null,
  task: null,
  banner: null,
};

/** True when the caller may not write to this project at all. */
export function isReadOnly() {
  return !canAnnotate(state.role);
}

/** The caller's effective project role, or null before it has loaded. */
export function currentRole() {
  return state.role;
}

/**
 * Load the project and remember the caller's role.
 * Returns the project, or null if it could not be loaded.
 */
export async function loadProjectPermissions(projectId) {
  if (!projectId) return null;
  try {
    const res = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}`);
    if (!res || !res.ok) return null;
    state.project = await res.json();
    state.role = state.project.my_role;
    return state.project;
  } catch (err) {
    console.error("Could not load project permissions", err);
    return null;
  }
}

// --- banner ----------------------------------------------------------------

function bannerHost() {
  if (state.banner) return state.banner;
  const el = document.createElement("div");
  el.id = "permissionBanner";
  el.className = "permission-banner";
  el.setAttribute("role", "status");
  document.body.prepend(el);
  state.banner = el;
  return el;
}

function showBanner(message, kind = "info") {
  const el = bannerHost();
  el.className = `permission-banner is-${kind}`;
  el.innerHTML = escapeHTML(message);
  el.hidden = false;
}

function hideBanner() {
  if (state.banner) state.banner.hidden = true;
}

/**
 * Decide and show the banner for the task now open.
 *
 * Shown **before** any drawing happens, not on the first rejected save: finding
 * out that your last ten minutes cannot be saved is the failure this prevents.
 */
export function updateTaskBanner(task, teamNameById = new Map()) {
  state.task = task;

  if (isReadOnly()) {
    showBanner("You have view-only access to this project.", "warn");
    return;
  }

  // The restriction only bites when the project opted in *and* the task belongs
  // to a team — an unassigned task is the shared pool and open to everyone
  // (01_DESIGN.md § 3.3).
  const restricted = state.project?.restrict_to_assigned_team;
  const assignedTeam = task?.assigned_team_id;
  if (restricted && assignedTeam) {
    const myTeamIds = new Set(state.myTeamIds || []);
    if (!myTeamIds.has(assignedTeam)) {
      const name = teamNameById.get(assignedTeam) || "another team";
      showBanner(`This task is assigned to ${name}. You can view it but not save changes.`, "warn");
      return;
    }
  }

  hideBanner();
}

/** Remember which teams the caller belongs to, for the banner check above. */
export function setMyTeams(teams) {
  state.myTeamIds = (teams || []).map((t) => t.id);
}

// --- read-only mode --------------------------------------------------------

/**
 * Disable the drawing surface for a viewer.
 *
 * Adds a class the stylesheet uses to grey out and block pointer events on the
 * tool palette, rather than disabling each control individually — a viewer
 * gains nothing from a half-live toolbar, and one class is far easier to keep
 * correct than a list of ids that drifts as tools are added.
 *
 * This is ergonomics, not security: every write is refused server-side anyway.
 */
export function applyReadOnlyMode() {
  if (!isReadOnly()) return;
  document.body.classList.add("is-read-only");
}

// --- review controls -------------------------------------------------------

/**
 * Render Approve / Reject for a reviewer.
 *
 * @param {HTMLElement} host
 * @param {object} task
 * @param {() => void} onReviewed called after a successful review
 */
export function renderReviewControls(host, task, onReviewed) {
  if (!host) return;
  if (!canReview(state.role) || !task) {
    host.innerHTML = "";
    return;
  }

  // Only offer the transitions that mean something for the current status:
  // approving an already-approved task is a no-op, and rejecting a task nobody
  // has finished is confusing.
  const buttons = [];
  if (task.status === "Completed" || task.status === "Rejected") {
    buttons.push(`<button type="button" class="tool-button" data-review="approved">✓ Approve</button>`);
  }
  if (task.status === "Completed" || task.status === "Approved") {
    buttons.push(`<button type="button" class="tool-button" data-review="rejected">✗ Reject</button>`);
  }
  host.innerHTML = buttons.join(" ");

  host.querySelectorAll("[data-review]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.review;
      let note = null;
      if (action === "rejected") {
        note = prompt("Why is this task being sent back?");
        if (note == null) return;
      }
      const res = await apiFetch(`/api/tasks/${encodeURIComponent(task.id)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, note: note || null }),
      });
      if (!res) return;
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        showBanner(body?.detail || `Could not record that review (${res.status}).`, "warn");
        return;
      }
      const result = await res.json();
      task.status = result.task_status;
      onReviewed?.(result);
      renderReviewControls(host, task, onReviewed);
    });
  });
}

/**
 * Report a permission failure from a save.
 *
 * Deliberately distinct from the 409 conflict flow, and deliberately
 * non-destructive: the draft and any queued write are left exactly as they are
 * (E-24). The user may regain access, and even if they do not, silently
 * discarding their work is the worst possible outcome.
 */
export function reportSaveForbidden(detail) {
  showBanner(
    `${detail || "You do not have permission to save this task."} ` +
    "Your unsaved work is kept in this browser.",
    "warn"
  );
}
