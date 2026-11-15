"""A red CI run caused by GitHub Actions billing is not a code failure: the
release must still put a DEBUG build in testers' hands, and must never claim
Play was updated.

Owner directive 2026-11-16: "if GitHub action fail due to billing generate
debug build on local machine and publish to firebase app testing if it is set
up". The detection is the part worth pinning — a fallback that fires on a
generic red run would ship a debug apk for every ordinary test failure, and one
that never fires leaves testers with nothing (the defect this module closes).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import local_delivery


class BillingSignsTest(unittest.TestCase):
    def test_both_phrases_github_emits_are_recognised(self):
        self.assertTrue(any("payments have failed" in s
                            for s in local_delivery.BILLING_SIGNS))
        self.assertTrue(any("spending limit" in s
                            for s in local_delivery.BILLING_SIGNS))

    def test_a_plain_test_failure_is_not_billing(self):
        text = ("x release in 2m — process completed with exit code 1; "
                "FAILED: compileDebugKotlin").lower()
        self.assertFalse(any(sign in text for sign in local_delivery.BILLING_SIGNS))

    def test_no_run_id_never_claims_billing(self):
        blocked, why = local_delivery.billing_blocked(Path("/nonexistent-project"), "")
        self.assertFalse(blocked)
        self.assertEqual(why, "")


class FirebaseTargetTest(unittest.TestCase):
    def test_no_app_id_means_the_project_declares_no_channel(self):
        self.assertIsNone(local_delivery.firebase_config({"ci": {}}))
        self.assertIsNone(local_delivery.firebase_config({}))

    def test_project_id_falls_back_to_the_firebase_connection(self):
        cfg = local_delivery.firebase_config({
            "ci": {"firebase": {"app_id": "1:2:android:3", "groups": "qa"}},
            "connections": {"firebase": {"project_id": "some-project"}},
        })
        self.assertEqual(cfg["app_id"], "1:2:android:3")
        self.assertEqual(cfg["groups"], "qa")
        self.assertEqual(cfg["project_id"], "some-project")


class DebugApkPathTest(unittest.TestCase):
    def test_default_follows_the_declared_module(self):
        p = local_delivery.debug_apk_path(
            Path("/repo"), {"ci": {"module": "composeApp"}})
        self.assertEqual(str(p),
                         "/repo/composeApp/build/outputs/apk/debug/composeApp-debug.apk")

    def test_an_explicit_path_wins(self):
        p = local_delivery.debug_apk_path(
            Path("/repo"), {"ci": {"module": "composeApp",
                                   "debug_apk": "out/custom.apk"}})
        self.assertEqual(str(p), "/repo/out/custom.apk")


if __name__ == "__main__":
    unittest.main()
