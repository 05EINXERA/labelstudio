// Team Activity view: per member and task, active time, the clock period and
// the change in object count. Mounted into the Teams page's Activity modal.
//
// Server contract: api/routers/team_activity.py. Only the team's creator is
// authorised; the client check that shows the button is presentation, the
// server decides.
import { apiFetch } from "../../api.js?v=3";
import { escapeHTML } from "../../utils.js?v=3";
import {
  emptyMessage, formatClock, formatCount, formatDayClock, formatDelta,
  formatDuration, formatPeriod, sessionsLabel,
} from "./activity-format.js?v=1";

const SEARCH_DEBOUNCE_MS = 300;

function pad(n) {
  return String(n).padStart(2, "0");
}

function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Minutes east of UTC -- the negation of the JS convention. */
function tzOffsetMinutes() {
  return -new Date().getTimezoneOffset();
}

let current = null;

/**
 * Build the view inside `container`. `ctx` = { teamId, teamName, members: [names] }.
 * Listeners are bound on an element this view creates and removes, never on the
 * container, so remounting cannot accumulate them.
 */
export function mount(container, ctx) {
  unmount();
  const offset = tzOffsetMinutes();
  const root = document.createElement("div");
  root.className = "activity-view";
  root.innerHTML = `
    <div class="activity-toolbar" style="display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;margin-bottom:12px;">
      <label style="display:grid;gap:4px;font-size:.8rem;color:var(--muted);">From
        <input type="date" data-role="from" style="padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);"></label>
      <label style="display:grid;gap:4px;font-size:.8rem;color:var(--muted);">To
        <input type="date" data-role="to" style="padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);"></label>
      <button type="button" class="tool-button" data-role="today">Today</button>
      <label style="display:grid;gap:4px;font-size:.8rem;color:var(--muted);">Member
        <select data-role="member" style="padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);">
          <option value="">All members</option>
          ${(ctx.members || []).map((m) => `<option value="${escapeHTML(m)}">${escapeHTML(m)}</option>`).join("")}
        </select></label>
      <label style="display:grid;gap:4px;font-size:.8rem;color:var(--muted);flex:1;min-width:140px;">Task / image
        <input type="search" data-role="q" placeholder="Search" style="padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);"></label>
      <span data-role="stamp" style="font-size:.78rem;color:var(--muted);margin-left:auto;"></span>
    </div>
    <div data-role="banner" style="display:none;padding:8px 12px;margin-bottom:10px;border-radius:6px;font-size:.85rem;"></div>
    <div data-role="body"></div>
    <p data-role="foot" style="font-size:.8rem;color:var(--muted);margin-top:10px;"></p>`;
  container.replaceChildren(root);

  const q = (role) => root.querySelector(`[data-role="${role}"]`);
  const state = {
    root, ctx, offset, token: 0, timer: null,
    rows: [], detail: new Map(), expanded: new Set(), lastBody: null,
    range: { from: todayISO(), to: todayISO() },
  };
  current = state;
  q("from").value = state.range.from;
  q("to").value = state.range.to;

  const reload = () => load(state);
  q("from").addEventListener("change", () => { state.range.from = q("from").value; if (state.range.to < state.range.from) { state.range.to = state.range.from; q("to").value = state.range.to; } reload(); });
  q("to").addEventListener("change", () => { state.range.to = q("to").value; if (state.range.to < state.range.from) { state.range.from = state.range.to; q("from").value = state.range.from; } reload(); });
  q("today").addEventListener("click", () => {
    state.range = { from: todayISO(), to: todayISO() };
    q("from").value = state.range.from;
    q("to").value = state.range.to;
    reload();
  });
  q("member").addEventListener("change", reload);
  q("q").addEventListener("input", () => {
    clearTimeout(state.timer);
    state.timer = setTimeout(reload, SEARCH_DEBOUNCE_MS);
  });
  root.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-expand]");
    if (btn) toggleRow(state, btn.dataset.expand);
  });

  reload();
}

/** Tear the view down; any in-flight response is ignored. */
export function unmount() {
  if (!current) return;
  clearTimeout(current.timer);
  current.token += 1;
  current.root.remove();
  current = null;
}

function params(state, extra = {}) {
  const p = new URLSearchParams({ from: state.range.from, to: state.range.to, tz_offset: String(state.offset) });
  Object.entries(extra).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") p.set(k, v); });
  return p;
}

function setBanner(state, text, kind = "warn") {
  const el = state.root.querySelector('[data-role="banner"]');
  el.style.display = text ? "block" : "none";
  el.textContent = text || "";
  el.style.background = kind === "error" ? "#ffebee" : "#fff8e1";
  el.style.color = kind === "error" ? "#c62828" : "#8a6d00";
}

