/**
 * Images Info view — the image inventory report.
 *
 * Answers "what resolutions does this project actually contain?", which
 * otherwise lives in one person's memory and a hand-kept spreadsheet. Every
 * image with its filename, pixel resolution, a named size category and its
 * file size — searchable, filterable, downloadable.
 *
 * Read-only reporting. It says nothing about labels or progress, and it never
 * repairs a gap it reveals: a row with no dimensions shows as Unknown and
 * stays that way until somebody deliberately runs a repair.
 *
 * Gated to the project owner or an appointed reviewer. That is enforced by the
 * API; the nav gating and route resolution in router.js are rendering only.
 *
 * Server-side paging throughout. The table holds one page out of potentially
 * thousands of rows, so filtering it in the browser would search that page and
 * confidently report "no matches" for an image sitting on page 30 — every
 * filter goes to the database.
 */
import { apiFetch } from "../../api.js?v=3";
import { escapeHTML } from "../../utils.js?v=3";
import { createDataTable } from "../../components/data-table.js?v=3";
import {
  IMAGE_SIZE_CATEGORIES,
  formatBytes,
  formatResolution,
  sizePillClass,
} from "../../image-sizes.js?v=1";

let root = null;
let ctx = null;
let table = null;
let summary = null;
let searchDebounce = null;

// The filter state of the last fetch, kept here because data-table.js exposes
// no getState(). The download must reproduce exactly these filters; reading
// them from a missing accessor and falling back to {} would silently export
// the whole project from under a filtered table, which is the one failure mode
// nobody notices until they have acted on the wrong data.
let lastState = { page: 1, pageSize: 50, sortKey: "filename", sortDesc: false, query: "", filters: {} };

// A request per keystroke against a single-process deployment serving a couple
// of dozen users is self-inflicted load, and data-table.js fires onFetchData
// synchronously from setQuery (it has no debounce of its own — checked).
const SEARCH_DEBOUNCE_MS = 250;

const el = (id) => root.querySelector(`#${id}`);

// ---------------------------------------------------------------------------
// Template
// ---------------------------------------------------------------------------

function template() {
  const options = ["all", ...IMAGE_SIZE_CATEGORIES]
    .map((c) => `<option value="${escapeHTML(c)}">${c === "all" ? "All sizes" : escapeHTML(c)}</option>`)
    .join("");

  return `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Project</p>
        <h2>Images Info</h2>
      </div>
    </div>

    <p style="color:var(--muted); font-size:.88rem; margin:-8px 0 20px; max-width:70ch;">
      Every image in this project with its pixel resolution and file size.
      <strong>Full</strong> is 5184 × 3888 and <strong>Half</strong> is 2592 × 1944.
      <strong>Other</strong> is an image measured at some other resolution;
      <strong>Unknown</strong> means no dimensions were ever recorded for it.
    </p>

    <div id="errorBanner" class="mgmt-error" style="display:none;"></div>

    <div id="summaryStrip" class="metric-grid" style="display:grid;
         grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin-bottom:20px;"></div>

    <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:16px;">
      <input id="searchInput" type="search" placeholder="Search filenames…"
             style="flex:1 1 240px; min-width:180px;" autocomplete="off">
      <select id="categoryFilter" style="min-width:150px;">${options}</select>
      <button id="downloadBtn" class="btn-secondary" type="button">⬇ Download spreadsheet</button>
    </div>

    <div id="tableMount"></div>
  `;
}

// ---------------------------------------------------------------------------
// Summary strip
// ---------------------------------------------------------------------------

