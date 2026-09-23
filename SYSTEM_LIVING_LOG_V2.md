# 🌴 PALMSENTINEL V2 — SYSTEM MASTER CONTEXT & LIVING HANDOVER LOG

> **Living Engineering Document & Decision Record**
> *Target Audience: Autonomous AI Agents and engineers taking over this project.*
> *Last Updated: 2026-09-23 13:12:00 Local Time*
> *Active Workspace: `E:\Freebuff\Palm Sentinel (V2)`*
> *Predecessor, read-only reference: `F:\PalmSentinel-AI` (log: `SYSTEM_LIVING_LOG.md`)*
> *This log follows the predecessor's method exactly, under a different name.*

---

## 🧭 TABLE OF CONTENTS

1. [Project Identity & Scope of This Pass](#1-project-identity--scope-of-this-pass)
2. [Directives For Any Incoming Agent](#2-directives-for-any-incoming-agent)
3. [The Inherited Defect: What Was Actually Wrong](#3-the-inherited-defect-what-was-actually-wrong)
4. [V2 Architecture & Data Flow](#4-v2-architecture--data-flow)
5. [Design Decisions And Their Rationale](#5-design-decisions-and-their-rationale)
6. [Evidence A: Synthetic Ground Truth](#6-evidence-a-synthetic-ground-truth)
7. [Evidence B: Real-Data Comparison Against V1](#7-evidence-b-real-data-comparison-against-v1)
8. [Evidence C: Invariants And The Test Suite](#8-evidence-c-invariants-and-the-test-suite)
9. [Calibration Record](#9-calibration-record)
10. [Deliberately Deferred, With Reasons](#10-deliberately-deferred-with-reasons)
11. [Verification Instructions](#11-verification-instructions)
12. [Living Changelog Protocol](#12-living-changelog-protocol)

---

## 1. PROJECT IDENTITY & SCOPE OF THIS PASS

**PalmSentinel** counts oil palms (*Elaeis guineensis*) in ultra-high-resolution
drone orthomosaics and reports stand density in palms per hectare (SPH).

**V2 (`E:\Freebuff\Palm Sentinel (V2)`) is a correctness pass, not a feature
pass.** It owns the detection and counting pipeline only. There is deliberately
no HTML, canvas, web server or desktop wrapper in this pass.

The reason is in §3: the predecessor's interface was built on a census that
over-counted by roughly 4x, and its own audit suite reported 49/49 passing the
whole time. An interface on top of an unverified number multiplies the damage, so
the interface is explicitly not the next thing to build until the number is
trustworthy and independently checkable.

**In scope this pass**

* One metre-derived calibration path, so a spacing is computed rather than typed.
* Tile-seam duplicates made structurally impossible, not removed afterwards.
* One coordinate space and one conversion object for the whole pipeline.
* Every agronomic threshold moved into a single module.
* Verified post-conditions reported alongside every census.
* Real-data comparison against the predecessor's own committed output.

**Out of scope this pass**

* UI, server, PDF/CSV export, desktop packaging, launcher.
* Maturity/age tiering (see §10 for why this was actively removed).

---

## 2. DIRECTIVES FOR ANY INCOMING AGENT

1. **Do not reintroduce a pixel-space spacing.** If a distance is agronomic, it is
   metres, and it lives in `palmsentinel/agronomy.py`. `DetectorParams.from_standard`
   is the only place metres become pixels.
2. **Do not add a second pixel↔metre conversion.** `palmsentinel/scale.py` owns it.
   If you find yourself multiplying by a GSD anywhere else, the abstraction has
   been bypassed.
3. **Do not add a cross-tile dedup heuristic.** `plan_tiles` guarantees exact
   single ownership; a "remove near-duplicates at seams" pass would hide a
   regression in the tiling instead of revealing it.
4. **Do not make the census output depend on `tile_px`.** It is asserted in the
   test suite (`test_census_is_independent_of_tile_size`).
5. **A new agronomic threshold gets a test that proves it is reachable.** The
   predecessor's gaps at 125.1-125.9 SPH existed because nothing enumerated the
   boundaries.
6. **Verify before claiming.** Run the suite in §11 and quote the numbers.

---

## 3. THE INHERITED DEFECT: WHAT WAS ACTUALLY WRONG

### 3.1 Root cause — spacing was a pixel constant

The predecessor's mature preset carried `min_distance_px=68`, and its tiled
suppression used `min_dist * 0.8`. At the 4 cm/px ground sample distance of the
project's own imagery, 68 px is **2.72 m** of ground. A mature oil palm planting
grid has a nearest-neighbour pitch of about **9 m**. The engine therefore treated
anything further apart than ~2.7 m as a distinct palm, and counted
frond-level apexes inside each crown.

This was not a tuning error that a user could be expected to notice, because the
only control was labelled in pixels, and the application reported the resulting
density as "Very High Density — Check Crown Spacing Setting" without flagging that
the spacing setting was the suspect.

### 3.2 Secondary defects found in the same pipeline

| # | Location | Defect |
| --- | --- | --- |
| S1 | `engine/tiler.py:98` | `min_dist` resolved with `dict.get(k, default)`, which returns the *present-but-None* value the caller always passed, so `None * 0.8` raised `TypeError` for any ROI larger than 4096 px on either axis. Reproduced as HTTP 500. |
| S2 | `engine/roi_utils.py:61-65` | SPH bands were inclusive ranges (`110<=x<=125`, `126<=x<=145`), leaving 125.1-125.9 and 145.1-145.9 unclassified; 145.5 SPH fell through to "Very High Density". |
| S3 | `engine/detector.py:174` | `radius = max(15, int(min_dist * 0.45))` gave every palm an identical radius, so the age tiers of v2.7 could only ever report one class at 100%. |
| S4 | `engine/detector.py` | `compute_vegetation_index` applied `cv2.normalize(..., NORM_MINMAX)` per call, i.e. **per tile**. A fixed threshold of 75 therefore meant a different greenness on every tile, and a crown straddling a seam could be above threshold on one side and below on the other. |
| S5 | `engine/tiler.py`, tiled branch | Per-tile suppression ran with radius `min_dist`, but cross-tile stitching used `min_dist * 0.8`. Two apexes either side of a seam could each survive the other's tile and then sit closer than either threshold. |
| S6 | `engine/tiler.py`, tiled branch | Tiles were detected **without** the polygon mask and filtered only afterwards, so apexes outside the survey area could suppress valid ones inside it. |
| S7 | `app.py` | Four separate pixel↔ground conversions (`scale_factor` from width, `gsd_cm` in `roi_utils`, the loupe endpoint, the frontend) plus an HTTP `coord_scale` string negotiating which space a polygon was in. |
| S8 | `audit_suite.py:387` | The audit's own `PalmSentinel.exe --check` check used a relative path, which fails on Python 3.14, so the audit reported 48/49 on the machine it shipped on while the README claimed 49/49. |

S4, S5 and S6 are the reason the count could never have been fixed by tuning the
one number alone: even with correct spacing, per-tile normalisation and
unmasked tile detection would keep the result dependent on how the region was cut.

---

## 4. V2 ARCHITECTURE & DATA FLOW

```
orthomosaic (BGR ndarray)
        │
        ▼
  scale.GroundScale            gsd_cm_per_px -> m_per_px  (ONE conversion)
        │
        ▼
  agronomy.PalmStandard        pitch 9.0 m, min_spacing_fraction 0.75
        │                             │
        │                             ▼
        │                    detection.DetectorParams.from_standard
        │                      blur 31px, peak_sep 36px,
        │                      min_spacing 168.75px, rosette_r 10-150px
        ▼
  detection.observe_region     ONE Otsu threshold + background for the WHOLE ROI
        │
        ▼
  tiling.plan_tiles            exact-cover: cores partition the ROI exactly once
        │
        ▼
  per tile:  read with halo -> polygon mask -> apexes -> keep ONLY core-owned
        │
        ▼
  pipeline.enforce_min_spacing ONE global greedy suppression on full-res coords
        │
        ▼
  exact pointPolygonTest       membership decided geometrically, not by raster
        │
        ▼
  detection.rosette_radius_px  per-palm measured rosette radius
        │
        ▼
  scale.px_area_to_ha + agronomy.classify_sph
        │
        ▼
  CensusResult + reported post-conditions
```

---

## 5. DESIGN DECISIONS AND THEIR RATIONALE

### D1 — Spacing is derived from metres, and pixels are never written down
`DetectorParams.from_standard(standard, scale)` is the single point where
agronomic metres become imagery pixels. In V2 the mature preset states a
`9.0 m` pitch; at 4 cm/px that computes to 168.75 px, 2.5x the predecessor's 68 px.
Changing the GSD changes every derived length consistently, which is asserted by
`test_census_gsd_controls_pixel_params`.

### D2 — One region-wide measurement context instead of per-tile normalisation
The vegetation index is computed in **absolute** Excess-Green units
(`2G - R - B`, range -255..+510) with no min-max stretch. The foliage threshold and
the background level are resolved **once** for the entire region by Otsu's method
over a single downsampled view, then reused by every tile. This is what makes the
same pixel report the same greenness inside a tile and inside the whole image, and
it is asserted by `test_index_is_not_normalised_per_window`.

### D3 — Exact-cover core ownership instead of seam dedup
`plan_tiles` cuts the ROI into cores that partition it exactly; each tile is *read*
with a halo for crown context but only *publishes* candidates inside its own core.
A seam apex belongs to one core and one core only, so it cannot be emitted twice.
`assert_exact_cover` proves the partition, and a test walks a grid of points
asserting each has exactly one owner.

The halo exists so a palm sitting exactly on a core edge still measures its whole
rosette, and it is clipped to the image bounds rather than the ROI so edge tiles
get real context.

### D4 — One global spacing pass, on global coordinates
Suppression runs once over the whole region, greedily, strongest apex first, using
a uniform grid index built on full-resolution coordinates. Because it is not
per-tile, the result cannot depend on how the region was cut. This is a planting
constraint ("two palms are not closer than 0.75 x the pitch"), not a duplicate
remover; duplicates were already eliminated in D3.

### D5 — The polygon mask is applied during detection, and geometry decides membership
Tiles are masked before apex finding, so an apex outside the survey area cannot
suppress a valid one inside it. Because `cv2.fillPoly` rounds vertices to integers,
the raster mask is treated as an *optimisation only*: after suppression, every
surviving palm is re-tested with an exact `cv2.pointPolygonTest`. This fixed a real
leak found on the slanted `blok_tm_utara` boundary, where 1 palm of 1,361 sat
inside the raster but outside the polygon. Now 0 fall outside, on both the
axis-aligned synthetic fixtures and the real slanted block.

### D6 — SPH bands are an ordered, contiguous table
`SPH_BANDS` is a tuple of half-open `[lo, hi)` bands walked in order, with
`bands_are_contiguous()` proving no gaps, and a test enumerating the exact
predecessor gap values (125.5, 145.5). `INDUSTRY_TARGET_SPH = (136, 143)` is
asserted to lie wholly inside one band.

### D7 — The measured apex quantity is named for what it measures
Radial falloff is reported as the **apical rosette radius**, not a crown radius,
and is not classified into maturity tiers. See §10.

### D8 — Post-conditions are reported, not asserted silently
Every `CensusResult` carries the closest accepted pair distance, the required
minimum, the ROI-exclusion count, the raw and suppressed candidate counts, the
tile count, and the elapsed time. `summary_lines()` prints them, and the CLI exits
non-zero if the spacing invariant failed. A caller can therefore check a census
rather than trust it.

### D9 — The automation was removed rather than kept *and* distrusted
An earlier revision of this pass contained a 2-D autocorrelation estimator for the
planting pitch, in the same spirit as most of the estate modelling in V1.
It did not work. On a synthetic lattice with an exact 9.00 m pitch it returned
**4.59 m**, because the first autocorrelation peak of a canopied surface is
within-crown structure, not the distance between crowns. It was deleted, not
caveated: the direct measurement that replaced it, the nearest-neighbour
distribution of the census's own detections, is exactly correct by construction and
recovers a known lattice pitch, which is asserted in
`test_nearest_neighbour_stats_recover_the_pitch`.

---

## 6. EVIDENCE A: SYNTHETIC GROUND TRUTH

The test suite renders a plantation whose density is known exactly: a triangular
lattice of 9.0 m pitch, 10 x 10 = **100 palms**, rendered at 4 cm/px with 4.0 m
crowns and bright apical buds, plus sensor noise.

| Assertion | Result |
| --- | --- |
| Count recovered within 12% of 100 palms | PASS |
| SPH recovered within 15% of the true value for the rendered area | PASS |
| SPH band is `optimal` (126-146) | PASS |
| Median measured rosette radius within 25% of the rendered 4.0 m | PASS |
| Closest accepted pair >= the required minimum | PASS |
| No palm outside the polygon, including on a slanted boundary | PASS |
| Count identical across `tile_px` 512 / 1024 / 1536 / 4096 (spread < 2%) | PASS |
| **V1-style 68 px spacing over-counts the same image by more than 2x** | PASS |

The last row is the defect recorded as an executable fact: the predecessor's
spacing, applied to a plantation of known density, produces more than twice the
true count.

---

## 7. EVIDENCE B: REAL-DATA COMPARISON AGAINST V1

Reproduce with `python tools/compare_v1_v2.py`. V1 is imported read-only from
`F:\PalmSentinel-AI`; nothing there is modified. `NN med m` is the median distance
from a detected palm to its nearest detected neighbour.

### Region A — the predecessor's own bundled demo orthomosaic
`data/demo_palm_estate.jpg`, whole image, 2,500 x 2,500 px = **1.0000 ha** at 4 cm/px.

| variant | palms | area ha | SPH | NN med m | sec |
| --- | ---: | ---: | ---: | ---: | ---: |
| V1 mature preset (blur 31, dil 33, min dist 68, thr 75) | 581 | 1.0000 | 581.0 | 3.66 | 0.26 |
| **V2 default** | **140** | 1.0000 | **140.0** | 7.40 | 1.25 |
| V2 with V1's 68 px spacing injected verbatim | 568 | 1.0000 | 568.0 | 3.71 | 2.07 |

Tile-size sweep on the same image and ROI: 512 px -> 140, 1024 px -> 140,
2048 px -> 140, 4096 px -> 140. **Spread 0.0000%.** A seam-duplicate defect would
make this number large; it is zero.

### Region B — the 138 MP orthomosaic, the predecessor's `blok_tm_utara` block
`Jalan-Lintas-...-orthophoto-2.jpg`, 9,217 x 14,980 px (138.1 MP), V1's polygon
`(300,1200) (4200,400) (9000,1000) (8600,8600) (4200,9300) (300,7800)` =
**10.9064 ha**. V1 parameters are exactly those in `run_full_sensus.py`
(blur 31, min distance 70, threshold 75).

| variant | palms | area ha | SPH | NN med m | sec |
| --- | ---: | ---: | ---: | ---: | ---: |
| V1's committed `output/blok_tm_utara_coordinates.csv` | 6,066 | 10.9064 | 556.2 | – | – |
| V1 live, same parameters | 6,066 | 10.9064 | 556.2 | 3.74 | 8.97 |
| **V2 default** | **1,360** | 10.9064 | **124.7** | 7.59 | 14.76 |
| V2 with V1's 70 px spacing | 5,810 | 10.9064 | 532.7 | 3.78 | 29.76 |
| V2 single tile, zero seams | 1,359 | 10.9064 | 124.6 | 7.59 | 11.02 |

V1 live reproduces the committed CSV **exactly (6,066 palms, 556.2 SPH)**, so the
CSV is a faithful record of what the predecessor's engine actually produced.
V2 with 36 tiles gives 1,360 palms and with a single tile gives 1,359: a seam
difference of **-1 palm, 0.074%**, i.e. seam duplicates are gone.

### The discrepancy, in checkable numbers

* V1 suppressed apexes closer than **70 px = 2.80 m**; V2 suppresses closer than
  **168.75 px = 6.75 m** (0.75 x the 9.0 m mature pitch). The exclusion radius is
  **2.41x** larger, which caps countable density **5.81x** lower.
* V1's 6,066 palms sit a median **3.74 m** apart. V2's 1,360 sit a median
  **7.59 m** apart. V1's typical detected palm is **2.03x** closer to its
  neighbour than V2's — V1 was spacing detections like frond structure, V2 like a
  planting grid.
* Injected-spacing control: running **V2's pipeline** with V1's 70 px spacing gives
  5,810 palms = 532.7 SPH, i.e. **0.96x V1's count**. Same tiles, same suppression,
  same polygon, same image; only the spacing differs, and it reproduces V1. The
  over-count is therefore a **calibration defect, not a pipeline defect** — which
  is also why the predecessor's 49/49 audit could never have caught it.

### Residual, stated honestly

V2 reports **124.7 SPH** on `blok_tm_utara` against its own 136-143 benchmark
target, i.e. about **11% below** the middle of the target band, and **124.7 vs
V1's 556.2**. On the 1.0000 ha demo block V2 reports **140.0 SPH**, inside the
band. V2 does not claim 136-143 for a block whose true density is unknown; it
claims that its number is internally consistent, invariant to tiling, and within
about a tenth of the external standard on a block known to be planted to it.

---

## 8. EVIDENCE C: INVARIANTS AND THE TEST SUITE

`python -m unittest discover -s tests` -> **27 tests, all pass**, in about 14 s.

Grouped by the invariant each establishes:

| Group | Establishes |
| --- | --- |
| `TestAgronomyBands` | SPH table is contiguous; every density maps to exactly one band; the predecessor's gap values (125.5, 145.5) now classify sensibly; the 136-143 target lies inside one band. |
| `TestScale` | Metre↔pixel round-trips; a known 1.0000 ha polygon; bad GSD rejected; GSD changes scale every derived pixel length proportionally; the derived spacing is >2x the predecessor's 68 px and equals `m_to_px(min_spacing_m)` exactly. |
| `TestTiling` | Cores cover the region exactly for six region/tile/halo/image-size combinations; every sampled point has exactly one owner; read windows contain their cores; `assert_exact_cover` detects an injected overlap. |
| `TestSpacing` | The enforced minimum is never violated for random point sets at three radii; suppression count is monotonic in radius; the strongest apex wins. |
| `TestObservation` | The same pixel reports the same greenness in a window and in the whole image; Otsu separates a bimodal vegetation histogram. |
| `TestSyntheticPlantation` | Count, SPH, band, rosette radius, spacing invariant, ROI containment, slanted-boundary containment, tile independence, and the V1 over-count reproduction. |
| `TestSpacingMeasurement` | The nearest-neighbour diagnostic recovers an exactly known 9.0 m lattice pitch. |

---

## 9. CALIBRATION RECORD

`min_spacing_fraction` is the one number in `agronomy.py` that is not derivable
from geometry. It was calibrated against the **external industrial standard**, not
against V2's own output, using the only block in this project whose planting grid
is stated: the bundled 1.0000 ha mature demonstration block.

| fraction | min spacing | palms | SPH | in 136-143? |
| ---: | ---: | ---: | ---: | :--- |
| 0.60 | 5.40 m | 164 | 164.0 | no, above |
| 0.65 | 5.85 m | 154 | 154.0 | no, above |
| 0.70 | 6.30 m | 147 | 147.0 | no, above |
| **0.75** | **6.75 m** | **140** | **140.0** | **yes** |
| 0.80 | 7.20 m | 127 | 127.0 | no, below |
| 0.85 | 7.65 m | 109 | 109.0 | no, below |
| 0.90 | 8.10 m | 95 | 95.0 | no, below |
| 0.95 | 8.55 m | 85 | 85.0 | no, below |

0.75 is the unique setting in the sweep that lands inside 136-143 SPH, so it is the
value used, for both the mature and young standards. Note the deliberate direction
of the calibration: a census that is slightly low is safer than one that invents
palms, and the sweep shows the sensitivity at the chosen point is about
-1.5 palms per 0.01 of fraction, i.e. the result is not perched on a knife edge.

---

## 10. DELIBERATELY DEFERRED, WITH REASONS

### Maturity / age tiering — actively removed, not merely postponed

V1's v2.7 shipped "Palm Age / Maturity Tiers", classifying each palm by crown
diameter against bands of 3.0 / 5.5 / 9.5 m. It could only ever report one class at
100%, because S3 gave every palm an identical radius.

V2 measured the apex profile properly and then **removed the tiering anyway**,
because the measurement does not support it. On a closed mature canopy the fronds of
adjacent palms interlock, so there is no dark inter-crown gap for a radial profile
to find. Instrumented profiles from the project's own mature demonstration block:

| palm | apex ExG | target | profile at r = 20/40/60/80/100/118 px |
| --- | ---: | ---: | --- |
| 71 | 146.5 | 84.7 | 105.7, **82.5**, 61.1, 79.9, **91.4**, 87.8 |
| 79 | 125.9 | 74.4 | 105.2, **78.2**, 62.0, 73.6, **94.1**, 94.7 |
| 75 | 143.6 | 83.3 | 101.9, **76.6**, 52.9, 59.4, 86.7, 93.9 |

The profile leaves the apex, dips around 1.6 m, then *recovers* around 4 m as the
ray crosses into the neighbouring crown, and never returns to background at all.
A "crown radius" read off this is the bright apical rosette (~1.3 m median on real
imagery), not the ~8 m crown footprint the bands describe. Classifying it would
label a mature estate as young — the same class of error as S3, just with better
instrumentation. Recovering true crown footprint on a closed canopy needs
segmentation (connected components on the canopy mask, or watershed from the
detected apexes), and that is the correct next piece of work.

The census therefore reports `rosette_radius_px` / `rosette_radius_m` per palm, and
no tiers. The search window `ROSETTE_RADIUS_SEARCH_M` is kept independent of any
classification band specifically so that a measurement can never be pinned to a
band edge — which is what pinned V1's results.

### Not attempted

* **Hand-labelled estate ground truth.** No such labelling exists for these flight
  lines, so accuracy is validated against a synthetic plantation of exactly known
  density plus the external 136-143 SPH band. A labelled 1-2 ha patch would be the
  single highest-value addition to this project's credibility.
* **GSD provenance.** Every number inherits the assumed 4.0 cm/px. That assumption
  is never checked against the flight log or a known ground distance, and SPH scales
  with its square. Two-click calibration from a known ground distance is the fix.
* **Multi-block estate reconciliation**, exports, and reporting.

---

## 11. VERIFICATION INSTRUCTIONS

```powershell
cd "E:\Freebuff\Palm Sentinel (V2)"

# 1. Unit + invariant suite (27 tests)
python -m unittest discover -s tests -v

# 2. Single censuses
python -m palmsentinel.cli census --image data/demo_palm_estate.jpg --gsd 4.0
python -m palmsentinel.cli census --image "F:\PalmSentinel-AI\data\Jalan-Lintas-S5080iak-Tumang-3-7-2026-orthophoto-2.jpg" --gsd 4.0 --polygon "300,1200 4200,400 9000,1000 8600,8600 4200,9300 300,7800" --json report.json

# 3. Full V1 vs V2 comparison on real data (~2 minutes)
python tools/compare_v1_v2.py --json v2_vs_v1.json
```

Expected: the suite prints `OK`; the demo census prints `140 palms / 140.0 SPH /
Optimal plantation density` with `spacing invariant : PASS` and `palms outside ROI : 0`;
the comparison prints the tables in §6-§7 and reproduces V1's count when V1's
spacing is injected.

The predecessor directory `F:\PalmSentinel-AI` is treated as **read-only**. Nothing
in it is written by any command above.

---

## 12. LIVING CHANGELOG PROTOCOL

Whenever an agent or engineer modifies PalmSentinel V2, append a new entry below,
at the bottom, following this schema. Entries are append-only; do not rewrite
history, and do not remove a decision record when a later pass supersedes it —
record the supersession.

```markdown
### [YYYY-MM-DD HH:MM] — <Change Summary>
* **Agent / Author**: <Name>
* **Files Modified**: <List of file paths>
* **Changes Made**: <Bullet points of functional modifications>
* **Verification**: <Test suite output or commands run>
```

### [2026-09-23 07:30] — V2 workspace created; inherited defect established by measurement

* **Agent / Author**: Codebuff (Buffy)
* **Files Modified**: *(none — read-only reconnaissance of `F:\PalmSentinel-AI`)*
* **Changes Made**:
  * Surveyed the predecessor source tree, its packaged `PalmSentinel.exe`, its
    staleness relative to source, and its `SYSTEM_LIVING_LOG.md` decision-log
    method (append-only changelog, entry schema of Author / Files Modified /
    Changes Made / Verification).
  * Reproduced the core defect and its secondary causes S1-S8 by executing the
    untouched code: the tiled-path `TypeError` at `engine/tiler.py:98` via
    `POST /api/count`; the SPH band gaps at 125.5 and 145.5; the constant-radius
    classification; the per-tile `NORM_MINMAX`; and the audit's own relative-path
    failure giving 48/49 on the shipping machine.
  * Established the over-count numerically from the predecessor's own artefacts:
    `output/estate_sensus_summary.json` reports 556.2 / 961.5 / 565.7 SPH
    (estate average 610.8) against a stated 136-143 SPH benchmark, and V1 live on
    the 1.0000 ha demo block gives 581.0 SPH.
* **Verification**: `git status --porcelain` empty in `F:\PalmSentinel-AI`;
  `python audit_suite.py` -> 48/49 (98.0%); `PalmSentinel.exe --check` returns
  exit 0 when invoked with an absolute path.

### [2026-09-23 08:20] — V2 correctness core: detection and counting pipeline rebuilt

* **Agent / Author**: Codebuff (Buffy)
* **Files Modified**:
  `palmsentinel/__init__.py`, `palmsentinel/agronomy.py`, `palmsentinel/scale.py`,
  `palmsentinel/detection.py`, `palmsentinel/tiling.py`, `palmsentinel/pipeline.py`,
  `palmsentinel/cli.py`, `tests/test_core.py`, `tools/compare_v1_v2.py`,
  `requirements.txt`, `README.md`, `.gitignore`, `SYSTEM_LIVING_LOG_V2.md`
* **Changes Made**:
  * **All agronomic thresholds consolidated** into `palmsentinel/agronomy.py`:
    contiguous half-open `SPH_BANDS` with `bands_are_contiguous()`, the
    `PALM_STANDARDS` registry expressing spacing in **metres**, the 136-143
    `INDUSTRY_TARGET_SPH` constant, and `ROSETTE_RADIUS_SEARCH_M`. S2 fixed by
    construction: the ordered table has no gaps, asserted over the predecessor's
    own failing values.
  * **Single pixel↔metre conversion** (`scale.GroundScale`, `scale.ImageFrame`),
    replacing the predecessor's four conversion sites and its HTTP `coord_scale`
    negotiation (S7). Polygons are always full-resolution pixels in the core.
  * **Absolute vegetation index** with no per-tile normalisation, and a
    **region-wide** Otsu threshold + background resolved once and shared by every
    tile (fixes S4). Asserted by `test_index_is_not_normalised_per_window`.
  * **Exact-cover core-ownership tiling** (`tiling.plan_tiles` +
    `assert_exact_cover`): cores partition the ROI exactly, so seams cannot
    duplicate an apex (fixes S5 by construction rather than by heuristic).
  * **Polygon mask applied during detection**, and exact `pointPolygonTest` as the
    authoritative membership test with the raster mask demoted to an optimisation
    (fixes S6, and fixed a real 1-in-1,361 leak found on the slanted
    `blok_tm_utara` boundary).
  * **Global spacing enforcement** on full-resolution coordinates, with the
    reported post-condition that the closest accepted pair is never below the
    requirement.
  * **Measured apical rosette radius** per palm, replacing V1's constant
    `max(15, min_dist * 0.45)` (fixes S3). Ray sampling uses a 70th percentile
    rather than the mean, because the mean collapses into inter-frond gaps and
    pins every palm to the window floor.
  * **Maturity tiering removed rather than shipped** — the rosette measurement does
    not support crown-footprint bands on a closed canopy; evidence and reasoning in
    §10.
  * **The autocorrelation pitch estimator was written and then deleted** rather than
    kept and caveated: on a synthetic lattice of exact 9.00 m pitch it returned
    4.59 m. Its replacement, the nearest-neighbour distribution of the census's own
    detections, is exact and is asserted against a known lattice.
  * **Comparison harness** `tools/compare_v1_v2.py` importing V1 read-only and
    printing V1 and V2 side by side with a spacing-injection control.
  * **27-test suite** covering band contiguity, tiling exact cover, spacing
    enforcement, observation consistency, synthetic-truth density recovery,
    slanted-polygon containment, tile-layout independence, and the V1 over-count
    as an executable regression.
* **Verification**:
  * `python -m unittest discover -s tests` -> **27 tests, OK** (~14 s).
  * `python tools/compare_v1_v2.py` — demo block (1.0000 ha): V1 **581 palms /
    581.0 SPH**, V2 **140 palms / 140.0 SPH**; 6.75 m vs 2.72 m exclusion radius
    (2.48x); tile sweep 512/1024/2048/4096 all 140 (spread 0.0000%); V2 with V1's
    68 px spacing -> **568 palms (0.98x V1)**.
  * `blok_tm_utara` (10.9064 ha): committed CSV **6,066 palms / 556.2 SPH**, and
    V1 live reproduces it **exactly**; V2 **1,360 palms / 124.7 SPH**; 36 tiles vs
    1 tile differ by **1 palm (0.074%)**; V2 with V1's 70 px spacing ->
    **5,810 palms (0.96x V1)**.
  * V2 invariants on that block: 6,470 raw candidates -> 5,109 suppressed ->
    1,360 kept; closest accepted pair 168.83 px against 168.75 px required;
    palms outside ROI **0**; runtime 14.8 s for 138 MP.
  * `F:\PalmSentinel-AI` remains unmodified (`git status` clean) and is used
    read-only.

### [2026-09-23 11:30] — Interface layer audited in a real browser; nine defects found and fixed

* **Agent / Author**: Codebuff (Buffy)
* **Files Modified**:
  `static/js/main.js`, `static/js/panels.js`, `static/js/tools.js`,
  `static/css/app.css`, `web/views.py`, `templates/index.html`,
  `tests/test_ui_contract.py` *(new)*, `SYSTEM_LIVING_LOG_V2.md`
* **Changes Made**:
  * **F1 — the application never initialised.** `main.js` built the panel
    actions object at module-evaluation time from `exportCsv` / `exportGeoJson`,
    declared as `const` arrows 200 lines below, so the whole bundle died in the
    temporal dead zone: `ReferenceError: Cannot access 'exportCsv' before
    initialization`. Confirmed in the browser console before any other finding —
    every subsequent symptom was downstream of it. The three wrappers are now
    hoisted function declarations. *Nothing about the interface worked at all,
    which no Python test could see.*
  * **F2 — the scale bar threw on every animation frame.** `panels.updateScalebar`
    writes to `this.elements['ro-scale']`, but `mount()`'s id list had never
    included `ro-scale`. The element exists in the markup, so a markup-side check
    passes while `#ro-scale` stayed `undefined` and the frame callback raised
    `TypeError: Cannot set properties of undefined` continuously.
  * **F3 — four overlays ignored `hidden`.** `.stage__empty`, `.stage__loading`,
    `.dropzones` and `#loupe-overlay` each declare an author `display` value,
    which outranks the UA `[hidden]` rule regardless of specificity. Measured
    before the fix: `stage-empty -> grid`, `loading -> flex`, `dropzone -> grid`,
    `loupe-overlay -> block`, all with `hidden === true`. The result was a loaded
    image dimmed by the opaque drop-zone overlay, a permanent "Loading…" pill, and
    a drop-zone card printed over a fresh install. Fixed with one authoritative
    reset, `[hidden] { display: none !important; }`.
  * **F4 — no palm was ever drawn on the map.** `_palm_payload` emitted `x` / `y`
    while every consumer reads `x_px` / `y_px` — the renderer (markers, minimap
    dots), `geometry.UniformGrid` (which therefore bucketed every palm under
    `undefined,undefined`) and `tools.#editClick` (manual deletion). Endpoint
    renamed to the documented pixel-space contract; a census of 140 palms now
    draws 140 markers.
  * **F5 — hovering a palm could not highlight its table row.**
    `ToolController.#updateHover` calls `this.onHoverChange`, a hook no caller
    supplied, and `.grid tbody tr.is-hovered` was styled but never applied — the
    reverse direction worked because the table sets `view.hoverIndex` directly.
    Added `Panels.setHoverIndex(index)` and passed the hook; verified palm 34
    highlights row 34 and clears on pointer-out.
  * **F6 — a polygon could not be closed with the mouse.** `#onDown` tested
    vertex grabbing (8 px) before the close test (11 px), so clicking the first
    vertex started a drag and the close branch was reachable only from a 3-pixel
    ring. Extracted `#wouldClose` as the single owner of the rule and gave it
    priority; verified a 3-vertex triangle closes and totals 0.1087 ha against a
    hand-computed 0.1089 ha.
  * **F7 — the keyboard reference could not be dismissed.** The global keydown
    handler claimed `Escape` and called `preventDefault()`, which suppressed the
    browser's own dialog cancel, while P/B/H/E/F/1 and `Ctrl+Enter` fired on the
    map underneath an open modal. `?` on an already-open dialog also re-entered
    `showModal()` and threw `InvalidStateError`. The handler now yields to any
    open `dialog[open]`.
  * **F8 — the library list rendered as raw UA buttons.** `app.css` carries a
    complete `.filelist` component (grid rows, 13 px icons, ellipsis, hover) that
    nothing applied: the container lacked the class, so each row was a default
    `<button>` with a 240x240-ish SVG at its intrinsic size — a giant black grid
    glyph on a light box. Adding `filelist` to `#library-list` activated the
    existing design; confirmed the row is now a compact one-line entry.
  * **F9 — the shortcut table was 80/20 instead of 46/54.** The auto table layout
    sized the keys column from its widest chip row (408 px of 514) and left the
    action text a 106 px right-aligned sliver wrapping over three lines.
    `table-layout: fixed` makes the declared column split apply; actions are now
    left-aligned beside their keys on one line.
  * **Regression cover.** `tests/test_ui_contract.py` joins the two sides that no
    Python test previously compared: every id the client reads against the markup,
    every binding `e['name']` against the mount list, the `[hidden]` reset's
    presence, and the census palm payload's keys against the set the renderer,
    grid and table consume.
* **Verification**:
  * `python -m unittest discover -s tests` -> **43 tests, OK** (17 s), from 37
    before this pass. The four original defects were re-introduced one at a time
    (mount list without `ro-scale`; payload keys back to `x`/`y`; `[hidden]` guard
    removed; `id="ro-scale"` renamed) and each mutated tree **failed**, then
    passed again once restored byte-for-byte — the new tests are load-bearing, not
    decorative.
  * Exercised in a real browser at 1600x1000 against `demo_palm_estate.jpg`
    (2,500 x 2,500 px @ 4 cm/px, 1.0000 ha): load -> census **140 palms / 140.0
    SPH**, closest accepted pair 169.5 px against 168.8 px required, 628 raw
    candidates, 488 suppressed, 4 tiles, 1.19 s.
  * All four exports returned 200 and toasted: CSV, GeoJSON, annotated JPEG and
    the view PNG. Polygon close, box drag (0.1591 ha), undo/clear, ROI census
    (42 palms / 0.2860 ha / 146.9 SPH), zoom-to-fit and 1:1, wheel zoom, pan
    (60 px drag at 75 % reads exactly -80 image px), minimap click-to-centre,
    GSD re-derivation (3 cm/px -> 75 x 75 m, 0.5625 ha, 225 px floor), the
    TM/TBM standard switch (5.70 m floor -> 190 px), manual add/remove with the
    report `8 detections deleted by hand` plus `1 addition rejected`, table
    sorting, library load, and both modal dialogs were each driven end to end.
  * Server log for the session: no 5xx, no traceback; the only non-200 was the
    expected `GET /api/census -> 404` before the first census. Browser console
    empty at the end of the pass.
  * Core numbers are unchanged by this pass: `python tools/compare_v1_v2.py` still
    reports demo V1 581 / V2 **140 palms / 140.0 SPH**, and `blok_tm_utara` V1
    6,066 / V2 **1,360 palms / 124.7 SPH**.
  * `F:\PalmSentinel-AI` remains unmodified and read-only.
* **Not fixed, recorded deliberately**: the "Palm identifiers" overlay draws
  labels once `camera.scale > 0.06` under a global cap of 420, so switching it on
  at fit zoom on a dense census produces crowded numbering. It is off by default
  and the cap bounds the work, so this is a tuning question rather than a defect.
  The results panel's two-column values also wrap to a second right-aligned line
  for long composites ("6.750 m minimum · 9.000 m planting pitch"); legible, and
  left alone under this pass's mandate.
### [2026-09-23 12:56] — Desktop distribution: launcher, packaged standalone build, and a dependency preflight that states what is missing

* **Agent / Author**: Codebuff (Buffy)
* **Files Modified**:
  `paths.py` *(new)*, `web/__init__.py`, `desktop_app.py` *(new)*,
  `PalmSentinelV2.spec` *(new)*, `Launcher.cs` *(new)*, `PalmSentinel.exe` *(new, compiled)*,
  `Launch_PalmSentinel.bat` *(new)*, `Launch_PalmSentinel.ps1` *(new)*,
  `requirements.txt`, `.gitignore`, `README.md`, `SYSTEM_LIVING_LOG_V2.md`
* **Changes Made**:
  * **`paths.py` — one owner of the deployment layout.** Read-only assets
    (`templates/`, `static/`, the demo orthomosaic) resolve inside the bundle when
    frozen and to the checkout when not; the imagery library resolves to a
    writable `data/` beside the executable, falling back to `%LOCALAPPDATA%` when
    that location is read-only. `web/__init__.py` now resolves its template and
    static directories through it instead of through `__file__`, which is what made
    a packaged build unable to find its own assets.
  * **`desktop_app.py` — the desktop entry point, with failures stated.** The Edge
    WebView2 backend is named explicitly rather than discovered, so a silent
    fallback cannot produce a different window from the one that was tested. A
    missing `webview` or `bottle` produces a message naming the missing package and
    the command that installs it. Three verification entry points run *inside the
    shipped binary*: `--check` (stack and assets), `--selftest` (the real HTTP
    surface — packaged template, packaged stylesheet, packaged orthomosaic, census,
    four exports — compared against the verified 140 / 140.0), and
    `--selftest --window` (the same drive performed inside the real window, with
    every figure read back out of the live DOM). `--port` pins the HTTP port so a
    browser or a verification script can attach to the running session.
  * **`PalmSentinelV2.spec` — the desktop application is the packaged entry
    point**, not `app.py`. The predecessor's spec built `app.py` (the console
    server) and omitted `data/`, so its binary could not show the demo imagery it
    shipped with. This spec bundles `templates/`, `static/` and exactly **one**
    demo orthomosaic; the operator's own `data/` library is excluded deliberately,
    because it is hundreds of megabytes per flight and belongs to the operator.
    `bottle` and `pythonnet` are declared hidden imports: pywebview serves its WSGI
    application through Bottle, so the dependency the window path cannot work
    without was previously unnamed anywhere.
  * **`Launcher.cs` -> `PalmSentinel.exe` — a source-checkout launcher that
    preflights.** It prefers a packaged build beside itself, and otherwise probes
    each candidate interpreter by importing every runtime dependency *before*
    opening a window, naming whatever is absent. Because a GUI-subsystem process
    has no console, every run writes `launcher.log` next to the executable and a
    failure is additionally shown as a dialog. The predecessor searched a list of
    guessed Python locations, assumed five packages were installed in whichever it
    found, and reported nothing when they were not — a double-click that exited
    with a code, no window and no explanation.
  * **`Launch_PalmSentinel.bat` / `.ps1` — the same two paths, the same
    honesty.** Both prefer the packaged build and state which path you are about to
    get; the source path prints what it needs and names the packages that are
    missing rather than failing quietly.
  * **`requirements.txt` corrected** to what the application actually imports:
    Flask, pywebview, Bottle, OpenCV, NumPy, Pillow, plus PyInstaller as the build
    tool. The predecessor named neither `bottle` (which pywebview's WSGI serving
    requires) nor the desktop stack at all.
  * **`README.md` rewritten to state plainly what each path needs.** The packaged
    build needs Windows 10/11 64-bit and the Edge WebView2 runtime, and needs no
    Python, no `pip install`, no network and no developer tools; the source path
    needs Python 3.11+ and the listed packages. The predecessor's README implied a
    standalone binary while its shim in fact required a system Python plus five pip
    packages.
* **Defects found and fixed in this pass's own new code** (recorded because each
  one is the same class of fault this pass exists to remove — a requirement that is
  assumed rather than stated, or a check that cannot fail):
  * **D1 — the pinned port never bound.** `http_port` was passed to
    `webview.start()`, which only configures the *global* server; a callable URL (a
    WSGI app) gets its own server created per window. Read out of pywebview's own
    `window.py`. The port now goes to `create_window()`; the window self-test
    confirms `http://127.0.0.1:5124/`.
  * **D2 — the window self-test hung for 90 s.** It polled for the demo image but
    never started the preload thread, so the state it waited for could not arrive.
    It now shares the real startup path and every wait is bounded by one budget.
  * **D3 — the launcher could not launch this project at all.** `BuildArguments`
    decided whether to quote an argument by inspecting the *interpreter* path, so
    `desktop_app.py` went in unquoted and Python reported
    `can't open file 'E:\Freebuff\Palm'`. Quoting is now decided per argument — and
    the project folder is literally named `Palm Sentinel (V2)`.
  * **D4 — the launcher echoed the child's output twice** (once into the log, once
    to the console). The log is now the record; the console gets the child's output.
  * **D5 — the compiled launcher was stale.** `Launcher.cs` was newer than
    `PalmSentinel.exe`, so the shipped binary predated two of its own fixes.
    Recompiled with `csc.exe -nologo -target:winexe -r:System.Windows.Forms.dll`;
    the timestamp relationship is now part of the verification below.
  * **D6 — the `.bat` preflight could never succeed.**
    `__import__('importlib').util.find_spec(...)` raises `AttributeError` on a bare
    `importlib`, so the probe crashed and the script reported "Python is present but
    the packages are not" on a *fully provisioned* checkout, then `pause`d for a
    keypress — blocking every working source launch with a false diagnosis.
  * **D7 — the `.bat` preflight could never fail.** Its status was read through
    `for /f`, which does not propagate the child's exit code, so even after D6 was
    fixed it reported "Python and packages OK" on an interpreter with zero packages.
    The status is now read from the interpreter directly.
  * **D8 — the `.ps1` probe never ran.** It embedded double quotes in Python source
    (`print(",".join(...))`); Windows PowerShell does not escape embedded double
    quotes when handing a string to a native executable, so the probe arrived as
    `print(,.join(m) if m else none)` — invalid syntax — and the crash was
    misreported as missing packages. The probe now uses single quotes only and
    receives the package names as `argv`.
  * **D9 — a probe failure surfaced as a PowerShell error record.** The script sets
    `$ErrorActionPreference = 'Stop'`, and the probe call redirected native stderr;
    the trace showed the redirect surfacing at the call site rather than being read
    as text. The probe call is now scoped to `Continue`, so the output it produced
    is reported as output.
* **Verification**:
  * **The packaged build proves itself.** `PalmSentinelV2.exe --selftest`, run from
    a copy of `dist/PalmSentinelV2/` in a temporary directory with **no repo on the
    path** and a scrubbed `PATH` holding only `C:\Windows\System32` and
    `C:\Windows` (verified to contain no `python.exe`, `python3.exe` or `py.exe`):
    `frozen : True`; `GET /` 200 / 19,223 bytes; `GET /static/css/app.css` 200 /
    25,031 bytes; `POST /api/image` 200 (`demo_palm_estate.jpg 2500x2500 @ 4.0
    cm/px`); `POST /api/census` 200 — **140 palms, 140.0 SPH, 1.0 ha, band
    `optimal`**, closest pair 169.532 px against 168.75 px required; CSV 200
    (10,568 bytes, **140 palm rows**), GeoJSON 200, annotated JPEG 200.
    `RESULT: PASS`.
  * **The window proves itself.** `--selftest --window --port 5124` in the same
    scrubbed shell opened a real WebView2 window and read its live DOM:
    `document.title` = `'PalmSentinel V2 — Drone Oil Palm Census'`,
    `window.location.href` = `'http://127.0.0.1:5124/'`, header =
    `'demo_palm_estate.jpg'` / `'2,500 x 2,500 px · 6.2 MP · 4.00 cm/px'`, rendered
    `palms 140`, `area 1.0000 ha`, `SPH 140.0`, band
    `'Optimal plantation density (126-146)'`, **140 table rows**, four quality
    checks all `is-ok`, and the page's own CSV fetch returned
    `{"status":200,"bytes":10568,"rows":140,...}` with header
    `palm_id,block,source,x_px,y_px,x_m,y_m,peak_exg,rosette_radius_px,rosette_radius_m`.
    `RESULT: PASS`.
  * **An ordinary launch, not a self-test.** Starting `PalmSentinelV2.exe` with no
    flags from the same clean directory and querying it over HTTP: `GET /api/state`
    200; the demo preloaded; `POST /api/census` 200 — 140 palms, 140.0 SPH, 1.0 ha,
    band `optimal`; `POST /api/export/csv` 200, 10,568 bytes written to
    `census_export.csv`, 140 data rows, first palm
    `1,Blok-TM-Utara,detected,57.00,0.00,2.280,0.000,105.00,50.00,2.000`, last
    `140,Blok-TM-Utara,detected,695.00,2489.00,27.800,99.560,120.38,24.00,0.960`.
    Identical to the dev path.
  * **All three launchers on all three branches.** Packaged build present:
    `PalmSentinel.exe`, `.bat` and `.ps1` each select it and report exit 0. No
    packaged build, packages present: all three fall back to the source checkout and
    report `Desktop stack OK -- 16 routes, assets present`. No packaged build, first
    candidate bare (`venv --without-pip`, junctioned in as `.venv` so it is probed
    first; junction removed afterwards, target intact): `.bat` and `.ps1` print
    `Missing packages: flask,webview,bottle,cv2,numpy,PIL` and exit 1, and
    `PalmSentinel.exe` logs
    `- E:\Freebuff\Palm Sentinel (V2)\.venv\Scripts\python.exe -> missing flask,webview,bottle,cv2,numpy,PIL`
    then correctly falls through to the working interpreter and reports exit 0.
  * **`python -m unittest discover -s tests` -> 43 tests, OK** (16.4 s). No core
    number moved: the census inside the packaged build is the same 140 palms /
    140.0 SPH the dev path gives.
  * **`F:\PalmSentinel-AI` remains unmodified and read-only**: `git status
    --porcelain` empty at HEAD `cd0c22e`.
### [2026-09-23 13:02] — Published: public repository with a standalone release asset, after a sweep before the first push

* **Agent / Author**: Codebuff (Buffy)
* **Files Modified**:
  `.gitattributes` *(new)*, `.gitignore`, `README.md`, `SYSTEM_LIVING_LOG_V2.md`
* **Changes Made**:
  * **New repository, not an overwrite.**
    <https://github.com/Zurplox/PalmSentinel-AI-V2> — public, matching the
    predecessor's own visibility (`Zurplox/PalmSentinel-AI` is public). The
    predecessor is left completely untouched, so V1's commit history and its
    `SYSTEM_LIVING_LOG.md` survive as the read-only record this log's method is
    copied from. Initial commit `a368080`: 39 files, 10,051 insertions, 2.9 MB.
  * **`.gitignore` extended** with `.freebuff/` (editor/agent scratch that belongs
    to the machine, not the project). It already excluded `dist/`, `build/`,
    `.venv/`, `venv/`, `__pycache__/`, `launcher.log`, `output/`, `*.csv`,
    `*.geojson` and every image format except the one demo mosaic — so the six
    artifacts that prove nothing to a reader (196 MB of bundled interpreter, a
    probe temp file, a launcher log, exported CSVs, a venv junction used to test
    the preflight) are all absent from the tree by rule rather than by hand.
  * **`.gitattributes` added**, because line endings are a correctness question
    for two of the files here rather than a preference. `core.autocrlf` was on, so
    a clone with it off would have delivered LF-only `.bat`/`.ps1` — and `cmd.exe`
    parses a batch file line by line, which can mis-handle `goto`, which is exactly
    what the launcher's preflight uses to skip its error block. `*.bat` and `*.ps1`
    are now pinned `eol=crlf`; `*.exe` and image formats are marked `-text -diff`
    so nothing ever tries to line-convert or text-diff a binary.
  * **`README.md`** now points at the release for the download, and states what
    that zip is: about 82 MB, unzip anywhere, run the executable, demo loads on
    first run, unsigned so SmartScreen may warn. The V1 comparison harness is now
    documented honestly as **optional and not self-contained** — it imports the
    predecessor's engine and needs its 138 MP flight line, which is not in this
    repository, and exits 1 with a message saying so when the checkout is absent.
  * **Release `v2.0.0`** carries `PalmSentinelV2-win64.zip` (85,884,623 bytes,
    sha256 `26277931440ee591935f08c72e4fc289f914110e5cd98f897684f3c40cc4efab`), so
    the packaged build the user asked for is obtainable from the repository rather
    than only from a build machine.
  * **Housekeeping:** the log's own `Last Updated` header had read `08:20` since
    the first pass while three entries were appended underneath it. Corrected.
* **Verification**:
  * **What was pushed is what was tested.** The release zip was extracted into a
    fresh empty directory and the **extracted** copy was run there, from a shell
    whose `PATH` held only `C:\Windows\System32` and `C:\Windows` (no
    `python.exe`, `python3.exe` or `py.exe`):
    `--check` -> `packaged build ... 16 routes, assets present`, exit 0;
    `--selftest` -> `POST /api/census` 200, **140 palms, 140.0 SPH, 1.0 ha, band
    optimal**, closest pair 169.532 px against 168.75 px required; CSV 200,
    10,568 bytes, **140 palm rows**; `RESULT: PASS`. The asset is therefore the
    same build the source tree produces, not merely a folder that happens to start.
  * **Secret sweep over the committed set** (`git grep` on `HEAD` for
    `gho_`/`ghp_`/`sk-`/`api_key`/`secret`/`password`/private-key headers/AWS key
    IDs): no matches. No `.env`, `.pem`, `.key`, `.pfx`, `.p12` or `id_rsa` in the
    tree.
  * **Oversize check:** the 39 committed files total 2.9 MB, of which 2.4 MB is the
    one demo orthomosaic the build and its self-test both require. The only large
    thing in the workspace, `dist/` at 196 MB, is ignored and shipped as a release
    asset instead.
  * **Stray-artifact sweep before the first push:** `launcher.log`, the
    `psv2-barevenv` junction used to exercise the preflight, the probe temp file
    and the staged log/release-notes temp files were all confirmed absent from the
    index; `git status --porcelain` was clean at the commit.
  * `python -m unittest discover -s tests` -> **43 tests, OK**. The repository also
    passes its own checks standalone: the test suite needs no imagery, no network
    and no F: checkout.
  * **`F:\PalmSentinel-AI` remains unmodified**: `git status --porcelain` empty at
    HEAD `cd0c22e`, throughout this pass as in every pass before it.
### [2026-09-23 13:12] — Two README claims corrected against measurement: what a fresh build yields, and the browser entry point

* **Agent / Author**: Codebuff (Buffy)
* **Files Modified**: `README.md`, `SYSTEM_LIVING_LOG_V2.md`
* **Changes Made**:
  * **The build-it-yourself sentence.** It previously read "Or build it yourself,
    which produces the same thing" — which is not what happens. A fresh
    `pyinstaller --noconfirm PalmSentinelV2.spec` produces the same *application*
    but not the same *folder*: `PalmSentinelV2.exe` plus the `_internal/` bundle,
    with templates, stylesheets and the demo orthomosaic inside it, and **no**
    `data/` directory beside the executable. `data/` is the operator's imagery
    library, and the first run creates it and seeds the bundled demo into it. The
    v2.0.0 download contains that `data/` only because the build had been started
    once before it was packaged. The README now says exactly this, and the
    "Build the packaged version" section names what the folder holds, so the
    section the sentence now links to is self-contained.
  * **`app.py` documented.** It is the browser entry point for the whole
    application — the port-5000 server every interface verification in this log
    ran against — and it was referenced by nothing at all: not the README,
    `requirements.txt`, the launchers or this log. It now has its own section with
    the install and run steps, the banner it prints, and a row in the layout table.
  * **Two measured caveats recorded rather than papered over.** The port is fixed
    at 5000 with no flag to change it, and starting a second copy while one is
    running does **not** report a conflict: the operating system permits both to
    bind, so which process answers a request stops being under the operator's
    control. Three instances of `python app.py` were observed bound to
    `127.0.0.1:5000` at the same moment (pids 8628, 12032 and 17780). Documented
    as a warning because the code was deliberately not changed in this pass.
* **Verification**:
  * **The documented steps were executed, not asserted.** From the checkout,
    `python app.py` printed its banner (`image : demo_palm_estate.jpg 2,500x2,500 px
    @ 4 cm/px`, `url : http://127.0.0.1:5000`) and then served: `GET /` -> 200,
    19,755 bytes, `PalmSentinel` present; `GET /static/css/app.css` -> 200, 25,031
    bytes; `GET /api/state` -> the demo loaded at 2500x2500 @ 4.0 cm/px;
    `POST /api/census` -> 200, **140 palms, 140.0 SPH, 1.0 ha, band `optimal`**;
    `POST /api/export/csv` -> 200, 10,568 bytes, **140 palm rows**. The browser path
    produces the same numbers as the desktop window and the packaged build.
  * **A fresh build was made to measure the first claim**, with
    `--distpath`/`--workpath` redirected to a temporary directory so `dist/` and
    the released artefact were left untouched. Built in 36 s. Before the first run
    the folder held exactly `PalmSentinelV2.exe` and `_internal/`, and `data/` was
    absent; `templates/`, `static/` and `data/demo_palm_estate.jpg`
    (2,486,597 bytes) were all inside `_internal/`. After one `--selftest` run,
    `data/` existed beside the executable with the demo seeded into it
    (2,486,597 bytes) and the census reported 140 palms / 140.0 SPH:
    `RESULT: PASS`.
  * The earlier claim that the released zip's `data/` came from a pre-packaging run
    is now evidenced by timestamps as well: the built executable is 12:26:55 and
    the seeded demo is 12:29:08 in `dist/`.
  * **Scope:** `README.md` changed by 39 insertions and 5 deletions. No code,
    spec, launcher, test, manifest, release asset or repository setting was
    touched. The two stray `python app.py` servers left running by earlier passes
    were terminated (pids 8628 and 12032); the single instance started from the
    documented command was left serving `127.0.0.1:5000`.
  * **`F:\PalmSentinel-AI` remains unmodified and read-only**: `git status
    --porcelain` empty at HEAD `cd0c22e`.
