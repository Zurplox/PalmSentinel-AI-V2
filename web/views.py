"""
HTTP surface.

Contracts this layer holds to, and why:

* **Coordinates are always full-resolution pixels.**  There is no ``coord_scale``
  parameter anywhere.  The client converts its display coordinates using the
  ``scale_factor`` it is given, in one function.  The previous version let a
  caller declare which space a polygon was in, which is the kind of contract that
  silently produces wrong areas when a caller guesses wrong.
* **The census is the core's census.**  Endpoints build a
  :class:`~palmsentinel.pipeline.CensusConfig` and call
  :func:`~palmsentinel.pipeline.run_census`.  No arithmetic about palms, areas or
  densities happens in this file, so there is exactly one implementation of the
  agronomy.
* **Exports reuse the last census.**  The store keeps the most recent
  :class:`~palmsentinel.pipeline.CensusResult`, so a 6,000-palm result is not
  round-tripped through the browser to be exported.
* **Download names are sanitised.**  A block name containing a quote or a newline
  previously produced an HTTP 500 from the header, and is a header-injection
  vector regardless.
"""

from __future__ import annotations

import io
import os
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import cv2
from flask import Blueprint, Response, jsonify, render_template, request
from werkzeug.utils import secure_filename

from palmsentinel.agronomy import (
    DEFAULT_GSD_CM_PER_PX,
    DEFAULT_STANDARD_KEY,
    INDUSTRY_TARGET_SPH,
    PALM_STANDARDS,
    ROSETTE_RADIUS_SEARCH_M,
    SPH_BANDS,
)
from palmsentinel.pipeline import CensusConfig, CensusResult, run_census
from palmsentinel.scale import GroundScale

from .store import ImageNotFound, ImageStore, UnsupportedImage

blueprint = Blueprint("palmsentinel", __name__)

#: Filled by :func:`create_app`.
store: ImageStore = None  # type: ignore[assignment]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _json_body() -> Dict[str, Any]:
    """A JSON body, or an empty dict.  Never raises 415 on a missing body."""
    return request.get_json(silent=True) or {}


def _attachment(filename: str) -> str:
    """
    A Content-Disposition value safe for arbitrary user text.

    ``secure_filename`` strips the dangerous characters for the legacy
    ``filename=`` form, and RFC 5987 carries the readable original for clients
    that understand it.
    """
    ascii_name = secure_filename(filename) or "palmsentinel-export"
    encoded = urllib.parse.quote(filename, safe="")
    return (
        f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"
    )


def _parse_points(raw: Any) -> Tuple[Tuple[float, float], ...]:
    """Operator-placed correction points, in full-resolution pixels."""
    points: List[Tuple[float, float]] = []
    for item in raw or ():
        try:
            if isinstance(item, dict):
                points.append((float(item["x"]), float(item["y"])))
            else:
                points.append((float(item[0]), float(item[1])))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return tuple(points)


def _parse_polygon(raw: Any) -> Optional[Tuple[Tuple[float, float], ...]]:
    if not raw:
        return None
    vertices: List[Tuple[float, float]] = []
    for item in raw:
        if isinstance(item, dict):
            vertices.append((float(item["x"]), float(item["y"])))
        else:
            vertices.append((float(item[0]), float(item[1])))
    if len(vertices) < 3:
        return None
    return tuple(vertices)


def _jpgs(payload: bytes, cache_seconds: int = 3600) -> Response:
    return Response(
        payload,
        mimetype="image/jpeg",
        headers={"Cache-Control": f"private, max-age={cache_seconds}"},
    )


def _standards_payload() -> Dict[str, Any]:
    return {
        key: {
            "key": standard.key,
            "label": standard.label,
            "pitch_m": standard.expected_spacing_m,
            "min_spacing_m": round(standard.min_spacing_m, 4),
            "min_spacing_fraction": standard.min_spacing_fraction,
            "blur_m": standard.blur_m,
            "peak_separation_m": standard.peak_separation_m,
        }
        for key, standard in PALM_STANDARDS.items()
    }


def _bands_payload() -> List[Dict[str, Any]]:
    return [
        {
            "key": band.key,
            "label": band.label,
            "guidance": band.guidance,
            "range": band.describe_range(),
            "lo": band.lo,
            "hi": band.hi,
        }
        for band in SPH_BANDS
    ]


