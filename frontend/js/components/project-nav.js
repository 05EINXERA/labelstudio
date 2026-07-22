/**
 * Side navigation for the project workspace (tracker P2.2).
 *
 * The five sections come from the user story: Home / Tasks / Classes /
 * Imports / Exports. Links are real `#/...` anchors rather than click handlers
 * so they can be middle-clicked, copied and deep-linked; the router listens for
 * `hashchange`.
 */
import { escapeHTML } from "../utils.js?v=1";

export const NAV_ITEMS = [
  { route: "home", label: "Home", icon: "📊", title: "Project metrics" },
  { route: "tasks", label: "Tasks", icon: "🖼️", title: "Images and annotation tasks" },
  { route: "classes", label: "Classes", icon: "🏷️", title: "Label classes for this project" },
  { route: "imports", label: "Imports", icon: "📥", title: "Import classes or annotations" },
  { route: "exports", label: "Exports", icon: "📦", title: "Export annotations" },
];

export function renderNav(container, activeRoute) {
  container.innerHTML =
    `<p class="nav-section">Project</p>` +
    NAV_ITEMS.map((item) => {
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
