/**
 * Side navigation for the project workspace (tracker P2.2).
 *
 * The sections come from the user story: Home / Tasks / Classes / Imports /
 * Exports, plus Access for the project owner. Links are real `#/...` anchors
 * rather than click handlers so they can be middle-clicked, copied and
 * deep-linked; the router listens for `hashchange`.
 *
 * `minRole` is the role needed to *see* the tab (04_UI_UX.md § 6.1). It is a
 * rendering hint only — `router.js` re-checks it on every route, because a
 * hidden tab does nothing about a typed or bookmarked URL, and every endpoint
 * behind these views is enforced server-side regardless.
 */
import { escapeHTML } from "../utils.js?v=1";
import { atLeast } from "../permissions.js?v=1";

export const NAV_ITEMS = [
  { route: "home", label: "Home", icon: "📊", title: "Project metrics", minRole: "viewer" },
  { route: "tasks", label: "Tasks", icon: "🖼️", title: "Images and annotation tasks", minRole: "viewer" },
  { route: "classes", label: "Classes", icon: "🏷️", title: "Label classes for this project", minRole: "viewer" },
  // Imports are manager+: a replace-mode import wipes a project's labels and
  // orphans annotations for everyone at once.
  { route: "imports", label: "Imports", icon: "📥", title: "Import classes or annotations", minRole: "manager" },
  // Exports stay at viewer: an export is a read, and QA reviewers need them.
  { route: "exports", label: "Exports", icon: "📦", title: "Export annotations", minRole: "viewer" },
  // Owner-only. Granting is not delegable to a manager (03_API.md § 3).
  { route: "access", label: "Access", icon: "🔐", title: "Teams with access to this project", minRole: "owner" },
];

/** The nav items a caller holding `role` may open. */
export function visibleNavItems(role) {
  return NAV_ITEMS.filter((item) => atLeast(role, item.minRole));
}

/**
 * @param {HTMLElement} container
 * @param {string} activeRoute
 * @param {string} role the caller's effective project role (`my_role`)
 */
export function renderNav(container, activeRoute, role) {
  container.innerHTML =
    `<p class="nav-section">Project</p>` +
    visibleNavItems(role)
      .map((item) => {
        const active = item.route === activeRoute ? " is-active" : "";
        const current = item.route === activeRoute ? ' aria-current="page"' : "";
        return `<a class="nav-link${active}" href="#/${item.route}" title="${escapeHTML(item.title)}"${current}>
          <span aria-hidden="true">${item.icon}</span>
          <span>${escapeHTML(item.label)}</span>
        </a>`;
      })
      .join("");
}

/** Update the highlight without re-rendering the whole nav. */
export function setActive(container, activeRoute) {
  container.querySelectorAll("a.nav-link").forEach((a) => {
    const isActive = a.getAttribute("href") === `#/${activeRoute}`;
    a.classList.toggle("is-active", isActive);
    if (isActive) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}
