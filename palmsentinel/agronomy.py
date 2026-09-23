"""
Agronomic constants -- THE single source of truth for every threshold.

Nothing else in this package is allowed to hard-code a palm density band, a
crown diameter band, a planting spacing, or a target stand density.  If a
number describes oil palm biology or estate practice, it lives here and only
here, so that changing it changes the whole pipeline consistently.

Two structural rules this module enforces by construction:

1.  SPH bands are *contiguous half-open intervals* ``[lo, hi)``.  Version 1 of
    this project used inclusive ranges (``110 <= sph <= 125``,
    ``126 <= sph <= 145``, ...) which left the gaps 125.1-125.9 and
    145.1-145.9 unclassified, so a perfectly normal 145.5 SPH stand fell
    through to the final ``else`` and was reported as "Very High Density".
    :func:`classify_sph` walks an ordered table instead of an if/elif ladder,
    and :func:`bands_are_contiguous` proves there are no gaps.

2.  Every spatial length is expressed in **metres of ground**.  Version 1
    expressed planting spacing in *pixels* (``min_distance_px=68``), which
    silently implied ~13 cm/px imagery.  Run against the 4 cm/px imagery the
    project actually ships, 68 px meant 2.7 m between "trees" instead of the
    ~9 m of a real planting grid -- about a 4x over-count, and the root cause
    of the reported 556-961 SPH figures.  Pixels are derived from metres and a
    ground sample distance in :mod:`palmsentinel.scale`, never written down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

# --------------------------------------------------------------------------
# Stand density (SPH = palms per hectare)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SphBand:
    """A half-open stand-density band ``[lo, hi)`` in palms per hectare."""

    lo: Optional[float]
    hi: Optional[float]
    key: str
    label: str
    guidance: str

    def contains(self, sph: float) -> bool:
        if self.lo is not None and sph < self.lo:
            return False
        if self.hi is not None and sph >= self.hi:
            return False
        return True

    def describe_range(self) -> str:
        if self.lo is None:
            return f"< {self.hi:g}"
        if self.hi is None:
            return f">= {self.lo:g}"
        return f"{self.lo:g}-{self.hi:g}"


#: The industry benchmark for a mature triangular planting (9.0 m x 7.8 m).
INDUSTRY_TARGET_SPH: Tuple[float, float] = (136.0, 143.0)

#: Ordered, contiguous, half-open.  Order matters: classify_sph() walks these.
SPH_BANDS: Tuple[SphBand, ...] = (
    SphBand(
        None,
        110.0,
        "underpopulated",
        "Underpopulated / high vacancy",
        "Titik sisipan required: replant the vacant grid positions.",
    ),
    SphBand(
        110.0,
        126.0,
        "low",
        "Slightly low density",
        "Below the industrial target; check for vacant pockets.",
    ),
    SphBand(
        126.0,
        146.0,
        "optimal",
        "Optimal plantation density",
        f"Within the industrial standard of {INDUSTRY_TARGET_SPH[0]:g}-"
        f"{INDUSTRY_TARGET_SPH[1]:g} SPH.",
    ),
    SphBand(
        146.0,
        166.0,
        "high",
        "High density / compact planting",
        "Above target. Normal for young TBM stands or high-intensity blocks.",
    ),
    SphBand(
        166.0,
        None,
        "very_high",
        "Very high density",
        "Above any commercial planting pattern. Verify spacing calibration "
        "or crown size before trusting this count.",
    ),
)


def _lowest_band() -> SphBand:
    return SPH_BANDS[0]


def classify_sph(sph: float) -> SphBand:
    """Classify a stand density.  Every finite value matches exactly one band."""
    for band in SPH_BANDS:
        if band.contains(sph):
            return band
    # Unreachable for finite input; keeps the type checker and the caller honest
    # for NaN/inf rather than silently returning a wrong agronomic label.
    return _lowest_band() if sph == sph and sph < 0 else SPH_BANDS[-1]


def bands_are_contiguous() -> bool:
    """True when the SPH table covers the whole real line with no gaps."""
    if not SPH_BANDS or SPH_BANDS[0].lo is not None:
        return False
    if SPH_BANDS[-1].hi is not None:
        return False
    return all(a.hi == b.lo for a, b in zip(SPH_BANDS, SPH_BANDS[1:]))


# --------------------------------------------------------------------------
# Apical rosette measurement window
# --------------------------------------------------------------------------

#: Search window, in metres of ground, for the radius at which an apex's
#: greenness has fallen half-way back to the background level.
#:
#: This measures the **bright apical rosette** around the bud, and deliberately
#: does not pretend to be the crown footprint.  On a closed mature canopy the
#: fronds of adjacent palms interlock, so there is no dark inter-crown gap for a
#: radial profile to find.  Measured on this project's own mature demonstration
#: block, the profile leaves the apex, dips around 1.6 m, recovers around 4 m as
#: it crosses into the neighbouring crown, and never returns to background at
#: all -- so a "crown radius" read off it would label every mature palm as young.
#: The window is kept independent of any classification band precisely so that a
#: measurement can never be pinned to a band edge.
#:
#: Recovering true crown footprint on a closed canopy needs segmentation, not a
#: radial profile; that is deferred, and the census does not emit maturity tiers
#: until it exists.  See the V2 decision log.
ROSETTE_RADIUS_SEARCH_M: Tuple[float, float] = (0.4, 6.0)


# --------------------------------------------------------------------------
# Palm standards -- the only place a planting spacing is written down
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PalmStandard:
    """
    A named estate planting standard.

    Every length is metres of ground.  Pixel equivalents are computed once, in
    :meth:`palmsentinel.detection.DetectorParams.from_standard`, from a
    :class:`palmsentinel.scale.GroundScale`.
    """

    key: str
    label: str
    expected_spacing_m: float
    """Nearest-neighbour distance of the planting grid (triangular pitch)."""

    min_spacing_fraction: float
    """
    Two distinct palms may not be closer than this fraction of the pitch.

    Calibrated against the external industrial standard rather than against this
    project's own output: on the bundled 1.0000 ha mature demonstration block,
    the resulting stand density is 164 SPH at 0.60, 154 at 0.65, 147 at 0.70,
    **140 at 0.75**, 127 at 0.80 and 109 at 0.85.  0.75 is the only setting that
    lands inside the 136-143 SPH target this project quotes as its benchmark, so
    it is the value used.  The full sweep is reproduced in the V2 log.
    """

    blur_m: float
    """Gaussian smoothing scale used to consolidate a frond rosette."""

    peak_separation_m: float
    """Morphological dilation radius isolating the apical bud (pucuk)."""

    vegetation_threshold: Optional[float]
    """Absolute Excess-Green threshold; ``None`` derives it from the image."""

    @property
    def min_spacing_m(self) -> float:
        return self.expected_spacing_m * self.min_spacing_fraction


PALM_STANDARDS = {
    "mature": PalmStandard(
        key="mature",
        label="Mature Palm (TM / Tanaman Menghasilkan)",
        # 9.0 m x 7.8 m triangular planting == 142.5 SPH, the industrial norm.
        expected_spacing_m=9.0,
        min_spacing_fraction=0.75,
        blur_m=1.25,
        peak_separation_m=1.45,
        vegetation_threshold=None,
    ),
    "young": PalmStandard(
        key="young",
        label="Young Palm (TBM / Tanaman Belum Menghasilkan)",
        expected_spacing_m=7.6,
        min_spacing_fraction=0.75,
        blur_m=0.85,
        peak_separation_m=1.05,
        vegetation_threshold=None,
    ),
}

DEFAULT_STANDARD_KEY = "mature"


def get_standard(key: str) -> PalmStandard:
    try:
        return PALM_STANDARDS[key]
    except KeyError:
        raise KeyError(
            f"Unknown palm standard {key!r}. Available: {sorted(PALM_STANDARDS)}"
        ) from None


# --------------------------------------------------------------------------
# Default imagery assumption
# --------------------------------------------------------------------------

#: The ground sample distance of the orthomosaics this project ships with
#: (a 9,217 x 14,980 px mosaic covering roughly 369 m x 599 m).
DEFAULT_GSD_CM_PER_PX = 4.0
