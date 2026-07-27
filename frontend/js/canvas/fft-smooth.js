/**
 * fft-smooth.js
 *
 * FFT-based polygon noise filtering.
 *
 * Usage:
 *   import { smoothPolygonFFT, autoKeepRatio } from './fft-smooth.js?v=1';
 *
 *   const cleaned = smoothPolygonFFT(annotation.points, 0.35);
 *
 * Algorithm:
 *   1. Treat the polygon X and Y coordinates as separate 1-D signals.
 *   2. Zero-pad each to the next power-of-2 length.
 *   3. Run a Cooley-Tukey in-place FFT.
 *   4. Zero out frequency bins above `keepRatio * N/2` (high-frequency noise).
 *   5. IFFT and sample back to the original point count.
 *
 * No backend calls are made. All work is synchronous and runs only on user
 * action (Smooth button) or polygon finalization (auto-smooth), never per-frame.
 */

// ---------------------------------------------------------------------------
// Internal: next power of 2 >= n
// ---------------------------------------------------------------------------
function nextPow2(n) {
  let p = 1;
  while (p < n) p <<= 1;
  return p;
}

// ---------------------------------------------------------------------------
// In-place Cooley-Tukey FFT / IFFT.
// real[] and imag[] are Float64Arrays of length N (must be a power of 2).
// ---------------------------------------------------------------------------
function fft1d(real, imag, inverse = false) {
  const N = real.length;
  if (N <= 1) return;

  // Bit-reversal permutation
  let j = 0;
  for (let i = 1; i < N; i++) {
    let bit = N >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [real[i], real[j]] = [real[j], real[i]];
      [imag[i], imag[j]] = [imag[j], imag[i]];
    }
  }

  // Butterfly stages
  for (let len = 2; len <= N; len <<= 1) {
    const half = len >> 1;
    const ang = (inverse ? 2 : -2) * Math.PI / len;
    const wRe = Math.cos(ang);
    const wIm = Math.sin(ang);
    for (let i = 0; i < N; i += len) {
      let curRe = 1, curIm = 0;
      for (let k = 0; k < half; k++) {
        const uRe = real[i + k];
        const uIm = imag[i + k];
        const vRe = real[i + k + half] * curRe - imag[i + k + half] * curIm;
        const vIm = real[i + k + half] * curIm + imag[i + k + half] * curRe;
        real[i + k]        = uRe + vRe;
        imag[i + k]        = uIm + vIm;
        real[i + k + half] = uRe - vRe;
        imag[i + k + half] = uIm - vIm;
        const newCurRe = curRe * wRe - curIm * wIm;
        curIm = curRe * wIm + curIm * wRe;
        curRe = newCurRe;
      }
    }
  }

  // Normalize after inverse
  if (inverse) {
    for (let i = 0; i < N; i++) {
      real[i] /= N;
      imag[i] /= N;
    }
  }
}

// ---------------------------------------------------------------------------
// smoothPolygonFFT(points, keepRatio)
//
// points    — Array of { x, y } image-coordinate objects.
// keepRatio — Fraction of frequency bins to keep (0.0 – 1.0).
//             0.1 = very smooth; 1.0 = no change.
//
// Returns a new Array of { x, y } with the same length as `points`.
// ---------------------------------------------------------------------------
export function smoothPolygonFFT(points, keepRatio = 0.4) {
  const n = points.length;

  // Nothing to do for degenerate polygons
  if (n < 4) return points.map(p => ({ x: p.x, y: p.y }));

  // Clamp keepRatio to a safe range
  const kr = Math.max(0.05, Math.min(1.0, keepRatio));

  const padN = nextPow2(n);
  const xReal = new Float64Array(padN);
  const xImag = new Float64Array(padN);
  const yReal = new Float64Array(padN);
  const yImag = new Float64Array(padN);

  // Fill with signal (imaginary parts stay 0)
  for (let i = 0; i < n; i++) {
    xReal[i] = points[i].x;
    yReal[i] = points[i].y;
  }
  // Zero-pad: already 0 from Float64Array init

  // Forward FFT
  fft1d(xReal, xImag, false);
  fft1d(yReal, yImag, false);

  // Low-pass filter: zero bins above cutoff, preserving DC symmetry
  // We keep bins [0 .. cutoff] and [padN-cutoff .. padN-1]
  const cutoff = Math.max(1, Math.floor(kr * padN / 2));
  for (let i = cutoff + 1; i < padN - cutoff; i++) {
    xReal[i] = 0; xImag[i] = 0;
    yReal[i] = 0; yImag[i] = 0;
  }

  // Inverse FFT
  fft1d(xReal, xImag, true);
  fft1d(yReal, yImag, true);

  // Sample back to original length
  const result = [];
  for (let i = 0; i < n; i++) {
    result.push({
      x: Math.round(xReal[i] * 100) / 100,
      y: Math.round(yReal[i] * 100) / 100
    });
  }
  return result;
}

// ---------------------------------------------------------------------------
// autoKeepRatio(pointCount)
//
// Returns a good default keepRatio based on how many points the polygon has.
// More points → more high-frequency content → stronger low-pass needed.
// ---------------------------------------------------------------------------
export function autoKeepRatio(pointCount) {
  if (pointCount <= 10)  return 1.0;   // already coarse, don't touch
  if (pointCount <= 30)  return 0.60;
  if (pointCount <= 80)  return 0.40;
  if (pointCount <= 200) return 0.28;
  return 0.20;
}
