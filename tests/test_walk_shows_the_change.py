"""A walk is evidence of the change only if the walk SAW the change.

The 2026-11-21 phone report — *"No message visible on app until feed arrived, on
my phone, did you not test it"* — was tested: a `search` walk ran on the emulator,
reached its goal, and PASSED, while the broken thing (the agent's progress card)
never appeared in a single frame. Search submits over REST; only the feed's own
turn goes through A2A. The gate accepted a walk that proved a different thing,
which is why the fix now demands the walk show the change.

Two duties, both cheap to satisfy and impossible to satisfy by accident:

* the task declares the words the change puts on screen (`walk.observables`),
  optionally pinning the journey that opens it (`walk.flows`);
* the walk records what every step actually displayed (accessibility-tree text,
  no model in the loop), and the gate matches the two.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from team import ux_flow, verify

CODE = "composeApp/src/commonMain/kotlin/Place.kt"
CARD = "Looking around your neighbourhood"


class WalkShowsChangeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "ux-flows.json").write_text(json.dumps({"flows": [
            {"id": "search", "surface": "Discovery", "goal": "find a book"},
            {"id": "home", "surface": "Home", "goal": "see the feed"},
        ]}))
        self.code = self.root / CODE
        self.code.parent.mkdir(parents=True)
        self.code.write_text("class Place\n")
        stamp = time.time() - 600
        os.utime(self.code, (stamp, stamp))

    def tearDown(self):
        self.tmp.cleanup()

    def _walk(self, flow_id: str, texts: list[str], age: float = 60) -> None:
        out = ux_flow.run_dir(self.root) / ux_flow._slug(flow_id)
        out.mkdir(parents=True, exist_ok=True)
        result = out / "result.json"
        result.write_text(json.dumps({
            "goal_reached": True,
            "outcome": "done",
            "reason": "",
            "steps": [{"n": 1, "did": "looked", "then": "saw it", "texts": texts}],
        }))
        stamp = time.time() - age
        os.utime(result, (stamp, stamp))

    def _gate(self, spec: dict):
        return verify.flow_gate(self.root, self.root, spec, [CODE])

    def test_a_walk_that_saw_the_declared_observable_passes(self):
        self._walk("home", ["BookSwap", CARD, "Checking your shelf"])
        ok, msg = self._gate({"walk": {"observables": [CARD]}})
        self.assertTrue(ok, msg)
        self.assertIn("home", msg)

    def test_a_green_walk_that_never_showed_the_change_is_refused(self):
        # The exact shape of the bug: search passed, the card was never on screen.
        self._walk("search", ["BookSwap", "added this week", "The Three-Body Problem"])
        ok, msg = self._gate({"walk": {"observables": [CARD]}})
        self.assertFalse(ok, msg)
        self.assertIn("never showed what this change does", msg)
        self.assertIn(CARD.lower(), msg.lower())

    def test_a_task_that_names_nothing_to_see_is_refused(self):
        self._walk("search", ["BookSwap"])
        ok, msg = self._gate({"walk": {}})
        self.assertFalse(ok, msg)
        self.assertIn("declares no observable", msg)
        self.assertIn("walk.observables", msg)

    def test_the_named_journey_is_the_evidence_not_the_newest_walk(self):
        # `search` is newer, but the task says the card is proven by `home`; the
        # home walk saw it, so the gate takes home's evidence.
        self._walk("home", ["BookSwap", CARD], age=120)
        self._walk("search", ["BookSwap", "results"], age=10)
        ok, msg = self._gate({"walk": {"flows": ["home"], "observables": [CARD]}})
        self.assertTrue(ok, msg)
        self.assertIn("home", msg)

    def test_a_pinned_journey_that_never_saw_it_is_refused(self):
        self._walk("home", ["BookSwap"], age=120)
        self._walk("search", ["BookSwap", CARD], age=10)
        ok, msg = self._gate({"walk": {"flows": ["home"], "observables": [CARD]}})
        self.assertFalse(ok, msg)
        self.assertIn("never showed what this change does", msg)

    def test_a_walk_recorded_before_texts_were_kept_is_not_evidence(self):
        out = ux_flow.run_dir(self.root) / ux_flow._slug("home")
        out.mkdir(parents=True, exist_ok=True)
        result = out / "result.json"
        result.write_text(json.dumps({"goal_reached": True, "outcome": "done",
                                      "reason": "", "steps": [{"n": 1}]}))
        stamp = time.time() - 60
        os.utime(result, (stamp, stamp))
        ok, msg = self._gate({"walk": {"observables": [CARD]}})
        self.assertFalse(ok, msg)
        self.assertIn("never showed", msg)

    def test_no_task_spec_keeps_the_old_clock_rule(self):
        self._walk("search", [])
        ok, msg = self._gate({})
        self.assertTrue(ok, msg)
        self.assertIn("was run on a device", msg)


class AuditAccountTest(unittest.TestCase):
    """Every walk signs in to the real backend, so the account is a real input."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".pipeline").mkdir()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DEVTEAM_AUDIT_EMAIL", None)
        os.environ.pop("DEVTEAM_AUDIT_PASSWORD", None)

    def _write(self, data: dict) -> None:
        (self.root / ".pipeline" / "audit-account.json").write_text(json.dumps(data))

    def test_the_account_comes_from_the_gitignored_project_file(self):
        self._write({"email": "walk@bookswap.test", "password": "s3cret",
                     "profile": {"name": "Walk"}})
        got = ux_flow.audit_account(self.root)
        self.assertEqual("walk@bookswap.test", got["email"])
        self.assertEqual("s3cret", got["password"])
        self.assertEqual("Walk", got["profile"]["name"])

    def test_environment_wins_over_the_file(self):
        self._write({"email": "file@bookswap.test", "password": "file"})
        os.environ["DEVTEAM_AUDIT_EMAIL"] = "env@bookswap.test"
        os.environ["DEVTEAM_AUDIT_PASSWORD"] = "env"
        got = ux_flow.audit_account(self.root)
        self.assertEqual("env@bookswap.test", got["email"])
        self.assertEqual("env", got["password"])

    def test_no_account_refuses_instead_of_using_a_fixture(self):
        # A tree that names no manifest is not a claim about the network, so the
        # ordinary rule holds: an account is required.
        manifest_dir = self.root / "app" / "src" / "main"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "AndroidManifest.xml").write_text(
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
            '<uses-permission android:name="android.permission.INTERNET" />'
            "</manifest>")
        with self.assertRaises(SystemExit) as caught:
            ux_flow.audit_account(self.root)
        self.assertIn("audit-account.json", str(caught.exception))
        self.assertIn("no mock backend", str(caught.exception))

    def test_an_app_that_cannot_reach_a_network_walks_offline(self):
        # FieldCert ships no backend at all: its manifest (and every other one in
        # the tree) omits INTERNET, so there is no sign-in to invent either way.
        manifest_dir = self.root / "app" / "src" / "main"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "AndroidManifest.xml").write_text(
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
            '<uses-permission android:name="android.permission.CAMERA" />'
            "</manifest>")
        got = ux_flow.audit_account(self.root)
        self.assertTrue(got.get("offline"))
        self.assertEqual("", got["email"])

    def test_one_manifest_with_internet_keeps_the_account_rule(self):
        for rel in ("app/src/main/AndroidManifest.xml",
                    "composeApp/src/androidMain/AndroidManifest.xml"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
                '<uses-permission android:name="android.permission.INTERNET" />'
                "</manifest>")
        with self.assertRaises(SystemExit):
            ux_flow.audit_account(self.root)


