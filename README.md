# PalmSentinel V2

Drone oil palm census (count + stand density) for 100 MP+ orthomosaics, with a
desktop window.

This is the second version. The first over-counted by roughly 4x — it expressed
its most important parameter, the minimum distance between two palms, in
**pixels** (`min_distance_px=68`), a value only correct near 13 cm/px. The
project's own imagery is 4 cm/px, where 68 px is 2.72 m of ground instead of the
~9 m of a real planting grid, so it counted frond-level apexes rather than palms
and reported 556–961 SPH against its own 136–143 SPH benchmark. Its 49/49 audit
suite passed throughout.

Full analysis, numbers and evidence: [`SYSTEM_LIVING_LOG_V2.md`](SYSTEM_LIVING_LOG_V2.md).

## Run it

### The packaged build — needs nothing installed

**[Download the standalone Windows build (v2.0.1)](https://github.com/Zurplox/PalmSentinel-AI-V2/releases/latest)**
— `PalmSentinelV2-win64.zip`, about 80 MB. Unzip it anywhere and run
`PalmSentinelV2.exe` from inside; the bundled demo orthomosaic loads on first run,
so the first census works with nothing else installed. Windows SmartScreen may warn
about an unknown publisher, because the build is not code-signed.

Or [build it yourself](#build-the-packaged-version), which produces the same
thing: `PalmSentinelV2.exe` plus the `_internal\` bundle beside it, and no `data\`
folder. `data\` is the imagery library; the first run creates it next to the
executable and copies the bundled demo into it, so the Library dialog has
something to list. Neither the download nor a build of your own contains anyone
else's imagery.

**What this needs:** Windows 10 or 11, 64-bit, and the Microsoft Edge WebView2
runtime. WebView2 ships with Windows 10/11 and with Microsoft Edge; if it has
been removed, install it from
<https://developer.microsoft.com/microsoft-edge/webview2/>.

**What this does not need:** no Python, no `pip install`, no network connection,
no developer tools. The build carries its own interpreter in `_internal/`.
Anything the application does — the vision pipeline, the census, every export —
runs on the machine it is launched from.

The build contains the application, its templates and stylesheets, and **one**
demo orthomosaic. It never contains anyone's own imagery: `data/` is the
operator's, often hundreds of megabytes per flight, and is excluded from the
package deliberately (see `PalmSentinelV2.spec`).

### From source — needs Python

```
pip install -r requirements.txt
python desktop_app.py
```

**What this needs:** Python 3.11 or newer (developed and tested on 3.14) and the
packages in `requirements.txt` — Flask, pywebview, Bottle, OpenCV, NumPy and
Pillow.

Either `Launch_PalmSentinel.bat` or `Launch_PalmSentinel.ps1` will pick the right
path for you. Both prefer the packaged build when one exists; otherwise they
check for Python *and* for the packages before opening a window, and name
whatever is missing instead of failing quietly. `PalmSentinel.exe` (compiled from
`Launcher.cs`) does the same and writes `launcher.log` next to itself, because a
GUI process has no console to report to.

### In a browser — needs Python, and no window

The same application also serves itself as a plain local web server, using the
same install as the desktop path:

```
pip install -r requirements.txt
python app.py
```

It names the flight it has loaded and then serves the identical interface on
<http://127.0.0.1:5000>:

```
  PalmSentinel V2
  ----------------------------------------------------------
  image   : demo_palm_estate.jpg 2,500x2,500 px @ 4 cm/px
  url     : http://127.0.0.1:5000
  data    : <checkout>\data
  ----------------------------------------------------------
  Local only. Imagery never leaves this computer.
```

The interface, the census, the numbers and the exports are the same ones the
desktop window and the packaged build produce.

### Launching it twice

The default port is **5000**. If it is already taken — by another copy of
PalmSentinel, or by anything else — the next free port is used, and the port that
was taken is named: on the console for `app.py`, and in the window title for the
desktop build, which reads `PalmSentinel V2 — port 5001` when it is not on 5000.
Two instances therefore never become two listeners on one port.

To choose the port yourself, pass `--port` to either entry point:

```
python app.py --port 5123
python desktop_app.py --port 5123
PalmSentinelV2.exe --port 5123
```

An explicit port is the port used, or the launch stops and says which port is in
the way and how to change it. It is never silently swapped for a different one.

### Build the packaged version

```
pyinstaller --noconfirm PalmSentinelV2.spec
```

Produces `dist/PalmSentinelV2/`, holding `PalmSentinelV2.exe` and the `_internal\`
bundle. A `data\` library folder appears beside the executable the first time you
run it. `build/` and `dist/` are not tracked; the build is reproducible from the
spec.

## Verify it

Both checks run inside whatever they are checking, so they work on the packaged
build with no Python present:

```
dist\PalmSentinelV2\PalmSentinelV2.exe --check              # is the desktop stack present?
dist\PalmSentinelV2\PalmSentinelV2.exe --selftest           # census + all four exports, headless
dist\PalmSentinelV2\PalmSentinelV2.exe --selftest --window  # the same, driven inside the real window
```

`--selftest` renders the packaged template, serves the packaged stylesheet, loads
the packaged orthomosaic, runs a census and writes four exports, then compares the
result against the verified figures below and exits non-zero if they differ. Add
`--report FILE` to keep the transcript. `--window` does the same through the live
UI — it reads the rendered numbers back out of the page.

Tests:

```
python -m unittest discover -s tests     # 70 tests, ~50 s, no imagery or network needed
python tests/ground_truth.py             # the true-versus-recovered tables, ~3.5 min
```

The comparison against V1 is **optional and not self-contained**: it imports the
predecessor's engine from a checkout of `PalmSentinel-AI` (read-only) and needs
that project's 138 MP flight line, which is not in this repository. Without the
checkout it exits 1 with a message saying so.

```
python tools/compare_v1_v2.py --v1-root path/to/PalmSentinel-AI
```

## The numbers

On the original author's bundled demo orthomosaic (1.0000 ha at 4 cm/px):

| | palms | SPH | median palm-to-palm |
| --- | ---: | ---: | ---: |
| V1 | 581 | 581.0 | 3.66 m |
| **V2** | **140** | **140.0** | **7.40 m** |

On the 138 MP `blok_tm_utara` block (10.9064 ha):

| | palms | SPH |
| --- | ---: | ---: |
| V1 | 6,066 | 556.2 |
| **V2** | **1,360** | **124.7** |

V1 suppressed apexes closer than 70 px (2.80 m); V2 requires 6.75 m, 0.75 of the
9 m mature pitch. Running V2's own pipeline with V1's spacing injected reproduces
V1's count to 0.96x — same tiles, same suppression, same image — which identifies
the over-count as a calibration defect rather than a pipeline one, and explains
why a self-referential audit could not see it.

**The V2 column is a measurement, not a verified truth.** Neither block has an
independent ground truth: `estate_sensus_summary.json` in the predecessor's
`output/` is V1's *own* export — it is the run that reports 556.2 SPH — and the
demo mosaic carries no labels. 140 and 124.7 are consistent with a mature block
planted to the estate standard. They are not evidence of one. What can be said
instead is below.

## Is the number true?

The exclusion radius is 0.75 of the standard's declared pitch, and it was chosen
while the demo's result was being watched — which is indistinguishable, from
outside, from a value fitted to one image. So the count is graded against a
triangular plantation whose density is arithmetic and which contains **nothing
from the pipeline**: known pitch, known palm coordinates, and a survey patch chosen
by geometry alone. Full tables: `python tests/ground_truth.py`.

Mature standard declared, palm size held at a real mature crown:

| true pitch (planting error) | true SPH | recovered SPH | error |
| --- | ---: | ---: | ---: |
| 9.0 m (0.0 m) | 142.6 | 143.5 | +0.6% |
| **9.0 m (1.0 m)** | **142.5** | **142.5** | **+0.0%** |
| 9.0 m (1.5 m) | 142.6 | 140.1 | −1.7% |
| 8.0 m (0.0 m) | 180.4 | 180.4 | +0.0% |
| 10.0 m (0.0 m) | 115.4 | 115.4 | +0.0% |
| 12.0 m (0.0 m) | 79.7 | 79.7 | +0.0% |
| 8.0 m (1.5 m) | 180.4 | 140.0 | **−22.4%** |

Four things follow.

**The count is a reading, not a constant.** Densities 2.3x apart come back as
they are, and where the standard is declared correctly the answer is the truth to
within a fraction of a percent. The last row is the warning that comes with it: a
block 28% denser than the demo reports **140.0 SPH — the demo's own number**. From
the number alone the two cannot be told apart.

**The fraction is not producing the answer.** Recovered SPH as the fraction moves,
on a surveyed lattice: 0.50 → +4.5%, 0.70 → +0.6%, 0.75 → +0.6%, 0.80 → +0.0%,
0.85 → +0.0%, 0.90 → −6.4%, 1.00 → −48%. There is a flat window from 0.70 to 0.85
and then a cliff, where the radius starts swallowing genuine neighbours. A fitted
constant slides across the whole range; this one has a plateau, and 0.75 sits
inside it rather than on its edge.

**The area is not inheriting a hidden constant either.** Tiling changes the count
by nothing at all (512 px against whole-image: identical), and the ground sample
distance changes the density by at most 1.3% across 2–8 cm/px — which matters
twice over, because area scales with GSD squared.

**The domain is the declared standard, and that is the dominant error term.** The
radius is a fraction of the pitch the caller *declares*: with ±1 m of planting
error the error stays under 8% while the true pitch is within 5.6% of the declared
one, and reaches −22% on a block 11% tighter (a 7.0 m block read as mature: −46%).
Nothing in the census detects a mis-declared standard. The fraction itself is known
to no better than about ±0.05: 0.70 has the smaller worst case across an 8–10 m
band (6.9% against 13.5%), but on real imagery the radius does most of its work
suppressing frond apexes — 628 raw candidates become 140 on the demo — and a
uniform synthetic crown cannot measure that side of the trade. 0.75 therefore
stands, and settling the last 0.05 needs a hand-labelled real patch.

## Layout

| Path | Responsibility |
| --- | --- |
| `palmsentinel/agronomy.py` | **Every** agronomic threshold. The only place a spacing, a density band or a target is written down. |
| `palmsentinel/scale.py` | The only pixel ↔ ground metre conversion. |
| `palmsentinel/detection.py` | Absolute vegetation index, one region-wide measurement context, apex finding, rosette radius. |
| `palmsentinel/tiling.py` | Exact-cover **core-ownership** tiling, so tile-seam duplicates are impossible by construction. |
| `palmsentinel/pipeline.py` | Orchestration, global spacing enforcement, area/SPH, verified post-conditions. |
| `palmsentinel/cli.py` | Headless single-image census. |
| `web/` | HTTP surface: `store.py` owns the loaded image, `views.py` owns the contracts. |
| `templates/`, `static/` | The interface. No CSS framework; the design system is `static/css/app.css`. |
| `desktop_app.py` | The desktop window, and the `--check` / `--selftest` entry points. |
| `app.py` | Browser entry point: the same application served on `127.0.0.1:5000`, no window. |
| `paths.py` | Where files live: read-only assets in the bundle, writable library beside the exe. |
| `ports.py` | Which port an instance serves on, and the guarantee that two never share one. |
| `Launcher.cs`, `PalmSentinel.exe` | Source-checkout launcher with a dependency preflight; writes `launcher.log` because a GUI process has no console. |
| `Launch_PalmSentinel.bat`, `.ps1` | The double-click entry points: prefer the packaged build, otherwise preflight Python and name whatever is missing. |
| `PalmSentinelV2.spec` | The standalone build's recipe. |
| `dist/PalmSentinelV2/` | The standalone build itself (not tracked, ~196 MB). Carries `_internal/` plus a writable `data/` alongside. |
| `tests/` | The suite, plus `ground_truth.py`: the true-versus-recovered sweeps behind [Is the number true?](#is-the-number-true). |

`paths.py` draws one line deliberately: assets (`templates/`, `static/`, the demo
image) are read-only and live inside the bundle, while the imagery library is
writable and lives next to the executable — or under `%LOCALAPPDATA%` if that
location is read-only. A build that wrote its library into the bundle would lose
every upload when it exited.

## Command line

```
python -m palmsentinel.cli census --image data/demo_palm_estate.jpg --gsd 4.0

python -m palmsentinel.cli census --image mosaic.jpg --gsd 4.0 \
    --polygon "300,1200 4200,400 9000,1000 8600,8600 4200,9300 300,7800" \
    --json report.json
```

Polygons are **always full-resolution pixels**; there is no "overview" coordinate
space in the core, which removes the class of bug where a caller and the engine
disagree about which space a vertex is in. Exit code is `0` only if the spacing
invariant held.

## What this does not do

- No maturity/age tiers. On a closed mature canopy a radial profile measures the
  bright apical rosette (~3 m), not the crown footprint (~8 m), so tiering on it
  would label mature palms as young. It needs segmentation; it is deferred rather
  than shipped wrong.
- Every figure scales from the ground sample distance, and nothing in the imagery
  verifies the GSD you enter. A wrong GSD gives a wrong area and a wrong density.
- No hand-labelled ground truth exists for the estate imagery, so the published
  demo and 138 MP figures are measurements consistent with the standard, not
  verified counts. The model behind them is validated against a synthetic
  plantation of exactly known density ([above](#is-the-number-true)), which pins
  the exclusion fraction to about ±0.05 and cannot pin it further.
- The census cannot tell whether you declared the right planting standard. A
  mature block planted tighter than its declared 9 m is undercounted — by 13% at
  8 m with ordinary planting error. Pass the standard that matches the block.
