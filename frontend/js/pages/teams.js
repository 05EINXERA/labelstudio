import { apiFetch } from "../api.js?v=3";
import { escapeHTML } from "../utils.js?v=1";
import { createDataTable } from "../components/data-table.js?v=2";

const els = {
  user: document.getElementById("currentUser"),
  logout: document.getElementById("logoutBtn"),
  error: document.getElementById("errorBanner"),
  newTeamBtn: document.getElementById("newTeamBtn"),
  newMemberBtn: document.getElementById("newMemberBtn"),
  
  teamModal: document.getElementById("teamModal"),
  teamModalClose: document.getElementById("teamModalClose"),
  teamForm: document.getElementById("teamForm"),
  teamFormName: document.getElementById("teamFormName"),
  teamFormCancel: document.getElementById("teamFormCancel"),
  
  newMemberModal: document.getElementById("newMemberModal"),
  newMemberModalClose: document.getElementById("newMemberModalClose"),
  newMemberForm: document.getElementById("newMemberForm"),
  newMemberFormName: document.getElementById("newMemberFormName"),
  newMemberFormTeams: document.getElementById("newMemberFormTeams"),
  newMemberFormCancel: document.getElementById("newMemberFormCancel"),
  
  assignModal: document.getElementById("assignModal"),
  assignModalClose: document.getElementById("assignModalClose"),
  assignForm: document.getElementById("assignForm"),
  assignFormName: document.getElementById("assignFormName"),
  assignMemberName: document.getElementById("assignMemberName"),
  assignFormTeams: document.getElementById("assignFormTeams"),
  assignFormCancel: document.getElementById("assignFormCancel"),
  
  teamModalError: document.getElementById("teamModalError"),
  newMemberModalError: document.getElementById("newMemberModalError"),
  assignModalError: document.getElementById("assignModalError"),
  
  teamsMount: document.getElementById("teamsMount"),
  membersMount: document.getElementById("membersMount"),
  teamSearchInput: document.getElementById("teamSearchInput"),
  memberSearchInput: document.getElementById("memberSearchInput"),
  selectedTeamBadge: document.getElementById("selectedTeamBadge"),
  selectedTeamName: document.getElementById("selectedTeamName"),
  clearTeamFilterBtn: document.getElementById("clearTeamFilterBtn"),

  exportSessionsBtn: document.getElementById("exportSessionsBtn"),
  exportSessionsModal: document.getElementById("exportSessionsModal"),
  exportSessionsModalClose: document.getElementById("exportSessionsModalClose"),
  exportSessionsModalError: document.getElementById("exportSessionsModalError"),
  exportSessionsForm: document.getElementById("exportSessionsForm"),
  exportSessionsCancel: document.getElementById("exportSessionsCancel"),
  exportStartDate: document.getElementById("exportStartDate"),
  exportEndDate: document.getElementById("exportEndDate"),

  memberTasksModal: document.getElementById("memberTasksModal"),
  memberTasksModalClose: document.getElementById("memberTasksModalClose"),
  memberTasksName: document.getElementById("memberTasksName"),
  memberTasksModalBody: document.getElementById("memberTasksModalBody"),

  transferModal: document.getElementById("transferModal"),
  transferModalClose: document.getElementById("transferModalClose"),
  transferModalError: document.getElementById("transferModalError"),
  transferForm: document.getElementById("transferForm"),
  transferTeamId: document.getElementById("transferTeamId"),
  transferTeamName: document.getElementById("transferTeamName"),
  transferNewOwnerSelect: document.getElementById("transferNewOwnerSelect"),
  transferFormCancel: document.getElementById("transferFormCancel"),
};

let teamsTable;
let membersTable;
let teamsCache = [];
let membersCache = [];
let selectedTeam = null;
let myCreatedTeamIds = new Set();

// Session history, keyed by member name: { loading, error, date, data }.
// Fetched lazily when a row is expanded so the list view stays one request.
const sessionHistory = new Map();

/** Minutes east of UTC — the negation of the JS convention. */
function tzOffsetMinutes() {
  return -new Date().getTimezoneOffset();
}

function todayISO() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  if (m) return `${m}m`;
  return "<1m";
}

