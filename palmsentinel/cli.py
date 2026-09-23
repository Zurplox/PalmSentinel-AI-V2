"""
Headless census CLI.

    python -m palmsentinel.cli census --image data/demo_palm_estate.jpg --gsd 4.0
    python -m palmsentinel.cli census --image mosaic.jpg --gsd 4.0 \
        --polygon "300,1200 4200,400 9000,1000 8600,8600 4200,9300 300,7800"

There is deliberately no "overview" coordinate space here: polygons are always
given in full-resolution pixels, which removes the V1 failure mode where the
caller and the server could disagree about which space a vertex was in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .agronomy import DEFAULT_GSD_CM_PER_PX, DEFAULT_STANDARD_KEY, get_standard
from .pipeline import CensusConfig, run_census
from .scale import GroundScale

# Large orthomosaics can exceed OpenCV's default pixel ceiling.
cv2.setNumThreads(max(1, os.cpu_count() or 1))


def load_image(path: str) -> np.ndarray:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        # Pillow handles a few JPEG/TIFF variants OpenCV refuses.
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as handle:
            image = cv2.cvtColor(np.array(handle.convert("RGB")), cv2.COLOR_RGB2BGR)
    if image is None:
        raise ValueError(f"Could not decode image: {path}")
    return image


def parse_polygon(text: Optional[str]) -> Optional[Tuple[Tuple[float, float], ...]]:
    """Parse ``"x,y x,y x,y"`` (or ``;`` separated) into float vertices."""
    if not text:
        return None
    cleaned = text.replace(";", " ").replace("|", " ")
    vertices: List[Tuple[float, float]] = []
    for chunk in cleaned.split():
        if "," not in chunk:
            raise ValueError(f"Bad polygon vertex {chunk!r}; expected 'x,y'")
        x, y = chunk.split(",", 1)
        vertices.append((float(x), float(y)))
    if len(vertices) < 3:
        raise ValueError("A polygon needs at least 3 vertices")
    return tuple(vertices)


def _print_result(result) -> None:
    print()
    print("=" * 68)
    print("PALMSENTINEL V2 -- CENSUS")
    print("=" * 68)
    for line in result.summary_lines():
        print(f"  {line}")
    print("=" * 68)
    ok = bool(result.diagnostics.get("spacing_invariant_ok"))
    outside = int(result.diagnostics.get("palms_outside_polygon", 0))
    print(f"  spacing invariant : {'PASS' if ok else 'FAIL'}")
    print(f"  palms outside ROI : {outside}")
    print("=" * 68)
    print()


def cmd_census(args: argparse.Namespace) -> int:
    image = load_image(args.image)
    h, w = image.shape[:2]
    scale = GroundScale(args.gsd)
    standard = get_standard(args.standard)
    polygon = parse_polygon(args.polygon)

    print(f"[i] image  : {args.image}")
    print(f"[i] pixels : {w:,} x {h:,} ({w * h / 1e6:,.1f} MP) @ {scale.describe()}")
    print(f"[i] standard: {standard.label} (pitch {standard.expected_spacing_m:g} m, "
          f"min spacing {standard.min_spacing_m:.2f} m)")

    config = CensusConfig(
        standard=standard,
        scale=scale,
        polygon=polygon,
        tile_px=args.tile_px,
        index=args.index,
        vegetation_threshold=args.threshold,
        min_spacing_px=args.min_spacing_px,
    )
    result = run_census(image, config)
    _print_result(result)

    if args.json:
        _write_json(result, args.json)
        print(f"[i] wrote {args.json}")
    return 0 if result.diagnostics.get("spacing_invariant_ok") else 2


def _write_json(result, path: str) -> None:
    payload = {
        "total_palms": result.total_palms,
        "area_ha": round(result.area_ha, 6),
        "sph": round(result.sph, 3),
        "sph_band": result.sph_band.key,
        "sph_band_label": result.sph_band.label,
        "min_spacing_px": round(result.min_spacing_px, 3),
        "observation": {
            "index": result.observation.index,
            "threshold": round(result.observation.threshold, 4),
            "background": round(result.observation.background, 4),
        },
        "detector_params": {
            "blur_ksize": result.params.blur_ksize,
            "peak_separation_px": result.params.peak_separation_px,
            "min_spacing_px": round(result.params.min_spacing_px, 3),
            "rosette_radius_min_px": round(result.params.rosette_radius_min_px, 3),
            "rosette_radius_max_px": round(result.params.rosette_radius_max_px, 3),
            "halo_px": result.params.halo_px,
        },
        "palm_spacing": {
            key: result.diagnostics.get(key)
            for key in ("nn_median_m", "nn_mode_m", "nn_p10_m", "nn_p90_m")
        },
        "diagnostics": {
            k: (list(v) if isinstance(v, tuple) else v)
            for k, v in result.diagnostics.items()
        },
        "palms": [
            {
                "id": p.palm_id,
                "x_px": round(p.x_px, 2),
                "y_px": round(p.y_px, 2),
                "x_m": round(p.x_m, 3),
                "y_m": round(p.y_m, 3),
                "peak_exg": round(p.peak_exg, 3),
                "rosette_radius_px": round(p.rosette_radius_px, 2),
                "rosette_radius_m": round(p.rosette_radius_m, 3),
            }
            for p in result.palms
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="palmsentinel",
        description="PalmSentinel V2 -- drone oil palm census correctness core",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--image", required=True, help="Orthomosaic (jpg/png/tif)")
        p.add_argument("--gsd", type=float, default=DEFAULT_GSD_CM_PER_PX,
                       help=f"Ground sample distance in cm/px (default {DEFAULT_GSD_CM_PER_PX})")
        p.add_argument("--index", default="exg", choices=["exg", "gli", "vari"])
        p.add_argument("--polygon", default=None,
                       help="ROI as 'x,y x,y x,y' in FULL-RESOLUTION pixels")

    census = sub.add_parser("census", help="Run a census")
    add_common(census)
    census.add_argument("--standard", default=DEFAULT_STANDARD_KEY,
                        choices=["mature", "young"])
    census.add_argument("--tile-px", type=int, default=1536)
    census.add_argument("--threshold", type=float, default=None,
                        help="Override the Otsu vegetation threshold (index units)")
    census.add_argument("--min-spacing-px", type=float, default=None,
                        help="Diagnostic override; bypasses metre-derived spacing")
    census.add_argument("--json", default=None, help="Write a full JSON report")
    census.set_defaults(func=cmd_census)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (FileNotFoundError, ValueError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
