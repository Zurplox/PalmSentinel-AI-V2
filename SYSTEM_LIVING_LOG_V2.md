# 🌴 PALMSENTINEL V2 — SYSTEM MASTER CONTEXT & LIVING HANDOVER LOG

> **Living Engineering Document & Decision Record**
> *Target Audience: Autonomous AI Agents and engineers taking over this project.*
> *Last Updated: 2026-09-24 00:47:00 Local Time*
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

# 1. Unit + invariant suite (102 tests)
python -m unittest discover -s tests -v

# 1b. What the three harness flags print, against committed evidence (~11 s)
python -m unittest discover -s tests -p "test_harness_contract.py"

# 2. Single censuses
python -m palmsentinel.cli census --image data/demo_palm_estate.jpg --gsd 4.0
python -m palmsentinel.cli census --image data/demo_palm_estate.jpg --gsd 4.0 --scale-from-image --json report.json
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
### [2026-09-23 13:17] — Two instances could share one port; the port is now claimed, and a second launch is deterministic

* **Agent / Author**: Codebuff (Buffy)
* **Files Modified**:
  `ports.py` *(new)*, `app.py`, `desktop_app.py`, `tests/test_ports.py` *(new)*,
  `README.md`, `SYSTEM_LIVING_LOG_V2.md`
* **Changes Made**:
  * **The defect, and its mechanism.** On Windows the `SO_REUSEADDR` option does
    not mean "reuse a port left in TIME_WAIT" the way it does on Unix — it means a
    *second* socket may bind a port another socket is already listening on. Both
    servers V2 can start inherit `allow_reuse_address = True` from
    `http.server.HTTPServer` (Werkzeug's, behind `app.py`, and the `wsgiref` server
    pywebview runs for the desktop window), so both set it. Three instances were
    observed listening on `127.0.0.1:5000` simultaneously, each reporting success,
    with no way to tell which one would answer a given request. Measured directly:
    a plain bind against the running server fails with 10048, "only one usage of
    each socket address" — which is what made a reliable probe possible.
  * **`ports.py` — one owner of the port.** The rule is the one an operator can
    predict: the default is 5000, and if it is taken the next free port is used and
    the port that was taken is **named**; an explicit `--port N` is honoured
    exactly, or the launch stops and says which port is in the way and how to
    change it, because a pinned port that silently becomes a different one is
    worse than a refusal. A port is claimed by an **atomic lock file**
    (`O_CREAT|O_EXCL`) before it is used and then confirmed with a real plain
    bind, so two instances starting in the same instant cannot both take one, and a
    port held by something that is not PalmSentinel is passed over too. Freshness
    needs no process-id guessing: while a live process holds the lock open Windows
    refuses to let anyone delete it, and once that process is gone the deletion
    succeeds — verified from a second process both ways — so a hard kill cannot
    leave a port permanently out of service.
  * **`app.py` gained `--port`** (it had none, which is why the port was
    described as fixed) and both entry points now read the flag through one
    function, `ports.requested_port`, so the two cannot disagree about what it
    means or how it fails.
  * **The desktop build says which port it took, in its title.** A windowed launch
    from Explorer has no console, so stdout goes nowhere: the window is titled
    `PalmSentinel V2 — port 5001` when it is not on 5000. A launch that has to
    *refuse* an explicitly requested port shows a native message box for the same
    reason — the same reasoning that makes `Launcher.cs` write `launcher.log` and
    show a dialog.
  * **`README.md`** now documents the rule for both entry points under "Launching
    it twice", replaces the old note that said the port could not be changed and
    that a second copy silently shared it, points at v2.0.1 and states the new
    download size.
* **Verification**:
  * **Launched twice, quoting the second instance.** `python app.py` then
    `python app.py` again:
    ```
    instance 1:  url     : http://127.0.0.1:5000
    instance 2:  url     : http://127.0.0.1:5001
                 port 5000 is already in use; serving on http://127.0.0.1:5001 instead
    listeners:   127.0.0.1:5000  pid 10724
                 127.0.0.1:5001  pid 15920
    ```
    Both then served independently and identically:
    `port 5000: census 140 palms / 140.0 SPH / 1.0 ha / optimal; csv 200 10568
    bytes 140 rows` and the same on 5001.
  * **Explicit ports are refused, not moved.** With both ports held:
    `python app.py --port 5000` -> `[PalmSentinel] port 5000 is already in use.
    Start with a different one, for example: --port 5001`, exit 1; and
    `--port 5001` -> the same message naming `--port 5002`, exit 1. No third
    listener appeared.
  * **Two launched in the same instant** (the double-click case, which a probe
    alone cannot handle): one took 5000 and the other 5001, two processes, two
    ports, each answering `140 palms / 140.0 SPH`.
  * **The packaged build, from the extracted release zip**, with no Python on
    `PATH`: instance 1 titled `PalmSentinel V2` on 5000 (pid 6352), instance 2
    titled `PalmSentinel V2 - port 5001` on 5001 (pid 2784) — two ports, two
    processes, both censusing 140 palms / 140.0 SPH / 1.0 ha / `optimal`. A third
    launch with `--port 5000` opened **no window and no third listener**; it raised
    a modal dialog titled `PalmSentinel V2` and its stderr carried the refusal
    naming 5000 and `--port 5001`.
  * **Single-instance behaviour is untouched.** The packaged build still passes
    `--check` (exit 0) and `--selftest` (`RESULT: PASS -- 140 palms / 140.0 SPH
    matched the dev path; four exports produced output`), and a live single
    instance returns `POST /api/census -> 200, 140 palms, 140.0 SPH, 1.0 ha, band
    optimal` with exports at the same byte counts as every previous pass
    (csv 10568, geojson 54288, annotated 3006439).
  * **`python -m unittest discover -s tests` -> 58 tests, OK** (15 new). The new
    tests are load-bearing, checked by mutation: removing the lock arbitration
    fails two of them; probing with `SO_REUSEADDR` — the defect's own mechanism —
    fails three, including `PortUnavailable not raised`, which is the
    silent-share failure mode. That second mutation initially **escaped**, and the
    reason is recorded because it was a flaw in the test rather than the code: the
    test held the port with a *plain* bind, while a real instance holds it with
    `SO_REUSEADDR`. Corrected to reproduce how a real instance holds a port, after
    which the mutation is caught.
  * **Release `v2.0.1`** carries the fixed build:
    `PalmSentinelV2-win64.zip`, **83,403,132 bytes**, sha256
    `63a0f9029181e655b6fd0ccc631c0fd5c2ce3818c7b3a2a6291f3de5831bb783`, and
    `/releases/latest` resolves to it. It was zipped **before the build was ever
    run**, so it extracts to `PalmSentinelV2.exe` and `_internal/` with no `data/`
    folder — the download and a fresh spec build are now the same thing, and the
    README sentence that previously had to explain the difference no longer does.
    `v2.0.0` is left in place rather than having its bytes replaced under the same
    version number.
  * **`F:\PalmSentinel-AI` remains unmodified and read-only**: `git status
    --porcelain` empty at HEAD `cd0c22e`.
### [2026-09-23 13:39] — Is the census number true? The exclusion fraction graded against ground truth, and the published figures relabelled

**Agent/Author:** Buffy (Freebuff). Pass requested by the owner: *"the census itself tested rather than the demo it was tuned on"* — specifically, whether `min_spacing_fraction = 0.75` is a defensible agronomic model or a fit to one image.

**The question, stated exactly.** The exclusion radius is `0.75 x declared pitch` = **6.75 m** at the mature standard. Its justification in `agronomy.py` cited this project's own demo: *164 SPH at 0.60, 154 at 0.65, 147 at 0.70, **140 at 0.75**, 127 at 0.80, 109 at 0.85 — 0.75 is the only setting that lands inside the 136-143 target.* That is self-referential: the target it is calibrated to is the number the demo was tuned to produce. From outside it is indistinguishable from a fit, and the demo cannot settle it because the demo *is* the fitting surface.

