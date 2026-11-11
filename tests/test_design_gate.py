import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TEAM_ROOT))

from team import designer, verify  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _init_project_repo(tmp: Path, *, stitch_project_id: str = "11111", manifest_project_id: str = "11111") -> tuple[Path, Path]:
    project = tmp / "sample_app"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "test@example.com")
    _git(project, "config", "user.name", "Test")

    # Initial Kotlin UI file + exported Stitch screen
    ui_file = project / "composeApp" / "src" / "commonMain" / "kotlin" / "PassportScreen.kt"
    ui_file.parent.mkdir(parents=True, exist_ok=True)
    ui_file.write_text(
        "@Composable\nfun PassportScreen() {\n    Text(\"Hello\")\n}\n"
    )

    screen_dir = project / "docs" / "stitch" / "screens" / "scr1"
    screen_dir.mkdir(parents=True, exist_ok=True)
    (screen_dir / "screen.png").write_bytes(b"PNG_BASELINE_V1")
    (screen_dir / "index.html").write_text("<html><body><h1>Passport</h1></body></html>")
    (project / "docs" / "stitch" / "manifest.json").write_text(
        json.dumps(
            {
                "project_id": manifest_project_id,
                "synced_at": "2026-11-11T00:00:00Z",
                "canonical_screens": [
                    {
                        "id": "scr1",
                        "title": "Trust Passport",
                        "html": "screens/scr1/index.html",
                        "screenshot": "screens/scr1/screen.png",
                    }
                ],
            }
        )
    )

    _git(project, "add", "-A")
    _git(project, "commit", "-m", "initial")

    # Pipeline state lives outside git tracking, just like real projects.
    pipe = project / ".pipeline"
    pipe.mkdir()
    (pipe / "profile.json").write_text(
        json.dumps(
            {
                "name": "sample_app",
                "design": {"stitch_project_id": stitch_project_id},
            }
        )
    )
    return project, project


class DesignGateTests(unittest.TestCase):
    def _spec(self, label: str = "feat-ui-2026-11-11") -> dict:
        return {
            "id": "dt-feat-ui-001",
            "surface": {
                "label": label,
                "targets": ["composeApp/src/commonMain/**"],
            },
        }

    def test_existing_screen_refused_when_new_composable_added(self):
        with tempfile.TemporaryDirectory() as td:
            project, wt = _init_project_repo(Path(td))
            spec = self._spec()
            designer.record(
                project,
                spec,
                {
                    "decision": "existing_screen",
                    "screen": {"id": "scr1", "title": "Trust Passport"},
                    "reason": "reusing screen",
                },
                root=wt,
            )
            ui_file = wt / "composeApp" / "src" / "commonMain" / "kotlin" / "PassportScreen.kt"
            ui_file.write_text(
                "@Composable\nfun PassportScreen() {\n    ProfileLocationCard()\n}\n\n"
                "@Composable\nfun ProfileLocationCard() {\n    Column { Text(\"New\") }\n}\n"
            )
            files = verify.changed_files(wt, "HEAD")
            ok, msg = verify.design_gate(project, wt, spec, files, base="HEAD")
            self.assertFalse(ok)
            self.assertIn("EXISTING_SCREEN CANNOT COVER NEW UI STRUCTURE", msg)
            self.assertIn("ProfileLocationCard", msg)

    def test_existing_screen_allowed_for_copy_only_edit(self):
        with tempfile.TemporaryDirectory() as td:
            project, wt = _init_project_repo(Path(td))
            spec = self._spec()
            designer.record(
                project,
                spec,
                {
                    "decision": "existing_screen",
                    "screen": {"id": "scr1", "title": "Trust Passport"},
                    "reason": "copy tweak",
                },
                root=wt,
            )
            ui_file = wt / "composeApp" / "src" / "commonMain" / "kotlin" / "PassportScreen.kt"
            ui_file.write_text(
                "@Composable\nfun PassportScreen() {\n    Text(\"Updated Copy\")\n}\n"
            )
            files = verify.changed_files(wt, "HEAD")
            ok, msg = verify.design_gate(project, wt, spec, files, base="HEAD")
            self.assertTrue(ok, msg)

    def test_tweak_screen_refused_without_exported_stitch_change(self):
        with tempfile.TemporaryDirectory() as td:
            project, wt = _init_project_repo(Path(td))
            spec = self._spec()
            designer.record(
                project,
                spec,
                {
                    "decision": "tweak_screen",
                    "screen": {"id": "scr1", "title": "Trust Passport"},
                    "tweak": "Add ProfileLocationCard below banner",
                    "reason": "needs card",
                },
                root=wt,
            )
            ui_file = wt / "composeApp" / "src" / "commonMain" / "kotlin" / "PassportScreen.kt"
            ui_file.write_text(
                "@Composable\nfun PassportScreen() {\n    ProfileLocationCard()\n}\n\n"
                "@Composable\nfun ProfileLocationCard() {\n    Column { Text(\"New\") }\n}\n"
            )
            files = verify.changed_files(wt, "HEAD")
            ok, msg = verify.design_gate(project, wt, spec, files, base="HEAD")
            self.assertFalse(ok)
            self.assertIn("TWEAK_SCREEN DID NOT EXPORT A STITCH TWEAK", msg)

    def test_tweak_screen_passes_when_snapshot_exported_in_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            project, wt = _init_project_repo(Path(td))
            spec = self._spec()
            designer.record(
                project,
                spec,
                {
                    "decision": "tweak_screen",
                    "screen": {"id": "scr1", "title": "Trust Passport"},
                    "tweak": "Add ProfileLocationCard below banner",
                    "reason": "needs card",
                },
                root=wt,
            )
            (wt / "docs" / "stitch" / "screens" / "scr1" / "screen.png").write_bytes(b"PNG_TWEAKED_V2")
            (wt / "docs" / "stitch" / "screens" / "scr1" / "index.html").write_text(
                "<html><body><h1>Passport + Location</h1></body></html>"
            )
            ui_file = wt / "composeApp" / "src" / "commonMain" / "kotlin" / "PassportScreen.kt"
            ui_file.write_text(
                "@Composable\nfun PassportScreen() {\n    ProfileLocationCard()\n}\n\n"
                "@Composable\nfun ProfileLocationCard() {\n    Column { Text(\"New\") }\n}\n"
            )
            files = verify.changed_files(wt, "HEAD")
            ok, msg = verify.design_gate(project, wt, spec, files, base="HEAD")
            self.assertTrue(ok, msg)

    def test_manifest_project_mismatch_blocks_record_and_verify(self):
        with tempfile.TemporaryDirectory() as td:
            project, wt = _init_project_repo(
                Path(td),
                stitch_project_id="99999",
                manifest_project_id="11111",
            )
            spec = self._spec()
            with self.assertRaises(ValueError):
                designer.record(
                    project,
                    spec,
                    {
                        "decision": "existing_screen",
                        "screen": {"id": "scr1", "title": "Trust Passport"},
                    },
                    root=wt,
                )
            ui_file = wt / "composeApp" / "src" / "commonMain" / "kotlin" / "PassportScreen.kt"
            ui_file.write_text("@Composable\nfun PassportScreen() { Text(\"X\") }\n")
            files = verify.changed_files(wt, "HEAD")
            ok, msg = verify.design_gate(project, wt, spec, files, base="HEAD")
            self.assertFalse(ok)
            self.assertIn("STITCH MANIFEST MISMATCH", msg)


if __name__ == "__main__":
    unittest.main()
