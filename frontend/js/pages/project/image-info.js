/**
 * Images Info — the project's image resolution and file-size inventory.
 *
 * Answers "which images in this project are what size", which the team
 * currently reconstructs from memory or a hand-kept spreadsheet because the
 * information is spread one-image-per-canvas-open across thousands of tasks.
 * Plan: `.devnotes/image-size-check/01_PLAN.md`.
 *
 * Server mode throughout (`createDataTable`'s `server` option). The search box,
 * the category select, the sort and the paging all resolve in SQL. Filtering
 * here instead would search the 100 rows in memory out of several thousand and
 * report "no matches" for an image that plainly exists on page 30 — the same
 * trap the Tasks view documents.
 *
 * The summary strip above the table describes the *filtered set*, not the page,
 * because that is the number a person acts on. It comes from the server's
 * `summary` block rather than being counted from the rows on screen, which
 * would change as the user paged.
 */
import { apiFetch } from "../../api.js?v=5";
import { escapeHTML } from "../../utils.js?v=2";
import { createDataTable } from "../../components/data-table.js?v=6";
import { canReview } from "../../permissions.js?v=1";
import {
  CATEGORY_ORDER,
  categoryClass,
  formatBytes,
  formatResolution,
} from "../../image-size.js?v=1";

let root = null;
let ctx = null;
let table = null;

// The server's most recent `summary` block. Kept so the strip can re-render
// without a second request, and so the download button knows whether there is
// anything to download.
let summary = null;

const PAGE_SIZE = 100;

/** Sort keys the client offers, mapped to what the endpoint accepts. */
const SERVER_SORT_KEYS = {
  filename: "filename",
  width: "width",
  height: "height",
  status: "status",
};

function el(id) {
  return root.querySelector(`#${id}`);
}

function template(role) {
  // The download is reviewer-gated server-side. Hiding it below that is a
  // rendering decision only (rule 18b) — the endpoint re-checks regardless, and
  // showing a button that always 403s is worse than not showing it.
  const download = canReview(role)
    ? `<button type="button" class="tool-button" id="downloadBtn">⬇ Download Excel</button>`
    : "";

  return `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Project</p>
        <h2>Images Info</h2>
      </div>
    </div>

    <p style="color:var(--muted); font-size:.88rem; margin:-8px 0 16px;">
      Every image in this project with its resolution and file size.
      <strong>Full</strong> is 5184 × 3888 and <strong>Half</strong> is
      2592 × 1944; anything else is listed as Other, and Unknown means the image
      was never measured.
    </p>

    <div id="errorBanner" class="mgmt-error" style="display:none;"></div>

    <div class="metric-grid" id="summaryStrip"></div>

    <div class="image-info-bar">
      <input type="search" id="searchInput" placeholder="Search filename…"
             aria-label="Search images by filename">
      <select id="categoryFilter" aria-label="Filter by size category">
        <option value="All">All sizes</option>
        ${CATEGORY_ORDER.map(
          (c) => `<option value="${escapeHTML(c)}">${escapeHTML(c)}</option>`
        ).join("")}
      </select>
      <span class="spacer"></span>
      ${download}
    </div>

    <div id="tableMount"></div>
  `;
}

// --- summary strip ---------------------------------------------------------

function renderSummary() {
  const mount = el("summaryStrip");
  if (!summary) {
    mount.innerHTML = "";
    return;
  }

  const tiles = CATEGORY_ORDER.map((name) => {
    const count = summary.by_category?.[name] ?? 0;
    return `
      <div class="metric-tile status-tile ${categoryClass(name)}">
        <p class="label">${escapeHTML(name)}</p>
        <p class="value">${count}</p>
      </div>`;
  });

  // The byte total is labelled honestly. When the filtered set was too large to
  // stat, the server sums only the page and sets `total_bytes_partial` — a
  // partial figure presented as a total is the kind of number someone orders
  // storage against.
  const bytesLabel = summary.total_bytes_partial ? "This page" : "Total size";
  const bytesSub = summary.total_bytes_partial
    ? `<p class="sub">Filter further for a whole-project total</p>`
    : "";
  tiles.push(`
    <div class="metric-tile">
      <p class="label">${bytesLabel}</p>
      <p class="value" style="font-size:1.5rem;">${formatBytes(summary.total_bytes)}</p>
      ${bytesSub}
    </div>`);

  if (summary.missing_files) {
    // Operational information the team has nowhere else: a task whose image is
    // no longer on disk. Surfaced rather than quietly rendered as an em dash in
    // one cell somebody has to notice.
    tiles.push(`
      <div class="metric-tile status-tile is-rejected">
        <p class="label">Files not found</p>
        <p class="value">${summary.missing_files}</p>
        <p class="sub">Image missing from disk</p>
      </div>`);
  }

  mount.innerHTML = tiles.join("");
}

// --- table -----------------------------------------------------------------

