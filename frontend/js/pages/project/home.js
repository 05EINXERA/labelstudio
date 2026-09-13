/**
 * Home: the project metrics page (tracker P2.4).
 *
 * Everything here comes from GET /api/projects/{id}/metrics. The old
 * dashboard.html read "Loaded Images", "Total Annotations" and "Classes
 * Created" out of localStorage['image-annotation-mvp-v1'], so the numbers
 * described whatever was last open in the canvas rather than the project.
 * These are server-side counts.
 */
import { apiFetch } from "../../api.js?v=3";
import { escapeHTML, formatTime } from "../../utils.js?v=2";

let abortController = null;

function tile({ label, value, sub, href }) {
  const inner = `
    <p class="label">${escapeHTML(label)}</p>
    <p class="value">${escapeHTML(value)}</p>
    ${sub ? `<p class="sub">${escapeHTML(sub)}</p>` : ""}`;
  return href
    ? `<a class="metric-tile" href="${href}">${inner}</a>`
    : `<div class="metric-tile">${inner}</div>`;
}

function render(root, project, m) {
  const total = m.total || 0;
  const completed = m.completed || 0;
  const remaining = Math.max(0, total - completed);

  root.innerHTML = `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Overview</p>
        <h2>${escapeHTML(project?.name || "Project")}</h2>
      </div>
    </div>

    <div class="metric-tile" style="margin-bottom: 18px;">
      <p class="label">Completion</p>
      <div class="progress-cell" style="margin-top: 6px;">
        <div class="progress-track" style="height: 10px;">
          <div class="progress-fill" style="width:${m.progress || 0}%"></div>
        </div>
        <span style="font-weight: 800; font-size: 1.1rem;">${m.progress || 0}%</span>
      </div>
      <p class="sub">${completed} of ${total} task${total === 1 ? "" : "s"} completed${remaining ? ` · ${remaining} remaining` : ""}</p>
    </div>

    <div class="metric-grid">
      ${tile({ label: "Total tasks", value: total, sub: "Images in this project", href: "#/tasks" })}
      ${
        m.status_counts && Object.keys(m.status_counts).length > 0
          ? Object.entries(m.status_counts).map(([status, count]) => 
              tile({ label: status, value: count, href: "#/tasks" })
            ).join('')
          : `${tile({ label: "Completed", value: 0, href: "#/tasks" })}
             ${tile({ label: "In progress", value: 0, href: "#/tasks" })}`
      }
      ${tile({ label: "Total classes", value: m.classes || 0, sub: "Labels available to every task", href: "#/classes" })}
      ${tile({ label: "Comments", value: m.comments || 0 })}
      ${tile({ label: "Time logged", value: formatTime(m.total_time || 0), sub: "Across all tasks" })}
      ${tile({ label: "Avg per task", value: formatTime(m.avg_time_per_task || 0) })}
      ${tile({ label: "Status", value: m.status || project?.status || "New" })}
    </div>

    ${total === 0 ? `
      <div class="mgmt-empty">
        <p>This project has no tasks yet.</p>
        <p><a class="cell-link" href="#/tasks">Upload images to get started →</a></p>
      </div>` : ""}

    <div id="reviewerPanel"></div>
  `;
}

/**
 * Reviewer management, rendered only for the project owner.
 *
 * Reviewers are per-project, so this lives in the project workspace rather than
 * the workspace-wide Teams page, where "is a reviewer" would have no single
 * answer. The server is the authority on both reads and writes here; the panel
 * is hidden for non-owners purely as an affordance (the endpoints return 403
 * regardless).
 */
