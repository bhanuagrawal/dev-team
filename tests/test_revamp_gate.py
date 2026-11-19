"""The revamp gate counts application code in the stack the project ships.

BookSwap's 2026-11-19 website revamp touched only `web/src` (the browser client
is the application for that surface) and the gate refused it with "0 code file
changes", refusing a real rewrite because no `.kt` file moved.
"""

import sys
import unittest
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TEAM_ROOT))

from team import verify  # noqa: E402


SPEC = {"id": "revamp-website", "kind": "revamp", "label": "revamp the website"}


class RevampGateTests(unittest.TestCase):
    def test_web_client_revamp_passes(self) -> None:
        files = [
            "web/src/App.tsx",
            "web/src/ui/HomePage.tsx",
            "web/src/core/home.ts",
            "web/tests/home.test.ts",
            "docs/web-client.md",
        ]
        ok, why = verify.revamp_gate(Path("."), Path("."), SPEC, files)
        self.assertTrue(ok, why)
        self.assertIn("3 code files", why)

    def test_kotlin_revamp_still_passes(self) -> None:
        files = [
            "app/src/main/java/a/Main.kt",
            "app/src/main/java/a/Home.kt",
            "app/src/main/java/a/Shelf.kt",
        ]
        ok, why = verify.revamp_gate(Path("."), Path("."), SPEC, files)
        self.assertTrue(ok, why)

    def test_a_tweak_is_not_a_rebuild(self) -> None:
        files = ["web/src/App.tsx", "web/tests/home.test.ts", "docs/web-client.md"]
        ok, why = verify.revamp_gate(Path("."), Path("."), SPEC, files)
        self.assertFalse(ok)
        self.assertIn("1 code file", why)

    def test_test_files_do_not_count_as_application_code(self) -> None:
        self.assertFalse(verify.is_app_code("web/tests/home.test.ts"))
        self.assertFalse(verify.is_app_code("web/src/core/home.test.ts"))
        self.assertFalse(verify.is_app_code("app/src/test/java/a/MainTest.kt"))
        self.assertTrue(verify.is_app_code("web/src/core/home.ts"))

    def test_dot_slash_paths_are_the_same_file(self) -> None:
        self.assertTrue(verify.is_app_code("./web/src/ui/HomePage.tsx"))

    def test_a_non_revamp_task_is_not_judged(self) -> None:
        ok, why = verify.revamp_gate(Path("."), Path("."), {"kind": "feature"}, [])
        self.assertTrue(ok)
        self.assertIn("not a revamp", why)


if __name__ == "__main__":
    unittest.main()
