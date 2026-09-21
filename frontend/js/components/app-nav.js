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
import { apiFetch } from "../api.js?v=5";
import { getCurrentUser } from "../session.js?v=2";
import { escapeHTML } from "../utils.js?v=2";

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
  // Admin-only, so it carries `adminOnly` rather than being pushed in by the
  // caller: the condition belongs beside the link it governs, not in three
  // page scripts that would each have to remember it.
  //
  // **Rendering only** (rule 18b). Hiding the tab is a convenience, not a
  // control: every attendance endpoint calls require_admin regardless, and a
  // stale bundle that still draws this link lands on a page whose requests
  // 404 — a cosmetic bug, not a hole.
  { key: "attendance", label: "Attendance", href: "attendance.html", adminOnly: true },
  // Everyone's own page: their attendance, and the account settings the
  // header button also reaches. Not admin-gated -- the endpoint behind it is
  // self-scoped by construction and takes no user parameter, so there is
  // nothing here one person could use to see another.
  { key: "profile", label: "My profile", href: "profile.html" },
];

/**
 * @param {HTMLElement} container
 * @param {"projects"|"teams"|"attendance"|"profile"} activeKey
 * @param {{isAdmin?: boolean}} [options] Admin-only links are omitted unless
 *   `isAdmin` is true. Defaults to false, so a caller that has not yet
 *   resolved identity draws the safe subset rather than flashing a tab the
 *   user may not use.
 *
 * Renders synchronously with the links the caller can justify *now*, then
 * upgrades once identity arrives (see `revealAdminLinks`). Every existing
 * caller invokes this before awaiting anything, so making it async would
 * either delay the whole header or make three pages restructure their init.
 */
export function renderAppNav(container, activeKey, options = {}) {
  if (!container) return;
  const isAdmin = Boolean(options.isAdmin);
  container.innerHTML = LINKS.filter((link) => !link.adminOnly || isAdmin).map((link) => {
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
 * Re-render the nav once identity is known, adding any admin-only links.
 *
 * Split from `renderAppNav` so the header paints immediately: identity costs a
 * round trip, and blocking the whole nav on it to decide one tab would make
 * every page feel slower for everyone. The admin tab appearing a moment late
 * is the right trade — and it appears for nobody if the request fails, which
 * is the safe direction.
 *
 * Idempotent: safe to call on a page that has no admin links to add.
 */
export async function revealAdminLinks(container, activeKey) {
  if (!container) return;
  try {
    const user = await getCurrentUser();
    if (user?.is_admin) {
      renderAppNav(container, activeKey, { isAdmin: true });
    }
  } catch (err) {
    // A nav that cannot confirm admin simply does not show the tab. Never
    // fatal: the rest of the page works without it.
    console.error("Could not resolve admin status for the nav", err);
  }
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
