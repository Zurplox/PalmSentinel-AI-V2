/**
 * Interaction.
 *
 * Owns one thing: turning pointer and keyboard events into changes to the
 * camera or to document state. It draws nothing and formats nothing. All
 * coordinates it produces are full-resolution image pixels, obtained from the
 * camera, which is why a vertex is never off by a display-scale factor.
 */

const VERTEX_HIT_PX = 8;
const CLOSE_HIT_PX = 11;
const PALM_HIT_PX = 14;

export class ToolController {
  /**
   * @param {object} options
   * @param {HTMLElement} options.stage       element receiving pointer input
   * @param {import('./camera.js').Camera} options.camera
   * @param {object} options.view             transient view state, shared with the painter
   * @param {object} options.store            document state
   * @param {Function} options.redraw         request a repaint
   * @param {Function} options.onCursor       cursor moved, for the readout and loupe
   * @param {Function} options.onRun          Ctrl+Enter
   * @param {Function} options.onHoverChange  hovered palm index changed
   */
  constructor(options) {
    Object.assign(this, options);
    this.drag = null;
    this.panning = false;
    this.spaceHeld = false;
    this.boxStart = null;
    this.#bind();
  }

  get image() {
    return this.store.get().image;
  }

  #imageSize() {
    const image = this.image;
    return image ? [image.width, image.height] : [1, 1];
  }

  #local(event) {
    const rect = this.stage.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  }

  #toImage(event) {
    const [sx, sy] = this.#local(event);
    return this.camera.toImage(sx, sy);
  }

  /** Index of the ROI vertex under a screen point, or -1. */
  #vertexAt(sx, sy) {
    const { roi } = this.store.get();
    for (let i = 0; i < roi.length; i += 1) {
      const [vx, vy] = this.camera.toScreen(roi[i][0], roi[i][1]);
      if (Math.hypot(vx - sx, vy - sy) <= VERTEX_HIT_PX) return i;
    }
    return -1;
  }

  #bind() {
    const stage = this.stage;
    stage.addEventListener('pointerdown', (e) => this.#onDown(e));
    stage.addEventListener('pointermove', (e) => this.#onMove(e));
    stage.addEventListener('pointerup', (e) => this.#onUp(e));
    stage.addEventListener('pointerleave', () => {
      this.view.cursor = null;
      this.redraw();
    });
    stage.addEventListener('wheel', (e) => this.#onWheel(e), { passive: false });
    stage.addEventListener('dblclick', (e) => this.#onDoubleClick(e));
    stage.addEventListener('contextmenu', (e) => e.preventDefault());
    // Pointer capture keeps a drag alive when the cursor leaves the canvas,
    // which is the difference between a deliberate box and a lost one.
    stage.addEventListener('pointercancel', () => {
      this.drag = null;
      this.panning = false;
      this.view.pointerDown = false;
      this.view.draft = null;
      this.redraw();
    });
  }

  #onDown(event) {
    if (event.button === 2) return;
    const [sx, sy] = this.#local(event);
    const state = this.store.get();
    this.view.pointerDown = true;
    this.stage.setPointerCapture?.(event.pointerId);

    const wantPan = state.tool === 'pan' || this.spaceHeld || event.button === 1;
    if (wantPan) {
      this.panning = { sx, sy };
      this.stage.classList.add('is-panning');
      return;
    }

    // Vertex editing is available in the drawing tools, and takes priority over
    // starting a new vertex, so a polygon can be adjusted without redrawing it.
    const vertex = this.#vertexAt(sx, sy);
    // Clicking the first vertex of an open polygon closes it. That gesture has
    // to be decided before vertex grabbing, because the grab radius (8 px) sits
    // inside the close radius (11 px): checked second, it swallowed the click
    // and left only a 3-pixel ring where closing with the mouse still worked.
    const closing = state.tool === 'polygon' && this.#wouldClose(sx, sy, state);
    if (vertex >= 0 && !closing && ['polygon', 'box', 'edit'].includes(state.tool)) {
      this.drag = { vertex };
      return;
    }

    const [ix, iy] = this.camera.toImage(sx, sy);

    if (state.tool === 'box') {
      this.boxStart = [ix, iy];
      this.view.draft = [[ix, iy], [ix, iy], [ix, iy], [ix, iy]];
      this.redraw();
      return;
    }

    if (state.tool === 'edit') {
      this.#editClick(event, ix, iy);
      return;
    }

    if (state.tool === 'polygon') {
      this.#polygonClick(sx, sy, ix, iy);
    }
  }

  #polygonClick(sx, sy, ix, iy) {
    const state = this.store.get();
    const roi = state.roi.slice();

    if (state.roiClosed) {
      // A closed region is adjusted by dragging vertices, not by appending.
      this.redraw();
      return;
    }

    if (this.#wouldClose(sx, sy, state)) {
      this.store.patch({ roi, roiClosed: true });
      this.redraw();
      return;
    }

    roi.push([ix, iy]);
    this.store.patch({ roi, roiClosed: false });
    this.redraw();
  }

  /** True when a click at (sx, sy) is on the first vertex of an open polygon. */
  #wouldClose(sx, sy, state) {
    if (state.roiClosed || state.roi.length < 3) return false;
    const [fx, fy] = this.camera.toScreen(state.roi[0][0], state.roi[0][1]);
    return Math.hypot(fx - sx, fy - sy) <= CLOSE_HIT_PX;
  }

  #editClick(event, ix, iy) {
    const state = this.store.get();
    const remove = event.altKey || event.ctrlKey || event.metaKey;

    if (remove) {
      // Prefer an operator's own marker; fall back to the nearest detected palm.
      const manual = this.#nearestIn(state.manualAdd, ix, iy, PALM_HIT_PX / this.camera.scale);
      if (manual >= 0) {
        const manualAdd = state.manualAdd.slice();
        manualAdd.splice(manual, 1);
        this.store.patch({ manualAdd });
      } else if (state.census && this.view.grid) {
        const radius = PALM_HIT_PX / this.camera.scale;
        const index = this.view.grid.nearest(state.census.palms, ix, iy, radius);
        if (index >= 0) {
          const palm = state.census.palms[index];
          this.store.patch({
            manualRemove: state.manualRemove.concat([[palm.x_px, palm.y_px]]),
          });
        }
      }
      this.redraw();
      return;
    }

    this.store.patch({ manualAdd: state.manualAdd.concat([[ix, iy]]) });
    this.redraw();
  }

  #nearestIn(points, x, y, radius) {
    let best = -1;
    let bestSq = radius * radius;
    for (let i = 0; i < points.length; i += 1) {
      const d = (points[i][0] - x) ** 2 + (points[i][1] - y) ** 2;
      if (d <= bestSq) {
        bestSq = d;
        best = i;
      }
    }
    return best;
  }

  #onMove(event) {
    const [sx, sy] = this.#local(event);
    const [ix, iy] = this.camera.toImage(sx, sy);
    const [width, height] = this.#imageSize();

    this.view.cursor = { sx, sy, ix, iy };
    if (this.onCursor) this.onCursor({ ix, iy, sx, sy });

    if (this.panning) {
      this.camera.panBy(sx - this.panning.sx, sy - this.panning.sy, width, height);
      this.panning = { sx, sy };
      this.redraw();
      return;
    }

    if (this.drag) {
      const roi = this.store.get().roi.slice();
      roi[this.drag.vertex] = [ix, iy];
      this.store.patch({ roi });
      this.redraw();
      return;
    }

    if (this.boxStart) {
      this.view.draft = this.#rectangle(this.boxStart, [ix, iy]);
      this.redraw();
      return;
    }

    this.#updateHover(ix, iy);
    this.redraw();
  }

  #updateHover(ix, iy) {
    const state = this.store.get();
    let index = -1;
    if (state.census && this.view.grid && state.overlays.palms) {
      const radius = PALM_HIT_PX / this.camera.scale;
      index = this.view.grid.nearest(state.census.palms, ix, iy, radius);
    }
    if (index !== this.view.hoverIndex) {
      this.view.hoverIndex = index;
      if (this.onHoverChange) this.onHoverChange(index);
    }
  }

  #rectangle(a, b) {
    const x0 = Math.min(a[0], b[0]);
    const y0 = Math.min(a[1], b[1]);
    const x1 = Math.max(a[0], b[0]);
    const y1 = Math.max(a[1], b[1]);
    return [
      [x0, y0],
      [x1, y0],
      [x1, y1],
      [x0, y1],
    ];
  }

  #onUp() {
    if (this.boxStart) {
      const draft = this.view.draft;
      this.boxStart = null;
      this.view.draft = null;
      if (draft) {
        const width = Math.abs(draft[2][0] - draft[0][0]);
        const height = Math.abs(draft[2][1] - draft[0][1]);
        // Ignore an accidental click as opposed to a drag.
        if (width > 3 && height > 3) {
          this.store.patch({ roi: draft, roiClosed: true });
        }
      }
      this.redraw();
    }
    this.drag = null;
    this.panning = null;
    this.view.pointerDown = false;
    this.stage.classList.remove('is-panning');
    this.redraw();
  }

  #onWheel(event) {
    if (!this.image) return;
    event.preventDefault();
    const [sx, sy] = this.#local(event);
    const [width, height] = this.#imageSize();
    const steps = event.deltaY === 0 ? 0 : event.deltaY < 0 ? 1 : -1;
    const magnitude = event.deltaMode === 1 ? 1 : Math.min(3, Math.abs(event.deltaY) / 100 + 1);
    if (this.camera.zoomBySteps(sx, sy, steps * magnitude, width, height)) {
      this.redraw();
      if (this.onCursor) this.onCursor({ ix: null, iy: null, sx, sy, keepLoupe: true });
    }
  }

  #onDoubleClick(event) {
    if (!this.image) return;
    const [sx, sy] = this.#local(event);
    const [width, height] = this.#imageSize();
    this.camera.zoomAt(sx, sy, 2, width, height);
    this.redraw();
  }

  /** Keyboard. Returns true when the key was consumed. */
  handleKey(event) {
    const state = this.store.get();
    const target = event.target;
    if (target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return false;

    if (event.key === ' ') {
      this.spaceHeld = true;
      this.stage.classList.add('is-panning');
      return true;
    }

    if (event.ctrlKey && event.key === 'Enter') {
      if (this.onRun) this.onRun();
      return true;
    }

    if (event.ctrlKey && (event.key === 'z' || event.key === 'Z')) {
      this.undo();
      return true;
    }

    switch (event.key) {
      case 'f':
      case 'F':
        if (this.onFit) this.onFit();
        return true;
      case '1':
        if (this.onActual) this.onActual();
        return true;
      case 'p':
      case 'P':
        this.store.patch({ tool: 'polygon' });
        return true;
      case 'b':
      case 'B':
        this.store.patch({ tool: 'box' });
        return true;
      case 'h':
      case 'H':
        this.store.patch({ tool: 'pan' });
        return true;
      case 'e':
      case 'E':
        this.store.patch({ tool: 'edit' });
        return true;
      case 'Enter':
        if (state.roi.length >= 3 && !state.roiClosed) {
          this.store.patch({ roiClosed: true });
        }
        return true;
      case 'Backspace':
        this.undo();
        return true;
      case 'Escape':
        this.store.patch({ roi: [], roiClosed: false, draft: null });
        this.view.draft = null;
        return true;
      default:
        return false;
    }
  }

  handleKeyUp(event) {
    if (event.key === ' ') {
      this.spaceHeld = false;
      this.stage.classList.remove('is-panning');
      if (!this.panning) this.stage.classList.remove('is-panning');
      return true;
    }
    return false;
  }

  /** Remove the last uncommitted vertex, or reopen a closed region. */
  undo() {
    const state = this.store.get();
    if (state.roiClosed) {
      this.store.patch({ roiClosed: false });
    } else if (state.roi.length) {
      this.store.patch({ roi: state.roi.slice(0, -1), roiClosed: false });
    }
    this.redraw();
  }

  clearRegion() {
    this.store.patch({ roi: [], roiClosed: false, draft: null });
    this.view.draft = null;
    this.redraw();
  }
}
