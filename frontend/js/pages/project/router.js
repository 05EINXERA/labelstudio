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
import { apiFetch } from "../../api.js?v=3";
import { escapeHTML } from "../../utils.js?v=1";
import { statusClass } from "../../task-status.js?v=3";
import { renderNav, setActive, visibleNavItems } from "../../components/project-nav.js?v=3";
import { renderAppNav, wireLogout } from "../../components/app-nav.js?v=1";
import { getCurrentUser } from "../../session.js?v=1";
import { wireAccountSettings } from "../../components/account-settings.js?v=1";
import { consumeReturnTicket } from "./tasks-view-restore.js?v=2";

const DEFAULT_ROUTE = "home";

const els = {
  nav: document.getElementById("projectNav"),
  view: document.getElementById("projectView"),
  name: document.getElementById("projectName"),
  status: document.getElementById("projectStatus"),
  user: document.getElementById("currentUser"),
  settings: document.getElementById("settingsBtn"),
  logout: document.getElementById("logoutBtn"),
};

const projectId = new URLSearchParams(window.location.search).get("id");

/** sessionStorage, or null where it is unavailable (private mode, storage
 *  disabled by policy). Reading the property itself can throw, so the guard
 *  cannot be a plain truthiness check. */
function _sessionStorage() {
  try {
    return window.sessionStorage ?? null;
  } catch {
    return null;
  }
}

// View loaders. Keyed by route; the dynamic import path must be a literal so
// it stays statically analysable.
const VIEWS = {
  home: () => import("./home.js?v=5"),
  tasks: () => import("./tasks.js?v=15"),
  classes: () => import("./classes.js?v=2"),
  imports: () => import("./imports.js?v=1"),
  exports: () => import("./exports.js?v=2"),
  access: () => import("./access.js?v=1"),
};

let currentView = null;   // the loaded module, so we can call unmount()
let currentRoute = null;
let loadToken = 0;        // guards against out-of-order async view loads

/**
 * False only for the very first render of this document; true once a hashchange
 * has driven a render.
 *
 * Views use it to tell "the user just navigated here" from "the document was
 * loaded at this URL". A Home tile linking to `#/tasks?status=Verified` is the
 * former and must keep its filter; a reload of that same URL is the latter and
 * starts clean (tasks-view-restore.js).
 */
let hasNavigatedInPage = false;

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

  // Project status, not task status — a narrower vocabulary (New / Preparing /
  // In Progress / Completed). `statusClass` covers it as a superset, so the two
  // pill colourings stay in one place.
  const status = p?.status || "New";
  els.status.textContent = status;
  els.status.className = "pill " + statusClass(status);
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

/**
 * The query string carried on the hash, e.g. `#/tasks?page=4` -> URLSearchParams.
 *
 * Views own their own query vocabulary; the router only splits it off and hands
 * it over. The Tasks view uses it to restore the page an annotator was on when
 * they opened an image, so Back from the canvas does not dump them on page 1.
 */
function paramsFromHash() {
  const raw = window.location.hash || "";
  const qs = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
  return new URLSearchParams(qs);
}

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
  if (route === currentRoute) {
    // Same view, different query (e.g. `#/tasks?page=4` -> `#/tasks?page=5`).
    // Remounting would throw away the table and its scroll position, so the
    // view is offered the new params instead and decides what to do. This is
    // what makes Back step through pages rather than leaving the view.
    if (currentView?.onParamsChange) {
      try {
        currentView.onParamsChange(paramsFromHash());
      } catch (err) {
        console.error(`Failed to apply params for "${route}"`, err);
      }
    }
    return;
  }

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
    await mod.mount(els.view, ctx, paramsFromHash(), { inPageNavigation: hasNavigatedInPage });
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
  // Before the project-id guard: an unreadable id must not also cost the user
  // the account controls in the header.
  wireAccountSettings(els.settings);

  if (!projectId || !/^\d+$/.test(projectId)) {
    els.name.textContent = "No project selected";
    renderFatal("No project id in the URL. Open a project from the projects list.");
    return;
  }

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

  window.addEventListener("hashchange", () => {
    // Every hashchange is by definition an in-page navigation, so from here on
    // a mounted view is arriving because the user asked for this URL — not
    // because the document happened to load at it.
    hasNavigatedInPage = true;
    renderRoute();
  });

  // A bfcache restore (Back from the canvas, where the browser had the whole
  // document parked) resurrects this page without re-running any view's
  // mount() — the filters are simply still applied, which is exactly what a
  // return should do. But nobody consumed the return ticket the canvas left, so
  // spend it here: an unspent ticket would otherwise survive to wrongly restore
  // the *next* reload, which must start clean.
  window.addEventListener("pageshow", (e) => {
    if (!e.persisted) return;   // a normal load; mount() consumed it already
    consumeReturnTicket(_sessionStorage());
  });

  await renderRoute();
}

init();
