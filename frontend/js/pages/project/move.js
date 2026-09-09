/**
 * `#/move` — move tasks to another project the owner owns.
 *
 * Owner only, both here and on the server. The router filters this route out
 * for everyone else (rule 18d), but that is rendering: `POST /api/tasks/bulk-move`
 * and `GET /api/projects/{id}/move-targets` each check Owner themselves.
 *
 * The table is the Tasks view's, in read-only mode (`buildColumns({readOnly})`)
 * so an owner recognises the rows they are moving without this becoming a
 * second place to edit them. It runs with `retainSelection`, because a
 * selection here is deliberately assembled across searches and pages — the
 * default prune would empty it on every keystroke.
 *
 * That opt-in has one consequence this module has to carry: `getSelection()`
 * returns ids whose rows are no longer in `getRows()`, so the action bar reads
 * from `selectedRows`, a side map kept in step with the checkbox events.
 *
 * Plan: .devnotes/move-task-feature/02_DESIGN.md § 6.
 */
import { apiFetch } from "../../api.js?v=5";
import { escapeHTML } from "../../utils.js?v=1";
import { createDataTable } from "../../components/data-table.js?v=5";
import { buildColumns, STATUSES } from "./task-columns.js?v=10";

const PAGE_SIZE = 10;

// Server sort keys, keyed by the column the user clicked. Mirrors the Tasks
// view's map; only the columns this table actually renders are listed.
const SERVER_SORT_KEYS = {
  description: "description",
  status: "status",
  time_spent: "time_spent",
  updated_at: "updated_at",
  assigned_team_id: "team",
  assignee: "assignee",
};

let ctx = null;
let root = null;
let table = null;
let targets = [];
let teamsById = new Map();
let usersById = new Map();

/**
 * The rows behind the selected ids, including ones that have scrolled off the
 * page or out of the current search. `getSelection()` alone gives ids; the
 * action bar has to name files, so it needs the rows too.
 */
let selectedRows = new Map();

const el = (id) => root?.querySelector(`#${id}`);

// --- chrome ----------------------------------------------------------------

function template() {
  return `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Project</p>
        <h2>Move Tasks</h2>
      </div>
    </div>

    <p class="muted move-intro">
      Move images and everything on them — annotations, elapsed time, review
      history and who they are assigned to — into another project you own.
      Nothing is copied and nothing is renumbered.
    </p>

    <div id="moveNotice" class="mgmt-notice" style="display:none;"></div>
    <div id="moveError" class="mgmt-error" style="display:none;"></div>

    <section class="move-destination">
      <h4>Destination</h4>
      <div id="destinationRow"></div>
    </section>

    <section class="move-classes">
      <h4>Classes</h4>
      <p class="muted">
        Classes belong to a project, so a moved annotation has to be matched to
        the destination's own class of the same name.
      </p>
      <label class="move-radio">
        <input type="radio" name="classStrategy" value="match_or_create" checked>
        <span>Match by name, and add any the destination does not have
          <em class="muted">(recommended — nothing is lost)</em></span>
      </label>
      <label class="move-radio">
        <input type="radio" name="classStrategy" value="match_only">
        <span>Match by name only
          <em class="muted">— annotations using a class the destination lacks
          arrive unlabelled</em></span>
      </label>
    </section>

    <section class="move-tasks">
      <h4>Tasks to move</h4>
      <div class="mgmt-toolbar">
        <input type="search" id="moveSearch" placeholder="Search filename…"
               aria-label="Search tasks">
        <select id="moveStatusFilter" aria-label="Filter by status">
          <option value="All">All statuses</option>
          ${STATUSES.map((s) => `<option value="${escapeHTML(s)}">${escapeHTML(s)}</option>`).join("")}
        </select>
      </div>
      <div id="moveTable"></div>
    </section>

    <div id="moveBar" class="bulk-bar">
      <span class="count" id="moveBarCount"></span>
      <button type="button" class="tool-button" id="moveClear">Clear selection</button>
      <button type="button" class="tool-button" id="moveSubmit" disabled>Move tasks</button>
    </div>

    <div class="modal-overlay" id="moveConfirm">
      <div class="modal-content">
        <div class="modal-header">
          <h2>Move tasks?</h2>
          <button class="modal-close" id="moveCancel" type="button">&times;</button>
        </div>
        <div class="modal-body" id="moveConfirmBody"></div>
        <div class="modal-footer">
          <button type="button" id="moveCancelBtn">Cancel</button>
          <button type="button" class="tool-button" id="moveConfirmGo">Move</button>
        </div>
      </div>
    </div>`;
}

function notice(message, kind = "info") {
  const box = kind === "error" ? el("moveError") : el("moveNotice");
  const other = kind === "error" ? el("moveNotice") : el("moveError");
  if (other) other.style.display = "none";
  if (!box) return;
  box.innerHTML = message;
  box.style.display = message ? "block" : "none";
}

function clearNotices() {
  notice("");
  const err = el("moveError");
  if (err) err.style.display = "none";
}

// --- destination -----------------------------------------------------------

