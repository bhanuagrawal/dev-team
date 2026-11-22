"""ADMIT × THE LAW — a request names a clause AND says what it does to it.

The gap this pins down (owner decision 2026-11-22): `--law P8` used to be a
label, not a test. The gate checked that the id existed and never asked whether
the request served, amended or defied the clause — so "add a price field" could
be admitted under "money does not exist" and every check stayed green. And a
project with no written law at all was never asked for one.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import admit, law  # noqa: E402


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


class AdmitLawTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "sample"
        self.project.mkdir()
        _write(self.project / ".pipeline" / "profile.json", {
            "name": "sample",
            "goals": {
                "vision": "every shelf is a library",
                "milestones": [{
                    "id": "goal-experience", "kind": "goal",
                    "title": "A great experience", "status": "pending",
                    "children": [{
                        "id": "f_offers", "kind": "milestone", "title": "Offers",
                        "status": "active", "serves": ["goal-experience"],
                        "measures": [{"text": "one page both readers answer on"}],
                    }],
                }],
            },
        })
        _write(self.project / ".pipeline" / "state.json", {"queue": []})

    def tearDown(self):
        self.tmp.cleanup()

    def _law(self, body: str | None) -> None:
        if body is not None:
            (self.project / "PRODUCT.md").write_text(body, encoding="utf-8")

    def _check(self, **kwargs):
        args = dict(project=self.project, query="add a price field",
                    kind="feature", advances="f_offers", why="readers asked",
                    verdict="aligned")
        args.update(kwargs)
        return admit.check(**args)

    def test_no_law_and_no_waiver_blocks_any_request(self):
        self._law(None)
        res = self._check(law="", effect="obeys")
        self.assertEqual("flag", res["verdict"])
        self.assertEqual("owner", res["who"])
        self.assertIn("PRODUCT.md", res["question"])
        self.assertIn("--waive", res["next"])

    def test_an_owner_waiver_lets_work_queue_without_a_clause(self):
        self._law(None)
        _write(self.project / ".pipeline" / "profile.json", {
            "name": "sample",
            "law_waiver": {"waived": True, "requested_by": "we will write it later"},
            "goals": {"vision": "every shelf is a library", "milestones": [{
                "id": "goal-experience", "kind": "goal", "title": "A great experience",
                "status": "pending",
                "children": [{"id": "f_offers", "kind": "milestone", "title": "Offers",
                              "status": "active", "serves": ["goal-experience"],
                              "measures": [{"text": "one page"}]}],
            }]},
        })
        res = self._check(law="", effect="")
        self.assertEqual("ok", res["verdict"])
        self.assertTrue(any("law:" in f for f in res["findings"]))

    def test_a_declared_law_requires_a_clause(self):
        self._law("- **P8** — money does not exist\n")
        res = self._check(law="", effect="obeys")
        self.assertEqual("flag", res["verdict"])
        self.assertEqual("agent", res["who"])
        self.assertIn("--law", res["next"])

    def test_an_unknown_clause_is_refused(self):
        self._law("- **P8** — money does not exist\n")
        res = self._check(law="P9", effect="obeys")
        self.assertEqual("flag", res["verdict"])
        self.assertIn("P9", res["question"])

    def test_the_effect_is_required_not_assumed(self):
        self._law("- **P8** — money does not exist\n")
        res = self._check(law="P8", effect="")
        self.assertEqual("flag", res["verdict"])
        self.assertEqual("agent", res["who"])
        self.assertIn("obeys", res["question"])

    def test_a_contradiction_queues_nothing_and_asks_the_owner(self):
        self._law("- **P8** — money does not exist\n")
        res = self._check(law="P8", effect="contradicts")
        self.assertEqual("flag", res["verdict"])
        self.assertEqual("owner", res["who"])
        self.assertIn("money does not exist", res["question"])
        self.assertIn("--amend", res["next"])
        self.assertIn("drop", res["question"])

    def test_an_amendment_is_allowed_and_recorded_on_the_entry(self):
        self._law("- **P8** — money does not exist\n")
        res = admit.admit(self.project, "add a price field", "feature",
                          "f_offers", "readers asked", "aligned",
                          law="P8", effect="amends", dry_run=True)
        self.assertEqual("ok", res["verdict"])
        self.assertTrue(any("AMENDS" in f for f in res["findings"]))
        self.assertEqual("amends", res["entry"]["law_effect"])
        entry = admit.entry_for({"name": "sample"}, "add a price field", "feature",
                                "f_offers", "readers asked", law="P8",
                                effect="amends")
        self.assertEqual("amends", entry["law_effect"])
        self.assertEqual("P8", entry["law"])

    def test_obeys_proceeds_with_the_clause_on_the_record(self):
        self._law("- **P8** — money does not exist\n")
        res = self._check(law="P8", effect="obeys")
        self.assertEqual("ok", res["verdict"])
        self.assertTrue(any("P8" in f for f in res["findings"]))

    def test_a_real_contradiction_is_recorded_not_queued(self):
        self._law("- **P8** — money does not exist\n")
        res = admit.admit(self.project, "add a price field", "feature",
                          "f_offers", "readers asked", "aligned",
                          law="P8", effect="contradicts")
        self.assertFalse(res["ok"])
        state = json.loads((self.project / ".pipeline" / "state.json").read_text())
        self.assertEqual([], state.get("queue") or [])
        self.assertEqual(1, len(state.get("admit_log") or []))
        self.assertEqual("owner", state["admit_log"][0]["who"])

    def test_an_obedient_request_is_pinned_with_its_clause(self):
        self._law("- **P8** — money does not exist\n")
        res = admit.admit(self.project, "show a note when a book is offered",
                          "feature", "f_offers", "readers asked", "aligned",
                          law="P8", effect="obeys")
        self.assertTrue(res["ok"])
        state = json.loads((self.project / ".pipeline" / "state.json").read_text())
        head = state["queue"][0]
        self.assertEqual("P8", head["law"])
        self.assertEqual("obeys", head["law_effect"])


if __name__ == "__main__":
    unittest.main()
