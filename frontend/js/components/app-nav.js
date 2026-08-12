/**
 * The "Projects | Teams" header nav and its shared logout handler.
 *
 * Teams are a **sibling** of projects, not a child (04_UI_UX.md § 3): a team
 * exists independently of any project and can be granted to many, so it gets a
 * peer entry rather than living inside a project.
 *
 * Rendered from JS rather than copy-pasted into three HTML files so adding a
 * fourth Level-1 page means editing one array. The markup is small; the
 * duplication is what would bite.
 */
import { apiFetch } from "../api.js?v=3";
import { escapeHTML } from "../utils.js?v=1";

const LINKS = [
  { key: "projects", label: "Projects", href: "projects.html" },
  { key: "teams", label: "Teams", href: "teams.html" },
];

/**
 * @param {HTMLElement} container
 * @param {"projects"|"teams"} activeKey
 */
export function renderAppNav(container, activeKey) {
  if (!container) return;
  container.innerHTML = LINKS.map((link) => {
    const active = link.key === activeKey;
    return `<a class="app-nav-link${active ? " is-active" : ""}" href="${link.href}"${
      active ? ' aria-current="page"' : ""
    }>${escapeHTML(link.label)}</a>`;
  }).join("");
}

/**
 * Wire the standard log-out button.
 *
 * Clears `logged_in` (a UI hint only — rule 14) and the legacy display name.
 * The httpOnly session cookie is what actually ends the session, and only the
 * server can clear it, so a failed request still falls through to the redirect
 * rather than stranding the user on a page they can no longer use.
 */
export function wireLogout(button) {
  if (!button) return;
  button.addEventListener("click", async () => {
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch (err) {
      console.error("Logout request failed", err);
    }
    localStorage.removeItem("logged_in");
    localStorage.removeItem("dataset_username");
    window.location.href = "/";
  });
}
