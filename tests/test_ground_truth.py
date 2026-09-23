"""
Is the census number *true*, or fitted to the image it was tuned on?

:mod:`tests.test_core` already proves the count is self-consistent: that it
recovers a synthetic plantation's density, that tiling owns every pixel once, and
that V1's pixel-derived spacing over-counts.  It cannot prove the number is true,
because its fixture renders its palms at a 9.0 m pitch and 4 cm/px -- the same two
quantities whose correctness is in question.

These tests ask a different question and use a fixture built differently: a
triangular planting of arithmetic density whose survey patch is chosen by geometry
alone (``tests/ground_truth.py``), swept over true pitch, planting error, ground
sample distance, tile size and frame fraction.  The exclusion radius --
``min_spacing_fraction`` of the declared pitch, 0.75 x 9.0 m = 6.75 m -- was
chosen while one image's result was being watched, so what is asserted here is
(a) that it is a model parameter rather than a fit, and (b) exactly where it stops
being valid.  The sweeps that produced these thresholds, and the tables, are in
``tests/ground_truth.py`` and in the V2 decision log.
"""

from __future__ import annotations

import unittest

import ground_truth as gt
from synthetic import nominal_sph

#: The density the mature standard *is*: a 9.0 m x 7.8 m estate pattern, which is
#: 142.5 SPH whether read as rectangular or as a 9.0 m triangular pitch.
INDUSTRY_SPH = 10000.0 / (9.0 * 7.8)

#: The number the published demo reports, and therefore the number an operator
#: would take as "correct" if nothing else were known.
CALIBRATED_SPH = 140.0


class TestRecoversTruthWhereTheStandardApplies(unittest.TestCase):
    def test_nominal_density_is_the_industry_standard(self) -> None:
        """
        The ground truth's arithmetic has to agree with the quoted standard.

        A 9.0 m triangular pitch and a 9.0 m x 7.8 m rectangular pattern are the
        same estate standard read two ways, so they must land on the same density
        to well inside a tenth of a percent -- they differ by 0.07%.
        """
        self.assertLess(abs(nominal_sph(9.0) - INDUSTRY_SPH) / INDUSTRY_SPH, 0.001)

    def test_recovers_truth_at_the_declared_pitch(self) -> None:
        """9.0 m planting, 1.0 m of planting error: the number is the truth."""
        item = gt.recover(9.0, jitter_m=1.0, crown_pitch_m=gt.STANDARD_PITCH_M)
        self.assertLessEqual(abs(item.sph_error), 0.02, item)

    def test_recovers_truth_on_a_surveyed_lattice(self) -> None:
        """Nothing planted off its mark: still exact."""
        item = gt.recover(9.0, jitter_m=0.0, crown_pitch_m=gt.STANDARD_PITCH_M)
        self.assertLessEqual(abs(item.sph_error), 0.02, item)

    def test_tracks_density_across_a_factor_of_two(self) -> None:
        """Two densities 2.3x apart, both recovered -- so no constant is echoed."""
        sparse = gt.recover(12.0, crown_pitch_m=gt.STANDARD_PITCH_M)
        dense = gt.recover(8.0, crown_pitch_m=gt.STANDARD_PITCH_M)
        for item in (sparse, dense):
            self.assertLessEqual(abs(item.sph_error), 0.02, item)
        self.assertGreater(dense.recovered_sph - sparse.recovered_sph, 90.0)
        self.assertNotAlmostEqual(dense.recovered_sph, CALIBRATED_SPH, delta=10.0)


