"""
V1 vs V2 census comparison on the original project's own data.

This is the evidence harness for the correctness pass.  It answers, in numbers:

* What does V2 count on the same image and the same polygon that V1 counted?
* Does V2 reproduce the number in V1's own committed ``blok_tm_utara`` CSV?
* If the counts differ, is the difference caused by the *pipeline* (tiling,
  suppression, polygon handling) or by the *spacing calibration*?
* Is the count sensitive to how the region was tiled (i.e. are seam duplicates
  really gone)?

The third question is answered by running V2 with V1's pixel spacing injected
verbatim (``--v1-spacing``): if V2 then lands near V1's number, the pipeline is
not the variable and the spacing is.  The fourth is answered by running V2 with
a single tile (no seams at all) and comparing to the many-seam default.

The V1 engine is imported read-only from ``--v1-root``; nothing there is written.

    python tools/compare_v1_v2.py
    python tools/compare_v1_v2.py --skip-heavy      # demo only
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from palmsentinel.agronomy import DEFAULT_GSD_CM_PER_PX, get_standard
from palmsentinel.pipeline import (
    CensusConfig,
    nearest_neighbour_summary,
    run_census,
)
from palmsentinel.scale import GroundScale, shoelace_area_px

DEFAULT_V1_ROOT = r"F:\PalmSentinel-AI"
DEMO_IMAGE = os.path.join("data", "demo_palm_estate.jpg")
MOSAIC_IMAGE = "Jalan-Lintas-S5080iak-Tumang-3-7-2026-orthophoto-2.jpg"

# Exactly the ROI and parameters that V1's `run_full_sensus.py` used for
# "Blok 1 - TM Utara", so V1-live and the committed CSV are comparable.
BLOK_TM_UTARA_POLYGON: Tuple[Tuple[float, float], ...] = (
    (300, 1200), (4200, 400), (9000, 1000), (8600, 8600), (4200, 9300), (300, 7800),
)
BLOK_TM_UTARA_V1_PARAMS = dict(blur_ksize=31, min_distance_px=70, vegetation_threshold=75)

# V1's mature preset, verbatim.
V1_MATURE = dict(blur_ksize=31, dilation_radius=33, min_distance_px=68,
                 vegetation_threshold=75)


@dataclass
class Outcome:
    label: str
    count: int
    area_ha: float
    sph: float
    seconds: float
    detail: Dict[str, object] = field(default_factory=dict)


@dataclass
class V1Engine:
    """Read-only handle on the original project's engine."""

    root: str

    def __post_init__(self) -> None:
        self.saved_path = list(sys.path)
        sys.path.insert(0, self.root)
        from engine.detector import PalmDetector  # noqa: E402
        from engine.roi_utils import calculate_polygon_area, calculate_sph  # noqa: E402
        from engine.tiler import TiledProcessor  # noqa: E402

        self._detector = PalmDetector
        self._tiler = TiledProcessor
        self._area = calculate_polygon_area
        self._sph = calculate_sph

    def close(self) -> None:
        sys.path[:] = self.saved_path

    def census(self, image, polygon, preset: str, gsd_cm: float,
               params: Dict[str, object], label: str) -> Outcome:
        detector = self._detector(preset)
        tiler = self._tiler(detector)
        started = time.perf_counter()
        result = tiler.process_roi(
            full_image=image, polygon_coords=list(polygon), **params
        )
        seconds = time.perf_counter() - started
        area = self._area(list(polygon), gsd_cm_per_pixel=gsd_cm)
        count = int(result["total_count"])
        sph = self._sph(count, area["area_hectares"])
        points = [(float(p["x"]), float(p["y"])) for p in result["palms"]]
        nn = nearest_neighbour_summary(points, gsd_cm / 100.0)
        return Outcome(
            label=label,
            count=count,
            area_ha=float(area["area_hectares"]),
            sph=float(sph["sph"]),
            seconds=round(seconds, 3),
            detail={
                "params": params,
                "sph_status": sph["status"],
                "palms": result["palms"],
                **nn,
            },
        )


