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

    The exclusion radius is ``fraction x expected_spacing_m``: 6.75 m at the
    mature standard, 5.70 m at the young one.  It has to sit between two physical
    limits, both derivable from the standards below rather than from this
    project's imagery:

    * **Above the palm's own apex structure.**  A mature crown presents several
      bright frond apexes within metres of the bud, and a detector that keeps the
      strongest and discards the rest needs the radius to cover them.
      :data:`ROSETTE_RADIUS_SEARCH_M` bounds that structure at roughly 4 m on
      this project's imagery.
    * **Below the true nearest-neighbour spacing.**  The estate pattern is
      9.0 m x 7.8 m, whose nearest-neighbour distance is 7.8 m (9.0 m if read as
      a triangular pitch); both readings are 142.5 SPH.  Merging two real palms
      undercounts the block, which is the error that costs money.

    0.75 puts the radius inside that window on both sides.

    **Evidence it is a model parameter and not a fit to one image.**  Graded
    against a synthetic triangular plantation whose density is arithmetic and
    which contains nothing from the pipeline (``tests/ground_truth.py``), with
    palm size held at a real mature crown:

    * at the standard's own pitch it recovers the truth -- 142.5 SPH against a
      true 142.5 at +/-1.0 m planting error (0.00%), +0.64% on a surveyed
      lattice, -1.72% at +/-1.5 m;
    * the count is *flat* in this fraction over 0.70-0.80 (within 1.3%) and only
      collapses at 0.90 on a surveyed lattice, where the radius starts swallowing
      genuine neighbours (-6.4%; -48% at 1.00) -- so the fraction is not
      manufacturing the answer the way a fitted constant would;
    * the census tracks density across a factor of three (7.0 m true pitch is
      235.7 SPH, 12.0 m is 79.7, both recovered as such), so no single number is
      being echoed back;
    * it is invariant to tiling (512 px to whole-image: identical count) and to
      the ground sample distance (2-8 cm/px: within 1.3%), so the area that SPH
      divides by is not inheriting a hidden constant.

    **Validity domain, measured rather than assumed.**  The radius is a fraction
    of the pitch the caller *declares*, so accuracy depends on declaring the
    right standard.  With +/-1 m of planting error the SPH error stays under 8%
    while the true pitch is within 5.6% of the declared one, and reaches -22% on
    a block 11% tighter than declared.  A mis-declared standard is the dominant
    error term in a census, and nothing here detects it.

    Left open deliberately: 0.70 has the smaller worst case across an 8-10 m
    band (6.9% versus 13.5%), because on a tightly planted block 6.75 m comes
    close to the true nearest-neighbour spacing.  0.75 is kept because on real
    imagery this radius does most of its work suppressing frond-structure
    apexes -- 628 raw candidates become 140 on the bundled demo -- and a uniform
    synthetic crown cannot measure that side of the trade.  Settling it needs a
    hand-labelled real patch, which this project does not have.  See the V2
    decision log.
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
    "dense": PalmStandard(
        key="dense",
        label="Dense Mature (TM Padat / Compact Blocks)",
        # Same 9 m grid assumption and mature crown optics as "mature"; only
        # the exclusion fraction is relaxed (0.65 -> 5.85 m floor). For blocks
        # where mature crowns stand tighter than the textbook grid (planting
        # error, lean, compact soil): admits strong neighbours the 6.75 m
        # floor deletes, while staying above the ~4 m frond-structure band
        # where the count collapses into fronds (measured elbow at ~4.8 m).
        # Opt-in per survey zone; the default standard is untouched.
        expected_spacing_m=9.0,
        min_spacing_fraction=0.65,
        blur_m=1.25,
        peak_separation_m=1.45,
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