async function load(state) {
  const token = ++state.token;
  const member = state.root.querySelector('[data-role="member"]').value;
  const search = state.root.querySelector('[data-role="q"]').value.trim();
  let data = null;
  let error = null;
  try {
    const res = await apiFetch(
      `/api/teams/${state.ctx.teamId}/work-sessions/summary?${params(state, { member, q: search })}`
    );
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail || ""; } catch { /* non-JSON error body */ }
      throw new Error(typeof detail === "string" && detail ? detail : `HTTP ${res.status}`);
    }
    data = await res.json();
  } catch (err) {
    error = err;
  }
  // A newer request (or an unmount) superseded this one: drop it.
  if (token !== state.token || current !== state) return;

  state.detail.clear();
  state.expanded.clear();
  if (error) {
    setBanner(state, `Could not load activity: ${error.message}`, "error");
    renderTable(state, [], emptyMessage({ error: true }));
    return;
  }
  // Reflect the range the server actually used back into the inputs.
  if (data.date_from) { state.range.from = data.date_from; state.root.querySelector('[data-role="from"]').value = data.date_from; }
  if (data.date_to) { state.range.to = data.date_to; state.root.querySelector('[data-role="to"]').value = data.date_to; }
  setBanner(state, data.truncated ? "Showing the first 1000 rows. Narrow the dates or search to see the rest." : "");
  state.root.querySelector('[data-role="stamp"]').textContent = data.as_of ? `updated ${formatClock(data.as_of, state.offset)}` : "";
  state.rows = data.rows;
  renderTable(state, data.rows, emptyMessage({
    enabled: data.enabled, monitoringSince: data.monitoring_since,
    filtered: Boolean(member || search), offsetMinutes: state.offset,
  }));
  const idle = (data.idle_members || []).map((m) => escapeHTML(m.member_name));
  state.root.querySelector('[data-role="foot"]').innerHTML = idle.length
    ? `No recorded activity in this period: ${idle.join(", ")}.` : "";
}

function rowKey(r) {
  return `${r.member_name}|${r.task_id === null || r.task_id === undefined ? `n:${r.task_name || ""}` : r.task_id}`;
}

function renderTable(state, rows, emptyText) {
  const body = state.root.querySelector('[data-role="body"]');
  if (!rows.length) {
    body.innerHTML = `<p style="padding:24px;text-align:center;color:var(--muted);">${escapeHTML(emptyText)}</p>`;
    return;
  }
  const off = state.offset;
  const cell = "padding:8px 10px;border-bottom:1px solid var(--line);";
  const html = rows.map((r) => {
    const key = rowKey(r);
    const open = state.expanded.has(key);
    return `<tr>
      <td style="${cell}"><button type="button" data-expand="${escapeHTML(key)}" aria-expanded="${open}" title="Show sessions" style="background:none;border:none;cursor:pointer;color:var(--accent-dark);font-weight:600;">${open ? "▾" : "▸"} ${escapeHTML(r.member_name)}</button></td>
      <td style="${cell}">${escapeHTML(r.task_name || (r.task_id ? `Task ${r.task_id}` : "Deleted task"))}</td>
      <td style="${cell}"><strong>${formatDuration(r.active_seconds)}</strong><div style="font-size:.75rem;color:var(--muted);">${escapeHTML(formatPeriod(r.first_start, r.last_end, off))} · ${sessionsLabel(r.sessions)}</div></td>
      <td style="${cell}">${formatCount(r.objects_start)}</td>
      <td style="${cell}">${formatCount(r.objects_end)}</td>
      <td style="${cell}">${formatDelta(r.objects_start, r.objects_end)}</td>
    </tr>${open ? detailRow(state, key) : ""}`;
  }).join("");
  const head = ["Member", "Task / image", "Session time", "Objects at start", "Objects at end", "Change"]
    .map((h) => `<th style="text-align:left;padding:8px 10px;border-bottom:2px solid var(--line);font-size:.78rem;color:var(--muted);">${h}</th>`).join("");
  body.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:.88rem;"><thead><tr>${head}</tr></thead><tbody>${html}</tbody></table>`;
}

function detailRow(state, key) {
  const d = state.detail.get(key);
  let inner;
  if (!d || d.loading) inner = "Loading sessions…";
  else if (d.error) inner = `Could not load sessions: ${escapeHTML(d.error)}`;
  else {
    inner = d.rows.map((s) =>
      `${escapeHTML(formatDayClock(s.started_at, state.offset))} – ${escapeHTML(formatClock(s.last_at, state.offset))}
       · ${formatDuration(s.active_seconds)} · objects ${formatCount(s.objects_start)} → ${formatCount(s.objects_end)} (${formatDelta(s.objects_start, s.objects_end)})`
    ).join("<br>") || "No sessions.";
    if (d.truncated) inner += "<br>Showing the first 200 sessions.";
  }
  return `<tr><td></td><td colspan="5" style="padding:6px 10px 12px;font-size:.82rem;color:var(--muted);border-bottom:1px solid var(--line);">${inner}</td></tr>`;
}

async function toggleRow(state, key) {
  if (current !== state) return;
  if (state.expanded.has(key)) {
    state.expanded.delete(key);
    state.detail.delete(key);
    renderTable(state, state.rows, "");
    return;
  }
  const row = state.rows.find((r) => rowKey(r) === key);
  if (!row) return;
  state.expanded.add(key);
  state.detail.set(key, { loading: true });
  renderTable(state, state.rows, "");
  const token = state.token;
  const extra = row.task_id === null || row.task_id === undefined
    ? { member: row.member_name, task_name: row.task_name }
    : { member: row.member_name, task_id: row.task_id };
  let result;
  try {
    const res = await apiFetch(`/api/teams/${state.ctx.teamId}/work-sessions?${params(state, extra)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    result = await res.json();
  } catch (err) {
    result = { error: err.message };
  }
  if (token !== state.token || current !== state || !state.expanded.has(key)) return;
  state.detail.set(key, result);
  renderTable(state, state.rows, "");
}
