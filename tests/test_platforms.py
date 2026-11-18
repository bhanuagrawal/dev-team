"""EVERY BUILDABLE UNIT HAS A GATE — derived from the tree, refused in the diff.

The incident these tests lock down: BookSwap's browser client (`web/`, its own
`package.json`) merged while `verify` ran Gradle alone, so a change to a shared
rule could PASS, ship, and leave the second client on the old copy.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import platforms


def project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


class InventoryTest(unittest.TestCase):
    def test_a_directory_with_a_manifest_is_a_unit(self):
        root = project({
            "settings.gradle.kts": "",
            "composeApp/build.gradle.kts": "",
            "web/package.json": "{}",
        })
        found = {u["label"]: u for u in platforms.units(root)}
        self.assertIn("gradle", found)
        self.assertIn("web", found)
        self.assertEqual("web", found["web"]["dir"])
        self.assertEqual("node", found["web"]["kind"])

    def test_a_gradle_module_is_not_a_second_platform(self):
        # build.gradle.kts marks a MODULE; only settings.gradle* marks a build.
        root = project({"settings.gradle.kts": "", "app/build.gradle.kts": ""})
        self.assertEqual(["gradle"], [u["label"] for u in platforms.units(root)])

    def test_dependencies_and_build_output_are_not_platforms(self):
        root = project({
            "settings.gradle.kts": "",
            "web/package.json": "{}",
            "web/node_modules/dep/package.json": "{}",
            "web/dist/package.json": "{}",
        })
        self.assertEqual(["gradle", "web"], [u["label"] for u in platforms.units(root)])

    def test_an_xcode_project_is_a_unit_at_its_own_directory(self):
        root = project({
            "settings.gradle.kts": "",
            "iosApp/iosApp.xcodeproj/project.pbxproj": "// project",
        })
        labels = {u["label"]: u for u in platforms.units(root)}
        self.assertIn("iosApp", labels)
        self.assertEqual("swift", labels["iosApp"]["kind"])


class GateDeclarationTest(unittest.TestCase):
    def test_the_legacy_compile_task_is_one_gate(self):
        gates = platforms.gates({"verify": {"compile": ":app:compileDebugKotlin"}})
        self.assertEqual(1, len(gates))
        self.assertEqual("", gates[0]["cwd"])
        self.assertTrue(gates[0]["gradle"])

    def test_a_task_list_is_read_in_order_with_its_cwd(self):
        gates = platforms.gates({"verify": {"tasks": [
            {"label": "android", "cmd": "./gradlew assembleDebug"},
            {"label": "web", "cmd": "npm run build", "cwd": "web/", "timeout": 60},
            "cargo build",
        ]}})
        self.assertEqual(["android", "web", "task-3"], [g["label"] for g in gates])
        self.assertEqual("web", gates[1]["cwd"])
        self.assertEqual(60, gates[1]["timeout"])
        self.assertFalse(gates[1]["gradle"])

    def test_a_root_gate_does_not_cover_a_nested_platform(self):
        # The whole point: the Gradle gate at the root must not "cover" web/.
        root = project({"settings.gradle.kts": "", "web/package.json": "{}"})
        missing = platforms.ungated(platforms.units(root),
                                    {"verify": {"compile": "./gradlew build"}})
        self.assertEqual(["web"], [u["label"] for u in missing])

    def test_a_declared_gate_covers_its_own_unit(self):
        root = project({"settings.gradle.kts": "", "web/package.json": "{}"})
        profile = {"verify": {"tasks": [
            {"label": "android", "cmd": "./gradlew build"},
            {"label": "web", "cmd": "npm run build", "cwd": "web"},
        ]}}
        self.assertEqual([], platforms.ungated(platforms.units(root), profile))


class DiffGateTest(unittest.TestCase):
    def test_a_diff_inside_an_ungated_unit_is_refused_with_the_profile_line(self):
        root = project({"settings.gradle.kts": "", "web/package.json": "{}"})
        ok, message = platforms.diff_gate(
            root, {"verify": {"compile": "./gradlew build"}},
            ["settings.gradle.kts", "web/src/core/location.ts"],
        )
        self.assertFalse(ok)
        self.assertIn("A PLATFORM CHANGED WITH NO GATE", message)
        self.assertIn("web/package.json", message)
        self.assertIn('"cwd": "web"', message)

    def test_a_diff_in_a_gated_unit_passes(self):
        root = project({"settings.gradle.kts": "", "web/package.json": "{}"})
        profile = {"verify": {"tasks": [
            {"label": "android", "cmd": "true"},
            {"label": "web", "cmd": "true", "cwd": "web"},
        ]}}
        ok, message = platforms.diff_gate(root, profile, ["web/src/App.tsx"])
        self.assertTrue(ok, message)

    def test_a_repo_level_file_belongs_to_no_platform(self):
        # A README never opens a platform, so it never charges a gate.
        root = project({"web/package.json": "{}", "README.md": "hi"})
        self.assertIsNone(platforms.unit_for(platforms.units(root), "README.md"))

    def test_a_nested_file_belongs_to_the_deepest_unit(self):
        root = project({
            "settings.gradle.kts": "",
            "web/package.json": "{}",
            "web/scripts/package.json": "{}",
        })
        unit = platforms.unit_for(platforms.units(root), "web/scripts/x.js")
        self.assertEqual("web/scripts", unit["dir"])


class RunGatesTest(unittest.TestCase):
    def test_every_declared_gate_runs_and_the_first_failure_stops_the_chain(self):
        root = project({})
        lines = []
        ok, message = platforms.run_gates(
            root, {"verify": {"tasks": [{"label": "first", "cmd": "true"}]}}, lines.append)
        self.assertTrue(ok, message)

        ok, message = platforms.run_gates(
            root, {"verify": {"tasks": [
                {"label": "broken", "cmd": "exit 3"},
                {"label": "never", "cmd": "true"},
            ]}}, lines.append)
        self.assertFalse(ok)
        self.assertIn("broken FAILED", message)
        self.assertFalse(any("never" in line for line in lines))

    def test_a_gate_naming_a_missing_directory_says_so(self):
        root = project({})
        ok, message = platforms.run_gates(
            root, {"verify": {"tasks": [
                {"label": "web", "cmd": "true", "cwd": "web"},
            ]}}, lambda *_: None)
        self.assertFalse(ok)
        self.assertIn("does not exist", message)


if __name__ == "__main__":
    unittest.main()
