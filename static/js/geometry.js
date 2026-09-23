/** Pure geometry and a spatial index. No DOM, no state, no side effects. */

export function polygonAreaPx(points) {
  if (points.length < 3) return 0;
  let total = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    total += x1 * y2 - x2 * y1;
  }
  return Math.abs(total) / 2;
}

export function polygonBounds(points) {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const [x, y] of points) {
    if (x < x0) x0 = x;
    if (y < y0) y0 = y;
    if (x > x1) x1 = x;
    if (y > y1) y1 = y;
  }
  return { x0, y0, x1, y1 };
}

/** Ray casting. Points exactly on an edge count as inside. */
export function pointInPolygon(px, py, points) {
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
    const [xi, yi] = points[i];
    const [xj, yj] = points[j];
    if (yi > py !== yj > py) {
      const x = ((xj - xi) * (py - yi)) / (yj - yi) + xi;
      if (px < x) inside = !inside;
    }
  }
  return inside;
}

/** Squared distance from a point to a segment, for hit-testing edges. */
export function distanceToSegmentSq(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSq = dx * dx + dy * dy;
  let t = lengthSq > 0 ? ((px - ax) * dx + (py - ay) * dy) / lengthSq : 0;
  t = Math.max(0, Math.min(1, t));
  const cx = ax + t * dx;
  const cy = ay + t * dy;
  return (px - cx) ** 2 + (py - cy) ** 2;
}

/**
 * A uniform grid over a point set.
 *
 * The census can hold tens of thousands of palms and the pointer hits it on
 * every mouse move, so hit-testing has to be O(1) rather than a linear scan.
 * Built once per census result and then read-only.
 */
export class UniformGrid {
  constructor(points, cell) {
    this.cell = Math.max(cell, 1);
    this.buckets = new Map();
    for (let i = 0; i < points.length; i += 1) {
      const key = this.#key(points[i].x_px, points[i].y_px);
      const bucket = this.buckets.get(key);
      if (bucket) bucket.push(i);
      else this.buckets.set(key, [i]);
    }
  }

  #key(x, y) {
    return `${Math.floor(x / this.cell)},${Math.floor(y / this.cell)}`;
  }

  /** Index of the nearest point within `radius`, or -1. */
  nearest(points, x, y, radius) {
    const span = Math.ceil(radius / this.cell);
    const gx = Math.floor(x / this.cell);
    const gy = Math.floor(y / this.cell);
    let best = -1;
    let bestSq = radius * radius;
    for (let dy = -span; dy <= span; dy += 1) {
      for (let dx = -span; dx <= span; dx += 1) {
        const bucket = this.buckets.get(`${gx + dx},${gy + dy}`);
        if (!bucket) continue;
        for (const index of bucket) {
          const point = points[index];
          const d = (point.x_px - x) ** 2 + (point.y_px - y) ** 2;
          if (d <= bestSq) {
            bestSq = d;
            best = index;
          }
        }
      }
    }
    return best;
  }
}

/** `1234.5` -> `1,234.5`; `0.42` -> `0.42`. */
export function num(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function int(value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return Math.round(value).toLocaleString('en-US');
}

/** Metres, with a precision that stays honest across five orders of magnitude. */
export function metres(value) {
  if (!Number.isFinite(value)) return '—';
  if (Math.abs(value) >= 1000) return `${num(value / 1000, 2)} km`;
  if (Math.abs(value) >= 100) return `${num(value, 1)} m`;
  if (Math.abs(value) >= 10) return `${num(value, 2)} m`;
  return `${num(value, 3)} m`;
}

export function hectares(value) {
  if (!Number.isFinite(value)) return '—';
  return num(value, value >= 100 ? 1 : 4);
}
