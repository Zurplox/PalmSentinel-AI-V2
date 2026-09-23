/**
 * Wiring.
 *
 * The only module that knows about all the others. It owns the animation loop
 * and the action handlers, and it deliberately contains no domain arithmetic:
 * every number the interface shows was computed by the Python core.
 */

import { api, saveBlob } from './api.js';
import { Camera } from './camera.js';
import { UniformGrid } from './geometry.js';
import { Panels } from './panels.js';
import { MapRenderer } from './render.js';
import { store, initialState } from './state.js';
import { ToolController } from './tools.js';

const canvas = document.getElementById('canvas');
const stage = document.getElementById('stage');
const minimap = document.getElementById('minimap');

store.patch(initialState);

const camera = new Camera();
const renderer = new MapRenderer(canvas, minimap);
const view = { cursor: null, hoverIndex: -1, pointerDown: false, draft: null, grid: null };

let dirty = true;
const redraw = () => {
  dirty = true;
};
renderer.onLayer = redraw;

const panels = new Panels({
  store,
  camera,
  renderer,
  view,
  actions: {
    redraw,
    undo: () => tools.undo(),
    clearRegion: () => tools.clearRegion(),
    fit,
    actual,
    centreOn,
    loadFile,
    loadPath,
    setGsd,
    calibrateGsdFromArea,
    runCensus,
    exportCsv,
    exportGeoJson,
    exportAnnotated,
    exportPng,
  },
});

const tools = new ToolController({
  stage,
  camera,
  view,
  store,
  redraw,
  onCursor: (cursor) => panels.updateCursor(store.get(), cursor),
  onHoverChange: (index) => panels.setHoverIndex(index),
  onRun: () => runCensus(),
  onFit: () => fit(),
  onActual: () => actual(),
});

panels.mount();

// -- frame loop -------------------------------------------------------------

function frame() {
  requestAnimationFrame(frame);
  if (!dirty) return;
  dirty = false;
  const state = store.get();
  renderer.draw({ state, camera, ...view });
  if (state.overlays.minimap) renderer.drawMinimap({ state, camera });
  panels.updateScalebar(state, camera);
  document.getElementById('ro-zoom').textContent =
    `${(camera.scale * 100).toFixed(camera.scale < 0.1 ? 2 : 0)}%`;
}

requestAnimationFrame(frame);

// -- camera helpers ---------------------------------------------------------

function fit() {
  const state = store.get();
  if (!state.image) return;
  camera.setImageLimits(state.image.width, state.image.height);
  camera.fit(state.image.width, state.image.height);
  redraw();
}

function actual() {
  const state = store.get();
  if (!state.image) return;
  camera.setImageLimits(state.image.width, state.image.height);
  camera.actualSize(state.image.width, state.image.height);
  redraw();
}

function centreOn(x, y) {
  const state = store.get();
  if (!state.image) return;
  camera.centreOn(x, y, state.image.width, state.image.height);
  redraw();
}

// -- reactive rendering -----------------------------------------------------

let gridKey = null;

store.subscribe((state) => {
  panels.render(state);

  const census = state.census;
  const key = census
    ? `${census.total_palms}:${census.min_spacing_px}`
    : null;
  if (key !== gridKey) {
    gridKey = key;
    view.grid = census
      ? new UniformGrid(census.palms, Math.max(24, census.min_spacing_px))
      : null;
    view.hoverIndex = -1;
  }

  const standard = state.standards[state.standardKey];
  // The overlay shows the *merge* radius -- half the spacing floor -- because
  // that is the distance at which a second detection becomes the same palm.
  // Drawing the full floor made every circle overlap several neighbours and
  // read as an error rather than a floor.
  renderer.exclusionRadiusPx = standard
    ? standard.min_spacing_m / (state.gsdCm / 100) / 2
    : 0;

  redraw();
});

// -- actions ----------------------------------------------------------------

async function loadFile(file) {
  try {
    panels.setBusy(true, `Decoding ${file.name}…`);
    panels.setStatus('busy', 'decoding');
    const response = await api.loadFile(file, store.get().gsdCm);
    await adoptImage(response.image, `${response.decode_seconds}s to decode`);
  } catch (error) {
    panels.setStatus('error', 'failed');
    panels.toast('error', error.message);
  } finally {
    panels.setBusy(false);
  }
}

async function loadPath(path) {
  try {
    panels.setBusy(true, 'Decoding…');
    panels.setStatus('busy', 'decoding');
    const response = await api.loadPath(path, store.get().gsdCm);
    await adoptImage(response.image, `${response.decode_seconds}s to decode`);
  } catch (error) {
    panels.setStatus('error', 'failed');
    panels.toast('error', error.message);
  } finally {
    panels.setBusy(false);
  }
}

async function adoptImage(info, suffix) {
  const images = (await api.images()).images;
  store.patch({
    image: info,
    images,
    gsdCm: info.gsd_cm_per_px,
    census: null,
    roi: [],
    roiClosed: false,
    manualAdd: [],
    manualRemove: [],
  });
  renderer.setImage(info).then(redraw);
  camera.setImageLimits(info.width, info.height);
  camera.fit(info.width, info.height);
  panels.setStatus('ok', 'loaded');
  panels.toast('ok', `${info.filename} loaded — ${info.megapixels} MP${suffix ? `, ${suffix}` : ''}.`);
  redraw();
}

async function setGsd(value) {
  try {
    const response = await api.gsd(value);
    store.patch({ gsdCm: response.image.gsd_cm_per_px, image: response.image });
    redraw();
  } catch (error) {
    panels.toast('error', error.message);
  }
}

/**
 * Solve the GSD from a trusted area: the whole orthomosaic is known to cover
 * N ha, so pixels² · (GSD)² = area. Only a guess-improver for the common case
 * where the drone log is lost but the estate record is not.
 */
