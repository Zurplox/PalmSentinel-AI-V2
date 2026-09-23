/**
 * The view layer: every DOM binding in the application lives here.
 *
 * It owns no domain logic. Every number it shows arrives from the server
 * already computed -- areas, densities, bands and the spacing statistics are
 * produced once in `palmsentinel` and never re-derived in the browser. That is
 * deliberate: a second implementation of the agronomy in JavaScript is exactly
 * how a report and its export end up disagreeing.
 */

import { api, saveBlob } from './api.js';
import { int, metres, num } from './geometry.js';
import { roiAreaHa } from './render.js';

const PALM_ROW_LIMIT = 500;
const LOUPE_DEBOUNCE_MS = 130;

const $ = (id) => document.getElementById(id);

export class Panels {
  constructor({ store, camera, renderer, view, actions }) {
    this.store = store;
    this.camera = camera;
    this.renderer = renderer;
    this.view = view;
    this.actions = actions;
    this.sort = { key: 'id', dir: 1 };
    this.loupeTimer = null;
    this.loupeKey = '';
    this.elements = {};
  }

  mount() {
    const e = this.elements;
    for (const id of [
      'file-name', 'file-meta', 'file-summary', 'btn-open-image', 'btn-pick-file', 'btn-fit',
      'btn-native', 'btn-shortcuts', 'file-input', 'btn-upload', 'select-flight', 'input-path',
      'btn-path', 'note-decode', 'input-gsd', 'input-gsd-m', 'note-extent', 'note-altitude',
      'input-area', 'btn-calibrate', 'note-calibrate', 'seg-standard', 'note-standard',
      'note-spacing', 'tool-toolbar', 'btn-undo', 'btn-clear', 'note-roi', 'input-block',
      'btn-run', 'run-label', 'note-run', 'input-tile', 'out-tile', 'input-threshold',
      'out-threshold', 'loupe-img', 'loupe-empty', 'loupe-overlay', 'loupe-ring',
      'loupe-readout', 'stage', 'canvas', 'loading', 'loading-label', 'stage-empty',
      'navigator', 'nav-res', 'nav-body', 'minimap', 'nav-view', 'dropzone', 'ro-xy', 'ro-m',
      'ro-zoom', 'ro-scale', 'ro-gsd', 'ro-layer', 'scalebar', 'panel-state',
      'results-placeholder',
      'results', 'k-palms', 'k-area', 'k-sph', 'k-band', 'density', 'density-target',
      'density-marker', 'density-min', 'density-ref', 'density-max', 'density-note',
      's-median', 's-mode', 's-range', 's-pitch', 'spacing-verdict', 'quality-checks',
      'q-candidates', 'q-suppressed', 'q-closest', 'q-tiles', 'q-threshold', 'q-rosette',
      'q-pitch', 'q-elapsed', 'palm-count-hint', 'palm-rows', 'palm-table-note', 'btn-csv',
      'btn-geojson', 'btn-annotated', 'btn-png', 'library', 'library-list', 'library-dir',
      'shortcuts', 'toasts',
    ]) {
      e[id] = $(id);
    }

    for (const input of ['chk-palms', 'chk-ids', 'chk-exclusion', 'chk-rosette', 'chk-minimap', 'chk-edges', 'chk-scale-image']) {
      e[input] = $(input);
    }
    this.#bind();
  }

  #bind() {
    const e = this.elements;

    e['btn-upload'].addEventListener('click', () => e['file-input'].click());
    e['btn-open-image'].addEventListener('click', () => e['file-input'].click());
    e['file-input'].addEventListener('change', (event) => {
      const file = event.target.files?.[0];
      if (file) this.actions.loadFile(file);
      event.target.value = '';
    });

    e['btn-pick-file'].addEventListener('click', () => this.openLibrary());

    e['btn-path'].addEventListener('click', () => {
      const path = e['input-path'].value.trim();
      if (path) this.actions.loadPath(path);
    });
    e['input-path'].addEventListener('keydown', (event) => {
      if (event.key === 'Enter') e['btn-path'].click();
    });

    e['select-flight'].addEventListener('change', (event) => {
      if (event.target.value) this.actions.loadPath(event.target.value);
    });

