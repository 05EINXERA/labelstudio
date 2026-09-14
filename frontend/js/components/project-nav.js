/**
 * Side navigation for the project workspace (tracker P2.2).
 *
 * The five sections come from the user story: Home / Tasks / Classes /
 * Imports / Exports. Links are real `#/...` anchors rather than click handlers
 * so they can be middle-clicked, copied and deep-linked; the router listens for
 * `hashchange`.
 */
import { escapeHTML } from "../utils.js?v=2";

// `requires` gates an item to a project role. Omitted means everyone with
// project access sees it.
//
// This is RENDERING ONLY. The endpoints are the actual boundary — hiding a nav
// link does nothing about a typed, bookmarked or shared URL, and a role can be
// revoked after a link was saved. `VALID_ROUTES` in the router derives from
// this same list, so route resolution is gated by the same rule for free.
export const NAV_ITEMS = [
  { route: "home", label: "Home", icon: "📊", title: "Project metrics" },
  { route: "tasks", label: "Tasks", icon: "🖼️", title: "Images and annotation tasks" },
  { route: "classes", label: "Classes", icon: "🏷️", title: "Label classes for this project" },
  { route: "imports", label: "Imports", icon: "📥", title: "Import classes or annotations" },
  { route: "exports", label: "Exports", icon: "📦", title: "Export annotations" },
  {
    route: "images",
    label: "Images Info",
    icon: "📐",
    title: "Image resolutions, size categories and file sizes",
    requires: ["is_owner", "is_reviewer"],
  },
];

/**
 * The nav items a caller may see, given their standing on the project.
 *
 * `project` is the object from GET /api/projects/{id}, which already carries
 * server-computed `is_owner` and `is_reviewer` — the client must not re-derive
 * either by comparing names, because on a shared login the annotator display
 * name usually differs from both the username and project.creator.
 */
export function visibleNavItems(project) {
  return NAV_ITEMS.filter((item) => {
    if (!item.requires) return true;
    return item.requires.some((flag) => Boolean(project && project[flag]));
  });
}

export function renderNav(container, activeRoute, project) {
  container.innerHTML =
    `<p class="nav-section">Project</p>` +
    visibleNavItems(project).map((item) => {
      const active = item.route === activeRoute ? " is-active" : "";
      const current = item.route === activeRoute ? ' aria-current="page"' : "";
      return `<a class="nav-link${active}" href="#/${item.route}" title="${escapeHTML(item.title)}"${current}>
          <span aria-hidden="true">${item.icon}</span>
          <span>${escapeHTML(item.label)}</span>
        </a>`;
    }).join("");
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
