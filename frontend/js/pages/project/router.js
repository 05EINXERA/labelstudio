/**
 * Level 2 shell: hash router for the project workspace (tracker P2.3).
 *
 * One page hosts five views. Each view is a module exporting
 * `mount(root, ctx)` and optionally `unmount()`; modules are loaded lazily on
 * first visit so opening Home does not pull in the export builder.
 *
 * `ctx` carries the shared project context so views do not each re-fetch it:
 *   { projectId, project, reloadProject(), setStatus(), navigate() }
 */
import { apiFetch } from "../../api.js?v=1";
import { escapeHTML } from "../../utils.js?v=1";
import { renderNav, setActive, visibleNavItems } from "../../components/project-nav.js?v=2";
import { renderAppNav, wireLogout } from "../../components/app-nav.js?v=1";
import { getCurrentUser } from "../../session.js?v=1";

const DEFAULT_ROUTE = "home";

const els = {
  nav: document.getElementById("projectNav"),
  view: document.getElementById("projectView"),
  name: document.getElementById("projectName"),
  status: document.getElementById("projectStatus"),
  user: document.getElementById("currentUser"),
  annotate: document.getElementById("annotateBtn"),
  logout: document.getElementById("logoutBtn"),
};

const projectId = new URLSearchParams(window.location.search).get("id");

// View loaders. Keyed by route; the dynamic import path must be a literal so
// it stays statically analysable.
const VIEWS = {
  home: () => import("./home.js?v=1"),
  tasks: () => import("./tasks.js?v=2"),
  classes: () => import("./classes.js?v=1"),
  imports: () => import("./imports.js?v=1"),
  exports: () => import("./exports.js?v=1"),
  access: () => import("./access.js?v=1"),
};

let currentView = null;   // the loaded module, so we can call unmount()
let currentRoute = null;
let loadToken = 0;        // guards against out-of-order async view loads

const ctx = {
  projectId,
  project: null,
  // The caller's effective role on this project, and their real identity. Views
  // level their controls on these rather than guessing from ownership.
  myRole: null,
  currentUser: null,
  reloadProject: loadProject,
  navigate(route) {
    window.location.hash = `#/${route}`;
  },
};

// --- chrome ----------------------------------------------------------------

function renderHeader() {
  const p = ctx.project;
  els.name.textContent = p?.name || "Untitled project";
  document.title = `${p?.name || "Project"} - Label Studio`;

  const status = p?.status || "New";
  els.status.textContent = status;
  els.status.className = "pill " + (
    status === "Completed" ? "is-completed"
      : status === "In Progress" ? "is-progress"
      : status === "Approved" ? "is-approved" : ""
  );
}

function renderFatal(message) {
  els.view.innerHTML = `<div class="mgmt-error">${escapeHTML(message)}</div>
    <p><a class="cell-link" href="projects.html">← Back to projects</a></p>`;
}

// --- data ------------------------------------------------------------------

async function loadProject() {
  const res = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}`);
  if (!res) return null; // apiFetch redirected to login
  if (res.status === 404) {
    // Owner-scoped: 404 also covers "exists but belongs to someone else".
    renderFatal("Project not found, or you do not have access to it.");
    return null;
  }
  if (!res.ok) {
    renderFatal(`Could not load this project (${res.status}).`);
    return null;
  }
  ctx.project = await res.json();
  ctx.myRole = ctx.project.my_role;
  renderHeader();
  return ctx.project;
}

// --- routing ---------------------------------------------------------------

function routeFromHash() {
  const raw = (window.location.hash || "").replace(/^#\/?/, "").split("?")[0];
  // Role-filtered, not just validated: deep-linking to a route above your role
  // resolves to Home rather than rendering a view whose every request 403s.
  // Hiding the nav item is not enough — a URL is typeable and bookmarkable, and
  // a role can be revoked after the link was saved (04_UI_UX.md § 6.1).
  const allowed = new Set(visibleNavItems(ctx.myRole).map((i) => i.route));
  return allowed.has(raw) ? raw : DEFAULT_ROUTE;
}

async function renderRoute() {
  const route = routeFromHash();
  if (route === currentRoute) return;

  const token = ++loadToken;

  // Tear down the previous view before swapping the DOM out from under it, so
  // it can drop timers and listeners.
  if (currentView?.unmount) {
    try {
      currentView.unmount();
    } catch (err) {
      console.error(`Failed to unmount "${currentRoute}"`, err);
    }
  }

  currentRoute = route;
  setActive(els.nav, route);
  els.view.innerHTML = `<div class="mgmt-empty">Loading…</div>`;

  try {
    const mod = await VIEWS[route]();
    // A newer navigation started while this module was loading; discard.
    if (token !== loadToken) return;
    currentView = mod;
    await mod.mount(els.view, ctx);
  } catch (err) {
    if (token !== loadToken) return;
    console.error(`Failed to load view "${route}"`, err);
    els.view.innerHTML =
      `<div class="mgmt-error">Could not load the ${escapeHTML(route)} view.</div>`;
  }
}

// --- init ------------------------------------------------------------------

async function init() {
  renderAppNav(document.getElementById("appNav"), "projects");
  wireLogout(els.logout);

  if (!projectId || !/^\d+$/.test(projectId)) {
    els.name.textContent = "No project selected";
    renderFatal("No project id in the URL. Open a project from the projects list.");
    return;
  }

  els.annotate.addEventListener("click", () => {
    window.location.href = `app.html?projectId=${encodeURIComponent(projectId)}`;
  });

  // Identity and the project are both needed before anything role-dependent is
  // drawn, and neither depends on the other, so they overlap.
  const [user, project] = await Promise.all([getCurrentUser(), loadProject()]);
  ctx.currentUser = user;
  els.user.textContent = user?.username || "";
  if (!project) return; // fatal already rendered

  // Nav renders *after* the role is known — rendering it first would flash tabs
  // the caller cannot open, and `routeFromHash` needs `ctx.myRole` to resolve.
  renderNav(els.nav, routeFromHash(), ctx.myRole);

  // Normalise the hash so the address bar always shows the route actually being
  // displayed. Done after the role check, so a viewer who deep-linked to
  // #/access sees #/home rather than a URL that lies about where they are.
  const resolved = routeFromHash();
  if ((window.location.hash || "").replace(/^#\/?/, "").split("?")[0] !== resolved) {
    history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}#/${resolved}`
    );
  }

  window.addEventListener("hashchange", renderRoute);
  await renderRoute();
}

init();
