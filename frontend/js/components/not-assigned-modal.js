/**
 * Not-assigned warning dialog.
 *
 * Shown when an annotator opens a task that is assigned to other people. The
 * canvas opens read-only in that case (gallery.js clears isFullyLoaded, which
 * gates every autosave), so anything they draw is silently discarded. The
 * status-bar line alone was easy to miss, so this puts the warning in front
 * of them before they start work.
 *
 * Never shown to the project owner or an appointed reviewer: both have full
 * edit authority over any task (see _is_task_editor in api/routers/tasks.py),
 * so their changes do save. The caller decides that; this module only renders.
 *
 * Visibility is toggled with classList 'is-active' only (CLAUDE.md rule 15).
 */

let wired = false;

function getModal() {
  return document.getElementById("notAssignedModal");
}

function closeNotAssignedModal() {
  const modal = getModal();
  if (modal) modal.classList.remove("is-active");
}

function wireOnce(modal) {
  if (wired) return;
  wired = true;
  const okBtn = document.getElementById("notAssignedOkBtn");
  const closeBtn = document.getElementById("notAssignedClose");
  if (okBtn) okBtn.addEventListener("click", closeNotAssignedModal);
  if (closeBtn) closeBtn.addEventListener("click", closeNotAssignedModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeNotAssignedModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal.classList.contains("is-active")) {
      closeNotAssignedModal();
    }
  });
}

/**
 * Opens the warning for the task just opened.
 * @param {string[]} assignees Names the task is assigned to (may be empty when
 *   the server refused the task outright and did not say who holds it).
 */
export function showNotAssignedModal(assignees = []) {
  const modal = getModal();
  if (!modal) return;
  wireOnce(modal);

  const who = document.getElementById("notAssignedAssignees");
  if (who) {
    // textContent, not innerHTML: assignee names are free text typed by users.
    who.textContent = assignees.length
      ? `This task is assigned to: ${assignees.join(", ")}.`
      : "";
  }
  modal.classList.add("is-active");
  const okBtn = document.getElementById("notAssignedOkBtn");
  if (okBtn) okBtn.focus();
}
