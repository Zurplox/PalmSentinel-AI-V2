"""
Planting-pitch estimation from the imagery itself (Problem 5).

The integer count must not depend on the operator-typed GSD.  This module
measures the planting pitch directly from canopy greenness periodicity, so
detection parameters can be derived from image-measured pixels instead of
typed-GSD-derived pixels.  It returns ``None`` (rather than a guess) when no
periodicity clears the bar.
"""

from __future__ import annotations

import math
from typing import Optional

import cv2
import numpy as np


def _radial_profile(autocorr: np.ndarray, max_lag: int) -> np.ndarray:
    """Mean autocorrelation at each integer lag 0..max_lag."""
    h, w = autocorr.shape
    cy, cx = h // 2, w // 2
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    profile = np.zeros(max_lag + 1, dtype=np.float64)
    for lag in range(max_lag + 1):
        ring = (dist >= lag - 0.5) & (dist < lag + 0.5)
        if ring.any():
            profile[lag] = float(autocorr[ring].mean())
    return profile


def estimate_pitch_px(
    green,
    *,
    min_lag_px: float = 24.0,
    max_lag_frac: float = 1.0 / 3.0,
    min_prominence: float = 0.03,
    target_side: int = 768,
) -> Optional[float]:
    """Estimate the planting pitch, in full-resolution pixels.

    ``green`` is a 2D greenness array (e.g. Excess Green) over the survey
    region.  Returns the lag of the first prominent autocorrelation peak past
    the central lobe, or ``None`` when the region shows no measurable planting
    periodicity (bare ground, forest, single crown).
    """
    g = np.asarray(green, dtype=np.float64)
    if g.ndim != 2 or g.shape[0] < 64 or g.shape[1] < 64:
        return None

    h, w = g.shape
    down = max(1.0, max(h, w) / float(target_side))
    if down > 1.0:
        small = cv2.resize(
            g, (max(1, int(round(w / down))), max(1, int(round(h / down)))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        small = g
        down = 1.0

    sh, sw = small.shape
    vec = small - small.mean()
    if float(np.std(vec)) < 1e-9:
        return None

    spectrum = np.abs(np.fft.rfft2(vec)) ** 2
    ac = np.fft.irfft2(spectrum, s=vec.shape)
    ac = np.fft.fftshift(ac)
    peak0 = float(ac[sh // 2, sw // 2])
    if peak0 <= 0:
        return None
    ac /= peak0

    max_lag = max(8, int(min(sh, sw) * max_lag_frac))
    profile = _radial_profile(ac, max_lag)
    start = max(2, int(math.ceil(min_lag_px / down)))
    if start + 4 >= len(profile):
        return None

    # First local minimum past the central lobe, then the first local maximum
    # after it with enough prominence to be planting, not noise.
    lo = None
    for lag in range(start, len(profile) - 1):
        if profile[lag] <= profile[lag - 1] and profile[lag] <= profile[lag + 1]:
            lo = lag
            break
    if lo is None:
        return None

    for lag in range(lo + 1, len(profile) - 1):
        if profile[lag] >= profile[lag - 1] and profile[lag] >= profile[lag + 1]:
            preceding = float(profile[lo:lag + 1].min())
            if float(profile[lag]) - preceding >= min_prominence:
                # Parabolic sub-pixel refinement.
                y0, y1, y2 = profile[lag - 1], profile[lag], profile[lag + 1]
                denom = (y0 - 2.0 * y1 + y2)
                shift = 0.0
                if abs(denom) > 1e-12:
                    shift = max(-0.5, min(0.5, 0.5 * (y0 - y2) / denom))
                # A plain float, deliberately: this value becomes the census's
                # ruler, and every derived quantity (scale, area, density)
                # inherits its dtype. Returning a numpy scalar here made the
                # whole payload unserializable at the JSON boundary.
                return float((lag + shift) * down)
    return None
