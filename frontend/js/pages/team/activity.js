/**
 * `#/activity` - what each member worked on, for team owners and managers.
 *
 * One row per (member, task): the member's stretches in the period pivoted
 * together. Expanding a row lists the individual stretches, so the pivot is
 * auditable and the effect of the timer's 5-minute idle expiry is visible rather
 * than hidden. Design: .devnotes/feature/team-monitoring/02_DESIGN.md § 6.
 *
 * Rendering only (CLAUDE.md rule 18b): the server decides who may read this and
 * answers 403/404 regardless of what the tab shows.
 */
import { apiFetch } from "../../api.js?v=5";
import { escapeHTML } from "../../utils.js?v=2";
import {
  formatDuration, formatPeriod, formatClock, formatCount, formatDelta,
  sessionsLabel, emptyMessage,
} from "./activity-format.js?v=1";

let ctx = null;
let root = null;
let loadToken = 0;
let body = null;

const keyOf = (r) => `${r.user_id}:${r.task_id ?? "x"}`;

function shellHTML() {
  return `
    <div data-role="activity">
    <div class="view-header">
      <h3>Activity</h3>
      <p class="muted">Active time recorded by each member's timer, per task. Object counts are the task's
        total at the start and end, not who drew what.</p>
    </div>
    <div class="mgmt-toolbar">
      <label class="field-label-inline" for="actFrom">From</label>
      <input type="date" id="actFrom" aria-label="From">
      <label class="field-label-inline" for="actTo">To</label>
      <input type="date" id="actTo" aria-label="To">
      <button class="tool-button" type="button" data-role="today">Today</button>
      <select id="actMember" aria-label="Member"><option value="">All members</option></select>
      <input type="search" id="actQuery" placeholder="Search tasks…" aria-label="Search tasks">
      <span class="toolbar-spacer"></span>
      <span class="muted" data-role="asof"></span>
    </div>
    <div data-role="banner"></div>
    <div data-role="table"></div>
    <div data-role="idle"></div>
    </div>`;
}

function params() {
  const q = new URLSearchParams();
  const from = root.querySelector("#actFrom").value;
  const to = root.querySelector("#actTo").value;
  if (from) q.set("from", from);
  if (to) q.set("to", to || from);
  const member = root.querySelector("#actMember").value;
  if (member) q.set("user_id", member);
  const text = root.querySelector("#actQuery").value.trim();
  if (text) q.set("q", text);
  return q;
}

function teamBase() {
  return `/api/teams/${encodeURIComponent(ctx.teamId)}/work-sessions`;
}

async function load() {
  const token = ++loadToken;
  const res = await apiFetch(`${teamBase()}/summary?${params()}`);
  if (token !== loadToken) return; // a newer request won
  const banner = root.querySelector("[data-role='banner']");
  if (!res || !res.ok) {
    let msg = "Could not load activity.";
    if (res) {
      try { msg = (await res.json()).detail || msg; } catch (e) { /* keep the generic message */ }
    }
    banner.innerHTML = `<div class="mgmt-error">${escapeHTML(msg)}</div>`;
    root.querySelector("[data-role='table']").innerHTML = "";
    root.querySelector("[data-role='idle']").innerHTML = "";
    return;
  }
  body = await res.json();
  if (token !== loadToken) return;
  // Reflect the range the server actually used (its own "today" on first load).
  root.querySelector("#actFrom").value = body.date_from;
  root.querySelector("#actTo").value = body.date_to;
  render();
}

function render() {
  const tz = body.timezone;
  const banner = root.querySelector("[data-role='banner']");
  const tableEl = root.querySelector("[data-role='table']");
  const asof = root.querySelector("[data-role='asof']");
  asof.textContent = body.as_of ? `Updated ${formatClock(body.as_of, tz)} · refreshes every minute` : "";

  banner.innerHTML = body.truncated
    ? `<div class="mgmt-error">Showing the most recent rows only - narrow the period or pick a member.</div>`
    : "";

  const empty = emptyMessage(body, tz);
  if (empty) {
    tableEl.innerHTML = `<div class="mgmt-empty">${escapeHTML(empty)}</div>`;
  } else {
    tableEl.innerHTML = `
      <div class="data-table-wrap">
        <table class="task-table data-table">
          <thead><tr>
            <th>Member</th><th>Task / image</th><th>Session time</th>
            <th title="Objects on the task when the first session began">Start</th>
            <th title="Objects on the task when the last session ended">End</th>
            <th title="Change in the task's total object count">Δ</th>
          </tr></thead>
          <tbody>${body.rows.map((r) => rowHTML(r, tz)).join("")}</tbody>
        </table>
      </div>`;
  }

  const idle = body.enabled ? body.idle_members : [];
  root.querySelector("[data-role='idle']").innerHTML = idle.length
    ? `<p class="muted">${idle.length} member${idle.length === 1 ? "" : "s"} with no activity in this period: ${
        idle.map((m) => escapeHTML(m.username)).join(", ")}</p>`
    : "";
}

