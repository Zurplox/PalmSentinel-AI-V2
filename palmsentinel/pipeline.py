"""
The census pipeline.

Order of operations, and why:

1.  Resolve region-wide measurement context (vegetation threshold + background)
    from one downsampled view.  Every tile then shares those two numbers.
2.  Plan tiles whose cores partition the region exactly (see
    :mod:`palmsentinel.tiling`).
3.  Per tile: read with halo, mask to the polygon, find apexes, then publish
    **only** those inside the tile's core.  Each pixel is owned by one tile, so
    a seam apex is emitted exactly once and no cross-tile dedup heuristic is
    needed or wanted.
4.  Enforce the agronomic minimum spacing once, globally, on full-resolution
    coordinates.  This is a planting-physics constraint (two palms cannot be
    closer than ``min_spacing_fraction`` x pitch), not a duplicate remover --
    the duplicates are already gone by step 3.
5.  Measure each surviving palm's crown radius from a local window.
6.  Area from the survey polygon (Shoelace, in pixels, converted through the
    single :class:`~palmsentinel.scale.GroundScale`), then SPH, then the band
    from the single :data:`~palmsentinel.agronomy.SPH_BANDS` table.
7.  Verify the post-conditions and report them instead of asking anyone to
    trust the count.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .agronomy import PalmStandard, SphBand, classify_sph

from .detection import (
    ArrayLike,
    Candidate,
    CanopyDetector,
    DetectorParams,
    Observation,
    observe_region,
)
from .scale import GroundScale, ImageFrame, bounds_of, shoelace_area_px
from .tiling import Tile, assert_exact_cover, core_contains, plan_tiles

Point = Tuple[float, float]


class EmptyRegionError(ValueError):
    """Raised when a region contains no analyzable pixels."""


@dataclass(frozen=True)
class Palm:
    """
    One detected palm, in full-resolution pixels and in ground metres.

    ``rosette_radius_*`` describes the bright apical rosette around the bud, as
    measured; it is not a crown footprint and is not classified into maturity
    tiers -- see :mod:`palmsentinel.agronomy`.
    """

    palm_id: int
    x_px: float
    y_px: float
    x_m: float
    y_m: float
    peak_exg: float
    rosette_radius_px: float
    rosette_radius_m: float
    manual: bool = False
    """True when an operator placed this palm rather than the detector finding it."""


@dataclass(frozen=True)
class CensusConfig:
    """Everything a census needs.  No pixel numbers, only metres and a GSD."""

    standard: PalmStandard
    scale: GroundScale
    polygon: Optional[Tuple[Point, ...]] = None
    tile_px: int = 1536
    index: str = "exg"
    vegetation_threshold: Optional[float] = None
    sensitivity: float = 0.0
    """0..1 fraction that lowers the Otsu vegetation threshold.

    0 is plain Otsu. 1.0 would place the threshold at the vegetation mean,
    which is far too aggressive; useful values sit in 0..0.5.
    """
    manual_additions: Tuple[Point, ...] = ()
    """Full-resolution points at which an operator asserts a palm exists.

    A census of a real block is always verified in the field, so the tool has to
    accept a correction.  Additions obey the same planting-physics spacing rule
    as a detection, so the post-condition still holds over the final palm set.
    """

    manual_removals: Tuple[Point, ...] = ()
    """Full-resolution points at which an operator says a detection is spurious.

    The nearest accepted palm within one minimum-spacing radius of each point is
    dropped, which is the palm an operator is pointing at.
    """

    min_spacing_px: Optional[float] = None
    """Diagnostic override, in pixels.  Used only to reproduce legacy behaviour
    in the comparison harness; a normal census leaves this ``None`` so spacing
    comes from metres."""

    def resolved_min_spacing_px(self, params: DetectorParams) -> float:
        return (
            float(self.min_spacing_px)
            if self.min_spacing_px is not None
            else params.min_spacing_px
        )


@dataclass
class CensusResult:
    total_palms: int
    area_ha: float
    sph: float
    sph_band: SphBand
    palms: List[Palm]
    frame: ImageFrame
    params: DetectorParams
    observation: Observation
    min_spacing_px: float
    diagnostics: Dict[str, object] = field(default_factory=dict)

    def spacing_check(self) -> Optional[float]:
        return self.diagnostics.get("min_pairwise_distance_px")  # type: ignore[return-value]

    def summary_lines(self) -> List[str]:
        d = self.diagnostics
        x0, y0, x1, y1 = d.get("region", (0, 0, 0, 0))  # type: ignore[misc]
        lines = [
            f"frame            : {self.frame.describe()}",
            f"region           : x{x0}-{x1}, y{y0}-{y1}  "
            f"({self.area_ha:,.4f} ha)",
            f"detector         : {self.params.describe()}",
            f"observation      : {self.observation.describe()}",
            f"min spacing      : {self.min_spacing_px:.1f} px "
            f"({self.params.index} derived from metres)",
            f"tiles            : {d.get('tiles')} planned, "
            f"{d.get('tiles_with_candidates')} with candidates",        f"apex candidates  : {d.get('candidates_raw'):,} raw",
        f"  suppressed     : {d.get('candidates_suppressed_by_spacing'):,} "
        f"(closer than min spacing to a stronger apex)",
        f"palms kept       : {self.total_palms:,}",
        f"  by operator    : {d.get('detections_removed_by_operator', 0):,} detections "
        f"deleted, {d.get('manual_additions', 0):,} points added, "
        f"{d.get('manual_additions_rejected', 0):,} additions rejected by spacing",
            f"area             : {self.area_ha:,.4f} ha",
            f"stand density    : {self.sph:,.1f} SPH  ->  {self.sph_band.label}",
            f"apical rosette   : {d.get('rosette_radius_median_m', 0):.2f} m radius "
            f"median, {d.get('rosette_radius_min_m', 0):.2f}-"
            f"{d.get('rosette_radius_max_m', 0):.2f} m range",
        ]
        if "nn_median_m" in d:
            lines.append(
                f"palm spacing     : nearest neighbour median "
                f"{d.get('nn_median_m', 0):.2f} m (mode {d.get('nn_mode_m', 0):.2f} m), "
                f"10-90% {d.get('nn_p10_m', 0):.2f}-{d.get('nn_p90_m', 0):.2f} m"
            )
        lines.append(
            f"spacing check    : closest accepted pair "
            f"{d.get('min_pairwise_distance_px', float('nan')):.1f} px "
            f"(required >= {self.min_spacing_px:.1f})"
        )
        lines.append(f"elapsed          : {d.get('elapsed_s')} s")
        return lines


# --------------------------------------------------------------------------
# Spacing enforcement
# --------------------------------------------------------------------------


def enforce_min_spacing(
    candidates: Sequence[Candidate], min_spacing_px: float
) -> List[Candidate]:
    """
    Greedy non-maximum suppression on full-resolution global coordinates.

    Strongest apex wins; anything inside its exclusion radius is dropped.  A
    detection outranks an operator's manual addition, so an addition can only
    fill ground the detector left free -- it never displaces a detection, since
    two palms 1.4 m apart is never the right answer.  To *move* a palm the
    operator deletes the detection first, which frees the ground and lets the
    addition be accepted (see run_census step 3a).  A uniform grid index keeps
    this near-linear, and because the grid is built on *global* coordinates the
    result does not depend on how the region was tiled.
    """
    if min_spacing_px <= 0:
        return list(candidates)
    if len(candidates) < 2:
        return list(candidates)

    cell = float(min_spacing_px)
    radius_sq = min_spacing_px ** 2
    order = sorted(
        range(len(candidates)),
        key=lambda i: (1 if candidates[i].manual else 0, -candidates[i].peak),
    )

    grid: Dict[Tuple[int, int], List[Tuple[float, float]]] = {}
    accepted: List[Candidate] = []

    for i in order:
        c = candidates[i]
        gx = int(c.x // cell)
        gy = int(c.y // cell)
        rejected = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                bucket = grid.get((gx + dx, gy + dy))
                if not bucket:
                    continue
                for ax, ay in bucket:
                    if (c.x - ax) ** 2 + (c.y - ay) ** 2 < radius_sq:
                        rejected = True
                        break
                if rejected:
                    break
            if rejected:
                break
        if not rejected:
            accepted.append(c)
            grid.setdefault((gx, gy), []).append((c.x, c.y))

    return accepted


def nearest_neighbour_distances(points: Sequence[Tuple[float, float]]) -> ArrayLike:
    """
    Distance from every point to its closest neighbour, exactly.

    This is the measurement that tells you whether a detector is resolving
    *palms* or *frond clusters*: a census of a real mature stand shows
    nearest-neighbour distances clustered on the planting pitch, whereas an
    over-detecting census clusters on whatever its own suppression radius was.

    Sorted by x, every unordered pair is visited once, and the inner loop stops
    as soon as the x-gap alone exceeds the best distance already found for the
    outer point.  On a plantation that terminates after a couple of neighbours,
    so this is effectively linear; it is exact in all cases.
    """
    n = len(points)
    if n < 2:
        return np.empty(0, dtype=np.float64)

    ordered = sorted((float(x), float(y)) for x, y in points)
    best_sq = np.full(n, np.inf, dtype=np.float64)

    for i in range(n - 1):
        xi, yi = ordered[i]
        for j in range(i + 1, n):
            xj, yj = ordered[j]
            dx = xj - xi
            if dx * dx >= best_sq[i]:
                break
            d_sq = dx * dx + (yj - yi) ** 2
            if d_sq < best_sq[i]:
                best_sq[i] = d_sq
            if d_sq < best_sq[j]:
                best_sq[j] = d_sq

    return np.sort(np.sqrt(best_sq[np.isfinite(best_sq)]))


def nearest_neighbour_summary(points: Sequence[Tuple[float, float]],
                              m_per_px: float,
                              bin_m: float = 0.5) -> Dict[str, float]:
    """Median / spread / modal nearest-neighbour spacing, in metres."""
    distances = nearest_neighbour_distances(points)
    if distances.size == 0:
        return {}
    metres = distances * m_per_px
    strip = max(bin_m, 1e-6)
    counts, edges = np.histogram(metres, bins=max(4, int((metres.max() - metres.min()) / strip) + 1))
    modal = float((edges[counts.argmax()] + edges[counts.argmax() + 1]) / 2.0)
    return {
        "nn_median_m": float(np.median(metres)),
        "nn_p10_m": float(np.percentile(metres, 10)),
        "nn_p90_m": float(np.percentile(metres, 90)),
        "nn_mode_m": modal,
    }


def minimum_pairwise_distance(points: Sequence[Tuple[float, float]]) -> float:
    """Exact smallest distance between any two points.

    Sort by x, then sweep with an early break once the x-gap alone already
    exceeds the best distance found so far.  Exact (no spatial-index
    approximation to get subtly wrong), and fast on a census-sized point set.
    """
    if len(points) < 2:
        return float("inf")

    ordered = sorted((float(x), float(y)) for x, y in points)
    best_sq = float("inf")
    for i, (xi, yi) in enumerate(ordered):
        for j in range(i + 1, len(ordered)):
            xj, yj = ordered[j]
            dx = xj - xi
            if dx * dx >= best_sq:
                break
            dy = yj - yi
            d_sq = dx * dx + dy * dy
            if d_sq < best_sq:
                best_sq = d_sq
    return math.sqrt(best_sq) if best_sq != float("inf") else float("inf")


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------


def _polygon_mask(read_box: Tuple[int, int, int, int], polygon) -> ArrayLike:
    x0, y0, x1, y1 = read_box
    mask = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
    local = np.array(
        [[[int(round(px - x0)), int(round(py - y0))] for px, py in polygon]],
        dtype=np.int32,
    )
    cv2.fillPoly(mask, local, 255)
    return mask


def run_census(image_bgr: ArrayLike, config: CensusConfig) -> CensusResult:
    """Run one census over one orthomosaic (in memory) and verify the result."""
    started = time.perf_counter()

    h_img, w_img = image_bgr.shape[:2]
    frame = ImageFrame(width=w_img, height=h_img, scale=config.scale)
    image_bounds = (0, 0, w_img, h_img)

    polygon = tuple(config.polygon) if config.polygon else None
    if polygon is not None and len(polygon) < 3:
        raise ValueError("A survey polygon needs at least 3 vertices")

    if polygon is not None:
        bx0, by0, bx1, by1 = bounds_of(polygon)
        region = (
            max(0, bx0), max(0, by0), min(w_img, bx1), min(h_img, by1),
        )
    else:
        region = image_bounds

    if region[2] - region[0] < 8 or region[3] - region[1] < 8:
        raise EmptyRegionError(f"Region too small to census: {region}")

    # 1. one region-wide measurement context
    observation = observe_region(image_bgr, region, index=config.index)
    if config.vegetation_threshold is not None:
        observation = Observation(
            index=config.index,
            threshold=float(config.vegetation_threshold),
            background=observation.background,
        )
    elif config.sensitivity > 0:
        # Sensitivity slides the Otsu threshold down so weaker crowns -- young
        # palms, drought-stressed crowns, hazy captures -- still read as
        # vegetation. The setting is a fraction of the interval between the
        # Otsu threshold and the region's background level (its non-canopy
        # percentile, which sits *below* the threshold): 0 is exactly Otsu,
        # 1.0 would drop the bar onto the background mode itself, and useful
        # values sit in 0..0.5. Resolved once, here, for the whole region --
        # the same discipline as Otsu itself.
        span = observation.threshold - observation.background
        lowered = observation.threshold - config.sensitivity * span
        observation = Observation(
            index=config.index,
            threshold=float(max(1.0, lowered)),
            background=observation.background,
            sensitivity=float(config.sensitivity),
        )

    params = DetectorParams.from_standard(config.standard, config.scale, config.index)
    min_spacing_px = config.resolved_min_spacing_px(params)
    detector = CanopyDetector(params, observation)

    # 2. exact-cover tile plan
    tiles: List[Tile] = plan_tiles(region, config.tile_px, params.halo_px, image_bounds)
    assert_exact_cover(region, tiles)

    # 3. core-owned candidate generation
    detected: List[Candidate] = []
    tiles_with_candidates = 0
    for tile in tiles:
        rx0, ry0, rx1, ry1 = tile.read
        patch = image_bgr[ry0:ry1, rx0:rx1]
        if patch.size == 0:
            continue

        mask = _polygon_mask(tile.read, polygon) if polygon else None
        apexes = detector.find_apexes(patch, offset_xy=(rx0, ry0), inside_mask=mask)
        owned = [a for a in apexes if core_contains(tile, a.x, a.y)]
        if owned:
            tiles_with_candidates += 1
        detected.extend(owned)

    detected_count = len(detected)

    # 3a. Operator deletions, applied to *detections* before spacing.
    #
    # Order matters and is the whole reason corrections compose: deleting before
    # spacing means "move this palm" -- delete where it is, add where it should
    # be -- leaves the destination ground free, while adding next to a palm that
    # is still recorded is rejected rather than silently displacing it.
    if config.manual_removals:
        removal_radius_sq = min_spacing_px ** 2
        detected = [
            c
            for c in detected
            if not any(
                (c.x - mx) ** 2 + (c.y - my) ** 2 <= removal_radius_sq
                for mx, my in config.manual_removals
            )
        ]
    removed_by_operator = detected_count - len(detected)

    # 3b. Operator additions, expressed as candidates so that everything
    # downstream -- spacing, ROI test, area, density, export -- treats them
    # identically to a detection.
    manual_added = [
        Candidate(float(x), float(y), 0.0, manual=True)
        for x, y in config.manual_additions
        if 0 <= x < w_img and 0 <= y < h_img
    ]

    # 4. global agronomic spacing enforcement
    candidates = detected + manual_added
    kept = enforce_min_spacing(candidates, min_spacing_px)

    # 4b. authoritative ROI test.  The rasterised per-tile mask is only an
    # optimisation: cv2.fillPoly rounds vertices to integers, so on a slanted
    # boundary a point can sit inside the raster but outside the survey polygon.
    # The exact geometric test is what decides membership.
    candidates_outside_polygon = 0
    if polygon is not None:
        poly_np = np.array(polygon, dtype=np.float32)
        inside: List[Candidate] = []
        for candidate in kept:
            if cv2.pointPolygonTest(poly_np, (candidate.x, candidate.y), False) >= 0:
                inside.append(candidate)
            else:
                candidates_outside_polygon += 1
        kept = inside

    # 5. crown measurement + packaging
    palms: List[Palm] = []
    rosette_radii_m: List[float] = []

    for i, c in enumerate(sorted(kept, key=lambda a: (a.y, a.x)), start=1):
        radius_px = detector.rosette_radius_px(image_bgr, c.x, c.y)
        radius_m = config.scale.px_to_m(radius_px)
        rosette_radii_m.append(radius_m)
        palms.append(
            Palm(
                palm_id=i,
                x_px=c.x,
                y_px=c.y,
                x_m=config.scale.px_to_m(c.x),
                y_m=config.scale.px_to_m(c.y),
                peak_exg=c.peak,
                rosette_radius_px=radius_px,
                rosette_radius_m=radius_m,
                manual=c.manual,
            )
        )

    # 6. area and density, from the one scale object and the one band table
    if polygon is not None:
        area_ha = config.scale.px_area_to_ha(shoelace_area_px(polygon))
    else:
        area_ha = frame.full_area_ha

    sph = (len(palms) / area_ha) if area_ha > 0 else 0.0
    band = classify_sph(sph)

    # 7. post-conditions (reported, not assumed)
    points = [(p.x_px, p.y_px) for p in palms]
    min_pair = minimum_pairwise_distance(points)
    nn = nearest_neighbour_summary(points, config.scale.m_per_px)
    outside = 0
    if polygon is not None:
        poly_np = np.array(polygon, dtype=np.float32)
        for p in palms:
            if cv2.pointPolygonTest(poly_np, (p.x_px, p.y_px), False) < 0:
                outside += 1

    elapsed = round(time.perf_counter() - started, 3)

    diagnostics: Dict[str, object] = {
        "region": region,
        "polygon_used": polygon is not None,
        "tiles": len(tiles),
        "tiles_with_candidates": tiles_with_candidates,
        "tile_px": config.tile_px,
        "halo_px": params.halo_px,
        "candidates_raw": detected_count,
        "candidates_after_spacing": len(kept),
        "candidates_suppressed_by_spacing": (
            len(candidates) - len(kept) - candidates_outside_polygon
        ),
        "candidates_outside_polygon": candidates_outside_polygon,
        "detections_removed_by_operator": removed_by_operator,
        "manual_additions": sum(1 for p in kept if p.manual),
        "manual_additions_rejected": sum(
            1 for c in manual_added if not any(c is k for k in kept)
        ),
        "min_spacing_m": round(config.scale.px_to_m(min_spacing_px), 4),
        "min_pairwise_distance_px": min_pair,
        "spacing_invariant_ok": bool(
            len(palms) < 2 or min_pair >= min_spacing_px - 1e-6
        ),
        **{k: round(v, 3) for k, v in nn.items()},
        "palms_outside_polygon": outside,
        "rosette_radius_median_m": (
            round(float(np.median(rosette_radii_m)), 3) if rosette_radii_m else 0.0
        ),
        "rosette_radius_min_m": (
            round(float(np.min(rosette_radii_m)), 3) if rosette_radii_m else 0.0
        ),
        "rosette_radius_max_m": (
            round(float(np.max(rosette_radii_m)), 3) if rosette_radii_m else 0.0
        ),
        "elapsed_s": elapsed,
    }

    return CensusResult(
        total_palms=len(palms),
        area_ha=area_ha,
        sph=sph,
        sph_band=band,
        palms=palms,
        frame=frame,
        params=params,
        observation=observation,
        min_spacing_px=min_spacing_px,
        diagnostics=diagnostics,
    )
