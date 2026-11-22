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

    def test_the_bump_is_not_evidence_but_the_carrying_commit_is(self):
        # One release carries several commits; the last one is a version bump
        # that touches one file. The row must name the commit under its target.
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True,
                                            capture_output=True, text=True)
            git("init", "-q")
            git("config", "user.email", "t@example.invalid")
            git("config", "user.name", "t")
            (root / "composeApp" / "src" / "commonMain").mkdir(parents=True)
            (root / "composeApp" / "src" / "commonMain" / "Screen.kt").write_text("deal\n")
            git("add", ".")
            git("commit", "-qm", "the deal")
            code = git("rev-parse", "HEAD").stdout.strip()
            (root / "version.txt").write_text("1.0.89\n")
            git("add", ".")
            git("commit", "-qm", "bump")
            bump = git("rev-parse", "HEAD").stdout.strip()

            self.assertEqual(code, evidence.commit_for_target(
                root, bump, "composeApp/src/commonMain/**"))
            self.assertEqual(bump, evidence.commit_for_target(root, bump, "version.txt"))
            self.assertEqual("not-a-sha", evidence.commit_for_target(
                root, "not-a-sha", "version.txt"))


if __name__ == "__main__":
    unittest.main()