function rowHTML(r, tz) {
  const task = r.task_name
    ? escapeHTML(r.task_name)
    : `<span class="muted">(deleted task)</span>`;
  return `
    <tr class="act-row" data-key="${escapeHTML(keyOf(r))}" data-user="${r.user_id}" data-task="${r.task_id ?? ""}"
        tabindex="0" role="button" aria-expanded="false" title="Show the individual sessions">
      <td>${escapeHTML(r.username)}</td>
      <td>${task}</td>
      <td>${escapeHTML(formatDuration(r.active_seconds))}
        <div class="muted">${escapeHTML(formatPeriod(r.first_start, r.last_end, tz))} · ${escapeHTML(sessionsLabel(r.sessions))}</div></td>
      <td>${escapeHTML(formatCount(r.objects_start))}</td>
      <td>${escapeHTML(formatCount(r.objects_end))}</td>
      <td>${escapeHTML(formatDelta(r.objects_start, r.objects_end))}</td>
    </tr>`;
}

async function toggle(tr) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("act-detail")) {
    next.remove();
    tr.setAttribute("aria-expanded", "false");
    return;
  }
  const detail = document.createElement("tr");
  detail.className = "act-detail";
  detail.innerHTML = `<td colspan="6"><span class="muted">Loading…</span></td>`;
  tr.after(detail);
  tr.setAttribute("aria-expanded", "true");

  const q = new URLSearchParams({ user_id: tr.dataset.user });
  if (tr.dataset.task) q.set("task_id", tr.dataset.task);
  q.set("from", root.querySelector("#actFrom").value);
  q.set("to", root.querySelector("#actTo").value);
  const res = await apiFetch(`${teamBase()}?${q}`);
  if (!detail.isConnected) return; // collapsed or re-rendered meanwhile
  if (!res || !res.ok) {
    detail.firstElementChild.innerHTML = `<span class="mgmt-error">Could not load sessions.</span>`;
    return;
  }
  const list = await res.json();
  const tz = list.timezone;
  detail.firstElementChild.innerHTML = list.rows.length
    ? `<ul class="act-sessions">${list.rows.map((s) =>
        `<li>${escapeHTML(formatPeriod(s.started_at, s.last_at, tz))} · ${escapeHTML(formatDuration(s.active_seconds))}
          · ${escapeHTML(formatCount(s.objects_start))} → ${escapeHTML(formatCount(s.objects_end))}</li>`).join("")}
        ${list.truncated ? `<li class="muted">More sessions exist than are listed.</li>` : ""}</ul>`
    : `<span class="muted">No sessions.</span>`;
}

export async function mount(container, context) {
  ctx = context;
  body = null;
  // Listeners go on an element this call creates, not on `container`: the router
  // reuses that element across navigations, so listeners bound to it would pile up.
  container.innerHTML = shellHTML();
  root = container.querySelector("[data-role='activity']");
  container = root;

  const select = container.querySelector("#actMember");
  for (const m of ctx.team?.members || []) {
    const opt = document.createElement("option");
    opt.value = String(m.user_id);
    opt.textContent = m.username;
    select.appendChild(opt);
  }

  let debounce = null;
  container.addEventListener("change", (e) => {
    if (e.target.matches("#actFrom, #actTo, #actMember")) load();
  });
  container.addEventListener("input", (e) => {
    if (!e.target.matches("#actQuery")) return;
    clearTimeout(debounce);
    debounce = setTimeout(load, 300);
  });
  container.addEventListener("click", (e) => {
    if (e.target.closest("[data-role='today']")) {
      container.querySelector("#actFrom").value = "";
      container.querySelector("#actTo").value = "";
      load();
      return;
    }
    const tr = e.target.closest("tr.act-row");
    if (tr) toggle(tr);
  });
  container.addEventListener("keydown", (e) => {
    const tr = e.target.closest && e.target.closest("tr.act-row");
    if (tr && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      toggle(tr);
    }
  });

  await load();
}

export function unmount() {
  loadToken++; // abandon anything in flight
  ctx = null;
  root = null;
  body = null;
}
