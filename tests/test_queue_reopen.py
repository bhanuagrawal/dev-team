"""A task whose label shipped once can be re-opened explicitly."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import queue


class ReopenShippedTaskTest(unittest.TestCase):
    def _old(self):
        return {"label": "feature-offer-screen", "type": "feature",
                "target": "composeApp/**", "advances": "m1"}

    def test_a_shipped_label_is_still_obsolete_without_an_explicit_supersede(self):
        verdict, why, _ = queue.verdict_for(
            self._old(), [], shipped=set(), shipped_labels={queue._norm("feature-offer-screen")})
        self.assertEqual("redundant", verdict)

    def test_an_explicit_supersede_reopens_it(self):
        incoming = [{"label": "feature-offer-screen", "type": "feature",
                     "target": "composeApp/**", "advances": "m1",
                     "supersedes": ["feature-offer-screen"],
                     "reason": "the shipped entry had unmet acceptance"}]
        verdict, why, by = queue.verdict_for(
            self._old(), incoming, shipped=set(),
            shipped_labels={queue._norm("feature-offer-screen")})
        self.assertEqual("superseded", verdict)
        self.assertEqual("feature-offer-screen", by)


if __name__ == "__main__":
    unittest.main()
