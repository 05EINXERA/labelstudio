/**
 * Entry point for `teams.html`.
 *
 * The page serves two levels (04_UI_UX.md § 3) and this module does one thing:
 * decide which, then hand off. `?id=N` means a single team, otherwise the list.
 *
 * The two panes live in one HTML file so the header, both modals and the nav
 * are declared once. The *logic* stays split — `teams-list.js` and
 * `team/router.js` know nothing about each other — and neither is loaded unless
 * its pane is the one being shown.
 */
import { renderAppNav, wireLogout } from "../components/app-nav.js?v=2";
import { getCurrentUser } from "../session.js?v=1";

const teamId = new URLSearchParams(window.location.search).get("id");

async function init() {
  renderAppNav(document.getElementById("appNav"), "teams");
  wireLogout(document.getElementById("logoutBtn"));

  // Identity first: both panes level their controls on it, and the header
  // should not flash a stale localStorage name before the real one arrives.
  const user = await getCurrentUser();
  const userEl = document.getElementById("currentUser");
  if (userEl) userEl.textContent = user?.username || "";

  const showTeam = teamId && /^\d+$/.test(teamId);
  const paneId = showTeam ? "teamPane" : "listPane";
  document.getElementById(paneId).hidden = false;

  // Loaded lazily so the list page never pulls in the team shell's four views,
  // and vice versa. Literal paths keep the imports statically analysable.
  if (showTeam) {
    const mod = await import("./team/router.js?v=1");
    await mod.start(Number(teamId), user);
  } else {
    const mod = await import("./teams-list.js?v=1");
    await mod.start(user);
  }
}

init();
