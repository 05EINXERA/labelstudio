/**
 * buffer.js — bounded record buffer. Pure: no DOM, no globals.
 *
 * Temporary, part of the network telemetry (.devnotes/frontend-telemetry/
 * 02_DESIGN.md §3.4). Memory is capped: past `cap` the oldest record goes and
 * is counted, so a server that stays unreachable all day costs a fixed amount
 * of RAM, and the report can see that data was lost.
 */
export function createRing(cap = 3000) {
  let items = [];
  let dropped = 0;

  function trim() {
    const over = items.length - cap;
    if (over > 0) {
      items.splice(0, over);
      dropped += over;
    }
  }

  return {
    push(record) {
      items.push(record);
      trim();
    },
    /** Hand over everything buffered, and the drop count since the last take. */
    take() {
      const out = { records: items, dropped };
      items = [];
      dropped = 0;
      return out;
    },
    /** Return an unsent batch to the head, still inside the cap. */
    putBack({ records = [], dropped: d = 0 } = {}) {
      items = records.concat(items);
      dropped += d;
      trim();
    },
    /** Count records lost outside the ring (e.g. a refused beacon). */
    noteDropped(n) {
      dropped += Math.max(0, n | 0);
    },
    get size() { return items.length; },
    get dropped() { return dropped; },
  };
}
