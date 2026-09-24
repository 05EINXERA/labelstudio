/**
 * The wipe guard's rule, for the client.
 *
 * Deliberate mirror of the server check in api/routers/tasks.py
 * (`_unexplained_removals` and the refusal around it), with the thresholds of
 * config.py WIPE_GUARD_MIN_LOST / WIPE_GUARD_RATIO. There is no build step to
 * share one definition, so both copies carry a pointer to the other and
 * tests/js/wipe_guard_spec.mjs pins this one to the server's cases.
 *
 * The server is the authority (rule 18b applies verbatim): the client uses this
 * only to decide when to ask "are you sure?", to hold back a save the server
 * would refuse anyway, and to leave a draft alone that would reintroduce such a
 * loss. Pure — no DOM, no state import — so it can be tested in node.
 *
 * The idea, in one line: a save may not remove shapes the user did not delete.
 * Every deliberate removal (Delete, Clear all, merge, undo, AI replace) is
 * recorded as a deleted id; only the loss those ids do not explain is judged.
 * See .devnotes/bulk-loss-guard/01_DESIGN.md.
 */

export const WIPE_GUARD_MIN_LOST = 10;
export const WIPE_GUARD_RATIO = 0.30;

/** Ids as the server stores them: `str(id)`, with a falsy id never matching. */
export function idSet(annotations) {
  const ids = new Set();
  for (const ann of annotations || []) {
    if (ann && typeof ann === "object" && ann.id) ids.add(String(ann.id));
  }
  return ids;
}

/**
 * `{ removed, unexplained }` for replacing `storedIds` with `annotations`,
 * given the ids the user deliberately deleted.
 */
export function unexplainedRemovals(storedIds, annotations, deletedIds) {
  const kept = idSet(annotations);
  const deleted = new Set([...(deletedIds || [])].map(String));
  let removed = 0;
  let unexplained = 0;
  for (const id of storedIds || []) {
    if (kept.has(id)) continue;
    removed += 1;
    if (!deleted.has(id)) unexplained += 1;
  }
  return { removed, unexplained };
}

/**
 * Would the server refuse this save?
 *
 * An empty payload is refused on any unexplained loss; otherwise the loss must
 * be both at least the floor and more than the ratio of what is stored.
 */
export function isRefusedLoss({ stored, unexplained, empty }) {
  if (stored <= 0 || unexplained <= 0) return false;
  if (empty) return true;
  return unexplained >= WIPE_GUARD_MIN_LOST && unexplained > WIPE_GUARD_RATIO * stored;
}

/**
 * Should deleting `count` of `total` shapes ask first?
 *
 * The same thresholds as the refusal, so a prompt appears exactly for the
 * deletions large enough that losing them by accident would matter — and not
 * for the everyday two-box delete, where a dialog would only teach annotators
 * to click through it.
 */
export function needsDeleteConfirm(count, total) {
  if (count <= 0 || total <= 0) return false;
  return count >= WIPE_GUARD_MIN_LOST && count > WIPE_GUARD_RATIO * total;
}
