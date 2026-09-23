"""
Vegetation measurement and apical-bud apex detection.

Two things here are deliberately different from V1, and both are structural
fixes rather than tuning:

**1. The vegetation index is not normalised per tile.**
V1 called ``cv2.normalize(..., NORM_MINMAX)`` inside the index computation, so
every tile was stretched onto 0-255 independently.  A fixed threshold of 75
therefore meant a wildly different physical greenness on each tile, and a
crown straddling a tile seam could be above threshold on one side and below it
on the other.  V2 works in *absolute* Excess-Green units
(``ExG = 2G - R - B``, range -255..+510) and resolves the threshold and the
background level **once for the whole region of interest** with Otsu's method,
then reuses those two numbers for every tile.

**2. Apex size is measured, not assumed.**
V1 returned ``max(15, int(min_dist * 0.45))`` for every single palm, so every
detection carried an identical radius unrelated to any tree.  V2 walks a radial
profile of the smoothed vegetation index out from each apex until it falls
half-way back to the background, and reports the resulting **apical rosette
radius** per palm.  It is named for what it measures; see
:data:`palmsentinel.agronomy.ROSETTE_RADIUS_SEARCH_M` for why that is not the
same thing as a crown footprint on a closed canopy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .agronomy import ROSETTE_RADIUS_SEARCH_M, PalmStandard
from .scale import GroundScale

ArrayLike = np.ndarray

#: Radial directions sampled when measuring a crown.
_CROWN_RAYS = 48

#: Which percentile of the ray samples to follow out from the apex.
#: The *mean* collapses almost immediately on a real interlocking canopy because
#: some rays immediately fall into the dark gaps between fronds, which pins every
#: crown to the minimum radius and makes the maturity tiers meaningless.  A high
#: percentile follows the fronds, which is what a crown boundary actually is.
_CROWN_RAY_PERCENTILE = 70.0


class UnknownIndexError(ValueError):
    pass


def vegetation_index(image_bgr: ArrayLike, index: str = "exg") -> ArrayLike:
    """
    Vegetation index as absolute float32 values (no per-image normalisation).

    ``exg``  -- Excess Green ``2G - R - B``        (range -255 .. +510)
    ``gli``  -- Green Leaf Index ``(2G-R-B)/(2G+R+B)`` (range -1 .. +1)
    ``vari`` -- Visible Atmospherically Resistant ``(G-R)/(G+R-B)``
    """
    if image_bgr.ndim != 3 or image_bgr.shape[2] < 3:
        raise ValueError(f"Expected an HxWx3 BGR image, got {image_bgr.shape}")

    b = image_bgr[:, :, 0].astype(np.float32)
    g = image_bgr[:, :, 1].astype(np.float32)
    r = image_bgr[:, :, 2].astype(np.float32)

    if index == "exg":
        return 2.0 * g - r - b
    if index == "gli":
        denom = 2.0 * g + r + b
        np.maximum(denom, 1e-5, out=denom)
        return (2.0 * g - r - b) / denom
    if index == "vari":
        denom = g + r - b
        denom = np.where(np.abs(denom) < 1e-5, 1e-5, denom)
        return (g - r) / denom
    raise UnknownIndexError(f"Unknown vegetation index {index!r}")


def otsu_threshold(values: ArrayLike, bins: int = 256) -> float:
    """Otsu's between-class-variance threshold for arbitrary float data."""
    flat = np.asarray(values).ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size < 16:
        return float(flat.mean()) if flat.size else 0.0

    lo, hi = np.percentile(flat, [1.0, 99.0])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 1e-6:
        return float(lo)

    hist, edges = np.histogram(flat, bins=bins, range=(float(lo), float(hi)))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return float(lo)

    prob = hist / total
    centres = (edges[:-1] + edges[1:]) / 2.0
    omega = np.cumsum(prob)
    mu = np.cumsum(prob * centres)
    mu_total = mu[-1]

    denom = omega * (1.0 - omega)
    denom[denom <= 0] = np.nan
    sigma_b = (mu_total * omega - mu) ** 2 / denom

    best = int(np.nanargmax(sigma_b))
    return float(centres[best])


@dataclass(frozen=True)
class Observation:
    """
    Region-wide measurement context, resolved once and shared by every tile.

    ``threshold``  -- can foliage vs. bare ground / drains / roads
    ``background`` -- the low percentile of the index, i.e. non-canopy level
    """

    index: str
    threshold: float
    background: float
    sensitivity: float = 0.0
    """The fraction actually applied to lower the threshold (0 when none was)."""

    def describe(self) -> str:
        return (
            f"{self.index}: threshold={self.threshold:.1f} "
            f"background={self.background:.1f}"
        )


