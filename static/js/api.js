/**
 * The HTTP surface.
 *
 * One rule holds this whole file together: **coordinates crossing this boundary
 * are full-resolution image pixels, always.** The client never negotiates a
 * coordinate space and never converts on the way out; the camera keeps the
 * viewport in full-resolution space so a polygon vertex is sent exactly as the
 * user clicked it. That single decision removes the class of bug where a
 * display-space polygon silently produces a display-space area.
 */

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function messageOf(response) {
  const type = response.headers.get('Content-Type') || '';
  if (type.includes('application/json')) {
    try {
      const body = await response.json();
      if (body && body.error) return body.error;
    } catch {
      /* fall through to a status message */
    }
  }
  return `${response.status} ${response.statusText || 'request failed'}`;
}

async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new ApiError(await messageOf(response), response.status);
  return response.json();
}

async function postJson(path, body, { signal } = {}) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body || {}),
    signal,
  });
  if (!response.ok) throw new ApiError(await messageOf(response), response.status);
  return response.json();
}

function filenameFrom(header, fallback) {
  if (!header) return fallback;
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim());
    } catch {
      /* keep looking */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : fallback;
}

async function postForBlob(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!response.ok) throw new ApiError(await messageOf(response), response.status);
  return {
    blob: await response.blob(),
    filename: filenameFrom(response.headers.get('Content-Disposition'), 'palmsentinel-export'),
  };
}

/** Hand a blob to the browser's download machinery and release it afterwards. */
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoking immediately can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

// The fingerprint of the image whose rasters may be fetched.  Every per-image
// URL carries it so the browser cannot serve one image's cached pixels for
// another: /api/preview in particular is one URL for the whole session with a
// 24-hour cache, identical for every image without this.
let fingerprint = '';

export const api = {
  async state() {
    const body = await getJson('/api/state');
    fingerprint = body.image?.fingerprint || '';
    return body;
  },
  images: () => getJson('/api/images'),
  gsd: (gsdCm) => postJson('/api/gsd', { gsd_cm: gsdCm }),
  census: (payload, options) => postJson('/api/census', payload, options),
  lastCensus: () => getJson('/api/census'),

  /** Load by server-side path, or by uploading a File. */
  async loadPath(path, gsdCm) {
    const body = await postJson('/api/image', { path, gsd_cm: gsdCm });
    fingerprint = body.image?.fingerprint || '';
    return body;
  },
  async loadFile(file, gsdCm) {
    const form = new FormData();
    form.append('file', file, file.name);
    if (gsdCm) form.append('gsd_cm', String(gsdCm));
    const response = await fetch('/api/image', { method: 'POST', body: form });
    if (!response.ok) throw new ApiError(await messageOf(response), response.status);
    const body = await response.json();
    fingerprint = body.image?.fingerprint || '';
    return body;
  },

  exportCsv: (block) => postForBlob('/api/export/csv', { block }),
  exportGeoJson: (block) => postForBlob('/api/export/geojson', { block }),
  exportAnnotated: (block) => postForBlob('/api/export/annotated', { block }),

  cropUrl(rect, maxDim) {
    const params = new URLSearchParams({
      x1: rect.x0.toFixed(1),
      y1: rect.y0.toFixed(1),
      x2: rect.x1.toFixed(1),
      y2: rect.y1.toFixed(1),
      max_dim: String(Math.round(maxDim)),
      fp: fingerprint,
    });
    return `/api/crop?${params}`;
  },

  sampleUrl(x, y, size) {
    const params = new URLSearchParams({
      x: x.toFixed(1),
      y: y.toFixed(1),
      size: String(Math.round(size)),
      fp: fingerprint,
    });
    return `/api/sample?${params}`;
  },

  /** The preview URL of the current image; keyed by fingerprint. */
  previewUrl() {
    return `/api/preview?fp=${fingerprint}`;
  },
};

/** Decode a URL into a bitmap, resolving to null if it fails. */
export function loadImage(url) {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => resolve(null);
    image.src = url;
  });
}
