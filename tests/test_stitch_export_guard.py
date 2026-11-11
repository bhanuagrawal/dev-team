import json
import sys
import tempfile
import unittest
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TEAM_ROOT))

from team import designer  # noqa: E402


LEDGER = {
    "project_id": "11111",
    "synced_at": "2026-10-30T06:13:22+00:00",
    "canonical_screens": [
        {"id": "aaa", "title": "Home"},
        {"id": "bbb", "title": "Settings"},
    ],
    "retired_screens": [],
}


class StitchExportGuardTest(unittest.TestCase):
    """A wiped Stitch canvas must never be written over the ledger as an empty spec.

    The run that found this: a project's six canonical screens had been deleted from
    Stitch, `stitch-export` listed zero screens, wrote an empty manifest and COMMITTED
    it — the design spec was gone and every UI gate downstream was blind.
    """

    def test_the_mirror_is_the_ledger_when_the_tracked_manifest_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "app"
            (project / ".pipeline").mkdir(parents=True)
            (project / ".pipeline" / "stitch_manifest.json").write_text(json.dumps(LEDGER))

            got = designer.ledger(project)

            self.assertEqual(["aaa", "bbb"], [s["id"] for s in got["canonical_screens"]])

    def test_an_empty_export_is_refused_while_the_ledger_names_surfaces(self):
        problem = designer.empty_export_refusal(LEDGER, live_count=0)

        self.assertIsNotNone(problem)
        self.assertIn("0 screens", problem)
        self.assertIn("2 surface(s)", problem)

    def test_an_empty_export_is_refused_even_without_a_ledger(self):
        problem = designer.empty_export_refusal({}, live_count=0)

        self.assertIsNotNone(problem)
        self.assertIn("nothing to export", problem)

    def test_a_real_export_is_never_refused(self):
        self.assertIsNone(designer.empty_export_refusal(LEDGER, live_count=3))


if __name__ == "__main__":
    unittest.main()
