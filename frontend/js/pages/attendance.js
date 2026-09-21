/**
 * The admin attendance dashboard (R5).
 *
 * **Rendering only, like every client-side role check** (CLAUDE.md rule 18b).
 * This module hides itself from a non-admin as a courtesy; the server answers
 * 404 to every request it makes regardless, so a stale bundle that still
 * renders the page shows an error, not data.
 *
 * Three labelling rules are load-bearing here rather than cosmetic, and the
 * footnote in `attendance.html` states each of them on screen:
 *
 *  - **"Tasks touched", never "completed".** Per-user completion is not
 *    derivable — there is no author column on the annotation write path — and
 *    a column that implies it will be read as it and acted on.
 *  - **A timeout end is never rendered as a plain logout time.** It is a lower
 *    bound, and an attendance record that overstates its own precision is
 *    worse than one that admits the gap.
 *  - **A manually entered break is marked, not hidden and not flagged as
 *    suspect.** The point is provenance, not suspicion.
 */
import { apiFetch } from "../api.js?v=5";
import { escapeHTML } from "../utils.js?v=2";
import { createDataTable } from "../components/data-table.js?v=6";
import { createModal } from "../components/modal.js?v=1";
import { renderAppNav, revealAdminLinks, wireLogout } from "../components/app-nav.js?v=5";
import { getCurrentUser } from "../session.js?v=2";

const els = {
  denied: document.getElementById("deniedPane"),
  main: document.getElementById("mainPane"),
  error: document.getElementById("errorBanner"),
  heading: document.getElementById("rangeHeading"),
  mode: document.getElementById("modeSelect"),
  dayControls: document.getElementById("dayControls"),
  rangeControls: document.getElementById("rangeControls"),
  day: document.getElementById("dayInput"),
  from: document.getElementById("fromInput"),
  to: document.getElementById("toInput"),
  prevDay: document.getElementById("prevDayBtn"),
  nextDay: document.getElementById("nextDayBtn"),
  today: document.getElementById("todayBtn"),
  applyRange: document.getElementById("applyRangeBtn"),
  search: document.getElementById("searchInput"),
  summary: document.getElementById("summaryStrip"),
  exportXlsx: document.getElementById("exportXlsxBtn"),
  exportCsv: document.getElementById("exportCsvBtn"),
  mount: document.getElementById("tableMount"),
  currentUser: document.getElementById("currentUser"),
  modal: document.getElementById("sessionsModal"),
  modalClose: document.getElementById("sessionsModalClose"),
  modalDone: document.getElementById("sessionsModalDone"),
  modalTitle: document.getElementById("sessionsModalTitle"),
  modalBody: document.getElementById("sessionsModalBody"),
};

let table = null;
let sessionsModal = null;
let siteTimezone = null;

// --- formatting -------------------------------------------------------------

/**
 * Seconds as "7h 32m".
 *
 * For display only. The xlsx export (R7) writes decimal hours as real numbers
 * instead, because a text duration cannot be summed and the recipient's first
 * instinct is to select the column and read the total.
 */
function duration(seconds) {
  if (!seconds) return "0h 00m";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

/**
 * An ISO instant as local clock time, in the *site* timezone.
 *
 * Explicitly not the viewer's zone: an attendance register is about when
 * people were in the office, so an admin reading it from anywhere else must
 * still see office time. `timeZone` is the IANA name the server reports, never
 * a numeric offset — Kathmandu is +05:45 and any whole-hour assumption is 45
 * minutes wrong in a way that looks like a rounding bug.
 */
function clockTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      ...(siteTimezone ? { timeZone: siteTimezone } : {}),
    });
  } catch (err) {
    console.error("Could not format a time", err);
    return "—";
  }
}

/**
 * The "latest seen" cell, qualified by *how* the day ended.
 *
 * A timeout is rendered with the reason attached rather than as a bare time,
 * because the two mean different things and a reader cannot tell them apart
 * otherwise.
 */
