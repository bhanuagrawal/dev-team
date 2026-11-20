"""The change must have been run on a device — not every journey re-walked per commit.

Owner rule 2026-11-20 ("jo banaya hai usko emulator pe test karo, fir release"),
narrowed on 2026-11-21: *"wo ux flow run wala rule hata do … current change jo
kiya hai wo emulator pe run karne wala laga do"*.

So the gate asks one question: is there a walk newer than the newest code file
this diff touched? A walk is not tied to a commit id, because a rebase or a merge
changes every sha without changing a single pixel anyone watched. Docs, the
journey spec, store copy and images are not code a phone executes.

These tests pin the reader at its cheapest point, so a regression is a failing
test and not another release nobody watched.
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from team import ux_flow, verify

CODE = "composeApp/src/commonMain/kotlin/Place.kt"


class ChangeWalkedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        spec = self.root / "docs" / "ux-flows.json"
        spec.parent.mkdir(parents=True)
        spec.write_text('{"flows": [{"id": "search", "surface": "Discovery", '
                        '"goal": "find a book"}]}')
        self.code = self.root / CODE
        self.code.parent.mkdir(parents=True)
        self.code.write_text("class Place\n")
        self.flow = {"id": "search", "surface": "Discovery", "goal": "find a book"}
        self.flows = [self.flow]

    def tearDown(self):
        self.tmp.cleanup()

    def _walk(self, age: float) -> Path:
        out = ux_flow.run_dir(self.root) / ux_flow._slug("search")
        out.mkdir(parents=True, exist_ok=True)
        result = out / "result.json"
        result.write_text('{"goal_reached": true}')
        stamp = time.time() - age
        os.utime(result, (stamp, stamp))
        return result

    def _touch(self, path: Path, age: float) -> None:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))

    def test_a_docs_only_diff_needs_no_walk(self):
        self.assertEqual(
            verify._change_walked_problem(self.root, self.root,
                                          ["docs/ux-flows.json", "README.md"],
                                          self.flows),
            None)

    def test_a_walk_that_watched_the_change_passes(self):
        self._touch(self.code, age=600)
        self._walk(age=60)
        self.assertIsNone(
            verify._change_walked_problem(self.root, self.root, [CODE], self.flows))

    def test_a_walk_older_than_the_change_is_refused(self):
        self._walk(age=600)
        self._touch(self.code, age=60)
        problem = verify._change_walked_problem(self.root, self.root, [CODE], self.flows)
        self.assertIn(CODE, problem or "")
        self.assertIn("has not been run on a device", problem or "")

    def test_no_walk_at_all_is_refused_for_a_code_change(self):
        problem = verify._change_walked_problem(self.root, self.root, [CODE], self.flows)
        self.assertIn("has not been run on a device", problem or "")

    def test_a_walk_that_named_another_commit_still_counts(self):
        # The commit-bound rule is gone (owner, 2026-11-21): a rebase changes the
        # sha without changing anything the walk watched.
        self._touch(self.code, age=600)
        out = ux_flow.run_dir(self.root) / ux_flow._slug("search")
        out.mkdir(parents=True, exist_ok=True)
        result = out / "result.json"
        result.write_text('{"goal_reached": true, "commit": "0" * 40}')
        stamp = time.time() - 60
        os.utime(result, (stamp, stamp))
        self.assertIsNone(
            verify._change_walked_problem(self.root, self.root, [CODE], self.flows))

    def test_test_and_spec_files_are_not_code_a_device_runs(self):
        for rel in ("tests/test_x.py", "docs/evidence/run.md", ".pipeline/x.json",
                    "distribution/whatsnew/en-US"):
            self.assertEqual(
                verify._change_walked_problem(self.root, self.root, [rel], self.flows), None,
                rel)


if __name__ == "__main__":
    unittest.main()
