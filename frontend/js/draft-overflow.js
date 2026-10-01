/**
 * Where a draft goes when localStorage will not hold it.
 *
 * The per-task draft (workspace.js `saveDraft`, CLAUDE.md rule 18) is what
 * protects unsaved work from a refresh, a crash or a failed save. It lived only
 * in `localStorage`, whose per-origin quota is ~5 MB *of UTF-16* — about 2.5
 * million characters — shared with the offline queue and every other task's
 * draft. A task of roughly a thousand polygons already serialises past that, so
 * `setItem` threw `QuotaExceededError`, the catch logged a `console.warn`
 * nobody reads, and **the safety net silently did not exist on exactly the large
 * tasks where lost work costs most** (.devnotes/performance-fixes/12_RESIDUAL_AUDIT.md
 * F3). Production's p90 task is well past the limit.
 *
 * The fix keeps localStorage as the primary — it is synchronous, so a draft is
 * on disk before `pagehide` returns, and every existing code path and test
 * keeps working — and adds IndexedDB as an **overflow** used only when the
 * primary write fails. IndexedDB has no 5 MB ceiling and stores the string as
 * is. It is asynchronous, so it is a fallback, not a replacement.
 *
 * Rules this preserves:
 *   * rule 18  — a draft is cleared only on a server-confirmed save (callers);
 *   * rule 18a — nothing here ever deletes a draft because of an error. A failed
 *     overflow write leaves whatever draft already existed untouched;
 *   * a draft is never *lost to* the move: the localStorage copy is removed only
 *     after the overflow write has succeeded.
 *
 * Pure and DOM-free: the storage backends are injected, so the decision logic is
 * tested without a browser (tests/js/draft_overflow_spec.mjs).
 */

const DB_NAME = 'annotation-drafts-v1';
const STORE = 'drafts';

/**
 * A promise-based key/value backend over IndexedDB, or null when the browser
 * has none (private mode in some browsers, very old engines).
 *
 * Every method resolves rather than rejects: callers treat the overflow as
 * best-effort and must never have an editing session broken by it.
 */
export function idbBackend(factory = globalThis.indexedDB) {
  if (!factory) return null;

  let opened = null;
  const open = () => {
    if (!opened) {
      opened = new Promise((resolve, reject) => {
        const request = factory.open(DB_NAME, 1);
        request.onupgradeneeded = () => request.result.createObjectStore(STORE);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
        request.onblocked = () => reject(new Error('indexedDB open blocked'));
      });
      // A failed open must be retried next time, not cached forever.
      opened.catch(() => { opened = null; });
    }
    return opened;
  };

  const run = (mode, body) => open().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const store = tx.objectStore(STORE);
    const request = body(store);
    tx.oncomplete = () => resolve(request ? request.result : undefined);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error('transaction aborted'));
  }));

  return {
    put: (key, value) => run('readwrite', (s) => s.put(value, key)),
    get: (key) => run('readonly', (s) => s.get(key)),
    delete: (key) => run('readwrite', (s) => s.delete(key)),
    keys: () => run('readonly', (s) => s.getAllKeys()),
  };
}

/**
 * The overflow store: the backend, wrapped so nothing it does can throw.
 *
 * @param {{put,get,delete,keys}|null} backend  null disables the overflow.
 */
export function createOverflowStore(backend) {
  const safe = async (label, fn, fallback) => {
    if (!backend) return fallback;
    try {
      const result = await fn();
      return result === undefined ? fallback : result;
    } catch (e) {
      console.warn(`[draft-overflow] ${label} failed:`, e);
      return fallback;
    }
  };
  return {
    available: !!backend,
    /** Resolves true when the draft is durably stored, false otherwise. */
    put: (key, text) => safe('put', async () => { await backend.put(key, text); return true; }, false),
    /** Resolves the stored text, or null. */
    get: (key) => safe('get', () => backend.get(key), null),
    /** Resolves true on success (including "was not there"). */
    delete: (key) => safe('delete', async () => { await backend.delete(key); return true; }, false),
    /** Resolves every stored key (empty when unavailable). */
    keys: () => safe('keys', () => backend.keys(), []),
  };
}

