/**
 * Behaviour spec for the draft overflow store.
 *
 * Run: node tests/js/draft_overflow_spec.mjs  (or via tests/test_draft_overflow.py)
 *
 * The draft is the safety net for unsaved work (CLAUDE.md rule 18), so what
 * matters here is what must NOT happen: a draft must never be destroyed by the
 * move to the overflow (rule 18a), a failure must be reported rather than
 * swallowed, and a stale copy must never beat a newer one.
 */
const m = await import(new URL('../../frontend/js/draft-overflow.js', import.meta.url));

let pass = 0, fail = 0;
const ok = (name, cond, extra = '') => {
  cond ? (pass++, console.log('  PASS', name))
       : (fail++, console.log('  FAIL', name, extra));
};

// Silence the module's intentional warnings so the spec output stays readable.
const realWarn = console.warn;
console.warn = () => {};

/** A localStorage stand-in that can be made to throw on write. */
function fakeStorage({ quota = Infinity } = {}) {
  const data = new Map();
  const calls = [];
  return {
    data, calls,
    setItem(k, v) {
      calls.push(['set', k]);
      if (v.length > quota) {
        const e = new Error('quota'); e.name = 'QuotaExceededError'; throw e;
      }
      data.set(k, v);
    },
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    removeItem(k) { calls.push(['remove', k]); data.delete(k); },
  };
}

/** An overflow backend backed by a Map; can be told to fail. */
function fakeBackend({ failPut = false, failGet = false } = {}) {
  const data = new Map();
  const log = [];
  return {
    data, log,
    async put(k, v) { log.push(['put', k]); if (failPut) throw new Error('idb put failed'); data.set(k, v); },
    async get(k) { log.push(['get', k]); if (failGet) throw new Error('idb get failed'); return data.has(k) ? data.get(k) : undefined; },
    async delete(k) { log.push(['delete', k]); data.delete(k); },
    async keys() { return [...data.keys()]; },
  };
}

const KEY = 'annotation-draft-v1:http://x:42';
const draft = (savedAt, n = 1) => JSON.stringify({ annotations: new Array(n).fill({ id: 'a' }), savedAt });

// 1. The common case is untouched: fits in localStorage, overflow never used.
{
  m._resetForTests();
  const storage = fakeStorage();
  const backend = fakeBackend();
  const overflow = m.createOverflowStore(backend);
  const outcome = await m.persistDraft({ key: KEY, text: draft(1), storage, overflow });
  ok('fits: stored locally', outcome === 'local' && storage.data.has(KEY));
  ok('fits: the overflow is never touched', backend.log.length === 0);
}

// 2. Over quota: goes to the overflow, and only THEN is the old local copy dropped.
{
  m._resetForTests();
  const storage = fakeStorage({ quota: 10 });
  storage.data.set(KEY, 'old-small');
  const backend = fakeBackend();
  const overflow = m.createOverflowStore(backend);
  const text = draft(5, 50);
  const outcome = await m.persistDraft({ key: KEY, text, storage, overflow });
  ok('over quota: reported as overflow', outcome === 'overflow');
  ok('over quota: the overflow holds the new draft', backend.data.get(KEY) === text);
  ok('over quota: the stale local copy is gone', !storage.data.has(KEY));
  const putAt = backend.log.findIndex((l) => l[0] === 'put');
  ok('over quota: overflow written before anything was removed',
     putAt === 0 && storage.calls.filter((c) => c[0] === 'remove').length === 1);
}

// 3. No overflow available: reported as failed, existing draft untouched.
{
  m._resetForTests();
  const storage = fakeStorage({ quota: 10 });
  storage.data.set(KEY, 'old-small');
  const overflow = m.createOverflowStore(null);
  const outcome = await m.persistDraft({ key: KEY, text: draft(5, 50), storage, overflow });
  ok('no overflow: reported as failed (never silent)', outcome === 'failed');
  ok('no overflow: the existing draft is NOT removed (rule 18a)',
     storage.data.get(KEY) === 'old-small'
     && !storage.calls.some((c) => c[0] === 'remove'));
}

// 4. Overflow write fails: failed, and the existing local draft survives.
{
  m._resetForTests();
  const storage = fakeStorage({ quota: 10 });
  storage.data.set(KEY, 'old-small');
  const overflow = m.createOverflowStore(fakeBackend({ failPut: true }));
  const outcome = await m.persistDraft({ key: KEY, text: draft(5, 50), storage, overflow });
  ok('overflow put fails: reported as failed', outcome === 'failed');
  ok('overflow put fails: the existing draft is kept', storage.data.get(KEY) === 'old-small');
}