function formatClock(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function isMemberOwnedByMe(member) {
  return (member.teams || []).some((t) => myCreatedTeamIds.has(t.id));
}

/**
 * Download the login history as CSV.
 *
 * Goes through apiFetch rather than a plain <a href> so the request carries the
 * auth/annotator headers, then saves the response as a blob.
 *
 * @param {object} opts  { name? } omit name to export every visible member;
 *                       { start?, end? } local YYYY-MM-DD, default today.
 */
async function downloadSessionsCsv({ name, start, end } = {}) {
  const params = new URLSearchParams({ tz_offset: String(tzOffsetMinutes()) });
  if (name) params.set("name", name);
  if (start) params.set("start", start);
  if (end) params.set("end", end);

  try {
    const res = await apiFetch(`/api/team/sessions/export?${params}`);
    if (!res) return;
    if (!res.ok) {
      let detail = `Export failed (${res.status}).`;
      try {
        const body = await res.json();
        if (body && body.detail) detail = body.detail;
      } catch (_) {
        // Non-JSON error body; the status-based message above is enough.
      }
      showError(detail);
      return;
    }

    // Prefer the filename the server chose, so single-member and range
    // exports stay distinguishable on disk.
    const disp = res.headers.get("Content-Disposition") || "";
    const match = disp.match(/filename="?([^"';]+)"?/i);
    const filename = match ? match[1] : "login-history.csv";

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Revoke on the next tick: revoking synchronously can cancel the download
    // in some browsers before it starts.
    setTimeout(() => URL.revokeObjectURL(url), 0);
    clearError();
  } catch (err) {
    showError("Could not download the spreadsheet: " + err.message);
  }
}

async function fetchSessions(name, date) {
  const params = new URLSearchParams({ tz_offset: String(tzOffsetMinutes()) });
  if (date) params.set("date", date);
  sessionHistory.set(name, { loading: true, date: date || todayISO() });
  membersTable.render();
  try {
    const res = await apiFetch(`/api/team/${encodeURIComponent(name)}/sessions?${params}`);
    if (!res) return;
    if (!res.ok) {
      const detail = res.status === 403
        ? "You can only view history for members of teams you created."
        : `Could not load history (${res.status}).`;
      sessionHistory.set(name, { error: detail, date: date || todayISO() });
    } else {
      const data = await res.json();
      sessionHistory.set(name, { data, date: data.date });
    }
  } catch (err) {
    sessionHistory.set(name, { error: err.message, date: date || todayISO() });
  }
  membersTable.render();
}

function renderSessionDetail(member) {
  const entry = sessionHistory.get(member.name);
  const day = entry?.date || todayISO();

  const header = `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px;">
      <strong style="font-size:0.85rem;">Login history — ${escapeHTML(member.name)}</strong>
      <input type="date" data-action="session-date" value="${escapeHTML(day)}" max="${escapeHTML(todayISO())}"
        style="font-size:0.78rem;padding:2px 6px;border:1px solid var(--line);border-radius:4px;">
      <button type="button" data-action="export-member" class="pill"
        title="Download ${escapeHTML(member.name)}'s sessions for this day as a spreadsheet"
        style="cursor:pointer;font-size:0.75rem;padding:2px 10px;border:1px solid var(--line);">⤓ CSV</button>
    </div>`;

  let body;
  if (entry?.loading) {
    body = `<div style="color:var(--muted);font-size:0.8rem;">Loading…</div>`;
  } else if (entry?.error) {
    body = `<div style="color:var(--danger,#b3261e);font-size:0.8rem;">${escapeHTML(entry.error)}</div>`;
  } else if (!entry?.data || !entry.data.sessions.length) {
    body = `<div style="color:var(--muted);font-size:0.8rem;">No logins recorded on this day.</div>`;
  } else {
    const rows = entry.data.sessions.map((s) => {
      let end;
      if (s.is_open) {
        end = `<span style="color:#2e7d32;font-weight:600;">still logged in</span>`;
      } else if (s.ended_reason === "inactive") {
        // Swept: the stamp is the last heartbeat, not a click on Log out.
        end = `${escapeHTML(formatClock(s.logout_at))} <span style="color:var(--muted);font-size:0.72rem;">(inactive)</span>`;
      } else {
        end = escapeHTML(formatClock(s.logout_at));
      }
      return `<tr>
        <td style="padding:3px 12px 3px 0;">${escapeHTML(formatClock(s.login_at))}</td>
        <td style="padding:3px 12px 3px 0;color:var(--muted);">→</td>
        <td style="padding:3px 12px 3px 0;">${end}</td>
        <td style="padding:3px 0;color:var(--muted);">${escapeHTML(formatDuration(s.duration_seconds))}</td>
      </tr>`;
    }).join("");
    body = `<table style="font-size:0.8rem;border-collapse:collapse;">
        <thead><tr style="color:var(--muted);font-size:0.72rem;text-transform:uppercase;">
          <th style="text-align:left;padding-right:12px;font-weight:600;">In</th><th></th>
          <th style="text-align:left;padding-right:12px;font-weight:600;">Out</th>
          <th style="text-align:left;font-weight:600;">Duration</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div style="margin-top:8px;font-size:0.8rem;font-weight:600;">Total: ${escapeHTML(formatDuration(entry.data.total_seconds))}</div>`;
  }

  return `<div style="padding:10px 6px 12px 18px;border-left:2px solid var(--accent);background:rgba(128,128,128,0.04);">${header}${body}</div>`;
}

function showError(msg) {
  els.error.textContent = msg;
  els.error.style.display = "block";
}

function clearError() {
  els.error.style.display = "none";
}

function selectOrToggleTeam(team) {
  if (selectedTeam && selectedTeam.id === team.id) {
    selectedTeam = null;
  } else {
    selectedTeam = team;
  }
  updateTeamFilterUI();
  renderMembers();
  teamsTable.render();
}

function updateTeamFilterUI() {
  if (selectedTeam) {
    const memberCount = membersCache.filter(
      (m) => m.teams && m.teams.some((t) => t.id === selectedTeam.id)
    ).length;
    if (els.selectedTeamBadge) els.selectedTeamBadge.style.display = "inline-flex";
    if (els.selectedTeamName) {
      els.selectedTeamName.textContent = `${selectedTeam.name} (${memberCount} member${memberCount === 1 ? "" : "s"})`;
    }
  } else {
    if (els.selectedTeamBadge) els.selectedTeamBadge.style.display = "none";
  }
}

function renderMembers() {
  let rows = membersCache;
  if (selectedTeam) {
    rows = rows.filter((r) => r.teams && r.teams.some((t) => t.id === selectedTeam.id));
  }
  membersTable.setRows(rows);
  refreshOpenSessionPanels(rows);
}

/** Keep expanded panels current across the 15s auto-refresh. */
function refreshOpenSessionPanels(rows) {
  rows.forEach((r) => {
    if (!membersTable.isExpanded(r.name)) return;
    const entry = sessionHistory.get(r.name);
    // Don't stack a second request on one already in flight, and leave a panel
    // showing a past day alone — only "today" goes stale on its own.
    if (entry?.loading) return;
    if (entry?.date && entry.date !== todayISO()) return;
    fetchSessions(r.name, entry?.date);
  });
}

const ICON_DELETE = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>`;
const ICON_EDIT = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/></svg>`;
const ICON_TRANSFER = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="m18 8 3 3-3 3"/><path d="M21 11H13"/></svg>`;

function initTables() {
  teamsTable = createDataTable({
    mount: els.teamsMount,
    rowId: (r) => r.id,
    emptyMessage: "No teams created yet.",
    rowClass: (r) => (selectedTeam && selectedTeam.id === r.id ? "selected-team-row is-selected-row" : ""),
    onRowClick: (row) => {
      selectOrToggleTeam(row);
    },
    columns: [
      {
        key: "name",
        label: "Team Name",
        render: (r) => {
          const isSelected = selectedTeam && selectedTeam.id === r.id;
          const memberCount = membersCache.filter((m) => m.teams && m.teams.some((t) => t.id === r.id)).length;
          return `<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
            <button type="button" class="cell-link team-name-btn" data-action="filter-team" title="Click to view members of ${escapeHTML(r.name)}" style="background:none;border:none;padding:0;cursor:pointer;font-weight:${isSelected ? '700' : '600'};color:${isSelected ? 'var(--accent)' : 'var(--accent-dark)'};text-decoration:${isSelected ? 'underline' : 'none'};text-align:left;display:inline-flex;align-items:center;gap:6px;">
              ${escapeHTML(r.name)}
              ${isSelected ? '<span class="pill is-completed" style="font-size:0.7rem;padding:1px 6px;">Active</span>' : ''}
            </button>
            <span class="pill" style="font-size:0.75rem;padding:2px 7px;color:var(--muted);">${memberCount} member${memberCount === 1 ? '' : 's'}</span>
          </div>`;
        }
      },
      {
        key: "actions", label: "", sortable: false, align: "right", width: "80px",
        render: (r) => {
          const datasetUsername = localStorage.getItem('dataset_username');
          if (r.creator === datasetUsername) {
            return `<div class="row-actions"><button type="button" data-action="transfer" title="Transfer Team Ownership">${ICON_TRANSFER}</button><button type="button" data-action="delete" class="danger" title="Delete Team">${ICON_DELETE}</button></div>`;
          }
          return '';
        }
      },
    ],
  });

  teamsTable.onAction("filter-team", (row) => {
    selectOrToggleTeam(row);
  });

  teamsTable.onAction("transfer", (row) => {
    openTransferModal(row);
  });

  teamsTable.onAction("delete", async (row) => {
      if (!confirm(`Delete team "${row.name}"? Members will be unassigned.`)) return;
      try {
        const res = await apiFetch(`/api/teams/${row.id}`, { method: "DELETE" });
        if (!res.ok) throw new Error(await res.text());
        if (selectedTeam && selectedTeam.id === row.id) {
          selectedTeam = null;
        }
        loadData();
      } catch (err) {
        showError("Failed to delete team: " + err.message);
      }
  });

  membersTable = createDataTable({
    mount: els.membersMount,
    rowId: (r) => r.name,
    emptyMessage: "No team members found.",
    rowDetail: (r) => renderSessionDetail(r),
    matches: (row, q) => {
      const query = q.trim().toLowerCase();
      if (!query) return true;
      const memberName = String(row.name || "").toLowerCase();
      const teamNames = (row.teams || []).map((t) => String(t.name || "").toLowerCase()).join(" ");
      // Match member/annotator name as well as team names
      return memberName.includes(query) || teamNames.includes(query);
    },
    columns: [
      { key: "name", label: "Annotator", render: (r) => `<strong style="color:var(--ink);">${escapeHTML(r.name)}</strong>` },
      {
        key: "status",
        label: "Status",
        width: "110px",
        render: (r) => {
          if (r.is_logged_in === true) {
            return `<span class="pill is-completed" style="display:inline-flex;align-items:center;gap:6px;font-size:0.75rem;padding:2px 8px;font-weight:600;"><span style="width:7px;height:7px;border-radius:50%;background:#2e7d32;display:inline-block;box-shadow:0 0 0 2px rgba(46,125,50,0.2);"></span>Logged in</span>`;
          }
          return `<span class="pill" style="display:inline-flex;align-items:center;gap:6px;font-size:0.75rem;padding:2px 8px;color:var(--muted);background:rgba(128,128,128,0.08);"><span style="width:7px;height:7px;border-radius:50%;background:var(--muted);display:inline-block;"></span>Offline</span>`;
        }
      },
      {
        key: "seconds_today",
        label: "Sessions",
        width: "120px",
        render: (r) => {
          // Hours are only visible to the owner of a team the member is in,
          // matching the Tasks column and the endpoint's own check.
          if (!isMemberOwnedByMe(r)) return `<span style="color:var(--muted);font-size:0.8rem;">—</span>`;
          const open = membersTable && membersTable.isExpanded(r.name);
          const total = r.seconds_today ? formatDuration(r.seconds_today) : "—";
          return `<button type="button" data-action="toggle-sessions" class="pill"
            title="Show today's login and logout times for ${escapeHTML(r.name)}"
            style="cursor:pointer;font-size:0.75rem;padding:2px 10px;border:1px solid var(--line);display:inline-flex;align-items:center;gap:5px;">
            ${escapeHTML(total)}<span style="font-size:0.6rem;">${open ? "▲" : "▼"}</span></button>`;
        }
      },
      {
        key: "tasks",
        label: "Tasks",
        width: "80px",
        sortable: false,
        render: (r) => {
          // Only show task count button to the team creator
          const memberTeamIds = (r.teams || []).map(t => t.id);
          const isOwner = memberTeamIds.some(id => myCreatedTeamIds.has(id));
          if (!isOwner) return `<span style="color:var(--muted);font-size:0.8rem;">—</span>`;
          return `<button type="button" data-action="view-tasks" class="pill" style="cursor:pointer;font-size:0.75rem;padding:2px 10px;border:1px solid var(--line);" title="View tasks assigned to ${escapeHTML(r.name)}">View tasks</button>`;
        }
      },
      {
        key: "team_name",
        label: "Teams",
        render: (r) => {
          if (!r.teams || r.teams.length === 0) {
            return `<span style="color:var(--muted);">Unassigned</span>`;
          }
          return r.teams
            .map((t) => {
              const isSelected = selectedTeam && selectedTeam.id === t.id;
              return `<button type="button" class="pill ${isSelected ? 'is-completed' : ''}" data-action="filter-team-by-id" data-team-id="${t.id}" title="Filter by ${escapeHTML(t.name)}" style="cursor:pointer;border:1px solid ${isSelected ? 'transparent' : 'var(--line)'};font-weight:${isSelected ? '700' : '500'};">${escapeHTML(t.name)}</button>`;
            })
            .join(" ");
        }
      },
      {
        key: "actions", label: "", sortable: false, align: "right", width: "50px",
        render: (r) => {
          return `<div class="row-actions"><button type="button" data-action="assign" title="Assign to Team">${ICON_EDIT}</button></div>`;
        }
      },
    ],
  });

  membersTable.onAction("assign", (row) => {
    openAssignModal(row);
  });

  membersTable.onAction("export-member", (row) => {
    // Export whichever day the open panel is showing, not always today.
    const day = sessionHistory.get(row.name)?.date || todayISO();
    downloadSessionsCsv({ name: row.name, start: day, end: day });
  });

  membersTable.onAction("toggle-sessions", (row) => {
    const nowOpen = membersTable.toggleExpanded(row.name);
    // Fetch on first open only; a re-open reuses what is already cached.
    if (nowOpen && !sessionHistory.has(row.name)) fetchSessions(row.name);
  });

  // The date picker lives inside the expanded panel, so it is delegated here
  // rather than bound at render time (rows are replaced wholesale).
  els.membersMount.addEventListener("change", (e) => {
    const input = e.target.closest('[data-action="session-date"]');
    if (!input) return;
    const name = input.closest("tr")?.dataset.detailFor;
    if (name) fetchSessions(name, input.value);
  });

  membersTable.onAction("view-tasks", (row) => {
    openMemberTasksModal(row);
  });

  membersTable.onAction("filter-team-by-id", (row, btn) => {
    const teamId = parseInt(btn.dataset.teamId, 10);
    const team = teamsCache.find((t) => t.id === teamId);
    if (team) {
      selectOrToggleTeam(team);
    }
  });

  if (els.clearTeamFilterBtn) {
    els.clearTeamFilterBtn.addEventListener("click", () => {
      selectedTeam = null;
      updateTeamFilterUI();
      renderMembers();
      teamsTable.render();
    });
  }

  if (els.teamSearchInput) {
    els.teamSearchInput.addEventListener("input", (e) => {
      teamsTable.setQuery(e.target.value);
    });
  }

  if (els.memberSearchInput) {
    els.memberSearchInput.addEventListener("input", (e) => {
      membersTable.setQuery(e.target.value);
    });
  }
}

async function loadData() {
  try {
    const [resTeams, resMembers] = await Promise.all([
      apiFetch("/api/teams"),
      apiFetch(`/api/team?tz_offset=${tzOffsetMinutes()}`),
    ]);
    
    if (!resTeams || !resMembers) return;
    
    if (!resTeams.ok || !resMembers.ok) {
      showError(`Could not load data.`);
      return;
    }
    
    clearError();
    const teams = await resTeams.json();
    const members = await resMembers.json();
    
    teamsCache = teams;
    membersCache = members;

    // Track which teams I created so the Tasks column renders correctly
    const datasetUsernameForCreator = localStorage.getItem('dataset_username');
    myCreatedTeamIds = new Set(
      teams.filter(t => t.creator === datasetUsernameForCreator).map(t => t.id)
    );

    if (selectedTeam && !teams.some((t) => t.id === selectedTeam.id)) {
      selectedTeam = null;
    }

    updateTeamFilterUI();
    teamsTable.setRows(teams);
    renderMembers();
    
    // Update team checkboxes in assign modal
    els.assignFormTeams.innerHTML = '';
    const datasetUsername = localStorage.getItem('dataset_username');
    teams.forEach(t => {
      const label = document.createElement('label');
      label.style.display = 'flex';
      label.style.gap = '8px';
      label.style.alignItems = 'center';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = t.id;
      if (t.creator !== datasetUsername) {
        cb.disabled = true;
        cb.title = "You can only modify teams you created";
        label.title = "You can only modify teams you created";
        label.style.opacity = "0.6";
        label.style.cursor = "not-allowed";
      }
      label.appendChild(cb);
      label.appendChild(document.createTextNode(t.name));
      els.assignFormTeams.appendChild(label);
    });
    
    // Update team checkboxes in new member modal
    els.newMemberFormTeams.innerHTML = '';
    teams.forEach(t => {
      const label = document.createElement('label');
      label.style.display = 'flex';
      label.style.gap = '8px';
      label.style.alignItems = 'center';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = t.id;
      if (t.creator !== datasetUsername) {
        cb.disabled = true;
        cb.title = "You can only modify teams you created";
        label.title = "You can only modify teams you created";
        label.style.opacity = "0.6";
        label.style.cursor = "not-allowed";
      }
      label.appendChild(cb);
      label.appendChild(document.createTextNode(t.name));
      els.newMemberFormTeams.appendChild(label);
    });
    
  } catch (err) {
    console.error("Failed to load", err);
    showError("Could not reach the server.");
  }
}

// --- Modals ---

function openTeamModal() {
  els.teamForm.reset();
  els.teamModalError.style.display = "none";
  els.teamModal.classList.add("is-active");
  els.teamFormName.focus();
}

function closeTeamModal() {
  els.teamModal.classList.remove("is-active");
}

els.newTeamBtn.addEventListener("click", openTeamModal);
els.teamModalClose.addEventListener("click", closeTeamModal);
els.teamFormCancel.addEventListener("click", closeTeamModal);

els.teamForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = { name: els.teamFormName.value.trim() };
  try {
    const res = await apiFetch("/api/teams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json();
      els.teamModalError.textContent = err.detail || "Failed to save team";
      els.teamModalError.style.display = "block";
      return;
    }
    closeTeamModal();
    loadData();
  } catch (err) {
    els.teamModalError.textContent = "Connection error";
    els.teamModalError.style.display = "block";
  }
});

function openNewMemberModal() {
  els.newMemberForm.reset();
  els.newMemberModalError.style.display = "none";
  els.newMemberModal.classList.add("is-active");
  els.newMemberFormName.focus();
}

function closeNewMemberModal() {
  els.newMemberModal.classList.remove("is-active");
}

els.newMemberBtn.addEventListener("click", openNewMemberModal);
els.newMemberModalClose.addEventListener("click", closeNewMemberModal);
els.newMemberFormCancel.addEventListener("click", closeNewMemberModal);

els.newMemberForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = els.newMemberFormName.value.trim();
  const teamIds = Array.from(els.newMemberFormTeams.querySelectorAll('input:checked')).map(cb => parseInt(cb.value, 10));
  try {
    const res = await apiFetch("/api/team", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, team_ids: teamIds })
    });
    if (!res.ok) {
      const err = await res.json();
      els.newMemberModalError.textContent = err.detail || "Failed to add team member";
      els.newMemberModalError.style.display = "block";
      return;
    }
    closeNewMemberModal();
    loadData();
  } catch (err) {
    els.newMemberModalError.textContent = "Connection error";
    els.newMemberModalError.style.display = "block";
  }
});

function openAssignModal(member) {
  els.assignForm.reset();
  els.assignModalError.style.display = "none";
  els.assignFormName.value = member.name;
  els.assignMemberName.textContent = member.name;
  els.assignFormTeams.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.checked = member.teams && member.teams.some(t => t.id === parseInt(cb.value, 10));
  });
  els.assignModal.classList.add("is-active");
}

function closeAssignModal() {
  els.assignModal.classList.remove("is-active");
}

// --- Member Tasks Modal ---

function statusBadge(status) {
  const s = (status || "").toLowerCase();
  if (s === "completed" || s === "done") {
    return `<span class="pill is-completed" style="font-size:0.72rem;padding:1px 7px;">${escapeHTML(status)}</span>`;
  }
  if (s === "in progress" || s === "in_progress") {
    return `<span class="pill" style="font-size:0.72rem;padding:1px 7px;background:rgba(37,99,235,0.08);color:#1d4ed8;border:1px solid rgba(37,99,235,0.2);">${escapeHTML(status)}</span>`;
  }
  return `<span class="pill" style="font-size:0.72rem;padding:1px 7px;color:var(--muted);">${escapeHTML(status || "New")}</span>`;
}

function openExportSessionsModal() {
  const today = todayISO();
  // Default to the last 7 days, the common "what did the team do this week" pull.
  const weekAgo = new Date();
  weekAgo.setDate(weekAgo.getDate() - 6);
  const pad = (n) => String(n).padStart(2, "0");
  els.exportStartDate.value = `${weekAgo.getFullYear()}-${pad(weekAgo.getMonth() + 1)}-${pad(weekAgo.getDate())}`;
  els.exportEndDate.value = today;
  els.exportStartDate.max = today;
  els.exportEndDate.max = today;
  els.exportSessionsModalError.style.display = "none";
  els.exportSessionsModal.classList.add("is-active");
}

function closeExportSessionsModal() {
  els.exportSessionsModal.classList.remove("is-active");
}

/** Fill the date inputs from one of the quick-range buttons. */
function applyExportPreset(preset) {
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const today = new Date();
  let start = new Date();
  if (preset === "today") {
    start = today;
  } else if (preset === "month") {
    start = new Date(today.getFullYear(), today.getMonth(), 1);
  } else {
    // A numeric preset counts back N days inclusive of today.
    start.setDate(today.getDate() - (Number(preset) - 1));
  }
  els.exportStartDate.value = iso(start);
  els.exportEndDate.value = iso(today);
}

async function openMemberTasksModal(member) {
  els.memberTasksName.textContent = member.name;
  els.memberTasksModalBody.innerHTML = `<p style="color:var(--muted);font-size:0.9rem;text-align:center;">Loading…</p>`;
  els.memberTasksModal.classList.add("is-active");

  try {
    const res = await apiFetch(`/api/team/${encodeURIComponent(member.name)}/tasks`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      els.memberTasksModalBody.innerHTML = `<p style="color:#c62828;text-align:center;">${escapeHTML(err.detail || "Failed to load tasks")}</p>`;
      return;
    }
    const tasks = await res.json();
    if (!tasks.length) {
      els.memberTasksModalBody.innerHTML = `<p style="color:var(--muted);font-size:0.9rem;text-align:center;">No tasks assigned to ${escapeHTML(member.name)}.</p>`;
      return;
    }
    els.memberTasksModalBody.innerHTML = `
      <table style="width:100%;border-collapse:collapse;font-size:0.87rem;">
        <thead>
          <tr style="border-bottom:2px solid var(--line);">
            <th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600;">Task</th>
            <th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600;">Project</th>
            <th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600;width:100px;">Status</th>
          </tr>
        </thead>
        <tbody>
          ${tasks.map(t => `
            <tr style="border-bottom:1px solid var(--line);">
              <td style="padding:7px 8px;color:var(--ink);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHTML(t.description || 'Task #' + t.id)}">
                ${escapeHTML(t.description || "Task #" + t.id)}
              </td>
              <td style="padding:7px 8px;color:var(--ink-light);">${escapeHTML(t.project_name || "")}</td>
              <td style="padding:7px 8px;">${statusBadge(t.status)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
      <p style="margin-top:10px;font-size:0.8rem;color:var(--muted);text-align:right;">${tasks.length} task${tasks.length === 1 ? "" : "s"} total</p>
    `;
  } catch (err) {
    els.memberTasksModalBody.innerHTML = `<p style="color:#c62828;text-align:center;">Connection error</p>`;
  }
}

function closeMemberTasksModal() {
  els.memberTasksModal.classList.remove("is-active");
}

if (els.exportSessionsBtn) {
  els.exportSessionsBtn.addEventListener("click", openExportSessionsModal);
  els.exportSessionsModalClose.addEventListener("click", closeExportSessionsModal);
  els.exportSessionsCancel.addEventListener("click", closeExportSessionsModal);
  els.exportSessionsModal.addEventListener("click", (e) => {
    if (e.target === els.exportSessionsModal) closeExportSessionsModal();
  });

  els.exportSessionsForm.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-range]");
    if (btn) applyExportPreset(btn.dataset.range);
  });

  els.exportSessionsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const start = els.exportStartDate.value;
    const end = els.exportEndDate.value;
    if (start && end && start > end) {
      els.exportSessionsModalError.textContent = "The start date must not be after the end date.";
      els.exportSessionsModalError.style.display = "block";
      return;
    }
    els.exportSessionsModalError.style.display = "none";
    closeExportSessionsModal();
    await downloadSessionsCsv({ start, end });
  });
}

