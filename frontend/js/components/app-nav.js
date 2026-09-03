/**
 * The "Projects | Teams | Manual" header nav and its shared logout handler.
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
  // The annotation manual is vendored under `frontend/manual/` and served by
  // this same app, so the href is root-relative: no hostname or port is baked
  // into the bundle, and the link survives the box changing address (the
  // deployed manual's own DEPLOY.md suggested an absolute LAN URL — that is
  // exactly the fragility this avoids).
  //
  // `external: true` means two things: it opens in a new tab, because losing
  // an annotator's place mid-task to read a rule is the whole reason they
  // would not open it; and it never renders active, because no in-app page
  // corresponds to it.
  { key: "manual", label: "Manual", href: "/manual/", external: true },
];

/**
 * @param {HTMLElement} container
 * @param {"projects"|"teams"} activeKey
 */
export function renderAppNav(container, activeKey) {
  if (!container) return;
  container.innerHTML = LINKS.map((link) => {
    const active = !link.external && link.key === activeKey;
    // `noopener` is required with `_blank`: without it the opened page gets a
    // handle on this one via `window.opener`.
    const target = link.external ? ' target="_blank" rel="noopener"' : "";
    return `<a class="app-nav-link${active ? " is-active" : ""}${
      link.external ? " is-external" : ""
    }" href="${link.href}"${target}${
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
