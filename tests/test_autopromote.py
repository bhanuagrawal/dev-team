"""The promotion-after-CI provision is an authorization, not a shortcut.

What these tests pin (owner ask 2026-11-17: "promote to production after ci
complete always — if release candidate — create a provision for doing this for a
app when user ask in dev team"):

  * `--on` cannot exist without the owner's words — a boolean alone is not an ask;
  * an app with no provision is a no-op while production is fresh — the one
    exception is the owner's team-wide weekly rule (2026-11-24), which promotes a
    stale production from LIVE Play data and never from `state.json`;
  * an explicit `rc --no` call for the exact version is a VETO that the provision
    never overrules;
  * the version is the NEWEST shipped one, never a superseded build;
  * every launch still goes through `promote()`, with the standing words carried
    into `requested_by`, so the record still names who asked.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import autopromote


OWNER_WORDS = "jo bhi release candidate CI se internal pe aa jaye prod pe bhej dena"


class ProvisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "Demo"
        (self.project / ".pipeline").mkdir(parents=True)
        (self.project / ".pipeline" / "profile.json").write_text(
            json.dumps({"name": "Demo", "repo_slug": "owner/Demo"}))
        (self.project / ".pipeline" / "state.json").write_text(json.dumps({}))

    def tearDown(self):
        self.tmp.cleanup()

    def profile(self):
        return json.loads((self.project / ".pipeline" / "profile.json").read_text())

    def write_state(self, **state):
        (self.project / ".pipeline" / "state.json").write_text(json.dumps(state))

    def test_on_without_the_owners_words_is_refused(self):
        with self.assertRaises(SystemExit):
            autopromote.provision(self.project, on=True)
        self.assertNotIn("promote", self.profile())

    def test_on_records_the_flag_with_the_owners_words_and_a_timestamp(self):
        block = autopromote.provision(self.project, on=True, requested_by=OWNER_WORDS)
        self.assertTrue(block["after_ci"])
        self.assertEqual(block["requested_by"], OWNER_WORDS)
        self.assertTrue(block["at"])
        self.assertEqual(len(block["history"]), 1)
        self.assertTrue(autopromote.enabled(self.project))

    def test_off_disarms_but_keeps_the_history(self):
        autopromote.provision(self.project, on=True, requested_by=OWNER_WORDS)
        block = autopromote.provision(self.project, on=False, note="owner paused it")
        self.assertFalse(block["after_ci"])
        self.assertFalse(autopromote.enabled(self.project))
        self.assertEqual(len(block["history"]), 2)
        self.assertTrue(block["off_at"])
        self.assertEqual(block["history"][0]["requested_by"], OWNER_WORDS)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "Demo"
        (self.project / ".pipeline").mkdir(parents=True)
        self.state = {
            "shipped": [
                {"version": "1.0.8"},
                {"version": "1.0.9"},
            ],
            "release_candidates": [],
            "promotions": [],
        }
        self.save_profile({"name": "Demo"})
        self.save_state()

    def tearDown(self):
        self.tmp.cleanup()

    def save_profile(self, profile):
        (self.project / ".pipeline" / "profile.json").write_text(json.dumps(profile))

    def save_state(self):
        (self.project / ".pipeline" / "state.json").write_text(json.dumps(self.state))

    def arm(self):
        autopromote.provision(self.project, on=True, requested_by=OWNER_WORDS)

    def run_with(self, result, *, prod=None, **kwargs):
        # A FRESH production by default: the weekly rule is not due, so these
        # cases keep pinning the per-app provision's own guarantees.
        fresh = {"version": "1.0.0", "versionCodes": ["100"],
                 "date": "2026-11-23", "days": 1, "why": ""}
        with mock.patch.object(autopromote, "promote", return_value=result) as pr, \
             mock.patch.object(autopromote, "production_now",
                               return_value=prod if prod is not None else fresh), \
             mock.patch.object(autopromote, "save_state"):
            got = autopromote.run(self.project, **kwargs)
        return got, pr

    def test_an_unprovisioned_app_is_a_noop_and_never_touches_play(self):
        got, pr = self.run_with({"status": "promoted"})
        self.assertEqual(got["status"], "not-provisioned")
        pr.assert_not_called()

    def test_an_rc_no_call_vetoes_that_exact_version(self):
        self.arm()
        self.state["release_candidates"] = [{"version": "1.0.9", "rc": False,
                                             "by": "agent", "why": "half-done screen"}]
        self.save_state()
        got, pr = self.run_with({"status": "promoted"})
        self.assertEqual(got["status"], "held")
        self.assertIn("NOT a release candidate", got["error"])
        self.assertIn("1.0.9", got["error"])
        pr.assert_not_called()

    def test_an_rc_yes_call_lets_the_provision_run(self):
        self.arm()
        self.state["release_candidates"] = [{"version": "1.0.9", "rc": True, "by": "agent"}]
        self.save_state()
        got, pr = self.run_with({"status": "DRY-RUN", "version": "1.0.9"}, yes=False)
        self.assertEqual(got["status"], "DRY-RUN")
        kwargs = pr.call_args.kwargs
        self.assertEqual(pr.call_args.args[1], "1.0.9")
        self.assertIn(OWNER_WORDS, kwargs["requested_by"])
        self.assertIn("standing provision", kwargs["requested_by"])
        self.assertTrue(kwargs["dry_run"])
        self.assertEqual(got["provision"]["requested_by"], OWNER_WORDS)

    def test_no_rc_call_means_the_provision_decides(self):
        self.arm()
        got, pr = self.run_with({"status": "DRY-RUN", "version": "1.0.9"}, yes=False)
        self.assertEqual(got["status"], "DRY-RUN")
        pr.assert_called_once()

    def test_the_newest_shipped_version_is_the_one_promoted(self):
        self.arm()
        got, pr = self.run_with({"status": "DRY-RUN"}, yes=False)
        self.assertEqual(pr.call_args.args[1], "1.0.9")
        self.assertEqual(got["status"], "DRY-RUN")

    def test_an_already_promoted_version_is_not_sent_again(self):
        self.arm()
        self.state["promotions"] = [{"version": "1.0.9"}]
        self.save_state()
        got, pr = self.run_with({"status": "promoted"})
        self.assertEqual(got["status"], "already-promoted")
        pr.assert_not_called()

    def test_a_disarmed_app_does_not_promote(self):
        self.arm()
        autopromote.provision(self.project, on=False)
        got, pr = self.run_with({"status": "promoted"})
        self.assertEqual(got["status"], "not-provisioned")
        pr.assert_not_called()

    def test_without_a_shipped_version_there_is_nothing_to_promote(self):
        self.arm()
        self.state["shipped"] = []
        self.save_state()
        got, pr = self.run_with({"status": "promoted"})
        self.assertEqual(got["status"], "nothing-to-promote")
        pr.assert_not_called()


class WeeklyRuleTest(unittest.TestCase):
    """Owner ask 2026-11-24: on every run, a production older than 7 days gets
    the newest release candidate — decided by LIVE Play data, no cache."""

    STALE = {"version": "1.0.7", "versionCodes": ["107"],
             "date": "2026-11-10", "days": 14, "why": ""}
    FRESH = {"version": "1.0.7", "versionCodes": ["107"],
             "date": "2026-11-22", "days": 2, "why": ""}
    UNKNOWN = {"version": "", "versionCodes": [], "date": "", "days": None,
               "why": "store page unreachable"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "Demo"
        (self.project / ".pipeline").mkdir(parents=True)
        (self.project / ".pipeline" / "profile.json").write_text(
            json.dumps({"name": "Demo"}))
        self.state = {"shipped": [{"version": "1.0.8"}, {"version": "1.0.9"}],
                      "release_candidates": [], "promotions": []}
        self.save_state()

    def tearDown(self):
        self.tmp.cleanup()

    def save_state(self):
        (self.project / ".pipeline" / "state.json").write_text(json.dumps(self.state))

    def run_with(self, result, prod, **kwargs):
        with mock.patch.object(autopromote, "promote", return_value=result) as pr, \
             mock.patch.object(autopromote, "production_now", return_value=prod), \
             mock.patch.object(autopromote, "save_state"):
            got = autopromote.run(self.project, **kwargs)
        return got, pr

    def test_a_stale_production_is_promoted_even_without_a_per_app_provision(self):
        got, pr = self.run_with({"status": "DRY-RUN", "version": "1.0.9"},
                                self.STALE, yes=False)
        self.assertEqual(got["status"], "DRY-RUN")
        self.assertEqual(got["rule"], "weekly")
        self.assertEqual(pr.call_args.args[1], "1.0.9")
        self.assertIn("weekly rule", pr.call_args.kwargs["requested_by"])
        self.assertIn("2026-11-10", pr.call_args.kwargs["requested_by"])

    def test_a_fresh_production_is_a_noop_for_an_unarmed_app(self):
        got, pr = self.run_with({"status": "promoted"}, self.FRESH)
        self.assertEqual(got["status"], "not-provisioned")
        pr.assert_not_called()

    def test_exactly_seven_days_is_not_more_than_seven(self):
        seven = dict(self.STALE, date="2026-11-17", days=7)
        got, pr = self.run_with({"status": "promoted"}, seven)
        self.assertEqual(got["status"], "not-provisioned")
        pr.assert_not_called()

    def test_an_unknown_age_holds_instead_of_guessing(self):
        got, pr = self.run_with({"status": "promoted"}, self.UNKNOWN)
        self.assertEqual(got["status"], "not-provisioned")
        self.assertIn("Play did not say", got["error"])
        pr.assert_not_called()

    def test_an_already_live_candidate_has_nothing_to_promote(self):
        live = dict(self.STALE, version="1.0.9", versionCodes=["109"])
        got, pr = self.run_with({"status": "promoted"}, live)
        self.assertEqual(got["status"], "not-provisioned")
        self.assertIn("already the live production build", got["error"])
        pr.assert_not_called()

    def test_the_weekly_rule_still_respects_an_rc_no_veto(self):
        self.state["release_candidates"] = [{"version": "1.0.9", "rc": False,
                                             "by": "agent", "why": "half-done"}]
        self.save_state()
        got, pr = self.run_with({"status": "promoted"}, self.STALE)
        self.assertEqual(got["status"], "held")
        pr.assert_not_called()

    def test_a_promotion_recorded_on_disk_never_makes_production_look_fresh(self):
        # The ledger says 1.0.9 shipped today; Play says production is 14 days
        # old. The live answer wins — state.json is not the clock.
        self.state["promotions"] = [{"version": "1.0.9", "at": "2026-11-24"}]
        self.save_state()
        got, pr = self.run_with({"status": "DRY-RUN"}, self.STALE, yes=False)
        self.assertEqual(got["status"], "DRY-RUN")
        self.assertEqual(got["rule"], "weekly")
        pr.assert_called_once()


class StatusLineTest(unittest.TestCase):
    def test_the_line_never_invents_a_provision(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "Demo"
            (project / ".pipeline").mkdir(parents=True)
            (project / ".pipeline" / "profile.json").write_text(json.dumps({"name": "Demo"}))
            self.assertIn("manual", autopromote.line(project))
            autopromote.provision(project, on=True, requested_by=OWNER_WORDS)
            self.assertIn("PROVISIONED", autopromote.line(project))
            self.assertIn(OWNER_WORDS, autopromote.line(project))


if __name__ == "__main__":
    unittest.main()
