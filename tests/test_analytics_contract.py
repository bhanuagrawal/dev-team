"""The analytics contract: a declaration checked against two independent facts.

2026-11-22, the day this module was written: every managed app passed `doctor`
because the only analytics gate was "some source file contains the string
logEvent". So the planner read installs as if they were the product —
BookSwap 715 first opens / 1 completed shelf scan, Paddle 631 matches started /
no purchase, MindfulLauncher (whose premise is "find what people will pay for")
with no paywall event at all.

These tests pin the properties that make the gate honest rather than decorative:
1. the declaration is read from the profile, never guessed;
2. the tree is read from the DELIVERY ref (a stale checkout certified a theme
   users never received once already);
3. a declared event absent from the tree, or with zero users beside declared
   events that have them, is reported as a distinct finding;
4. mode 'none' is a first-class answer that checks ABSENCE;
5. every blocker becomes exactly one research upgrade, so the planner queues it.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import analytics


def _repo(files: dict[str, str]) -> Path:
    """A throwaway git repo whose HEAD is also origin/main (the delivery ref)."""
    root = Path(tempfile.mkdtemp(prefix="dt-analytics-"))
    def g(*a):
        subprocess.run(["git", *a], cwd=root, check=True,
                       capture_output=True, text=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    g("add", "-A")
    g("commit", "-qm", "init")
    g("update-ref", "refs/remotes/origin/main", "HEAD")
    return root


KOTLIN = '''
package app
class Tracker { fun track() { logEvent("match_started"); logEvent("match_finished") } }
'''


class SourceScanTest(unittest.TestCase):
    def test_reads_the_delivery_ref_not_the_checkout(self):
        root = _repo({"app/src/Main.kt": KOTLIN,
                      "app/build.gradle.kts": "implementation 'com.google.firebase:firebase-analytics:21'"})
        # a checkout-only edit must not count: the ref is what ships
        (root / "app/src/Main.kt").write_text('logEvent("paywall_shown")')
        scan = analytics.source_scan(root)
        self.assertTrue(scan["sdk"])
        self.assertTrue(scan["tracker"])
        self.assertFalse(scan["billing"])
        self.assertIn("match_started", scan["names"])
        self.assertNotIn("paywall_shown", scan["names"])

    def test_tree_contains_finds_a_literal_outside_the_analytics_file(self):
        root = _repo({"app/src/Screen.kt": 'Analytics.logEvent("focus_window_toggled")',
                      "app/src/Other.kt": 'val label = "focus_window_toggled"'})
        self.assertEqual(analytics.tree_contains(root, ["focus_window_toggled"]),
                         {"focus_window_toggled"})
        self.assertEqual(analytics.tree_contains(root, ["never_sent"]), set())


class AuditTest(unittest.TestCase):
    def _audit(self, root, profile, events):
        return analytics.audit(root, profile, events=events, live=False)

    def test_an_undeclared_contract_is_blocked(self):
        root = _repo({"app/src/Main.kt": KOTLIN})
        res = self._audit(root, {"connections": {"ga4": {"property": "1"}}},
                          {"first_open": {"users": 10}})
        self.assertEqual([c["id"] for c in res["blocked"]], ["analytics.declared"])
        self.assertIn("--draft", res["blocked"][0]["fix"])

    def test_mode_none_checks_absence_not_presence(self):
        root = _repo({"app/src/Main.kt": "class Nothing { fun f() = 1 }"})
        clean = self._audit(root, {"analytics": {"mode": "none", "law": "P2"}}, {})
        self.assertEqual(clean["blocked"], [])
        self.assertEqual(clean["checks"][0]["status"], "ok")

        # a property declared for an app that must send nothing is the failure
        dirty = self._audit(root, {"analytics": {"mode": "none", "law": "P2"},
                                   "connections": {"ga4": {"property": "1"}}}, {})
        self.assertEqual(len(dirty["blocked"]), 1)
        self.assertIn("ga4", dirty["blocked"][0]["detail"])

    def test_a_measured_contract_passes_when_it_is_named_and_lands(self):
        root = _repo({"app/src/Main.kt": KOTLIN,
                      "app/build.gradle.kts": "implementation 'com.google.firebase:firebase-analytics:21'"})
        profile = {"connections": {"ga4": {"property": "1"}},
                   "analytics": {"mode": "measured", "classes": {
                       "activation": ["match_started"],
                       "completion": ["match_finished"],
                       "features": ["match_started", "match_finished"]}}}
        events = {"first_open": {"users": 50},
                  "match_started": {"users": 30},
                  "match_finished": {"users": 25}}
        res = self._audit(root, profile, events)
        self.assertEqual(res["blocked"], [], res["blocked"])
        self.assertTrue(any("funnel: match_started 30" in f for f in res["findings"]))

    def test_a_declared_event_the_tree_does_not_send_is_blocked(self):
        root = _repo({"app/src/Main.kt": KOTLIN,
                      "app/build.gradle.kts": "implementation 'com.google.firebase:firebase-analytics:21'"})
        profile = {"connections": {"ga4": {"property": "1"}},
                   "analytics": {"mode": "measured", "classes": {
                       "activation": ["match_started"],
                       "completion": ["offer_accepted"],
                       "features": ["match_started", "match_finished"]}}}
        res = self._audit(root, profile, {"match_started": {"users": 5}})
        blocked = {c["id"]: c["detail"] for c in res["blocked"]}
        self.assertIn("analytics.completion.declared", blocked)
        self.assertIn("offer_accepted", blocked["analytics.completion.declared"])

    def test_a_declared_constant_nothing_sends_is_not_an_event(self):
        # BookSwap 2026-11-22: the tracker declared wish_created/offer_proposed as
        # constants and no screen ever sent them, so "the name is in the tree"
        # passed while the product's two core acts read as zeros for 30 days.
        root = _repo({"app/src/Analytics.kt":
                      'object Names { const val WISH_CREATED = "wish_created" }',
                      "app/src/Main.kt": KOTLIN,
                      "app/build.gradle.kts": "implementation 'com.google.firebase:firebase-analytics:21'"})
        profile = {"connections": {"ga4": {"property": "1"}},
                   "analytics": {"mode": "measured", "classes": {
                       "activation": ["match_started"],
                       "completion": ["wish_created"],
                       "features": ["match_started", "match_finished"]}}}
        res = self._audit(root, profile,
                          {"match_started": {"users": 9}, "wish_created": {"users": 0}})
        blocked = {c["id"]: c["detail"] for c in res["blocked"]}
        self.assertIn("analytics.completion.declared", blocked)
        self.assertIn("nothing emits it", blocked["analytics.completion.declared"])
        # one gap, one row: the zero is a consequence, not a second finding
        self.assertNotIn("analytics.completion.lands", blocked)

    def test_a_declared_constant_its_screen_sends_is_an_event(self):
        root = _repo({"app/src/Analytics.kt":
                      'object Names { const val WISH_CREATED = "wish_created" }',
                      "app/src/Main.kt":
                      'class T { fun f() { logEvent(Names.WISH_CREATED) } }',
                      "app/build.gradle.kts": "implementation 'com.google.firebase:firebase-analytics:21'"})
        profile = {"connections": {"ga4": {"property": "1"}},
                   "analytics": {"mode": "measured", "classes": {
                       "activation": ["wish_created"],
                       "completion": ["wish_created"],
                       "features": ["wish_created", "wish_created"]}}}
        res = self._audit(root, profile, {"wish_created": {"users": 4}})
        self.assertFalse([c for c in res["blocked"] if "declared" in c["id"]], res["blocked"])

    def test_a_declared_event_with_zero_users_is_not_a_quiet_zero(self):
        root = _repo({"app/src/Main.kt": KOTLIN,
                      "app/build.gradle.kts": "implementation 'com.google.firebase:firebase-analytics:21'"})
        profile = {"connections": {"ga4": {"property": "1"}},
                   "analytics": {"mode": "measured", "classes": {
                       "activation": ["match_started"],
                       "completion": ["match_finished"],
                       "features": ["match_started", "match_finished"]}}}
        res = self._audit(root, profile,
                          {"match_started": {"users": 1}, "match_finished": {"users": 0}})
        self.assertEqual([c["id"] for c in res["blocked"]],
                         ["analytics.completion.lands"])
        self.assertTrue(any("silent" in f and "match_finished" in f
                            for f in res["findings"]), res["findings"])

    def test_a_billing_tree_without_a_purchase_funnel_is_blocked(self):
        root = _repo({"app/src/Main.kt": KOTLIN,
                      "app/src/Paywall.kt": "class Paywall { val b = BillingClient }",
                      "app/build.gradle.kts": "implementation 'com.google.firebase:firebase-analytics:21'"})
        profile = {"connections": {"ga4": {"property": "1"}},
                   "analytics": {"mode": "measured", "classes": {
                       "activation": ["match_started"],
                       "completion": ["match_finished"],
                       "features": ["match_started", "match_finished"]}}}
        res = self._audit(root, profile,
                          {"match_started": {"users": 5}, "match_finished": {"users": 4}})
        self.assertIn("analytics.money.declared", [c["id"] for c in res["blocked"]])

    def test_an_app_that_sells_nothing_owes_no_money_class(self):
        root = _repo({"app/src/Main.kt": KOTLIN,
                      "app/build.gradle.kts": "implementation 'com.google.firebase:firebase-analytics:21'"})
        profile = {"connections": {"ga4": {"property": "1"}},
                   "analytics": {"mode": "measured", "classes": {
                       "activation": ["match_started"],
                       "completion": ["match_finished"],
                       "features": ["match_started", "match_finished"]}}}
        res = self._audit(root, profile,
                          {"match_started": {"users": 5}, "match_finished": {"users": 4}})
        self.assertFalse(any("money" in c["id"] for c in res["checks"]))

    def test_a_renamed_event_is_called_out_so_the_old_name_is_not_read_as_live(self):
        root = _repo({"app/src/Main.kt":
                      'class T { fun f() { logEvent("pause_breath_completed") } }',
                      "app/build.gradle.kts": "implementation 'com.google.firebase:firebase-analytics:21'"})
        profile = {"connections": {"ga4": {"property": "1"}},
                   "analytics": {"mode": "measured", "classes": {
                       "activation": ["pause_breath_completed"],
                       "completion": ["pause_done_completed"],
                       "features": ["pause_breath_completed", "pause_done_completed"]}}}
        (root / "app/src/Main.kt").write_text(
            'class T { fun f() { logEvent("pause_breath_completed");'
            ' logEvent("pause_done_completed") } }')
        subprocess.run(["git", "commit", "-qam", "both"], cwd=root, check=True,
                       capture_output=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
                       cwd=root, check=True, capture_output=True)
        res = self._audit(root, profile,
                          {"pause_breath_completed": {"users": 10},
                           "pause_done_completed": {"users": 1},
                           "breathing_pause_completed": {"users": 9}})
        self.assertTrue(any("continuity" in f and "breathing_pause_completed" in f
                            for f in res["findings"]), res["findings"])


class UpgradeTest(unittest.TestCase):
    def test_every_blocker_becomes_one_queueable_upgrade(self):
        root = _repo({"app/src/Main.kt": KOTLIN})
        res = analytics.audit(root, {"connections": {"ga4": {"property": "1"}}},
                              events={"first_open": {"users": 1}}, live=False)
        ups = analytics.upgrades(res, "Demo")
        self.assertEqual(len(ups), 1)
        self.assertEqual(ups[0]["id"], "analytics-contract")
        self.assertTrue(ups[0]["how"].strip())

    def test_doctor_rows_never_raise_and_mark_blockers_not_ok(self):
        root = _repo({"app/src/Main.kt": KOTLIN})
        res = analytics.audit(root, {}, events={}, live=False)
        rows = analytics.doctor_checks(res)
        self.assertTrue(rows)
        self.assertFalse(rows[0][1])  # name, ok, fix


if __name__ == "__main__":
    unittest.main()
