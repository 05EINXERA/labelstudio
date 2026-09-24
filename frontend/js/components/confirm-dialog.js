/**
 * A styled yes/no dialog for destructive canvas actions, resolving to a boolean.
 *
 * Replaces native confirm() for the deletes the wipe guard cares about (Clear
 * all, and a multi-select delete large enough that the server would refuse
 * losing it by accident — see wipe-guard.js needsDeleteConfirm). Native
 * confirm() blocks the page, cannot say which button is the dangerous one, and
 * gets clicked through; this names the count, defaults focus to Cancel, and
 * styles the destructive choice as one.
 *
 * Built on demand and reused, so it needs no markup in any page. Toggles
 * `is-active` (rule 15). While open it swallows every key except Tab, so the
 * canvas's global Delete/Backspace and tool shortcuts cannot act on the
 * selection behind it — a second Delete press must not delete again.
 */

let overlay = null;
let titleEl = null;
let messageEl = null;
let confirmBtn = null;
let cancelBtn = null;
let settle = null;

function build() {
  overlay = document.createElement("div");
  overlay.className = "modal-overlay confirm-dialog";
  overlay.setAttribute("role", "alertdialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.innerHTML = `
    <div class="modal-content confirm-dialog-content">
      <div class="modal-header"><h2 class="confirm-dialog-title"></h2></div>
      <div class="modal-body"><p class="confirm-dialog-message"></p></div>
      <div class="modal-footer">
        <button type="button" class="confirm-dialog-cancel">Cancel</button>
        <button type="button" class="confirm-dialog-confirm confirm-danger"></button>
      </div>
    </div>`;
  titleEl = overlay.querySelector(".confirm-dialog-title");
  messageEl = overlay.querySelector(".confirm-dialog-message");
  confirmBtn = overlay.querySelector(".confirm-dialog-confirm");
  cancelBtn = overlay.querySelector(".confirm-dialog-cancel");
  overlay.setAttribute("aria-labelledby", "confirmDialogTitle");
  titleEl.id = "confirmDialogTitle";

  confirmBtn.addEventListener("click", () => close(true));
  cancelBtn.addEventListener("click", () => close(false));
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close(false);
  });
  // Capture phase on window, so this runs before the canvas's own handlers.
  window.addEventListener("keydown", onKey, true);
  document.body.appendChild(overlay);
}

function onKey(e) {
  if (!overlay || !overlay.classList.contains("is-active")) return;
  if (e.key === "Tab") {
    // Keep focus on the two buttons.
    e.preventDefault();
    (document.activeElement === cancelBtn ? confirmBtn : cancelBtn).focus();
    return;
  }
  e.preventDefault();
  e.stopPropagation();
  if (e.key === "Escape") {
    close(false);
  } else if (e.key === "Enter" || e.key === " ") {
    // Whichever button has focus; Cancel unless the user moved to Delete.
    close(document.activeElement === confirmBtn);
  }
}

function close(result) {
  if (!overlay) return;
  overlay.classList.remove("is-active");
  const done = settle;
  settle = null;
  if (done) done(result);
}

/**
 * Ask a destructive question. Resolves true only on an explicit confirm;
 * Cancel, Escape and a backdrop click all resolve false. A second call while
 * one is open resolves the first as cancelled.
 */
export function confirmDialog({ title, message, confirmLabel = "Delete" }) {
  if (!overlay) build();
  if (settle) close(false);
  titleEl.textContent = title;
  messageEl.textContent = message;
  confirmBtn.textContent = confirmLabel;
  overlay.classList.add("is-active");
  // Deferred a frame, as in modal.js: focusing inside a panel still
  // transitioning in is ignored by some browsers.
  requestAnimationFrame(() => cancelBtn.focus());
  return new Promise((resolve) => {
    settle = resolve;
  });
}
