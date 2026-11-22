"""THE LAW — clauses are declared once, cited everywhere, and never fiction."""

import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import law


class LawParsingTest(unittest.TestCase):
    def _project(self, files: dict) -> Path:
        root = Path(tempfile.mkdtemp())
        for name, body in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return root

    def test_no_law_file_means_no_law(self):
        root = self._project({"README.md": "# hi\n"})
        result = law.audit(root, state={})
        self.assertFalse(result["present"])
        self.assertEqual([], result["clauses"])

    def test_bullets_and_tables_are_both_clauses(self):
        root = self._project({
            "PRODUCT.md": (
                "# Product\n"
                "- **P1** — a wish is an ad\n"
                "- P2 — one copy each way\n"
                "| P3 | one area |\n"
            ),
        })
        ids = [c["id"] for c in law.clauses(root)]
        self.assertEqual(["P1", "P2", "P3"], ids)

    def test_a_clause_nothing_cites_is_dead_law(self):
        root = self._project({
            "PRODUCT.md": "- **P1** — a wish is an ad\n- **P2** — one copy each way\n",
            "code.py": "# Law: P1\n",
        })
        result = law.audit(root, state={})
        self.assertEqual(["P2"], result["orphans"])

    def test_a_citation_of_a_missing_clause_is_fiction(self):
        root = self._project({
            "PRODUCT.md": "- **P1** — a wish is an ad\n",
            "code.py": "# Law: P1, P9\n",
        })
        result = law.audit(root, state={})
        self.assertEqual(["P9"], result["unknown"])

    def test_p0_is_a_priority_word_not_a_clause(self):
        root = self._project({
            "PRODUCT.md": "- **P1** — a wish is an ad\n",
            "plan.md": "P0 is the ship-blocking tier; P1 the next one.\n",
            "code.py": "# Law: P1\n",
        })
        result = law.audit(root, state={})
        self.assertEqual([], result["unknown"])
        self.assertEqual([], result["orphans"])

    def test_a_queued_task_must_name_its_clause(self):
        root = self._project({
            "PRODUCT.md": "- **P1** — a wish is an ad\n",
            "code.py": "# Law: P1\n",
        })
        state = {"queue": [
            {"label": "feature-one", "kind": "feature", "law": "P1"},
            {"label": "feature-two", "kind": "feature", "law": ""},
            {"label": "release-1.0", "kind": "release", "law": ""},
        ]}
        result = law.audit(root, state=state)
        self.assertEqual(["feature-two"], result["tasks_without_law"])

    def test_the_law_file_does_not_cite_itself(self):
        root = self._project({
            "PRODUCT.md": "- **P1** — a wish is an ad (see P1)\n",
        })
        self.assertEqual(["P1"], law.audit(root, state={})["orphans"])


class LawBindingTest(unittest.TestCase):
    """`binds:` — a clause that spans platforms, checked per platform.

    The incident: a location clause was satisfied by the Kotlin seam alone while
    the browser client shipped a hand-copy of the old rule on the same disk.
    """

    def _project(self, files: dict) -> Path:
        root = Path(tempfile.mkdtemp())
        for name, body in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return root

    def _bindings(self, mapping: dict) -> str:
        return json.dumps({"law_bindings": mapping})

    def test_the_marker_leaves_the_sentence(self):
        root = self._project({
            "PRODUCT.md": "- **P1** — a place is named by the device (binds: app, web)\n",
        })
        clause = law.clauses(root)[0]
        self.assertEqual(["app", "web"], clause["binds"])
        self.assertEqual("a place is named by the device", clause["text"])

    def test_a_binding_its_own_platform_ignores_is_a_finding(self):
        root = self._project({
            "PRODUCT.md": "- **P1** — a place is named by the device (binds: app, web)\n",
            "composeApp/A.kt": "// Law: P1\n",
            ".pipeline/profile.json": self._bindings({
                "app": ["composeApp/**"], "web": ["web/**"],
            }),
        })
        result = law.audit(root, state={})
        self.assertEqual([], result["orphans"])  # cited, so not dead law
        self.assertEqual(
            # `glob` names the pattern that was checked and failed: without it
              # a finding said "web is uncited" and left the reader guessing
              # which of web's globs was meant (b92faa3).
            [{"clause": "P1", "binding": "web", "glob": "web/**",
              "reason": "uncited"}],
            result["unbound"],
        )

    def test_every_declared_platform_citing_it_passes(self):
        root = self._project({
            "PRODUCT.md": "- **P1** — a place is named by the device (binds: app, web)\n",
            "composeApp/A.kt": "// Law: P1\n",
            "web/src/location.ts": "// Law: P1\n",
            ".pipeline/profile.json": self._bindings({
                "app": ["composeApp/**"], "web": ["web/**"],
            }),
        })
        self.assertEqual([], law.audit(root, state={})["unbound"])

    def test_a_binding_the_project_never_mapped_is_a_finding(self):
        root = self._project({
            "PRODUCT.md": "- **P1** — one copy each way (binds: backend)\n",
            "code.py": "# Law: P1\n",
            ".pipeline/profile.json": self._bindings({"app": ["composeApp/**"]}),
        })
        self.assertEqual(
            [{"clause": "P1", "binding": "backend", "glob": "",
              "reason": "unmapped"}],
            law.audit(root, state={})["unbound"],
        )


