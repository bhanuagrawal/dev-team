import json
import sys
import tempfile
import unittest
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TEAM_ROOT))

from team import spec  # noqa: E402


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n" if not isinstance(data, str) else data)


class SpecContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "sample_app"
        self.project.mkdir()
        _write(self.project / ".pipeline" / "profile.json", {
            "name": "sample_app",
            "design": {"stitch_project_id": "424242", "design_system_id": "sys-1"},
            "verify": {"compile": ":composeApp:compileDebugKotlinAndroid",
                       "test": ":composeApp:testDebugUnitTest"},
            "goals": {"vision": "one shelf, shared", "milestones": [
                {"id": "f_offers", "kind": "milestone", "title": "Offers",
                 "status": "active", "phase": 1, "serves": ["goal-experience"],
                 "outcome": "one page both readers answer on",
                 "measures": [{"text": "Make Offer opens a composer"},
                              {"text": "no borrow appears anywhere"}]},
                {"id": "goal-experience", "kind": "goal", "title": "A great experience",
                 "status": "pending", "children": []},
            ]},
        })
        _write(self.project / "docs" / "stitch" / "manifest.json", {
            "project_id": "424242",
            "synced_at": "2026-11-11T00:00:00Z",
            "canonical_screens": [{
                "id": "scr1", "title": "Book Detail",
                "html": "screens/scr1/index.html",
                "screenshot": "screens/scr1/screen.png",
            }],
        })
        screen = self.project / "docs" / "stitch" / "screens" / "scr1"
        screen.mkdir(parents=True)
        (screen / "index.html").write_text("<html></html>")
        (screen / "screen.png").write_bytes(b"PNG")
        _write(self.project / "docs" / "ux-flows.json", {
            "flows": [{"id": "request-a-book", "surface": "Discovery",
                       "goal": "make an offer and reach an agreed swap"}]
        })
        (self.project / "AGENTS.md").write_text("# rules\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _ui_task(self, label: str = "bugfix-borrow-2026-11-11") -> dict:
        return {
            "id": "dt-bugfix-borrow-001",
            "type": "bugfix",
            "advances": "f_offers",
            "surface": {"label": label,
                        "targets": ["composeApp/src/commonMain/**"]},
        }

    def test_ui_task_without_answer_is_blocked_and_names_the_command(self):
        ctx = spec.context(self.project, self._ui_task())
        design = ctx["design"]
        self.assertEqual("pending", design["state"])
        self.assertTrue(design["blocked"])
        self.assertTrue(design["required"])
        self.assertIn("design-consult", design["command"])
        self.assertIn("BLOCKED", design["instruction"])

    def test_recorded_existing_screen_is_answered_and_blocks_new_structure(self):
        from team import designer
        _write(self.project / ".pipeline" / "design_consult.json", {
            "tasks": {"bugfix-borrow-2026-11-11": {
                "task": "bugfix-borrow-2026-11-11",
                "decision": "existing_screen",
                "screen": {"id": "scr1", "title": "Book Detail"},
                "reason": "the detail screen already covers the surface",
                "fingerprint": designer.fingerprint(self.project),
            }}
        })
        design = spec.context(self.project, self._ui_task())["design"]
        self.assertEqual("answered", design["state"])
        self.assertFalse(design["blocked"])
        self.assertIn("Book Detail", design["instruction"])
        self.assertIn("docs/stitch/screens/scr1/", design["instruction"])

    def test_non_ui_task_needs_no_design(self):
        task = self._ui_task()
        task["surface"]["targets"] = ["docs/"]
        design = spec.context(self.project, task)["design"]
        self.assertEqual("not_applicable", design["state"])
        self.assertFalse(design["required"])

    def test_context_carries_goal_build_conventions_and_journeys(self):
        ctx = spec.context(self.project, self._ui_task())
        self.assertEqual("f_offers", ctx["goal"]["id"])
        self.assertEqual(2, len(ctx["goal"]["measures"]))
        self.assertIn("compileDebugKotlinAndroid", ctx["build"]["compile"])
        self.assertTrue(any("AGENTS.md" in p for p in ctx["conventions"]))
        self.assertEqual("request-a-book", ctx["journeys"][0]["id"])

    def test_refresh_rewrites_an_existing_spec_in_place(self):
        path = self.project / ".pipeline" / "tasks" / "dt-bugfix-borrow-001.json"
        _write(path, self._ui_task())
        spec.refresh(self.project, path)
        data = json.loads(path.read_text())
        self.assertEqual("pending", data["context"]["design"]["state"])
        self.assertEqual("f_offers", data["context"]["goal"]["id"])


if __name__ == "__main__":
    unittest.main()
