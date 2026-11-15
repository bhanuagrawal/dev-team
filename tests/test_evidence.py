"""A shipped entry must be backed by a commit under its target."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import evidence


class ShippedEvidenceTest(unittest.TestCase):
    def test_a_commit_under_the_target_counts(self):
        self.assertTrue(evidence.touched(
            ["composeApp/src/commonMain/Foo.kt"], "composeApp/src/commonMain/**"))

    def test_a_commit_somewhere_else_does_not(self):
        self.assertFalse(evidence.touched(
            ["distribution/whatsnew/whatsnew-en-US"], "composeApp/src/commonMain/**"))

    def test_no_target_means_nothing_to_prove(self):
        self.assertTrue(evidence.touched(["anything"], ""))

    def test_prefix_stops_at_the_first_glob(self):
        self.assertEqual("composeApp/src", evidence.target_prefix("composeApp/src/**"))


if __name__ == "__main__":
    unittest.main()