// 5. The overflow copy is retired once a draft fits locally again — but
//    ordinary writes never pay an overflow round trip.
{
  m._resetForTests();
  const storage = fakeStorage();
  const backend = fakeBackend();
  const overflow = m.createOverflowStore(backend);
  await m.persistDraft({ key: KEY, text: draft(1), storage, overflow });
  await m.persistDraft({ key: KEY, text: draft(2), storage, overflow });
  ok('ordinary writes make no overflow call', backend.log.length === 0);

  // Force one overflow write, then a fitting one.
  storage.setItem = ((orig) => (k, v) => { if (v.length > 100) { const e = new Error('q'); e.name = 'QuotaExceededError'; throw e; } return orig(k, v); })(storage.setItem);
  await m.persistDraft({ key: KEY, text: draft(3, 40), storage, overflow });
  ok('a big draft used the overflow', backend.data.has(KEY));
  await m.persistDraft({ key: KEY, text: draft(4, 1), storage, overflow });
  ok('a later fitting draft retires the overflow copy', !backend.data.has(KEY));
  ok('...and the fitting draft is stored locally', storage.data.has(KEY));
}

// 6. The overflow never throws, whatever the backend does.
{
  const overflow = m.createOverflowStore(fakeBackend({ failGet: true, failPut: true }));
  let threw = false;
  try {
    ok('get on a failing backend resolves null', (await overflow.get(KEY)) === null);
    ok('put on a failing backend resolves false', (await overflow.put(KEY, 'x')) === false);
  } catch { threw = true; }
  ok('a failing backend never throws to the caller', !threw);
  const none = m.createOverflowStore(null);
  ok('a missing backend is inert', (await none.get(KEY)) === null && (await none.keys()).length === 0
     && none.available === false);
}

// 7. newerDraft: newer savedAt wins; unstamped/unparseable loses.
{
  ok('newer by savedAt wins (b)', m.newerDraft(draft(1), draft(2)) === 'b');
  ok('newer by savedAt wins (a)', m.newerDraft(draft(9), draft(2)) === 'a');
  ok('a stamped draft beats an unstamped one', m.newerDraft('{"annotations":[]}', draft(1)) === 'b');
  ok('a stamped draft beats garbage', m.newerDraft('not json', draft(1)) === 'b');
  ok('a tie keeps the first', m.newerDraft(draft(5), draft(5)) === 'a');
}

// 8. pruneOverflowDrafts: TTL and outbox are respected.
{
  const PREFIX = 'annotation-draft-v1:http://x:';
  const backend = fakeBackend();
  const overflow = m.createOverflowStore(backend);
  const DAY = 24 * 3600 * 1000;
  const now = 100 * DAY;
  backend.data.set(PREFIX + '1', draft(now - 8 * DAY));       // old, no outbox -> pruned
  backend.data.set(PREFIX + '2', draft(now - 8 * DAY));       // old, queued -> kept
  backend.data.set(PREFIX + '3', draft(now - 1 * DAY));       // recent -> kept
  backend.data.set(PREFIX + '4', 'not json at all');           // unparseable -> pruned
  backend.data.set('someone-else:9', draft(now - 99 * DAY));  // not ours -> kept
  const removed = await m.pruneOverflowDrafts({
    prefix: PREFIX, ttlMs: 7 * DAY, now,
    hasPendingWrite: (id) => id === '2', overflow,
  });
  ok('prune removed exactly the stale, unqueued and unparseable', removed === 2);
  ok('prune kept the queued old draft (unsaved work)', backend.data.has(PREFIX + '2'));
  ok('prune kept the recent draft', backend.data.has(PREFIX + '3'));
  ok('prune left other prefixes alone', backend.data.has('someone-else:9'));
}

// 9. The real IndexedDB wrapper, against a minimal fake of the IDB API.
{
  function fakeIndexedDB() {
    const stores = new Map();
    const defer = (fn) => queueMicrotask(fn);
    return {
      open() {
        const request = {};
        defer(() => {
          const db = {
            createObjectStore: (name) => { if (!stores.has(name)) stores.set(name, new Map()); },
            transaction(name) {
              const map = stores.get(name);
              const tx = {};
              tx.objectStore = () => {
                const op = (fn) => {
                  const req = {};
                  defer(() => { req.result = fn(); defer(() => tx.oncomplete && tx.oncomplete()); });
                  return req;
                };
                return {
                  put: (v, k) => op(() => { map.set(k, v); return k; }),
                  get: (k) => op(() => map.get(k)),
                  delete: (k) => op(() => { map.delete(k); }),
                  getAllKeys: () => op(() => [...map.keys()]),
                };
              };
              return tx;
            },
          };
          request.result = db;
          request.onupgradeneeded && request.onupgradeneeded();
          request.onsuccess && request.onsuccess();
        });
        return request;
      },
    };
  }
  const store = m.createOverflowStore(m.idbBackend(fakeIndexedDB()));
  ok('idb: put resolves true', (await store.put('k', 'v1')) === true);
  ok('idb: get returns what was put', (await store.get('k')) === 'v1');
  await store.put('k2', 'v2');
  ok('idb: keys lists both', (await store.keys()).sort().join() === 'k,k2');
  ok('idb: delete resolves true', (await store.delete('k')) === true);
  ok('idb: a deleted key reads back null', (await store.get('k')) === null);
  ok('idb: no indexedDB means no backend', m.idbBackend(null) === null);
}

console.warn = realWarn;
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
