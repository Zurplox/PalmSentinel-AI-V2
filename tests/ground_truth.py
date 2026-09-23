"""
Ground-truth density recovery -- is the number true, or fitted?

The pipeline's exclusion radius is ``min_spacing_fraction`` of the standard's
planting pitch.  That fraction was chosen by watching one image: on the bundled
demo mosaic the resulting density is 164 SPH at 0.60, 154 at 0.65, 147 at 0.70,
**140 at 0.75**, 127 at 0.80 and 109 at 0.85, and 0.75 is the only setting that
lands inside the 136-143 SPH target the project quotes as its benchmark.  From
outside, that is indistinguishable from a defensible agronomic model and from a
value fitted to one orthomosaic -- and the demo mosaic, being the thing the
fraction was chosen on, cannot settle it.

So this module grades the count against ground truth that contains **nothing from
the pipeline**: a triangular planting grid of exactly known pitch, whose density
is arithmetic (:func:`synthetic.nominal_sph`), whose palms are planted at known
coordinates, and whose survey region is chosen to be representative of that
density by geometry alone.  The axes that could hide a fit -- true pitch, planting
error, ground sample distance, tile size, and how much of the frame is surveyed --
are swept, and the recovered count is reported against the true one.

It is importable so the test suite can assert the findings, and runnable for the
table:

    python tests/ground_truth.py
"""

from __future__ import annotations

import dataclasses
import math
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# Runnable as a script -- ``python tests/ground_truth.py``, which is how the
# tables get reproduced -- as well as importable from the suite.  The script form
# puts ``tests/`` on the path, not the repository root, so the root is added here
# rather than documented as a PYTHONPATH the caller has to remember.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from palmsentinel.agronomy import get_standard
from palmsentinel.pipeline import CensusConfig, run_census
from palmsentinel.scale import GroundScale

from synthetic import (
    jittered_lattice,
    nominal_sph,
    points_inside,
    render_plantation_at,
    representative_roi,
    roi_polygon_px,
)

#: Canopy and bud size scale with the planting pitch, so the canopy closure is
#: held constant across the sweep.  A 9.0 m pitch therefore renders 4.05 m crowns
#: and a 1.17 m bud, matching the fixture the rest of the suite uses.
CROWN_RADIUS_FRACTION = 0.45
APEX_RADIUS_FRACTION = 0.13

#: The value under test: the fraction of the standard's pitch that two palms must
#: exceed to both be counted.  Read from the product, never written down twice.
UNDER_TEST = get_standard("mature").min_spacing_fraction

#: The pipeline's own default tiling, so the sweeps measure the shipped behaviour
#: unless a case deliberately changes the tile size.
DEFAULT_TILE_PX = CensusConfig.__dataclass_fields__["tile_px"].default

#: "One tile, no seams": the whole region at once (the comparison harness uses the
#: same expression).
SINGLE_TILE = 1 << 20


@dataclass(frozen=True)
class Recovery:
    """One plantation, its true density, and what the census made of it."""

    pitch_m: float
    jitter_m: float
    gsd_cm: float
    tile_px: int
    frame_fraction: float
    fraction: float

    true_count: int
    true_sph: float
    nominal_sph: float
    area_ha: float
    recovered: int
    recovered_sph: float

    @property
    def count_error(self) -> float:
        return (self.recovered - self.true_count) / self.true_count

    @property
    def sph_error(self) -> float:
        return (self.recovered_sph - self.true_sph) / self.true_sph

    @property
    def roi_is_representative(self) -> float:
        """How far the chosen patch's own density sits from the standard's."""
        return abs(self.true_sph - self.nominal_sph) / self.nominal_sph


def _region_for_frame(
    points: Sequence[Tuple[float, float]],
    pitch_m: float,
    frame_fraction: float,
):
    """
    A representative patch covering roughly ``frame_fraction`` of the frame.

    Shrinking the survey region by simply cutting it down would slice through
    planting cells and bias the patch density, so the inset is still chosen by
    :func:`synthetic.representative_roi` -- the scan is just narrowed to a band
    around the inset that gives the requested coverage.
    """
    if frame_fraction >= 0.999:
        return representative_roi(points, pitch_m)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    # Linear scale that turns the full region into the requested area fraction.
    scale = math.sqrt(frame_fraction)
    wanted = (1.0 - scale) / 2.0 * min(max(xs) - min(xs), max(ys) - min(ys))
    half = 0.75 * pitch_m
    lo = max(0.5 * pitch_m, wanted - half)
    hi = wanted + half
    return representative_roi(points, pitch_m, inset_range=(lo, hi))


