"""
Tests for the V2 correctness core.

These are written as *invariants* wherever possible -- properties that must hold
for any input rather than values that happened to be observed once.  The three
that matter most:

* ``test_sph_bands_cover_every_value``   -- the V1 band gap cannot come back
* ``test_tile_cores_own_every_pixel_once`` -- seam duplicates cannot come back
* ``test_spacing_invariant_holds_after_census`` -- and neither can the 4x count

The last test class runs the full pipeline on a synthetic plantation of known
density and asserts both that V2 recovers the true density and that the V1
pixel-derived spacing over-counts it, so the original defect is recorded as an
executable fact rather than a claim in a document.
"""

from __future__ import annotations

import unittest

import cv2
import numpy as np
import os

from palmsentinel.agronomy import (
    INDUSTRY_TARGET_SPH,
    SPH_BANDS,
    bands_are_contiguous,
    classify_sph,
    get_standard,
)
from palmsentinel.detection import (
    DetectorParams,
    otsu_threshold,
    vegetation_index,
)
from palmsentinel.pipeline import (
    CensusConfig,
    enforce_min_spacing,
    minimum_pairwise_distance,
    run_census,
)
from palmsentinel.detection import Candidate
from palmsentinel.scale import GroundScale, bounds_of, shoelace_area_px
from palmsentinel.tiling import Tile, assert_exact_cover, core_contains, plan_tiles

from synthetic import render_plantation, triangular_lattice


# --------------------------------------------------------------------------
# Agronomy: the band gap regression
# --------------------------------------------------------------------------


class TestAgronomyBands(unittest.TestCase):
    def test_sph_table_is_contiguous(self):
        self.assertTrue(
            bands_are_contiguous(),
            "SPH bands must tile the real line with no gaps and no overlaps",
        )

    def test_sph_bands_cover_every_value(self):
        """Every density maps to exactly one band -- including the V1 gaps."""
        values = [0.0, 109.9, 110.0, 125.9, 126.0, 145.5, 146.0, 148.0, 165.9,
                  166.0, 250.0, 611.0]
        for value in values:
            matches = [b for b in SPH_BANDS if b.contains(value)]
            self.assertEqual(len(matches), 1, f"{value} matched {len(matches)} bands")

    def test_v1_gap_values_are_now_sensible(self):
        """125.5 and 145.5 fell through V1's if/elif ladder to 'very high'."""
        self.assertEqual(classify_sph(125.5).key, "low")
        self.assertEqual(classify_sph(145.5).key, "optimal")
        self.assertEqual(classify_sph(146.0).key, "high")

    def test_industry_target_sits_in_the_optimal_band(self):
        """The 136-143 SPH industry window must lie wholly inside one band."""
        band = classify_sph(float(np.mean(INDUSTRY_TARGET_SPH)))
        self.assertEqual(band.key, "optimal")
        self.assertLessEqual(band.lo, INDUSTRY_TARGET_SPH[0])
        self.assertGreaterEqual(band.hi, INDUSTRY_TARGET_SPH[1])
        for target in INDUSTRY_TARGET_SPH:
            self.assertEqual(classify_sph(target).key, "optimal")


# --------------------------------------------------------------------------
# Scale: one conversion, both directions
# --------------------------------------------------------------------------


class TestScale(unittest.TestCase):
    def test_metre_pixel_roundtrip(self):
        scale = GroundScale(4.0)
        for metres in (0.5, 1.0, 7.2, 9.0, 42.0):
            self.assertAlmostEqual(scale.px_to_m(scale.m_to_px(metres)), metres, places=9)

    def test_known_hectare(self):
        # 100 m x 100 m at 4 cm/px is 2500 x 2500 px == 1.0 ha
        scale = GroundScale(4.0)
        poly = [(0, 0), (2500, 0), (2500, 2500), (0, 2500)]
        self.assertAlmostEqual(scale.px_area_to_ha(shoelace_area_px(poly)), 1.0, places=6)

    def test_rejects_bad_gsd(self):
        for bad in (0.0, -1.0, float("nan")):
            with self.assertRaises(ValueError):
                GroundScale(bad)

    def test_census_gsd_controls_pixel_params(self):
        """Coarser imagery must yield proportionally fewer pixels per metre."""
        standard = get_standard("mature")
        coarse = DetectorParams.from_standard(standard, GroundScale(8.0))
        fine = DetectorParams.from_standard(standard, GroundScale(4.0))
        self.assertAlmostEqual(coarse.min_spacing_px, fine.min_spacing_px / 2.0, places=6)
        self.assertAlmostEqual(
            coarse.rosette_radius_max_px, fine.rosette_radius_max_px / 2.0, places=6
        )

    def test_spacing_is_metre_derived_not_pixel_hardcoded(self):
        """The V1 mature preset used 68 px, which is only right near 13 cm/px."""
        standard = get_standard("mature")
        params = DetectorParams.from_standard(standard, GroundScale(4.0))
        # Derived from metres, not typed in: 0.75 x 9.0 m at 4 cm/px == 168.75 px,
        # 2.5x the V1 preset value of 68 px.
        self.assertAlmostEqual(
            params.min_spacing_px, GroundScale(4.0).m_to_px(standard.min_spacing_m),
            places=3,
        )
        self.assertGreater(params.min_spacing_px / 68.0, 2.0)