def observe_region(
    image_bgr: ArrayLike,
    bounds: Tuple[int, int, int, int],
    index: str = "exg",
    max_side: int = 2048,
    background_percentile: float = 20.0,
) -> Observation:
    """
    Resolve the vegetation threshold and background level for one whole region.

    Computed from a single downsampled view of the *entire* region, so every
    tile in the census is judged against the same two numbers.
    """
    x0, y0, x1, y1 = bounds
    crop = image_bgr[y0:y1, x0:x1]
    if crop.size == 0:
        raise ValueError(f"Empty region {bounds}")

    h, w = crop.shape[:2]
    longest = max(h, w)
    if longest > max_side:
        s = max_side / float(longest)
        crop = cv2.resize(
            crop, (max(1, int(round(w * s))), max(1, int(round(h * s)))),
            interpolation=cv2.INTER_AREA,
        )

    veg = vegetation_index(crop, index)
    return Observation(
        index=index,
        threshold=otsu_threshold(veg),
        background=float(np.percentile(veg, background_percentile)),
    )


@dataclass(frozen=True)
class DetectorParams:
    """
    All pixel-space detection parameters, derived once from metres and a GSD.

    Nothing downstream may multiply by a magic pixel number again: this object
    is the only place where agronomy (metres) becomes imagery (pixels).
    """

    blur_ksize: int
    peak_separation_px: int
    min_spacing_px: float
    rosette_radius_min_px: float
    rosette_radius_max_px: float
    halo_px: int
    index: str

    @classmethod
    def from_standard(
        cls,
        standard: PalmStandard,
        scale: GroundScale,
        index: str = "exg",
    ) -> "DetectorParams":
        peak_sep = scale.px_to_int(standard.peak_separation_m, minimum=3)
        rosette_min_m, rosette_max_m = ROSETTE_RADIUS_SEARCH_M
        rosette_r_min = scale.m_to_px(rosette_min_m)
        rosette_r_max = scale.m_to_px(rosette_max_m)

        # The halo must cover the largest rosette we may need to measure plus the
        # peak-isolation radius, so a palm sitting exactly on a tile core edge
        # still sees its whole rosette rather than a clipped one.
        halo = int(math.ceil(rosette_r_max)) + peak_sep + 8

        return cls(
            blur_ksize=scale.px_to_odd(standard.blur_m),
            peak_separation_px=peak_sep,
            min_spacing_px=float(scale.m_to_px(standard.min_spacing_m)),
            rosette_radius_min_px=rosette_r_min,
            rosette_radius_max_px=rosette_r_max,
            halo_px=halo,
            index=index,
        )

    @classmethod
    def from_measured_pitch(
        cls,
        standard: PalmStandard,
        pitch_px: float,
        index: str = "exg",
    ) -> "DetectorParams":
        """Pixel parameters from an image-measured pitch (Problem 5).

        Identical proportions to :meth:`from_standard`, but the ruler is a
        pitch measured from the imagery itself
        (:mod:`palmsentinel.pitch`) instead of metres divided by a typed GSD.
        The integer count this produces cannot depend on the typed GSD; the
        GSD is applied afterwards, for area and density only.  ``pitch_px``
        must be positive and finite; anything else is a caller error.
        """
        if not (pitch_px > 0.0) or not math.isfinite(pitch_px):
            raise ValueError(f"Measured pitch must be positive pixels, got {pitch_px!r}")
        ex = standard.expected_spacing_m
        peak_sep = max(3, int(round(pitch_px * standard.peak_separation_m / ex)))
        rosette_r_min = max(1.0, pitch_px * ROSETTE_RADIUS_SEARCH_M[0] / ex)
        rosette_r_max = max(rosette_r_min, pitch_px * ROSETTE_RADIUS_SEARCH_M[1] / ex)
        blur = max(3, int(round(pitch_px * standard.blur_m / ex)))
        if blur % 2 == 0:
            blur += 1
        halo = int(math.ceil(rosette_r_max)) + peak_sep + 8
        return cls(
            blur_ksize=blur,
            peak_separation_px=peak_sep,
            min_spacing_px=float(standard.min_spacing_fraction * pitch_px),
            rosette_radius_min_px=rosette_r_min,
            rosette_radius_max_px=rosette_r_max,
            halo_px=halo,
            index=index,
        )

    def describe(self) -> str:
        return (
            f"blur={self.blur_ksize}px peak_sep={self.peak_separation_px}px "
            f"min_spacing={self.min_spacing_px:.1f}px "
            f"rosette_r={self.rosette_radius_min_px:.0f}-"
            f"{self.rosette_radius_max_px:.0f}px halo={self.halo_px}px"
        )


@dataclass(frozen=True)
class Candidate:
    """A raw apical-bud apex, in absolute full-resolution pixel coordinates."""

    x: float
    y: float
    peak: float
    manual: bool = False
    """True for a point an operator placed by hand.

    Manual points are candidates like any other: they pass through the same
    region, the same spacing rule and the same coordinates as a detected apex, so
    a corrected count is still produced by one implementation.  They only take
    precedence when two points contend for the same piece of ground, because an
    operator's assertion about a gap they can see outranks a local maximum that
    may be frond structure.
    """