function buildColumns(projectId) {
  return [
    {
      key: "filename",
      label: "Filename",
      sortable: true,
      render: (r) => {
        const name = escapeHTML(r.filename || "(no filename)");
        // Linked to the canvas, as the Tasks view does. It costs nothing and
        // turns a report into a way to go and look at the odd one.
        return `<a class="cell-link" href="app.html?task=${encodeURIComponent(r.task_id)}"
                   title="Open in the annotation canvas">${name}</a>`;
      },
    },
    {
      key: "width",
      label: "Resolution",
      sortable: true,
      align: "right",
      render: (r) =>
        `<span class="res-cell">${escapeHTML(formatResolution(r.width, r.height))}</span>`,
    },
    {
      key: "category",
      label: "Category",
      align: "center",
      render: (r) =>
        `<span class="pill ${categoryClass(r.category)}">${escapeHTML(r.category)}</span>`,
    },
    {
      key: "size_bytes",
      label: "File size",
      align: "right",
      render: (r) => `<span class="res-cell">${escapeHTML(formatBytes(r.size_bytes))}</span>`,
    },
    {
      key: "status",
      label: "Status",
      sortable: true,
      align: "center",
      render: (r) => escapeHTML(r.status || "—"),
    },
  ];
}

/** Query string for the current view state, shared by the table and download. */
function queryParams({ page, pageSize, sortKey, sortDesc, query, filters }) {
  const params = new URLSearchParams();
  if (page) params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));
  params.set("sort", SERVER_SORT_KEYS[sortKey] || "filename");
  params.set("order", sortDesc ? "desc" : "asc");
  if (query) params.set("q", query);
  const category = filters?.category;
  if (category && category !== "All") params.set("category", category);
  return params;
}

async function fetchPage(state) {
  const params = queryParams(state);
  const res = await apiFetch(
    `/api/projects/${encodeURIComponent(ctx.projectId)}/image-info?${params}`
  );
  if (!res) return null;                    // apiFetch handled a 401 redirect
  if (!res.ok) throw new Error(`Could not load image info (${res.status}).`);

  const body = await res.json();
  summary = body.summary;
  renderSummary();
  clearError();
  return body;
}

function showError(message) {
  const banner = el("errorBanner");
  banner.textContent = message;
  banner.style.display = "block";
}

function clearError() {
  const banner = el("errorBanner");
  if (banner) banner.style.display = "none";
}

// --- download --------------------------------------------------------------

async function download() {
  const btn = el("downloadBtn");
  if (!btn) return;

  // Same filters as the table, without page or page size: the button under a
  // filtered view means "give me this", and the whole filtered set is the point
  // of a download.
  const state = table.getState();
  const params = queryParams({
    sortKey: state.sortKey,
    sortDesc: state.sortDesc,
    query: state.query,
    filters: state.filters,
  });

  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Preparing…";
  try {
    const res = await apiFetch(
      `/api/projects/${encodeURIComponent(ctx.projectId)}/image-info.xlsx?${params}`
    );
    if (!res) return;
    if (!res.ok) {
      // The server's message names the reason (role, or too many rows), which
      // is more use than a status code.
      let detail = `Could not build the spreadsheet (${res.status}).`;
      try {
        const body = await res.json();
        if (body?.detail) detail = body.detail;
      } catch {
        // A non-JSON error body; the status-code message stands.
      }
      showError(detail);
      return;
    }

    const blob = await res.blob();
    // The filename the server chose, so the download matches what the server
    // logged. Falls back to a sensible name if the header is unreadable.
    const disposition = res.headers.get("content-disposition") || "";
    const match = /filename="([^"]+)"/.exec(disposition);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = match ? match[1] : `image-info-${ctx.projectId}.xlsx`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Revoked on the next tick, not immediately: revoking synchronously after
    // click() races the browser's own fetch of the blob in some engines and
    // produces an empty file.
    setTimeout(() => URL.revokeObjectURL(url), 0);
    clearError();
  } catch (err) {
    console.error("Image info download failed", err);
    showError("Could not build the spreadsheet. Check your connection and retry.");
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

// --- lifecycle -------------------------------------------------------------

export async function mount(mountRoot, context) {
  root = mountRoot;
  ctx = context;
  summary = null;

  root.innerHTML = template(ctx.myRole);

  table = createDataTable({
    mount: el("tableMount"),
    rowId: (r) => r.task_id,
    sortKey: "filename",
    sortDesc: false,
    pageSize: PAGE_SIZE,
    emptyMessage: "No images match these filters.",
    // No `matches`: search is a server query, not a client predicate.
    server: { fetchPage },
    columns: buildColumns(ctx.projectId),
  });

  el("searchInput").addEventListener("input", (e) => table.setQuery(e.target.value));
  el("categoryFilter").addEventListener("change", (e) =>
    table.setFilter("category", e.target.value)
  );
  el("downloadBtn")?.addEventListener("click", download);

  try {
    await table.load();
  } catch (err) {
    console.error("Failed to load image info", err);
    showError(err.message || "Could not load image info.");
  }
}

export function unmount() {
  root = null;
  ctx = null;
  table = null;
  summary = null;
}