# --------------------------------------------------------------------------
# Tiling: exact cover, single ownership
# --------------------------------------------------------------------------


class TestTiling(unittest.TestCase):
    def _cases(self):
        return [
            ((0, 0, 2500, 2500), 1536, 192, (0, 0, 2500, 2500)),
            ((0, 0, 100, 100), 1536, 192, (0, 0, 2500, 2500)),
            ((0, 0, 3072, 1536), 1536, 192, (0, 0, 3072, 1536)),
            ((10, 20, 1500, 900), 512, 64, (0, 0, 4096, 4096)),
            ((0, 0, 9217, 14980), 2048, 256, (0, 0, 9217, 14980)),
            ((300, 400, 9000, 8600), 1536, 178, (0, 0, 9217, 14980)),
        ]

    def test_cores_cover_region_exactly(self):
        for region, tile_px, halo, image_bounds in self._cases():
            with self.subTest(region=region, tile=tile_px):
                tiles = plan_tiles(region, tile_px, halo, image_bounds)
                assert_exact_cover(region, tiles)

    def test_tile_cores_own_every_pixel_once(self):
        for region, tile_px, halo, image_bounds in self._cases():
            with self.subTest(region=region, tile=tile_px):
                tiles = plan_tiles(region, tile_px, halo, image_bounds)
                x0, y0, x1, y1 = region
                xs = np.linspace(x0 + 0.5, x1 - 0.5, 41)
                ys = np.linspace(y0 + 0.5, y1 - 0.5, 37)
                for px in xs:
                    for py in ys:
                        owners = [t for t in tiles if core_contains(t, float(px), float(py))]
                        self.assertEqual(
                            len(owners), 1,
                            f"point ({px:.1f},{py:.1f}) owned by {len(owners)} tiles",
                        )

    def test_read_window_contains_its_core(self):
        for region, tile_px, halo, image_bounds in self._cases():
            tiles = plan_tiles(region, tile_px, halo, image_bounds)
            for tile in tiles:
                rx0, ry0, rx1, ry1 = tile.read
                cx0, cy0, cx1, cy1 = tile.core
                self.assertLessEqual(rx0, cx0)
                self.assertLessEqual(ry0, cy0)
                self.assertGreaterEqual(rx1, cx1)
                self.assertGreaterEqual(ry1, cy1)

    def test_assert_exact_cover_detects_overlap(self):
        region = (0, 0, 1000, 1000)
        tiles = plan_tiles(region, 500, 32, (0, 0, 1000, 1000))
        broken = tiles + [Tile(read=tiles[0].read, core=(0, 0, 100, 100), index=99)]
        with self.assertRaises(AssertionError):
            assert_exact_cover(region, broken)


# --------------------------------------------------------------------------
# Spacing enforcement
# --------------------------------------------------------------------------


