"""
PalmSentinel V2 -- drone oil palm census, correctness core.

Layout (one owner per concern):

``agronomy``   every agronomic threshold and the only place a spacing is written
``scale``      the only pixel <-> ground metre conversion
``detection``  vegetation index, region-wide observation context, apex finding,
               crown radius measurement
``tiling``     exact-cover core-ownership tiling (no seam duplicates)
``pipeline``   orchestration, verified post-conditions, and the
               nearest-neighbour spacing measurement that validates a census

The census never guesses a crop extent: a survey polygon is optional, and when
it is absent the whole image is censused, with the area always going through
the same :class:`~palmsentinel.scale.GroundScale`.
"""

from __future__ import annotations

from .agronomy import (
    DEFAULT_GSD_CM_PER_PX,
    DEFAULT_STANDARD_KEY,
    INDUSTRY_TARGET_SPH,
    PALM_STANDARDS,
    ROSETTE_RADIUS_SEARCH_M,
    SPH_BANDS,
    PalmStandard,
    SphBand,
    bands_are_contiguous,
    classify_sph,
    get_standard,
)
from .detection import (
    Candidate,
    CanopyDetector,
    DetectorParams,
    Observation,
    otsu_threshold,
    observe_region,
    vegetation_index,
)
from .pipeline import (
    CensusConfig,
    CensusResult,
    EmptyRegionError,
    Palm,
    enforce_min_spacing,
    minimum_pairwise_distance,
    nearest_neighbour_distances,
    nearest_neighbour_summary,
    run_census,
)
from .scale import GroundScale, ImageFrame, bounds_of, shoelace_area_px
from .tiling import Tile, assert_exact_cover, core_contains, plan_tiles

__all__ = [
    # agronomy
    "DEFAULT_GSD_CM_PER_PX",
    "DEFAULT_STANDARD_KEY",
    "INDUSTRY_TARGET_SPH",
    "PALM_STANDARDS",
    "ROSETTE_RADIUS_SEARCH_M",
    "SPH_BANDS",
    "PalmStandard",
    "SphBand",
    "bands_are_contiguous",
    "classify_sph",
    "get_standard",
    # detection
    "Candidate",
    "CanopyDetector",
    "DetectorParams",
    "Observation",
    "otsu_threshold",
    "observe_region",
    "vegetation_index",
    # pipeline
    "CensusConfig",
    "CensusResult",
    "EmptyRegionError",
    "Palm",
    "enforce_min_spacing",
    "minimum_pairwise_distance",
    "nearest_neighbour_distances",
    "nearest_neighbour_summary",
    "run_census",
    # scale
    "GroundScale",
    "ImageFrame",
    "bounds_of",
    "shoelace_area_px",
    # tiling
    "Tile",
    "assert_exact_cover",
    "core_contains",
    "plan_tiles",
]

__version__ = "2.0.0"