def _palm_payload(result: CensusResult) -> List[Dict[str, Any]]:
    """
    The palm list the client draws from.

    Keys are the contract the renderer, the hover grid and the manual-removal
    tool all read: ``x_px``/``y_px`` are *full-resolution pixels*, ``x_m``/``y_m``
    are ground metres.  Emitting bare ``x``/``y`` here silently produced a map
    with no markers at all, because every consumer looked up ``x_px``.
    """
    return [
        {
            "id": palm.palm_id,
            "x_px": round(palm.x_px, 2),
            "y_px": round(palm.y_px, 2),
            "x_m": round(palm.x_m, 3),
            "y_m": round(palm.y_m, 3),
            "peak": round(palm.peak_exg, 2),
            "rosette_px": round(palm.rosette_radius_px, 2),
            "rosette_m": round(palm.rosette_radius_m, 3),
            "manual": palm.manual,
        }
        for palm in result.palms
    ]


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------


@blueprint.get("/")
def index() -> str:
    return render_template("index.html")


@blueprint.get("/favicon.ico")
def favicon() -> Response:
    return Response(status=204)


@blueprint.get("/api/health")
def health() -> Response:
    return jsonify({"ok": True, "image_loaded": store.loaded})


# --------------------------------------------------------------------------
# imagery
# --------------------------------------------------------------------------


@blueprint.get("/api/state")
def state() -> Response:
    """Everything the client needs to render itself on first paint."""
    info = store.info.to_dict() if store.loaded else None
    return jsonify(
        {
            "ok": True,
            "image": info,
            "images": store.available(),
            "default_standard": DEFAULT_STANDARD_KEY,
            "standards": _standards_payload(),
            "sph_bands": _bands_payload(),
            "industry_target_sph": list(INDUSTRY_TARGET_SPH),
            "rosette_search_m": list(ROSETTE_RADIUS_SEARCH_M),
            "has_census": store.last_census is not None,
        }
    )


@blueprint.get("/api/images")
def list_images() -> Response:
    return jsonify({"ok": True, "images": store.available()})


@blueprint.post("/api/image")
def select_image() -> Response:
    """
    Load an orthomosaic, either from an uploaded file or from a local path.

    Uploads are stored inside the configured data directory and are never
    transmitted anywhere else; the demo asset is the only image in version
    control.
    """
    gsd = _json_body().get("gsd_cm")
    if "file" in request.files:
        upload = request.files["file"]
        if not upload or not upload.filename:
            return jsonify({"ok": False, "error": "No file was uploaded."}), 400
        try:
            path = store.save_upload(upload.filename, upload)
        except UnsupportedImage as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
    else:
        body = _json_body()
        path = str(body.get("path", "")).strip().strip('"').strip("'")
        if not path or not os.path.exists(path):
            return jsonify(
                {"ok": False, "error": "That path does not exist on this computer."}
            ), 400

    started = time.perf_counter()
    try:
        info = store.load(path, gsd_cm_per_px=gsd)
    except (ImageNotFound, UnsupportedImage) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify(
        {
            "ok": True,
            "image": info.to_dict(),
            "decode_seconds": round(time.perf_counter() - started, 3),
        }
    )


@blueprint.post("/api/gsd")
def set_gsd() -> Response:
    body = _json_body()
    if body.get("gsd_cm") is None:
        return jsonify({"ok": False, "error": "gsd_cm is required."}), 400
    try:
        info = store.set_gsd(float(body["gsd_cm"]))
    except (ImageNotFound, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "image": info.to_dict()})


@blueprint.get("/api/preview")
def preview() -> Response:
    try:
        return _jpgs(store.preview_jpeg(), cache_seconds=86400)
    except ImageNotFound as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404


@blueprint.get("/api/crop")
def crop() -> Response:
    try:
        args = request.args
        return _jpgs(
            store.crop_jpeg(
                float(args.get("x1", 0)),
                float(args.get("y1", 0)),
                float(args.get("x2", 0)),
                float(args.get("y2", 0)),
                max_dim=int(float(args.get("max_dim", 2048))),
            )
        )
    except (ImageNotFound, UnsupportedImage) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404