function renderReviewerPanel(root, ctx, members) {
  const host = root.querySelector("#reviewerPanel");
  if (!host) return;
  if (!ctx?.project?.is_owner) {
    host.innerHTML = "";
    return;
  }

  const reviewers = ctx.project.reviewers || [];
  const candidates = members.filter((m) => m !== ctx.project.creator && !reviewers.includes(m));

  host.innerHTML = `
    <div class="metric-tile" style="margin-top:18px;">
      <p class="label">Reviewers</p>
      <p class="sub" style="margin-top:4px;">
        A reviewer can open and correct any task in this project, whoever it is
        assigned to, and move a finished task back for rework. They cannot
        delete tasks, edit classes or change the project.
      </p>

      <div id="reviewerList" style="margin-top:14px; display:flex; flex-wrap:wrap; gap:8px;">
        ${
          reviewers.length
            ? reviewers.map((name) => `
                <span class="pill" style="display:inline-flex;align-items:center;gap:8px;padding:4px 10px;border:1px solid var(--line);">
                  ${escapeHTML(name)}
                  <button type="button" data-remove-reviewer="${escapeHTML(name)}"
                    title="Remove ${escapeHTML(name)} as a reviewer"
                    style="background:none;border:none;cursor:pointer;color:var(--muted);font-size:1rem;line-height:1;padding:0;">×</button>
                </span>`).join("")
            : `<span style="color:var(--muted);font-size:.85rem;">No reviewers appointed.</span>`
        }
      </div>

      <div style="margin-top:14px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
        <select id="reviewerSelect" aria-label="Team member to appoint as reviewer"
          style="padding:7px 10px;border-radius:8px;border:1px solid var(--line);min-width:200px;">
          <option value="">Choose a team member…</option>
          ${candidates.map((m) => `<option value="${escapeHTML(m)}">${escapeHTML(m)}</option>`).join("")}
        </select>
        <button type="button" class="primary" id="addReviewerBtn"
          style="padding:8px 16px;border-radius:8px;font-weight:600;">Add reviewer</button>
      </div>
      <div id="reviewerError" class="mgmt-error" style="display:none; margin-top:10px;"></div>
    </div>
  `;

  const err = host.querySelector("#reviewerError");
  const fail = (message) => {
    err.textContent = message;
    err.style.display = "block";
  };

  host.querySelector("#addReviewerBtn")?.addEventListener("click", async () => {
    err.style.display = "none";
    const name = host.querySelector("#reviewerSelect")?.value;
    if (!name) return fail("Choose a team member first.");
    try {
      const res = await apiFetch(`/api/projects/${encodeURIComponent(ctx.projectId)}/reviewers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ member_name: name }),
      });
      if (!res || !res.ok) {
        const body = res ? await res.json().catch(() => null) : null;
        return fail(body?.detail || `Could not add the reviewer (${res?.status}).`);
      }
      // Re-read the project so `reviewers` and `is_reviewer` come from the
      // server rather than being patched in locally and drifting.
      await ctx.reloadProject();
      renderReviewerPanel(root, ctx, members);
    } catch (e) {
      console.error("Failed to add reviewer", e);
      fail("Could not add the reviewer.");
    }
  });

  host.querySelectorAll("[data-remove-reviewer]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      err.style.display = "none";
      const name = btn.getAttribute("data-remove-reviewer");
      try {
        const res = await apiFetch(
          `/api/projects/${encodeURIComponent(ctx.projectId)}/reviewers/${encodeURIComponent(name)}`,
          { method: "DELETE" },
        );
        if (!res || !res.ok) return fail(`Could not remove the reviewer (${res?.status}).`);
        await ctx.reloadProject();
        renderReviewerPanel(root, ctx, members);
      } catch (e) {
        console.error("Failed to remove reviewer", e);
        fail("Could not remove the reviewer.");
      }
    });
  });
}

/** Team member names, for the appoint dropdown. Empty on failure — the panel
 *  still renders so existing reviewers can be removed. */
async function loadMemberNames(signal) {
  try {
    const res = await apiFetch("/api/team", { signal });
    if (!res || !res.ok) return [];
    const rows = await res.json();
    return Array.isArray(rows) ? rows.map((r) => r.name).filter(Boolean) : [];
  } catch (e) {
    if (e.name !== "AbortError") console.error("Failed to load team members", e);
    return [];
  }
}

export async function mount(root, ctx) {
  abortController = new AbortController();
  root.innerHTML = `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Overview</p>
        <h2>${escapeHTML(ctx?.project?.name || "Project")}</h2>
      </div>
    </div>
    <div class="metric-tile skeleton skeleton-card" style="margin-bottom: 18px; height: 100px;"></div>
    <div class="metric-grid">
      ${Array.from({ length: 8 }).map(() => '<div class="metric-tile skeleton skeleton-card" style="height: 90px;"></div>').join('')}
    </div>
  `;

  try {
    const res = await apiFetch(`/api/projects/${encodeURIComponent(ctx.projectId)}/metrics`, {
      signal: abortController.signal,
    });
    if (!res) return;
    if (!res.ok) {
      root.innerHTML = `<div class="mgmt-error">Could not load metrics (${res.status}).</div>`;
      return;
    }
    render(root, ctx.project, await res.json());

    // After render(), which owns root.innerHTML and would otherwise wipe the
    // panel. The member list is only needed for the owner's appoint dropdown,
    // so non-owners never pay for the request.
    if (ctx?.project?.is_owner) {
      renderReviewerPanel(root, ctx, await loadMemberNames(abortController.signal));
    }
  } catch (err) {
    if (err.name === "AbortError") return; // navigated away mid-request
    console.error("Failed to load metrics", err);
    root.innerHTML = `<div class="mgmt-error">Could not load metrics.</div>`;
  }
}

export function unmount() {
  // Stop an in-flight fetch so a slow response cannot paint over the next view.
  abortController?.abort();
  abortController = null;
}