function renderDestination() {
  const row = el("destinationRow");
  if (!row) return;

  if (!targets.length) {
    row.innerHTML = `<p class="muted">
      You do not own another project to move these into.
      <a class="cell-link" href="projects.html">Create one on the projects page</a>,
      then come back.
    </p>`;
    return;
  }

  row.innerHTML = `
    <select id="moveTarget" aria-label="Destination project">
      <option value="">Choose a project…</option>
      ${targets.map((t) => `<option value="${t.id}">${escapeHTML(t.name || `Project ${t.id}`)} (${t.task_count} task${t.task_count === 1 ? "" : "s"})</option>`).join("")}
    </select>`;
  el("moveTarget").addEventListener("change", updateBar);
}

const targetId = () => Number(el("moveTarget")?.value || 0) || null;
const targetName = () =>
  targets.find((t) => t.id === targetId())?.name || "the destination project";

const strategy = () =>
  root?.querySelector('input[name="classStrategy"]:checked')?.value || "match_or_create";

// --- table -----------------------------------------------------------------

async function fetchTaskPage({ page, pageSize, sortKey, sortDesc, query, filters }) {
  const params = new URLSearchParams({
    projectId: ctx.projectId,
    // This view never reads annotation contents — only the Classes and
    // Comments counts, which the server returns as two integers per row
    // (CLAUDE.md rule 17).
    include_annotations: "false",
    page: String(page),
    page_size: String(pageSize),
    sort: SERVER_SORT_KEYS[sortKey] || "description",
    order: sortDesc ? "desc" : "asc",
  });
  if (query) params.set("q", query);
  if (filters.status && filters.status !== "All") params.set("status", filters.status);

  const res = await apiFetch(`/api/tasks?${params.toString()}`);
  if (!res) return null;
  if (!res.ok) throw new Error(`Could not load tasks (${res.status}).`);
  return res.json();
}

/**
 * Keep `selectedRows` in step with the id set.
 *
 * Rows are added from whatever the table currently holds; ids removed from the
 * selection are dropped. A row that has scrolled out of the current search
 * stays in the map, which is the whole point — the bar has to be able to name
 * it, and re-fetching it would be a request per keystroke.
 */
function onSelectionChange(selected) {
  const onPage = new Map(table.getRows().map((r) => [r.id, r]));
  selected.forEach((id) => {
    if (!selectedRows.has(id) && onPage.has(id)) selectedRows.set(id, onPage.get(id));
  });
  [...selectedRows.keys()].forEach((id) => {
    if (!selected.has(id)) selectedRows.delete(id);
  });
  updateBar();
}

function updateBar() {
  const bar = el("moveBar");
  if (!bar) return;
  const count = table ? table.getSelection().size : 0;
  // Class toggle, never style.display — the CSS transition depends on it
  // (CLAUDE.md rule 15).
  bar.classList.toggle("is-active", count > 0);
  if (!count) return;

  const visible = new Set(table.getRows().map((r) => r.id));
  const offPage = [...table.getSelection()].filter((id) => !visible.has(id)).length;

  el("moveBarCount").innerHTML =
    `<strong>${count}</strong> task${count === 1 ? "" : "s"} selected` +
    (offPage
      ? ` <span class="muted">(${offPage} not shown by the current search — still included)</span>`
      : "");

  el("moveSubmit").disabled = !targetId();
}

// --- the move --------------------------------------------------------------

function openConfirm() {
  const ids = [...table.getSelection()];
  const names = ids
    .map((id) => selectedRows.get(id)?.description)
    .filter(Boolean);
  const shown = names.slice(0, 5).map((n) => `<li>${escapeHTML(n)}</li>`).join("");
  const more = names.length > 5
    ? `<li class="muted">…and ${names.length - 5} more</li>` : "";

  el("moveConfirmBody").innerHTML = `
    <p>Move <strong>${ids.length}</strong> task${ids.length === 1 ? "" : "s"}
       into <strong>${escapeHTML(targetName())}</strong>?</p>
    <ul class="move-preview">${shown}${more}</ul>
    <p class="muted">${
      strategy() === "match_or_create"
        ? "Classes the destination does not have will be added to it."
        : "Annotations using a class the destination lacks will arrive unlabelled."
    }</p>`;
  el("moveConfirm").classList.add("is-active");
}

function closeConfirm() {
  el("moveConfirm")?.classList.remove("is-active");
}

function resultHTML(body) {
  const parts = [
    `<p><strong>Moved ${body.moved} task${body.moved === 1 ? "" : "s"}</strong> into ${escapeHTML(targetName())}.</p>`,
  ];
  if (body.skipped) {
    parts.push(`<p class="muted">${body.skipped} were skipped — already there, or not yours to move.</p>`);
  }
  if (body.labels_created || body.labels_matched) {
    parts.push(`<p class="muted">Classes: ${body.labels_matched} matched, ${body.labels_created} added to the destination.
      ${body.annotations_relabelled} annotation${body.annotations_relabelled === 1 ? "" : "s"} re-pointed.</p>`);
  }
  if (body.warnings?.length) {
    parts.push(
      `<ul class="move-warnings">${body.warnings.map((w) => `<li>${escapeHTML(w)}</li>`).join("")}</ul>` +
      `<p><a class="cell-link" href="project.html?id=${encodeURIComponent(body.target_project_id)}#/access">Grant team access to ${escapeHTML(targetName())} →</a></p>`
    );
  }
  return parts.join("");
}