/** The store the workspace uses: IndexedDB where the browser has it. */
export const overflowStore = createOverflowStore(
  (() => { try { return idbBackend(); } catch { return null; } })()
);

/**
 * Keys written to the overflow during this page's life. Lets a later, smaller
 * save that fits in localStorage retire the overflow copy it superseded without
 * paying an IndexedDB round trip on every ordinary draft write.
 */
const usedOverflow = new Set();

/**
 * Persist one draft: localStorage first, the overflow if that fails.
 *
 * @param {object} args
 * @param {string} args.key       The draft key (state.draftKey).
 * @param {string} args.text      The serialised draft.
 * @param {Storage-like} args.storage   `getItem/setItem/removeItem`.
 * @param {object} [args.overflow]      An overflow store.
 * @returns {Promise<'local'|'overflow'|'failed'>}
 *   `'failed'` means neither store took it — the caller should say so, because
 *   from that moment the task has no local safety net.
 */
export async function persistDraft({ key, text, storage, overflow = overflowStore }) {
  try {
    storage.setItem(key, text);
    if (usedOverflow.delete(key)) {
      // The task shrank (or other drafts were cleared) and it fits again; the
      // overflow copy is now older than the one just written.
      overflow.delete(key);
    }
    return 'local';
  } catch (e) {
    // Almost always QuotaExceededError. Any failure to write the primary is
    // worth the overflow, so no attempt is made to tell them apart.
    if (!overflow.available) {
      console.warn('Could not write local draft', e);
      return 'failed';
    }
  }

  const stored = await overflow.put(key, text);
  if (!stored) {
    console.warn('Could not write local draft: localStorage is full and the overflow store failed');
    return 'failed';
  }
  usedOverflow.add(key);
  try {
    // Only now, with the newer copy safe, drop the older one that could not be
    // replaced — otherwise a restore would offer stale work first.
    storage.removeItem(key);
  } catch {
    // Harmless: restore compares timestamps and prefers the newer.
  }
  return 'overflow';
}

/** Draft text for a key from the overflow, or null. */
export function readOverflowDraft(key, overflow = overflowStore) {
  return overflow.get(key);
}

/** Remove a task's overflow draft. Safe to call whether or not one exists. */
export function clearOverflowDraft(key, overflow = overflowStore) {
  usedOverflow.delete(key);
  return overflow.delete(key);
}

/**
 * Which of two drafts is newer, by their own `savedAt`. A draft with no
 * timestamp is treated as oldest, so a stamped one always wins over it.
 */
export function newerDraft(textA, textB) {
  const stamp = (text) => {
    try {
      const value = JSON.parse(text);
      return typeof value?.savedAt === 'number' ? value.savedAt : -Infinity;
    } catch {
      return -Infinity;
    }
  };
  return stamp(textB) > stamp(textA) ? 'b' : 'a';
}

/**
 * Delete overflow drafts that are both past `ttlMs` and not backed by a queued
 * write. Mirror of workspace.js `pruneStaleDrafts`: a draft's only job is to
 * cover work the server does not have, and an old one with an empty outbox is
 * dead weight. Never touches a draft that represents unsaved work.
 *
 * @returns {Promise<number>} how many were removed.
 */
export async function pruneOverflowDrafts({
  prefix, ttlMs, now = Date.now(), hasPendingWrite, overflow = overflowStore,
}) {
  let removed = 0;
  for (const key of await overflow.keys()) {
    if (typeof key !== 'string' || !key.startsWith(prefix)) continue;
    if (hasPendingWrite(key.slice(prefix.length))) continue;
    const text = await overflow.get(key);
    let savedAt;
    try { savedAt = JSON.parse(text)?.savedAt; } catch { savedAt = undefined; }
    // Unparseable can never be restored, so it is pure waste; otherwise TTL.
    const stale = text == null
      ? false
      : (savedAt === undefined && !isParseable(text))
        || (typeof savedAt === 'number' && now - savedAt > ttlMs);
    if (stale && await overflow.delete(key)) removed++;
  }
  return removed;
}

function isParseable(text) {
  try { JSON.parse(text); return true; } catch { return false; }
}

/** Reset module state. Tests only. */
export function _resetForTests() {
  usedOverflow.clear();
}
