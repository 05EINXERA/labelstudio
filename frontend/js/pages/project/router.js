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
import { renderNav, setActive, NAV_ITEMS } from "../../components/project-nav.js?v=1";

const VALID_ROUTES = new Set(NAV_ITEMS.map((i) => i.route));
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

// View loaders with caching and preloading for instant zero-lag tab switching
const VIEW_LOADERS = {
  home: () => import("./home.js?v=1"),
  tasks: () => import("./tasks.js?v=2"),
  classes: () => import("./classes.js?v=1"),
  imports: () => import("./imports.js?v=1"),
  exports: () => import("./exports.js?v=1"),
};

const moduleCache = new Map();

function getViewModule(route) {
  if (moduleCache.has(route)) {
    return Promise.resolve(moduleCache.get(route));
  }
  const loader = VIEW_LOADERS[route];
  if (!loader) return Promise.reject(new Error(`Unknown route "${route}"`));
  return loader().then((mod) => {
    moduleCache.set(route, mod);
    return mod;
  });
}

function preloadAllViews() {
  for (const [key, loader] of Object.entries(VIEW_LOADERS)) {
    if (!moduleCache.has(key)) {
      loader()
        .then((mod) => moduleCache.set(key, mod))
        .catch(() => {});
    }
  }
}

let currentView = null;   // the loaded module, so we can call unmount()
let currentRoute = null;
let loadToken = 0;        // guards against out-of-order async view loads

const ctx = {
  projectId,
  project: null,
  reloadProject: loadProject,
  navigate(route) {
    window.location.hash = `#/${route}`;
  },
};

// --- chrome ----------------------------------------------------------------

function renderHeader() {
  const p = ctx.project;
  els.name.textContent = p?.name || "Untitled project";
  document.title = `${p?.name || "Project"} - Dataset Workspace`;

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
  renderHeader();
  return ctx.project;
}

// --- routing ---------------------------------------------------------------

function routeFromHash() {
  const raw = (window.location.hash || "").replace(/^#\/?/, "").split("?")[0];
  return VALID_ROUTES.has(raw) ? raw : DEFAULT_ROUTE;
}

async function renderRoute(optPromise) {
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
    const mod = await (optPromise || getViewModule(route));
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
  els.user.textContent = localStorage.getItem("dataset_username") || "";

  if (!projectId || !/^\d+$/.test(projectId)) {
    els.name.textContent = "No project selected";
    renderFatal("No project id in the URL. Open a project from the projects list.");
    return;
  }

  els.annotate.addEventListener("click", () => {
    window.location.href = `app.html?projectId=${encodeURIComponent(projectId)}`;
  });

  els.logout.addEventListener("click", async () => {
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch (err) {
      console.error("Logout request failed", err);
    }
    localStorage.removeItem("logged_in");
    localStorage.removeItem("dataset_username");
    localStorage.removeItem("access_token");
    window.location.replace("/");
  });

  const initialRoute = routeFromHash();
  renderNav(els.nav, initialRoute);

  // Normalise a bare/unknown hash so the address bar always shows the real
  // route and a reload lands in the same place.
  if (!window.location.hash || !VALID_ROUTES.has((window.location.hash || "").replace(/^#\/?/, ""))) {
    history.replaceState(null, "", `${window.location.pathname}${window.location.search}#/${DEFAULT_ROUTE}`);
  }

  // Concurrently fetch project details and load view module
  const initialViewPromise = getViewModule(initialRoute);
  const project = await loadProject();
  if (!project) return; // fatal already rendered

  window.addEventListener("hashchange", () => renderRoute());
  await renderRoute(initialViewPromise);

  // Background preload remaining tab modules for instant subsequent navigation
  if (typeof window.requestIdleCallback === "function") {
    window.requestIdleCallback(preloadAllViews);
  } else {
    setTimeout(preloadAllViews, 100);
  }
}

init();
