// Which rows the Objects panel shows, and how many of them are hidden.
//
// Deliberately pure: no imports, no DOM, no `state`. Everything arrives as an
// argument so the rules can be tested without a browser
// (tests/js/objects_filter_spec.mjs) — and, more importantly, so it is obvious
// by inspection that filtering cannot reach the annotation set that gets saved.
// See .devnotes/object-selection/01_DESIGN.md § 5.
//
// A "row" is the descriptor built by buildRows() in components/workspace.js:
//   { annotation, isGroup, groupAnns, index }

/**
 * The rows to render, given the current selection and filter toggle.
 *
 * Precedence (01_DESIGN.md § 2.1):
 *   1. a non-empty selection wins — the user just clicked a shape and the panel
 *      answers about that shape, even if the hidden filter is also on;
 *   2. otherwise the hidden filter, when active;
 *   3. otherwise everything.
 *
 * Returns a new array; `rows` and the row descriptors in it are never mutated.
 */
export function visibleRows(rows, { selectedIds, hiddenFilterActive, isHidden } = {}) {
  if (!Array.isArray(rows)) return [];

  if (selectedIds && selectedIds.size > 0) {
    // A group renders as one row standing for every member, so the row survives
    // when *any* member is selected — which is the normal case, since selecting
    // a grouped annotation selects its whole group.
    return rows.filter((row) => rowIds(row).some((id) => selectedIds.has(id)));
  }

  if (hiddenFilterActive) {
    // No isHidden predicate means we cannot tell hidden from visible; showing
    // everything is the honest fallback, and never hides real work.
    if (typeof isHidden !== "function") return rows.slice();
    return rows.filter((row) => isRowHidden(row, isHidden));
  }

  return rows.slice();
}

/**
 * How many rows are currently hidden — the number shown beside the header eye.
 *
 * Counts *rows*, so a group counts once however many members it has, matching
 * both the panel's own row count and the per-class counts. Comments are
 * excluded for the same reason they are excluded from the class counts: they
 * are annotations in storage but not objects in the image.
 */
export function hiddenRowCount(rows, isHidden) {
  if (!Array.isArray(rows) || typeof isHidden !== "function") return 0;
  return rows.reduce(
    (total, row) => (isCommentRow(row) ? total : total + (isRowHidden(row, isHidden) ? 1 : 0)),
    0
  );
}

/** Every annotation id a row stands for. */
function rowIds(row) {
  const members = row && row.groupAnns;
  if (Array.isArray(members) && members.length) return members.map((a) => a.id);
  return row && row.annotation ? [row.annotation.id] : [];
}

/**
 * Is the row hidden?
 *
 * Judged by the row's representative annotation, which is the one the row is
 * drawn from and the one its eye button toggles. A group's members are hidden
 * and revealed together by that button, so asking about the representative is
 * the same question as asking about the group.
 */
function isRowHidden(row, isHidden) {
  return !!(row && row.annotation) && !!isHidden(row.annotation);
}

function isCommentRow(row) {
  return !!(row && row.annotation && row.annotation.type === "comment");
}
