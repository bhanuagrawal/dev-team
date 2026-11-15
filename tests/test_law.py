"""THE LAW — clauses are declared once, cited everywhere, and never fiction."""

import sys
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


if __name__ == "__main__":
    unittest.main()
