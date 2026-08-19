/**
 * Decide whether the Tasks view should restore the filters the URL carries, or
 * start clean.
 *
 * The hash records page, sort, search and the three filters so that opening an
 * image and coming back lands the annotator exactly where they left
 * (see tasks.js § "view state in the URL"). That is right for a return from the
 * canvas and wrong for a reload: reloading a page is how a user asks for a
 * fresh start, and a restored-but-unapplied filter is worse than either — the
 * dropdown reads "Approved" over a list of every task, and re-picking
 * "Approved" fires no `change` event, so the control appears dead until the
 * user selects some other status and comes back.
 *
 * So the filters survive a *navigation back from the workspace* and nothing
 * else. Two signals say a return happened, because the back arrow has two
 * paths (init.js § initWorkspaceContext):
 *
 *   - `history.back()` when the canvas was opened from this tab's table. The
 *     browser reports the resulting load as `back_forward`.
 *   - following the arrow's href, when there is no history to pop (a
 *     middle-clicked task opened in a fresh tab, a bookmarked canvas URL). That
 *     is an ordinary `navigate`, indistinguishable from someone pasting a tasks
 *     URL — so the canvas leaves a one-shot ticket in sessionStorage and this
 *     module consumes it.
 *
 * Pure by design, like `objects-filter.js` and `assignee-search.js`: the
 * storage and navigation objects are arguments, so every branch is testable
 * without a browser.
 */

/** Ticket key written by the canvas's back arrow, consumed here exactly once.
 *  Must match RETURN_TICKET_KEY in `frontend/js/init.js` — there is no build
 *  step to share one constant, so the two files name each other. */
export const RETURN_TICKET_KEY = "tasks_return_ticket";

/** How long a ticket stays trustworthy. It is written immediately before the
 *  navigation, so anything older belongs to an earlier visit — a tab left open
 *  overnight and reopened from a bookmark must not inherit stale filters. */
export const RETURN_TICKET_TTL_MS = 60 * 60 * 1000;

/** The filter/search keys that reset on a reload. Page and sort are deliberately
 *  not among them: they are not "filters the user forgot they set" — a reload of
 *  `?page=4` should stay on page 4, and a shared sorted link should stay sorted. */
export const RESETTABLE_KEYS = ["q", "status", "team", "assignee"];

/**
 * Read and consume the one-shot return ticket.
 *
 * Consuming it on read is what makes it one-shot: a return from the canvas
 * restores the filters once, and the *next* reload of that same URL clears
 * them, which is the whole point.
 *
 * @param {Storage|null} storage  sessionStorage, or null when unavailable.
 * @param {number} now  epoch ms, injected so the TTL is testable.
 * @returns {boolean} true when a live ticket was present.
 */
export function consumeReturnTicket(storage, now = Date.now()) {
  if (!storage) return false;
  let raw = null;
  try {
    raw = storage.getItem(RETURN_TICKET_KEY);
    // Removed whether or not it is still live: a stale ticket has no further
    // use, and leaving it behind would let a much later reload find it.
    if (raw !== null) storage.removeItem(RETURN_TICKET_KEY);
  } catch {
    return false;   // private mode / storage disabled: treat as a fresh visit
  }
  if (raw === null) return false;
  const age = now - Number(raw);
  return Number.isFinite(age) && age >= 0 && age < RETURN_TICKET_TTL_MS;
}

/**
 * True when the current document load is a Back/Forward traversal.
 *
 * `performance.getEntriesByType("navigation")` is the modern reading;
 * `performance.navigation.type === 2` is the deprecated one, kept because it is
 * the only signal on older WebViews. Either answering "back" is enough.
 *
 * @param {Performance|null} perf
 */
export function isBackForwardNavigation(perf) {
  if (!perf) return false;
  try {
    const entries = perf.getEntriesByType?.("navigation");
    if (entries?.length) return entries[0]?.type === "back_forward";
  } catch { /* fall through to the legacy API */ }
  try {
    // 2 === TYPE_BACK_FORWARD
    if (perf.navigation) return perf.navigation.type === 2;
  } catch { /* no navigation timing at all */ }
  return false;
}

/**
 * Should this load keep the filters the hash carries?
 *
 * Order matters: the ticket is consumed even when the navigation type already
 * says "back", so a ticket can never outlive the load it was written for.
 */
export function shouldRestoreFilters({ storage, performance: perf, now = Date.now() } = {}) {
  const hadTicket = consumeReturnTicket(storage, now);
  return hadTicket || isBackForwardNavigation(perf);
}

/**
 * Strip the resettable keys out of the parsed hash params.
 *
 * Returns a *new* URLSearchParams; the caller's copy is left alone so nothing
 * else that reads the hash is surprised.
 */
export function clearResettableParams(params) {
  const next = new URLSearchParams(params ?? "");
  for (const key of RESETTABLE_KEYS) next.delete(key);
  return next;
}
