/**
 * The read-only "Assignment history" dialog for one task.
 *
 * Opened from the clock icon in the Tasks table's actions column. It only ever
 * reads: `GET /api/tasks/{id}/assignment-history`. Recording happens on the
 * server for every assignment path, so nothing here needs to know how a change
 * was made (.devnotes/features/task-assignment-history/02_DESIGN.md).
 *
 * Formatting lives in `assignment-history-format.js` (pure, spec'd under node);
 * this module is the DOM and the fetch.
 */
import { apiFetch } from "../../api.js?v=5";
import { escapeHTML } from "../../utils.js?v=2";
import { formatEvent } from "./assignment-history-format.js?v=1";

let overlay = null;
// Guards against a slow response for task A painting over task B's dialog.
let openToken = 0;

function shell(title) {
  return `
    <div class="modal-content assignment-history-modal">
      <div class="modal-header">
        <h2>${escapeHTML(title)}</h2>
        <button class="modal-close" data-role="close" type="button" aria-label="Close">&times;</button>
      </div>
      <div class="modal-body" data-role="body" aria-live="polite"></div>
      <div class="modal-footer">
        <button type="button" class="tool-button" data-role="close">Close</button>
      </div>
    </div>`;
}

function setBody(html) {
  overlay.querySelector("[data-role='body']").innerHTML = html;
}

function renderRow(event) {
  const f = formatEvent(event);
  const change = f.initial
    ? `<span class="ah-to">${escapeHTML(f.to)}</span> <span class="ah-tag" title="Recorded when assignment history was introduced; earlier changes were not kept.">initial</span>`
    : `<span class="ah-from">${escapeHTML(f.from)}</span>
       <span class="ah-arrow" aria-label="changed to">&rarr;</span>
       <span class="ah-to">${escapeHTML(f.to)}</span>`;
  const note = f.note ? `<div class="ah-note">${escapeHTML(f.note)}</div>` : "";
  return `
    <li class="ah-row">
      <time class="ah-stamp">${escapeHTML(f.stamp)}</time>
      <div class="ah-change">${change}${note}</div>
    </li>`;
}

function close() {
  openToken++;
  overlay?.classList.remove("is-active");
}

/**
 * Show the history for a task.
 *
 * @param {{id:number, description?:string}} task
 */
export async function openAssignmentHistory(task) {
  // Built lazily and reused: page-level, so per-call creation would stack
  // listeners (same reasoning as assign-modal.js).
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.id = "assignmentHistoryModal";
    document.body.appendChild(overlay);
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay || e.target.closest("[data-role='close']")) close();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && overlay.classList.contains("is-active")) close();
    });
  }

  const token = ++openToken;
  overlay.innerHTML = shell(`Assignment history — ${task.description || `task ${task.id}`}`);
  setBody(`<p class="muted">Loading…</p>`);
  overlay.classList.add("is-active");

  let events;
  try {
    const res = await apiFetch(
      `/api/tasks/${encodeURIComponent(task.id)}/assignment-history`
    );
    if (token !== openToken) return; // closed or reopened meanwhile
    if (!res) return; // apiFetch already handled a 401 redirect
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      // Server-written messages (403 naming the role) beat anything composed here.
      setBody(`<p class="ah-error">${escapeHTML(body?.detail || `Could not load the history (${res.status}).`)}</p>`);
      return;
    }
    events = await res.json();
  } catch (err) {
    if (token !== openToken) return;
    setBody(`<p class="ah-error">Could not load the history. Check your connection and try again.</p>`);
    return;
  }
  if (token !== openToken) return;

  if (!events.length) {
    setBody(`<p class="muted">No assignment changes recorded for this task.</p>`);
    return;
  }
  setBody(`<ol class="assignment-history-list">${events.map(renderRow).join("")}</ol>`);
}