def recover(
    pitch_m: float,
    *,
    jitter_m: float = 0.0,
    gsd_cm: float = 4.0,
    frame_fraction: float = 1.0,
    fraction: float = UNDER_TEST,
    tile_px: int = DEFAULT_TILE_PX,
    crown_pitch_m: Optional[float] = None,
    cols: int = 14,
    rows: int = 14,
    seed: int = 7,
) -> Recovery:
    """
    Plant a grid of known density, census it, and report both numbers.

    ``crown_pitch_m`` decides how large a palm is drawn.  Left ``None``, crowns
    scale with the sampled pitch -- the whole plantation gets bigger or smaller
    together.  Set to the standard's own pitch, the palms keep the size a mature
    oil palm actually has (~8 m of crown) and only the *spacing* changes, which is
    the agronomically honest way to ask what happens on an off-specification block:
    a 12 m planting is normal palms further apart, not 5.4 m giants.
    """
    scale = GroundScale(gsd_cm)
    points = jittered_lattice(pitch_m, cols, rows, jitter_m, seed)
    crown_pitch = pitch_m if crown_pitch_m is None else crown_pitch_m
    image, _, origin = render_plantation_at(
        points,
        scale,
        crown_radius_m=CROWN_RADIUS_FRACTION * crown_pitch,
        apex_radius_m=APEX_RADIUS_FRACTION * crown_pitch,
        margin_m=1.5 * max(pitch_m, crown_pitch),
    )
    roi = _region_for_frame(points, pitch_m, frame_fraction)
    x0, y0, x1, y1 = roi
    area_ha = (x1 - x0) * (y1 - y0) / 10000.0
    true_count = points_inside(roi, points)

    standard = dataclasses.replace(get_standard("mature"), min_spacing_fraction=fraction)
    result = run_census(
        image,
        CensusConfig(
            standard=standard,
            scale=scale,
            polygon=roi_polygon_px(roi, origin, scale),
            tile_px=tile_px,
        ),
    )
    return Recovery(
        pitch_m=pitch_m,
        jitter_m=jitter_m,
        gsd_cm=gsd_cm,
        tile_px=tile_px,
        frame_fraction=frame_fraction,
        fraction=fraction,
        true_count=true_count,
        true_sph=true_count / area_ha,
        nominal_sph=nominal_sph(pitch_m),
        area_ha=area_ha,
        recovered=result.total_palms,
        recovered_sph=result.sph,
    )


# --------------------------------------------------------------------------
# The sweeps
# --------------------------------------------------------------------------


def fraction_sweep(
    pitch_m: float = 9.0,
    jitters: Sequence[float] = (0.0, 1.0, 1.5),
    fractions: Sequence[float] = tuple(round(0.45 + 0.05 * i, 2) for i in range(12)),
) -> List[Recovery]:
    """
    Recovered count as a function of the exclusion fraction.

    This is the sweep that separates a model from a fit.  If detection were
    perfect, the recovered count would be *flat* over every fraction below the
    true nearest-neighbour distance and then fall away once the radius began
    swallowing real neighbours -- so a fraction sitting in the flat part does not
    manufacture the answer.  A count that slides smoothly with the fraction across
    the whole range means each step is merging differently, and the value then has
    to be justified rather than found.
    """
    return [
        recover(
            pitch_m,
            jitter_m=jitter,
            fraction=fraction,
            crown_pitch_m=STANDARD_PITCH_M,
        )
        for jitter in jitters
        for fraction in fractions
    ]


def gsd_sweep(
    gsds: Sequence[float] = (2.0, 3.0, 4.0, 6.0, 8.0),
    pitch_m: float = 9.0,
    jitter_m: float = 1.0,
) -> List[Recovery]:
    return [
        recover(pitch_m, jitter_m=jitter_m, gsd_cm=gsd, crown_pitch_m=STANDARD_PITCH_M)
        for gsd in gsds
    ]


def tile_sweep(
    tiles: Sequence[int] = (512, 1024, DEFAULT_TILE_PX, 2048, SINGLE_TILE),
    pitch_m: float = 9.0,
    jitter_m: float = 1.0,
) -> List[Recovery]:
    return [
        recover(pitch_m, jitter_m=jitter_m, tile_px=tile, crown_pitch_m=STANDARD_PITCH_M)
        for tile in tiles
    ]


def frame_sweep(
    fractions: Sequence[float] = (1.0, 0.85, 0.70, 0.50, 0.25),
    pitch_m: float = 9.0,
    jitter_m: float = 1.0,
) -> List[Recovery]:
    return [
        recover(
            pitch_m,
            jitter_m=jitter_m,
            frame_fraction=fraction,
            crown_pitch_m=STANDARD_PITCH_M,
        )
        for fraction in fractions
    ]


#: The pitches an estate actually plants, spanning the standard's own 9.0 m pitch
#: by a full +/-1 m (the band an agronomist would call the same planting standard), and
#: the fraction values a reviewer would compare 0.75 against.
BAND_PITCHES: Tuple[float, ...] = (8.0, 9.0, 10.0)
BAND_FRACTIONS: Tuple[float, ...] = (0.65, 0.70, 0.75, 0.80)


STANDARD_PITCH_M = get_standard("mature").expected_spacing_m


