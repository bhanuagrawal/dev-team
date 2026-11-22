"""OWNER MAIL — a decision that needs the owner reaches them, once.

The behaviour pinned here:
  * a complete setup reads as configured, and a password is never in the file
    when the Keychain is available;
  * env variables win over the file (launchd carries the setup);
  * a decision is collected from the ledgers (goal, owner admit flag, explicit
    proposal) and NOT from asks a machine can do;
  * sending is deduplicated — the same open question is mailed once;
  * a failed send leaves the ask UNSENT (never "delivered"), so the next run
    retries;
  * an unconfigured machine records the ask and says so, and never fails a run.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import mail  # noqa: E402


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class MailTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "sample"
        self.project.mkdir()
        _write(self.project / ".pipeline" / "profile.json", {
            "name": "sample",
            "goals": {"vision": "every shelf is a library", "milestones": []},
        })
        _write(self.project / ".pipeline" / "state.json", {"queue": [], "runs": []})
        # Keep tests off the real team config and the report/site rebuild: this
        # suite is about the mail ledger, not about generating pages.
        self._cfg = self.root / "mail.json"
        self._env = mock.patch.dict(os.environ, {"DEVTEAM_MAIL_CONFIG": str(self._cfg)},
                                    clear=False)
        self._env.start()
        for key in ("DEVTEAM_MAIL_TO", "DEVTEAM_MAIL_FROM", "DEVTEAM_SMTP_HOST",
                    "DEVTEAM_SMTP_PORT", "DEVTEAM_SMTP_MODE", "DEVTEAM_SMTP_USER",
                    "DEVTEAM_SMTP_PASSWORD"):
            os.environ.pop(key, None)
        self._save = mock.patch.object(mail, "save_state", side_effect=self._save_state)
        self._save.start()

    def _save_state(self, project: Path, state: dict) -> None:
        _write(Path(project) / ".pipeline" / "state.json", state)

    def tearDown(self):
        self._save.stop()
        self._env.stop()
        self.tmp.cleanup()

    def configured(self):
        mail.set_config(to="owner@example.com", provider="custom",
                        host="smtp.example.com", port=587, mode="starttls",
                        user="owner@example.com")
        # bypass the Keychain in tests: store the secret in the 0600 file
        with mock.patch.object(mail, "keychain_set", return_value=False):
            mail.set_config(password="secret")


class ConfigTest(MailTestBase):
    def test_a_complete_setup_is_configured_and_hides_the_secret(self):
        self.configured()
        st = mail.status()
        self.assertTrue(st["configured"], st["why"])
        self.assertEqual("o****@example.com", st["to_masked"])
        self.assertNotIn("secret", mail.format_status(st))
        # the password is in the 0600 fallback only because the Keychain was stubbed out
        self.assertEqual(0o600, self._cfg.stat().st_mode & 0o777)

    def test_keychain_is_preferred_over_the_file(self):
        with mock.patch.object(mail, "keychain_set", return_value=True):
            res = mail.set_config(to="a@b.co", provider="custom", host="h",
                                  user="a@b.co", password="pw")
        self.assertEqual("keychain", res["password_stored"])
        self.assertNotIn('"password": "pw"', self._cfg.read_text())
        with mock.patch.object(mail, "keychain_get", return_value="from-keychain"):
            self.assertEqual("from-keychain", mail.password_for(mail.load_config()))

    def test_env_wins_over_the_file(self):
        self.configured()
        with mock.patch.dict(os.environ, {"DEVTEAM_MAIL_TO": "env@example.com",
                                          "DEVTEAM_SMTP_HOST": "relay.env"}):
            cfg = mail.load_config()
        self.assertEqual("env@example.com", cfg["to"])
        self.assertEqual("relay.env", cfg["host"])

    def test_unconfigured_says_what_is_missing(self):
        st = mail.status()
        self.assertFalse(st["configured"])
        self.assertIn("no recipient", st["why"])

    def test_off_is_muted_but_still_valid_config(self):
        self.configured()
        mail.set_config(enabled=False)
        st = mail.status()
        self.assertFalse(st["configured"])
        self.assertIn("muted", st["why"])


class CollectTest(MailTestBase):
    def _state(self, **extra):
        data = {"queue": [], "runs": []}
        data.update(extra)
        _write(self.project / ".pipeline" / "state.json", data)
        return data

    def test_an_owner_admit_flag_is_collected_with_its_command(self):
        self._state(admit_log=[{"who": "owner", "question": "is this the new goal?",
                                "verdict": "against", "law": "P3",
                                "next": 'devteam goal sample set --vision "…"'}])
        items = mail.collect(self.project)
        self.assertEqual(1, len(items))
        self.assertEqual("admit", items[0]["source"])
        self.assertIn("devteam goal", items[0]["command"])
        self.assertEqual("P3", items[0]["law"])

    def test_an_agent_flag_is_not_the_owner_s(self):
        self._state(admit_log=[{"who": "agent", "question": "which kind?",
                                "verdict": "gap"}])
        self.assertEqual([], mail.collect(self.project))

    def test_a_proposal_is_recorded_and_can_be_resolved(self):
        res = mail.add_proposal(self.project, title="the pause loses half",
                                question="shorten the first pause?",
                                why="28 start, 13 finish", law="P3")
        self.assertTrue(res["ok"])
        items = mail.collect(self.project)
        self.assertEqual("proposal", items[0]["source"])
        closed = mail.resolve(self.project, res["item"]["id"])
        self.assertTrue(closed["ok"], closed.get("why"))
        self.assertEqual([], mail.collect(self.project))

    def test_a_derived_ask_cannot_be_resolved_as_a_proposal(self):
        res = mail.resolve(self.project, "nope")
        self.assertFalse(res["ok"])
        self.assertIn("answered by its own command", res["why"])

    def test_inbox_prints_the_answer_command(self):
        self._state(admit_log=[{"who": "owner", "question": "change it?",
                                "next": "devteam law sample --amend P1",
                                "verdict": "contradicts"}])
        text = mail.format_inbox(mail.inbox(self.project))
        self.assertIn("devteam law sample --amend P1", text)
        self.assertIn("WAIT", text)


class SendTest(MailTestBase):
    def setUp(self):
        super().setUp()
        _write(self.project / ".pipeline" / "state.json", {
            "queue": [], "runs": [],
            "admit_log": [{"who": "owner", "question": "is this the new goal?",
                           "next": 'devteam goal sample set --vision "…"',
                           "verdict": "against"}],
        })

    def test_unconfigured_records_the_ask_and_never_loses_it(self):
        res = mail.send_pending(self.project)
        self.assertEqual("unconfigured", res["status"])
        self.assertEqual(1, res["waiting"])
        data = mail.inbox(self.project)
        self.assertEqual("", data["items"][0]["sent_at"])
        self.assertFalse(data["configured"])

    def test_a_sent_digest_marks_the_item_and_deduplicates(self):
        self.configured()
        with mock.patch.object(mail.smtplib, "SMTP") as smtp:
            res = mail.send_pending(self.project)
            self.assertEqual("sent", res["status"], res.get("why"))
            self.assertEqual(1, res["sent"])
            smtp.assert_called_once()
        again = mail.send_pending(self.project)
        self.assertEqual("nothing-new", again["status"])
        self.assertEqual(0, again["sent"])

    def test_a_failed_send_stays_unsent(self):
        self.configured()
        with mock.patch.object(mail.smtplib, "SMTP",
                               side_effect=OSError("connection refused")):
            res = mail.send_pending(self.project)
        self.assertEqual("error", res["status"])
        self.assertIn("connection refused", res["why"])
        self.assertEqual("", mail.inbox(self.project)["items"][0]["sent_at"])

    def test_the_digest_names_the_change_and_the_command(self):
        self.configured()
        captured = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=None):
                captured["host"] = host
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            def ehlo(self):
                pass
            def starttls(self, context=None):
                pass
            def login(self, user, password):
                captured["user"] = user
            def send_message(self, msg):
                captured["subject"] = msg["Subject"]
                captured["body"] = msg.get_content()

        with mock.patch.object(mail.smtplib, "SMTP", FakeSMTP):
            mail.send_pending(self.project)
        self.assertIn("sample", captured["subject"])
        self.assertIn("decision needs", captured["subject"])
        self.assertIn("devteam goal sample set --vision", captured["body"])
        self.assertIn("devteam mail inbox sample", captured["body"])


if __name__ == "__main__":
    unittest.main()
