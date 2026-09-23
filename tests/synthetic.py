"""
Synthetic oil-palm orthomosaics with a *known* true density.

Shared by the core and API suites so both are graded against the same generated
imagery, and so the generator exists in exactly one place.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import cv2
import numpy as np

from palmsentinel.scale import GroundScale

Point = Tuple[float, float]
Rect = Tuple[float, float, float, float]  # x0, y0, x1, y1 in ground metres


def nominal_sph(pitch_m: float) -> float:
    """
    The stand density a triangular planting of this nearest-neighbour pitch *is*.

    A triangular (equilateral) grid of pitch ``p`` occupies ``p * p * sqrt(3)/2``
    square metres per palm, so the density is exact arithmetic, not a measurement:
    9.0 m gives 142.55 SPH, which is the 9.0 m x 7.8 m estate standard quoted in
    :mod:`palmsentinel.agronomy`.  This is the ground truth the detector is graded
    against, and it contains nothing from the pipeline.
    """
    return 10000.0 * 2.0 / (math.sqrt(3.0) * pitch_m * pitch_m)


def triangular_lattice(pitch_m: float, cols: int, rows: int) -> List[Point]:
    """A triangular (equilateral) planting grid with nearest-neighbour pitch."""
    row_spacing = pitch_m * math.sqrt(3.0) / 2.0
    points = []
    for r in range(rows):
        offset = (pitch_m / 2.0) if r % 2 else 0.0
        for c in range(cols):
            points.append((offset + c * pitch_m, r * row_spacing))
    return points


def jittered_lattice(
    pitch_m: float,
    cols: int,
    rows: int,
    jitter_m: float = 0.0,
    seed: int = 7,
) -> List[Point]:
    """
    A planted grid with planting error.

    Real blocks are not surveyed lattices: a planting crew leaves each palm some
    distance from its nominal position, and the two palms that close on each other
    can end up much nearer than the pitch.  ``jitter_m`` is the radius of a disc
    around each nominal position, sampled uniformly in area, which is the standard
    way to model that.  Jitter is what decides whether a minimum-spacing rule that
    is a *fraction of the pitch* is safe, so it has to be a parameter rather than
    an accident of the fixture.
    """
    points = triangular_lattice(pitch_m, cols, rows)
    if jitter_m <= 0.0:
        return points
    rng = np.random.default_rng(seed)
    scattered: List[Point] = []
    for x, y in points:
        radius = jitter_m * math.sqrt(rng.random())  # uniform over the disc
        angle = rng.random() * 2.0 * math.pi
        scattered.append((x + radius * math.cos(angle), y + radius * math.sin(angle)))
    return scattered


def points_inside(roi: Rect, points_m: Sequence[Point]) -> int:
    """How many planted palms lie strictly inside an axis-aligned region."""
    x0, y0, x1, y1 = roi
    return sum(1 for x, y in points_m if x0 < x < x1 and y0 < y < y1)


def representative_roi(
    points_m: Sequence[Point],
    pitch_m: float,
    inset_range: Tuple[float, float] = (1.0, 4.0),
) -> Rect:
    """
    The patch of a plantation whose own density **is** ``nominal_sph(pitch)``.

    An inset rectangle does not automatically contain a representative number of
    palms: the count is an integer, so truncating at the edges can lose most of a
    column and bias the patch density by several percent.  Rather than let that
    bias be mistaken for detector error, the inset is chosen from a fine sweep as
    the one whose implied density lands closest to the standard's exact density.

    The choice depends only on the planted coordinates and the ROI area -- it
    contains no measurement, no detection and no threshold -- so it removes an
    edge artefact without flattering the pipeline.
    """
    xs = [p[0] for p in points_m]
    ys = [p[1] for p in points_m]
    target = nominal_sph(pitch_m)
    lo, hi = inset_range
    best: Tuple[float, Rect] = (float("inf"), (0.0, 0.0, 0.0, 0.0))
    steps = 150
    for step in range(steps + 1):
        inset = lo + (hi - lo) * step / steps
        roi = (
            min(xs) + inset,
            min(ys) + inset,
            max(xs) - inset,
            max(ys) - inset,
        )
        x0, y0, x1, y1 = roi
        if x1 <= x0 or y1 <= y0:
            continue
        count = points_inside(roi, points_m)
        if count == 0:
            continue
        area_ha = (x1 - x0) * (y1 - y0) / 10000.0
        deviation = abs(count / area_ha - target)
        if deviation < best[0]:
            best = (deviation, roi)
    return best[1]


def roi_polygon_px(roi: Rect, origin_m: Point, scale: GroundScale):
    """An axis-aligned ground region as an image-space polygon."""
    x0, y0, x1, y1 = roi
    ox, oy = origin_m
    return (
        (scale.m_to_px(x0 - ox), scale.m_to_px(y0 - oy)),
        (scale.m_to_px(x1 - ox), scale.m_to_px(y0 - oy)),
        (scale.m_to_px(x1 - ox), scale.m_to_px(y1 - oy)),
        (scale.m_to_px(x0 - ox), scale.m_to_px(y1 - oy)),
    )


def render_plantation_at(
    points_m: Sequence[Point],
    scale: GroundScale,
    crown_radius_m,
    apex_radius_m: float,
    margin_m: float,
    seed: int = 5,
):
    """
    Render a synthetic orthomosaic: soil background, green crowns, bright apexes.

    Returns ``(image_bgr, polygon_px, origin_m)``.  ``origin_m`` is the ground
    coordinate of pixel (0, 0), which is what a test needs in order to express a
    manual marker in the same terms as the palms it is placed among.
    """
    radii_m = (
        [float(crown_radius_m)] * len(points_m)
        if np.isscalar(crown_radius_m)
        else [float(r) for r in crown_radius_m]
    )
    xs = [p[0] for p in points_m]
    ys = [p[1] for p in points_m]
    x0, y0 = min(xs) - margin_m, min(ys) - margin_m
    x1, y1 = max(xs) + margin_m, max(ys) + margin_m

    w = int(round(scale.m_to_px(x1 - x0)))
    h = int(round(scale.m_to_px(y1 - y0)))
    rng = np.random.default_rng(seed)
    image = np.zeros((h, w, 3), dtype=np.float32)
    image[:, :] = (40.0, 70.0, 110.0)

    def to_px(mx: float, my: float) -> Tuple[int, int]:
        return (
            int(round(scale.m_to_px(mx - x0))),
            int(round(scale.m_to_px(my - y0))),
        )

    apex_px = max(2, int(round(scale.m_to_px(apex_radius_m))))

    for (mx, my), radius_m in zip(points_m, radii_m):
        cx, cy = to_px(mx, my)
        crown_px = max(2, int(round(scale.m_to_px(radius_m))))
        cv2.circle(image, (cx, cy), crown_px, (30.0, 180.0, 50.0), -1)
    for mx, my in points_m:
        cx, cy = to_px(mx, my)
        cv2.circle(image, (cx, cy), apex_px, (60.0, 245.0, 100.0), -1)

    image += rng.normal(0.0, 4.0, size=image.shape)
    image = np.clip(image, 0, 255).astype(np.uint8)

    polygon_m = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    polygon_px = tuple(
        (scale.m_to_px(px - x0), scale.m_to_px(py - y0)) for px, py in polygon_m
    )
    return image, polygon_px, (x0, y0)


def render_plantation(
    points_m: Sequence[Point],
    scale: GroundScale,
    crown_radius_m,
    apex_radius_m: float,
    margin_m: float,
    seed: int = 5,
):
    """``render_plantation_at`` without the ground origin."""
    image, polygon_px, _ = render_plantation_at(
        points_m, scale, crown_radius_m, apex_radius_m, margin_m, seed
    )
    return image, polygon_px


def sparse_plantation(scale: GroundScale, pitch_m: float = 30.0):
    """
    Four palms on a wide grid, with the ground origin.

    Wide enough that the gaps between palms are far larger than any planting
    standard's minimum spacing, which is what a test needs in order to place a
    manual marker *between* palms rather than on top of one.
    """
    points = [(20.0, 20.0), (20.0 + pitch_m, 20.0),
              (20.0, 20.0 + pitch_m), (20.0 + pitch_m, 20.0 + pitch_m)]
    return render_plantation_at(
        points,
        scale,
        crown_radius_m=3.0,
        apex_radius_m=1.2,
        margin_m=15.0,
    )


def to_px(origin_m: Point, scale: GroundScale, mx: float, my: float) -> Tuple[float, float]:
    """Ground metres to image pixels, using the origin a renderer returned."""
    x0, y0 = origin_m
    return (scale.m_to_px(mx - x0), scale.m_to_px(my - y0))
