"""The index is a place a change can hide — `changed_files` must look there.

The defect this closes (2026-11-15, one run): a developer ran `git add -A`
before `devteam verify`, every change sat in the INDEX, and `changed_files`
asked only `git diff base HEAD` (committed) and `git diff` (unstaged). The
gates judged a 27-file change as "scope ok (2 files)" and the ux-flow gate
passed journeys for a screen it had never seen, so the run recorded a PASS on
a tree nobody had judged. Staging is the normal habit — `release` stages with
`git add -A` itself — so this is one command away on every run.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from team import verify


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


def _repo(td: str) -> Path:
    repo = Path(td) / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "agent@test")
    _git(repo, "config", "user.name", "agent")
    (repo / "a.txt").write_text("a\n")
    (repo / "b.txt").write_text("b\n")
    (repo / "clean.txt").write_text("clean\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")
    return repo


class ChangedFilesIndexTests(unittest.TestCase):
    def test_staged_change_is_visible(self):
        """The outage: `git add -A`, nothing unstaged, no commit yet."""
        with tempfile.TemporaryDirectory() as td:
            repo = _repo(td)
            (repo / "a.txt").write_text("changed\n")
            _git(repo, "add", "-A")
            # Nothing unstaged and nothing committed — the old code missed it.
            self.assertEqual(verify.changed_files(repo, "HEAD"), ["a.txt"])

    def test_every_place_a_change_can_live_is_seen(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _repo(td)
            (repo / "a.txt").write_text("staged\n")
            (repo / "b.txt").write_text("unstaged\n")
            (repo / "new.txt").write_text("untracked\n")
            _git(repo, "add", "a.txt")
            self.assertEqual(verify.changed_files(repo, "HEAD"),
                             ["a.txt", "b.txt", "new.txt"])

    def test_clean_tree_reports_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _repo(td)
            self.assertEqual(verify.changed_files(repo, "HEAD"), [])


class VerifyVerdictCoverageTests(unittest.TestCase):
    """`release` reads a PASS only for the bytes it was measured on."""

    def test_no_record_blocks_the_release(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _repo(td)
            self.assertIn("no verify recorded", verify.release_blocker({}, repo))

    def test_pass_survives_an_unchanged_tree(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _repo(td)
            rec = {"worktree": str(repo), "ok": True,
                   "fingerprint": verify.tree_fingerprint(repo)}
            self.assertEqual(verify.release_blocker({"last_verify": rec}, repo), "")

    def test_an_edit_after_verify_invalidates_the_pass(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _repo(td)
            rec = {"worktree": str(repo), "ok": True,
                   "fingerprint": verify.tree_fingerprint(repo)}
            (repo / "clean.txt").write_text("edited after the walk\n")
            self.assertIn("changed after its last verify",
                          verify.release_blocker({"last_verify": rec}, repo))

    def test_a_pass_on_another_tree_does_not_transfer(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _repo(td)
            other = Path(td) / "other"
            other.mkdir()
            rec = {"worktree": str(other), "ok": True,
                   "fingerprint": verify.tree_fingerprint(repo)}
            self.assertIn("different tree",
                          verify.release_blocker({"last_verify": rec}, repo))

    def test_a_failed_verify_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _repo(td)
            rec = {"worktree": str(repo), "ok": False,
                   "fingerprint": verify.tree_fingerprint(repo)}
            self.assertIn("FAILED", verify.release_blocker({"last_verify": rec}, repo))


if __name__ == "__main__":
    unittest.main()
