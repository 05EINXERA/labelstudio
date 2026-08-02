/**
 * Modal open/close, with the field-level error handling the teams screens need.
 *
 * Exists because three new screens need modals and hand-rolling each one is how
 * `style.display` creeps back in. Rule 15: modals toggle `is-active`, never
 * `style.display` — the CSS transition keys off the class, so setting display
 * directly makes the panel appear without animating and leaves the class out of
 * sync with what is on screen.
 *
 * Deliberately thin: it owns visibility, focus and Escape, and nothing about
 * the form inside. Callers keep their own submit handlers.
 */

/**
 * @param {HTMLElement} overlay the `.modal-overlay` element
 * @param {object} [opts]
 * @param {HTMLElement} [opts.closeButton]
 * @param {HTMLElement} [opts.focusOnOpen] focused after opening
 * @param {()=>void} [opts.onClose] called after any close
 */
export function createModal(overlay, opts = {}) {
  const { closeButton, focusOnOpen, onClose } = opts;

  function open() {
    overlay.classList.add("is-active");
    // Deferred a frame: focusing an element inside a panel that is still
    // transitioning in gets scrolled/ignored by some browsers.
    requestAnimationFrame(() => focusOnOpen?.focus());
  }

  function close() {
    overlay.classList.remove("is-active");
    onClose?.();
  }

  closeButton?.addEventListener("click", close);

  // Click the backdrop (but not the panel) to dismiss.
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });

  // Escape closes, but only while this modal is the visible one — otherwise
  // every modal on the page would react to every keypress.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && overlay.classList.contains("is-active")) close();
  });

  return {
    open,
    close,
    isOpen: () => overlay.classList.contains("is-active"),
  };
}

/**
 * Show a validation message under a field, leaving the value in place.
 *
 * The add-member flow needs exactly this (04_UI_UX.md § 5.1): a mistyped
 * username must produce an inline error with the typed value **preserved** so
 * the user can fix a typo — not a toast over a modal that has already closed
 * and thrown their input away.
 */
export function setFieldError(fieldEl, message) {
  const holder = fieldEl.parentElement;
  let el = holder.querySelector(".field-error");
  if (!message) {
    el?.remove();
    fieldEl.removeAttribute("aria-invalid");
    return;
  }
  if (!el) {
    el = document.createElement("p");
    el.className = "field-error";
    el.setAttribute("role", "alert");
    holder.appendChild(el);
  }
  // textContent, not innerHTML: the message can embed a username the user typed.
  el.textContent = message;
  fieldEl.setAttribute("aria-invalid", "true");
  fieldEl.focus();
}

/** Clear any field error without touching the value. */
export function clearFieldError(fieldEl) {
  setFieldError(fieldEl, "");
}