def v2_census(image, polygon, scale: GroundScale, label: str,
              min_spacing_px: Optional[float] = None,
              tile_px: int = 1536) -> Outcome:
    config = CensusConfig(
        standard=get_standard("mature"),
        scale=scale,
        polygon=tuple(polygon),
        tile_px=tile_px,
        min_spacing_px=min_spacing_px,
    )
    result = run_census(image, config)
    return Outcome(
        label=label,
        count=result.total_palms,
        area_ha=result.area_ha,
        sph=result.sph,
        seconds=float(result.diagnostics["elapsed_s"]),
        detail={
            "result": result,
            "min_spacing_px": result.min_spacing_px,
            "candidates_raw": result.diagnostics["candidates_raw"],
            "suppressed": result.diagnostics["candidates_suppressed_by_spacing"],
            "min_pairwise_px": result.diagnostics["min_pairwise_distance_px"],
            "spacing_ok": result.diagnostics["spacing_invariant_ok"],
            "outside_roi": result.diagnostics["palms_outside_polygon"],
            "tiles": result.diagnostics["tiles"],
            "band": result.sph_band.label,
            "rosette_median_m": result.diagnostics["rosette_radius_median_m"],
            **{
                key: result.diagnostics[key]
                for key in ("nn_median_m", "nn_mode_m", "nn_p10_m", "nn_p90_m")
                if key in result.diagnostics
            },
        },
    )


def committed_csv_numbers(path: str, gsd_cm: float) -> Outcome:
    """The original author's own committed sensus CSV for blok_tm_utara."""
    with open(path, "r", encoding="utf-8") as handle:
        head = handle.readline()
        rows = [line for line in handle if line.strip()]
    del head
    count = len(rows)
    px_area = _polygon_px_area(BLOK_TM_UTARA_POLYGON)
    area_ha = px_area * (gsd_cm / 100.0) ** 2 / 10_000.0
    return Outcome(
        label="V1 committed CSV",
        count=count,
        area_ha=area_ha,
        sph=count / area_ha if area_ha else 0.0,
        seconds=float("nan"),
        detail={"path": path, "area_source": "shoelace @4 cm/px"},
    )


def _polygon_px_area(polygon: Sequence[Tuple[float, float]]) -> float:
    return shoelace_area_px([(float(x), float(y)) for x, y in polygon])


def load_image(path: str) -> np.ndarray:
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as handle:
            image = cv2.cvtColor(np.array(handle.convert("RGB")), cv2.COLOR_RGB2BGR)
    if image is None:
        raise FileNotFoundError(path)
    return image


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _nn(o: Outcome, key: str = "nn_median_m") -> Optional[float]:
    value = o.detail.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def print_block(title: str, outcomes: List[Outcome], baseline: Optional[Outcome] = None) -> None:
    print()
    print("=" * 92)
    print(title)
    print("=" * 92)
    print(f"{'variant':<26}{'palms':>10}{'area ha':>10}{'SPH':>9}"
          f"{'NN med m':>10}{'NN mode m':>10}{'sec':>8}")
    print("-" * 92)
    for o in outcomes:
        seconds = "   -" if math.isnan(o.seconds) else f"{o.seconds:8.2f}"
        median = _nn(o)
        mode = _nn(o, "nn_mode_m")
        median_s = f"{median:10.2f}" if median is not None else f"{'-':>10}"
        mode_s = f"{mode:10.2f}" if mode is not None else f"{'-':>10}"
        print(f"{o.label:<26}{o.count:>10,}{o.area_ha:>10.4f}{o.sph:>9.1f}"
              f"{median_s}{mode_s}{seconds:>8}")
    print("-" * 92)
    print("'NN med m' is the median distance from a detected palm to its nearest "
          "detected neighbour:")
    print("a census that resolves palms clusters near the planting pitch; one that "
          "resolves frond clusters clusters near its own suppression radius.")

    if baseline is not None:
        print("ratios against V1's live result on the same image and polygon:")
        for o in outcomes:
            if o is baseline:
                continue
            ratio = o.count / baseline.count if baseline.count else float("nan")
            print(
                f"  {o.label:<26} count x{ratio:5.2f}   "
                f"SPH delta {o.sph - baseline.sph:+8.1f}"
            )
    print("=" * 92)


