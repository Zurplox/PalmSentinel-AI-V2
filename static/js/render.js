/**
 * The canvas painter.
 *
 * Drawing happens in screen space, one CSS pixel at a time; the camera supplies
 * the mapping from full-resolution image pixels. Two raster layers are
 * available and the renderer chooses between them by zoom:
 *
 *   preview  one downscaled JPEG of the whole mosaic, always present
 *   native   a full-resolution crop of the visible rectangle, requested on
 *            demand and cached, so zooming in reaches real sensor pixels
 *            instead of an interpolated blur
 *
 * The painter is stateless with respect to the application: everything it needs
 * arrives in the `frame` object handed to `draw()`. It reads the palette once
 * from CSS custom properties so there is a single place colours are defined.
 */

import { api, loadImage } from './api.js';
import { polygonAreaPx } from './geometry.js';

const PALM_MARKER_MIN = 1.6;
// Capped small: the marker is a pin, not a portrait. A 26 px blob paved over
// neighbouring fronds on closed canopy and read as one circle covering
// several trees; the count never looked at markers, but owners do.
const PALM_MARKER_MAX = 12;
const LABEL_LIMIT = 420;
const NATIVE_CACHE_LIMIT = 4;
const NATIVE_MAX_DIM = 2048;
const EDGE_LABEL_MIN_PX = 54;

function palette() {
  const css = getComputedStyle(document.documentElement);
  const read = (name, fallback) => (css.getPropertyValue(name) || fallback).trim();
  return {
    stage: read('--bg-stage', '#08090b'),
    palm: read('--palm', '#6ee7a8'),
    roi: read('--roi', '#d8a13c'),
    text: read('--text', '#e6e9ed'),
    text3: read('--text-3', '#6a7481'),
    info: read('--info', '#5aa9e6'),
    bad: read('--bad', '#e2665c'),
    line: read('--line-strong', '#313843'),
    ink: 'rgba(6, 8, 10, 0.78)',
    roiFill: 'rgba(216, 161, 60, 0.07)',
  };
}

export class MapRenderer {
  constructor(canvas, minimapCanvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.minimapCanvas = minimapCanvas;
    this.minimapCtx = minimapCanvas ? minimapCanvas.getContext('2d') : null;
    this.colors = palette();
    this.image = null;
    this.preview = null;
    this.native = null;
    this.nativeRequest = null;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.width = 1;
    this.height = 1;
    this.minimapPreview = null;
    this.exclusionRadiusPx = 0;
    this.cache = [];
    this.inFlight = false;
    // Bumped on every image change; results of raster requests queued for an
    // older generation are discarded rather than painted over the new image.
    this.generation = 0;
  }

  /** A new orthomosaic invalidates every derived raster view. */
  async setImage(info) {
    this.image = info;
    this.preview = null;
    this.native = null;
    this.nativeRequest = null;
    this.minimapPreview = null;
    this.cache = [];
    this.generation += 1;
    if (!info) return;
    const image = await loadImage(api.previewUrl());
    if (this.image !== info) return;
    this.preview = image;
    this.minimapPreview = image;
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.width = Math.max(1, rect.width);
    this.height = Math.max(1, rect.height);
    const w = Math.round(this.width * this.dpr);
    const h = Math.round(this.height * this.dpr);
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
  }