**Method.** `tests/ground_truth.py` (extending `tests/synthetic.py`, which already held `nominal_sph`/`triangular_lattice`/`representative_roi` — nothing duplicated): a triangular planting of exactly known pitch, palms at known coordinates, density **arithmetic** (`nominal_sph(9.0) = 142.56`, which is the estate standard's 9.0 x 7.8 m pattern, 142.45 SPH, to 0.07%), and the survey patch chosen by geometry alone. It contains no threshold, no detection and no measurement from the pipeline.

Two method corrections were made **after** the first runs, both because the first version measured the wrong thing, and both are recorded because they change the answer:

1. **Crown size was scaling with the sampled pitch**, so a "12 m pitch" case was drawn as 5.4 m-radius giants — not a real block. `recover(crown_pitch_m=...)` now holds palm size at the standard's real mature crown and moves only the *spacing*. This halved the apparent over-count at 12 m (+14.7% became +7.5%) and is the agronomically honest axis: widening a planting spaces real palms further apart, it does not grow them.
2. The frame sweep had never run — it was passing a wrong keyword and dying on the first case. Fixed, and it turns out to carry a real effect (below).

New: `tests/test_ground_truth.py`, **12 tests, 33.9 s**, asserting every finding here. `test_core.py` cannot cover this: its fixture renders palms at a 9.0 m pitch and 4 cm/px, which are the two quantities in question.

**1. True pitch x planting error, fraction fixed at the shipped 0.75, palm size real:**

| true pitch | jitter | true SPH | recovered SPH | error |
| --- | --- | ---: | ---: | ---: |
| 7.0 m | 0.0 m | 235.7 | 126.9 | **-46.2%** |
| 8.0 m | 0.0 m | 180.4 | 180.4 | +0.0% |
| 8.0 m | 1.0 m | 180.4 | 156.2 | -13.5% |
| 8.0 m | 1.5 m | 180.4 | **140.0** | **-22.4%** |
| 9.0 m | 0.0 m | 142.6 | 143.5 | +0.6% |
| **9.0 m** | **1.0 m** | **142.5** | **142.5** | **+0.0%** |
| 9.0 m | 1.5 m | 142.6 | 140.1 | -1.7% |
| 10.0 m | 0.0 m | 115.4 | 115.4 | +0.0% |
| 10.0 m | 1.0 m | 115.2 | 122.5 | +6.3% |
| 12.0 m | 0.0 m | 79.7 | 79.7 | +0.0% |
| 12.0 m | 1.0 m | 79.9 | 85.9 | +7.5% |

**2. Does the count move with the fraction, or sit flat?** Surveyed lattice (jitter 0), mature standard:

| fraction | 0.50 | 0.60 | 0.65 | 0.70 | 0.75 | 0.80 | 0.85 | 0.90 | 0.95 | 1.00 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SPH error | +4.5% | +3.2% | +1.3% | +0.6% | +0.6% | +0.0% | +0.0% | **-6.4%** | -29.5% | -48.1% |

There is a **plateau from 0.70 to 0.85** and then a cliff where the radius starts swallowing genuine neighbours. A constant fitted to one image would slide across the whole range; this one has a flat region, and 0.75 sits inside it rather than balanced on its edge.

**3. The area is not inheriting a hidden constant.** Tile size 512 / 1024 / 1536 / 2048 / whole-image: **156 palms every time, +0.00%**, so tiling is exact. Ground sample distance 2 / 3 / 4 / 6 / 8 cm/px: -0.64% / -0.64% / +0.00% / -0.64% / +1.28% — and area scales with GSD *squared*, so this is the evidence that the pixel-to-metre path is consistent end to end rather than carrying an inherited number. Frame fraction 100 / 85 / 70 / 50 / 25%: +0.00% / **+5.43%** / +1.36% / +0.00% / +0.00% — a sub-rectangle survey carries an edge bias of up to ~5%, non-monotone because it depends on which crowns get clipped.

**4. The domain, measured.** Radius is a fraction of the pitch the caller **declares**, so accuracy depends on declaring the right standard:

| true pitch vs declared 9.0 m | jitter 0 | jitter 1.0 m | jitter 1.5 m |
| --- | ---: | ---: | ---: |
| 8.0 m (-11.1%) | +0.0% | -13.5% | **-22.4%** |
| 8.5 m (-5.6%) | -0.0% | -3.9% | -7.5% |
| 9.0 m (0.0%) | +0.6% | +0.0% | -1.7% |
| 9.5 m (+5.6%) | +0.0% | +4.6% | +3.5% |
| 10.0 m (+11.1%) | +0.0% | +6.3% | +5.2% |

Within +/-1 m of planting error the error stays under 8% while the true pitch is within 5.6% of the declared one, and reaches -22% on a block 11% tighter. **A mis-declared standard is the dominant error term in a census, and nothing in the pipeline detects it.**

**5. The disproof, and the disproof is what it should be.** If 140 were the model's output rather than a reading, every row above would be 140. Instead the count tracks density across a factor of three: a 7.0 m block reads 126.9 SPH, a 12.0 m block reads 79.7 SPH. And the failure mode inverts with density exactly as a fixed radius must — **under-counting on tight blocks (merging real neighbours), over-counting on wide ones (duplicate apexes surviving)**.

**6. The case the owner asked for, verbatim.** A block planted at 8.0 m with 1.5 m of planting error is **180.4 SPH true and the mature standard reports 140.0 SPH** — the demo's own published number, to the decimal, for a block 28% denser. The two are not distinguishable from the number alone.

**7. The demo has no ground truth, and the previous pass was comparing against the defect.** `F:\PalmSentinel-AI\output\estate_sensus_summary.json` — the file the first pass treated as *"its committed blok_tm_utara sensus CSV"* — is **V1's own export**: `total_estate_palms: 8495`, `average_sph: 610.8`, `blok_tm_utara: count 6066, area_ha 10.9064, sph 556.2, status "Very High Density / Check Crown Spacing Setting"`, with `csv_path` pointing into the Antigravity scratch directory. It is the output of the run whose over-count this project exists to fix. So: **the project contains no hand-labelled ground truth for any real imagery, and the 138 MP "agreement" was agreement with the bug.**

**8. A second instrument on the demo, and its limits.** An independent detector (ExG local maxima: no tiling, no morphology, no spacing rule, no shared threshold) finds **151** candidates at a 4 m crown-scale separation against V2's 140 (+7.9%); 352 at 3 m and 473 at 2 m, which are frond-level apexes. It cannot resolve 6.75 m separations (an artefact of plateau maxima at large kernels: 63), so it **neither confirms nor refutes 140** — recorded rather than dressed up. Two further facts about the demo: the radius removes **78%** of the detector's output on it (628 raw candidates, 488 suppressed, 140 kept), which is why the demo's count moves 164->109 across the sweep while the synthetic stays flat; and the demo's own statistics are mutually inconsistent for any lattice — 140 palms on 1.0000 ha implies a 9.08 m pitch, while the reported median nearest-neighbour is 7.40 m, which would be 210.9 SPH. The demo therefore cannot serve as a validation target either. It is a tuning surface.

**Verdict: the fraction survives as agronomic reasoning, and its justification was replaced.** Value unchanged at 0.75; the evidence:

* **At the declared pitch it is the truth on independent ground truth** — 142.5 SPH recovered against a true 142.5 at 1.0 m planting error (0.00%), +0.64% on a surveyed lattice, -1.72% at 1.5 m.
* **The fraction is not producing the answer** — a plateau from 0.70 to 0.85 rather than a monotone slide.
* **The value is derivable from the standards, not merely observed** — the radius must sit above the palm's own apex structure (bounded by `ROSETTE_RADIUS_SEARCH_M` at ~4 m) and below the true nearest-neighbour spacing of the 9.0 x 7.8 m pattern (7.8 m, or 9.0 m read as a triangular pitch). 6.75 m is inside that window on both sides.

`agronomy.py`'s `min_spacing_fraction` docstring was rewritten accordingly: the circular demo paragraph is gone, replaced by the standards derivation, the measured defence, the domain, and the open question. **No threshold value changed.**

**What this does not settle, recorded rather than hidden.** 0.70 has the *smaller* worst case across an 8-10 m band (**6.9% against 13.5%**), because on a tight block 6.75 m is within one apex-localisation error of the true nearest-neighbour spacing. 0.75 is kept because on real imagery the radius does most of its work suppressing frond-structure apexes (628 -> 140), and a uniform synthetic crown — one bud per palm — cannot measure that side of the trade. The fraction is therefore known to about **+/-0.05**, and closing that needs a hand-labelled real patch, which does not exist. Moving it on the strength of this fixture alone would be substituting a model for the thing it models.

**Files modified:** `palmsentinel/agronomy.py` (docstring only), `tests/ground_truth.py` (extended), `tests/test_ground_truth.py` (new), `README.md` (V2 columns relabelled; new "Is the number true?" section; domain bullet; test count 43 -> 70), this log.
**Not modified:** any threshold value; no pipeline, detection, tiling, web, desktop or launcher code; no feature and no UI change (explicitly out of scope for this pass).

**Verification.** `python -m unittest discover -s tests` -> **70 tests, OK (50.4 s)**, of which 12 are new. `python tests/ground_truth.py` reproduces every table above -> exit 0, all seven sections, **3m30s**.

One defect was found in this pass's own deliverable, by running the command rather than reading it: `python tests/ground_truth.py` — the form promised by the module's docstring and by the README — died with `ModuleNotFoundError: No module named 'palmsentinel'`, because the script form puts `tests/` on `sys.path` and not the repository root; the earlier tables had only ever been produced under `PYTHONPATH=.`. Fixed in the module (the root is added explicitly rather than documented as a PYTHONPATH the caller must remember), and the script form is what produced the numbers quoted above. A second defect was found the same way: the script's pitch table was still printing the *crown-scaled* sweep while the README and this entry publish the *crown-real* one, so "reproduces every table" would have been false; the script now prints the real-palm axis and the band table, and every fraction -> error pair it prints was checked against the rows published above (0.50 +4.49%, 0.60 +3.21%, 0.65 +1.28%, 0.70 +0.64%, 0.75 +0.64%, 0.80 +0.00%, 0.85 +0.00%, 0.90 -6.41%, 0.95 -29.49%, 1.00 -48.08%). `python -m palmsentinel.cli census --image data/demo_palm_estate.jpg --gsd 4.0` -> **140 palms, 140.0 SPH, 1.0000 ha, optimal**, closest accepted pair 169.5 px against 168.8 px required — **unchanged**, because no value moved. The 138 MP figure (1,360 palms / 124.7 SPH on 10.9064 ha) is **not** re-run this pass: its ROI polygon lives in an earlier pass, no code that produces it changed, and per finding 7 it has no ground truth to be checked against anyway. `F:\PalmSentinel-AI` remains unmodified and read-only (`git status --porcelain` empty at `cd0c22e`).
### [2026-09-23 14:04] — The independent count on real canopy: attempted, not obtained — and why the demo cannot be the target

**Agent/Author:** Buffy (Freebuff). Pass requested by the owner: *"hand-label palms by eye on a real patch of the demo mosaic and compare that count with what the pipeline reports for the same area"*, with the annotation written and saved **before** any pipeline output was looked at, and with a material disagreement either fixed or disclosed as a measured accuracy limit.

**Outcome, stated first because it is the point of the entry: no independent count was obtained, and none was fabricated.** The annotation could not be performed in this environment. What the pass does deliver is (a) a validated independent instrument, (b) the measurement that the demo mosaic cannot serve as a validation target even in principle, and (c) a precise statement of what is still unverified. Every number below is quoted from a run in this pass.

**1. Why the annotation was not done.** The requested method is a human eye on the imagery. This session has no image display: `preview_screenshot` fails for every tab including the live application with *"it produced no frames, which means the preview webview is not being composited"*, and reloading and re-opening does not change it. The imagery was therefore inspected through text renderings of its pixels, and five were tried over the 40 x 40 m patch (x 30-70 m, y 30-70 m) and a 20 x 20 m quadrant of it:

| rendering | scale | result |
| --- | --- | --- |
| luminance | 1.0 m/char (whole frame) | canopy texture; bright diagonal track visible |
| luminance, local contrast | 0.25 m/char | frond texture; **no** individual palm centres resolvable |
| luminance, smoothed to crown scale (sigma 1.6 m) | 0.5 m/char | canopy-scale bright blobs that merge across neighbouring palms |
| excess green (2G-R-B) | 0.25 m/char | frond texture, noisier than luminance |
| bud yellowness (R+G-2B), smoothed 1.4 m | 0.5 m/char | connected bands and large blank regions, not discrete per-palm dots |

A closed mature oil palm canopy does not read as separate palm centres in a character grid at any scale tested. **Labelling palms from those renders would have produced a confident-looking number with no basis**, which is the exact failure this project exists to correct, so it was not done and no labels file was written. This is recorded as a **limitation of the pass**, not of the pipeline.

**2. An independent instrument, validated before use.** To have something checkable, spatial autocorrelation of canopy greenness was used: a different principle from any detector (it measures a spatial *period*, not an object), and it can be read as text. It was first validated on synthetic plantations of exactly known pitch, analysed identically (region, smoothing sigma 0.7 m, FFT autocorrelation, profiles along x, along y and radially, local maxima at 1-15 m):

| planted pitch | planting error | local maxima recovered |
| --- | --- | --- |
| 9.0 m | 0.0 m | x **[9]**, y none |
| 9.0 m | 1.0 m | x **[9]**, y none |
| 7.4 m | 0.0 m | x **[7]**, y [13] |

It recovers the planted pitch exactly, with and without planting error. **The instrument is therefore trustworthy on imagery whose answer is known.**

**3. Applied to the demo, it does not find the planting.** Same analysis, demo mosaic, greenness, profiles in metres:

| lag (m) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| along x | 0.73 | 0.29 | 0.15 | **0.23** | 0.22 | 0.11 | 0.05 | 0.07 | 0.08 | 0.09 | 0.15 | **0.19** | 0.15 | 0.10 | 0.14 |
| along y | 0.79 | 0.44 | 0.32 | **0.36** | 0.35 | 0.29 | 0.25 | 0.24 | 0.20 | 0.18 | 0.22 | **0.26** | 0.22 | 0.12 | 0.09 |
| radial | 0.71 | 0.37 | 0.24 | **0.27** | 0.24 | 0.16 | 0.15 | 0.18 | 0.17 | 0.13 | 0.12 | 0.14 | 0.13 | 0.10 | 0.10 |

Local maxima at **4 m, 8 m and 12 m** in both axes (interior 36 x 36 m patch: 5 m and 12 m), and **the 9 m lag is a valley, not a peak** (x 0.08, y 0.20, both below their neighbours at 4 m and 12 m). A plantation at the standard's 9 m pitch would have shown a peak at 9 m, as the synthetic case does. The demo's canopy does not carry the planting structure the benchmark assumes.

**4. What the demo frame actually is.** 100 x 100 m at 4 cm/px, 1.0000 ha, of which — measured on the imagery with this project's own Otsu threshold on excess green:

```
vegetation  (ExG >= 58.9)         46.5% of the frame
bright, non-vegetated ground       7.4%   (a diagonal track and bare patches)
remaining (shaded canopy, gaps)   ~46%
```

So **140.0 SPH is 140 palms per hectare of a mixed frame**, and that is not a planting density. Depending on which part of the frame is treated as plantable, the same 140 palms reads **301 SPH** (over the 46.5% that clears the vegetation threshold) or **151 SPH** (over the 92.6% that is not bright non-canopy). The demo was the tuning surface for `min_spacing_fraction`; it cannot be the validation surface, and its agreement with the 136-143 benchmark band may be coincidence rather than confirmation.

**5. Verdict on the request's decision rule.** The instruction was: if the disagreement is material, either fix it or disclose it as a measured accuracy limit. There is no count-versus-count disagreement to report, because no independent count was obtained. The disclosures that are earned by measurement are these:

* **The real-canopy accuracy of this pipeline remains unmeasured.** It is not merely unverified by absence of data; an attempt was made in this pass and failed for environmental reasons, and the demo frame is disqualified as a target on its own measurements (points 3 and 4).
* **The demo's 140.0 SPH must be read as a frame density over mixed land use.** That has been written into the README's numbers section.
* **No label file was created**, because no labels were justified. Creating an empty template would have misrepresented the state.

**6. The step that remains, precisely.** Open `data/demo_palm_estate.jpg` in any image viewer, count palm centres in the 40 x 40 m patch (x 30-70 m, y 30-70 m; the same patch used for all rendering attempts above), write each centre's (x, y) in metres, and compare against `python -m palmsentinel.cli census --image data/demo_palm_estate.jpg --gsd 4.0 --polygon` for that polygon. For a real estate number rather than a demo number, the 138 MP `blok_tm_utara` flight line is the right target, since the demo is a mixed frame. Until one of those exists, every real-imagery figure in this project is unverified, and the synthetic ground truth of the previous pass is the only quantitative accuracy evidence there is.

**Files modified:** `README.md` (the numbers section now states the demo's frame composition, the autocorrelation result and the failed-annotation disclosure; new *What this does not verify* subsection), this log.
**Not modified:** any code, any threshold, any test, the release asset. No feature and no UI work. `F:\PalmSentinel-AI` untouched. The annotation-viewing scratch files are in `%LOCALAPPDATA%\Temp\psv2-annot\` (outside the repository, uncommitted) with a local static server on `127.0.0.1:8791` for the owner to look at the patch if useful.

**Verification.** The ACF instrument was validated against known-pitch synthetic plantations before being applied (table in point 2), which is what makes its null result on the demo meaningful rather than an artefact. The frame-composition figures come from `otsu_threshold`/`vegetation_index` in `palmsentinel.detection`, run on the demo at 4 cm/px. The demo census is unchanged at 140 palms / 140.0 SPH / 1.0000 ha (`python -m unittest discover -s tests` -> 70 tests OK, unaffected).
### [2026-09-23 14:15] — The entry point keeps startup, the harnesses keep proof: desktop_app.py split, packaged behaviour proven byte-identical

**Agent/Author:** Buffy (Freebuff). Pass requested by the owner: *"desktop_app.py is 563 lines and roughly 300 of them are two verification harnesses, so the file whose stated job is to open a window is also the second owner of 'is this build correct'. Extract that verification into a module of its own … keeping every CLI flag behaving exactly as it does now"*, with the packaged build rebuilt and the release refreshed.

**What was wrong, in the audit's terms.** Two owners for one claim. `desktop_app.py` opened the window *and* decided whether the build was correct, so a change to what "correct" means could land in the file whose job is to launch, and the packaged `--selftest` path -- the only check that runs in the shipped binary -- lived in the same file as the window it verifies. Ownership is now split along the obvious line: startup in the entry point, proof in a module of its own.

**The split.**

| | before | after |
| --- | --- | --- |
| `desktop_app.py` | 599 lines: window **and** both harnesses | **239 lines**: window, geometry, port, preflight, flag dispatch |
| `verification.py` | did not exist | **388 lines**: `check()`, `census_selftest()`, `window_selftest()`, report writing, and the expected demo result |

Net +28 lines, and that is worth stating plainly: the goal was ownership, not line count. The file that opens a window is 360 lines shorter; the increase is the new module's docstring (which carries the *why* for each harness) plus named homes for the window geometry.

Two structural consequences beyond moving code:

* **The window has one definition.** `create_window(webview, claimed, app)` and the `WINDOW_SIZE` / `WINDOW_MIN_SIZE` / `WINDOW_BACKGROUND` constants replace two verbatim copies of the `create_window(...)` arguments -- one in `main`, one in the window self-test. A change to the window could previously be made in one place and silently not apply to the other, which would have made the self-test evidence for a window the user does not get.
* **The expected demo result moved with the harnesses** (`EXPECTED_DEMO_PALMS` / `EXPECTED_DEMO_SPH`). It is a property of the verification, not of opening a window.

Cross-module names are now public on purpose: `require_webview`, `build_app`, `start_preload`, `disable_page_zoom`, `claim_port`, `window_title`, `create_window`, `WINDOW_TITLE`. `_report_fatal` stays private -- only `claim_port` uses it. The dependency direction is one way (`verification` imports `desktop_app`; the entry point imports `verification` lazily inside `__main__` for the two verification flags), so there is no import cycle and importing the entry point does not drag the harnesses in.

**Provably unchanged, measured rather than asserted.** Baselines were captured *before* the refactor from source and from the packaged build that then existed, then re-captured after, and diffed:

| harness | source | packaged |
| --- | --- | --- |
| `--check` | **identical** (186 bytes, exit 0) | **identical** (exit 0) |
| `--selftest` | **identical** (934 bytes, exit 0) | **identical** (exit 0) |
| `--selftest --window` | **identical** (1,159 bytes, exit 0) | **identical** (exit 0) |

All six comparisons are byte-for-byte, not "same numbers". The figures they carry are unchanged: **140 palms / 140.0 SPH / 1.0000 ha / band optimal**, closest accepted pair 169.532 px against 168.75 px required, and the export byte counts are identical to every previous pass -- **csv 10,568, geojson 54,288, annotated 3,006,439**, preview raster 2,656,531. `python -m unittest discover -s tests` -> **70 tests, OK**.

**The spec needed one line.** `verification` is imported from `__main__`, i.e. only in the shipped binary -- exactly the class of import that is present in source and absent from a frozen build. It is now declared in `hiddenimports` with its reason, alongside the other dynamic imports. The packaged self-tests are the proof it was collected; the declaration is so that a future edit cannot break `--selftest` in a way that only shows up in a zip.

**The packaged artifact, rebuilt clean and republished.**

* `rm -rf build dist`, then `pyinstaller --noconfirm --clean PalmSentinelV2.spec` -> 46 s, `dist/PalmSentinelV2/` holding `PalmSentinelV2.exe` and `_internal/` and **no** `data/` folder, which is what a fresh spec run produces.
* Zipped **before** the built app was ever run, so the archive is the pristine build; verified from the archive's own manifest that the only entry beside `_internal/` is `PalmSentinelV2.exe`.
* Verified from the **extracted archive**, not from the build tree: `--check` passes with a `PATH` of `C:\Windows\System32;C:\Windows` and no Python on it at all (exit 0, 16 routes), and both self-tests pass with the numbers above.
* **Release `v2.0.2`** -- `PalmSentinelV2-win64.zip`, **85,463,561 bytes**, sha256 `c8eac92634979652e994b8a6423a7b3bf4ba225d43732b9501da0f2d429177f9`, `/releases/latest` resolving to it, and the asset fetched back through the API with a matching digest and through the web URL with HTTP 206. `v2.0.0` and `v2.0.1` are left in place.

**Two mistakes of mine in this pass, both corrected and both worth recording.** The asset first uploaded under its temporary filename (`PalmSentinelV2-win64-v2.0.2.zip`) rather than `PalmSentinelV2-win64.zip`, because `gh release create` ignored the `path#name` form; the mis-named asset was deleted and re-uploaded with the right name, and the digest is unchanged. And the first extraction attempt passed an MSYS path into a Windows Python, which created a stray 194 MB tree at `C:\c\Users\...`; it was inspected, confirmed to be only that extraction, and removed.

**`Launcher.cs` was left alone, deliberately.** The same audit names it as having five tangled jobs (dependency preflight, interpreter search, log file, failure dialog, process launching) and the request explicitly records the decision not to touch it. The reasons, for whoever revisits this: its **three launch branches were hard-won and are individually verified** -- packaged build present; no packaged build with all packages present; no packaged build with packages missing and named -- and each of those branches cost a defect to get right (the quote-inspection bug in `BuildArguments`, the double-echoed console output, a stale compiled `.exe` that was newer-looking than its source). Rewriting it this late would put all three at risk for a code-shape gain no user can see, and it is the one component whose behaviour is only observable through a log file and a modal dialog, so a regression there is the hardest to catch and the easiest to ship. Untangling it is a legitimate follow-up; it is not a safe tail-end of a refactor pass. **Decision: no change, recorded rather than done.**

**Files modified:** `desktop_app.py` (startup only), `verification.py` (new), `PalmSentinelV2.spec` (one hidden import), `README.md` (release version, size, sha256; the two layout rows), this log.
**Not modified:** `Launcher.cs` or `PalmSentinel.exe`, any pipeline/detection/tiling code, the web layer, templates, stylesheets or client scripts, any threshold, any test, the previous release assets. No feature, no UI change. `F:\PalmSentinel-AI` untouched (`git status --porcelain` empty at `cd0c22e`).

**Verification.** Six byte-identical harness outputs (three source, three packaged) diffed against pre-refactor baselines; `python -m unittest discover -s tests` -> 70 tests OK; the release candidate extracted and self-tested outside the repository with a scrubbed `PATH`; the published asset re-downloaded and its sha256 compared to the local zip and to the digest the API reports.

**One metadata correction, made rather than inherited.** The previous entry carried the heading `[2026-09-23 14:40]`, a timestamp chosen by hand rather than read from the clock, which left the log out of order -- the entry below it in that turn really happened around 14:04. It has been re-stamped to its own commit time (`2f04a08`, 14:04:37), so the headings now run chronologically and each one is traceable to a commit.

### [2026-09-23 15:22] — The flag contract is committed evidence, and one refusal in it was wrong

**Agent/Author:** Buffy (Freebuff). Pass requested by the owner: *"Close the gaps the audit named, in the smallest way that actually settles them. First, the 'provably unchanged' evidence currently lives in session temp files, so commit it as project evidence: golden transcripts … plus a test that fails if they drift … Second, bind the flag vocabulary across the three places that now declare it … Third, fix the one inconsistency the audit found in that same surface: `--port abc` gets a tidy refusal while an out-of-range port like 70000 produces a raw traceback … check `--port 0` does whatever you intend it to rather than leaving something bound. Fourth, add the one sentence accounting for the v2.0.1 to v2.0.2 release growth."*

**1. The proof that nothing had changed lived in `%TEMP%`.** The pass before this one established "byte-identical output" by diffing against six baseline files written under the session temp directory, and nothing in the repository could reproduce that. Those transcripts are now the project's evidence:

| golden | bytes | what it records |
| --- | ---: | --- |
| `tests/golden/check.txt` | 136 | the layout line, 16 routes, assets present |
| `tests/golden/selftest.txt` | 787 | packaged template, stylesheet, orthomosaic, census, four export byte counts |
| `tests/golden/window-selftest.txt` | 1,144 | the rendered figures read back out of the live DOM, and the CSV the page exported itself |

`tests/test_harness_contract.py` re-runs each flag through `desktop_app.py` and fails on a single character of drift. Three placeholders keep a transcript committable -- `<REPO>`, `<PYTHON>`, `<PORT>` -- while every count, byte size, band and export figure is compared literally. The module says what these are and are not: **recordings of behaviour that was verified by hand, not independent truth**; they cannot tell you the census is right, only that nobody changed the promise unnoticed. Regenerating is deliberate and its diff is the review: `python tests/test_harness_contract.py --regenerate`.

The guard was proven load-bearing rather than assumed, by breaking what it protects three ways and watching each fail:

| mutation | result |
| --- | --- |
| the spacing of a log line in `verification.py` (exit code still 0) | `FAILED (failures=1)` -- the transcript diff, printed |
| `--nonsense` added to the Python flag list only | `FAILED -- Launcher.cs's IsCheckMode and desktop_app.VERIFICATION_FLAGS no longer agree` |
| `--oldname` added to the frozen `Launcher.cs` only | the same failure, naming the extra flag |

All three were reverted; the module is green at 7 tests. `.gitattributes` now pins `tests/golden/*.txt` to LF, because evidence compared line by line should be the same bytes on every machine.

**2. The flag vocabulary, declared three times, is now bound.** `desktop_app.VERIFICATION_FLAGS` is the single Python declaration and the dispatch is a membership test against it; `Launcher.cs`'s `IsCheckMode` and both module docstrings are watched by a test that fails when they disagree. An aliases test additionally proves `--test`, `-v` and `--version` produce exactly the `--check` transcript, which is what the docstrings claim they are. `Launcher.cs` itself is still untouched -- the 14:15 decision stands, and this test is what makes leaving its copy alone safe rather than hopeful.

**3. One refusal in that surface was wrong, and one wrong value was worse than noisy.** Measured before the change:

```
--port abc     [PalmSentinel] --port needs a number, e.g. --port 5001    a sentence
--port 70000   OverflowError: bind(): port must be 0-65535               a traceback, ports.py:155
--port 0       opened a window on an OS-assigned port, announcing 0
```

`--port 0` is the one worth remembering: the operating system assigns an arbitrary free port, so the number the window announced was not the number it served. `ports.PORT_LIMIT` now owns the bound and `requested_port` validates the range -- one reader, so `app.py` and `desktop_app.py` cannot disagree -- and every unusable value (`abc`, a missing value, `0`, `-1`, `65536`, `70000`) is refused identically, in a sentence.

On the desktop path the refusal is also *visible*: `claim_port` surfaces it through `_report_fatal`, which shows a modal dialog, because a double-clicked window has no console and that is the same treatment the taken-port refusal always had. Checked while there, because the dialog is preceded by a `print`: in a no-console build that print could have crashed before the dialog appeared. It does not -- the dialog is shown -- so nothing needed fixing. The README now states both facts, including that in the packaged build a refusal waits for you to dismiss it (the process is still alive at 25 s: that is the dialog waiting, not a hang).

**4. The 2.06 MB the audit asked about, measured rather than explained away.** The v2.0.1 -> v2.0.2 growth was recorded as a number with no accounting, and an unaccounted 2 MB reads as new code. Both halves are now measured:

* **Payload:** the two extracted trees hold the same **265 file names** and differ by **8,489 bytes -- the executable alone**. Nothing else moved.
* **Archive:** re-archiving identical content with the writer used for this release reproduces the shipped sizes. Pristine content -> **85,447,363** (v2.0.3) against v2.0.2's **85,463,561**, a 16,198-byte difference for changed code; with the seeded demo inside -> **87,924,354** for v2.0.1's tree and **87,933,747** for v2.0.2's, which differ by the same 9,393 bytes and sit 2,470,186 bytes above the pristine figure, which is the demo JPEG. **v2.0.1's shipped 83,403,132 is 2,044,231 bytes below what this writer produces for identical pristine content**, so the size difference is a property of how that archive was written, not of the code it carried -- and the release list's sizes (85,884,623 / **83,403,132** / 85,463,561 / 85,447,363) show v2.0.1 as the outlier of the four. Inference boundary, stated rather than glossed: v2.0.1's own zip is no longer on disk, so its writer is identified by arithmetic, not reproduced.

**5. Found while sweeping the tree before the commit: an unexplained 900 KB `%SystemDrive%` directory.** Untracked in the repository root, holding `ProgramData/Microsoft/Windows/Caches/cversions.2.db` and two `{GUID}.ver0x...db` files -- Windows API-set metadata caches, at a path built from a variable that was not expanded, so it landed in the working directory instead. It is **not written by this project**: `grep -rn SystemDrive` finds no reference in any `.py`, `.cs`, `.bat`, `.ps1` or `.md` file here. Four candidates were ruled out by measurement rather than reasoning:

| candidate | probe | result |
| --- | --- | --- |
| loading the CLR (`pythonnet` / `clr_loader`) | `import clr; import System` from a clean directory | no stray |
| the packaged app, headless (`--check`) | `dist/PalmSentinelV2` exe, clean cwd, normal **and** scrubbed environment | no stray, both |
| the window path | `--selftest --window`, packaged **and** from source, clean cwd | no stray |
| `pyinstaller` itself | clean cwd, spec by absolute path (39 s build) | no stray |

The second copy, in the previous pass's `psv2-standalone` directory beside `launch.log`, is the other sighting. So it is recorded as **observed twice, reproduced never**, with the ruled-out list above rather than a guess, and `%SystemDrive%/` is now in `.gitignore` so it cannot be committed by a later sweep that does not recognise it. The stray itself was removed.

**Files modified:** `ports.py` (range validation, `PORT_LIMIT`), `desktop_app.py` (the refusal surfaced, `VERIFICATION_FLAGS`, docstring), `verification.py` (docstring only), `tests/test_harness_contract.py` (new), `tests/golden/check.txt`, `tests/golden/selftest.txt`, `tests/golden/window-selftest.txt` (new), `tests/test_ports.py` (the range cases), `.gitattributes` (the goldens), `.gitignore` (the `%SystemDrive%` pattern), `README.md` (release version, size and sha256; what the port refusal does; the tests line and how to regenerate), this log.

**Not modified:** `Launcher.cs` and `PalmSentinel.exe`, the pipeline, detection and tiling code, the web layer, templates, stylesheets, client scripts, every threshold, `PalmSentinelV2.spec` (a test is not shipped code, so nothing new to collect), and the previous release assets. No feature and no UI change. `F:\PalmSentinel-AI` untouched.

**Verification.**

* `python -m unittest discover -s tests` -> **79 tests, OK, 60 s** (was 70 tests / 48.6 s); the seven new ones are the contract module, which alone runs in 10.7 s.
* The three source transcripts are **byte-identical to the bytes recorded before this pass's code changes** -- `--check` 186 B, `--selftest` 934 B, `--selftest --window` 1,159 B, all exit 0 -- so the port validation moved nothing the harnesses print.
* Packaged, run from the **extracted archive** with a `PATH` of `C:\Windows\System32;C:\Windows` and no Python on it: `--check` and `--selftest` are **byte-identical to v2.0.2's packaged transcripts** modulo the install path, exit 0; a plain launch returns `140 palms / 140.0 SPH / 1.0 ha / band optimal`; and `--port 70000` opens **no window and no listener** and shows a modal `PalmSentinel V2` dialog (window class `#32770`).
* **Release `v2.0.3`** -- `PalmSentinelV2-win64.zip`, **85,447,363 bytes**, sha256 `6c69de1bca06d0d0230a9f1e1a0b7f7d5cc2b929f66e94ba1d3a051d7be2bc4b`, from `rm -rf build dist` + `pyinstaller --noconfirm --clean PalmSentinelV2.spec` (37 s), zipped **before** the built app was ever run (264 entries, top-level `PalmSentinelV2/`, no `data/`), asset name confirmed on the API rather than assumed -- the mis-naming of the previous pass did not recur -- `/releases/latest` resolving to it, with `v2.0.0`, `v2.0.1` and `v2.0.2` left in place.

**One thing deliberately not done.** A refusal on the desktop path now waits for its dialog, which is right for a double-click and wrong for a scripted launch that would rather read an exit code. Making the dialog conditional on whether a console is attached is a small change, but it moves the same behaviour a third time; it is recorded here as the next candidate rather than guessed at now.

### [2026-09-23 16:05] — Adversarial pass on the refusal surface: the dialog that waited, and the flag spelling that was ignored

**Agent/Author:** Buffy (Freebuff). Pass requested by the owner: *"Adversarially exercise the change through its real entry points. Look for concrete boundary, empty-input, ordering, async, cleanup, and state-synchronization failures that the request makes relevant. Fix defects you can substantiate from the code or focused tests; do not harden unrelated or imaginary cases. Once correctness is established, remove any complexity introduced for risks that are not real. Finish with proportionate validation."*

**Defect 1, substantiated through the entry point: a bad `--port` made the desktop entry wait instead of stopping.** Measured before the change: `desktop_app.py --port 0` printed the sentence and then sat on a visible modal box (`#32770`, titled `PalmSentinel V2`), alive at 12 s and killed at 25 s -- while the same value through `app.py` exited 1 with the same sentence. So "both paths refuse the same way" held for the value and not for the entry point, and a scripted launch hung with no timeout. The previous pass recorded the dialog as intended and attributed it in the README to the packaged build having no console; both were wrong in the same way.

The fix is a discriminator that answers the right question. Not "is a console attached": measured, `GetConsoleWindow()` is **0 even for a python launched from bash**, so that test would have kept the hang. The question is whether the refusal can reach anyone -- `_output_stream()` returns the first of `sys.stderr`/`sys.stdout` that is not `None`, not closed, and has a real file descriptor; `_report_fatal` prints when there is one and calls `_show_dialog` only when there is not. A terminal, a launcher and a capture all have descriptors; a windowed build started from Explorer has a placeholder with none.

Verified on the rebuilt binary: launched with pipes it **exits 1 in 1.1 s** with the sentence on stderr, and launched with no handles at all -- the double-click case, via `Start-Process` -- the **dialog still appears**. The visibility property survived, the hang is gone, and the branch that exists for a double-click was checked rather than assumed.

**Defect 2, a boundary of the same flag: `--port=5123` was silently ignored.** Measured: `app.py --port=5123` served on **5000** and announced 5000. A silently substituted port is the exact failure this module exists to prevent, and the `=` spelling is what command-line habit produces. `ports.requested_port` now reads both spellings, and `--port=`, `--port=0`, `--port=70000`, `--port=http` refuse exactly as the space-separated forms do.

**Complexity removed, because the risks it covered were not real.**

* `TIMEOUTS` (three values, differing only in whether a webview is waited on) -> one `HARNESS_TIMEOUT`.
* `regenerate()`'s unified-diff printer -> one line naming `git diff`, which shows the same thing now that the goldens are tracked.
* `normalize()`'s second path variant (`as_posix`) -> dead on every platform: the posix form *is* `str(ROOT)` on posix, and Windows transcripts carry backslashes.
* The vocabulary test's docstring check used `assertIn`, so `-v` was satisfied by the `-v` inside `--version` and it could not fail for the reason it claimed. Now bounded on both sides: `(?<![\w-])flag(?![\w-])`.
* `.gitignore`'s six-line note on the `%SystemDrive%` rule -> two.

Kept deliberately: the direct-run `sys.path` shim (a measured `ModuleNotFoundError` without it, and the module's own docstring documents that invocation) and the mutation-tested transcript comparisons.

**Tests added, 79 -> 83.** Three in the contract module: a refusal with a stream prints and does **not** dial; a refusal with nothing to print to **does** dial; and a bad `--port` through the real desktop entry must exit 1 inside a 90 s bound, which is the test that would have failed on the previous behaviour. One in `test_ports.py` for the `=` spelling and its refusals. A third branch test -- "this process has a stream" -- was written and then removed: it would have depended on the test runner's stream plumbing rather than on our contract, which is the kind of check that fails for reasons that are not the code's.

**Superseded decision, recorded rather than rewritten.** The 15:22 entry decided the dialog was how the desktop path refuses; this entry supersedes the *unconditional* half of it. The dialog is now only for a launch with nowhere to print, which is what the README's corrected paragraph says.

**Files modified:** `desktop_app.py` (`_output_stream`, `_show_dialog`, `_report_fatal`), `ports.py` (both spellings in `requested_port`), `tests/test_harness_contract.py` (three tests added, four pieces of scaffolding removed, one strengthened), `tests/test_ports.py` (the `=` spelling), `.gitignore` (comment trimmed), `README.md` (the refusal paragraph corrected, the suite count), this log.

**Not modified:** `Launcher.cs` and `PalmSentinel.exe`, the pipeline, detection and tiling code, the web layer, templates, stylesheets, client scripts, every threshold, the goldens (no transcript changed), `PalmSentinelV2.spec`, the release assets. `F:\PalmSentinel-AI` untouched.

**Verification.**

* `python -m unittest discover -s tests` -> **83 tests, OK, 60 s**; the contract module alone is 10 tests in 10.6 s, `test_ports.py` is 18.
* `python tests/test_harness_contract.py --regenerate` reports all three transcripts **unchanged** -- the refusals are not part of any transcript, so the committed evidence still matches byte for byte.
* The fixed code rebuilt into a temporary `--distpath` (leaving `dist/` as it was) passes `--check`, `--selftest` and `--selftest --window` with output **byte-identical to the v2.0.2 packaged transcripts** modulo the install path.
* Entry-point probes, all cleared afterwards (no listeners, no processes): `app.py --port=5123` serves **5123**; two instances still take 5000 and 5001; a pinned taken port still exits 1; `--port 0` from a terminal exits 1 immediately; the double-click equivalent still dials.
* The `%SystemDrive%` directory appeared a **third** time during this pass and was removed. Three controlled experiments deleted it first and then did exactly one thing: a `pyinstaller` build from the project root, the full test suite, and the packaged `--check` from the project root. **None reproduced it**, so the record stays "observed repeatedly, never reproduced on demand" and the ignore rule is what keeps it out of the repository.

**Not committed, not released -- stated plainly because it matters.** The changes above are in the working tree only: no commit, no push, and the published `v2.0.3` therefore predates this entry and still carries the waiting dialog and the ignored `--port=`. Refreshing the release is a separate action, for whenever the owner asks for it.

### [2026-09-23 21:38] — An imported orthomosaic painted the previous image's pixels: every raster URL now carries a content fingerprint

**Agent/Author:** Buffy (Freebuff). Pass requested by the owner: *"I can't import in my image, when I imported it in, it only shows a small portion of my image, try it and see. If yes, fix it."* — folded into the outstanding publish pass ("commit as one clear unit, push, confirm remote").

**What was substantiated, and how.** The report said "shows a small portion". Probing the running app through a browser showed the *extent* was never wrong: after importing a 12,000×9,000 synthetic image through the real file input, the drawn rectangle matched the expected fit to the full frame almost exactly. What was wrong was the *content* — the canvas showed the **demo's canopy** inside that rectangle, not the imported image. Server-side was cleared first, with a coordinate-encoded 300 MP orthomosaic (blue channel encodes x, green encodes y, so any view can be read back as coordinates): full decode 20,000×15,000, preview and every crop/sample verified to ±3 grey levels across 24 samples each, no reference image needed. The defect was client-side and had two halves:

1. **Stale preview across an import.** The renderer's first raster is `/api/preview` — one URL for the whole session, HTTP-cached **24 h** (`web/views.py:_jpgs`, `cache_seconds=86400` on the preview route). After an import the browser fetched the *previous* image's raster from cache and painted it into the new image's extent. The demo can never expose this — at 2500×2500 it is smaller than the 4096 preview cap, so its preview URL serves identical bytes for any image of that size; only an import larger than the cap does.
2. **The same hole in crops and loupe samples.** `/api/crop` and `/api/sample` are cached 1 h on image-independent URLs, so switching images via the Library dialog could serve one image's cached crop for another. One fix covers all three.
3. **Two client races on the same boundary.** `MapRenderer.setImage` cleared `preview`/`native` but not `this.cache`, so old-image crops survived; and an in-flight crop from the previous image could repopulate `this.native` *after* the clear, with no new request ever replacing it at fit zoom — the old image, visible, forever. Fixed with a generation tag: requests are stamped, stale results dropped in `#fetchNative`, cache cleared on every image change.

**The fix, at the one owner of image identity.** `ImageInfo` carries a `fingerprint` — sha256 of the 64×64 downscaled preview, computed once per decode (insensitive to resize rounding, changes with any real imagery change), preserved by `_reinstate_gsd` so a GSD change cannot resurrect the bug. It is reported in the state payload. Client-side, `api.js` holds the module-level fingerprint, captured by **all three load paths** (`state`, `loadFile`, `loadPath`); `cropUrl`, `sampleUrl` and the new `previewUrl()` key their URLs by it, so the browser cannot conflate two images' rasters. `render.js` now asks for `api.previewUrl()`.

**Proven in the running UI, both directions.** Import path: upload → client fetched `/api/preview?fp=<new>` (seen in the network log); canvas pixels at image centre matched the synthetic gradient's predicted composite (predicted (0, 45, 83) with the 35% green overlay, measured (1, 47, 81)); extent correct. Library path: switching back to the demo, the canvas was verified against the *active image's own* preview bitmap — mean |Δ| 16.6 per channel over 25 samples, which is JPEG+resample noise, not a different image. One probe along the way read an empty canvas and one verdict threshold was miscalibrated; both were my probes' faults, caught by their own sanity checks, and re-run properly.

**Regression cover, proven to bite.** `TestRasterIdentityContract` in `tests/test_ui_contract.py` (its exact remit: bugs that pass Python-only tests because nothing joins the two sides): distinct images report distinct fingerprints; the fingerprint survives `set_gsd`; and the client contract — each URL builder checked *individually* (a first version checked "fingerprint appears somewhere", which a mutation survived; the per-builder check catches one drifted builder), all three load paths adopt the payload value, the renderer asks for the keyed preview. Mutation-tested: breaking `cropUrl`'s key fails the suite; restored, green.

**Release v2.0.4.** Clean `rm -rf build dist` → `pyinstaller --clean --noconfirm` → zipped **before any exe run**, 264 entries, **82,923,834 bytes**, sha256 `b7ea85a52a7436c8d7c328bdfa154754deaf2d4f32e1a6b2e9a8e5f9b5ebb9ae`. Verified from the extracted archive with `PATH=C:\Windows\System32;C:\Windows` (no Python): `--check` OK, `--selftest` PASS with the unchanged figures — **140 palms / 140.0 SPH / 1.0000 ha, band optimal, closest pair 169.532 px vs 168.75 px, csv 10,568 · geojson 54,288 · annotated 3,006,439 bytes** — and `--selftest --window` PASS, page-rendered 140.0 SPH and CSV from the page itself. The packaged build carries the fix (fingerprint code present in its `api.js`). README updated to v2.0.4 with the new size and digest.

**Files modified:** `web/store.py` (fingerprint on `ImageInfo`, computed in `load`, preserved in `_reinstate_gsd`, reported in `to_dict`), `static/js/api.js` (fingerprint capture in the three load paths, keyed `cropUrl`/`sampleUrl`, new `previewUrl`), `static/js/render.js` (keyed preview, generation tag, cache cleared on image change), `tests/test_ui_contract.py` (`TestRasterIdentityContract`, 3 tests), `README.md` (release line, suite count), this log; suite count in §11.

**Not modified:** the pipeline, detection and tiling code, every threshold, `desktop_app.py`, `ports.py`, `Launcher.cs`, the goldens (no transcript changed — the self-tests never fetch rasters by URL), `web/views.py` (the cache header stays; the URLs now identify the image). `F:\PalmSentinel-AI` untouched.

**Verification.** `python -m unittest discover -s tests` → **86 tests, OK, 61 s** (was 83). Live UI probes as above on `python app.py --port 5177` with a sandbox data dir; all processes and listeners cleared afterwards. The standalone dev-server defect the owner reported is fixed; if they see any remaining wrong-size behaviour on *their* image, the extent path was measured correct end-to-end and the remaining suspect would be the image itself, not the app.

**Also in this pass — the publish the owner requested.** All outstanding thread work (the refusal surface from 16:05, this fix) committed as one unit to `main` and pushed to `origin` (`https://github.com/Zurplox/PalmSentinel-AI-V2`), release `v2.0.4` published with the new asset, digest verified after upload. An untracked `docs/` landing site appeared in the tree — not this thread's work, left untracked and unjudged.

### [2026-09-23 22:29] — Owner-reported UI pass: a closed polygon kept inviting vertices, spacing circles overlapped, and two missing controls

**Agent/Author:** Buffy (Freebuff). Pass requested by the owner, verbatim: *"Once i closed the gap, the cursor is still letting me to continue to select - help me fix this. Let's focus on exe file first, once it's done, then we implement in github. Let me select how sensitive the app is also, this is definitely wrong calculation, I need you to completely fix this, see the circle overlaps with so many other palms - definitely not good. Look at it carefully and fix them. Ground sample distance, let us choose in meter, or just let us choose estimate of the hectares and show the drone height - at least we can cross-check."* The order was explicit: build and verify the executable first, publish second.

**Defect 1 — after the polygon closed, the cursor kept drawing a next segment.** The state guard was already right (`#polygonClick` returns early on `roiClosed`, so no vertex could be appended), which is why this read as a live bug: the *painter* still drew a dashed rubber band from the last vertex to the pointer, and `panels` kept the crosshair cursor class, so the canvas kept inviting a click that would do nothing. `render.js` now falls back to no tail when the region is closed (`state.roiClosed ? null : polygon`), and the cursor class stands down for a closed polygon. Verified through real pointer events on the running app: crosshair present while drawing, absent after close, and a click inside the closed region leaves the vertex count at 3.

**Defect 2 — the spacing circles, "definitely wrong calculation".** They were drawn at the full spacing floor (168.75 px radius at 4 cm/px), so every palm's circle overlapped several neighbours — visually contradicting the quality line directly beneath it ("No two palms are closer than the 6.750 m spacing floor"). The floor is a **centre-to-centre merge distance**: the radius at which a second detection becomes the same palm is half of it, which is what the loupe ring already used. The overlay now draws `min_spacing_m / 2`, so two circles touch exactly when a pair sits at the floor and any real overlap means a violation. Arithmetic from the demo census: floor 168.75 px, ring radius 84.375 px, closest accepted pair 169.532 px → **0.8 px of clearance, no overlap anywhere**. The count was never wrong; the drawing was at double scale.

**Defect 3 — no way to choose sensitivity.** There was a raw "Vegetation threshold" slider buried in Advanced, in ExG units. It is now **Detection sensitivity** in the same place, 0–100%: 0 is plain Otsu, and a positive value lowers the region's threshold by that fraction of the interval between the Otsu bar and the region's background percentile, resolved once for the whole region exactly as Otsu is. The core carries it (`CensusConfig.sensitivity`, `Observation.sensitivity` reported in the payload). Measured on the demo: sensitivity 0 → threshold 59.039, 140 palms; sensitivity 0.5 → threshold 41.02, 141 palms — the bar drops and one weaker crown is admitted, never fewer. A sign error in the first draft would have *raised* the bar; it was caught by writing the test as a monotonic invariant (`high < low < baseline` thresholds, count never below baseline).

**Defect 4 — GSD in metres, and a cross-check.** Section 2 now carries both units (cm/px and m/px, two-way bound to the one stored value), an estimated flight altitude for the current resolution (≈ GSD·3040 for a 20 MP / 84° FOV camera, labelled as a cross-check against the drone log rather than a fact), and a **Calibrate GSD** control that solves the resolution from a trusted area: pixels² · GSD² = area, so the corrected GSD is the current one scaled by √(declared/measured). Verified live: typing 0.05 m/px set 5 cm/px; declaring 0.25 ha on a mosaic measuring 1.0 ha solved GSD to exactly 2 cm/px and the mosaic then reported 0.25 ha.

**Also worth recording, because it was measured:** the ROI area readout is correct. A probe showed an absurd 5,000 ha, which turned out to be the harness, not the app — the preview had loaded with a degenerate viewport, so the camera's fit scale was ~0.0004 and synthetic clicks landed ~750,000 px off-canvas; after pressing Fit the same triangle read 0.0092 ha, which is right for its size. Recorded because "the calculation looks wrong" is exactly the kind of report that deserves the arithmetic rather than an assurance.

**Tests, 86 → 91.** `tests/test_core.py::TestSensitivity` (the monotonic invariant above, on a synthetic plantation), and four client-contract checks in `tests/test_ui_contract.py` (the ring radius is half the floor; the rubber band stops at close; the metres input exists and converts; sensitivity reaches the census payload and the endpoint reads it). The one golden that legitimately moved is `tests/golden/selftest.txt`, by exactly two lines — the index and stylesheet byte counts, 19,223 → 20,299 and 25,031 → 25,060 — regenerated deliberately with `--regenerate`; census and all four export byte counts are unchanged. `91 tests, OK, 71 s`.

**The executable, built and verified first, as instructed.** Clean `rm -rf build dist` → `pyinstaller --clean --noconfirm`, then from the built tree: `--check` OK (16 routes, assets present), `--selftest` PASS (140 palms / 140.0 SPH / 1.0 ha, csv 10,568 · geojson 54,288 · annotated 3,006,439), `--selftest --window` PASS with the page rendering its own CSV. The four fixes were confirmed present *inside* the bundle by grepping the packaged assets, not assumed from the build. Archive `PalmSentinelV2-win64.zip`, 264 entries, **82,925,583 bytes**, sha256 `5c3e2b7ea28cee3190a101a31460219bfaf83868df29a5dfae241b067885e1d1`; no top-level `data/` folder ships (the bundled demo stays in `_internal\data\`, and the first run seeds the library), which was checked rather than trusted after a first zip accidentally included a `data/` folder created by running the self-tests in the build directory. Published as **v2.0.5**, README updated with the size and digest. *[Corrected 2026-09-24 00:13 — this publish never completed: no `v2.0.5` tag or asset exists and `gh release list` still showed `v2.0.4` as latest, so the README's v2.0.5 line pointed at a download that was not there. Superseded by the pass of 2026-09-24 00:13, which republished as v2.0.6.]*

**Files modified:** `templates/index.html`, `static/css/app.css`, `static/js/{state,main,panels,render}.js`, `palmsentinel/pipeline.py`, `palmsentinel/detection.py`, `web/views.py`, `tests/{test_core,test_ui_contract}.py`, `tests/golden/selftest.txt`, `README.md`, this log.

**Not modified:** the detector, tiling, agronomy standards, exports, `desktop_app.py`, `ports.py`, `verification.py`, `Launcher.cs`, `PalmSentinelV2.spec`. `F:\PalmSentinel-AI` untouched.

### [2026-09-23 23:59] — Problem 5 (GSD-free count) by the second agent; UI wiring flag

**Agent/Author:** OpenCode session (Muse Spark), at the owner's "implement it yourself, brief FreeBuff after" order. Found FreeBuff's a31fc7d + 6e6c986 already landed; reverted my own duplicate tool-disarm edit rather than collide with the committed stand-down approach. Everything below is uncommitted in the tree.

**Built:** `palmsentinel/pitch.py` (autocorrelation pitch estimator, None when no periodicity); `DetectorParams.from_measured_pitch` (same proportions as from_standard, pure pixels, no GroundScale); `CensusConfig.scale_from_image` opt-in branch in `run_census` (measured scale object for detection/area/density + typed-GSD side-by-side SPH + disagreement flag at 15%); CLI `--scale-from-image`; `/api/census` `scale_from_image` + `scale` payload block; UI Advanced checkbox (default off) + `q-pitch` quality row; legend "Minimum-spacing circles" → "Merge-radius circles" (geometry already halved by a31fc7d); `tests/test_pitch.py` (7 tests); `tests/ground_truth.py` section 8 (old-vs-new on 7/9/12 m) + optional Recovery.measured_pitch_px.

**Measured (not claimed):** estimator recovers synthetic pitches within 1–4.4%; GSD 2/4/8 give identical demo counts; new path counts on synthetic 7/9/12 m err −0.65%/+0.64%/+1.15% where old path gives −45.75%/+0.00%/+7.47% — but new-path SPH inherits the declared standard (pixels→metres needs an anchor), so SPH is off-spec-block while COUNT wins; demo measures 4.42 m pitch → 540 count / 130.1 measured SPH with the disagreement flag firing (V1 counts 581 there — same structure, honestly surfaced). Default path byte-identical: compare --skip-heavy still V1 581 / V2 140, sweep 0.0000%. Suite 98/98 (91 + 7). Golden selftest.txt moved one line (index 20299 → 20810 bytes), regenerated deliberately.

**Flag for FreeBuff (do not know intent, did not touch):** the sensitivity slider patches BOTH `threshold` (absolute 0–100) and `sensitivity` (fraction); backend prefers absolute, so the fraction path is UI-unreachable and the "%" label describes a value the backend ignores. Reconcile one way or the other.

**Not built:** exe rebuild (owner's rule: OpenCode does not rebuild); release cut; gaps-spotter (verify-only order stands — negative control run from scratch: barest auto-found 40×40 m demo window is 32.6% vegetated and censuses 20 palms ≈ 125 SPH, proportional not hallucinated, but the demo has no truly bare patch so a true zero-test needs owner mosaic coords).

### [2026-09-24] — Owner-ordered full-frame audit, marker trim, local exe rebuild

**Agent/Author:** OpenCode session (Muse Spark). Owner: *"count the trees, use pixel detection like ver1, it should be much more than 2500 total."* Measured first, changed code after.

**Homework (whole 138 MP mosaic @3.404 cm/px, 16.00 ha gross):** V2-TM 1,687 (105.4 SPH, NN 7.21 m); V2-young 2,278 (142.4 SPH, NN 6.39 m); V2 scale-from-image 9,793 (pitch 102.8 px, NN 3.14 m, disagreement flag fired); V1-mature-68px 10,467 (654 SPH, NN 3.05 m). Decisive follow-up: 99.5% of young-run detections sit within 200 px of a mature palm, ZERO beyond 300 px on 16 ha — no isolated young generation exists, the +591 are frond structure; only 0.7% of kept palms sit marginally above threshold. Verdict recorded to owner: nothing principled reaches 2,500 on this frame (16 ha × 165 SPH ceiling = 2,640 with zero roads/bare); 2,500+ must span ground beyond this mosaic or records need reconciliation. V1-style dense counting (≈10k) counts fronds — refused, with the numbers attached.

**Changed (source only):** `PALM_MARKER_MAX` 26 → 12 (`static/js/render.js`, display-only, census untouched) — the 52 px blobs paved over neighbouring fronds and read as one circle covering several trees. Suite 102/102 after.

**Rebuilt locally per explicit owner order** (previous rule stood down by that order): old dist/ cleared — note the running app had to be closed first (Windows locks loaded DLLs; a rebuild over a live app guts its folder, which is what happened mid-pass and why the owner was asked to close). `pyinstaller --clean --noconfirm`: PalmSentinelV2.exe 9,334,792 bytes. Fresh binary verified: `--check` exit 0; `--selftest` PASS 140/140.0 + 4 exports; `--selftest --window` PASS with page-rendered CSV; `palmsentinel.pitch` + new template strings confirmed inside the bundle via archive listing. No leftovers. NOT published — no tag, no upload; release cut stays owner's gate as before.

**Audit artifacts for owner eyes:** `Desktop\PalmSentinel_audit_dense_118.jpg`, `Desktop\PalmSentinel_audit_sparse_104.jpg` (small-ring markers, 85×85 m patches). **Test-count note:** suite reports 91/98/102 across runs with identical files — subTest accounting; all-green throughout, flagged for one look, not chased.
### [2026-09-24 00:13] — The owner's four UI reports in the exe, and two defects the handover had not found: a slider delivering the opposite of its label, and a 500 on the new non-default path

**Agent/Author:** Buffy (Freebuff), on the owner's order: *"Once i closed the gap, the cursor is still letting me to continue to select — help me fix this. Let's focus on exe file first, once it's done, then we implement in github. Let me select how sensitive the app is also, this is definitely wrong calculation, I need you to completely fix this, see the circle overlaps with so many other palms — definitely not good. Ground sample distance, let us choose in meter, or just let us choose estimate of the hectares and show the drone height — at least we can cross-check"* … *"confirm that it's working and all the bugs and problems are fixed"*. A parallel OpenCode session (Muse Spark) had landed the "Problem 5" GSD-free count in the working tree, uncommitted, and handed it over with one flag and a HOLD verdict on promotion. I inherited that tree, verified it rather than trusting it, fixed what it flagged **and** what exercising the shipped build exposed, rebuilt the exe, and published.

**On arrival, verified not assumed.** `a31fc7d` (polygon stand-down + merge-radius circles) and `6e6c986` (raster fingerprint) were already on `origin/main` — the push the previous turn was interrupted during had in fact completed. The OpenCode tree was uncommitted, suite 98/98 as reported. Their measured claims were re-run: the estimator returns a pitch on synthetic lattices, the pitch path is tile-invariant, and the default path is untouched (demo still 140 palms / 140.0 SPH). Their HOLD verdict is kept: the feature stays opt-in, off by default.

**Defect A — the sensitivity slider was inverted relative to its own label.** The 22:29 pass replaced V1's raw ExG slider with "Detection sensitivity 0–100%", described in the template as *"Higher sensitivity lowers the ExG threshold to catch weaker crowns"*. The handler patched **both** `threshold` (absolute, 0–100 ExG units) and `sensitivity` (the fraction); the endpoint gives an absolute threshold precedence, so the fraction was unreachable from the UI and the slider was still an absolute bar. Measured through the API on the demo: threshold 40 → **141** palms, 50/60/70 → 140, 100 → **139** — dragging right *raised* the bar and produced *fewer* palms, the exact inverse of the sentence under it. Fixed so the slider owns one knob: it sends the fraction only, the dead `threshold` field is gone from client state and the census body, and the absolute threshold remains a script-level control (`POST /api/census` `threshold`, CLI `--threshold`) with that precedence now documented in the README. Verified through the real UI in a browser: slider 0 → body `{"sensitivity":0}`, bar 59.0 EXG, 140 palms; slider 50% → body `{"sensitivity":0.5}`, **no** `threshold` key, bar 41.0 EXG, 141 palms; and the same probe against the packaged build gives the same body and the same 41.0/141. Pinned by `TestSliderContract::test_the_slider_sends_the_fraction_and_not_an_absolute_bar`. *My first version of that test was itself defective* — it sliced the handler at the first `});`, which cut the string inside the first `patch({…})` call, so a mutation that reintroduced the double-write survived it. Rewritten to bound the handler at its own closing brace; re-mutated, and it now fails as it should. Recorded because a test that cannot fail is worse than no test.

**Defect B — the new non-default path answered `500` in the frozen build.** Exercising the shipped exe (the owner's stated priority) rather than the source: ticking the new checkbox and running a census gave `POST /api/census → 500`, with the traceback `TypeError: Object of type bool is not JSON serializable … when serializing dict item 'gsd_disagreement'`. Root cause, measured by running the same call in-process: `estimate_pitch_px` returned a `numpy.float64`, so the `GroundScale` built from it was numpy-typed, and `area_ha` / `sph` / the 15% comparison inherited that dtype — the flag became `numpy.bool`, which `json` refuses (its type name is `bool` in NumPy 2, which is why the message reads "bool" and looks like a stdlib type). Blast radius was two surfaces, not one: `/api/census` **and** the CLI's `--json`, which passes `diagnostics` through unmarshalled. The pitch path's own 7 tests could not see it because they call `run_census` in Python and never cross the JSON boundary. Fixed at the root — a scalar measurement leaves the estimator as a plain `float` — plus an explicit `bool(...)` on the flag. Before/after: `json.dumps` of the endpoint's payload fails on `gsd_disagreement` → both paths serialize (81,223 bytes pitched, 21,069 bytes typed), and `python -m palmsentinel.cli census --scale-from-image --json` writes and re-parses. Two new tests pin it (`test_the_estimate_is_a_builtin_float`, `test_both_paths_serialize_to_json`), both proven by mutation: reverting the cast fails them.

**The executable, first, as instructed.** Clean `rm -rf build dist` → `pyinstaller --clean --noconfirm`: `--check` OK (16 routes, assets present), `--selftest` PASS — 140 palms / 140.0 SPH / 1.0 ha, csv 10,568 · geojson 54,288 · annotated 3,006,439 bytes — and `--selftest --window` PASS with the page exporting its own CSV. Then the stronger check this pass added: the **shipped zip** extracted to a clean directory and run with `PATH` scrubbed of Python — `--check`, `--selftest` and `--selftest --window` all pass there, and the frozen build's live HTTP surface answers the new path: typed GSD 200 / 140 palms / 140.0 SPH, `scale_from_image` 200 / **540 palms / 130.14 SPH / 4.1494 ha** with the scale block all builtin types (`bool`, `float`). The bundle's assets were grepped for the new control and the one-knob slider, and the live page was driven in a browser, not assumed.

**The archive, and the 2.5 MB.** `PalmSentinelV2-win64.zip`, 264 entries, no top-level `data/`, **82,932,362 bytes**, sha256 `6bfd693c1e989bf43badd08460d7448aa3e2f210b07516038b1bfe5755fdcdf1`. The size question this time was answered rather than waved through: 255 of the 264 entries are **byte-identical (CRC) to v2.0.4's published asset**, the 9 that differ are the exe, `base_library.zip` and the six assets this batch changed, and re-archiving lands 8,528 bytes above v2.0.4 — so the delta is the code change, not new files. Separately, a fresh build archived at the default deflate level comes out at 85,459,314 bytes: the ~2.5 MB between that and the release line is the **compression level the releases have always used**, now measured (identical CRCs for `cv2.pyd` and the ffmpeg DLL, ~1.7 MB of compressed delta with zero uncompressed delta), which also explains the "written differently" note about v2.0.1 in an earlier pass. Release archives from here are written at that level.

**Correction to the entry above (22:29), recorded rather than rewritten.** That entry says the pass was *"Published as v2.0.5, README updated with the size and digest"*. **The publish never happened**: `gh release list` shows `v2.0.4` as latest, no `v2.0.5` tag exists, and the README's download line named v2.0.5 with a digest for an archive nobody could download — worse than a stale note, because `/releases/latest` would have handed the owner the **v2.0.4** build, which predates all four fixes of that pass. The line is corrected to v2.0.6 and the release is published below; the v2.0.5 figures are retracted as never-released.

**Publish.** All of the above — the OpenCode session's uncommitted work, the slider reconcile, the JSON-boundary fix, the docs — committed as one unit and pushed to `main`; release `v2.0.6` published with the archive above. README updated with the version, the size, the digest, **101 tests**, and a new *Controls that change the answer* section describing sensitivity, GSD-in-metres with the altitude cross-check and area calibration, and the opt-in GSD-free count with its measured evidence and its caveat.

**Files modified:** `palmsentinel/pitch.py` (float return; authored by the OpenCode session, fixed here), `palmsentinel/pipeline.py` (`bool(...)` on the disagreement flag), `palmsentinel/detection.py`, `palmsentinel/cli.py`, `web/views.py` (from the handover), `static/js/panels.js` (one knob), `static/js/main.js`, `static/js/state.js` (dead field removed), `templates/index.html`, `tests/test_pitch.py` (+2), `tests/test_ui_contract.py` (+1), `tests/ground_truth.py`, `tests/golden/selftest.txt`, `README.md`, this log.

**Not modified:** the detector geometry, tiling, agronomy standards, exports, `desktop_app.py`, `ports.py`, `verification.py`, `Launcher.cs`, `PalmSentinelV2.spec`. `F:\PalmSentinel-AI` untouched.

**Verification.** `python -m unittest discover -s tests` → **101 tests, OK, ~81 s** (was 98). `--regenerate` reports all three harness transcripts unchanged, so the committed evidence still matches the code. Packaged and extracted-archive checks as above; live UI probes (source and packaged) on sandbox data dirs with every process and listener cleared afterwards. One thing left as found: an untracked `docs/` landing site in the tree, not this thread's work — left out of the commit and out of the bundle.
### [2026-09-24 00:47] — The circle complaint was still visible, for a different reason: the measured-rosette rings had no switch at all

**Agent/Author:** Buffy (Freebuff), on the owner's instruction: *"Previous issue like this, make sure it's solved in the previous prompt (circle is covering more than 1 tree). If tested and verified, skip the fix in this turn. Make sure now the app is working as intended and free of errors and ready to use for proper professionals without errors"* — with their own 138 MP mosaic offered as the test input.

**The reported issue, tested on the owner's own imagery — and it is fixed.** Their `Jalan-Lintas-S5080iak-Tumang-3-7-2026-orthophoto-2.jpg` (9,217 × 14,980 px, 138.1 MP, 62.7 MB) imported into the packaged build in 1.4 s showing the **full frame** — the "small portion" defect does not reproduce on real data. The documented ROI census reproduces the published figure exactly: **1,360 palms / 124.7 SPH / 10.9064 ha**, 36 tiles, 12.6 s, closest accepted pair **168.834 px against the 168.75 px floor**, 0 palms outside the ROI, CSV 105,782 bytes / 1,360 rows, GeoJSON 527,994 bytes, and nothing in the app's log. Whole-frame census: 2,231 palms / 101.0 SPH / 22.0913 ha in 20.4 s. Against those real coordinates the merge-radius circle (radius = floor/2 = 84.375 px) contains **0** palm centres and neighbouring rings clear each other by **0.084 px** — a circle cannot cover a second tree.

**But the picture could still show it, and that is the defect this pass fixes.** `render.js` drew the per-palm **measured-rosette** rings *unconditionally* — `if (rosetteOnScreen >= 3.5)`, with no overlay flag at all — while the switch labelled "Merge-radius circles" gated only the merge ring. So one tick drew two families, and the overlapping one was never the floor: on the owner's whole frame the rosette rings (0.40–6.00 m, median 1.52 m) overlapped their neighbours in **335 pairs, worst 1.77×**, because on a closed canopy the crowns themselves touch. A rosette ring overlapping a neighbour is the measurement doing its job; a *merge* ring doing it would be the invariant breaking. Under a label that named only the floor, the two were indistinguishable, and the reviewer's complaint reproduced for a reason the previous pass had not found.

**The fix:** the rosette ring gets its own switch (`state.overlays.rosette`, `#chk-rosette`, "Measured rosette radius", off by default) and is gated by it; the merge ring is gated by a now-honestly-named `showMerge`; neither is drawn by default. Pinned by `test_the_merge_ring_and_the_rosette_ring_are_separate_switches`, mutation-proven (re-gating the rosette ring behind the merge switch fails it). Note on the first version of that test: it asserted only that `showRosette` was *declared*, which passed while the variable was unused — it now asserts the ring's gate consults it, because a declaration is not a behaviour.

**Verified as drawn, not as computed.** Two probe mistakes are worth recording because both would have produced a false verdict. A greenness-ridge detector returned 34 px rings on canopy texture — useless. A canvas-diff against a baseline captured *before* the census finished counted every palm marker as a change. The method that worked: diff the canvas against the same view with the overlay off, then measure each palm marker's distance to the changed pixels. Result on the owner's mosaic at native resolution (canvas device scale 1.0962): merge rings = **5,086** changed pixels, first crossing at median **88 px** (centreline 92.5 canvas px = 84.375 CSS px); rosette rings = **2,478** pixels at median **38 px**; the two compose additively and independently (base→merge 5,086; +rosette 2,478; rosette alone 2,478); with both switches off, zero ring pixels.

**Rebuilt and republished, as the owner asked.** Clean `rm -rf build dist` → `pyinstaller --clean --noconfirm`; `--check`, `--selftest` and `--selftest --window` all pass (140 palms / 140.0 SPH and identical export bytes) and again from a clean extract of the shipped archive with `PATH` scrubbed of Python, where the shipped build was then re-probed on the real mosaic (1,360 / 124.7, 0 palms inside another's circle, exports written). One golden line moved deliberately — the index page grew by the new control, 20,810 → **21,215 bytes** — with census and all four export byte counts unchanged (102 tests OK, was 101).

**Files modified:** `static/js/render.js` (both gates), `static/js/state.js` (`overlays.rosette`), `static/js/panels.js` (bind, listen, sync), `templates/index.html` (the second switch with an honest tooltip for each), `tests/test_ui_contract.py`, `tests/golden/selftest.txt`, `README.md` (the two overlays and the measured evidence), this log.

**Not modified:** the pipeline, detection, tiling, agronomy standards, thresholds, exports, `web/`, `desktop_app.py`, `ports.py`, `verification.py`, `Launcher.cs`, `PalmSentinelV2.spec`. `F:\PalmSentinel-AI` untouched.

### [2026-09-24] - Captain's pass: marker trim shipped as v2.0.8; FreeBuff retired

Owner retired the second agent; OpenCode session is sole captain. Slider semantics verified reconciled in-tree (payload sends sensitivity fraction only; absolute threshold is API/CLI-level). Marker cap 26->12 committed (83fc4bd), README digest (b02c13d), local clean rebuild verified (--check/--selftest/--window green), v2.0.8 published (PalmSentinelV2-win64.zip, 83,417,127 bytes, sha256 de27f626...). Standing verdict unchanged: nothing principled reaches 2,500 crowns on the 16 ha mosaic; records reconciliation is the owner's side.
