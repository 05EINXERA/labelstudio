/**
 * Geometry tests for the lasso closing path in canvas/geometry.js.
 *
 * The bug: dragging a freehand lasso past its own starting point leaves the
 * tail of the stroke running alongside the head. Those edges never actually
 * cross, so the self-intersection pass in resolveClosedPolygonIntersections
 * never saw them — it looks for loops with area to compare, and this overlap
 * encloses none. What it forms instead is a degenerate sliver: the path
 * arrives at the last point still travelling forward and the closing edge
 * sends it straight back, producing a hairpin (~6 degrees against ~176
 * elsewhere) at the free end of the stroke.
 */
import {
  resolvePolygonClosingIntersections,
  resolveClosedPolygonIntersections,
  polygonArea,
} from "../../frontend/js/canvas/geometry.js";

let failures = 0;
function check(name, cond, detail = "") {
  if (cond) {
    console.log(`ok - ${name}`);
  } else {
    failures++;
    console.log(`FAIL - ${name}${detail ? ": " + detail : ""}`);
  }
}

/** Interior turn angle at vertex i of a closed ring, in degrees. */
function turnAngle(pts, i) {
  const n = pts.length;
  const a = pts[(i - 1 + n) % n];
  const b = pts[i];
  const c = pts[(i + 1) % n];
  const v1 = { x: a.x - b.x, y: a.y - b.y };
  const v2 = { x: c.x - b.x, y: c.y - b.y };
  const m = Math.hypot(v1.x, v1.y) * Math.hypot(v2.x, v2.y);
  if (m === 0) return 180;
  const dot = v1.x * v2.x + v1.y * v2.y;
  return (Math.acos(Math.max(-1, Math.min(1, dot / m))) * 180) / Math.PI;
}

const sharpest = (pts) => Math.min(...pts.map((_, i) => turnAngle(pts, i)));

let seed = 1;
const rnd = () => {
  seed = (seed * 1103515245 + 12345) % 2147483648;
  return seed / 2147483648 - 0.5;
};

/** A hand-drawn lasso around a lobed blob, overshooting the start by `deg`. */
function lassoTrace(deg, n = 140) {
  const pts = [];
  const total = Math.PI * 2 + (deg * Math.PI) / 180;
  for (let i = 0; i <= n; i++) {
    const t = (i / n) * total;
    const r = 95 + Math.sin(t * 3) * 15;
    pts.push({
      x: 200 + Math.cos(t) * r + rnd() * 2,
      y: 200 + Math.sin(t) * r + rnd() * 2,
    });
  }
  return pts;
}

// --- the regression ---------------------------------------------------------

let spiking = [];
for (let deg = 0; deg <= 60; deg += 2) {
  const out = resolvePolygonClosingIntersections(lassoTrace(deg));
  if (sharpest(out) < 60) spiking.push(`${deg}deg:${sharpest(out).toFixed(1)}`);
}
check(
  "no hairpin spike at any overshoot from 0 to 60 degrees",
  spiking.length === 0,
  spiking.join(", ")
);

// A perfect-circle lasso is the sharpest form of the bug (no jitter to blur
// the doubled-back edge), so it is worth pinning separately.
function circleLasso(deg, n = 100) {
  const pts = [];
  const total = Math.PI * 2 + (deg * Math.PI) / 180;
  for (let i = 0; i <= n; i++) {
    const t = (i / n) * total;
    pts.push({ x: 200 + Math.cos(t) * 100, y: 200 + Math.sin(t) * 100 });
  }
  return pts;
}
const c15 = resolvePolygonClosingIntersections(circleLasso(15));
check(
  "the 15-degree overshoot that reproduced the report is trimmed",
  sharpest(c15) > 90,
  `sharpest ${sharpest(c15).toFixed(1)}deg`
);
check("trimming actually removed the tail", c15.length < 101, `${c15.length} pts`);

// --- legitimate shapes must survive untouched -------------------------------

const thin = [];
for (let i = 0; i <= 40; i++) thin.push({ x: 100 + i * 5, y: 200 + i * 0.2 });
for (let i = 40; i >= 0; i--) thin.push({ x: 100 + i * 5, y: 206 + i * 0.2 });
const thinOut = resolvePolygonClosingIntersections(thin);
check(
  "a long thin annotation (pole, wire) is not trimmed",
  thinOut.length === thin.length && Math.abs(polygonArea(thinOut) - polygonArea(thin)) < 1,
  `${thin.length} -> ${thinOut.length} pts`
);

const star = [];
for (let i = 0; i < 10; i++) {
  const t = (i / 10) * Math.PI * 2;
  const r = i % 2 ? 40 : 100;
  star.push({ x: 200 + Math.cos(t) * r, y: 200 + Math.sin(t) * r });
}
const starOut = resolvePolygonClosingIntersections(star);
check(
  "a star's genuinely sharp points are preserved",
  starOut.length === star.length,
  `${star.length} -> ${starOut.length} pts`
);

const circle = [];
for (let i = 0; i < 80; i++) {
  const t = (i / 80) * Math.PI * 2;
  circle.push({ x: 200 + Math.cos(t) * 100, y: 200 + Math.sin(t) * 100 });
}
const circleOut = resolvePolygonClosingIntersections(circle);
check(
  "a cleanly closed circle is untouched",
  circleOut.length === circle.length,
  `${circle.length} -> ${circleOut.length} pts`
);

check(
  "a triangle survives (minimum viable polygon)",
  resolvePolygonClosingIntersections([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 50, y: 80 }]).length === 3
);

// --- the crossing case still works ------------------------------------------

// A lasso that genuinely crosses itself leaves a loop with real area; that is
// the case the original pass handles and it must keep handling it.
const fig8 = [{ x: 0, y: 0 }, { x: 100, y: 100 }, { x: 100, y: 0 }, { x: 0, y: 100 }];
check(
  "a self-crossing trace still has its loop resolved",
  resolvePolygonClosingIntersections(fig8).length < 4
);

// --- the closed-ring caller is unaffected -----------------------------------

// Vertex-drag on a finished polygon goes through resolveClosedPolygonIntersections
// directly. That ring is genuinely closed, so no tail trimming applies to it.
const ring = circle.slice();
check(
  "dragging a vertex on a closed polygon does not trim it",
  resolveClosedPolygonIntersections(ring, 5).length === ring.length
);

// --- degenerate inputs ------------------------------------------------------

check("empty input is safe", resolvePolygonClosingIntersections([]).length === 0);
check("null input is safe", (resolvePolygonClosingIntersections(null) || []).length === 0);

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall checks passed");