class TestSpacing(unittest.TestCase):
    def test_enforced_minimum_is_never_violated(self):
        rng = np.random.default_rng(7)
        for spacing in (20.0, 75.0, 157.5):
            pts = rng.uniform(0, 400, size=(600, 2))
            candidates = [Candidate(float(x), float(y), float(rng.random())) for x, y in pts]
            kept = enforce_min_spacing(candidates, spacing)
            if len(kept) < 2:
                continue
            closest = minimum_pairwise_distance([(c.x, c.y) for c in kept])
            self.assertGreaterEqual(closest, spacing - 1e-9)

    def test_spacing_is_monotonic_in_radius(self):
        rng = np.random.default_rng(11)
        candidates = [
            Candidate(float(x), float(y), float(rng.random()))
            for x, y in rng.uniform(0, 1200, size=(1500, 2))
        ]
        counts = [
            len(enforce_min_spacing(candidates, s)) for s in (25.0, 50.0, 100.0, 200.0)
        ]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_strongest_apex_wins(self):
        candidates = [
            Candidate(100.0, 100.0, 5.0),
            Candidate(101.0, 100.0, 90.0),
            Candidate(102.0, 100.0, 50.0),
        ]
        kept = enforce_min_spacing(candidates, 30.0)
        self.assertEqual(len(kept), 1)
        self.assertAlmostEqual(kept[0].peak, 90.0)


# --------------------------------------------------------------------------
# Measurement context: no per-tile normalisation
# --------------------------------------------------------------------------


class TestObservation(unittest.TestCase):
    def test_index_is_not_normalised_per_window(self):
        """
        The same pixel must report the same greenness whether it is measured
        inside a small window or inside the whole image.  V1's NORM_MINMAX made
        this false, which is why a fixed threshold meant different things on
        different tiles.
        """
        image = np.zeros((400, 600, 3), dtype=np.uint8)
        image[:, :] = (40, 70, 110)          # soil
        image[100:200, 100:200] = (30, 200, 50)   # bright canopy patch

        whole = vegetation_index(image, "exg")
        window = vegetation_index(image[80:260, 80:420], "exg")

        self.assertAlmostEqual(float(whole[150, 150]), 320.0, places=3)
        self.assertAlmostEqual(
            float(window[70, 70]), float(whole[150, 150]), places=6
        )

    def test_otsu_splits_bimodal_vegetation(self):
        rng = np.random.default_rng(3)
        soil = rng.normal(-20.0, 8.0, size=4000)
        canopy = rng.normal(220.0, 25.0, size=4000)
        values = np.concatenate([soil, canopy])
        threshold = otsu_threshold(values)
        self.assertGreater(threshold, 0.0)
        self.assertLess(threshold, 200.0)


# --------------------------------------------------------------------------
# End-to-end on a synthetic plantation of known density
# --------------------------------------------------------------------------