@blueprint.get("/api/sample")
def sample() -> Response:
    try:
        return _jpgs(
            store.sample_jpeg(
                float(request.args.get("x", 0)),
                float(request.args.get("y", 0)),
                size=int(float(request.args.get("size", 320))),
            )
        )
    except (ImageNotFound, UnsupportedImage, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404


# --------------------------------------------------------------------------
# the census
# --------------------------------------------------------------------------


@blueprint.post("/api/census")
def census() -> Response:
    if not store.loaded:
        return jsonify({"ok": False, "error": "Load an orthomosaic first."}), 400

    body = _json_body()
    polygon = _parse_polygon(body.get("polygon"))

    standard_key = str(body.get("standard") or DEFAULT_STANDARD_KEY)
    if standard_key not in PALM_STANDARDS:
        return jsonify({"ok": False, "error": f"Unknown standard {standard_key!r}"}), 400

    gsd_cm = float(body.get("gsd_cm") or store.info.gsd_cm_per_px)
    if gsd_cm <= 0:
        return jsonify({"ok": False, "error": "GSD must be positive."}), 400

    try:
        tile_px = int(body.get("tile_px") or 1536)
        threshold = body.get("threshold")
        sensitivity = float(body.get("sensitivity") or 0.0)
        config = CensusConfig(
            standard=PALM_STANDARDS[standard_key],
            scale=GroundScale(gsd_cm),
            polygon=polygon,
            tile_px=max(256, min(tile_px, 8192)),
            vegetation_threshold=float(threshold) if threshold is not None else None,
            sensitivity=max(0.0, min(sensitivity, 1.0)),
            manual_additions=_parse_points(body.get("manual_add")),
            manual_removals=_parse_points(body.get("manual_remove")),
        )
        result = run_census(store.image, config)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except MemoryError:
        return jsonify(
            {"ok": False, "error": "Ran out of memory. Try a smaller region or a larger tile size."}
        ), 500

    store.remember_census(result)
    return jsonify(_census_payload(result))


def _census_payload(result: CensusResult) -> Dict[str, Any]:
    diagnostics = result.diagnostics
    return {
        "ok": True,
        "total_palms": result.total_palms,
        "area_ha": round(result.area_ha, 4),
        "sph": round(result.sph, 2),
        "sph_band": {
            "key": result.sph_band.key,
            "label": result.sph_band.label,
            "guidance": result.sph_band.guidance,
            "range": result.sph_band.describe_range(),
        },
        "industry_target_sph": list(INDUSTRY_TARGET_SPH),
        "min_spacing_px": round(result.min_spacing_px, 2),
        "min_spacing_m": diagnostics.get("min_spacing_m"),
        "palms": _palm_payload(result),
        "spacing": {
            field: diagnostics.get(field)
            for field in (
                "nn_median_m",
                "nn_mode_m",
                "nn_p10_m",
                "nn_p90_m",
            )
        },
        "quality": {
            "spacing_invariant_ok": bool(diagnostics.get("spacing_invariant_ok")),
            "closest_pair_px": round(
                float(diagnostics.get("min_pairwise_distance_px") or 0.0), 3
            ),
            "required_spacing_px": round(result.min_spacing_px, 3),
            "palms_outside_polygon": int(diagnostics.get("palms_outside_polygon", 0)),
            "candidates_raw": int(diagnostics.get("candidates_raw", 0)),
            "candidates_suppressed": int(
                diagnostics.get("candidates_suppressed_by_spacing", 0)
            ),
            "tiles": int(diagnostics.get("tiles", 0)),
            "elapsed_s": diagnostics.get("elapsed_s"),
            "manual_additions": int(diagnostics.get("manual_additions", 0)),
            "manual_additions_rejected": int(
                diagnostics.get("manual_additions_rejected", 0)
            ),
            "detections_removed_by_operator": int(
                diagnostics.get("detections_removed_by_operator", 0)
            ),
        },
        "rosette": {
            "median_m": diagnostics.get("rosette_radius_median_m"),
            "min_m": diagnostics.get("rosette_radius_min_m"),
            "max_m": diagnostics.get("rosette_radius_max_m"),
        },
        "observation": {
            "index": result.observation.index,
            "threshold": round(result.observation.threshold, 3),
            "background": round(result.observation.background, 3),
            "sensitivity": round(result.observation.sensitivity, 3),
        },
        "detector": {
            "blur_ksize": result.params.blur_ksize,
            "peak_separation_px": result.params.peak_separation_px,
            "min_spacing_px": round(result.params.min_spacing_px, 2),
            "halo_px": result.params.halo_px,
        },
        "used_polygon": bool(diagnostics.get("polygon_used")),
    }


@blueprint.get("/api/census")
def last_census() -> Response:
    if store.last_census is None:
        return jsonify({"ok": False, "error": "No census has been run yet."}), 404
    return jsonify(_census_payload(store.last_census))


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------


def _require_census():
    if store.last_census is None:
        return jsonify({"ok": False, "error": "Run a census before exporting."}), 400
    return None


@blueprint.post("/api/export/csv")
def export_csv() -> Response:
    missing = _require_census()
    if missing is not None:
        return missing

    body = _json_body()
    block = str(body.get("block") or "Blok-Utama").strip() or "Blok-Utama"
    result = store.last_census

    buffer = io.StringIO()
    buffer.write(
        "palm_id,block,source,x_px,y_px,x_m,y_m,peak_exg,"
        "rosette_radius_px,rosette_radius_m\n"
    )
    for palm in result.palms:
        source = "manual" if palm.manual else "detected"
        buffer.write(
            f"{palm.palm_id},{block},{source},{palm.x_px:.2f},{palm.y_px:.2f},"
            f"{palm.x_m:.3f},{palm.y_m:.3f},{palm.peak_exg:.2f},"
            f"{palm.rosette_radius_px:.2f},{palm.rosette_radius_m:.3f}\n"
        )
    buffer.write("#\n")
    buffer.write(f"# image,{store.info.filename}\n")
    buffer.write(f"# gsd_cm_per_px,{store.info.gsd_cm_per_px:g}\n")
    buffer.write(f"# palms,{result.total_palms}\n")
    buffer.write(f"# area_ha,{result.area_ha:.6f}\n")
    buffer.write(f"# sph,{result.sph:.3f}\n")
    buffer.write(f"# sph_band,{result.sph_band.key}\n")

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": _attachment(f"sensus_{block}.csv")},
    )


