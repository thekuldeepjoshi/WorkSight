# Unit tests for package activity and staffing rules.

import unittest

from worksight.rules import package_activity_signals, staffing_pressure


class PackageActivityRuleTests(unittest.TestCase):
    """Check that package evidence creates the expected worker cues."""

    def classify(self, *, count=0, carrying=False, hand=False, approaching=False):
        """Call the rule with readable test defaults."""
        return package_activity_signals(count, carrying, hand, approaching)

    def test_available_only_when_belt_is_empty_and_worker_is_not_carrying(self):
        result = self.classify()
        self.assertTrue(result["candidate_available_no_belt_demand"])
        self.assertFalse(self.classify(carrying=True)["candidate_available_no_belt_demand"])
        self.assertFalse(self.classify(count=1)["candidate_available_no_belt_demand"])

    def test_carrying_a_package_is_candidate_work_even_when_belt_is_empty(self):
        result = self.classify(carrying=True)
        self.assertTrue(result["candidate_working_with_package"])
        self.assertFalse(result["candidate_available_no_belt_demand"])

    def test_bottleneck_excludes_carrying_hand_activity_and_approach(self):
        self.assertTrue(self.classify(count=1)["candidate_belt_bottleneck"])
        self.assertFalse(self.classify(count=1, carrying=True)["candidate_belt_bottleneck"])
        self.assertFalse(self.classify(count=1, hand=True)["candidate_belt_bottleneck"])
        self.assertFalse(self.classify(count=1, approaching=True)["candidate_belt_bottleneck"])

    def test_full_belt_waiting_requires_four_packages_and_no_hand_or_carry_signal(self):
        self.assertTrue(self.classify(count=4)["candidate_waiting_full_belt"])
        self.assertFalse(self.classify(count=3)["candidate_waiting_full_belt"])
        self.assertFalse(self.classify(count=4, hand=True)["candidate_waiting_full_belt"])
        self.assertFalse(self.classify(count=4, carrying=True)["candidate_waiting_full_belt"])

class StaffingPressureRuleTests(unittest.TestCase):
    """Check the minimum package and active-worker thresholds."""
    def test_requires_at_least_three_packages_and_two_active_workers(self):
        self.assertTrue(staffing_pressure(3, 2))
        self.assertFalse(staffing_pressure(2, 2))
        self.assertFalse(staffing_pressure(3, 1))


if __name__ == "__main__":
    unittest.main()
