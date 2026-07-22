/**
 * Reusable sort / filter / paginate / multi-select table.
 *
 * Extracted from the inline logic in `project_details.js`, which hardcoded its
 * columns and kept eight module-level `let`s for state. Both the projects list
 * and the project Tasks view render through this instead of duplicating it.
 *
 * The caller supplies column definitions; this module owns the state and the
 * rendering, and re-binds row listeners on every render (rows are replaced
 * wholesale, so listeners cannot be bound once up front).
 */
import { escapeHTML } from "../utils.js?v=1";

/**
 * @param {object} opts
 * @param {HTMLElement} opts.mount        container to render into
 * @param {Array<object>} opts.columns    { key, label, sortable?, align?, width?, render? }
 * @param {(row:object)=>string|number} opts.rowId  stable id for selection
 * @param {boolean} [opts.selectable]     render the checkbox column
 * @param {string}  [opts.sortKey]        initial sort column
 * @param {boolean} [opts.sortDesc]
 * @param {number}  [opts.pageSize]
 * @param {(row:object, q:string)=>boolean} [opts.matches]  custom search predicate
 * @param {string}  [opts.emptyMessage]
 * @param {(ids:Set)=>void} [opts.onSelectionChange]
 */
export function createDataTable(opts) {
  const {
    mount,
    columns,
    rowId,
    selectable = false,
    matches,
    emptyMessage = "No rows match your filters.",
    onSelectionChange,
  } = opts;

  const state = {
    rows: [],
    sortKey: opts.sortKey || null,
    sortDesc: opts.sortDesc || false,
    page: 1,
    pageSize: opts.pageSize || 10,
    query: "",
    filters: {},
    selected: new Set(),
  };

  // --- derivation ---------------------------------------------------------

  function filtered() {
    const q = state.query.trim().toLowerCase();
    return state.rows.filter((row) => {
      if (q) {
        const hit = matches
          ? matches(row, q)
          : columns.some((c) => String(row[c.key] ?? "").toLowerCase().includes(q));
        if (!hit) return false;
      }
      return Object.entries(state.filters).every(
        ([key, value]) => value === "All" || value === "" || value == null || row[key] === value
      );
    });
  }

  function sorted(rows) {
    if (!state.sortKey) return rows;
    const key = state.sortKey;
    // Copy first: sorting `rows` in place would reorder state.rows via the
    // shared array reference when no filter is active.
    return [...rows].sort((a, b) => {
      let va = a[key];
      let vb = b[key];
      if (va == null) va = "";
      if (vb == null) vb = "";
      if (typeof va === "string") va = va.toLowerCase();
      if (typeof vb === "string") vb = vb.toLowerCase();
      if (va < vb) return state.sortDesc ? 1 : -1;
      if (va > vb) return state.sortDesc ? -1 : 1;
      return 0;
    });
  }

  function pageInfo(rows) {
    const totalPages = Math.max(1, Math.ceil(rows.length / state.pageSize));
    if (state.page > totalPages) state.page = totalPages;
    const start = (state.page - 1) * state.pageSize;
    return { totalPages, start, slice: rows.slice(start, start + state.pageSize) };
  }

  // --- rendering ----------------------------------------------------------

  function render() {
    const rows = sorted(filtered());
    const { totalPages, start, slice } = pageInfo(rows);

    const head = columns
      .map((c) => {
        const arrow = state.sortKey === c.key ? (state.sortDesc ? " ↓" : " ↑") : (c.sortable === false ? "" : " ↕");
        const attrs = c.sortable === false ? "" : ` data-sort="${escapeHTML(c.key)}" class="is-sortable"`;
        const style = `${c.width ? `width:${c.width};` : ""}${c.align ? `text-align:${c.align};` : ""}`;
        return `<th${attrs} style="${style}">${escapeHTML(c.label)}${arrow}</th>`;
      })
      .join("");

    const selectHead = selectable
      ? `<th style="width:40px;text-align:center;"><input type="checkbox" data-role="select-all"></th>`
      : "";

    const body = slice.length
      ? slice
          .map((row) => {
            const id = rowId(row);
            const checked = state.selected.has(id) ? "checked" : "";
            const cells = columns
              .map((c) => {
                const style = c.align ? ` style="text-align:${c.align};"` : "";
                // `render` returns trusted HTML; plain values are escaped.
                const content = c.render ? c.render(row) : escapeHTML(row[c.key] ?? "");
                return `<td${style}>${content}</td>`;
              })
              .join("");
            const box = selectable
              ? `<td style="text-align:center;"><input type="checkbox" data-role="row" data-id="${escapeHTML(id)}" ${checked}></td>`
              : "";
            return `<tr data-id="${escapeHTML(id)}">${box}${cells}</tr>`;
          })
          .join("")
      : `<tr><td colspan="${columns.length + (selectable ? 1 : 0)}" style="text-align:center;color:var(--muted);padding:24px;">${escapeHTML(emptyMessage)}</td></tr>`;

    mount.innerHTML = `
      <div class="data-table-wrap">
        <table class="task-table data-table">
          <thead><tr>${selectHead}${head}</tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
      <div class="data-table-footer">
        <span class="data-table-info">Showing ${rows.length ? start + 1 : 0} to ${Math.min(start + state.pageSize, rows.length)} of ${rows.length} entries</span>
        <div class="data-table-pager">
          <button type="button" class="tool-button" data-role="prev" ${state.page === 1 ? "disabled" : ""}>Previous</button>
          <span class="data-table-page">Page ${state.page} / ${totalPages}</span>
          <button type="button" class="tool-button" data-role="next" ${state.page >= totalPages ? "disabled" : ""}>Next</button>
        </div>
      </div>`;

    bind(slice);
  }

  function bind(slice) {
    mount.querySelectorAll("th.is-sortable").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.sortKey === key) state.sortDesc = !state.sortDesc;
        else {
          state.sortKey = key;
          state.sortDesc = false;
        }
        render();
      });
    });

    const prev = mount.querySelector('[data-role="prev"]');
    const next = mount.querySelector('[data-role="next"]');
    if (prev) prev.addEventListener("click", () => { if (state.page > 1) { state.page--; render(); } });
    if (next) next.addEventListener("click", () => { state.page++; render(); });

    if (selectable) {
      const all = mount.querySelector('[data-role="select-all"]');
      if (all) {
        all.checked = slice.length > 0 && slice.every((r) => state.selected.has(rowId(r)));
        all.addEventListener("change", () => {
          slice.forEach((r) => {
            if (all.checked) state.selected.add(rowId(r));
            else state.selected.delete(rowId(r));
          });
          render();
          onSelectionChange?.(state.selected);
        });
      }
      mount.querySelectorAll('[data-role="row"]').forEach((cb) => {
        cb.addEventListener("change", () => {
          // data-id is a string; rowId may be numeric. Match on the row object
          // so the Set stays keyed consistently.
          const row = slice.find((r) => String(rowId(r)) === cb.dataset.id);
          if (!row) return;
          if (cb.checked) state.selected.add(rowId(row));
          else state.selected.delete(rowId(row));
          const all = mount.querySelector('[data-role="select-all"]');
          if (all) all.checked = slice.every((r) => state.selected.has(rowId(r)));
          onSelectionChange?.(state.selected);
        });
      });
    }
  }

  // --- public api ---------------------------------------------------------

  return {
    setRows(rows) {
      state.rows = Array.isArray(rows) ? rows : [];
      // Drop selections for rows that no longer exist, so a stale id cannot be
      // submitted by a later bulk action.
      const live = new Set(state.rows.map(rowId));
      state.selected.forEach((id) => { if (!live.has(id)) state.selected.delete(id); });
      render();
    },
    setQuery(q) { state.query = q || ""; state.page = 1; render(); },
    setFilter(key, value) { state.filters[key] = value; state.page = 1; render(); },
    setPageSize(n) { state.pageSize = Number(n) || 10; state.page = 1; render(); },
    clearSelection() { state.selected.clear(); render(); onSelectionChange?.(state.selected); },
    getSelection() { return new Set(state.selected); },
    getRows() { return [...state.rows]; },
    render,
    /** Delegate a click on a row action button, e.g. onAction('edit', row => …) */
    onAction(name, handler) {
      mount.addEventListener("click", (e) => {
        const btn = e.target.closest(`[data-action="${name}"]`);
        if (!btn || !mount.contains(btn)) return;
        const id = btn.closest("tr")?.dataset.id;
        const row = state.rows.find((r) => String(rowId(r)) === id);
        if (row) handler(row, btn);
      });
    },
  };
}