class TestSyntheticPlantation(unittest.TestCase):
    """Density recovery on a plantation whose true density is known exactly."""

    PITCH_M = 9.0
    GSD = 4.0
    COLS = ROWS = 10
    CROWN_RADIUS_M = 4.0

    @classmethod
    def setUpClass(cls):
        cls.scale = GroundScale(cls.GSD)
        cls.points = triangular_lattice(cls.PITCH_M, cls.COLS, cls.ROWS)
        cls.true_count = len(cls.points)
        cls.image, cls.polygon = render_plantation(
            cls.points, cls.scale,
            crown_radius_m=cls.CROWN_RADIUS_M,
            apex_radius_m=1.2,
            margin_m=cls.CROWN_RADIUS_M,
        )
        cls.true_area_ha = cls.scale.px_area_to_ha(shoelace_area_px(cls.polygon))
        cls.true_sph = cls.true_count / cls.true_area_ha

    def _census(self, min_spacing_px=None):
        config = CensusConfig(
            standard=get_standard("mature"),
            scale=self.scale,
            polygon=self.polygon,
            min_spacing_px=min_spacing_px,
        )
        return run_census(self.image, config)

    def test_recovers_the_true_palm_count(self):
        result = self._census()
        error = abs(result.total_palms - self.true_count) / self.true_count
        self.assertLess(
            error, 0.12,
            f"counted {result.total_palms} vs true {self.true_count} ({error:.1%})",
        )

    def test_recovers_the_true_stand_density(self):
        result = self._census()
        self.assertAlmostEqual(result.area_ha, self.true_area_ha, places=3)
        error = abs(result.sph - self.true_sph) / self.true_sph
        self.assertLess(
            error, 0.15,
            f"SPH {result.sph:.1f} vs true {self.true_sph:.1f} ({error:.1%})",
        )
        self.assertEqual(result.sph_band.key, "optimal")

    def test_spacing_invariant_holds(self):
        result = self._census()
        self.assertTrue(result.diagnostics["spacing_invariant_ok"])
        self.assertGreaterEqual(
            result.diagnostics["min_pairwise_distance_px"],
            result.min_spacing_px - 1e-6,
        )

    def test_no_palm_falls_outside_the_polygon(self):
        result = self._census()
        self.assertEqual(result.diagnostics["palms_outside_polygon"], 0)

    def test_slanted_polygon_does_not_leak_palms(self):
        """
        The per-tile ROI mask is rasterised, so ``cv2.fillPoly`` rounds its
        vertices.  On an axis-aligned rectangle that never matters; on a slanted
        boundary the raster and the survey polygon disagree by up to a pixel, so
        the exact geometric test has to be the one that decides membership.
        """
        height, width = self.image.shape[:2]
        triangle = (
            (60.0, 90.0),
            (float(width) - 55.0, 130.0),
            (float(width) / 2.0, float(height) - 70.0),
        )
        result = run_census(
            self.image,
            CensusConfig(
                standard=get_standard("mature"),
                scale=self.scale,
                polygon=triangle,
            ),
        )
        self.assertEqual(result.diagnostics["palms_outside_polygon"], 0)
        self.assertGreater(result.total_palms, 0)
        self.assertEqual(
            result.diagnostics["candidates_outside_polygon"]
            + result.diagnostics["candidates_suppressed_by_spacing"],
            result.diagnostics["candidates_raw"] - result.total_palms,
        )

    def test_rosette_radius_matches_the_rendered_ground_truth(self):
        """
        Every palm in this fixture was rendered as a 4.0 m canopy disc with a
        bright apex, so the measured rosette radius must land near 4.0 m.  V1
        could not do this at all: it returned ``max(15, int(min_dist * 0.45))``
        -- one constant for every palm, unrelated to any tree.
        """
        result = self._census()
        true_radius_px = self.scale.m_to_px(self.CROWN_RADIUS_M)
        measured = float(np.median([p.rosette_radius_px for p in result.palms]))
        error = abs(measured - true_radius_px) / true_radius_px
        self.assertLess(
            error, 0.25,
            f"median measured radius {measured:.1f} px vs true {true_radius_px:.1f} px "
            f"({error:.1%})",
        )
        self.assertGreater(
            len({round(p.rosette_radius_px, 3) for p in result.palms}), 1,
            "radii must come from measurement, not a constant",
        )

    def test_v1_pixel_spacing_overcounts_the_same_plantation(self):
        """
        The recorded defect: V1's 68 px minimum spacing is 2.72 m at 4 cm/px,
        about a third of a real pitch, so it counts frond-level apexes instead
        of palms.
        """
        v2 = self._census()
        v1_style = self._census(min_spacing_px=68.0)
        self.assertGreater(
            v1_style.total_palms, 2.0 * v2.total_palms,
            "the legacy pixel spacing should over-count by a large factor",
        )
        self.assertGreater(v1_style.sph, 1.8 * self.true_sph)

    def test_census_is_independent_of_tile_size(self):
        """Core ownership must make the count tile-layout independent."""
        results = []
        for tile_px in (512, 1024, 1536, 4096):
            config = CensusConfig(
                standard=get_standard("mature"),
                scale=self.scale,
                polygon=self.polygon,
                tile_px=tile_px,
            )
            results.append(run_census(self.image, config).total_palms)
        spread = (max(results) - min(results)) / max(results)
        self.assertLess(
            spread, 0.02,
            f"count varied with tile size: {results} (spread {spread:.2%})",
        )



class TestSpacingMeasurement(unittest.TestCase):
    """
    The spacing diagnostic must recover a spacing we know exactly, because it
    is the measurement used to judge whether a census resolved palms or frond
    clusters.
    """

    def test_nearest_neighbour_stats_recover_the_pitch(self):
        """The direct measurement: lattice points report their own pitch."""
        from palmsentinel.pipeline import nearest_neighbour_summary

        scale = GroundScale(4.0)
        points = triangular_lattice(9.0, 12, 12)
        points_px = [(scale.m_to_px(x), scale.m_to_px(y)) for x, y in points]
        summary = nearest_neighbour_summary(points_px, scale.m_per_px)
        self.assertAlmostEqual(summary["nn_median_m"], 9.0, delta=0.4)
        self.assertAlmostEqual(summary["nn_mode_m"], 9.0, delta=0.6)