function lastSeenCell(row) {
  const time = escapeHTML(clockTime(row.last_seen));
  if (row.last_seen_reason === "logout") {
    return `${time} <span class="muted">logged out</span>`;
  }
  if (row.last_seen_reason === "open") {
    return `${time} <span class="badge badge-live">still here</span>`;
  }
  return `${time} <span class="muted" title="The tab was closed without logging out. This is the last moment they were seen — a lower bound, not a logout time.">ended by timeout</span>`;
}

function breaksCell(row) {
  const base = escapeHTML(duration(row.break_seconds));
  const notes = [];
  if (row.manual_break_seconds) {
    // Provenance, not suspicion.
    notes.push(
      `<span class="muted" title="Part of this was entered after the fact from the profile page, rather than declared with the break button.">*entered later</span>`
    );
  }
  if (row.has_unended_break) {
    notes.push(
      `<span class="muted" title="A break was started and never ended. The figure is a lower bound.">*not ended</span>`
    );
  }
  return notes.length ? `${base} ${notes.join(" ")}` : base;
}

// --- data -------------------------------------------------------------------

function showError(message) {
  els.error.textContent = message;
  els.error.style.display = "block";
}

function clearError() {
  els.error.style.display = "none";
}

function todayInSiteZone() {
  // Derived from the server's own view rather than the browser's clock: near
  // local midnight the two disagree, and the register must follow the office.
  const now = new Date();
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: siteTimezone || undefined,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(now);
    return parts; // en-CA formats as YYYY-MM-DD
  } catch (err) {
    return now.toISOString().slice(0, 10);
  }
}