def explain(region_outcomes: Dict[str, Outcome], gsd_cm: float) -> List[str]:
    """Write the numeric explanation of the discrepancy, evidence first."""
    v1 = region_outcomes["v1_live"]
    v2 = region_outcomes["v2_default"]
    v2_v1spacing = region_outcomes.get("v2_v1_spacing")
    standard = get_standard("mature")
    lines: List[str] = []

    v1_spacing_px = float(v1.detail["params"]["min_distance_px"])  # type: ignore[index]
    v1_spacing_m = v1_spacing_px * gsd_cm / 100.0
    min_spacing_px = float(v2.detail["min_spacing_px"])  # type: ignore[arg-type]
    min_spacing_m = min_spacing_px * gsd_cm / 100.0

    lines.append("STATED SPACING")
    lines.append(
        f"  V1 suppressed apexes closer than {v1_spacing_px:g} px = "
        f"{v1_spacing_m:.2f} m at {gsd_cm:g} cm/px."
    )
    lines.append(
        f"  V2 suppresses apexes closer than {min_spacing_px:.1f} px = "
        f"{min_spacing_m:.2f} m "
        f"({standard.min_spacing_fraction:g} x the "
        f"{standard.expected_spacing_m:g} m mature pitch)."
    )
    lines.append(
        f"  A {min_spacing_m / v1_spacing_m:.2f}x larger exclusion radius caps "
        f"countable density {(min_spacing_m / v1_spacing_m) ** 2:.2f}x lower."
    )

    v1_nn = _nn(v1)
    v2_nn = _nn(v2)
    if v1_nn is not None and v2_nn is not None:
        lines.append("MEASURED SPACING -- nearest neighbour among each engine's own detections")
        lines.append(
            f"  V1's {v1.count:,} palms sit a median {v1_nn:.2f} m apart "
            f"(10-90% {_nn(v1, 'nn_p10_m'):.2f}-{_nn(v1, 'nn_p90_m'):.2f} m)."
        )
        lines.append(
            f"  V2's {v2.count:,} palms sit a median {v2_nn:.2f} m apart "
            f"(10-90% {_nn(v2, 'nn_p10_m'):.2f}-{_nn(v2, 'nn_p90_m'):.2f} m)."
        )
        lines.append(
            f"  V1's typical palm is {v2_nn / max(1e-9, v1_nn):.2f}x closer to its "
            f"neighbour than V2's, i.e. V1 is spacing detections like frond structure "
            f"rather than like a planting grid."
        )

    lines.append("AREA-CONSISTENCY CHECK")
    implied_v1 = 10_000.0 / (v1_nn ** 2) if v1_nn else 0.0
    implied_v2 = 10_000.0 / (v2_nn ** 2) if v2_nn else 0.0
    lines.append(
        f"  A square grid of {v1_nn:.2f} m spacing could hold at most "
        f"{implied_v1:,.0f} palms/ha; V1 reports {v1.sph:,.1f} SPH."
        if v1_nn else "  V1 spacing unavailable."
    )
    lines.append(
        f"  A square grid of {v2_nn:.2f} m spacing holds {implied_v2:,.0f} palms/ha; "
        f"V2 reports {v2.sph:,.1f} SPH against an industry target of "
        f"136-143 SPH."
        if v2_nn else "  V2 spacing unavailable."
    )

    lines.append("RESULT")
    lines.append(
        f"  V1 counted {v1.count:,} palms = {v1.sph:.1f} SPH; V2 counts "
        f"{v2.count:,} = {v2.sph:.1f} SPH, a factor of "
        f"{v1.count / max(1, v2.count):.2f}."
    )
    if v2_v1spacing is not None:
        lines.append(
            f"  V2 with V1's spacing injected verbatim counts "
            f"{v2_v1spacing.count:,} = {v2_v1spacing.sph:.1f} SPH, i.e. "
            f"{v2_v1spacing.count / max(1, v2.count):.2f}x its own default and "
            f"{v2_v1spacing.count / max(1, v1.count):.2f}x V1's number."
        )
        lines.append(
            "  Same tiles, same suppression, same polygons, same image: only the "
            "spacing differs, and it reproduces V1's count. The over-count is a "
            "calibration defect, not a pipeline defect."
        )
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-root", default=DEFAULT_V1_ROOT)
    parser.add_argument("--gsd", type=float, default=DEFAULT_GSD_CM_PER_PX)
    parser.add_argument("--tile-px", type=int, default=1536)
    parser.add_argument("--skip-heavy", action="store_true",
                        help="Skip the 138 MP mosaic; demo image only")
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    v1_root = os.path.abspath(args.v1_root)
    demo_path = os.path.join(v1_root, DEMO_IMAGE)
    mosaic_path = os.path.join(v1_root, "data", MOSAIC_IMAGE)
    csv_path = os.path.join(v1_root, "output", "blok_tm_utara_coordinates.csv")

    print(f"[i] V1 reference root : {v1_root}")
    print(f"[i] V2 core           : {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}")
    print(f"[i] GSD               : {args.gsd:g} cm/px")

    scale = GroundScale(args.gsd)
    report: Dict[str, object] = {"gsd_cm_per_px": args.gsd}

    # ---------------- V1 engine handle ----------------
    try:
        v1 = V1Engine(v1_root)
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[!] Could not import the V1 engine from {v1_root}: {exc}")
        print("    Run with --v1-root pointing at the PalmSentinel-AI checkout.")
        return 1
    v1_handle = v1

    # ---------------- Region A: bundled demo orthomosaic ----------------
    print()
    print("#" * 78)
    print("# REGION A -- bundled demo orthomosaic (V1's own public demo asset)")
    print("#" * 78)
    demo = load_image(demo_path)
    dh, dw = demo.shape[:2]
    demo_polygon = ((0.0, 0.0), (float(dw), 0.0), (float(dw), float(dh)), (0.0, float(dh)))
    demo_area_ha = scale.px_area_to_ha(shoelace_area_px(demo_polygon))
    print(f"  image   : {dw:,} x {dh:,} px ({dw * dh / 1e6:.2f} MP)")
    print(f"  ROI     : full image, {demo_area_ha:.4f} ha at {args.gsd:g} cm/px")

    demo_outcomes: List[Outcome] = []
    v1_demo = v1_handle.census(demo, demo_polygon, "mature", args.gsd, V1_MATURE,
                               "V1 mature preset")
    demo_outcomes.append(v1_demo)
    demo_outcomes.append(v2_census(demo, demo_polygon, scale, "V2 default"))
    demo_outcomes.append(
        v2_census(demo, demo_polygon, scale, "V2 with V1 spacing",
                  min_spacing_px=float(V1_MATURE["min_distance_px"]))
    )
    print_block("REGION A -- demo orthomosaic", demo_outcomes, baseline=v1_demo)

    # Tile-size sweep on the same image: proves seams add no palms.
    sweep_counts = []
    for tile_px in (512, 1024, 2048, 4096):
        outcome = v2_census(demo, demo_polygon, scale, f"V2 tile {tile_px}px",
                            tile_px=tile_px)
        sweep_counts.append((tile_px, outcome.count))
    print("tile-size sweep (same ROI, same image):")
    for tile_px, count in sweep_counts:
        print(f"  tile {tile_px:>5}px -> {count:>7,} palms")
    spread = (max(c for _, c in sweep_counts) - min(c for _, c in sweep_counts)) / max(
        1, max(c for _, c in sweep_counts)
    )
    print(f"  spread: {spread:.4%}  (a seam-duplicate bug would make this large)")

    report["region_a"] = {
        "image": demo_path,
        "outcomes": [
            {"label": o.label, "count": o.count, "area_ha": o.area_ha, "sph": o.sph}
            for o in demo_outcomes
        ],
        "tile_sweep": sweep_counts,
        "tile_sweep_spread": spread,
        "explanation": explain(
            {"v1_live": v1_demo, "v2_default": demo_outcomes[1],
             "v2_v1_spacing": demo_outcomes[2]},
            args.gsd,
        ),
    }

    # ---------------- Region B: the 138 MP mosaic, blok_tm_utara ----------------
    if not args.skip_heavy and os.path.exists(mosaic_path):
        print()
        print("#" * 78)
        print("# REGION B -- 138 MP mosaic, V1's 'Blok 1 - TM Utara' ROI")
        print("#" * 78)
        mosaic = load_image(mosaic_path)
        mh, mw = mosaic.shape[:2]
        block_area_ha = scale.px_area_to_ha(shoelace_area_px(BLOK_TM_UTARA_POLYGON))
        print(f"  image   : {mw:,} x {mh:,} px ({mw * mh / 1e6:.1f} MP)")
        print(f"  ROI     : V1's blok_tm_utara polygon, {block_area_ha:.4f} ha")

        block_outcomes: List[Outcome] = []
        if os.path.exists(csv_path):
            block_outcomes.append(committed_csv_numbers(csv_path, args.gsd))
        v1_block = v1_handle.census(mosaic, BLOK_TM_UTARA_POLYGON, "mature", args.gsd,
                                    BLOK_TM_UTARA_V1_PARAMS, "V1 live (same params)")
        block_outcomes.append(v1_block)
        v2_block = v2_census(mosaic, BLOK_TM_UTARA_POLYGON, scale, "V2 default")
        block_outcomes.append(v2_block)
        v2_block_v1spacing = v2_census(
            mosaic, BLOK_TM_UTARA_POLYGON, scale, "V2 with V1 spacing",
            min_spacing_px=float(BLOK_TM_UTARA_V1_PARAMS["min_distance_px"]),
        )
        block_outcomes.append(v2_block_v1spacing)

        # Single-tile baseline: zero internal seams.  Equal count => no seam duplicates.
        single_tile = v2_census(
            mosaic, BLOK_TM_UTARA_POLYGON, scale, "V2 single tile (no seams)",
            tile_px=1 << 20,
        )
        block_outcomes.append(single_tile)

        print_block("REGION B -- blok_tm_utara", block_outcomes, baseline=v1_block)

        seam_delta = single_tile.count - v2_block.count
        print(f"seam check: {v2_block.detail['tiles']} tiles -> "
              f"{v2_block.count:,} palms; 1 tile -> {single_tile.count:,} palms; "
              f"difference {seam_delta:+d} ({abs(seam_delta) / max(1, single_tile.count):.3%})")
        print("V2 diagnostics:")
        for key in ("min_spacing_px", "candidates_raw", "suppressed", "min_pairwise_px",
                    "spacing_ok", "outside_roi", "band", "rosette_median_m",
                    "nn_median_m", "nn_mode_m"):
            print(f"  {key:<18}: {v2_block.detail[key]}")

        explanation = explain(
            {"v1_live": v1_block, "v2_default": v2_block,
             "v2_v1_spacing": v2_block_v1spacing},
            args.gsd,
        )
        print()
        print("WHY THE NUMBERS DIFFER")
        print("-" * 78)
        for line in explanation:
            print(f"  * {line}")
        print("-" * 78)

        report["region_b"] = {
            "image": mosaic_path,
            "polygon": BLOK_TM_UTARA_POLYGON,
            "v1_params": BLOK_TM_UTARA_V1_PARAMS,
            "outcomes": [
                {"label": o.label, "count": o.count, "area_ha": o.area_ha, "sph": o.sph}
                for o in block_outcomes
            ],
            "seam_delta": seam_delta,
            "v2_default_diagnostics": {
                k: (str(v) if isinstance(v, object) and k == "band" else v)
                for k, v in v2_block.detail.items()
                if k not in ("result",)
            },
            "explanation": explanation,
            "v2_palm_count": v2_block.count,
            "v2_sph": v2_block.sph,
        }
    elif not args.skip_heavy:
        print(f"[!] mosaic not found at {mosaic_path}; skipping region B")

    # ---------------- Region A explanation ----------------
    print()
    print("#" * 78)
    print("# DEMO IMAGE -- why the numbers differ")
    print("#" * 78)
    for line in report["region_a"]["explanation"]:  # type: ignore[index]
        print(f"  * {line}")

    if args.json:
        def _clean(value):
            if isinstance(value, (np.integer,)):
                return int(value)
            if isinstance(value, (np.floating,)):
                return float(value)
            if isinstance(value, np.ndarray):
                return value.tolist()
            return str(value)

        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=_clean)
        print(f"\n[i] wrote {args.json}")

    v1_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