class CanopyDetector:
    """Finds apical-bud apexes and measures crown radius around them."""

    def __init__(self, params: DetectorParams, observation: Observation):
        self.params = params
        self.observation = observation
        self._se = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (params.peak_separation_px * 2 + 1, params.peak_separation_px * 2 + 1),
        )

    # -- candidate generation ------------------------------------------------

    def smooth(self, image_bgr: ArrayLike) -> ArrayLike:
        veg = vegetation_index(image_bgr, self.params.index)
        return cv2.GaussianBlur(
            veg, (self.params.blur_ksize, self.params.blur_ksize), 0
        )

    def find_apexes(
        self,
        patch_bgr: ArrayLike,
        offset_xy: Tuple[int, int] = (0, 0),
        inside_mask: Optional[ArrayLike] = None,
    ) -> List[Candidate]:
        """
        Local maxima of the smoothed index that clear the region threshold.

        A pixel is an apex when it equals its own morphological dilation (i.e.
        nothing nearby is greener) and is at least as green as the threshold.
        """
        p = self.params
        smoothed = self.smooth(patch_bgr)
        dilated = cv2.dilate(smoothed, self._se)

        candidate_mask = (smoothed >= dilated) & (smoothed >= self.observation.threshold)
        if inside_mask is not None:
            candidate_mask &= inside_mask > 0

        ys, xs = np.nonzero(candidate_mask)
        if xs.size == 0:
            return []

        ox, oy = offset_xy
        peaks = smoothed[ys, xs]
        return [
            Candidate(float(x + ox), float(y + oy), float(pk))
            for x, y, pk in zip(xs, ys, peaks)
        ]

    # -- crown measurement ---------------------------------------------------

    def _radii(self) -> ArrayLike:
        """Candidate rosette radii in pixels, shared by the ray grid and lookup."""
        p = self.params
        radii = np.arange(p.rosette_radius_min_px, p.rosette_radius_max_px + 1.0, 2.0)
        if radii.size == 0:
            radii = np.array([p.rosette_radius_min_px], dtype=np.float64)
        return radii

    def _ray_offsets(self) -> Tuple[ArrayLike, ArrayLike]:
        radii = self._radii()
        angles = np.linspace(0.0, 2.0 * math.pi, _CROWN_RAYS, endpoint=False)
        dx = (np.cos(angles)[:, None] * radii[None, :]).astype(np.float32)
        dy = (np.sin(angles)[:, None] * radii[None, :]).astype(np.float32)
        return dx, dy

    def rosette_radius_px(self, image_bgr: ArrayLike, x: float, y: float) -> float:
        """
        Radius at which greenness along 48 rays has fallen half-way from the
        apex value back to the region background level.

        This is the apical rosette, not the crown footprint -- see
        :data:`palmsentinel.agronomy.ROSETTE_RADIUS_SEARCH_M`.
        """
        p = self.params
        half = int(math.ceil(p.rosette_radius_max_px)) + p.blur_ksize
        x0 = int(math.floor(x)) - half
        y0 = int(math.floor(y)) - half
        x1 = int(math.ceil(x)) + half
        y1 = int(math.ceil(y)) + half

        h, w = image_bgr.shape[:2]
        rx0, ry0 = max(0, x0), max(0, y0)
        rx1, ry1 = min(w, x1), min(h, y1)
        if rx1 - rx0 < 8 or ry1 - ry0 < 8:
            return float(p.rosette_radius_min_px)

        # Pad so that the apex always sits at the same place in the window and
        # edge palms still get a full set of rays.
        window_h, window_w = y1 - y0, x1 - x0
        padded = np.zeros((window_h, window_w, 3), dtype=image_bgr.dtype)
        padded[ry0 - y0:ry1 - y0, rx0 - x0:rx1 - x0] = image_bgr[ry0:ry1, rx0:rx1]

        smoothed = self.smooth(padded)
        cx = int(round(x)) - x0
        cy = int(round(y)) - y0
        if not (0 <= cx < window_w and 0 <= cy < window_h):
            return float(p.rosette_radius_min_px)

        apex_value = float(smoothed[cy, cx])
        background = self.observation.background
        target = background + (apex_value - background) * 0.5
        if apex_value <= target:
            return float(p.rosette_radius_min_px)

        dx, dy = self._ray_offsets()
        ix = np.clip(np.round(cx + dx).astype(np.int32), 0, window_w - 1)
        iy = np.clip(np.round(cy + dy).astype(np.int32), 0, window_h - 1)
        profile = np.percentile(smoothed[iy, ix], _CROWN_RAY_PERCENTILE, axis=0)

        radii = self._radii()
        if radii.size == 0:
            return float(p.rosette_radius_min_px)

        # Cross where the *trailing mean* falls below target, not a single ring,
        # so one dark drain does not cut a crown short.  Averaging a short
        # trailing window rather than the whole remaining profile keeps the
        # crossing at the crown edge instead of pulling it inward.
        window = 3
        if profile.size >= window:
            kernel = np.ones(window, dtype=np.float64) / float(window)
            trailing = np.convolve(profile, kernel, mode="valid")
        else:
            trailing = profile
        below = np.nonzero(trailing < target)[0]
        if below.size == 0:
            return float(p.rosette_radius_max_px)
        return float(radii[int(min(below[0], radii.size - 1))])