class LawDeclarationTest(unittest.TestCase):
    """Declaring the law is mandatory; the only other legal state is a waiver.

    Owner decision 2026-11-22: every app the team manages (and every new one)
    declares what the product is, or the owner says in their own words that they
    will run without a written law. Silence is not one of the three states.
    """

    def _project(self, files: dict) -> Path:
        root = Path(tempfile.mkdtemp())
        for name, body in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return root

    def test_no_law_and_no_waiver_is_blocked_and_names_both_fixes(self):
        root = self._project({"README.md": "# hi\n"})
        status, note = law.readiness(root, {})
        self.assertEqual("blocked", status)
        self.assertIn("PRODUCT.md", note)
        self.assertIn("--waive", note)
        self.assertIn("--requested-by", note)

    def test_a_declared_law_is_ready(self):
        root = self._project({"PRODUCT.md": "- **P1** — one copy each way\n"})
        self.assertEqual(("ready", ""), law.readiness(root, {}))

    def test_an_owner_waiver_is_ready_and_says_who_decided(self):
        root = self._project({"README.md": "# hi\n"})
        profile = {law.WAIVER_KEY: {"waived": True,
                                   "requested_by": "ship it, we will write the law later",
                                   "at": "2026-11-22T10:00:00Z"}}
        status, note = law.readiness(root, profile)
        self.assertEqual("ready", status)
        self.assertIn("waived", note)
        self.assertIn("ship it", note)

    def test_a_false_waiver_flag_is_not_a_waiver(self):
        root = self._project({"README.md": "# hi\n"})
        self.assertEqual("blocked", law.readiness(root, {law.WAIVER_KEY: {"waived": False}})[0])

    def test_audit_carries_the_decision(self):
        root = self._project({"README.md": "# hi\n"})
        result = law.audit(root, state={})
        self.assertEqual("blocked", result["status"])
        self.assertFalse(result["waived"])


class LawAmendTest(unittest.TestCase):
    """An amendment is the owner's act: their words, one clause, `binds:` kept."""

    def _project(self) -> Path:
        root = Path(tempfile.mkdtemp())
        (root / "PRODUCT.md").write_text(
            "# Product\n- **P1** — a wish is an ad\n"
            "- **P2** — a place is named by the device (binds: app, web)\n"
            "- **P3** — money does not exist\n",
            encoding="utf-8",
        )
        return root

    def test_an_amendment_rewrites_one_clause_only(self):
        root = self._project()
        ok, message = law.amend(root, "P3", "a deal may name a price",
                                "we are adding paid swaps")
        self.assertTrue(ok)
        self.assertIn("P3", message)
        clauses = {c["id"]: c for c in law.clauses(root)}
        self.assertEqual("a deal may name a price", clauses["P3"]["text"])
        self.assertEqual("a wish is an ad", clauses["P1"]["text"])

    def test_the_binds_marker_survives_a_reword(self):
        root = self._project()
        ok, _ = law.amend(root, "P2", "a place is the device's geocoder",
                          "clearer words, same rule")
        self.assertTrue(ok)
        clause = {c["id"]: c for c in law.clauses(root)}["P2"]
        self.assertEqual(["app", "web"], clause["binds"])
        self.assertEqual("a place is the device's geocoder", clause["text"])

    def test_a_new_binds_marker_wins(self):
        root = self._project()
        ok, _ = law.amend(root, "P1", "a wish is an ad (binds: app)",
                          "the web client dropped it")
        self.assertTrue(ok)
        self.assertEqual(["app"], {c["id"]: c for c in law.clauses(root)}["P1"]["binds"])

    def test_no_owner_words_no_amendment(self):
        root = self._project()
        ok, message = law.amend(root, "P1", "something else", "")
        self.assertFalse(ok)
        self.assertIn("--requested-by", message)
        self.assertEqual("a wish is an ad",
                         {c["id"]: c for c in law.clauses(root)}["P1"]["text"])

    def test_an_unknown_clause_changes_nothing(self):
        root = self._project()
        before = (root / "PRODUCT.md").read_text()
        ok, message = law.amend(root, "P9", "invented", "owner words")
        self.assertFalse(ok)
        self.assertIn("P9", message)
        self.assertEqual(before, (root / "PRODUCT.md").read_text())


