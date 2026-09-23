/**
 * The viewport transform.
 *
 * The camera maps **full-resolution image pixels** to screen pixels:
 *
 *     screen = image * scale + translate
 *
 * It is not a display-space transform. The canvas paints a downscaled preview
 * into the full-resolution rectangle, so a click at screen (sx, sy) yields the
 * same pixel number the census will be given, with no conversion step anywhere.
 * A maintainer can therefore trace a coordinate from a mouse event to an export
 * row without leaving this file.
 */

export class Camera {
  constructor() {
    this.scale = 1;
    this.tx = 0;
    this.ty = 0;
    this.vw = 1;
    this.vh = 1;
    this.minScale = 0.0001;
    this.maxScale = 64;
  }

  resize(width, height) {
    this.vw = Math.max(1, width);
    this.vh = Math.max(1, height);
  }

  toScreen(x, y) {
    return [x * this.scale + this.tx, y * this.scale + this.ty];
  }

  toImage(sx, sy) {
    return [(sx - this.tx) / this.scale, (sy - this.ty) / this.scale];
  }

  /**
   * Bounds are set by the image, not by a fixed cap: a 140 MP mosaic needs a
   * smaller fit-scale than a 1 MP crop, and the maximum should let one sensor
   * pixel occupy a legible number of screen pixels without going absurd.
   */
  setImageLimits(imageWidth, imageHeight) {
    const fit = this.fitScale(imageWidth, imageHeight);
    this.minScale = fit * 0.35;
    this.maxScale = Math.max(4, 48);
  }

  fitScale(imageWidth, imageHeight, padding = 26) {
    const usableW = Math.max(1, this.vw - padding * 2);
    const usableH = Math.max(1, this.vh - padding * 2);
    return Math.min(usableW / imageWidth, usableH / imageHeight);
  }

  fit(imageWidth, imageHeight, padding = 26) {
    this.scale = this.fitScale(imageWidth, imageHeight, padding);
    this.clamp(imageWidth, imageHeight, padding);
  }

  /** One image pixel per screen pixel -- what an agronomist means by "actual size". */
  actualSize(imageWidth, imageHeight, focusX, focusY) {
    const cx = focusX ?? this.vw / 2;
    const cy = focusY ?? this.vh / 2;
    const [ix, iy] = this.toImage(cx, cy);
    this.scale = 1;
    this.tx = cx - ix;
    this.ty = cy - iy;
    this.clamp(imageWidth, imageHeight);
  }

  /** Zoom by `factor` keeping the image point under (sx, sy) pinned. */
  zoomAt(sx, sy, factor, imageWidth, imageHeight, padding = 26) {
    const [ix, iy] = this.toImage(sx, sy);
    const next = Math.max(this.minScale, Math.min(this.maxScale, this.scale * factor));
    if (next === this.scale) return false;
    this.scale = next;
    this.tx = sx - ix * this.scale;
    this.ty = sy - iy * this.scale;
    this.clamp(imageWidth, imageHeight, padding);
    return true;
  }

  zoomBySteps(sx, sy, steps, imageWidth, imageHeight) {
    return this.zoomAt(sx, sy, 1.28 ** steps, imageWidth, imageHeight);
  }

  panBy(dx, dy, imageWidth, imageHeight, padding = 26) {
    this.tx += dx;
    this.ty += dy;
    this.clamp(imageWidth, imageHeight, padding);
  }

  /** Centre the view on an image point without changing zoom. */
  centreOn(x, y, imageWidth, imageHeight, padding = 26) {
    this.tx = this.vw / 2 - x * this.scale;
    this.ty = this.vh / 2 - y * this.scale;
    this.clamp(imageWidth, imageHeight, padding);
  }

  /**
   * Keep the image reachable.
   *
   * When the image is smaller than the viewport on an axis it is centred on that
   * axis, so a fully zoomed-out mosaic sits still instead of drifting under the
   * cursor. When it is larger, its edges may not cross the viewport edge.
   */
  clamp(imageWidth, imageHeight, padding = 26) {
    const drawnW = imageWidth * this.scale;
    const drawnH = imageHeight * this.scale;

    if (drawnW <= this.vw) {
      this.tx = (this.vw - drawnW) / 2;
    } else {
      this.tx = Math.min(padding, Math.max(this.vw - drawnW - padding, this.tx));
    }

    if (drawnH <= this.vh) {
      this.ty = (this.vh - drawnH) / 2;
    } else {
      this.ty = Math.min(padding, Math.max(this.vh - drawnH - padding, this.ty));
    }
  }

  /** The image-space rectangle currently on screen, optionally expanded. */
  visibleRect(imageWidth, imageHeight, margin = 0) {
    const [x0, y0] = this.toImage(-margin, -margin);
    const [x1, y1] = this.toImage(this.vw + margin, this.vh + margin);
    return {
      x0: Math.max(0, x0),
      y0: Math.max(0, y0),
      x1: Math.min(imageWidth, x1),
      y1: Math.min(imageHeight, y1),
    };
  }
}
