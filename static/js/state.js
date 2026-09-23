/**
 * Document state -- one owner per piece of it.
 *
 * This store holds only what is *persistent*: the loaded image, the region, the
 * chosen standard, the overlay switches, the last census and a status line.
 *
 * Transient interaction state deliberately does **not** live here. The camera
 * transform is owned by `camera.js`, the pointer position by `render.js` and
 * `tools.js`. Putting a mouse-move through a pub/sub store would repaint three
 * panels sixty times a second to move one label, and worse, it would make the
 * ROI geometry and the cursor share an origin. They must not.
 */

const listeners = new Set();
let state = {};

export const store = {
  get: () => state,

  patch(changes) {
    state = { ...state, ...changes };
    for (const listener of listeners) listener(state);
  },

  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export const initialState = {
  // imagery
  image: null,             // ImageInfo from the server
  images: [],              // directory listing
  gsdCm: 4.0,

  // agronomy, delivered by the server so the UI never hard-codes a threshold
  standards: {},
  sphBands: [],
  industryTargetSph: [136, 143],
  standardKey: 'mature',

  // region of interest, in full-resolution pixels
  roi: [],                 // [[x, y], ...]
  roiClosed: false,
  draft: null,             // [[x, y], ...] while drawing, uncommitted

  // operator corrections, in full-resolution pixels
  manualAdd: [],
  manualRemove: [],

  // view preferences
  tool: 'polygon',
  overlays: { palms: true, ids: false, exclusion: false, minimap: true, edges: true },

  // run parameters
  blockName: 'Blok 1',
  tilePx: 1536,
  threshold: 0,            // 0 means "derive with Otsu"
  sensitivity: 0,          // 0..1, lowers the Otsu bar to catch weaker crowns

  // results
  census: null,
  status: { kind: 'idle', text: '' },
};

/** True when a census needs to cover exactly the drawn polygon. */
export function activePolygon(s) {
  if (s.roi.length >= 3 && (s.roiClosed || s.roi.length >= 3)) return s.roi;
  return s.roi.length >= 3 ? s.roi : null;
}

/** Metres per pixel, from the one number the user controls. */
export function mPerPx(s) {
  return (s.gsdCm || 0) / 100;
}

/** Instantaneous estimate of the ground extent of the drawn region. */
export function roiStats(s) {
  const m = mPerPx(s);
  if (s.roi.length < 3) {
    if (!s.image) return null;
    const ha = (s.image.width * s.image.height * m * m) / 10000;
    return { areaHa: ha, perimeterM: null, label: 'whole orthomosaic' };
  }
  let area2 = 0;
  let perimeter = 0;
  const n = s.roi.length;
  for (let i = 0; i < n; i += 1) {
    const [x1, y1] = s.roi[i];
    const [x2, y2] = s.roi[(i + 1) % n];
    area2 += x1 * y2 - x2 * y1;
    perimeter += Math.hypot(x2 - x1, y2 - y1);
  }
  return {
    areaHa: (Math.abs(area2) / 2) * m * m / 10000,
    perimeterM: perimeter * m,
    label: `${n}-vertex polygon`,
  };
}