class TestSensitivity(unittest.TestCase):
    """
    The sensitivity control lowers the region's vegetation threshold.

    The demo-class failure it must not cause is a count *collapse*: the bar
    drops, weaker crowns join, spacing suppression absorbs some -- but the
    accepted count must never fall below the plain-Otsu census.
    """

    def test_threshold_lowers_monotonically_and_count_never_collapses(self):
        from palmsentinel.pipeline import CensusConfig, run_census

        scale = GroundScale(4.0)
        points = triangular_lattice(9.0, 8, 8)
        image, polygon = render_plantation(
            points, scale, crown_radius_m=4.0, apex_radius_m=1.2,
            margin_m=4.0,
        )

        def census(sensitivity):
            return run_census(
                image,
                CensusConfig(
                    standard=get_standard("mature"),
                    scale=scale,
                    polygon=polygon,
                    sensitivity=sensitivity,
                ),
            )

        baseline = census(0.0)
        low = census(0.25)
        high = census(0.5)
        # The applied bar actually drops, and drops further as sensitivity rises.
        self.assertLess(low.observation.threshold, baseline.observation.threshold)
        self.assertLess(high.observation.threshold, low.observation.threshold)
        self.assertEqual(low.observation.sensitivity, 0.25)
        self.assertEqual(baseline.observation.sensitivity, 0.0)
        # Suppression may absorb added candidates, but the census may not lose
        # palms it had already found at the stricter bar.
        self.assertGreaterEqual(low.total_palms, baseline.total_palms)


class TestDenseStandard(unittest.TestCase):
    """The dense-mature standard: same grid assumption and crown optics as
    mature, only the exclusion fraction relaxed (0.65 -> 5.85 m floor).

    For compact blocks where mature crowns stand tighter than the textbook
    grid: admits strong neighbours the 6.75 m floor deletes, while staying
    above the ~4 m frond-structure band where the count collapses (measured
    elbow). Opt-in per survey zone; the default standard is untouched.
    """

    def test_floor_sits_inside_the_physical_window(self):
        dense = get_standard("dense")
        mature = get_standard("mature")
        self.assertGreater(dense.min_spacing_m, 4.0)
        self.assertLess(dense.min_spacing_m, 7.8)
        self.assertAlmostEqual(dense.min_spacing_m, 5.85)
        self.assertEqual(dense.blur_m, mature.blur_m)
        self.assertEqual(dense.peak_separation_m, mature.peak_separation_m)
        self.assertEqual(dense.expected_spacing_m, mature.expected_spacing_m)

    def test_admits_more_than_mature_without_leaving_crown_scale(self):
        from palmsentinel.pipeline import nearest_neighbour_summary

        image = cv2.imread(os.path.join("data", "demo_palm_estate.jpg"),
                           cv2.IMREAD_COLOR)
        self.assertIsNotNone(image, "bundled demo orthomosaic missing")
        scale = GroundScale(4.0)
        results = {}
        for key in ("mature", "dense"):
            result = run_census(
                image,
                CensusConfig(standard=get_standard(key), scale=scale),
            )
            points = [(p.x_px, p.y_px) for p in result.palms]
            nn = nearest_neighbour_summary(points, scale.m_per_px)
            results[key] = (result, nn)
        mature, dense = results["mature"], results["dense"]
        self.assertGreater(dense[0].total_palms, mature[0].total_palms)
        self.assertGreaterEqual(nn_mode(dense[1]), 6.0)
        self.assertTrue(dense[0].diagnostics["spacing_invariant_ok"])

    def test_no_regression_on_uniform_synthetic_stands(self):
        from synthetic import jittered_lattice

        scale = GroundScale(4.0)
        points = jittered_lattice(9.0, 14, 14, jitter_m=0.0, seed=7)
        image, poly = render_plantation(points, scale, crown_radius_m=3.0,
                                        apex_radius_m=1.0, margin_m=12.0)
        counts = {}
        for key in ("mature", "dense"):
            counts[key] = run_census(
                image,
                CensusConfig(standard=get_standard(key), scale=scale,
                             polygon=poly),
            ).total_palms
        self.assertAlmostEqual(counts["dense"] / counts["mature"], 1.0,
                               delta=0.03)


def nn_mode(nn):
    return float(nn.get("nn_mode_m", 0.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
