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
 *
 * Two modes:
 *
 *  - **client** (default): the caller hands over every row via `setRows()`, and
 *    this module filters, sorts and slices in memory. Right for the small,
 *    bounded lists (projects, classes, teams, members, grants).
 *  - **server** (`opts.server`): the caller supplies a `fetchPage` function and
 *    this module asks for one page at a time. Filtering, sorting and slicing
 *    all happen in SQL; doing any of them here would reorder a 10-row page
 *    against the other 3,990 rows the user cannot see.
 *    See .devnotes/tasks-pagination/PLAN.md § 3.3.
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
 * @param {object}  [opts.server]         server-side mode; omit for client mode
 * @param {(q:object)=>Promise<{items:Array,total:number,total_pages:number}>}
 *        opts.server.fetchPage           receives {page,pageSize,sortKey,sortDesc,query,filters}
 * @param {(state:object)=>void} [opts.onStateChange]  fired when page/sort/filter changes
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
    server = null,
    onStateChange,
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
    // Server mode only: totals come from the response rather than rows.length.
    total: 0,
    totalPages: 1,
    loading: false,
    error: null,
  };

  // Server responses are async and can land out of order: click page 4 then
  // page 5, and if 4's request is slower it resolves last and overwrites 5.
  // Every fetch takes the next sequence number and a response is applied only
  // if it is still the newest one outstanding.
  let fetchSeq = 0;

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
    // Server mode: the server already sliced and counted. Re-slicing here would
    // show 10 of the 10 rows we were given as "page 1 of 1".
    if (server) {
      return {
        totalPages: state.totalPages,
        start: (state.page - 1) * state.pageSize,
        slice: rows,
        total: state.total,
      };
    }
    const totalPages = Math.max(1, Math.ceil(rows.length / state.pageSize));
    if (state.page > totalPages) state.page = totalPages;
    const start = (state.page - 1) * state.pageSize;
    return {
      totalPages,
      start,
      slice: rows.slice(start, start + state.pageSize),
      total: rows.length,
    };
  }

  /**
   * Fetch the current page from the server and render it.
   *
   * Clamps and retries once when the requested page has fallen off the end —
   * deleting the last row of the last page, or applying a filter that shrinks
   * the set, otherwise leaves the user on an empty page that reports rows.
   */
  async function loadPage({ clamped = false } = {}) {
    const seq = ++fetchSeq;
    state.loading = true;
    state.error = null;
    render();

    let body;
    try {
      body = await server.fetchPage({
        page: state.page,
        pageSize: state.pageSize,
        sortKey: state.sortKey,
        sortDesc: state.sortDesc,
        query: state.query,
        filters: { ...state.filters },
      });
    } catch (err) {
      if (seq !== fetchSeq) return;      // superseded; its error is irrelevant
      state.loading = false;
      state.error = err?.message || "Could not load this page.";
      render();
      return;
    }

    // A newer request went out while this one was in flight. Dropping the
    // response is the whole point of the sequence guard.
    if (seq !== fetchSeq) return;

    state.loading = false;
    if (!body) {
      state.error = "Could not load this page.";
      render();
      return;
    }

    state.rows = Array.isArray(body.items) ? body.items : [];
    state.total = Number(body.total) || 0;
    state.totalPages = Math.max(1, Number(body.total_pages) || 1);

    // Past the end: clamp and re-fetch, once. `clamped` stops a server that
    // keeps reporting a smaller total_pages from looping forever.
    if (state.page > state.totalPages && !clamped) {
      state.page = state.totalPages;
      onStateChange?.(publicState());
      return loadPage({ clamped: true });
    }

    // Drop selections for rows no longer present, matching client mode.
    const live = new Set(state.rows.map(rowId));
    state.selected.forEach((id) => { if (!live.has(id)) state.selected.delete(id); });

    render();
  }

  function publicState() {
    return {
      page: state.page,
      pageSize: state.pageSize,
      sortKey: state.sortKey,
      sortDesc: state.sortDesc,
      query: state.query,
      filters: { ...state.filters },
      total: state.total,
      totalPages: state.totalPages,
    };
  }

  /** Re-fetch (server) or re-render (client) after a state change. */
  function refresh() {
    onStateChange?.(publicState());
    if (server) loadPage();
    else render();
  }

  // Typing "cat.png" is 7 keystrokes; without this it is 7 requests, and the
  // stale-response guard would discard 6 of them after the server had already
  // done the work.
  const SEARCH_DEBOUNCE_MS = 300;
  let debounceTimer = null;
  function debouncedRefresh() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      refresh();
    }, SEARCH_DEBOUNCE_MS);
  }

  /**
   * Page numbers to render, with `null` marking an elided run.
   * Always includes first, last, and a window around the current page, so the
   * control stays a fixed width no matter how many tasks a project holds.
   */
  function pageNumbers(totalPages) {
    const span = 2;                       // pages shown either side of current
    const out = [];
    let last = 0;
    for (let p = 1; p <= totalPages; p++) {
      const near = Math.abs(p - state.page) <= span;
      if (p === 1 || p === totalPages || near) {
        if (last && p - last > 1) out.push(null);
        out.push(p);
        last = p;
      }
    }
    return out;
  }

  // --- rendering ----------------------------------------------------------

  function render() {
    // Server mode already filtered and sorted in SQL. Running the client
    // predicates over the page would filter 10 rows against a query the server
    // has applied to all 4,000, and re-sorting would order the page against
    // itself rather than against the full set.
    const rows = server ? state.rows : sorted(filtered());
    const { totalPages, start, slice, total } = pageInfo(rows);

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
      : `<tr><td colspan="${columns.length + (selectable ? 1 : 0)}" style="text-align:center;color:var(--muted);padding:24px;">${escapeHTML(
            state.loading ? "Loading…" : state.error || emptyMessage
          )}</td></tr>`;

    const atStart = state.page <= 1;
    const atEnd = state.page >= totalPages;
    const pageBtns = pageNumbers(totalPages)
      .map((p) =>
        p == null
          ? `<span class="pager-gap">…</span>`
          : `<button type="button" class="pager-btn pager-num${p === state.page ? " is-current" : ""}"
                     data-role="page" data-page="${p}"
                     aria-label="Page ${p}"${p === state.page ? ' aria-current="page"' : ""}>${p}</button>`
      )
      .join("");

    mount.innerHTML = `
      <div class="data-table-wrap">
        <table class="task-table data-table">
          <thead><tr>${selectHead}${head}</tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
      <div class="data-table-footer">
        <span class="data-table-info">Showing ${total ? start + 1 : 0} to ${Math.min(start + slice.length, total)} of ${total} entries</span>
        <div class="data-table-pager">
          <button type="button" class="pager-btn" data-role="first" title="First page" aria-label="First page" ${atStart ? "disabled" : ""}>«</button>
          <button type="button" class="pager-btn" data-role="prev" title="Previous page" aria-label="Previous page" ${atStart ? "disabled" : ""}>‹</button>
          <span class="pager-pages">${pageBtns}</span>
          <button type="button" class="pager-btn" data-role="next" title="Next page" aria-label="Next page" ${atEnd ? "disabled" : ""}>›</button>
          <button type="button" class="pager-btn" data-role="last" title="Last page" aria-label="Last page" ${atEnd ? "disabled" : ""}>»</button>
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
        // Re-sorting changes which rows are on page 1, so the old page number
        // is meaningless — a user on page 7 of "name asc" has no reason to be
        // on page 7 of "status desc".
        state.page = 1;
        refresh();
      });
    });

    // "Last" needs a real page number in server mode: there is no full row
    // array to clamp against locally, and MAX_SAFE_INTEGER would ask the server
    // for an absurd offset. state.totalPages is authoritative there; in client
    // mode pageInfo() still clamps on the next render.
    const goto = {
      first: () => 1,
      prev: () => state.page - 1,
      next: () => state.page + 1,
      last: () => (server ? state.totalPages : Number.MAX_SAFE_INTEGER),
    };
    Object.entries(goto).forEach(([role, to]) => {
      const btn = mount.querySelector(`[data-role="${role}"]`);
      if (btn) btn.addEventListener("click", () => {
        const next = Math.max(1, to());
        if (next === state.page) return;
        state.page = next;
        refresh();
      });
    });

    mount.querySelectorAll('[data-role="page"]').forEach((btn) => {
      btn.addEventListener("click", () => {
        const p = Number(btn.dataset.page);
        if (p && p !== state.page) { state.page = p; refresh(); }
      });
    });

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
    setQuery(q) {
      state.query = q || "";
      state.page = 1;
      // Debounced in server mode only: each keystroke would otherwise be a
      // round trip. Client mode filters an array already in memory, so
      // debouncing there would only add lag.
      if (server) debouncedRefresh();
      else render();
    },
    setFilter(key, value) { state.filters[key] = value; state.page = 1; refresh(); },
    setPageSize(n) { state.pageSize = Number(n) || 10; state.page = 1; refresh(); },
    clearSelection() { state.selected.clear(); render(); onSelectionChange?.(state.selected); },
    getSelection() { return new Set(state.selected); },
    getRows() { return [...state.rows]; },
    getState() { return publicState(); },

    /**
     * Server mode: restore page, sort, search and filters without emitting
     * onStateChange and without fetching.
     *
     * Used to restore the whole view from the URL on load. Going through
     * setPage()/setQuery()/setFilter() would fire onStateChange and rewrite the
     * very URL being read, issue one request per control, and — because those
     * setters mean "the user just changed this", so page 1 is the only sane
     * landing — reset the page the caller is trying to restore. Applying it all
     * here instead leaves exactly one fetch, on the page the URL asked for.
     *
     * `filters` is merged, not assigned: a caller restoring only `status` must
     * not silently drop a filter set elsewhere.
     */
    setInitialState({ page, sortKey, sortDesc, query, filters } = {}) {
      if (Number.isFinite(page) && page >= 1) state.page = Math.floor(page);
      if (sortKey) state.sortKey = sortKey;
      if (sortDesc !== undefined) state.sortDesc = !!sortDesc;
      if (query !== undefined) state.query = query || "";
      if (filters) Object.assign(state.filters, filters);
    },

    /** Server mode: (re)load the current page. */
    load() { return server ? loadPage() : Promise.resolve(render()); },
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
