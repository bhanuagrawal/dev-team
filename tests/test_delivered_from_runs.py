"""`already_delivered` must read BOTH task-id shapes in the run ledger.

A queue entry that outlives its release is the plan's most misleading kind of
wrong: the review announces it as "this run builds X" and `pick` would rebuild
work main already has. Two shapes reach `runs`:
  - `dt-<label-slug>-<seq>` minted by `pick` (sequence suffix is not identity),
  - the admitted queue label itself (`bugfix-<words>-<date>`), written when the
    shipped run carried no task spec.

Observed 2026-11-22 on BookSwapKMP: `bugfix-barcode-scanning-working-but-2026-11-21`
shipped on 2026-11-21 (commit 9cc9c9c) with no shipped-ledger row, and because
the run-ledger belt skipped every id that did not start with `dt-`, the entry
came back as the queue head — pinned, so the planner could not even drop it.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import scan


class DeliveredFromRunsTest(unittest.TestCase):
    def _state(self, task_id, result="shipped"):
        return {"runs": [{"task_id": task_id, "result": result}], "shipped": []}

    def test_pick_id_with_sequence_suffix_is_delivered(self):
        q = {"label": "visual-wrap-complete-end-end-2026-11-07"}
        self.assertTrue(scan.already_delivered(
            q, self._state("dt-visual-wrap-complete-end-end-2-002")))

    def test_admitted_label_id_is_delivered(self):
        q = {"label": "bugfix-barcode-scanning-working-but-2026-11-21"}
        self.assertTrue(scan.already_delivered(q, self._state(q["label"])))

    def test_a_blocked_run_delivers_nothing(self):
        q = {"label": "bugfix-barcode-scanning-working-but-2026-11-21"}
        self.assertFalse(scan.already_delivered(
            q, self._state(q["label"], result="blocked")))

    def test_an_unrelated_id_leaves_the_entry_queued(self):
        q = {"label": "bugfix-the-shelf-forgets-what-the-reader-set-2026-11-20"}
        self.assertFalse(scan.already_delivered(
            q, self._state("bugfix-barcode-scanning-working-but-2026-11-21")))

    def test_empty_task_id_is_not_a_match(self):
        self.assertFalse(scan.already_delivered({"label": "x"}, self._state("")))


if __name__ == "__main__":
    unittest.main()