async function submitMove() {
  const ids = [...table.getSelection()];
  const button = el("moveConfirmGo");
  button.disabled = true;
  clearNotices();

  let res;
  try {
    res = await apiFetch("/api/tasks/bulk-move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ids,
        target_project_id: targetId(),
        class_strategy: strategy(),
      }),
    });
  } finally {
    button.disabled = false;
  }
  if (!res) return;                       // apiFetch redirected to login
  closeConfirm();

  if (res.status === 409) {
    // Some of the selection is open in somebody's canvas. Nothing moved, so
    // the selection is kept — the owner retries when they are done.
    const detail = (await res.json().catch(() => null))?.detail;
    const blocked = detail?.blocked || [];
    const names = blocked
      .map((b) => selectedRows.get(b.task_id)?.description || `#${b.task_id}`)
      .map((n) => escapeHTML(n));
    notice(
      `<p>${escapeHTML(detail?.message || "Some tasks are being edited right now.")}</p>` +
      (names.length ? `<ul class="move-warnings"><li>${names.join("</li><li>")}</li></ul>` : "") +
      `<p class="muted">Nothing was moved. Try again once they are closed.</p>`,
      "error"
    );
    return;
  }

  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    notice(
      escapeHTML(
        typeof detail === "string" ? detail : `The move failed (${res.status}).`
      ),
      "error"
    );
    return;
  }

  const body = await res.json();
  notice(resultHTML(body));

  // The moved rows are gone from this project, so the selection that named
  // them is meaningless. Clear it before reloading.
  selectedRows.clear();
  table.clearSelection();
  await table.load();
  await refreshTargets();
  updateBar();
}

// --- data ------------------------------------------------------------------

async function refreshTargets() {
  const res = await apiFetch(
    `/api/projects/${encodeURIComponent(ctx.projectId)}/move-targets`
  );
  if (!res || !res.ok) {
    targets = [];
    return;
  }
  targets = await res.json();
}

/**
 * Team and user names for the Team / Assignee cells, exactly as the Tasks view
 * sources them.
 *
 * Grants rather than `assignable-members` for the team names: a team can hold a
 * grant and have no members yet, and a task assigned to it would otherwise
 * render as "Team 7" — which is precisely the assignment an owner most needs to
 * see before moving it somewhere the team cannot reach.
 */
async function loadAssignmentNames() {
  const [grantsRes, membersRes] = await Promise.all([
    apiFetch(`/api/projects/${encodeURIComponent(ctx.projectId)}/grants`),
    apiFetch(`/api/projects/${encodeURIComponent(ctx.projectId)}/assignable-members`),
  ]);

  if (grantsRes?.ok) {
    for (const grant of await grantsRes.json()) {
      teamsById.set(grant.team_id, grant.team_name);
    }
  }
  if (membersRes?.ok) {
    for (const m of await membersRes.json()) usersById.set(m.user_id, m.username);
  }
}

// --- lifecycle -------------------------------------------------------------

export async function mount(container, context) {
  ctx = context;
  root = container;
  selectedRows = new Map();
  teamsById = new Map();
  usersById = new Map();

  root.innerHTML = template();

  const chosen = el("moveTarget");
  if (chosen) chosen.value = "";

  await Promise.all([refreshTargets(), loadAssignmentNames()]);
  renderDestination();

  table = createDataTable({
    mount: el("moveTable"),
    rowId: (r) => r.id,
    selectable: true,
    // The point of this view: a selection assembled across searches and pages.
    retainSelection: true,
    sortKey: "description",
    sortDesc: false,
    pageSize: PAGE_SIZE,
    emptyMessage: "No tasks match your search.",
    onSelectionChange,
    server: { fetchPage: fetchTaskPage },
    columns: buildColumns({
      role: ctx.myRole,
      projectId: ctx.projectId,
      teamsById,
      usersById,
      lockCache: {},
      currentUser: ctx.currentUser,
      readOnly: true,
    }),
  });

  el("moveSearch").addEventListener("input", (e) => table.setQuery(e.target.value));
  el("moveStatusFilter").addEventListener("change", (e) =>
    table.setFilter("status", e.target.value)
  );
  el("moveClear").addEventListener("click", () => {
    selectedRows.clear();
    table.clearSelection();
    updateBar();
  });
  el("moveSubmit").addEventListener("click", openConfirm);
  el("moveCancel").addEventListener("click", closeConfirm);
  el("moveCancelBtn").addEventListener("click", closeConfirm);
  el("moveConfirmGo").addEventListener("click", submitMove);
  root.querySelectorAll('input[name="classStrategy"]').forEach((r) =>
    r.addEventListener("change", updateBar)
  );

  await table.load();
  updateBar();
}

export function unmount() {
  table = null;
  selectedRows = new Map();
  root = null;
}
