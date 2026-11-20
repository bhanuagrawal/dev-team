"""A walk is evidence about the build it walked, never about an older one.

Owner rule 2026-11-20 ("jo banaya hai usko emulator pe test karo, fir release"),
after BookSwap shipped a search no device had ever run: four green walks from
earlier builds vouched for a build none of them had opened, because the gate only
re-walked a journey when the diff touched a file its surface declared.

These tests pin the rule at its cheapest point — the gate's own reader — so a
regression is a failing test and not another release nobody watched.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from team import ux_flow, verify


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()


class WalkCommitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "t")
        (self.root / "a.txt").write_text("one\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "one")
        self.head = _git(self.root, "rev-parse", "HEAD")
        self.flow = {"id": "search", "surface": "Discovery", "goal": "find a book"}

    def tearDown(self):
        self.tmp.cleanup()

    def _walk(self, data: dict) -> None:
        out = ux_flow.run_dir(self.root) / ux_flow._slug(self.flow["id"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(json.dumps(data))

    def test_tree_commit_is_the_trees_head(self):
        self.assertEqual(self.head, ux_flow.tree_commit(self.root))

    def test_a_tree_without_git_names_no_commit(self):
        with tempfile.TemporaryDirectory() as bare:
            self.assertEqual("", ux_flow.tree_commit(Path(bare)))

    def test_a_walk_of_this_commit_passes(self):
        self._walk({"commit": self.head, "goal_reached": True})
        self.assertIsNone(
            verify._walk_commit_problem(self.root, self.flow, self.root))

    def test_a_walk_of_an_older_build_is_refused(self):
        self._walk({"commit": "0" * 40, "goal_reached": True})
        problem = verify._walk_commit_problem(self.root, self.flow, self.root)
        self.assertIn("this tree is", problem or "")
        self.assertIn(self.head[:12], problem or "")

    def test_a_walk_that_does_not_say_what_it_walked_is_refused(self):
        # Every result recorded before this rule has no `commit`; a green one of
        # those is exactly the pass that must not count.
        self._walk({"goal_reached": True})
        problem = verify._walk_commit_problem(self.root, self.flow, self.root)
        self.assertIn("does not name the build", problem or "")


if __name__ == "__main__":
    unittest.main()
