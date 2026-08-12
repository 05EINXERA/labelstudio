/**
 * Level 2 shell: hash router for one team (04_UI_UX.md § 5).
 *
 * Deliberately the same shape as `pages/project/router.js` — lazy view loading,
 * `mount(root, ctx)` / `unmount()`, and a load token guarding against
 * out-of-order async loads. Two routers that behave differently would be two
 * things to learn.
 *
 * `ctx` carries the shared team context so views do not each re-fetch it:
 *   { teamId, team, currentUser, myRole, reloadTeam(), navigate() }
 */
import { apiFetch } from "../../api.js?v=3";
import { escapeHTML } from "../../utils.js?v=1";
import { teamRoleBadge } from "../../components/role-badge.js?v=1";
import { canManageTeam } from "../../permissions.js?v=1";

// `minRole` is the team role needed to *see* the tab at all. Settings is
// manager+ because rename lives there; the owner-only danger zone is gated
// again inside the view.
const NAV_ITEMS = [
  { route: "members", label: "Members", icon: "👥", title: "Team roster", minRole: "member" },
  { route: "projects", label: "Projects", icon: "📁", title: "Projects this team can reach", minRole: "member" },
  { route: "settings", label: "Settings", icon: "⚙️", title: "Rename, transfer, delete", minRole: "manager" },
];

const DEFAULT_ROUTE = "members";

const VIEWS = {
  members: () => import("./members.js?v=1"),
  projects: () => import("./projects.js?v=1"),
  settings: () => import("./settings.js?v=1"),
};

const els = {
  nav: document.getElementById("teamNav"),
  view: document.getElementById("teamView"),
  name: document.getElementById("teamName"),
  badge: document.getElementById("teamRoleBadge"),
};

let currentView = null;
let currentRoute = null;
let loadToken = 0;

const ctx = {
  teamId: null,
  team: null,
  currentUser: null,
  myRole: null,
  reloadTeam: loadTeam,
  navigate(route) {
    window.location.hash = `#/${route}`;
  },
};

// --- chrome ----------------------------------------------------------------

/** Routes this caller may actually open, given their team role. */
function allowedRoutes() {
  return NAV_ITEMS.filter((item) =>
    item.minRole === "member" ? true : canManageTeam(ctx.myRole)
  );
}

function renderNav(activeRoute) {
  els.nav.innerHTML =
    `<p class="nav-section">Team</p>` +
    allowedRoutes()
      .map((item) => {
        const active = item.route === activeRoute;
        return `<a class="nav-link${active ? " is-active" : ""}" href="#/${item.route}"
            title="${escapeHTML(item.title)}"${active ? ' aria-current="page"' : ""}>
            <span aria-hidden="true">${item.icon}</span>
            <span>${escapeHTML(item.label)}</span>
          </a>`;
      })
      .join("");
}

function setActive(activeRoute) {
  els.nav.querySelectorAll("a.nav-link").forEach((a) => {
    const isActive = a.getAttribute("href") === `#/${activeRoute}`;
    a.classList.toggle("is-active", isActive);
    if (isActive) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

function renderHeader() {
  els.name.textContent = ctx.team?.name || "Team";
  document.title = `${ctx.team?.name || "Team"} - Label Studio`;
  els.badge.innerHTML = teamRoleBadge(ctx.myRole);
}

function renderFatal(message) {
  els.view.innerHTML = `<div class="mgmt-error">${escapeHTML(message)}</div>
    <p><a class="cell-link" href="teams.html">← Back to teams</a></p>`;
}

// --- data ------------------------------------------------------------------

async function loadTeam() {
  const res = await apiFetch(`/api/teams/${encodeURIComponent(ctx.teamId)}`);
  if (!res) return null;
  if (res.status === 404) {
    // 404 also covers "exists, but you are not a member" — the server does not
    // confirm team ids you cannot see (E-16).
    renderFatal("Team not found, or you are not a member of it.");
    return null;
  }
  if (!res.ok) {
    renderFatal(`Could not load this team (${res.status}).`);
    return null;
  }
  ctx.team = await res.json();
  ctx.myRole = ctx.team.my_role;
  renderHeader();
  return ctx.team;
}

// --- routing ---------------------------------------------------------------

function routeFromHash() {
  const raw = (window.location.hash || "").replace(/^#\/?/, "").split("?")[0];
  // A member deep-linking to #/settings lands on #/members. Hiding the tab is
  // not enough — the URL is typeable, bookmarkable and shareable, and the role
  // can change after the link was saved (04_UI_UX.md § 5.3).
  const allowed = new Set(allowedRoutes().map((i) => i.route));
  return allowed.has(raw) ? raw : DEFAULT_ROUTE;
}

async function renderRoute() {
  const route = routeFromHash();
  if (route === currentRoute) return;

  const token = ++loadToken;

  if (currentView?.unmount) {
    try {
      currentView.unmount();
    } catch (err) {
      console.error(`Failed to unmount "${currentRoute}"`, err);
    }
  }

  currentRoute = route;
  setActive(route);
  els.view.innerHTML = `<div class="mgmt-empty">Loading…</div>`;

  try {
    const mod = await VIEWS[route]();
    if (token !== loadToken) return; // a newer navigation won
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

export async function start(teamId, currentUser) {
  ctx.teamId = teamId;
  ctx.currentUser = currentUser;

  const team = await loadTeam();
  if (!team) return; // fatal already rendered

  renderNav(routeFromHash());

  // Normalise the hash *after* the role is known, so a member who deep-linked
  // to #/settings sees #/members in the address bar rather than a URL that lies
  // about where they are.
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