@blueprint.post("/api/export/geojson")
def export_geojson() -> Response:
    missing = _require_census()
    if missing is not None:
        return missing

    body = _json_body()
    block = str(body.get("block") or "Blok-Utama").strip() or "Blok-Utama"
    result = store.last_census
    gsd = store.info.gsd_cm_per_px

    features = []
    for palm in result.palms:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(palm.x_m, 3), round(palm.y_m, 3)]},
                "properties": {
                    "palm_id": palm.palm_id,
                    "block": block,
                    "x_px": round(palm.x_px, 2),
                    "y_px": round(palm.y_px, 2),
                    "peak_exg": round(palm.peak_exg, 2),
                    "rosette_radius_m": round(palm.rosette_radius_m, 3),
                    "source": "manual" if palm.manual else "detected",
                },
            }
        )

    payload = {
        "type": "FeatureCollection",
        "name": f"PalmSentinel_{block}",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "metadata": {
            "coordinate_space": "orthophoto-local metres from the image origin",
            "gsd_cm_per_px": gsd,
            "image": store.info.filename,
            "total_palms": result.total_palms,
            "area_ha": round(result.area_ha, 4),
            "sph": round(result.sph, 2),
            "sph_band": result.sph_band.key,
            "min_spacing_m": round(result.min_spacing_px * gsd / 100.0, 4),
        },
        "features": features,
    }
    return Response(
        _json_dump(payload),
        mimetype="application/geo+json",
        headers={"Content-Disposition": _attachment(f"sensus_{block}.geojson")},
    )


def _json_dump(payload: Dict[str, Any]) -> str:
    import json

    return json.dumps(payload, indent=2)


@blueprint.post("/api/export/annotated")
def export_annotated() -> Response:
    """A JPEG of the censused region with numbered, to-scale crown markers."""
    missing = _require_census()
    if missing is not None:
        return missing

    body = _json_body()
    block = str(body.get("block") or "Blok-Utama").strip() or "Blok-Utama"
    result = store.last_census
    region = result.diagnostics.get("region")
    if not isinstance(region, tuple) or len(region) != 4:
        return jsonify({"ok": False, "error": "Census region is unavailable."}), 500

    x0, y0, x1, y1 = (int(v) for v in region)
    pad = 120
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(store.info.width, x1 + pad), min(store.info.height, y1 + pad)
    crop = store.image[y0:y1, x0:x1].copy()

    # Line weight and label size scale with the marker radius so the export stays
    # legible whether the region is 1 ha or 100 ha.
    for palm in result.palms:
        cx = int(round(palm.x_px)) - x0
        cy = int(round(palm.y_px)) - y0
        if not (0 <= cx < crop.shape[1] and 0 <= cy < crop.shape[0]):
            continue
        radius = int(round(palm.rosette_radius_px))
        radius = max(6, min(radius, 400))
        cv2.circle(crop, (cx, cy), radius, (255, 140, 60), 2, cv2.LINE_AA)
        cv2.circle(crop, (cx, cy), 3, (60, 90, 255), -1, cv2.LINE_AA)

    longest = max(crop.shape[0], crop.shape[1])
    if longest > 4000:
        factor = 4000 / float(longest)
        crop = cv2.resize(
            crop,
            (int(round(crop.shape[1] * factor)), int(round(crop.shape[0] * factor))),
            interpolation=cv2.INTER_AREA,
        )

    ok, buffer = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:  # pragma: no cover
        return jsonify({"ok": False, "error": "Could not encode the annotated image."}), 500
    return Response(
        buffer.tobytes(),
        mimetype="image/jpeg",
        headers={"Content-Disposition": _attachment(f"annotated_{block}.jpg")},
    )


@blueprint.errorhandler(413)
def payload_too_large(_exc) -> Response:
    return jsonify(
        {"ok": False, "error": "That upload exceeds the configured size limit."}
    ), 413
