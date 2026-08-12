/**
 * The signed-in user's real identity, fetched once per page load.
 *
 * Before Teams the frontend read its identity from
 * `localStorage['dataset_username']` — free text the user typed into a prompt.
 * That is a display hint at best (rule 14) and is frequently just wrong, so
 * every role-levelled decision now needs `GET /api/auth/me` instead.
 *
 * Cached per page load, not in storage: a cached identity that outlives a
 * logout is how one annotator ends up labelled as another. One request per page
 * is cheap; being wrong about who someone is, is not.
 */
import { apiFetch } from "./api.js?v=3";

let cached = null;
let inFlight = null;

/**
 * The current user as `{ id, username, teams: [{id, name, role}] }`,
 * or `null` if unauthenticated / unreachable.
 *
 * Concurrent callers share one request: several modules ask for identity during
 * boot and each firing its own request would be wasteful and racy.
 */
export function getCurrentUser() {
  if (cached) return Promise.resolve(cached);
  if (inFlight) return inFlight;

  inFlight = (async () => {
    try {
      const res = await apiFetch("/api/auth/me");
      if (!res || !res.ok) return null;
      cached = await res.json();
      return cached;
    } catch (err) {
      console.error("Could not load current user", err);
      return null;
    } finally {
      inFlight = null;
    }
  })();

  return inFlight;
}

/**
 * Best-effort display name, for chrome that renders before identity arrives.
 *
 * Falls back to the legacy localStorage value **only** so a cached bundle
 * mid-rollout still shows something in the header. Phase 5 (F3) removes the
 * fallback once every client has reloaded. Never use this for a permission
 * decision — it is a string the user typed.
 */
export function cachedDisplayName() {
  return cached?.username || localStorage.getItem("dataset_username") || "";
}

/** True when the user belongs to at least one team — drives empty states. */
export function hasTeams(user) {
  return Boolean(user?.teams?.length);
}
