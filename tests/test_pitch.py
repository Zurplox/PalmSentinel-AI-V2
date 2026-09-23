"""
Problem 5: the integer count must not depend on the typed GSD.

The typed ground sample distance is a measurement input.  It belongs
downstream (area, density, bands), never inside detection.  These tests pin
that separation:

* the pitch estimator recovers exactly known synthetic pitches;
* it declines (``None``) on ground with no planting periodicity;
* with ``scale_from_image`` on, the SAME image censused under typed GSDs of
  2, 4 and 8 cm/px yields the SAME integer count;
* the new path is tile-invariant like the old one;
* the default path is untouched (no pitch keys populate the diagnostics).
"""

from __future__ import annotations

import os
import unittest

import cv2
import numpy as np

from palmsentinel.agronomy import get_standard
from palmsentinel.detection import vegetation_index
from palmsentinel.pitch import estimate_pitch_px
from palmsentinel.pipeline import CensusConfig, run_census
from palmsentinel.scale import GroundScale
from synthetic import jittered_lattice, render_plantation

DEMO = os.path.join("data", "demo_palm_estate.jpg")


def _synthetic(pitch_m, jitter_m=0.0, seed=11):
    scale = GroundScale(4.0)
    points = jittered_lattice(pitch_m, 12, 12, jitter_m=jitter_m, seed=seed)
    image, _poly = render_plantation(
        points, scale, crown_radius_m=3.0, apex_radius_m=1.0, margin_m=12.0
    )
    return image


class TestPitchEstimation(unittest.TestCase):
    def test_recovers_known_synthetic_pitches(self):
        for pitch_m in (7.0, 9.0, 12.0):
            image = _synthetic(pitch_m)
            veg = vegetation_index(image, "exg").astype(np.float64)
            got = estimate_pitch_px(veg)
            self.assertIsNotNone(got, f"no pitch measured at {pitch_m} m")
            true_px = pitch_m / 0.04
            self.assertAlmostEqual(got / true_px, 1.0, delta=0.06)

    def test_recovers_pitch_with_planting_error(self):
        image = _synthetic(9.0, jitter_m=1.0)
        veg = vegetation_index(image, "exg").astype(np.float64)
        got = estimate_pitch_px(veg)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got / 225.0, 1.0, delta=0.06)

    def test_returns_none_on_uniform_ground(self):
        flat = np.full((400, 400), 128.0)
        self.assertIsNone(estimate_pitch_px(flat))

    def test_returns_none_on_tiny_region(self):
        small = np.random.default_rng(3).normal(100, 10, size=(40, 40))
        self.assertIsNone(estimate_pitch_px(small))

    def test_the_estimate_is_a_builtin_float(self):
        """Everything derived from it -- the scale object, area, density,
        the disagreement flag -- inherits this dtype. A numpy scalar here
        made the whole census payload unserializable at the JSON boundary,
        which surfaced as a 500 on /api/census and in the CLI's --json."""
        got = estimate_pitch_px(
            vegetation_index(_synthetic(9.0), "exg").astype(np.float64)
        )
        self.assertIsNotNone(got)
        self.assertIs(type(got), float)


class TestGsdFreeCount(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = cv2.imread(DEMO, cv2.IMREAD_COLOR)
        if cls.demo is None:  # pragma: no cover - developer asset missing
            raise unittest.SkipTest("bundled demo orthomosaic not found")

    def _census(self, gsd_cm, tile_px=1536):
        return run_census(
            self.demo,
            CensusConfig(
                standard=get_standard("mature"),
                scale=GroundScale(gsd_cm),
                tile_px=tile_px,
                scale_from_image=True,
            ),
        )

    def test_count_identical_across_typed_gsd(self):
        counts = {}
        for gsd in (2.0, 4.0, 8.0):
            result = self._census(gsd)
            self.assertTrue(result.diagnostics["pitch_estimation_ok"])
            counts[gsd] = result.total_palms
        self.assertEqual(counts[2.0], counts[4.0])
        self.assertEqual(counts[4.0], counts[8.0])

    def test_new_path_is_tile_invariant(self):
        counts = {tile: self._census(4.0, tile_px=tile).total_palms
                  for tile in (512, 4096)}
        self.assertEqual(counts[512], counts[4096])

    def test_both_paths_serialize_to_json(self):
        """The endpoint and the CLI both hand this payload to json; one numpy
        scalar anywhere turns the census into a 500 (measured: 'gsd_disagreement'
        carried numpy's bool, and the density inherited it from the pitch)."""
        import json

        from web.views import _census_payload

        measured = self._census(4.0)
        typed = run_census(
            self.demo,
            CensusConfig(standard=get_standard("mature"), scale=GroundScale(4.0)),
        )
        for result in (measured, typed):
            json.dumps(_census_payload(result))
        self.assertIs(type(measured.diagnostics["gsd_disagreement"]), bool)
        self.assertIs(type(measured.diagnostics["measured_pitch_px"]), float)
        self.assertIs(type(measured.sph), float)
        self.assertIs(type(measured.area_ha), float)

    def test_default_path_populates_no_pitch_keys(self):
        result = run_census(
            self.demo,
            CensusConfig(standard=get_standard("mature"), scale=GroundScale(4.0)),
        )
        self.assertFalse(result.diagnostics["pitch_estimation_ok"])
        self.assertIsNone(result.diagnostics["measured_pitch_px"])
        self.assertIsNone(result.diagnostics["sph_typed_gsd"])


if __name__ == "__main__":
    unittest.main()
