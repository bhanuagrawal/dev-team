"""The two facts a release depends on, and the one way they used to lie.

A release is delivered when Play has the version, and testers are notified when
the workflow's QA job actually reaches App Distribution. Both were reported
"green" on 2026-11-16 while the tester channel received nothing: the QA upload
was a Docker container action, which GitHub refuses on a macOS runner, and
`continue-on-error` turned that refusal into a passing step. So these tests pin

  * where the tester target is declared (`ci.firebase`),
  * how a billed-out run is recognised (the run's own annotation),
  * that the template uploads with the CLI in a `run:` step, and
  * that `self_check` refuses the container action if anyone puts it back.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import ci, shipper


class FirebaseTargetTest(unittest.TestCase):
    def test_no_declaration_means_no_qa_job(self):
        self.assertIsNone(ci.firebase_config({"ci": {}}))
        self.assertIsNone(ci.firebase_config({}))
        self.assertIsNone(ci.firebase_config({"ci": {"firebase": {"groups": "qa"}}}))

    def test_a_declared_app_carries_its_group_and_project(self):
        cfg = ci.firebase_config({
            "ci": {"firebase": {"app_id": "1:2:android:3", "groups": "qa"}},
            "connections": {"firebase": {"project_id": "bookswapkmp"}},
        })
        self.assertEqual(cfg["app_id"], "1:2:android:3")
        self.assertEqual(cfg["groups"], "qa")
        self.assertEqual(cfg["project_id"], "bookswapkmp")

    def test_the_group_is_optional(self):
        cfg = ci.firebase_config({"ci": {"firebase": {"app_id": "1:2:android:3"}}})
        self.assertEqual(cfg["groups"], "")


class BillingBlockedTest(unittest.TestCase):
    def test_both_phrases_github_emits_are_recognised(self):
        for sign in shipper.BILLING_SIGNS:
            with mock.patch.object(shipper, "profile_of",
                                   return_value={"repo_slug": "o/r"}), \
                 mock.patch.object(shipper, "run",
                                   return_value=mock.Mock(
                                       returncode=0, stdout=f"annotation: {sign}",
                                       stderr="")):
                blocked, why = shipper.billing_blocked(Path("/p"), "123")
            self.assertTrue(blocked, sign)
            self.assertEqual(why, sign)

    def test_an_ordinary_red_run_is_not_billing(self):
        with mock.patch.object(shipper, "profile_of",
                               return_value={"repo_slug": "o/r"}), \
             mock.patch.object(shipper, "run",
                               return_value=mock.Mock(returncode=0,
                                                      stdout="step failed: compileDebugKotlin",
                                                      stderr="")):
            self.assertEqual(shipper.billing_blocked(Path("/p"), "123"), (False, ""))

    def test_a_missing_run_id_never_blocks(self):
        self.assertEqual(shipper.billing_blocked(Path("/p"), ""), (False, ""))
        self.assertEqual(shipper.billing_blocked(Path("/p"), None), (False, ""))

    def test_the_check_run_annotation_is_read_when_the_run_view_is_silent(self):
        """2026-11-19: `gh run view` showed a red run with a blank job and no
        annotation text, while the check-run carried the budget sentence. The
        release reported a false code failure and never dispatched the runner."""
        jobs = '{"jobs": [{"id": 135815690713, "name": "release"}]}'
        annotation = json.dumps([{
            "annotation_level": "failure",
            "message": "The job was not started because an Actions budget is preventing further use.",
        }])

        def fake_run(cmd, *args, **kwargs):
            argv = list(cmd)
            if argv[:3] == ["gh", "run", "view"]:
                return mock.Mock(returncode=0, stdout="X release · 123\n", stderr="")
            if argv[:2] == ["gh", "api"] and argv[2].endswith("/jobs"):
                return mock.Mock(returncode=0, stdout=jobs, stderr="")
            if argv[:2] == ["gh", "api"] and argv[2].endswith("/annotations"):
                return mock.Mock(returncode=0, stdout=annotation, stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="unexpected")

        with mock.patch.object(shipper, "profile_of",
                               return_value={"repo_slug": "o/r"}), \
             mock.patch.object(shipper, "run", side_effect=fake_run):
            blocked, why = shipper.billing_blocked(Path("/p"), "123")
        self.assertTrue(blocked)
        self.assertEqual(why, "actions budget is preventing further use")

    def test_an_unreadable_annotation_answer_is_not_a_dispatch(self):
        jobs = '{"jobs": [{"id": 7}]}'

        def fake_run(cmd, *args, **kwargs):
            argv = list(cmd)
            if argv[:3] == ["gh", "run", "view"]:
                return mock.Mock(returncode=0, stdout="X release · 123\n", stderr="")
            if argv[:2] == ["gh", "api"] and argv[2].endswith("/jobs"):
                return mock.Mock(returncode=0, stdout=jobs, stderr="")
            # Not JSON: gh answered with an error page, or the job was pruned.
            return mock.Mock(returncode=1, stdout="not json", stderr="boom")

        with mock.patch.object(shipper, "profile_of",
                               return_value={"repo_slug": "o/r"}), \
             mock.patch.object(shipper, "run", side_effect=fake_run):
            self.assertEqual(shipper.billing_blocked(Path("/p"), "123"), (False, ""))


class TemplateUploadTest(unittest.TestCase):
    """The QA lane must upload where every runner can, and say when it cannot."""

    def test_the_upload_is_a_cli_run_step(self):
        text = ci.TEMPLATE.read_text()
        self.assertIn("appdistribution:distribute", text)
        self.assertNotIn("uses: wzieba/Firebase-Distribution-Github-Action", text)

    def test_self_check_refuses_a_container_action(self):
        # Put the action back the way it was, and watch the gate catch it.
        broken = ci.TEMPLATE.read_text().replace(
            "      - name: Share with testers (Firebase App Distribution)",
            "      - name: Share with testers (Firebase App Distribution)\n"
            "        uses: wzieba/Firebase-Distribution-Github-Action@v1")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ci.yml"
            p.write_text(broken)
            with mock.patch.object(ci, "TEMPLATE", p):
                problems = ci.self_check()
        self.assertTrue(any("container action" in p for p in problems), problems)

    def test_a_failed_distribution_is_named_in_the_run(self):
        text = ci.TEMPLATE.read_text()
        self.assertIn("steps.firebase.outcome == 'failure'", text)
        self.assertIn("testers were NOT notified", text)

    def test_the_upload_uses_release_notes_file(self):
        text = ci.TEMPLATE.read_text()
        self.assertIn("--release-notes-file", text)


class WhatsNewAndTesterNotesTest(unittest.TestCase):
    """Store notes must be consumer copy; tester notes must carry rich change details."""

    def test_whatsnew_gate_refuses_technical_jargon(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / ".pipeline").mkdir()
            (p / "distribution" / "whatsnew").mkdir(parents=True)
            (p / ".pipeline" / "profile.json").write_text('{"whatsnew_file": "distribution/whatsnew/whatsnew-en-US"}')
            (p / ".pipeline" / "state.json").write_text('{"shipped": []}')
            (p / "distribution" / "whatsnew" / "whatsnew-en-US").write_text(
                "Standardized AnalyticsTracker and instrumented shelf scanner"
            )
            ok, msg = shipper.whatsnew_gate(p, {"whatsnew": "Standardized AnalyticsTracker and instrumented shelf scanner"})
            self.assertFalse(ok)
            self.assertIn("technical engineering vocabulary", msg)
            self.assertIn("analytics", msg)

    def test_whatsnew_gate_accepts_consumer_benefit_copy(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / ".pipeline").mkdir()
            (p / "distribution" / "whatsnew").mkdir(parents=True)
            (p / ".pipeline" / "profile.json").write_text('{"whatsnew_file": "distribution/whatsnew/whatsnew-en-US"}')
            (p / ".pipeline" / "state.json").write_text('{"shipped": []}')
            note = "✓ Faster shelf scanning when adding books\n✓ Everything you already use keeps working smoothly"
            (p / "distribution" / "whatsnew" / "whatsnew-en-US").write_text(note)
            ok, msg = shipper.whatsnew_gate(p, {"whatsnew": note})
            self.assertTrue(ok, msg)

    def test_write_tester_notes_generates_detailed_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            task = {
                "type": "feature",
                "label": "shelf-scanner-v2",
                "advances": "shelf-vision",
                "problem": "Reader wants fast multi-book detection on their bookshelf",
                "user_sees": "Scanned books appear in your tradable library instantly",
                "done_when": ["Tap camera button captures shelf", "Detects 5 books cleanly"],
                "surface": {"targets": ["composeApp/src/commonMain/shelf/**"]},
                "whatsnew": "✓ Faster book scanning from your shelves",
            }
            notes = shipper.write_tester_notes(w, task, "1.0.83", code=10132)
            self.assertTrue((w / "distribution" / "tester-notes.md").exists())
            self.assertIn("# Build v1.0.83 (10132) — Tester Notes", notes)
            self.assertIn("Reader wants fast multi-book detection", notes)
            self.assertIn("- [ ] Tap camera button captures shelf", notes)
            self.assertIn("Test-only affordances enabled", notes)


class DebugTaskTest(unittest.TestCase):
    """A flavored module has no `assembleDebug`, so the task is a profile fact."""

    def test_the_default_is_the_plain_task(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "composeApp").mkdir()
            with mock.patch.object(ci, "profile_of",
                                   return_value={"ci": {"package_name": "a.b"}}):
                cfg = ci.ci_config(root)
        self.assertEqual(cfg["debug_task"], ":composeApp:assembleDebug")

    def test_a_declared_task_wins(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "app").mkdir()
            with mock.patch.object(ci, "profile_of", return_value={
                    "ci": {"package_name": "a.b",
                           "debug_task": ":app:assembleZenlistDebug"}}):
                cfg = ci.ci_config(root)
        self.assertEqual(cfg["debug_task"], ":app:assembleZenlistDebug")

    def test_the_template_uses_the_placeholder_not_a_hardcoded_task(self):
        text = ci.TEMPLATE.read_text()
        self.assertIn("__DEBUG_TASK__", text)
        self.assertNotIn(":__MODULE__:assembleDebug", text)


if __name__ == "__main__":
    unittest.main()
