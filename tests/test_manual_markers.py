"""
Operator corrections.

A census of a real block is always verified on the ground, so the tool must
accept a correction. The risk in adding that is obvious: a second, separate
counting path, and a report that no longer matches its own export.

These tests pin the property that makes it safe -- a manual point is a
:class:`~palmsentinel.detection.Candidate` like any other, so it passes through
the same region, the same spacing rule and the same coordinate conversion as a
detected apex, and the invariants therefore still hold over the final palm set.
"""

from __future__ import annotations

import unittest

from palmsentinel.agronomy import get_standard
from palmsentinel.pipeline import CensusConfig, run_census
from palmsentinel.scale import GroundScale
from synthetic import sparse_plantation, to_px


class TestManualMarkers(unittest.TestCase):
    GSD = 4.0

    @classmethod
    def setUpClass(cls):
        cls.scale = GroundScale(cls.GSD)
        cls.image, cls.polygon, cls.origin = sparse_plantation(cls.scale)
        cls.spacing_px = cls.scale.m_to_px(
            get_standard("mature").min_spacing_m
        )

    def census(self, **kwargs):
        return run_census(
            self.image,
            CensusConfig(
                standard=get_standard("mature"),
                scale=self.scale,
                polygon=self.polygon,
                **kwargs,
            ),
        )

    def px(self, mx, my):
        return to_px(self.origin, self.scale, mx, my)

    # -- baseline -------------------------------------------------------------

    def test_baseline_finds_the_planted_palms(self):
        result = self.census()
        self.assertEqual(result.total_palms, 4)
        self.assertTrue(result.diagnostics["spacing_invariant_ok"])

    # -- additions ------------------------------------------------------------

    def test_addition_in_open_ground_is_counted_once(self):
        """A palm an operator can see, in a gap the detector left alone."""
        result = self.census(manual_additions=(self.px(35.0, 35.0),))
        self.assertEqual(result.total_palms, 5)
        self.assertEqual(result.diagnostics["manual_additions"], 1)
        self.assertEqual(result.diagnostics["manual_additions_rejected"], 0)

        manual = [p for p in result.palms if p.manual]
        self.assertEqual(len(manual), 1)
        x_m, y_m = manual[0].x_m, manual[0].y_m
        self.assertAlmostEqual(x_m, 30.0, delta=0.1)
        self.assertAlmostEqual(y_m, 30.0, delta=0.1)

    def test_addition_on_top_of_a_recorded_palm_is_rejected(self):
        """
        An addition may only fill a gap. Placing one where a palm is already
        recorded is refused rather than quietly displacing the detection,
        because "two palms are 1.4 m apart" is never the right answer.
        """
        result = self.census(manual_additions=(self.px(21.0, 21.0),))
        self.assertEqual(result.total_palms, 4)
        self.assertEqual(result.diagnostics["manual_additions"], 0)
        self.assertEqual(result.diagnostics["manual_additions_rejected"], 1)
        self.assertTrue(result.diagnostics["spacing_invariant_ok"])

    def test_removing_then_adding_moves_a_palm(self):
        """
        A correction is a deletion and an addition, composed in that order. The
        freed ground is why the addition is accepted here but refused above.
        """
        result = self.census(
            manual_removals=(self.px(20.0, 20.0),),
            manual_additions=(self.px(21.5, 21.5),),
        )
        self.assertEqual(result.total_palms, 4)
        self.assertEqual(result.diagnostics["manual_additions"], 1)
        self.assertEqual(result.diagnostics["detections_removed_by_operator"], 1)
        self.assertTrue(result.diagnostics["spacing_invariant_ok"])

    def test_addition_outside_the_image_is_ignored(self):
        result = self.census(manual_additions=((-50.0, -50.0),))
        self.assertEqual(result.diagnostics["manual_additions"], 0)
        self.assertEqual(result.total_palms, 4)

    # -- removals -------------------------------------------------------------

    def test_removal_drops_exactly_the_palm_pointed_at(self):
        result = self.census(manual_removals=(self.px(20.0, 20.0),))
        self.assertEqual(result.total_palms, 3)
        self.assertEqual(result.diagnostics["detections_removed_by_operator"], 1)

    def test_removal_does_not_touch_a_distant_palm(self):
        point = self.px(20.0, 20.0)
        result = self.census(manual_removals=(point,))
        nearest = min(
            result.palms,
            key=lambda p: (p.x_px - point[0]) ** 2 + (p.y_px - point[1]) ** 2,
        )
        self.assertGreater(
            ((nearest.x_px - point[0]) ** 2 + (nearest.y_px - point[1]) ** 2) ** 0.5,
            self.spacing_px,
        )

    def test_removal_in_empty_ground_is_harmless(self):
        result = self.census(manual_removals=(self.px(35.0, 35.0),))
        self.assertEqual(result.total_palms, 4)
        self.assertEqual(result.diagnostics["detections_removed_by_operator"], 0)

    # -- interactions ---------------------------------------------------------

    def test_a_deletion_never_removes_a_hand_placed_palm(self):
        """Deletions act on detections only, so they cannot undo an addition."""
        point = self.px(35.0, 35.0)
        result = self.census(
            manual_additions=(point,),
            manual_removals=(point,),
        )
        self.assertEqual(result.total_palms, 5)
        self.assertEqual(result.diagnostics["detections_removed_by_operator"], 0)
        self.assertEqual(result.diagnostics["manual_additions"], 1)

    def test_manual_and_detected_palms_share_one_coordinate_space(self):
        """
        A manual point's reported metres are its own pixel position converted
        once. If a second conversion path existed, this would disagree.
        """
        point = self.px(35.0, 35.0)
        result = self.census(manual_additions=(point,))
        manual = next(p for p in result.palms if p.manual)
        self.assertAlmostEqual(manual.x_px, point[0], places=6)
        self.assertAlmostEqual(manual.x_m, self.scale.px_to_m(point[0]), places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
