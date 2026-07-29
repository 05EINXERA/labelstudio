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

function getFurthestFromFirst(points) {
  let maxDist = 0;
  let maxI = 0;
  for (let i = 1; i < points.length; i++) {
    const dist = Math.hypot(points[i].x - points[0].x, points[i].y - points[0].y);
    if (dist > maxDist) {
      maxDist = dist;
      maxI = i;
    }
  }
  return maxI;
}

function simplifyPolygon(points, epsilon) {
  if (points.length < 3) return points;
  const splitIdx = getFurthestFromFirst(points);
  
  if (splitIdx === 0 || splitIdx === points.length - 1) {
    return rdp(points, epsilon);
  }

  const path1 = points.slice(0, splitIdx + 1);
  const path2 = points.slice(splitIdx);
  path2.push(points[0]); // close the second half

  const simp1 = rdp(path1, epsilon);
  const simp2 = rdp(path2, epsilon);

  simp2.pop(); // remove duplicate closing point
  simp1.pop(); // remove duplicate split point
  return simp1.concat(simp2);
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