function shiftDate(iso, days) {
  const d = new Date(`${iso}T12:00:00Z`); // midday, so a DST/offset shift cannot roll the date
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

async function load() {
  clearError();
  const mode = els.mode.value;
  const url =
    mode === "day"
      ? `/api/attendance/days/${encodeURIComponent(els.day.value)}`
      : `/api/attendance/range?from=${encodeURIComponent(els.from.value)}&to=${encodeURIComponent(els.to.value)}`;

  let res;
  try {
    res = await apiFetch(url);
  } catch (err) {
    console.error("Attendance request failed", err);
    showError("Could not reach the server.");
    return;
  }

  if (res.status === 404) {
    // The server's answer for a non-admin is identical to a nonexistent
    // resource, by design. Treat it as the gate, not as a missing page.
    showDenied();
    return;
  }
  if (!res.ok) {
    let detail = `Could not load attendance (${res.status}).`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch (err) {
      /* the status alone is the message */
    }
    showError(detail);
    return;
  }

  const body = await res.json();
  siteTimezone = body.timezone || siteTimezone;
  renderHeading(body);
  renderSummary(body.rows || []);
  table.setRows(body.rows || []);
}

function renderHeading(body) {
  const scope =
    els.mode.value === "day"
      ? body.date
      : `${body.date_from} → ${body.date_to}`;
  els.heading.textContent = `Attendance · ${scope}`;
  els.heading.title = `Instance ${body.instance_id} · ${body.timezone}`;
}

function renderSummary(rows) {
  if (!rows.length) {
    els.summary.innerHTML = "";
    return;
  }
  const people = new Set(rows.map((r) => r.user_id)).size;
  const total = (key) => rows.reduce((sum, r) => sum + (r[key] || 0), 0);
  const cards = [
    ["People", people],
    ["Present", duration(total("present_seconds"))],
    ["Active (timer)", duration(total("active_seconds"))],
    ["Breaks", duration(total("break_seconds"))],
    ["Tasks touched", total("tasks_touched")],
    ["Tasks reviewed", total("tasks_reviewed")],
  ];
  els.summary.innerHTML = cards
    .map(
      ([label, value]) =>
        `<div class="summary-card"><span class="summary-label">${escapeHTML(
          label
        )}</span><span class="summary-value">${escapeHTML(String(value))}</span></div>`
    )
    .join("");
}

// --- export -----------------------------------------------------------------

/**
 * The period currently on screen, as the export endpoints take it.
 *
 * Exporting what is displayed rather than a separately-chosen range is what
 * stops the file disagreeing with the table it was downloaded from — the one
 * thing that would make an admin distrust both.
 */
function currentRange() {
  if (els.mode.value === "day") {
    return { from: els.day.value, to: els.day.value };
  }
  return { from: els.from.value, to: els.to.value };
}

/**
 * Trigger a download.
 *
 * A plain navigation rather than fetch + Blob: the browser then handles the
 * Content-Disposition filename, the progress and the save dialog, and there is
 * no object URL to leak. The cookie rides along, and these are GETs, so no
 * CSRF token is needed.
 */
function downloadExport(extension) {
  const { from, to } = currentRange();
  if (!from || !to) {
    showError("Pick a period before exporting.");
    return;
  }
  const url =
    `/api/attendance/export.${extension}` +
    `?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`;
  // Same tab would replace the dashboard on an error response; a download
  // navigation in a throwaway tab leaves the page where it is either way.
  window.open(url, "_blank", "noopener");
}

// --- the sessions popup -----------------------------------------------------

async function openSessions(row) {
  els.modalTitle.textContent = `${row.username} · ${row.local_date}`;
  els.modalBody.innerHTML = `<p class="field-hint">Loading…</p>`;
  sessionsModal.open();

  try {
    const res = await apiFetch(
      `/api/attendance/days/${encodeURIComponent(row.local_date)}/users/${row.user_id}/sessions`
    );
    if (!res.ok) {
      els.modalBody.innerHTML = `<p class="mgmt-error">Could not load sessions (${res.status}).</p>`;
      return;
    }
    const body = await res.json();
    els.modalBody.innerHTML = renderSessions(body.sessions || []);
  } catch (err) {
    console.error("Could not load sessions", err);
    els.modalBody.innerHTML = `<p class="mgmt-error">Could not reach the server.</p>`;
  }
}

function endReasonLabel(reason) {
  if (reason === "logout") return "logged out";
  if (reason === "open") return "still open";
  return "ended by timeout";
}

function renderSessions(sessions) {
  if (!sessions.length) return `<p class="field-hint">No sessions on this day.</p>`;

  return sessions
    .map((s) => {
      const breaks = (s.breaks || [])
        .map((b) => {
          const source =
            b.source === "manual" ? " · entered later" : "";
          const ended = b.ended ? "" : " · not ended";
          return `<li>${escapeHTML(clockTime(b.started_at))}–${escapeHTML(
            clockTime(b.ended_at)
          )} · ${escapeHTML(duration(b.seconds))}${escapeHTML(source)}${escapeHTML(ended)}</li>`;
        })
        .join("");

      return `
        <div class="session-block">
          <div class="session-head">
            <strong>${escapeHTML(clockTime(s.started_at))} – ${escapeHTML(
        clockTime(s.ended_at)
      )}</strong>
            <span class="muted">${escapeHTML(endReasonLabel(s.end_reason))}</span>
          </div>
          <div class="session-meta">
            Present ${escapeHTML(duration(s.seconds))}
            · breaks ${escapeHTML(duration(s.break_seconds))}
            · ${s.tasks_touched} task${s.tasks_touched === 1 ? "" : "s"} touched
          </div>
          ${breaks ? `<ul class="session-breaks">${breaks}</ul>` : ""}
        </div>`;
    })
    .join("");
}

// --- boot -------------------------------------------------------------------

function showDenied() {
  els.main.hidden = true;
  els.denied.hidden = false;
}

function buildTable() {
  table = createDataTable({
    mount: els.mount,
    rowId: (row) => `${row.user_id}-${row.local_date}`,
    sortKey: "present_seconds",
    sortDesc: true,
    emptyMessage: "No attendance recorded for this period.",
    matches: (row, q) => row.username.toLowerCase().includes(q.toLowerCase()),
    columns: [
      { key: "username", label: "Person", sortable: true },
      { key: "local_date", label: "Date", sortable: true },
      {
        key: "first_seen",
        label: "First seen",
        sortable: true,
        render: (row) => escapeHTML(clockTime(row.first_seen)),
      },
      { key: "last_seen", label: "Last seen", sortable: true, render: lastSeenCell },
      {
        key: "session_count",
        label: "Sessions",
        align: "right",
        sortable: true,
        render: (row) =>
          `<button type="button" class="cell-link" data-sessions="1">${row.session_count}</button>`,
      },
      {
        key: "present_seconds",
        label: "Present",
        align: "right",
        sortable: true,
        render: (row) => escapeHTML(duration(row.present_seconds)),
      },
      {
        key: "active_seconds",
        label: "Active (timer)",
        align: "right",
        sortable: true,
        render: (row) => escapeHTML(duration(row.active_seconds)),
      },
      {
        key: "break_seconds",
        label: "Breaks",
        align: "right",
        sortable: true,
        render: breaksCell,
      },
      {
        key: "tasks_touched",
        label: "Tasks touched",
        align: "right",
        sortable: true,
      },
      {
        key: "tasks_reviewed",
        label: "Tasks reviewed",
        align: "right",
        sortable: true,
      },
    ],
  });

  // The sessions popup is opened from a cell button. Delegated, because the
  // table replaces its rows wholesale on every render.
  els.mount.addEventListener("click", (e) => {
    const trigger = e.target.closest("[data-sessions]");
    if (!trigger) return;
    const tr = trigger.closest("tr");
    if (!tr) return;
    const row = (table.getRows() || []).find(
      (r) => `${r.user_id}-${r.local_date}` === tr.dataset.id
    );
    if (row) openSessions(row);
  });
}

function wireControls() {
  els.mode.addEventListener("change", () => {
    const isDay = els.mode.value === "day";
    els.dayControls.hidden = !isDay;
    els.rangeControls.hidden = isDay;
    load();
  });

  els.day.addEventListener("change", load);
  els.prevDay.addEventListener("click", () => {
    els.day.value = shiftDate(els.day.value, -1);
    load();
  });
  els.nextDay.addEventListener("click", () => {
    els.day.value = shiftDate(els.day.value, 1);
    load();
  });
  els.today.addEventListener("click", () => {
    els.day.value = todayInSiteZone();
    load();
  });
  els.applyRange.addEventListener("click", load);

  els.search.addEventListener("input", () => table.setQuery(els.search.value));

  els.exportXlsx?.addEventListener("click", () => downloadExport("xlsx"));
  els.exportCsv?.addEventListener("click", () => downloadExport("csv"));
}

async function init() {
  renderAppNav(document.getElementById("appNav"), "attendance");
  wireLogout(document.getElementById("logoutBtn"));

  const user = await getCurrentUser();
  if (!user) {
    window.location.href = "/";
    return;
  }
  els.currentUser.textContent = user.username;

  // The client-side check is for rendering: it avoids showing an admin page
  // skeleton to someone who cannot use it. The server check is what matters,
  // and `load()` handles a 404 from it regardless of what this decided.
  if (!user.is_admin) {
    showDenied();
    return;
  }

  revealAdminLinks(document.getElementById("appNav"), "attendance");
  els.main.hidden = false;

  sessionsModal = createModal(els.modal, {
    closeButton: els.modalClose,
  });
  els.modalDone.addEventListener("click", () => sessionsModal.close());

  const today = todayInSiteZone();
  els.day.value = today;
  els.to.value = today;
  els.from.value = shiftDate(today, -6);

  buildTable();
  wireControls();
  await load();
}

init();
