/**
 * Geometry tests for splitClosedPolygonAtIntersections in canvas/geometry.js.
 *
 * Dragging a vertex of a finished polygon across the opposite edge pinches the
 * outline in two. The old resolve pass kept the larger loop and discarded the
 * rest, so half the annotation vanished. The split pass keeps both halves as
 * separate rings instead; these tests pin that behaviour down, and check that
 * shapes which do not self-intersect still come back whole and single.
 */
import {
  splitClosedPolygonAtIntersections,
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

// --- A square whose top-right corner is dragged past the left edge, pinching
// the outline into two lobes (the screenshot case).
const pinched = [
  { x: 0, y: 0 },
  { x: -60, y: 50 },   // dragged corner, now left of the left edge
  { x: 100, y: 100 },
  { x: 0, y: 100 },
];
const parts = splitClosedPolygonAtIntersections(pinched);
check("pinched square splits into two parts", parts.length === 2, `got ${parts.length}`);
check(
  "both parts are real rings",
  parts.every((p) => p.length >= 3 && polygonArea(p) > 1),
  JSON.stringify(parts.map((p) => [p.length, polygonArea(p)]))
);
check(
  "parts are ordered largest first",
  parts.length === 2 && polygonArea(parts[0]) >= polygonArea(parts[1])
);

// The old behaviour is what the split replaces: resolve keeps one loop only.
const resolved = resolveClosedPolygonIntersections(pinched);
check(
  "split keeps more total area than the old resolve pass",
  parts.reduce((sum, p) => sum + polygonArea(p), 0) > polygonArea(resolved)
);

// --- A classic bowtie: two equal lobes, neither one "primary".
const bowtie = [
  { x: 0, y: 0 },
  { x: 100, y: 100 },
  { x: 100, y: 0 },
  { x: 0, y: 100 },
];
const bowParts = splitClosedPolygonAtIntersections(bowtie);
check("bowtie splits into two lobes", bowParts.length === 2, `got ${bowParts.length}`);
check(
  "bowtie lobes have equal area",
  bowParts.length === 2 && Math.abs(polygonArea(bowParts[0]) - polygonArea(bowParts[1])) < 1
);

// --- Shapes that do not cross themselves must come back unchanged and single.
const square = [
  { x: 0, y: 0 },
  { x: 100, y: 0 },
  { x: 100, y: 100 },
  { x: 0, y: 100 },
];
const squareParts = splitClosedPolygonAtIntersections(square);
check("simple square stays one part", squareParts.length === 1);
check("simple square keeps its vertices", squareParts[0]?.length === 4);
check("simple square keeps its area", Math.abs(polygonArea(squareParts[0]) - 10000) < 1);

const concave = [
  { x: 0, y: 0 },
  { x: 100, y: 0 },
  { x: 100, y: 100 },
  { x: 50, y: 40 },
  { x: 0, y: 100 },
];
check("concave polygon stays one part", splitClosedPolygonAtIntersections(concave).length === 1);

// --- A traced blob with many vertices and no crossings is untouched.
const blob = [];
for (let i = 0; i < 60; i++) {
  const a = (i / 60) * Math.PI * 2;
  blob.push({ x: 200 + Math.cos(a) * (80 + Math.sin(a * 3) * 10), y: 200 + Math.sin(a) * 80 });
}
const blobParts = splitClosedPolygonAtIntersections(blob);
check("traced blob stays one part", blobParts.length === 1, `got ${blobParts.length}`);

// --- A crossing that encloses almost nothing is a fumbled drag, not a split.
const sliver = [
  { x: 0, y: 0 },
  { x: 100, y: 0 },
  { x: 100, y: 100 },
  { x: 0, y: 100 },
  { x: 0.2, y: 50 },
  { x: -0.2, y: 25 },
];
const sliverParts = splitClosedPolygonAtIntersections(sliver);
check(
  "sub-threshold sliver does not spawn a second shape",
  sliverParts.length === 1,
  `got ${sliverParts.length}`
);

// --- Degenerate input is safe.
check("empty input is safe", splitClosedPolygonAtIntersections([]).length === 0);
check("null input is safe", splitClosedPolygonAtIntersections(null).length === 0);
check("triangle passes through", splitClosedPolygonAtIntersections([
  { x: 0, y: 0 }, { x: 100, y: 0 }, { x: 50, y: 80 },
]).length === 1);
check(
  "split never returns an empty set for a real polygon",
  splitClosedPolygonAtIntersections(pinched).length > 0
);

console.log(failures === 0 ? "\nALL PASS" : `\n${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
