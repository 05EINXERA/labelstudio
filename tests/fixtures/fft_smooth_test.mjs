/**
 * Geometry tests for canvas/fft-smooth.js — run by tests/test_fft_smooth.py.
 *
 * The property under test is rotation invariance: a polygon's point array has
 * an arbitrary starting index, so smoothing the same ring from two different
 * starting vertices must produce the same outline. The old simplifyPolygon
 * failed this because RDP pins the first/last point of each path it is given,
 * leaving points[0] as the one un-averaged vertex — a visible sharp corner
 * exactly where the annotator closed the trace.
 */
import { smoothPolygon, autoTolerance } from "../../frontend/js/canvas/fft-smooth.js";

let failures = 0;
function check(name, cond, detail = "") {
  if (cond) {
    console.log(`ok - ${name}`);
  } else {
    failures++;
    console.log(`FAIL - ${name}${detail ? ": " + detail : ""}`);
  }
}

/** Interior turn angle at vertex i of a closed ring, in degrees.
 * 180 = straight through; smaller = sharper corner.
 */
function turnAngle(pts, i) {
  const a = pts[(i - 1 + pts.length) % pts.length];
  const b = pts[i];
  const c = pts[(i + 1) % pts.length];
  const v1 = { x: a.x - b.x, y: a.y - b.y };
  const v2 = { x: c.x - b.x, y: c.y - b.y };
  const dot = v1.x * v2.x + v1.y * v2.y;
  const m = Math.hypot(v1.x, v1.y) * Math.hypot(v2.x, v2.y);
  if (m === 0) return 180;
  return (Math.acos(Math.max(-1, Math.min(1, dot / m))) * 180) / Math.PI;
}

function sharpestAngle(pts) {
  let min = 180;
  for (let i = 0; i < pts.length; i++) min = Math.min(min, turnAngle(pts, i));
  return min;
}

/** A hand-drawn-ish circle: dense points with slight jitter, no true corners. */
function jitteryCircle(n = 60, r = 100, jitter = 1.5) {
  const pts = [];
  let seed = 42;
  const rand = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648 - 0.5;
  };
  for (let i = 0; i < n; i++) {
    const t = (i / n) * Math.PI * 2;
    pts.push({
      x: 200 + Math.cos(t) * r + rand() * jitter,
      y: 200 + Math.sin(t) * r + rand() * jitter,
    });
  }
  return pts;
}

function rotate(pts, k) {
  return pts.slice(k).concat(pts.slice(0, k));
}

/** Chamfer-style max distance from each point of A to the nearest point of B. */
function maxDeviation(a, b) {
  let worst = 0;
  for (const p of a) {
    let best = Infinity;
    for (const q of b) best = Math.min(best, Math.hypot(p.x - q.x, p.y - q.y));
    worst = Math.max(worst, best);
  }
  return worst;
}

// --- the regression: no sharp corner at the closing vertex -------------------

const circle = jitteryCircle();
const tol = autoTolerance(circle.length);
const smoothed = smoothPolygon(circle, tol);

// A smoothed blob traced from a jittery circle has no real corners, so every
// turn should be gentle. The old code left the closing vertex near ~90deg.
check(
  "smoothed circle has no sharp corner anywhere",
  sharpestAngle(smoothed) > 120,
  `sharpest turn was ${sharpestAngle(smoothed).toFixed(1)}deg`
);

// --- rotation invariance ----------------------------------------------------

for (const k of [1, 7, 23, 41]) {
  const fromElsewhere = smoothPolygon(rotate(circle, k), tol);
  const dev = maxDeviation(smoothed, fromElsewhere);
  check(
    `outline is stable when the trace starts at vertex ${k}`,
    dev < 3.0,
    `max deviation ${dev.toFixed(2)}px`
  );
}

// --- genuine corners are still preserved ------------------------------------

const square = [];
for (let i = 0; i < 40; i++) {
  const t = i / 40;
  if (t < 0.25) square.push({ x: 100 + t * 4 * 200, y: 100 });
  else if (t < 0.5) square.push({ x: 300, y: 100 + (t - 0.25) * 4 * 200 });
  else if (t < 0.75) square.push({ x: 300 - (t - 0.5) * 4 * 200, y: 300 });
  else square.push({ x: 100, y: 300 - (t - 0.75) * 4 * 200 });
}
const smoothedSquare = smoothPolygon(square, autoTolerance(square.length));
check(
  "a real square keeps its corners (not rounded into a blob)",
  sharpestAngle(smoothedSquare) < 140,
  `sharpest turn was ${sharpestAngle(smoothedSquare).toFixed(1)}deg`
);
check(
  "a real square stays near its input outline",
  maxDeviation(smoothedSquare, square) < 20,
  `max deviation ${maxDeviation(smoothedSquare, square).toFixed(2)}px`
);

// --- degenerate inputs are passed through ------------------------------------

check("fewer than 4 points is returned unchanged", smoothPolygon([{ x: 0, y: 0 }, { x: 1, y: 1 }]).length === 2);
check("empty input is safe", smoothPolygon([]).length === 0);
check("null input is safe", smoothPolygon(null) === null);

// --- the shape does not drift -----------------------------------------------

check(
  "smoothed circle stays close to the input trace",
  maxDeviation(smoothed, circle) < 8,
  `max deviation ${maxDeviation(smoothed, circle).toFixed(2)}px`
);

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall checks passed");
