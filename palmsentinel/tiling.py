"""
Core-ownership tiling: the structural fix for tile-boundary duplicates.

V1 cut the region into *overlapping* tiles, ran non-maximum suppression
**inside each tile independently**, then stitched the results with a second
dedup pass using a *smaller* radius (``min_dist * 0.8``) than the per-tile NMS.
Two apexes on either side of a seam could therefore both survive: each was
invisible to the other's tile, and the cross-tile radius was too small to
reject them.  Repeat that over a 2048 px grid and a 64 MP block gains hundreds
of phantom palms, which is exactly what inflated the V1 census to 556 SPH.

V2 instead assigns every pixel of the region to **exactly one** tile.  Each
tile is *read* with a halo of context (so crowns near a seam are still measured
from their full rosette) but each tile only *publishes* candidates that fall in
its own core rectangle.  A seam apex belongs to one core and one core only, so
it cannot be produced twice -- duplicates are impossible by construction rather
than removed by a heuristic afterwards.

:func:`plan_tiles` guarantees the cores partition the region exactly;
:func:`assert_exact_cover` proves it and is exercised by the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

Box = Tuple[int, int, int, int]  # x0, y0, x1, y1 (half-open)


@dataclass(frozen=True)
class Tile:
    """One work unit: a padded read window and the core rectangle it owns."""

    read: Box
    core: Box
    index: int

    @property
    def core_area(self) -> int:
        x0, y0, x1, y1 = self.core
        return max(0, x1 - x0) * max(0, y1 - y0)


def _axis_splits(start: int, end: int, step: int) -> List[Tuple[int, int]]:
    """Partition ``[start, end)`` into consecutive ``(core_start, core_end)``."""
    if end <= start:
        return []
    spans: List[Tuple[int, int]] = []
    s = start
    while s < end:
        e = min(s + step, end)
        spans.append((s, e))
        s = e
    return spans


def plan_tiles(
    region: Box,
    tile_px: int,
    halo_px: int,
    image_bounds: Box,
) -> List[Tile]:
    """
    Tile plan with exact core ownership.

    ``region``        -- the ROI to census (``x0, y0, x1, y1``, half-open)
    ``tile_px``       -- the core size; each core is at most this large
    ``halo_px``       -- context added around each core when reading pixels
    ``image_bounds``  -- ``(0, 0, width, height)``; halos are clipped to it
    """
    if tile_px <= 0:
        raise ValueError("tile_px must be positive")
    if halo_px < 0:
        raise ValueError("halo_px must not be negative")

    rx0, ry0, rx1, ry1 = region
    ix0, iy0, ix1, iy1 = image_bounds
    rx0, ry0 = max(rx0, ix0), max(ry0, iy0)
    rx1, ry1 = min(rx1, ix1), min(ry1, iy1)

    x_spans = _axis_splits(rx0, rx1, tile_px)
    y_spans = _axis_splits(ry0, ry1, tile_px)

    tiles: List[Tile] = []
    for cy0, cy1 in y_spans:
        for cx0, cx1 in x_spans:
            read = (
                max(ix0, cx0 - halo_px),
                max(iy0, cy0 - halo_px),
                min(ix1, cx1 + halo_px),
                min(iy1, cy1 + halo_px),
            )
            tiles.append(Tile(read=read, core=(cx0, cy0, cx1, cy1), index=len(tiles)))
    return tiles


def assert_exact_cover(region: Box, tiles: List[Tile]) -> None:
    """Raise unless the tile cores partition ``region`` exactly once."""
    if not tiles:
        raise AssertionError("No tiles planned for a non-empty region")

    # Cores must tile the region as a rectangle without overlap.  Because the
    # plan is a full cartesian product of splits, checking spans is sufficient
    # and O(n) rather than a per-pixel scan.
    x_bounds = sorted({t.core[0] for t in tiles} | {t.core[2] for t in tiles})
    y_bounds = sorted({t.core[1] for t in tiles} | {t.core[3] for t in tiles})

    rx0, ry0, rx1, ry1 = region
    if x_bounds[0] != rx0 or x_bounds[-1] != rx1:
        raise AssertionError(f"X cores do not span region {region}: {x_bounds}")
    if y_bounds[0] != ry0 or y_bounds[-1] != ry1:
        raise AssertionError(f"Y cores do not span region {region}: {y_bounds}")

    covered = sum(t.core_area for t in tiles)
    expected = (rx1 - rx0) * (ry1 - ry0)
    if covered != expected:
        raise AssertionError(
            f"Cores cover {covered:,} px but region is {expected:,} px "
            f"(overlap or gap)"
        )


def core_contains(tile: Tile, x: float, y: float) -> bool:
    """Half-open containment test -- the single ownership decision."""
    cx0, cy0, cx1, cy1 = tile.core
    return cx0 <= x < cx1 and cy0 <= y < cy1