  #visible(frame) {
    const { camera } = frame;
    const { width, height } = this.image;
    return camera.visibleRect(width, height, 40);
  }

  /**
   * Ask for a native crop when a preview pixel is stretched beyond usefulness.
   *
   * Requests are serialised and the newest one wins: while a crop is in flight
   * the next request replaces the queued one rather than piling up, which is
   * what makes continuous zooming feel smooth instead of queued.
   */
  #maybeRequestNative(frame) {
    const { camera } = frame;
    const info = this.image;
    const previewScale = (info.preview_width || info.width) / info.width;
    const previewPixelOnScreen = camera.scale / previewScale;
    if (previewPixelOnScreen < 1.4) return;

    const rect = this.#visible(frame);
    const key = `${rect.x0.toFixed(0)}:${rect.y0.toFixed(0)}:${rect.x1.toFixed(0)}:${rect.y1.toFixed(0)}`;
    if (this.native && this.native.key === key) return;
    if (this.nativeRequest && this.nativeRequest.key === key) return;

    const screenSide = Math.max(
      (rect.x1 - rect.x0) * camera.scale,
      (rect.y1 - rect.y0) * camera.scale,
    );
    const maxDim = Math.max(256, Math.min(NATIVE_MAX_DIM, Math.ceil(screenSide)));
    this.nativeRequest = { key, rect, maxDim, generation: this.generation };
    this.#fetchNative();
  }

  async #fetchNative() {
    if (this.inFlight || !this.nativeRequest) return;
    this.inFlight = true;
    while (this.nativeRequest) {
      const request = this.nativeRequest;
      this.nativeRequest = null;
      const image = await loadImage(api.cropUrl(request.rect, request.maxDim));
      // The image changed while this was in flight: drop the result and keep
      // draining, since the queue may now hold a request for the new image.
      if (request.generation !== this.generation) continue;
      if (!image) break;
      this.native = { ...request, image };
      this.cache = this.cache.concat(this.native).slice(-NATIVE_CACHE_LIMIT);
      if (this.onLayer) this.onLayer();
    }
    this.inFlight = false;
  }

  /** Is a cached crop good enough to paint the current viewport outright? */
  #nativeFor(frame) {
    const rect = this.#visible(frame);
    const candidates = [];

    const covers = (candidate) =>
      candidate &&
      candidate.rect.x0 <= rect.x0 + 1 &&
      candidate.rect.y0 <= rect.y0 + 1 &&
      candidate.rect.x1 >= rect.x1 - 1 &&
      candidate.rect.y1 >= rect.y1 - 1;

    if (covers(this.native)) candidates.push(this.native);
    for (const entry of this.cache) if (covers(entry)) candidates.push(entry);
    if (!candidates.length) return null;
    // Prefer the smallest crop that still covers, since it carries the most
    // detail per pixel for the area actually on screen.
    candidates.sort(
      (a, b) =>
        (a.rect.x1 - a.rect.x0) * (a.rect.y1 - a.rect.y0) -
        (b.rect.x1 - b.rect.x0) * (b.rect.y1 - b.rect.y0),
    );
    return candidates[0];
  }

  draw(frame) {
    const { camera, state } = frame;
    const ctx = this.ctx;
    if (!this.image) {
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.clearRect(0, 0, this.width, this.height);
      return;
    }

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = this.colors.stage;
    ctx.fillRect(0, 0, this.width, this.height);

    this.#maybeRequestNative(frame);
    this.#paintRaster(frame);
    this.#paintCensus(frame);
    this.#paintRoi(frame);
    this.#paintCorrections(frame);
    this.#paintCursor(frame);
  }

  #paintRaster(frame) {
    const ctx = this.ctx;
    const { camera } = frame;
    const { width, height } = this.image;
    const [sx, sy] = camera.toScreen(0, 0);
    const w = width * camera.scale;
    const h = height * camera.scale;

    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';

    if (this.preview) {
      ctx.drawImage(this.preview, sx, sy, w, h);
    } else {
      ctx.fillStyle = '#0d1013';
      ctx.fillRect(sx, sy, w, h);
    }

    const native = this.#nativeFor(frame);
    if (native) {
      const { rect } = native;
      const [nx, ny] = camera.toScreen(rect.x0, rect.y0);
      const nw = (rect.x1 - rect.x0) * camera.scale;
      const nh = (rect.y1 - rect.y0) * camera.scale;
      ctx.drawImage(native.image, nx, ny, nw, nh);
    }

    // A hairline around the mosaic so its extent is unambiguous against the
    // stage background when zoomed out.
    ctx.strokeStyle = this.colors.line;
    ctx.lineWidth = 1;
    ctx.strokeRect(sx + 0.5, sy + 0.5, w - 1, h - 1);
  }

  #paintCensus(frame) {
    const { state, camera, hoverIndex } = frame;
    const result = state.census;
    if (!result || !state.overlays.palms) return;

    const ctx = this.ctx;
    const palms = result.palms;
    if (!palms.length) return;

    const view = this.#visible(frame);
    const margin = 30 / camera.scale;
    const showIds = state.overlays.ids;
    // Two different circles, two different meanings, two different switches.
    // The merge radius is the floor (half the minimum spacing), so it can never
    // reach a neighbour's centre; the rosette radius is *measured per palm* and
    // on a closed canopy it legitimately overlaps neighbouring crowns. Drawing
    // both under one tick, labelled "merge-radius circles", read as the floor
    // being broken.
    const showMerge = state.overlays.exclusion;
    const showRosette = state.overlays.rosette;
    const radiusOnScreen = (this.exclusionRadiusPx || 0) * camera.scale;

    ctx.lineWidth = 1;
    let labelled = 0;

    for (let i = 0; i < palms.length; i += 1) {
      const palm = palms[i];
      if (
        palm.x_px < view.x0 - margin ||
        palm.x_px > view.x1 + margin ||
        palm.y_px < view.y0 - margin ||
        palm.y_px > view.y1 + margin
      ) {
        continue;
      }

      const [sx, sy] = camera.toScreen(palm.x_px, palm.y_px);

      if (showMerge && radiusOnScreen > 4) {
        const r = Math.min(radiusOnScreen, 400);
        ctx.beginPath();
        ctx.arc(sx, sy, r, 0, Math.PI * 2);
        ctx.strokeStyle = palm.manual ? 'rgba(90,169,230,0.75)' : 'rgba(110,231,168,0.45)';
        ctx.stroke();
      }

      // Gated by its own switch. It never had one: the measured-rosette rings
      // were drawn unconditionally whenever they were big enough on screen, so
      // they appeared on top of the merge rings with no way to turn them off --
      // and on a closed canopy they overlap neighbouring crowns by construction.
      const rosetteOnScreen = palm.rosette_px * camera.scale;
      if (showRosette && rosetteOnScreen >= 3.5) {
        ctx.beginPath();
        ctx.arc(sx, sy, Math.min(rosetteOnScreen, 200), 0, Math.PI * 2);
        ctx.strokeStyle = palm.manual ? this.colors.info : 'rgba(110,231,168,0.8)';
        ctx.stroke();
      }

      const size = Math.max(PALM_MARKER_MIN, Math.min(PALM_MARKER_MAX, rosetteOnScreen * 0.5));
      const isHovered = i === hoverIndex;

      ctx.beginPath();
      if (palm.manual) {
        ctx.rect(sx - size, sy - size, size * 2, size * 2);
      } else {
        ctx.arc(sx, sy, size, 0, Math.PI * 2);
      }
      ctx.fillStyle = palm.manual ? this.colors.info : this.colors.palm;
      ctx.fill();
      ctx.strokeStyle = this.colors.ink;
      ctx.lineWidth = 1;
      ctx.stroke();

      if (isHovered) {
        ctx.beginPath();
        ctx.arc(sx, sy, Math.max(size + 3, 5), 0, Math.PI * 2);
        ctx.strokeStyle = this.colors.roi;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      if (showIds && labelled < LABEL_LIMIT && camera.scale > 0.06) {
        labelled += 1;
        ctx.font = '10px ui-monospace, "Cascadia Mono", monospace';
        ctx.fillStyle = this.colors.ink;
        ctx.fillStyle = isHovered ? this.colors.roi : 'rgba(230, 233, 237, 0.72)';
        ctx.fillText(String(palm.id), sx + size + 3, sy + 3);
      }
    }
  }

  #paintRoi(frame) {
    const { state, camera, draft } = frame;
    const ctx = this.ctx;

    const polygon = state.roi;
    if (polygon.length >= 2) {
      const points = polygon;
      ctx.beginPath();
      const [x0, y0] = camera.toScreen(points[0][0], points[0][1]);
      ctx.moveTo(x0, y0);
      for (let i = 1; i < points.length; i += 1) {
        const [x, y] = camera.toScreen(points[i][0], points[i][1]);
        ctx.lineTo(x, y);
      }
      if (state.roiClosed && points.length >= 3) {
        ctx.closePath();
        ctx.fillStyle = this.colors.roiFill;
        ctx.fill();
      }
      ctx.strokeStyle = this.colors.roi;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      if (state.overlays.edges && state.roiClosed && points.length >= 3) {
        this.#paintEdgeLabels(frame, points);
      }
    }

    // Vertices, and the closing hint on the first one.
    if (polygon.length && ['polygon', 'edit'].includes(state.tool)) {
      for (let i = 0; i < polygon.length; i += 1) {
        const [sx, sy] = camera.toScreen(polygon[i][0], polygon[i][1]);
        const isFirst = i === 0 && !state.roiClosed && polygon.length >= 3;
        ctx.beginPath();
        ctx.rect(sx - 3, sy - 3, 6, 6);
        ctx.fillStyle = isFirst ? this.colors.roi : this.colors.ink;
        ctx.fill();
        ctx.strokeStyle = this.colors.roi;
        ctx.lineWidth = isFirst ? 2 : 1.2;
        ctx.stroke();
      }
    }

    // Rubber band to the pointer, only while a polygon is still open.
    const tail = draft && draft.length ? draft : (state.roiClosed ? null : polygon);
    if (tail && tail.length && frame.cursor && !frame.pointerDown) {
      const [lx, ly] = camera.toScreen(tail[tail.length - 1][0], tail[tail.length - 1][1]);
      ctx.beginPath();
      ctx.moveTo(lx, ly);
      ctx.lineTo(frame.cursor.sx, frame.cursor.sy);
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = 'rgba(216, 161, 60, 0.7)';
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  #paintEdgeLabels(frame, points) {
    const ctx = this.ctx;
    const { camera, state } = frame;
    const mPerPx = state.gsdCm / 100;
    ctx.font = '10px ui-monospace, "Cascadia Mono", monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    for (let i = 0; i < points.length; i += 1) {
      const [x1, y1] = camera.toScreen(points[i][0], points[i][1]);
      const [x2, y2] = camera.toScreen(
        points[(i + 1) % points.length][0],
        points[(i + 1) % points.length][1],
      );
      if (Math.hypot(x2 - x1, y2 - y1) < EDGE_LABEL_MIN_PX) continue;
      const metres = Math.hypot(
        points[(i + 1) % points.length][0] - points[i][0],
        points[(i + 1) % points.length][1] - points[i][1],
      ) * mPerPx;
      const label = metres >= 100 ? `${(metres / 1000).toFixed(2)} km` : `${metres.toFixed(1)} m`;
      const mx = (x1 + x2) / 2;
      const my = (y1 + y2) / 2;
      const width = ctx.measureText(label).width + 8;
      ctx.fillStyle = 'rgba(8, 10, 13, 0.82)';
      ctx.fillRect(mx - width / 2, my - 8, width, 15);
      ctx.fillStyle = this.colors.roi;
      ctx.fillText(label, mx, my);
    }
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
  }

  /** Manual additions and deletions, which exist before a census is run. */
  #paintCorrections(frame) {
    const { state, camera } = frame;
    const ctx = this.ctx;
    if (state.tool !== 'edit') return;

    ctx.lineWidth = 1.4;
    for (const [x, y] of state.manualAdd) {
      const [sx, sy] = camera.toScreen(x, y);
      ctx.beginPath();
      ctx.rect(sx - 4, sy - 4, 8, 8);
      ctx.strokeStyle = this.colors.info;
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(sx - 6, sy);
      ctx.lineTo(sx + 6, sy);
      ctx.moveTo(sx, sy - 6);
      ctx.lineTo(sx, sy + 6);
      ctx.stroke();
    }
    for (const [x, y] of state.manualRemove) {
      const [sx, sy] = camera.toScreen(x, y);
      ctx.beginPath();
      ctx.arc(sx, sy, 5, 0, Math.PI * 2);
      ctx.strokeStyle = this.colors.bad;
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(sx - 4, sy - 4);
      ctx.lineTo(sx + 4, sy + 4);
      ctx.moveTo(sx + 4, sy - 4);
      ctx.lineTo(sx - 4, sy + 4);
      ctx.stroke();
    }
  }

  #paintCursor(frame) {
    const { cursor, state } = frame;
    if (!cursor || !['polygon', 'box'].includes(state.tool)) return;
    const ctx = this.ctx;
    ctx.beginPath();
    ctx.moveTo(cursor.sx - 7, cursor.sy);
    ctx.lineTo(cursor.sx + 7, cursor.sy);
    ctx.moveTo(cursor.sx, cursor.sy - 7);
    ctx.lineTo(cursor.sx, cursor.sy + 7);
    ctx.strokeStyle = 'rgba(230, 233, 237, 0.5)';
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  /** The overview navigator: whole mosaic, region outline, palms, view window. */
  drawMinimap(frame) {
    const { state, camera } = frame;
    const ctx = this.minimapCtx;
    if (!ctx || !this.image) return;

    const size = this.minimapCanvas.width;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = '#0a0c0f';
    ctx.fillRect(0, 0, size, size);

    const scale = Math.min(size / this.image.width, size / this.image.height);
    const offsetX = (size - this.image.width * scale) / 2;
    const offsetY = (size - this.image.height * scale) / 2;
    const project = (x, y) => [offsetX + x * scale, offsetY + y * scale];

    if (this.minimapPreview) {
      ctx.globalAlpha = 0.85;
      ctx.drawImage(this.minimapPreview, offsetX, offsetY, this.image.width * scale, this.image.height * scale);
      ctx.globalAlpha = 1;
    }

    const result = state.census;
    if (result && state.overlays.palms && result.palms.length <= 40000) {
      ctx.fillStyle = this.colors.palm;
      for (const palm of result.palms) {
        const [x, y] = project(palm.x_px, palm.y_px);
        ctx.fillRect(x, y, 1.2, 1.2);
      }
    }

    if (state.roi.length >= 2) {
      ctx.beginPath();
      state.roi.forEach(([x, y], index) => {
        const [px, py] = project(x, y);
        if (index === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      if (state.roiClosed) ctx.closePath();
      ctx.strokeStyle = this.colors.roi;
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }

    const view = camera.visibleRect(this.image.width, this.image.height, 0);
    const [vx, vy] = project(view.x0, view.y0);
    const vw = Math.max(2, (view.x1 - view.x0) * scale);
    const vh = Math.max(2, (view.y1 - view.y0) * scale);
    ctx.strokeStyle = 'rgba(230, 233, 237, 0.65)';
    ctx.lineWidth = 1;
    ctx.strokeRect(vx + 0.5, vy + 0.5, vw - 1, vh - 1);
  }

  /** Map a minimap click back to image pixels, for click-to-navigate. */
  minimapToImage(clientX, clientY) {
    if (!this.image) return null;
    const rect = this.minimapCanvas.getBoundingClientRect();
    const size = this.minimapCanvas.width;
    const scale = Math.min(size / this.image.width, size / this.image.height);
    const offsetX = (size - this.image.width * scale) / 2;
    const offsetY = (size - this.image.height * scale) / 2;
    const px = ((clientX - rect.left) / rect.width) * size;
    const py = ((clientY - rect.top) / rect.height) * size;
    return [(px - offsetX) / scale, (py - offsetY) / scale];
  }

  /** The current view as a PNG blob, for the in-app "view PNG" export. */
  toBlob() {
    return new Promise((resolve) => this.canvas.toBlob((blob) => resolve(blob), 'image/png'));
  }

  /** Set by the app so a late-arriving crop can trigger one more repaint. */
  onLayer = null;
}

/** Ground area of a region, in hectares. Used for the rail's live readout. */
export function roiAreaHa(points, mPerPx) {
  return (polygonAreaPx(points) * mPerPx * mPerPx) / 10000;
}