    e['input-gsd'].addEventListener('change', () => {
      const value = Number.parseFloat(e['input-gsd'].value);
      if (Number.isFinite(value) && value > 0) this.actions.setGsd(value);
    });
    e['input-gsd'].addEventListener('input', () => {
      const value = Number.parseFloat(e['input-gsd'].value);
      if (Number.isFinite(value) && value > 0) {
        this.store.patch({ gsdCm: value });
      }
    });

    // The same GSD in metres per pixel, for drone logs quoted in metres.
    e['input-gsd-m'].addEventListener('change', () => {
      const metres = Number.parseFloat(e['input-gsd-m'].value);
      if (Number.isFinite(metres) && metres > 0) this.actions.setGsd(metres * 100);
    });
    e['input-gsd-m'].addEventListener('input', () => {
      const metres = Number.parseFloat(e['input-gsd-m'].value);
      if (Number.isFinite(metres) && metres > 0) {
        this.store.patch({ gsdCm: metres * 100 });
      }
    });

    // Solve the GSD from a trusted area (estate record), as a cross-check.
    const calibrate = () => {
      const ha = Number.parseFloat(e['input-area'].value);
      if (Number.isFinite(ha) && ha > 0) this.actions.calibrateGsdFromArea(ha);
      else this.toast('warn', 'Enter the block area in hectares first.');
    };
    e['btn-calibrate'].addEventListener('click', calibrate);
    e['input-area'].addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        calibrate();
      }
    });

    e['seg-standard'].addEventListener('click', (event) => {
      const button = event.target.closest('button[data-standard]');
      if (button) this.store.patch({ standardKey: button.dataset.standard });
    });

    e['tool-toolbar'].addEventListener('click', (event) => {
      const button = event.target.closest('button[data-tool]');
      if (button) this.store.patch({ tool: button.dataset.tool });
    });

    e['btn-undo'].addEventListener('click', () => this.actions.undo());
    e['btn-clear'].addEventListener('click', () => this.actions.clearRegion());

    for (const key of ['palms', 'ids', 'exclusion', 'rosette', 'minimap', 'edges']) {
      const input = e[`chk-${key}`];
      input.addEventListener('change', () => {
        const overlays = { ...this.store.get().overlays, [key]: input.checked };
        this.store.patch({ overlays });
      });
    }

    e['btn-fit'].addEventListener('click', () => this.actions.fit());
    e['btn-native'].addEventListener('click', () => this.actions.actual());

    e['btn-shortcuts'].addEventListener('click', () => e['shortcuts'].showModal());

    e['input-block'].addEventListener('input', (event) => {
      this.store.patch({ blockName: event.target.value });
    });

    e['input-tile'].addEventListener('input', (event) => {
      const value = Number(event.target.value);
      e['out-tile'].textContent = String(value);
      this.store.patch({ tilePx: value });
    });

    // The slider owns exactly one knob: the fraction of the interval between
    // the Otsu bar and the region background. An absolute ExG threshold stays
    // available to scripts over the API, but the UI must send only the
    // fraction -- the endpoint prefers an absolute value, so patching both
    // here would silently turn the labelled percentage into the ignored one.
    e['input-threshold'].addEventListener('input', (event) => {
      const value = Number(event.target.value);
      e['out-threshold'].textContent = value === 0 ? 'auto' : `${value}%`;
      this.store.patch({ sensitivity: value / 100 });
    });

    e['chk-scale-image'].addEventListener('change', (event) => {
      this.store.patch({ scaleFromImage: event.target.checked });
    });

    e['btn-run'].addEventListener('click', () => this.actions.runCensus());

    e['btn-csv'].addEventListener('click', () => this.actions.exportCsv());
    e['btn-geojson'].addEventListener('click', () => this.actions.exportGeoJson());
    e['btn-annotated'].addEventListener('click', () => this.actions.exportAnnotated());
    e['btn-png'].addEventListener('click', () => this.actions.exportPng());

    e['nav-body'].addEventListener('click', (event) => {
      const point = this.renderer.minimapToImage(event.clientX, event.clientY);
      if (point) this.actions.centreOn(point[0], point[1]);
    });

    e['palm-rows'].addEventListener('mouseover', (event) => {
      const row = event.target.closest('tr[data-index]');
      const index = row ? Number(row.dataset.index) : -1;
      this.view.hoverIndex = index;
      this.setHoverIndex(index);
      this.actions.redraw();
    });
    e['palm-rows'].addEventListener('mouseleave', () => {
      this.view.hoverIndex = -1;
      this.setHoverIndex(-1);
      this.actions.redraw();
    });

    for (const header of document.querySelectorAll('#palm-table thead th[data-sort]')) {
      header.addEventListener('click', () => {
        const key = header.dataset.sort;
        this.sort = { key, dir: this.sort.key === key ? -this.sort.dir : 1 };
        for (const other of document.querySelectorAll('#palm-table thead th')) {
          other.classList.toggle('is-sorted', other === header);
        }
        this.renderPalmRows(this.store.get().census);
      });
    }

    this.#bindDropZone();
  }

  #bindDropZone() {
    const stage = this.elements.stage;
    const zone = this.elements.dropzone;
    let depth = 0;

    stage.addEventListener('dragenter', (event) => {
      event.preventDefault();
      depth += 1;
      zone.hidden = false;
    });
    stage.addEventListener('dragover', (event) => event.preventDefault());
    stage.addEventListener('dragleave', () => {
      depth -= 1;
      if (depth <= 0) {
        depth = 0;
        zone.hidden = true;
      }
    });
    stage.addEventListener('drop', (event) => {
      event.preventDefault();
      depth = 0;
      zone.hidden = true;
      const file = event.dataTransfer?.files?.[0];
      if (file) this.actions.loadFile(file);
    });
  }

  // -- status ---------------------------------------------------------------

  setStatus(kind, text) {
    const state = this.elements['panel-state'];
    state.className = `panel__state${kind === 'ok' ? ' is-ok' : kind === 'busy' ? ' is-busy' : kind === 'error' ? ' is-error' : ''}`;
    state.textContent = text;
    const dot = this.elements['file-summary'].querySelector('.dot');
    dot.className = `dot dot--${kind === 'ok' ? 'ready' : kind === 'busy' ? 'busy' : kind === 'error' ? 'error' : 'idle'}`;
  }

  setBusy(busy, label) {
    this.elements.loading.hidden = !busy;
    if (label) this.elements['loading-label'].textContent = label;
    this.elements['btn-run'].disabled = Boolean(busy);
    if (busy) this.elements['run-label'].textContent = label || 'Working…';
    else this.elements['run-label'].textContent = 'Run census';
  }

  toast(kind, text) {
    const icon = kind === 'error' ? 'i-alert' : kind === 'warn' ? 'i-alert' : 'i-check';
    const node = document.createElement('div');
    node.className = `toast toast--${kind}`;
    node.innerHTML = `<svg viewBox="0 0 24 24"><use href="#${icon}"/></svg><span></span>`;
    node.querySelector('span').textContent = text;
    this.elements.toasts.appendChild(node);
    setTimeout(() => {
      node.style.transition = 'opacity .2s';
      node.style.opacity = '0';
      setTimeout(() => node.remove(), 220);
    }, kind === 'error' ? 6500 : 3600);
  }

  // -- reactive render ------------------------------------------------------

  render(state) {
    const e = this.elements;

    for (const button of e['tool-toolbar'].querySelectorAll('button[data-tool]')) {
      button.classList.toggle('is-active', button.dataset.tool === state.tool);
    }
    // A closed polygon is an object now, not a drawing in progress: the
    // crosshair stands down so the pointer stops inviting another vertex.
    const cursorClasses = { polygon: 'is-crosshair', box: 'is-crosshair', edit: 'is-editing', pan: 'is-panning' };
    const cursor = state.tool === 'polygon' && state.roiClosed ? '' : cursorClasses[state.tool] || '';
    e.stage.className = `stage ${cursor}`.trim();

    e['chk-palms'].checked = state.overlays.palms;
    e['chk-ids'].checked = state.overlays.ids;
    e['chk-exclusion'].checked = state.overlays.exclusion;
    e['chk-rosette'].checked = state.overlays.rosette;
    e['chk-minimap'].checked = state.overlays.minimap;
    e['chk-edges'].checked = state.overlays.edges;
    e.navigator.hidden = !state.overlays.minimap;

    e['input-gsd'].value = String(state.gsdCm);
    if (document.activeElement !== e['input-gsd-m']) {
      e['input-gsd-m'].value = (state.gsdCm / 100).toFixed(3);
    }
    e['input-tile'].value = String(state.tilePx);
    e['out-tile'].textContent = String(state.tilePx);
    e['input-threshold'].value = String(Math.round(state.sensitivity * 100));
    e['out-threshold'].textContent = state.sensitivity === 0 ? 'auto' : `${Math.round(state.sensitivity * 100)}%`;
    e['chk-scale-image'].checked = Boolean(state.scaleFromImage);
    if (document.activeElement !== e['input-block']) e['input-block'].value = state.blockName;

    this.#renderStandards(state);
    this.#renderImageSummary(state);
    this.#renderRoiNote(state);
    this.#renderResults(state);
  }

  #renderStandards(state) {
    const e = this.elements;
    const keys = Object.keys(state.standards);
    if (e['seg-standard'].dataset.built !== keys.join(',')) {
      e['seg-standard'].innerHTML = '';
      for (const key of keys) {
        const standard = state.standards[key];
        const button = document.createElement('button');
        button.dataset.standard = key;
        button.setAttribute('role', 'radio');
        const short = key === 'young' ? 'TBM' : 'TM';
        button.innerHTML = `<b>${short}</b><span>${standard.pitch_m} m</span>`;
        button.title = `${standard.label} — ${standard.pitch_m} m planting pitch`;
        e['seg-standard'].appendChild(button);
      }
      e['seg-standard'].dataset.built = keys.join(',');
    }
    for (const button of e['seg-standard'].querySelectorAll('button')) {
      button.classList.toggle('is-active', button.dataset.standard === state.standardKey);
      button.setAttribute('aria-checked', String(button.dataset.standard === state.standardKey));
    }

    const standard = state.standards[state.standardKey];
    if (!standard) return;
    e['note-standard'].textContent =
      `${standard.pitch_m} m triangular pitch. Detections closer than ` +
      `${standard.min_spacing_m.toFixed(2)} m (${standard.min_spacing_fraction}× pitch) are ` +
      `treated as one palm.`;

    const floorPx = standard.min_spacing_m / (state.gsdCm / 100);
    e['note-spacing'].textContent =
      `Floor is ${floorPx.toFixed(1)} px at ${state.gsdCm} cm/px. ` +
      `The floor comes from the planting standard, never from the imagery.`;
  }

  #renderImageSummary(state) {
    const e = this.elements;
    const image = state.image;

    if (!image) {
      e['file-name'].textContent = 'No orthomosaic loaded';
      e['file-meta'].textContent = '';
      e['stage-empty'].hidden = false;
      e['note-extent'].textContent = '';
      e['ro-gsd'].textContent = `${state.gsdCm} cm/px`;
      return;
    }

    e['stage-empty'].hidden = true;
    e['file-name'].textContent = image.filename;
    e['file-meta'].textContent = `${int(image.width)} × ${int(image.height)} px · ${num(image.megapixels, 1)} MP · ${num(image.gsd_cm_per_px, 2)} cm/px`;
    e['note-extent'].textContent =
      `${num(image.extent_m[0], 1)} m × ${num(image.extent_m[1], 1)} m · ` +
      `${num(image.full_area_ha, 4)} ha at this GSD.`;
    // Cross-check, not a fact: the height above canopy that a ~20 MP camera
    // with an 84° horizontal FOV flies at to produce this GSD (h ≈ GSD·3040).
    // Orthos are stitched, so the drone log is the authority; this catches a
    // GSD typed an order of magnitude wrong.
    e['note-altitude'].hidden = false;
    const altitudeM = Math.round(((image.gsd_cm_per_px / 100) * 3040) / 5) * 5;
    e['note-altitude'].textContent =
      `≈ ${int(altitudeM)} m above canopy for this resolution (20 MP camera, 84° FOV) — cross-check against the drone log.`;
    e['ro-gsd'].textContent = `${state.gsdCm} cm/px`;
    e['nav-res'].textContent = `${num(image.gsd_cm_per_px, 2)} cm/px`;

    const list = e['select-flight'];
    if (list.dataset.count !== String(state.images.length)) {
      list.innerHTML = '<option value="">— none —</option>';
      for (const entry of state.images) {
        const option = document.createElement('option');
        option.value = entry.path;
        option.textContent = `${entry.filename} · ${entry.size_mb} MB`;
        list.appendChild(option);
      }
      list.dataset.count = String(state.images.length);
    }
  }

  #renderRoiNote(state) {
    const e = this.elements;
    const stats = roiAreaHa(state.roi, state.gsdCm / 100);
    const closed = state.roiClosed && state.roi.length >= 3;

    if (!state.roi.length) {
      e['note-roi'].textContent = state.image
        ? `No region selected — a census will cover the whole ${num(state.image.full_area_ha, 4)} ha orthomosaic.`
        : 'No region selected.';
      e['note-roi'].className = 'note';
      return;
    }

    const status = closed ? '' : ' — not closed yet, press Enter to close';
    e['note-roi'].textContent = `${state.roi.length} vertices · ${num(stats, 4)} ha${status}`;
    e['note-roi'].className = closed ? 'note' : 'note note--warn';
  }

  #renderResults(state) {
    const e = this.elements;
    const result = state.census;

    e['results'].hidden = !result;
    e['results-placeholder'].hidden = Boolean(result);
    for (const id of ['btn-csv', 'btn-geojson', 'btn-annotated', 'btn-png']) {
      e[id].disabled = !result;
    }
    if (!result) return;

    e['k-palms'].textContent = int(result.total_palms);
    e['k-area'].innerHTML = `${num(result.area_ha, 4)} <em>ha</em>`;
    e['k-sph'].textContent = num(result.sph, 1);
    e['k-band'].textContent = `${result.sph_band.label} (${result.sph_band.range})`;
    e['k-band'].title = result.sph_band.guidance;

    this.#renderDensity(result);
    this.#renderSpacing(result, state);
    this.#renderQuality(result, state);
    this.renderPalmRows(result);
  }

  #renderDensity(result) {
    const e = this.elements;
    const [lo, hi] = result.industry_target_sph;
    const max = Math.max(300, Math.ceil((result.sph * 1.25) / 50) * 50);
    const position = (value) => `${Math.max(0, Math.min(100, (value / max) * 100))}%`;

    e['density-target'].style.left = position(lo);
    e['density-target'].style.width = `${Math.max(1.5, ((hi - lo) / max) * 100)}%`;
    e['density-marker'].style.left = position(result.sph);
    e['density-max'].textContent = String(max);
    e['density-ref'].textContent = `${lo}–${hi}`;

    const delta = result.sph - (lo + hi) / 2;
    const direction = delta >= 0 ? 'above' : 'below';
    e['density-note'].textContent =
      `${num(Math.abs(delta), 1)} SPH ${direction} the midpoint of the ` +
      `${lo}–${hi} SPH industrial target. ${result.sph_band.guidance}`;
  }

  #renderSpacing(result, state) {
    const e = this.elements;
    const spacing = result.spacing || {};
    e['s-median'].textContent = metres(spacing.nn_median_m);
    e['s-mode'].textContent = metres(spacing.nn_mode_m);
    e['s-range'].textContent =
      `${metres(spacing.nn_p10_m)} – ${metres(spacing.nn_p90_m)}`;
    e['s-pitch'].textContent =
      `${metres(result.min_spacing_m)} minimum · ${metres(
        state.standards[state.standardKey]?.pitch_m,
      )} planting pitch`;

    const median = spacing.nn_median_m;
    const floor = result.min_spacing_m;
    if (!Number.isFinite(median) || !Number.isFinite(floor) || floor <= 0) {
      e['spacing-verdict'].textContent = 'Not enough palms to characterise spacing.';
      e['spacing-verdict'].className = 'note';
      return;
    }

    const ratio = median / floor;
    if (ratio < 0.98) {
      e['spacing-verdict'].textContent =
        `Median spacing ${metres(median)} is at the ${metres(floor)} exclusion floor. ` +
        `Detections are packed against the spacing limit rather than sitting on a planting ` +
        `grid, which is the signature of over-detection.`;
      e['spacing-verdict'].className = 'note note--bad';
    } else if (ratio < 1.05) {
      e['spacing-verdict'].textContent =
        `Median spacing ${metres(median)} sits just above the ${metres(floor)} floor. ` +
        `Treat this count as a lower bound: the floor is still shaping the result.`;
      e['spacing-verdict'].className = 'note note--warn';
    } else {
      e['spacing-verdict'].textContent =
        `Median spacing ${metres(median)} is ${num(ratio, 2)}× the ${metres(floor)} floor, ` +
        `so spacing is being set by the canopy rather than by the suppression radius.`;
      e['spacing-verdict'].className = 'note note--ok';
    }
  }

  #renderQuality(result, state) {
    const e = this.elements;
    const quality = result.quality;
    const checks = [];

    checks.push({
      kind: quality.spacing_invariant_ok ? 'ok' : 'bad',
      text: quality.spacing_invariant_ok
        ? `No two palms are closer than the ${metres(result.min_spacing_m)} spacing floor.`
        : `Two palms are ${num(quality.closest_pair_px, 1)} px apart, inside the ${num(quality.required_spacing_px, 1)} px floor.`,
    });

    checks.push({
      kind: quality.palms_outside_polygon === 0 ? 'ok' : 'bad',
      text: quality.palms_outside_polygon === 0
        ? 'Every palm lies inside the survey region.'
        : `${quality.palms_outside_polygon} palms fall outside the survey region.`,
    });

    const rosette = result.rosette || {};
    const varies = Number.isFinite(rosette.max_m) && rosette.max_m > rosette.min_m * 1.02;
    checks.push({
      kind: varies ? 'ok' : 'warn',
      text: varies
        ? `Rosette radius is measured per palm (${num(rosette.min_m, 2)}–${num(rosette.max_m, 2)} m), not assigned a constant.`
        : 'Rosette radius came out near-constant, so per-palm crown size is not resolving.',
    });

    const tiles = quality.tiles;
    checks.push({
      kind: tiles > 0 ? 'ok' : 'warn',
      text: tiles > 0
        ? `Region covered by ${int(tiles)} tile${tiles === 1 ? '' : 's'} whose cores partition it exactly, so no palm can be counted twice at a seam.`
        : 'Region was processed without tiling.',
    });

    const added = quality.manual_additions;
    const removed = quality.detections_removed_by_operator;
    if (added || removed) {
      const parts = [];
      if (added) parts.push(`${added} added by hand`);
      if (removed) parts.push(`${removed} detection${removed === 1 ? '' : 's'} deleted by hand`);
      checks.push({
        kind: 'ok',
        text: `Operator corrections applied: ${parts.join(', ')}.`,
      });
    }
    if (quality.manual_additions_rejected) {
      checks.push({
        kind: 'warn',
        text: `${quality.manual_additions_rejected} operator addition(s) were rejected for sitting inside the ${metres(result.min_spacing_m)} spacing floor of another palm.`,
      });
    }

    e['quality-checks'].innerHTML = '';
    for (const check of checks) {
      const item = document.createElement('li');
      item.className = `is-${check.kind}`;
      item.textContent = check.text;
      e['quality-checks'].appendChild(item);
    }

    e['q-candidates'].textContent = int(quality.candidates_raw);
    e['q-suppressed'].textContent = `${int(quality.candidates_suppressed)} (${num(
      quality.candidates_raw ? (quality.candidates_suppressed / quality.candidates_raw) * 100 : 0,
      1,
    )}%)`;
    e['q-closest'].textContent = `${num(quality.closest_pair_px, 1)} px / ${num(quality.required_spacing_px, 1)} px`;
    e['q-tiles'].textContent = int(quality.tiles);
    e['q-threshold'].textContent = `${num(result.observation.threshold, 1)} ${result.observation.index.toUpperCase()} · background ${num(result.observation.background, 1)}`;
    {
      const sc = result.scale || {};
      e['q-pitch'].textContent = sc.pitch_estimation_ok
        ? `${num(sc.measured_pitch_px, 1)} px (${num(sc.measured_m_per_px, 4)} m/px)` +
          (sc.gsd_disagreement ? ' — disagrees with typed GSD, verify both' : ' — agrees with typed GSD')
        : 'off (count uses typed GSD)';
    }
    e['q-rosette'].textContent = `${num(rosette.median_m, 2)} m median (${num(rosette.min_m, 2)}–${num(rosette.max_m, 2)} m)`;
    e['q-elapsed'].textContent = `${num(quality.elapsed_s, 2)} s`;
    e['ro-layer'].textContent = state.image ? 'full resolution' : 'preview';
  }

  /**
   * Table side of the palm hover, driven from either direction.
   *
   * ``index`` is a position in ``census.palms`` -- the same index the renderer
   * and the spatial grid use -- and rows carry it in ``data-index``.  Without
   * this the map-to-table half of the highlight did nothing at all: the tool
   * controller called an ``onHoverChange`` hook that no caller supplied, so
   * ``.grid tbody tr.is-hovered`` was styled but never applied.
   */
  setHoverIndex(index) {
    const body = this.elements['palm-rows'];
    const previous = body.querySelector('tr.is-hovered');
    if (previous) previous.classList.remove('is-hovered');
    if (index < 0) return;

    const row = body.querySelector(`tr[data-index="${index}"]`);
    if (!row) return;
    row.classList.add('is-hovered');

    // Reveal it by scrolling only the table's own box, so the surrounding
    // results panel does not jump while the pointer sweeps the map.
    const wrap = row.closest('.table-wrap');
    if (!wrap) return;
    const box = row.getBoundingClientRect();
    const frame = wrap.getBoundingClientRect();
    if (box.top < frame.top) wrap.scrollTop -= frame.top - box.top;
    else if (box.bottom > frame.bottom) wrap.scrollTop += box.bottom - frame.bottom;
  }

  renderPalmRows(result) {
    const e = this.elements;
    const body = e['palm-rows'];
    body.innerHTML = '';
    if (!result) return;

    const palms = result.palms;
    const { key, dir } = this.sort;
    const sorted = palms.slice().sort((a, b) => {
      if (key === 'id') return (a.id - b.id) * dir;
      return ((a[key] ?? 0) - (b[key] ?? 0)) * dir;
    });

    const shown = sorted.slice(0, PALM_ROW_LIMIT);
    const fragment = document.createDocumentFragment();
    for (const palm of shown) {
      const row = document.createElement('tr');
      row.dataset.index = String(palms.indexOf(palm));
      if (palm.manual) row.classList.add('is-in-roi');
      const cells = [
        String(palm.id),
        num(palm.x_m, 2),
        num(palm.y_m, 2),
        num(palm.rosette_m, 2),
        num(palm.peak, 1),
      ];
      for (const value of cells) {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.appendChild(cell);
      }
      if (palm.manual) row.title = 'Placed by an operator';
      fragment.appendChild(row);
    }
    body.appendChild(fragment);

    e['palm-count-hint'].textContent =
      palms.length > PALM_ROW_LIMIT
        ? `${int(palms.length)} palms · showing ${int(PALM_ROW_LIMIT)}`
        : `${int(palms.length)} palms`;
    e['palm-table-note'].textContent =
      palms.length > PALM_ROW_LIMIT
        ? `Sorted by ${key}; the table previews the first ${PALM_ROW_LIMIT} rows. The CSV export contains all ${int(palms.length)}.`
        : `Sorted by ${key}. Metres are measured from the orthomosaic origin.`;
  }

  // -- cursor, loupe, scalebar ---------------------------------------------

  updateCursor(state, cursor) {
    const e = this.elements;
    if (!cursor) {
      e['ro-xy'].textContent = '—';
      e['ro-m'].textContent = '—';
      return;
    }
    const mPerPx = state.gsdCm / 100;
    e['ro-xy'].textContent = `${int(cursor.ix)}, ${int(cursor.iy)}`;
    e['ro-m'].textContent = `${num(cursor.ix * mPerPx, 1)}, ${num(cursor.iy * mPerPx, 1)}`;
    e['ro-zoom'].textContent = `${num(this.camera.scale * 100, this.camera.scale < 0.1 ? 2 : 0)}%`;

    this.#queueLoupe(state, cursor);
  }

  #queueLoupe(state, cursor) {
    const size = state.image ? 256 : 128;
    const key = `${Math.round(cursor.ix / 8)}:${Math.round(cursor.iy / 8)}`;
    if (key === this.loupeKey) return;
    clearTimeout(this.loupeTimer);
    this.loupeTimer = setTimeout(() => {
      this.loupeKey = key;
      this.#showLoupe(state, cursor.ix, cursor.iy, size);
    }, LOUPE_DEBOUNCE_MS);
  }

  #showLoupe(state, x, y, size) {
    const e = this.elements;
    if (!state.image) {
      e['loupe-overlay'].hidden = true;
      e['loupe-img'].hidden = true;
      e['loupe-empty'].hidden = false;
      e['loupe-readout'].textContent = '—';
      return;
    }

    const image = e['loupe-img'];
    image.onload = () => {
      e['loupe-empty'].hidden = true;
      e['loupe-img'].hidden = false;
      e['loupe-overlay'].hidden = false;
    };
    image.src = api.sampleUrl(x, y, size);

    // The ring shows the exclusion floor at true scale: inside this radius a
    // second palm would be merged into this one.
    const mPerPx = state.gsdCm / 100;
    const floorPx = (state.standards[state.standardKey]?.min_spacing_m || 0) / mPerPx;
    const ringPx = Math.min(158, Math.max(6, (floorPx / 2 / (size / 2)) * 160));
    e['loupe-ring'].setAttribute('r', String(ringPx));
    e['loupe-overlay'].setAttribute('viewBox', `0 0 ${size} ${size}`);
    e['loupe-overlay'].setAttribute('preserveAspectRatio', 'xMidYMid slice');
    e['loupe-readout'].textContent =
      `${num(x * mPerPx, 1)}, ${num(y * mPerPx, 1)} m · ${int(x)}, ${int(y)} px`;
    e['loupe-overlay'].hidden = false;
  }

  updateScalebar(state, camera) {
    const e = this.elements;
    if (!state.image) {
      e['ro-scale'].textContent = '';
      e.scalebar.style.display = 'none';
      return;
    }
    e.scalebar.style.display = '';
    const mPerPx = state.gsdCm / 100;
    const pxPerMetre = camera.scale / mPerPx;
    const targetMetres = 84 / pxPerMetre;
    const nice = niceRound(targetMetres);
    const width = Math.max(20, Math.min(160, nice * pxPerMetre));

    const bar = e.scalebar.querySelector('i');
    bar.style.width = `${width}px`;
    bar.style.height = '5px';
    e['ro-scale'].textContent = nice >= 1000 ? `${nice / 1000} km` : `${nice} m`;
  }

  // -- library --------------------------------------------------------------

  async openLibrary() {
    const e = this.elements;
    try {
      const response = await api.images();
      e['library-list'].innerHTML = '';
      if (!response.images.length) {
        const empty = document.createElement('p');
        empty.className = 'note';
        empty.textContent = 'No imagery in the data folder yet. Upload a file or type a path.';
        e['library-list'].appendChild(empty);
      }
      for (const entry of response.images) {
        const button = document.createElement('button');
        button.type = 'button';
        button.innerHTML =
          '<span class="fl-name"><svg viewBox="0 0 24 24"><use href="#i-grid"/></svg>' +
          '<span></span></span><span class="fl-size"></span>';
        button.querySelector('.fl-name span').textContent = entry.filename;
        button.querySelector('.fl-size').textContent = `${entry.size_mb} MB`;
        button.addEventListener('click', () => {
          e.library.close();
          this.actions.loadPath(entry.path);
        });
        e['library-list'].appendChild(button);
      }
      e.library.showModal();
    } catch (error) {
      this.toast('error', error.message);
    }
  }
}

/** 1, 2 or 5 times a power of ten, so a scale bar never reads "137 m". */
function niceRound(value) {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const exponent = Math.floor(Math.log10(value));
  const base = 10 ** exponent;
  const mantissa = value / base;
  const step = mantissa >= 5 ? 5 : mantissa >= 2 ? 2 : 1;
  return step * base;
}