function calibrateGsdFromArea(areaHa) {
  const state = store.get();
  if (!state.image || !(areaHa > 0)) return;
  const current = state.image.full_area_ha; // hectares at the current GSD
  if (!(current > 0)) return;
  // Area scales with GSD², so the corrected GSD is the current one scaled by
  // sqrt(target/current).
  const corrected = state.gsdCm * Math.sqrt(areaHa / current);
  setGsd(Math.round(corrected * 1000) / 1000);
  panels.toast('ok', `GSD set to ${corrected.toFixed(3)} cm/px so the mosaic covers ${areaHa} ha.`);
}

async function runCensus() {
  const state = store.get();
  if (!state.image) {
    panels.toast('warn', 'Load an orthomosaic before running a census.');
    return;
  }
  if (state.roi.length && state.roi.length < 3) {
    panels.toast('warn', 'A survey region needs at least three vertices, or none at all.');
    return;
  }

  panels.setBusy(true, 'Censusing…');
  panels.setStatus('busy', 'censusing');
  try {
    const payload = {
      polygon: state.roi.length >= 3 ? state.roi : null,
      standard: state.standardKey,
      gsd_cm: state.gsdCm,
      tile_px: state.tilePx,
      sensitivity: state.sensitivity > 0 ? state.sensitivity : 0,
      scale_from_image: Boolean(state.scaleFromImage),
      manual_add: state.manualAdd,
      manual_remove: state.manualRemove,
    };
    const result = await api.census(payload);
    store.patch({ census: result });
    panels.setStatus('ok', `${result.total_palms} palms`);
    panels.toast(
      result.total_palms ? 'ok' : 'warn',
      `${result.total_palms} palms over ${result.area_ha} ha — ${result.sph} SPH, ${result.sph_band.label}.`,
    );
    if (result.quality.manual_additions_rejected) {
      panels.toast(
        'warn',
        `${result.quality.manual_additions_rejected} operator marker(s) were dropped for being inside the spacing floor.`,
      );
    }
  } catch (error) {
    panels.setStatus('error', 'failed');
    panels.toast('error', error.message);
  } finally {
    panels.setBusy(false);
  }
}

async function exportWith(fn, label) {
  if (!store.get().census) {
    panels.toast('warn', 'Run a census before exporting.');
    return;
  }
  try {
    const { blob, filename } = await fn(store.get().blockName);
    saveBlob(blob, filename);
    panels.toast('ok', `${label} export saved as ${filename}.`);
  } catch (error) {
    panels.toast('error', error.message);
  }
}

// Declared as hoisted functions, not consts: the panel actions object above is
// built at module evaluation time and reads these bindings immediately.
function exportCsv() {
  return exportWith(api.exportCsv, 'CSV');
}

function exportGeoJson() {
  return exportWith(api.exportGeoJson, 'GeoJSON');
}

function exportAnnotated() {
  return exportWith(api.exportAnnotated, 'Annotated JPEG');
}

async function exportPng() {
  if (!store.get().census) {
    panels.toast('warn', 'Run a census before exporting.');
    return;
  }
  try {
    renderer.draw({ state: store.get(), camera, ...view });
    const blob = await renderer.toBlob();
    saveBlob(blob, `view_${store.get().blockName.replace(/\s+/g, '_')}.png`);
    panels.toast('ok', 'Current view exported as PNG.');
  } catch (error) {
    panels.toast('error', error.message);
  }
}

// -- keyboard ---------------------------------------------------------------

window.addEventListener('keydown', (event) => {
  // A modal dialog owns the keyboard while it is open. Otherwise the tool
  // shortcuts fire on the map underneath it, and -- because the tool handler
  // claims Escape and calls preventDefault -- the browser's own "Escape closes
  // the dialog" action is suppressed and the reference sheet cannot be
  // dismissed. Re-invoking showModal() on an open dialog also throws.
  if (document.querySelector('dialog[open]')) return;

  if (event.key === '?' || (event.key === '/' && event.shiftKey)) {
    document.getElementById('shortcuts').showModal();
    return;
  }
  if (tools.handleKey(event)) event.preventDefault();
});
window.addEventListener('keyup', (event) => {
  if (tools.handleKeyUp(event)) event.preventDefault();
});
window.addEventListener('blur', () => {
  tools.spaceHeld = false;
});

// -- viewport ---------------------------------------------------------------

function syncSize() {
  const rect = stage.getBoundingClientRect();
  camera.resize(rect.width, rect.height);
  renderer.resize();
  const state = store.get();
  if (state.image) camera.clamp(state.image.width, state.image.height);
  redraw();
}

new ResizeObserver(syncSize).observe(stage);
window.addEventListener('resize', syncSize);

// -- bootstrap --------------------------------------------------------------

async function boot() {
  try {
    const state = await api.state();
    store.patch({
      image: state.image,
      images: state.images,
      standards: state.standards,
      sphBands: state.sph_bands,
      industryTargetSph: state.industry_target_sph,
      standardKey: state.default_standard,
      gsdCm: state.image ? state.image.gsd_cm_per_px : 4.0,
    });

    syncSize();

    if (state.image) {
      await renderer.setImage(state.image);
      camera.setImageLimits(state.image.width, state.image.height);
      camera.fit(state.image.width, state.image.height);
      panels.setStatus('ok', 'loaded');
    } else {
      panels.setStatus('idle', 'awaiting imagery');
      panels.toast('warn', 'No orthomosaic in the data folder. Load one to begin.');
    }
    redraw();
  } catch (error) {
    panels.setStatus('error', 'offline');
    panels.toast('error', `Could not reach the local server: ${error.message}`);
  }
}

boot();
