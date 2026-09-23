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


def triangular_lattice(pitch_m: float, cols: int, rows: int) -> List[Point]:
    """A triangular (equilateral) planting grid with nearest-neighbour pitch."""
    row_spacing = pitch_m * math.sqrt(3.0) / 2.0
    points = []
    for r in range(rows):
        offset = (pitch_m / 2.0) if r % 2 else 0.0
        for c in range(cols):
            points.append((offset + c * pitch_m, r * row_spacing))
    return points


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
