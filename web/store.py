"""
Image store -- the single owner of loaded orthomosaic state.

The previous version kept a module-level ``CACHE`` dict that every endpoint read
and wrote, plus an ad-hoc ``scale_factor`` derived from image width and a
``coord_scale`` string negotiated over HTTP to communicate which coordinate space
a polygon was in.  Four different places converted between pixels and ground.

This class owns exactly one thing: *which image is loaded, and what derived raster
views can be served for it*.  It knows nothing about censusing (that is
``palmsentinel``) and nothing about HTTP (that is ``web.views``).

Every derived view is cached under a small bounded LRU because the viewport
renderer asks for the same crop repeatedly while a user pans over a still
picture.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple

import cv2
import numpy as np

from palmsentinel.agronomy import DEFAULT_GSD_CM_PER_PX
from palmsentinel.scale import GroundScale, ImageFrame

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from palmsentinel.pipeline import CensusResult

ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")

#: Longest side of the raster view the canvas paints first.  4096 keeps a whole
#: estate legible while staying under a couple of megabytes as JPEG.
PREVIEW_MAX_SIDE = 4096

#: Cache budget, in entries, for viewport crops.
_CROP_CACHE_SIZE = 24


class ImageNotFound(FileNotFoundError):
    pass


class UnsupportedImage(ValueError):
    pass


@dataclass(frozen=True)
class ImageInfo:
    """Everything the client needs to draw and to convert coordinates."""

    path: str
    filename: str
    width: int
    height: int
    preview_width: int
    preview_height: int
    gsd_cm_per_px: float
    # Content fingerprint of the decoded image.  Raster URLs carry it so the
    # browser cannot serve one image's cached pixels for another image whose
    # URL is identical -- the preview URL, for one, never changes otherwise.
    fingerprint: str = ""

    @property
    def scale_factor(self) -> float:
        """Full-resolution pixels per preview pixel."""
        return self.width / float(self.preview_width)

    def to_dict(self) -> dict:
        frame = ImageFrame(self.width, self.height, GroundScale(self.gsd_cm_per_px))
        extent_w, extent_h = frame.ground_extent_m
        return {
            "path": self.path,
            "filename": self.filename,
            "width": self.width,
            "height": self.height,
            "megapixels": round(self.width * self.height / 1e6, 1),
            "preview_width": self.preview_width,
            "preview_height": self.preview_height,
            "scale_factor": round(self.scale_factor, 6),
            "gsd_cm_per_px": self.gsd_cm_per_px,
            "fingerprint": self.fingerprint,
            "m_per_px": round(frame.m_per_px, 6),
            "extent_m": [round(extent_w, 2), round(extent_h, 2)],
            "full_area_ha": round(frame.full_area_ha, 4),
        }


class ImageStore:
    """Holds one orthomosaic at a time and serves derived raster views for it."""

    def __init__(self, data_dir: str, default_gsd_cm: float = DEFAULT_GSD_CM_PER_PX):
        self.data_dir = os.path.abspath(data_dir)
        self.default_gsd_cm = float(default_gsd_cm)
        self._lock = threading.RLock()
        self._image: Optional[np.ndarray] = None
        self._info: Optional[ImageInfo] = None
        self._preview_bgr: Optional[np.ndarray] = None
        self._preview_jpeg: Optional[bytes] = None
        self._crops: "OrderedDict[tuple, bytes]" = OrderedDict()
        self._last_census: Optional["CensusResult"] = None

    # -- loading -------------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self._image is not None

    @property
    def image(self) -> np.ndarray:
        with self._lock:
            if self._image is None:
                raise ImageNotFound("No orthomosaic is loaded")
            return self._image

    @property
    def info(self) -> ImageInfo:
        with self._lock:
            if self._info is None:
                raise ImageNotFound("No orthomosaic is loaded")
            return self._info

    @staticmethod
    def decode(path: str) -> np.ndarray:
        if not os.path.exists(path):
            raise ImageNotFound(path)
        image = cv2.imread(path, cv2.IMREAD_COLOR)
        if image is None:
            # Pillow covers a few JPEG/TIFF variants OpenCV refuses.
            from PIL import Image

            Image.MAX_IMAGE_PIXELS = None
            try:
                with Image.open(path) as handle:
                    image = cv2.cvtColor(np.array(handle.convert("RGB")), cv2.COLOR_RGB2BGR)
            except Exception as exc:  # pragma: no cover - depends on codec support
                raise UnsupportedImage(f"Could not decode {os.path.basename(path)}: {exc}") from exc
        if image is None or image.size == 0:
            raise UnsupportedImage(f"Could not decode {os.path.basename(path)}")
        return image

    def load(self, path: str, gsd_cm_per_px: Optional[float] = None) -> ImageInfo:
        """Decode ``path`` and make it the active orthomosaic."""
        resolved = os.path.abspath(path)
        with self._lock:
            if self._info is not None and self._info.path == resolved and self._image is not None:
                if gsd_cm_per_px is not None:
                    self._info = self._reinstate_gsd(self._info, gsd_cm_per_px)
                return self._info

            image = self.decode(resolved)
            height, width = image.shape[:2]

            longest = max(width, height)
            factor = min(1.0, PREVIEW_MAX_SIDE / float(longest))
            preview_w = max(1, int(round(width * factor)))
            preview_h = max(1, int(round(height * factor)))
            preview = cv2.resize(
                image, (preview_w, preview_h), interpolation=cv2.INTER_AREA
            )

            self._image = image
            self._preview_bgr = preview
            self._preview_jpeg = None
            self._crops.clear()
            # A census belongs to the image it was run on; a new image
            # invalidates it rather than leaving a stale result exportable.
            self._last_census = None
            # A thumbnail hash identifies the decoded content cheaply: 64x64
            # is 12 kB, insensitive to resize rounding, and changes with any
            # real change of imagery.
            thumb = cv2.resize(preview, (64, 64), interpolation=cv2.INTER_AREA)
            fingerprint = hashlib.sha256(thumb.tobytes()).hexdigest()[:12]
            self._info = ImageInfo(
                path=resolved,
                filename=os.path.basename(resolved),
                width=width,
                height=height,
                preview_width=preview_w,
                preview_height=preview_h,
                gsd_cm_per_px=float(gsd_cm_per_px or self.default_gsd_cm),
                fingerprint=fingerprint,
            )
            return self._info

    def _reinstate_gsd(self, info: ImageInfo, gsd_cm_per_px: float) -> ImageInfo:
        return ImageInfo(
            path=info.path,
            filename=info.filename,
            width=info.width,
            height=info.height,
            preview_width=info.preview_width,
            preview_height=info.preview_height,
            gsd_cm_per_px=float(gsd_cm_per_px),
            fingerprint=info.fingerprint,
        )

    def set_gsd(self, gsd_cm_per_px: float) -> ImageInfo:
        with self._lock:
            if self._info is None:
                raise ImageNotFound("No orthomosaic is loaded")
            self._info = self._reinstate_gsd(self._info, gsd_cm_per_px)
            return self._info

    # -- session result ------------------------------------------------------

    @property
    def last_census(self) -> Optional["CensusResult"]:
        return self._last_census

    def remember_census(self, result: "CensusResult") -> None:
        self._last_census = result

    # -- derived views -------------------------------------------------------

    def _encode(self, bgr: np.ndarray, quality: int) -> bytes:
        ok, buffer = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:  # pragma: no cover - only on an unsupported dtype
            raise RuntimeError("JPEG encoding failed")
        return buffer.tobytes()

    def preview_jpeg(self) -> bytes:
        with self._lock:
            if self._preview_bgr is None:
                raise ImageNotFound("No orthomosaic is loaded")
            if self._preview_jpeg is None:
                self._preview_jpeg = self._encode(self._preview_bgr, 88)
            return self._preview_jpeg

    def crop_jpeg(
        self, x1: int, y1: int, x2: int, y2: int, max_dim: int = 2048
    ) -> bytes:
        """
        A full-resolution crop of the requested rectangle, downscaled only if it
        exceeds ``max_dim``.  This is what makes zooming show real sensor pixels
        rather than an interpolated preview.
        """
        with self._lock:
            if self._image is None or self._info is None:
                raise ImageNotFound("No orthomosaic is loaded")

            info = self._info
            x1 = max(0, min(int(x1), info.width - 1))
            y1 = max(0, min(int(y1), info.height - 1))
            x2 = max(x1 + 1, min(int(x2), info.width))
            y2 = max(y1 + 1, min(int(y2), info.height))
            max_dim = max(64, min(int(max_dim), 4096))

            key = (x1, y1, x2, y2, max_dim)
            cached = self._crops.get(key)
            if cached is not None:
                self._crops.move_to_end(key)
                return cached

            crop = self._image[y1:y2, x1:x2]
            longest = max(crop.shape[0], crop.shape[1])
            if longest > max_dim:
                scale = max_dim / float(longest)
                crop = cv2.resize(
                    crop,
                    (max(1, int(round(crop.shape[1] * scale))),
                     max(1, int(round(crop.shape[0] * scale)))),
                    interpolation=cv2.INTER_AREA,
                )
            encoded = self._encode(crop, 90)

            self._crops[key] = encoded
            while len(self._crops) > _CROP_CACHE_SIZE:
                self._crops.popitem(last=False)
            return encoded

    def sample_jpeg(self, x: float, y: float, size: int = 320) -> bytes:
        """A square full-resolution window centred on a point, for the tree loupe."""
        with self._lock:
            if self._image is None or self._info is None:
                raise ImageNotFound("No orthomosaic is loaded")
            half = max(16, size // 2)
            cx, cy = int(round(x)), int(round(y))
            crop = self._image[
                max(0, cy - half): min(self._info.height, cy + half),
                max(0, cx - half): min(self._info.width, cx + half),
            ]
            if crop.size == 0:
                raise UnsupportedImage("Sample point is outside the image")
            side = max(crop.shape[0], crop.shape[1])
            square = np.zeros((side, side, 3), dtype=np.uint8)
            square[: crop.shape[0], : crop.shape[1]] = crop
            return self._encode(square, 94)

    # -- directory -----------------------------------------------------------

    def available(self) -> List[dict]:
        os.makedirs(self.data_dir, exist_ok=True)
        entries: List[dict] = []
        current = self._info.path if self._info else None
        for name in sorted(os.listdir(self.data_dir)):
            if os.path.splitext(name)[1].lower() not in ALLOWED_EXTENSIONS:
                continue
            full = os.path.join(self.data_dir, name)
            if not os.path.isfile(full):
                continue
            entries.append(
                {
                    "filename": name,
                    "path": full,
                    "size_mb": round(os.path.getsize(full) / (1024 * 1024), 2),
                    "active": bool(current and os.path.normcase(full) == os.path.normcase(current)),
                }
            )
        return entries

    def default_image(self) -> Optional[str]:
        """Prefer the bundled demo asset so a fresh clone works immediately."""
        candidate = os.path.join(self.data_dir, "demo_palm_estate.jpg")
        if os.path.exists(candidate):
            return candidate
        for entry in self.available():
            return entry["path"]
        return None

    def save_upload(self, filename: str, stream) -> str:
        safe = os.path.basename(filename)
        extension = os.path.splitext(safe)[1].lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise UnsupportedImage(
                f"Unsupported file type {extension!r}. Allowed: "
                + ", ".join(ALLOWED_EXTENSIONS)
            )
        os.makedirs(self.data_dir, exist_ok=True)
        destination = os.path.join(self.data_dir, safe)
        stream.save(destination)
        return destination

    def describe(self) -> str:
        if self._info is None:
            return "no image loaded"
        info = self._info
        return (
            f"{info.filename} {info.width:,}x{info.height:,} px "
            f"@ {info.gsd_cm_per_px:g} cm/px"
        )

    @staticmethod
    def now() -> float:
        return time.perf_counter()