class TestTheFractionIsAModelParameter(unittest.TestCase):
    def test_count_is_flat_in_the_fraction(self) -> None:
        """
        The heart of it: 0.70, 0.75 and 0.80 must agree.

        If the answer were produced by the fraction rather than by the image, the
        count would slide with every step.  On real ground truth it does not.
        """
        for fraction in (0.70, 0.75, 0.80):
            with self.subTest(fraction=fraction):
                item = gt.recover(9.0, jitter_m=1.0, fraction=fraction,
                                  crown_pitch_m=gt.STANDARD_PITCH_M)
                self.assertLessEqual(abs(item.sph_error), 0.02, item)

    def test_the_flat_window_has_a_low_edge(self) -> None:
        """Too small a radius leaves duplicate apexes standing: it over-counts."""
        item = gt.recover(9.0, jitter_m=0.0, fraction=0.50,
                          crown_pitch_m=gt.STANDARD_PITCH_M)
        self.assertGreater(item.sph_error, 0.02, item)

    def test_the_flat_window_has_a_high_edge(self) -> None:
        """Too large a radius merges genuine neighbours: it under-counts."""
        item = gt.recover(9.0, jitter_m=0.0, fraction=0.90,
                          crown_pitch_m=gt.STANDARD_PITCH_M)
        self.assertLess(item.sph_error, -0.04, item)


class TestInvariance(unittest.TestCase):
    def test_tiling_does_not_change_the_count(self) -> None:
        small = gt.recover(9.0, jitter_m=1.0, tile_px=512,
                           crown_pitch_m=gt.STANDARD_PITCH_M)
        whole = gt.recover(9.0, jitter_m=1.0, tile_px=gt.SINGLE_TILE,
                           crown_pitch_m=gt.STANDARD_PITCH_M)
        self.assertEqual(small.recovered, whole.recovered)

    def test_ground_sample_distance_does_not_change_the_density(self) -> None:
        """
        Area scales with GSD squared, so SPH inherits any GSD error twice over.

        Recovering the same density at 8 cm/px and at 4 cm/px is the evidence that
        the pixel <-> metre path is consistent end to end.
        """
        coarse = gt.recover(9.0, jitter_m=1.0, gsd_cm=8.0,
                            crown_pitch_m=gt.STANDARD_PITCH_M)
        fine = gt.recover(9.0, jitter_m=1.0, gsd_cm=4.0,
                          crown_pitch_m=gt.STANDARD_PITCH_M)
        for item in (coarse, fine):
            self.assertLessEqual(abs(item.sph_error), 0.02, item)


class TestTheNumberAloneIsNotEvidence(unittest.TestCase):
    """
    The limits of the number, asserted so they cannot quietly be forgotten.

    These are not failures of the pipeline; they are the domain in which the
    answer means what it says, and the reason the demo's own result cannot be
    quoted as proof.
    """

    def test_a_denser_block_can_report_the_calibrated_answer(self) -> None:
        """
        The 140.0 SPH coincidence, at the density where it means nothing.

        A block planted 8.0 m apart with 1.5 m of planting error is 180.4 SPH
        true.  The mature standard reads it as 140.0 SPH -- indistinguishable,
        from the number alone, from the demo's own published result.
        """
        item = gt.recover(8.0, jitter_m=1.5, crown_pitch_m=gt.STANDARD_PITCH_M)
        self.assertGreater(item.true_sph, 175.0, item)
        self.assertAlmostEqual(item.recovered_sph, CALIBRATED_SPH, delta=2.0)
        self.assertLess(item.sph_error, -0.15, item)

    def test_a_tighter_than_declared_block_is_undercounted(self) -> None:
        """The measured edge of the domain: 11% tighter than declared, -13%."""
        item = gt.recover(8.0, jitter_m=1.0, crown_pitch_m=gt.STANDARD_PITCH_M)
        self.assertLess(item.sph_error, -0.10, item)

    def test_a_dense_block_is_undercounted_by_the_mature_standard(self) -> None:
        """
        A 235.7 SPH block read with the mature standard loses a third of itself.

        This is what "declare the right standard" means in practice; there is no
        detection of a mis-declared pitch anywhere in the census.
        """
        item = gt.recover(7.0, crown_pitch_m=gt.STANDARD_PITCH_M)
        self.assertLess(item.sph_error, -0.25, item)


if __name__ == "__main__":
    unittest.main()
