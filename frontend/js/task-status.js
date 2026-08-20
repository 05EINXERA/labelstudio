/**
 * The task-status vocabulary.
 *
 * ⚠️ THIS IS A DELIBERATE MIRROR OF the status block in `schemas.py`. ⚠️
 *
 * The lists below duplicate `APPROVED_STATUSES` / `WORKING_STATUSES` /
 * `TASK_STATUSES` there. The duplication is intentional: this project has no
 * build step (rule 13), so there is no way to share one definition between
 * Python and the browser without adding a toolchain the project refuses.
 *
 * **This copy is for rendering only. The server is the security boundary.**
 * Everything here answers "which options do I draw?" — never "is this allowed?".
 * The reviewer gate on every approved-group status lives in
 * `api/routers/tasks.py::_require_review_role_for_status`, and it is what
 * actually stops an annotator approving their own work. A stale bundle offering
 * a status the API refuses is a cosmetic bug (E-17); a server check dropped
 * because "the client already hides it" is a vulnerability.
 *
 * `tests/js/task_status_spec.mjs` guards this file against drifting from
 * schemas.py. If you add a status there, add it here.
 *
 * ---------------------------------------------------------------------------
 * THE APPROVED GROUP
 * ---------------------------------------------------------------------------
 * 'Approved', 'Verified', 'Checked' and 'Passed' are synonyms: they mean the
 * same thing about the work (a reviewer signed it off) and differ only in which
 * *export batch* the sign-off belongs to. Approving a batch under a fresh name
 * lets the next export select only that batch instead of re-exporting every
 * task ever approved.
 *
 * Every rule that applies to 'Approved' applies to all of them. Adding a batch
 * status is one line here and one in schemas.py.
 */

import { atLeast } from "./permissions.js?v=1";

/** Approval synonyms, in display order. Mirrors `APPROVED_STATUSES`. */
export const APPROVED_STATUSES = ["Approved", "Verified", "Checked", "Passed"];

/** The non-approval vocabulary, in display order. Mirrors `WORKING_STATUSES`. */
export const WORKING_STATUSES = ["New", "In Progress", "Completed"];

/** The full vocabulary, in display order. Mirrors `TASK_STATUSES`. */
export const TASK_STATUSES = [...WORKING_STATUSES, ...APPROVED_STATUSES, "Rejected"];

/**
 * Statuses only a reviewer may set. Mirrors `REVIEW_STATUSES`.
 * Approving under any batch name is as privileged as approving.
 */
export const REVIEW_STATUSES = [...APPROVED_STATUSES, "Rejected"];

/**
 * Statuses asserting "this work is finished", so editing the annotations
 * afterwards demotes the task to 'In Progress' rather than letting amended work
 * keep a sign-off granted to a previous version. Mirrors `TERMINAL_STATUSES`.
 */
export const TERMINAL_STATUSES = [...APPROVED_STATUSES, "Completed"];

/** True when `status` is any member of the approved group. */
export function isApproved(status) {
  return APPROVED_STATUSES.includes(status);
}

/**
 * True when an approved-group status freezes this task for `role`.
 *
 * Mirrors the freeze branch of `can_write_task` in api/permissions.py: sign-off
 * ends the annotator's claim on the work, so an approved task is read-only for
 * everyone below reviewer. Reviewers, managers and owners stay writable, or
 * nobody could correct or un-approve a task.
 *
 * Rendering only (rule 18b) — the server refuses the write regardless.
 */
export function isFrozenForRole(status, role) {
  if (!isApproved(status)) return false;
  return !atLeast(role, "reviewer");
}

/** True when `status` is a reviewer verdict (approved group or Rejected). */
export function isReviewStatus(status) {
  return REVIEW_STATUSES.includes(status);
}

/** True when saving an edit to a task in `status` should demote it. */
export function isTerminal(status) {
  return TERMINAL_STATUSES.includes(status);
}

/**
 * Review verb -> the status it sets. Mirrors `REVIEW_ACTION_STATUS`.
 * Each approval verb is its status lowercased; 'reopened' is not a status of
 * its own, it just returns the task to the working vocabulary.
 */
export const REVIEW_ACTION_STATUS = {
  ...Object.fromEntries(APPROVED_STATUSES.map((s) => [s.toLowerCase(), s])),
  rejected: "Rejected",
  reopened: "In Progress",
};

/**
 * The status a review verb produces, for optimistic local updates only — the
 * server's `task_status` is authoritative whenever it answers.
 */
export function statusForReviewAction(action) {
  return REVIEW_ACTION_STATUS[action] || null;
}

/**
 * CSS modifier for a status chip/pill.
 *
 * Each approved-group status gets its own modifier (`is-approved`,
 * `is-verified`, ...) so export batches are distinguishable at a glance. The
 * modifiers are styled in one colour family (see styles.css) because the
 * statuses mean the same thing about the work — only the batch differs. This is
 * a *rendering* distinction only: every group membership test still goes
 * through `isApproved`, never through the class name.
 */
export function statusClass(status) {
  if (isApproved(status)) return `is-${status.toLowerCase()}`;
  switch (status) {
    case "Completed": return "is-completed";
    case "In Progress": return "is-progress";
    // Rejected is a warning colour, deliberately distinct from the approved
    // group: "reviewed and sent back" must not read as "reviewed and accepted".
    case "Rejected": return "is-rejected";
    default: return "";
  }
}

/** `status-*` variant used by the Tasks table's status <select>. */
export function statusSelectClass(status) {
  if (isApproved(status)) return `status-${status.toLowerCase()}`;
  switch (status) {
    case "In Progress": return "status-inprogress";
    case "Completed": return "status-completed";
    case "Rejected": return "status-rejected";
    default: return "status-new";
  }
}