class LawEntryBlockTest(unittest.TestCase):
    """`pick` refuses a queued task with no clause — the last gate before code."""

    def _project(self, law_body: str | None) -> Path:
        root = Path(tempfile.mkdtemp())
        if law_body is not None:
            (root / "PRODUCT.md").write_text(law_body, encoding="utf-8")
        (root / ".pipeline").mkdir()
        (root / ".pipeline" / "profile.json").write_text(
            json.dumps({"name": "sample"}), encoding="utf-8")
        return root

    def test_a_clause_less_entry_is_refused(self):
        root = self._project("- **P1** — one copy each way\n")
        reason = law.entry_block(root, {"label": "bugfix-x", "kind": "bugfix"})
        self.assertIn("bugfix-x", reason)
        self.assertIn("--law", reason)

    def test_a_cited_entry_passes(self):
        root = self._project("- **P1** — one copy each way\n")
        self.assertEqual("", law.entry_block(
            root, {"label": "bugfix-x", "kind": "bugfix", "law": "P1"}))

    def test_release_and_store_are_exempt(self):
        root = self._project("- **P1** — one copy each way\n")
        self.assertEqual("", law.entry_block(root, {"label": "r", "kind": "release"}))
        self.assertEqual("", law.entry_block(root, {"label": "s", "kind": "store"}))

    def test_a_waived_project_asks_nothing(self):
        root = self._project(None)
        profile = {law.WAIVER_KEY: {"waived": True, "requested_by": "later"}}
        self.assertEqual("", law.entry_block(
            root, {"label": "bugfix-x", "kind": "bugfix"}, profile))

    def test_no_law_and_no_waiver_is_not_this_gate(self):
        # The project-level gate (`pick`, rc 5) speaks first; entry_block is silent.
        root = self._project(None)
        self.assertEqual("", law.entry_block(root, {"label": "x", "kind": "bugfix"}))


class LawPromptTest(unittest.TestCase):
    """The planner reads the law: clauses as constraints, not as a footnote."""

    def _project(self, law_body: str | None) -> Path:
        root = Path(tempfile.mkdtemp())
        if law_body is not None:
            (root / "PRODUCT.md").write_text(law_body, encoding="utf-8")
        (root / ".pipeline").mkdir()
        (root / ".pipeline" / "profile.json").write_text(
            json.dumps({"name": "sample"}), encoding="utf-8")
        return root

    def test_every_clause_and_the_citation_rule_reach_the_prompt(self):
        root = self._project(
            "- **P1** — a wish is an ad\n"
            "- **P2** — a place is named by the device (binds: app, web)\n")
        block = law.prompt_block(root)
        self.assertIn("P1 — a wish is an ad", block)
        self.assertIn("P2 — a place is named by the device", block)
        self.assertIn("binds app, web", block)
        self.assertIn('"law": ["<Pn>"', block)
        self.assertIn("owner's act", block)

    def test_an_undeclared_law_is_said_not_invented(self):
        block = law.prompt_block(self._project(None))
        self.assertIn("has not declared one", block)
        self.assertIn("issues_to_file", block)

    def test_a_waiver_is_stated_as_the_owner_s_decision(self):
        root = self._project(None)
        profile = {law.WAIVER_KEY: {"waived": True, "requested_by": "later"}}
        block = law.prompt_block(root, profile)
        self.assertIn("waived", block)
        self.assertIn("do not invent", block.lower())


if __name__ == "__main__":
    unittest.main()