els.memberTasksModalClose.addEventListener("click", closeMemberTasksModal);
els.memberTasksModal.addEventListener("click", (e) => {
  if (e.target === els.memberTasksModal) closeMemberTasksModal();
});

els.assignModalClose.addEventListener("click", closeAssignModal);
els.assignFormCancel.addEventListener("click", closeAssignModal);

els.assignForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = els.assignFormName.value;
  const teamIds = Array.from(els.assignFormTeams.querySelectorAll('input:checked')).map(cb => parseInt(cb.value, 10));
  try {
    const res = await apiFetch(`/api/team/${encodeURIComponent(name)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team_ids: teamIds })
    });
    if (!res.ok) {
      const err = await res.json();
      els.assignModalError.textContent = err.detail || "Failed to assign team";
      els.assignModalError.style.display = "block";
      return;
    }
    closeAssignModal();
    loadData();
  } catch (err) {
    els.assignModalError.textContent = "Connection error";
    els.assignModalError.style.display = "block";
  }
});

// --- Transfer Ownership Modal ---

function openTransferModal(team) {
  els.transferForm.reset();
  els.transferModalError.style.display = "none";
  els.transferTeamId.value = team.id;
  els.transferTeamName.textContent = team.name;

  const datasetUsername = localStorage.getItem('dataset_username');
  const candidates = membersCache.filter(m => m.name !== team.creator && m.name !== datasetUsername);

  els.transferNewOwnerSelect.innerHTML = '';
  if (candidates.length === 0) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = 'No other members available';
    opt.disabled = true;
    opt.selected = true;
    els.transferNewOwnerSelect.appendChild(opt);
  } else {
    const inTeam = [];
    const notInTeam = [];
    candidates.forEach(m => {
      if (m.teams && m.teams.some(t => t.id === team.id)) {
        inTeam.push(m);
      } else {
        notInTeam.push(m);
      }
    });

    if (inTeam.length > 0) {
      const group = document.createElement('optgroup');
      group.label = 'Team Members';
      inTeam.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.name;
        opt.textContent = m.name;
        group.appendChild(opt);
      });
      els.transferNewOwnerSelect.appendChild(group);
    }

    if (notInTeam.length > 0) {
      const group = document.createElement('optgroup');
      group.label = 'Other Members';
      notInTeam.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.name;
        opt.textContent = m.name;
        group.appendChild(opt);
      });
      els.transferNewOwnerSelect.appendChild(group);
    }
  }

  els.transferModal.classList.add("is-active");
}

function closeTransferModal() {
  els.transferModal.classList.remove("is-active");
}

els.transferModalClose.addEventListener("click", closeTransferModal);
els.transferFormCancel.addEventListener("click", closeTransferModal);
els.transferModal.addEventListener("click", (e) => {
  if (e.target === els.transferModal) closeTransferModal();
});

els.transferForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const teamId = els.transferTeamId.value;
  const newOwner = els.transferNewOwnerSelect.value;
  if (!newOwner) {
    els.transferModalError.textContent = "Please select a member to transfer ownership to";
    els.transferModalError.style.display = "block";
    return;
  }

  if (!confirm(`Are you sure you want to transfer ownership of "${els.transferTeamName.textContent}" to ${newOwner}? All projects belonging to this team will also be transferred to ${newOwner}.`)) {
    return;
  }

  try {
    const res = await apiFetch(`/api/teams/${teamId}/transfer-ownership`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_owner: newOwner })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      els.transferModalError.textContent = err.detail || "Failed to transfer ownership";
      els.transferModalError.style.display = "block";
      return;
    }
    closeTransferModal();
    loadData();
  } catch (err) {
    els.transferModalError.textContent = "Connection error";
    els.transferModalError.style.display = "block";
  }
});

// --- Boot ---
els.user.textContent = localStorage.getItem("dataset_username") || "Annotator";
els.logout.addEventListener("click", async () => {
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } catch (_) {
    /* best effort */
  }
  localStorage.removeItem("logged_in");
  localStorage.removeItem("dataset_username");
  localStorage.removeItem("access_token");
  window.location.replace("/");
});

initTables();
teamsTable.showLoading(4);
membersTable.showLoading(4);
loadData();

// Auto-refresh team & member data periodically so team members see real-time presence changes
setInterval(() => {
  if (document.visibilityState === "visible") {
    loadData();
  }
}, 15000);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    loadData();
  }
});
