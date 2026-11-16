"""A delivered version must stop blocking, whoever delivered it.

`pending_delivery` exists so a billed-out account cannot pile tag on tag. It
used to accept exactly one proof of delivery: a GREEN CI run for the tag's
commit. That made a version delivered by any other route — a self-hosted
runner, a re-run that was refused, an upload made by hand — block the next
release forever.

Observed 2026-11-16 (BookSwapKMP v1.0.71): every GitHub-hosted job was refused
for billing ("recent account payments have failed"), the same commit was then
built and uploaded to `internal` from the owner's Mac, Play reported
versionCode 10113, and the next `release` still refused. The store is the
authority: this pins that the store's answer wins, and that a version Play does
NOT hold still blocks.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import shipper


GRADLE = """\
        versionCode = 10113
        versionName = "1.0.71"
"""

KTS_INCREMENT = """\
            versionCodeOverride = versionCode.toInt()
            versionCode = 42
"""


class VersionCodeReadingTest(unittest.TestCase):
    """The pipeline reads the literal the same way the CI template does."""

    def test_gradle_and_kts_literals_read_the_same(self):
        self.assertEqual(shipper.version_code_in(GRADLE), 10113)
        self.assertEqual(shipper.version_code_in("versionCode = 10113"), 10113)
        self.assertEqual(shipper.version_code_in("versionCode 7"), 7)

    def test_an_override_expression_does_not_win_over_the_literal(self):
        # `versionCodeOverride = versionCode.toInt()` is an identifier, not a
        # number; the last standalone number on the line is the one that ships.
        self.assertEqual(shipper.version_code_in(KTS_INCREMENT), 42)

    def test_no_version_file_shape_gives_no_answer(self):
        self.assertIsNone(shipper.version_code_in("applicationId = \"x\""))
        self.assertIsNone(shipper.version_code_in(""))


class PendingDeliveryTest(unittest.TestCase):
    """Which evidence clears the block, and which does not."""

    def _run(self, floor, code=10113, conclusion="failure", blocked=True):
        latest = {"version": "1.0.71"}
        runs = [{"headSha": "9f2ca14", "conclusion": conclusion,
                 "databaseId": 43726028911}]
        with mock.patch.object(shipper, "profile_of",
                               return_value={"version_file": "v.gradle",
                                             "repo_slug": "o/r",
                                             "ci_workflow": "release.yml"}), \
             mock.patch.object(shipper, "remote_latest_release", return_value=latest), \
             mock.patch.object(shipper, "git",
                               return_value=mock.Mock(stdout="9f2ca14", returncode=0)), \
             mock.patch.object(shipper, "version_code_at", return_value=code), \
             mock.patch.object(shipper, "play_max_version_code",
                               return_value=(floor, "pkg on Play")), \
             mock.patch.object(shipper, "_ci_runs", return_value=runs), \
             mock.patch("team.local_delivery.billing_blocked",
                        return_value=(blocked, "recent account payments have failed")):
            return shipper.pending_delivery(Path("/project"))

    def test_the_store_holding_the_code_clears_the_block(self):
        # The Mac built and uploaded the exact commit: Play has 10113.
        self.assertIsNone(self._run(floor=10113))

    def test_a_higher_code_on_play_also_clears_it(self):
        # The store moved past that build; piling another tag on top is safe.
        self.assertIsNone(self._run(floor=10114))

    def test_a_version_play_does_not_hold_still_blocks(self):
        pending = self._run(floor=10112)
        self.assertEqual(pending["version"], "1.0.71")
        self.assertEqual(pending["reason"], "github_actions_billing")
        self.assertEqual(pending["commit"], "9f2ca14")

    def test_a_green_run_clears_it_without_reading_the_store(self):
        # No Play connection declared: the ordinary path must still work.
        with mock.patch.object(shipper, "play_max_version_code",
                               side_effect=AssertionError("store read before CI")):
            self.assertIsNone(self._run(floor=0, conclusion="success"))

    def test_an_unreadable_version_file_falls_back_to_the_run_list(self):
        self.assertEqual(self._run(floor=0, code=None)["version"], "1.0.71")


if __name__ == "__main__":
    unittest.main()
