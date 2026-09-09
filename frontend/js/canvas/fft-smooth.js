/**
 * fft-smooth.js (Renamed internally to use RDP + Chaikin)
 *
 * Replaces the old FFT-based smoothing with Ramer-Douglas-Peucker (RDP)
 * simplification followed by Chaikin's corner cutting. This approach
 * dramatically increases accuracy by preserving intentional corners while
 * removing hand-jitter noise.
 */

function pointLineDistance(point, start, end) {
  if (start.x === end.x && start.y === end.y) {
    return Math.hypot(point.x - start.x, point.y - start.y);
  }
  const n = Math.abs((end.y - start.y) * point.x - (end.x - start.x) * point.y + end.x * start.y - end.y * start.x);
  const d = Math.hypot(end.x - start.x, end.y - start.y);
  return n / d;
}

function rdp(points, epsilon) {
  if (points.length < 3) return points;

  let dmax = 0;
  let index = 0;
  const end = points.length - 1;

  for (let i = 1; i < end; i++) {
    const d = pointLineDistance(points[i], points[0], points[end]);
    if (d > dmax) {
      index = i;
      dmax = d;
    }
  }

  if (dmax > epsilon) {
    const recResults1 = rdp(points.slice(0, index + 1), epsilon);
    const recResults2 = rdp(points.slice(index), epsilon);
    return recResults1.slice(0, recResults1.length - 1).concat(recResults2);
  } else {
    return [points[0], points[end]];
  }
}

/** Index of the vertex furthest from the polygon's centroid.
 *
 * This picks the split point for simplifyPolygon. RDP always keeps the two
 * endpoints of the path it is given, so whichever vertices the split lands on
 * are pinned at their exact input positions while every other vertex is free
 * to be dropped or averaged. That pinning has to land somewhere, so it should
 * land where a corner genuinely belongs.
 *
 * Measuring from the centroid — rather than from points[0], as this used to —
 * is what makes the choice independent of where the annotator happened to
 * start drawing. The furthest-from-centre vertex is a real extremity of the
 * shape (a tip, a corner), so preserving it exactly is correct; preserving
 * "wherever the first click landed" was not.
 */
function getFurthestFromCentroid(points) {
  let cx = 0;
  let cy = 0;
  for (const p of points) {
    cx += p.x;
    cy += p.y;
  }
  cx /= points.length;
  cy /= points.length;

  let maxDist = -1;
  let maxI = 0;
  for (let i = 0; i < points.length; i++) {
    const dist = Math.hypot(points[i].x - cx, points[i].y - cy);
    if (dist > maxDist) {
      maxDist = dist;
      maxI = i;
    }
  }
  return maxI;
}

/** RDP-simplify a closed ring without privileging the vertex it starts on.
 *
 * A polygon's point array has an arbitrary starting index — it is a loop, and
 * points[0] is just wherever the annotator's first click landed. Running RDP
 * over the array as-is pins that vertex (see rdp's base case, which always
 * returns [first, last]), leaving it as the one un-simplified, un-averaged
 * point on the outline. Against neighbours that Chaikin has rounded off, it
 * reads as a sharp corner at the exact spot where the trace closed.
 *
 * So rotate the ring to start at a real extremity, simplify the two halves
 * around that, then rotate back so the caller's winding and starting vertex
 * are preserved.
 */
function simplifyPolygon(points, epsilon) {
  if (points.length < 3) return points;

  const rotateBy = getFurthestFromCentroid(points);
  const ring = rotateBy === 0
    ? points.slice()
    : points.slice(rotateBy).concat(points.slice(0, rotateBy));

  // Split the rotated ring at its furthest-across vertex, so each half is an
  // open path RDP can handle, and both pinned endpoints are true extremities.
  const splitIdx = getFurthestFromCentroid(ring.slice(1)) + 1;

  let simplified;
  if (splitIdx <= 0 || splitIdx >= ring.length - 1) {
    simplified = rdp(ring, epsilon);
  } else {
    const path1 = ring.slice(0, splitIdx + 1);
    const path2 = ring.slice(splitIdx);
    path2.push(ring[0]); // close the second half

    const simp1 = rdp(path1, epsilon);
    const simp2 = rdp(path2, epsilon);

    simp2.pop(); // remove duplicate closing point
    simp1.pop(); // remove duplicate split point
    simplified = simp1.concat(simp2);
  }

  if (rotateBy === 0 || simplified.length < 3) return simplified;

  // Rotate back: find where the original starting vertex ended up (it may have
  // been simplified away, in which case the nearest survivor takes its place)
  // and re-anchor the ring there, so a caller that relies on the first point
  // sees the shape it drew rather than one silently re-indexed.
  const origin = points[0];
  let bestI = 0;
  let bestDist = Infinity;
  for (let i = 0; i < simplified.length; i++) {
    const d = Math.hypot(simplified[i].x - origin.x, simplified[i].y - origin.y);
    if (d < bestDist) {
      bestDist = d;
      bestI = i;
    }
  }
  return bestI === 0
    ? simplified
    : simplified.slice(bestI).concat(simplified.slice(0, bestI));
}

function chaikin(points, iterations = 1) {
  if (iterations === 0 || points.length < 3) return points;
  let smoothed = [];
  for (let i = 0; i < points.length; i++) {
    const p1 = points[i];
    const p2 = points[(i + 1) % points.length];

    smoothed.push({
      x: 0.75 * p1.x + 0.25 * p2.x,
      y: 0.75 * p1.y + 0.25 * p2.y
    });
    smoothed.push({
      x: 0.25 * p1.x + 0.75 * p2.x,
      y: 0.25 * p1.y + 0.75 * p2.y
    });
  }
  return chaikin(smoothed, iterations - 1);
}

/**
 * Smooths the polygon using RDP to simplify, then Chaikin to round slightly.
 * 
 * @param {Array} points Array of {x, y} coordinate objects
 * @param {Number} tolerance RDP distance epsilon threshold
 */
export function smoothPolygon(points, tolerance = 2.0) {
  if (!points || points.length < 4) return points;
  
  // 1. Simplify (removes noise, keeps sharp corners)
  const simplified = simplifyPolygon(points, tolerance);
  
  // 2. Smooth (rounds out the sharp polyline slightly for organic feel)
  const smoothed = chaikin(simplified, 1);
  
  return smoothed;
}

/**
 * Returns a good tolerance epsilon based on how many points the polygon has.
 * Larger point counts usually mean more noise to filter out.
 */
export function autoTolerance(pointCount) {
  if (pointCount <= 10)  return 0.5;   // Very few points, be gentle
  if (pointCount <= 30)  return 1.0;
  if (pointCount <= 80)  return 1.5;
  if (pointCount <= 200) return 2.0;
  return 3.0; // Heavy decimation for very complex shapes
}