function renderSummary() {
  const mount = el("summaryStrip");
  if (!mount) return;
  if (!summary) {
    mount.innerHTML = "";
    return;
  }

  // Every category renders, including zeroes, so the strip keeps a stable
  // shape as filters change instead of reflowing — and so an Unknown count is
  // never simply absent. A project that is 40% unmeasured has to show that, or
  // the reader trusts a number describing 60% of their data.
  const tiles = summary.categories.map((c) => `
    <div class="metric-tile">
      <p class="label"><span class="pill ${sizePillClass(c.category)}">${escapeHTML(c.category)}</span></p>
      <p class="value" style="font-variant-numeric:tabular-nums;">${c.count}</p>
    </div>
  `);

  // The total-size tile says plainly when it covers only the current page.
  // Presenting a partial figure as a project total would be worse than
  // omitting it.
  const partial = summary.total_size_is_complete === false;
  tiles.push(`
    <div class="metric-tile">
      <p class="label">Total size${partial ? " (this page)" : ""}</p>
      <p class="value" style="font-variant-numeric:tabular-nums;">${formatBytes(summary.total_size)}</p>
      <p class="sub">${summary.total} image${summary.total === 1 ? "" : "s"}</p>
    </div>
  `);

  if (summary.missing_files > 0) {
    tiles.push(`
      <div class="metric-tile">
        <p class="label">Files missing from disk</p>
        <p class="value" style="font-variant-numeric:tabular-nums; color:var(--danger,#c13b48);">${summary.missing_files}</p>
        <p class="sub">Referenced by a task but not found</p>
      </div>
    `);
  }

  mount.innerHTML = tiles.join("");
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

function showError(message) {
  const banner = el("errorBanner");
  if (!banner) return;
  banner.textContent = message;
  banner.style.display = message ? "block" : "none";
}

function currentParams(state) {
  const params = new URLSearchParams();
  params.set("limit", String(state.pageSize));
  params.set("offset", String((state.page - 1) * state.pageSize));
  if (state.sortKey) {
    params.set("sort_by", state.sortKey);
    params.set("sort_desc", state.sortDesc ? "true" : "false");
  }
  const query = (state.query || "").trim();
  if (query) params.set("search", query);
  const category = state.filters?.category;
  if (category && category !== "all") params.set("category", category);
  return params;
}

async function fetchRows(state) {
  lastState = {
    page: state.page,
    pageSize: state.pageSize,
    sortKey: state.sortKey,
    sortDesc: state.sortDesc,
    query: state.query,
    filters: { ...(state.filters || {}) },
  };
  table.showLoading?.();
  try {
    const params = currentParams(state);
    const res = await apiFetch(
      `/api/projects/${encodeURIComponent(ctx.projectId)}/image-inventory?${params}`
    );
    if (!res) return;
    if (!res.ok) {
      showError(
        res.status === 403
          ? "Only the project owner or an appointed reviewer can view the image inventory."
          : `Could not load the image inventory (${res.status}).`
      );
      table.setServerData([], 0);
      return;
    }
    showError("");
    const body = await res.json();
    summary = body.summary;
    renderSummary();
    table.setServerData(body.items, body.total);
  } catch (err) {
    console.error("Image inventory load failed", err);
    showError("Could not load the image inventory.");
    table.setServerData([], 0);
  }
}

function downloadSpreadsheet() {
  // Honours the active filters: a download button beneath a filtered table
  // means "give me this". One that silently returned everything would only be
  // discovered after somebody acted on the wrong data.
  const params = currentParams(lastState);
  // Paging is a property of the table, not of the request: the download covers
  // the whole filtered set, not the page being looked at.
  params.delete("limit");
  params.delete("offset");
  window.location.href =
    `/api/projects/${encodeURIComponent(ctx.projectId)}/image-inventory.xlsx?${params}`;
}

// ---------------------------------------------------------------------------
// Mount
// ---------------------------------------------------------------------------

export async function mount(hostRoot, hostCtx) {
  root = hostRoot;
  ctx = hostCtx;
  summary = null;

  root.innerHTML = template();

  table = createDataTable({
    mount: el("tableMount"),
    rowId: (r) => r.id,
    sortKey: "filename",
    sortDesc: false,
    pageSize: 50,
    emptyMessage: "No images match your filters.",
    onFetchData: fetchRows,
    columns: [
      {
        key: "filename",
        label: "Filename",
        // Links to the annotation canvas the way the Tasks table does, so the
        // odd one out is one click from being looked at.
        render: (r) => `<a href="app.html?projectId=${encodeURIComponent(ctx.projectId)}&taskId=${encodeURIComponent(r.id)}"
             title="${escapeHTML(r.filename || "")}"
             style="max-width:340px;display:inline-block;overflow:hidden;text-overflow:ellipsis;
                    white-space:nowrap;vertical-align:middle;color:var(--accent);text-decoration:none;"
             >${escapeHTML(r.filename || "")}</a>`,
      },
      {
        key: "width",
        label: "Resolution",
        align: "right",
        // Tabular figures: these columns exist to be compared down the page.
        render: (r) => `<span style="font-variant-numeric:tabular-nums;">${escapeHTML(formatResolution(r.width, r.height))}</span>`,
      },
      {
        key: "category",
        label: "Size",
        sortable: false,
        render: (r) => `<span class="pill ${sizePillClass(r.category)}">${escapeHTML(r.category)}</span>`,
      },
      {
        key: "file_size",
        label: "File size",
        align: "right",
        sortable: false,
        render: (r) => `<span style="font-variant-numeric:tabular-nums;">${escapeHTML(formatBytes(r.file_size))}</span>`,
      },
      { key: "status", label: "Status", render: (r) => escapeHTML(r.status || "") },
    ],
  });

  el("searchInput").addEventListener("input", (e) => {
    const value = e.target.value;
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => table.setQuery(value), SEARCH_DEBOUNCE_MS);
  });
  el("categoryFilter").addEventListener("change", (e) => {
    table.setFilter("category", e.target.value);
  });
  el("downloadBtn").addEventListener("click", downloadSpreadsheet);

  await fetchRows({ page: 1, pageSize: 50, sortKey: "filename", sortDesc: false, query: "", filters: {} });
}

export function unmount() {
  clearTimeout(searchDebounce);
  searchDebounce = null;
  table = null;
  summary = null;
  root = null;
  ctx = null;
}
