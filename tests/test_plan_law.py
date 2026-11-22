"""THE PLAN × THE LAW — what gets built carries the clause it serves.

The plan used to be the one door into the queue that no law gate watched: its
JSON schema had no `law` field, the prompt never mentioned PRODUCT.md, and the
entries it created arrived with `kind/law/why` all empty. Owner decision
2026-11-22: what gets built always aligns with the product rule. So the plan
names a clause for every entry, inherits one when it can, and may never amend or
defy the law — that is the owner's call.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import goals, law, research  # noqa: E402


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


class PlanLawValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "sample"
        self.project.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _law(self, body: str | None) -> None:
        if body is not None:
            (self.project / "PRODUCT.md").write_text(body, encoding="utf-8")

    def _entry(self, **over):
        entry = {"type": "feature", "label": "offers-page", "reason": "why",
                 "problem": "the page is empty", "done_when": ["it is not"],
                 "law": ["P4"]}
        entry.update(over)
        return entry

    def test_a_planned_entry_must_name_a_clause(self):
        self._law("- **P4** — an offer names two copies\n")
        result = {"queue": [self._entry(law=[])]}
        err = research._validate_law(self.project, result)
        self.assertIsNotNone(err)
        self.assertIn("offers-page", err)
        self.assertIn("P4", err)

    def test_an_unknown_clause_in_the_plan_is_fiction(self):
        self._law("- **P4** — an offer names two copies\n")
        err = research._validate_law(self.project, {"queue": [self._entry(law=["P9"])]})
        self.assertIn("P9", err)

    def test_obeys_with_a_real_clause_passes(self):
        self._law("- **P4** — an offer names two copies\n")
        self.assertIsNone(research._validate_law(
            self.project, {"queue": [self._entry()]}))

    def test_the_plan_may_not_amend_the_law(self):
        self._law("- **P4** — an offer names two copies\n")
        err = research._validate_law(
            self.project, {"queue": [self._entry(law_effect="amends")]})
        self.assertIn("issues_to_file", err)
        self.assertIn("owner", err)

    def test_the_plan_may_not_contradict_the_law(self):
        self._law("- **P8** — money does not exist\n")
        err = research._validate_law(
            self.project, {"queue": [self._entry(law=["P8"], law_effect="contradicts")]})
        self.assertIn("issues_to_file", err)

    def test_a_waived_project_needs_no_clause(self):
        self._law(None)
        _write(self.project / ".pipeline" / "profile.json", {
            "name": "sample", "law_waiver": {"waived": True, "requested_by": "later"},
        })
        self.assertIsNone(research._validate_law(self.project, {"queue": [self._entry(law=[])]}))

    def test_a_goal_branch_citing_fiction_is_refused(self):
        self._law("- **P4** — an offer names two copies\n")
        err = research._validate_law(self.project, {
            "queue": [], "goal_changes": [{"action": "add", "title": "Offers",
                                           "law": ["P7"], "why": "plan moved"}],
        })
        self.assertIn("P7", err)


class PlanLawInheritanceTest(unittest.TestCase):
    def _profile(self):
        return {"goals": {"vision": "every shelf is a library", "milestones": [{
            "id": "goal-experience", "kind": "goal", "title": "A great experience",
            "status": "pending",
            "children": [{"id": "f_offers", "kind": "milestone", "title": "Offers",
                          "status": "active", "law": ["P4", "P5"]}],
        }]}}

    def test_an_entry_inherits_its_milestone_s_clauses(self):
        entries = [{"type": "bugfix", "label": "empty-table", "advances": "f_offers"}]
        research._inherit_law(self._profile(), entries)
        self.assertEqual("P4,P5", entries[0]["law"])
        self.assertEqual("obeys", entries[0]["law_effect"])

    def test_an_entry_s_own_clause_wins(self):
        entries = [{"type": "bugfix", "label": "empty-table", "advances": "f_offers",
                    "law": "P10"}]
        research._inherit_law(self._profile(), entries)
        self.assertEqual("P10", entries[0]["law"])

    def test_no_milestone_and_no_clause_leaves_it_empty_for_the_gate(self):
        entries = [{"type": "feature", "label": "off-goal", "advances": "none"}]
        research._inherit_law(self._profile(), entries)
        self.assertEqual("", entries[0]["law"])

    def test_the_planner_prompt_carries_the_law(self):
        self.assertIn("{law}", research.PROMPT_TMPL)

    def test_a_new_milestone_keeps_the_clauses_it_exists_to_render(self):
        profile = {"goals": {"vision": "v", "milestones": [{
            "id": "g", "kind": "goal", "title": "Experience", "status": "pending"}]}}
        node, what, why, err = goals.apply_one(profile, {
            "action": "add", "title": "Offers", "parent": "g",
            "kind": "milestone", "why": "planned", "law": ["P4", "p5"],
        })
        self.assertEqual("", err)
        kids = profile["goals"]["milestones"][0]["children"]
        self.assertEqual(["P4", "P5"], kids[0]["law"])


if __name__ == "__main__":
    unittest.main()
