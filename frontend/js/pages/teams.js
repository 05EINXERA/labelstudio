import { apiFetch } from "../api.js?v=1";
import { escapeHTML } from "../utils.js?v=1";
import { createDataTable } from "../components/data-table.js?v=1";

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
  newMemberFormTeam: document.getElementById("newMemberFormTeam"),
  newMemberFormCancel: document.getElementById("newMemberFormCancel"),
  
  assignModal: document.getElementById("assignModal"),
  assignModalClose: document.getElementById("assignModalClose"),
  assignForm: document.getElementById("assignForm"),
  assignFormName: document.getElementById("assignFormName"),
  assignMemberName: document.getElementById("assignMemberName"),
  assignFormTeam: document.getElementById("assignFormTeam"),
  assignFormCancel: document.getElementById("assignFormCancel"),
  
  teamsMount: document.getElementById("teamsMount"),
  membersMount: document.getElementById("membersMount"),
};

let teamsTable;
let membersTable;
let teamsCache = [];

function showError(msg) {
  els.error.textContent = msg;
  els.error.style.display = "block";
}

function clearError() {
  els.error.style.display = "none";
}

const ICON_DELETE = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>`;
const ICON_EDIT = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/></svg>`;

function initTables() {
  teamsTable = createDataTable({
    mount: els.teamsMount,
    rowId: (r) => r.id,
    emptyMessage: "No teams created yet.",
    columns: [
      { key: "name", label: "Name", render: (r) => escapeHTML(r.name) },
      {
        key: "actions", label: "", sortable: false, align: "right", width: "60px",
        render: () => `<div class="row-actions"><button type="button" data-action="delete" class="danger" title="Delete Team">${ICON_DELETE}</button></div>`,
      },
    ],
  });

  teamsTable.onAction("delete", async (row) => {
      if (!confirm(`Delete team "${row.name}"? Members will be unassigned.`)) return;
      try {
        const res = await apiFetch(`/api/teams/${row.id}`, { method: "DELETE" });
        if (!res.ok) throw new Error(await res.text());
        loadData();
      } catch (err) {
        showError("Failed to delete team: " + err.message);
      }
  });

  membersTable = createDataTable({
    mount: els.membersMount,
    rowId: (r) => r.name,
    emptyMessage: "No team members yet.",
    columns: [
      { key: "name", label: "Annotator", render: (r) => escapeHTML(r.name) },
      { key: "team_name", label: "Team", render: (r) => r.team_name ? escapeHTML(r.team_name) : `<span style="color:var(--muted);">Unassigned</span>` },
      {
        key: "actions", label: "", sortable: false, align: "right", width: "60px",
        render: () => `<div class="row-actions"><button type="button" data-action="assign" title="Assign to Team">${ICON_EDIT}</button></div>`,
      },
    ],
  });

  membersTable.onAction("assign", (row) => {
    openAssignModal(row);
  });
}

async function loadData() {
  try {
    const [resTeams, resMembers] = await Promise.all([
      apiFetch("/api/teams"),
      apiFetch("/api/team"),
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
    teamsTable.setRows(teams);
    membersTable.setRows(members);
    
    // Update team select dropdown in assign modal
    els.assignFormTeam.innerHTML = '<option value="">No Team</option>';
    teams.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = t.name;
      els.assignFormTeam.appendChild(opt);
    });
    
    // Update team select dropdown in new member modal
    els.newMemberFormTeam.innerHTML = '<option value="">No Team</option>';
    teams.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = t.name;
      els.newMemberFormTeam.appendChild(opt);
    });
    
  } catch (err) {
    console.error("Failed to load", err);
    showError("Could not reach the server.");
  }
}

// --- Modals ---

function openTeamModal() {
  els.teamForm.reset();
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
      alert(err.detail || "Failed to save team");
      return;
    }
    closeTeamModal();
    loadData();
  } catch (err) {
    alert("Connection error");
  }
});

function openNewMemberModal() {
  els.newMemberForm.reset();
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
  const teamId = els.newMemberFormTeam.value ? parseInt(els.newMemberFormTeam.value, 10) : null;
  try {
    const res = await apiFetch("/api/team", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, team_id: teamId })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.detail || "Failed to add team member");
      return;
    }
    closeNewMemberModal();
    loadData();
  } catch (err) {
    alert("Connection error");
  }
});

function openAssignModal(member) {
  els.assignForm.reset();
  els.assignFormName.value = member.name;
  els.assignMemberName.textContent = member.name;
  els.assignFormTeam.value = member.team_id || "";
  els.assignModal.classList.add("is-active");
}

function closeAssignModal() {
  els.assignModal.classList.remove("is-active");
}

els.assignModalClose.addEventListener("click", closeAssignModal);
els.assignFormCancel.addEventListener("click", closeAssignModal);

els.assignForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = els.assignFormName.value;
  const teamId = els.assignFormTeam.value ? parseInt(els.assignFormTeam.value, 10) : null;
  try {
    const res = await apiFetch(`/api/team/${encodeURIComponent(name)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team_id: teamId })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.detail || "Failed to assign team");
      return;
    }
    closeAssignModal();
    loadData();
  } catch (err) {
    alert("Connection error");
  }
});

// --- Boot ---
els.user.textContent = localStorage.getItem("dataset_username") || "Annotator";
els.logout.addEventListener("click", () => {
  localStorage.removeItem("logged_in");
  window.location.href = "/";
});

initTables();
loadData();
