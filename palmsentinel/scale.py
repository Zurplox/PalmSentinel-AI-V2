"""
Ground scale and image geometry.

This is the *only* module that converts between pixels and ground distance.
Version 1 spread that arithmetic across ``app.py`` (which derived a
``scale_factor`` from image width), ``roi_utils.py`` (which re-derived metres
from cm/px), the loupe endpoint and the frontend -- four implementations, and a
``coord_scale`` negotiation string passed over HTTP to say which space a
polygon was in.  Any disagreement between them silently produced wrong areas.

V2 makes the frame explicit: geometry is either in **full-resolution pixel
space** or in **ground metres**, and a conversion is a call on a
:class:`GroundScale` rather than an inline multiply.  There is no "overview"
space in the core at all; a UI may downscale for display, but the census always
runs on full-resolution pixels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Tuple

Point = Tuple[float, float]


@dataclass(frozen=True)
class GroundScale:
    """A ground sample distance, and the pixel<->metre conversions built on it."""

    gsd_cm_per_px: float

    def __post_init__(self) -> None:
        if not (self.gsd_cm_per_px > 0.0) or not math.isfinite(self.gsd_cm_per_px):
            raise ValueError(
                f"GSD must be a positive, finite number of cm/px, "
                f"got {self.gsd_cm_per_px!r}"
            )

    @property
    def m_per_px(self) -> float:
        return self.gsd_cm_per_px / 100.0

    def m_to_px(self, metres: float) -> float:
        return metres / self.m_per_px

    def px_to_m(self, pixels: float) -> float:
        return pixels * self.m_per_px

    def px_area_to_m2(self, px_area: float) -> float:
        return px_area * self.m_per_px ** 2

    def px_area_to_ha(self, px_area: float) -> float:
        return self.px_area_to_m2(px_area) / 10_000.0

    def px_to_odd(self, metres: float, minimum: int = 3) -> int:
        """A metres length as an odd pixel kernel size (required by OpenCV)."""
        size = int(round(self.m_to_px(metres)))
        if size % 2 == 0:
            size += 1
        return max(minimum, size)

    def px_to_int(self, metres: float, minimum: int = 1) -> int:
        return max(minimum, int(round(self.m_to_px(metres))))

    def describe(self) -> str:
        return f"{self.gsd_cm_per_px:g} cm/px ({self.m_per_px * 100:.1f} cm per pixel)"


@dataclass(frozen=True)
class ImageFrame:
    """The pixel extent of an orthomosaic plus its ground scale."""

    width: int
    height: int
    scale: GroundScale

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"Degenerate frame {self.width}x{self.height}")

    @property
    def m_per_px(self) -> float:
        return self.scale.m_per_px

    @property
    def full_area_ha(self) -> float:
        return self.scale.px_area_to_ha(float(self.width) * float(self.height))

    @property
    def ground_extent_m(self) -> Tuple[float, float]:
        return (
            self.scale.px_to_m(self.width),
            self.scale.px_to_m(self.height),
        )

    def describe(self) -> str:
        w_m, h_m = self.ground_extent_m
        return (
            f"{self.width:,}x{self.height:,} px @ {self.scale.describe()} "
            f"= {w_m:,.0f} m x {h_m:,.0f} m ({self.full_area_ha:,.2f} ha)"
        )


def shoelace_area_px(polygon: Iterable[Point]) -> float:
    """Unsigned polygon area in px^2 (Shoelace / surveyor's formula)."""
    pts = list(polygon)
    if len(pts) < 3:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def bounds_of(polygon: Iterable[Point]) -> Tuple[int, int, int, int]:
    """Integer ``(x0, y0, x1, y1)`` bounding box of a polygon."""
    pts = list(polygon)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (
        int(math.floor(min(xs))),
        int(math.floor(min(ys))),
        int(math.ceil(max(xs))),
        int(math.ceil(max(ys))),
    )
