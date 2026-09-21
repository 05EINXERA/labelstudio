/**
 * The profile page: the caller's own attendance, over a range (R9).
 *
 * **Self-scoped by construction.** `GET /api/attendance/me` takes no user
 * parameter at all, so this page cannot request another person's attendance
 * even if it tried. That is deliberate and is the shape `visible_time_logs`
 * was given after the same endpoint class leaked the whole roster's hours to
 * any authenticated caller: an endpoint that accepts an id and checks it is a
 * check that can be forgotten; one that cannot express another user is safe.
 *
 * Account settings are **hosted** here, not moved here. The two existing
 * buttons on the projects and project pages stay exactly where they are —
 * they are how a user reaches the password modal from anywhere, and removing
 * them would be a regression rather than a consolidation (Q28). This page
 * mounts a third button through the same `wireAccountSettings`, so nothing
 * moves and no import pin at either existing call site needs bumping.
 *
 * The formatting mirrors the admin dashboard deliberately: an annotator
 * checking their own hours and an admin checking the same day must not see
 * two different numbers or two different words for the same fact.
 */
import { apiFetch } from "../api.js?v=5";
import { escapeHTML } from "../utils.js?v=2";
import { createDataTable } from "../components/data-table.js?v=6";
import { renderAppNav, revealAdminLinks, wireLogout } from "../components/app-nav.js?v=5";
import { wireAccountSettings } from "../components/account-settings.js?v=2";
import { getCurrentUser } from "../session.js?v=2";

const els = {
  error: document.getElementById("errorBanner"),
  heading: document.getElementById("profileHeading"),
  from: document.getElementById("fromInput"),
  to: document.getElementById("toInput"),
  apply: document.getElementById("applyRangeBtn"),
  last7: document.getElementById("last7Btn"),
  last30: document.getElementById("last30Btn"),
  summary: document.getElementById("summaryStrip"),
  mount: document.getElementById("tableMount"),
  currentUser: document.getElementById("currentUser"),
  settings: document.getElementById("settingsBtn"),
};

let table = null;
let siteTimezone = null;

// --- formatting -------------------------------------------------------------
//
// Kept identical to pages/attendance.js on purpose. Two renderings of the same
// figure that disagree by a rounding rule would be read as two different
// facts.

function duration(seconds) {
  if (!seconds) return "0h 00m";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

/**
 * An ISO instant as clock time in the *site* zone, not the viewer's.
 *
 * Someone checking their hours from home must see office time. The zone is
 * the IANA name the server reports, never a numeric offset — Kathmandu is
 * +05:45 and a whole-hour assumption is wrong in a way that looks like a
 * rounding bug.
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

function lastSeenCell(row) {
  const time = escapeHTML(clockTime(row.last_seen));
  if (row.last_seen_reason === "logout") {
    return `${time} <span class="muted">logged out</span>`;
  }
  if (row.last_seen_reason === "open") {
    return `${time} <span class="badge badge-live">still here</span>`;
  }
  return `${time} <span class="muted" title="The tab was closed without logging out. This is the last moment you were seen, not when you left.">ended by timeout</span>`;
}

function breaksCell(row) {
  const base = escapeHTML(duration(row.break_seconds));
  const notes = [];
  if (row.manual_break_seconds) {
    notes.push(
      `<span class="muted" title="Part of this was entered after the fact rather than declared with the break button.">*entered later</span>`
    );
  }
  if (row.has_unended_break) {
    notes.push(
      `<span class="muted" title="A break was started and never ended, so this is a lower bound.">*not ended</span>`
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
  const now = new Date();
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: siteTimezone || undefined,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(now); // en-CA formats as YYYY-MM-DD
  } catch (err) {
    return now.toISOString().slice(0, 10);
  }
}

function shiftDate(iso, days) {
  // Midday, so an offset shift cannot roll the date under us.
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

async function load() {
  clearError();
  const from = encodeURIComponent(els.from.value);
  const to = encodeURIComponent(els.to.value);

  let res;
  try {
    // No user parameter, and none can be added: the endpoint does not accept
    // one. This is the whole self-scoping mechanism.
    res = await apiFetch(`/api/attendance/me?from=${from}&to=${to}`);
  } catch (err) {
    console.error("Attendance request failed", err);
    showError("Could not reach the server.");
    return;
  }

  if (!res.ok) {
    let detail = `Could not load your attendance (${res.status}).`;
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
  els.heading.textContent = `My attendance · ${body.date_from} → ${body.date_to}`;
  els.heading.title = `Instance ${body.instance_id} · ${body.timezone}`;
  renderSummary(body.rows || []);
  table.setRows(body.rows || []);
}

function renderSummary(rows) {
  if (!rows.length) {
    els.summary.innerHTML = "";
    return;
  }
  const total = (key) => rows.reduce((sum, r) => sum + (r[key] || 0), 0);
  const cards = [
    ["Days", rows.length],
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

function buildTable() {
  table = createDataTable({
    mount: els.mount,
    rowId: (row) => String(row.local_date),
    sortKey: "local_date",
    sortDesc: true,
    emptyMessage: "No attendance recorded for this period.",
    columns: [
      { key: "local_date", label: "Date", sortable: true },
      {
        key: "first_seen",
        label: "First seen",
        sortable: true,
        render: (row) => escapeHTML(clockTime(row.first_seen)),
      },
      { key: "last_seen", label: "Last seen", sortable: true, render: lastSeenCell },
      { key: "session_count", label: "Sessions", align: "right", sortable: true },
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
      { key: "tasks_touched", label: "Tasks touched", align: "right", sortable: true },
      { key: "tasks_reviewed", label: "Tasks reviewed", align: "right", sortable: true },
    ],
  });
}

function setRange(days) {
  const today = todayInSiteZone();
  els.to.value = today;
  els.from.value = shiftDate(today, -(days - 1));
  load();
}

function wireControls() {
  els.apply.addEventListener("click", load);
  els.from.addEventListener("change", load);
  els.to.addEventListener("change", load);
  els.last7.addEventListener("click", () => setRange(7));
  // 30, not 31: the server's ceiling is 31 days and an off-by-one here would
  // answer 400 for a button the user just pressed.
  els.last30.addEventListener("click", () => setRange(30));
}

async function init() {
  renderAppNav(document.getElementById("appNav"), "profile");
  wireLogout(document.getElementById("logoutBtn"));
  // Unchanged component, third mount point. The other two stay put.
  wireAccountSettings(els.settings);

  const user = await getCurrentUser();
  if (!user) {
    window.location.href = "/";
    return;
  }
  els.currentUser.textContent = user.username;
  revealAdminLinks(document.getElementById("appNav"), "profile");

  buildTable();
  wireControls();
  setRange(7);
}

init();
