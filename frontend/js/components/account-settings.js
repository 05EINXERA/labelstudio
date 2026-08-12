/**
 * The header settings button and its "Change password" modal.
 *
 * Lives in one module for the same reason `app-nav.js` does: two Level-1/2
 * headers need it today and a third will tomorrow, and the markup is small
 * next to the duplication. The modal is injected into `document.body` on first
 * use rather than pasted into every HTML file.
 *
 * Rule 15: visibility is toggled through `createModal` (`is-active`), never
 * `style.display`.
 */
import { apiFetch } from "../api.js?v=3";
import { createModal, setFieldError, clearFieldError } from "./modal.js?v=1";

const ICON_SETTINGS = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>`;

const MODAL_ID = "accountSettingsModal";

const MODAL_HTML = `
<div class="modal-overlay" id="${MODAL_ID}" role="dialog" aria-modal="true" aria-labelledby="${MODAL_ID}Title">
  <div class="modal-content" style="max-width: 420px;">
    <div class="modal-header">
      <h2 id="${MODAL_ID}Title">Change password</h2>
      <button class="modal-close" id="${MODAL_ID}Close" type="button" aria-label="Close">&times;</button>
    </div>
    <form id="${MODAL_ID}Form" autocomplete="off">
      <div class="modal-body settings-form" style="padding: 20px 24px; max-width: none;">
        <p class="mgmt-error" id="${MODAL_ID}Error" style="display: none;"></p>
        <p class="mgmt-empty" id="${MODAL_ID}Success" style="display: none;"></p>
        <div class="form-field">
          <label for="${MODAL_ID}Current">Current password</label>
          <input type="password" id="${MODAL_ID}Current" name="current_password"
                 autocomplete="current-password" required>
        </div>
        <div class="form-field">
          <label for="${MODAL_ID}New">New password</label>
          <input type="password" id="${MODAL_ID}New" name="new_password"
                 autocomplete="new-password" required>
        </div>
        <div class="form-field">
          <label for="${MODAL_ID}Confirm">Confirm new password</label>
          <input type="password" id="${MODAL_ID}Confirm" name="confirm_password"
                 autocomplete="new-password" required>
        </div>
      </div>
      <div style="display: flex; gap: 10px; justify-content: flex-end; padding: 16px;">
        <button type="button" class="tool-button" id="${MODAL_ID}Cancel">Cancel</button>
        <button type="submit" class="primary" id="${MODAL_ID}Submit"
                style="padding: 9px 18px; border-radius: 6px;">Change password</button>
      </div>
    </form>
  </div>
</div>`;

let modal = null; // built once per page load, then reused

function buildModal() {
  if (modal) return modal;

  const holder = document.createElement("div");
  holder.innerHTML = MODAL_HTML;
  const overlay = holder.firstElementChild;
  document.body.appendChild(overlay);

  const els = {
    overlay,
    form: overlay.querySelector(`#${MODAL_ID}Form`),
    current: overlay.querySelector(`#${MODAL_ID}Current`),
    next: overlay.querySelector(`#${MODAL_ID}New`),
    confirm: overlay.querySelector(`#${MODAL_ID}Confirm`),
    error: overlay.querySelector(`#${MODAL_ID}Error`),
    success: overlay.querySelector(`#${MODAL_ID}Success`),
    submit: overlay.querySelector(`#${MODAL_ID}Submit`),
  };

  function reset() {
    els.form.reset();
    els.error.style.display = "none";
    els.success.style.display = "none";
    [els.current, els.next, els.confirm].forEach(clearFieldError);
    els.submit.disabled = false;
  }

  function showError(message) {
    els.success.style.display = "none";
    els.error.textContent = message;
    els.error.style.display = "block";
  }

  const controller = createModal(overlay, {
    closeButton: overlay.querySelector(`#${MODAL_ID}Close`),
    focusOnOpen: els.current,
    // Passwords never linger in the DOM after the panel closes, whether it was
    // dismissed by Escape, the backdrop, Cancel or a successful change.
    onClose: reset,
  });

  overlay.querySelector(`#${MODAL_ID}Cancel`)
    .addEventListener("click", () => controller.close());

  els.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    els.error.style.display = "none";
    els.success.style.display = "none";
    [els.current, els.next, els.confirm].forEach(clearFieldError);

    const current = els.current.value;
    const next = els.next.value;

    // Confirmation is a client-only concern — the server never sees it — so it
    // is checked here and nowhere else.
    if (next !== els.confirm.value) {
      setFieldError(els.confirm, "The two new passwords do not match.");
      return;
    }
    if (next === current) {
      setFieldError(els.next, "New password must be different from the current one.");
      return;
    }

    els.submit.disabled = true;
    let res;
    try {
      res = await apiFetch("/api/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
    } catch (err) {
      console.error("Password change request failed", err);
      els.submit.disabled = false;
      showError("Could not reach the server. Check your connection and try again.");
      return;
    }

    // apiFetch returns undefined after redirecting an unauthenticated caller.
    if (!res) return;

    if (!res.ok) {
      els.submit.disabled = false;
      let detail = "";
      try {
        const body = await res.json();
        // FastAPI validation errors arrive as a list of objects, not a string.
        detail = typeof body.detail === "string" ? body.detail : "";
      } catch (err) {
        console.error("Password change failed with a non-JSON body", err);
      }
      if (res.status === 400) {
        setFieldError(els.current, detail || "Current password is incorrect.");
      } else {
        showError(detail || `Could not change your password (${res.status}).`);
      }
      return;
    }

    // The server reissued the session and CSRF cookies, so this tab stays
    // usable. Clear the fields immediately rather than waiting for the close.
    els.form.reset();
    els.submit.disabled = false;
    els.success.textContent = "Password changed.";
    els.success.style.display = "block";
    setTimeout(() => controller.close(), 1200);
  });

  modal = { open: controller.open, close: controller.close };
  return modal;
}

/**
 * Turn a header button into the settings entry point.
 *
 * @param {HTMLElement|null} button
 */
export function wireAccountSettings(button) {
  if (!button) return;
  button.innerHTML = ICON_SETTINGS;
  button.setAttribute("aria-label", "Account settings");
  button.setAttribute("title", "Account settings — change password");
  button.addEventListener("click", () => buildModal().open());
}