def real_palm_pitch_sweep(
    pitches: Sequence[float] = (7.0, 8.0, 9.0, 10.0, 12.0),
    jitters: Sequence[float] = (0.0, 1.0, 1.5),
) -> List[Recovery]:
    """
    Spacing varies, palm size does not.

    Real mature palms are a fixed physical size, so widening the planting spaces
    them further apart rather than growing them.  Holding the crown at the
    standard's pitch and moving only the true pitch is therefore the honest test of
    the exclusion radius, and it is the axis on which a fixed radius of
    0.75 x 9.0 m == 6.75 m is most likely to fail at the dense end.
    """
    return [
        recover(pitch, jitter_m=jitter, crown_pitch_m=STANDARD_PITCH_M)
        for pitch in pitches
        for jitter in jitters
    ]


def fraction_over_band_sweep(
    fractions: Sequence[float] = BAND_FRACTIONS,
    pitches: Sequence[float] = BAND_PITCHES,
    jitters: Sequence[float] = (0.0, 1.0),
) -> List[Recovery]:
    """
    Every candidate fraction against every pitch in the standard's own band.

    The demo mosaic can only ever say which fraction reproduces the number it was
    tuned to; this says which fraction is *least wrong* across the plantings the
    standard claims to describe.  A value justified by biology should be the one
    that minimises the worst case here, and should not be perched on the edge of
    the window it needs.
    """
    return [
        recover(pitch, jitter_m=jitter, fraction=fraction, crown_pitch_m=STANDARD_PITCH_M)
        for fraction in fractions
        for pitch in pitches
        for jitter in jitters
    ]


def pitch_floor_and_ceiling() -> List[Recovery]:
    """
    The disproof case: densities far either side of 140 SPH.

    If the number were an artefact of the model rather than a reading of the
    image, these would still come back near 140.  A 7.0 m pitch is 235 SPH and a
    12.0 m pitch is 80 SPH -- a factor of three apart -- so tracking them is the
    evidence that the census is measuring rather than reproducing a constant.
    """
    return [
        recover(pitch, crown_pitch_m=STANDARD_PITCH_M) for pitch in (7.0, 9.0, 12.0)
    ]


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------


def _head(title: str, columns: str) -> None:
    print()
    print(title)
    print("  " + columns)
    print("  " + "-" * (len(columns) + 2))


def _row(item: Recovery, tag: str = "") -> None:
    print(
        f"  {tag:<12} p={item.pitch_m:4.1f}m  j={item.jitter_m:3.1f}m  "
        f"gsd={item.gsd_cm:3.1f}  tile={str(item.tile_px):>4}  "
        f"frame={item.frame_fraction:5.2f}  f={item.fraction:4.2f}  "
        f"true={item.true_count:4d}/{item.true_sph:6.1f}  "
        f"got={item.recovered:4d}/{item.recovered_sph:6.1f}  "
        f"count err={item.count_error:+7.2%}  SPH err={item.sph_error:+7.2%}"
    )


def main() -> int:
    print("Ground truth: a triangular planting of exactly known pitch.")
    print(f"Exclusion fraction under test: {UNDER_TEST} of the pitch")
    print(
        "  nominal density is arithmetic: 9.0 m -> "
        f"{nominal_sph(9.0):.2f} SPH, 7.0 m -> {nominal_sph(7.0):.2f}, "
        f"12.0 m -> {nominal_sph(12.0):.2f}"
    )

    _head(
        "1. TRUE PITCH x PLANTING ERROR (palm size real, fraction at the shipped value)",
        "jitter   pitch  gsd   tile  frame    f     true cnt/SPH    recovered cnt/SPH   errors",
    )
    for item in real_palm_pitch_sweep():
        _row(item)

    _head(
        "2. THE FRACTION ITSELF -- does the count move with it, or does it sit flat?",
        "jitter   pitch  gsd   tile  frame    f     true cnt/SPH    recovered cnt/SPH   errors",
    )
    for item in fraction_sweep():
        _row(item)

    _head(
        "3. WHICH FRACTION IS LEAST WRONG ACROSS THE STANDARD'S OWN BAND?",
        "f      pitch  jitter    true SPH    recovered SPH      SPH err",
    )
    worst: Dict[float, float] = {}
    for item in fraction_over_band_sweep():
        print(
            f"  {item.fraction:4.2f}   {item.pitch_m:4.1f}m  {item.jitter_m:4.1f}m    "
            f"{item.true_sph:7.1f}    {item.recovered_sph:7.1f}    {item.sph_error:+8.2%}"
        )
        worst[item.fraction] = max(worst.get(item.fraction, 0.0), abs(item.sph_error))
    print("\n  worst-case |SPH error| over that band:")
    for fraction in sorted(worst):
        tag = "   <- shipped" if fraction == UNDER_TEST else ""
        print(f"    f={fraction:.2f}   {worst[fraction]:7.2%}{tag}")

    _head("4. GROUND SAMPLE DISTANCE", "row")
    for item in gsd_sweep():
        _row(item)

    _head("5. TILE SIZE", "row")
    for item in tile_sweep():
        _row(item)

    _head("6. HOW MUCH OF THE FRAME IS SURVEYED", "row")
    for item in frame_sweep():
        _row(item)

    _head(
        "7. THE DISPROOF: densities a factor of three apart",
        "row",
    )
    for item in pitch_floor_and_ceiling():
        _row(item)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