class WatcherTest(unittest.TestCase):
    """The recorder keeps what passed while the model was deciding."""

    def test_words_seen_during_the_model_call_are_kept(self):
        import threading
        from unittest import mock
        stop = threading.Event()
        seen: list[str] = []
        xml = '<node text="Looking around your neighbourhood" package="app" bounds="[0,0][9,9]"/>'

        def fake_dump(_serial):
            stop.set()
            return xml

        with mock.patch.object(ux_flow, "_screen_texts",
                               wraps=ux_flow._screen_texts), \
             mock.patch("team.ux_video._ui_xml", side_effect=fake_dump):
            ux_flow._watch_screen("device", "app", seen, stop)
        self.assertEqual(["Looking around your neighbourhood"], seen)


class WalkRecordsWhatItSawTest(unittest.TestCase):
    """The recorder itself: the screen's words, straight from the dump."""

    def test_texts_come_from_text_and_content_desc_of_the_app_only(self):
        xml = (
            '<node text="BookSwap" package="app" bounds="[0,0][10,10]"/>'
            '<node content-desc="Looking around your neighbourhood" package="app" '
            'bounds="[0,0][10,10]"/>'
            '<node text="BookSwap" package="app" bounds="[0,0][10,10]"/>'
            '<node text="Wi-Fi" package="android" bounds="[0,0][10,10]"/>'
            '<node text="  " package="app" bounds="[0,0][10,10]"/>'
        )
        self.assertEqual(
            ux_flow._screen_texts(xml, "app"),
            ["BookSwap", "Looking around your neighbourhood"],
        )


if __name__ == "__main__":
    unittest.main()
