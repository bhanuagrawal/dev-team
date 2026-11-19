"""A browser client is application source, and its components are UI.

BookSwap ships two surfaces: the Kotlin app and `web/` (its own `package.json`,
`tsc --noEmit && vite build`). The classifier the design gate reads only knew
`.kt`/`.java`, so a diff that rewrote the entire web client was reported as
"no UI file changed — no design needed" (2026-11-19) — the design gate silently
never applied to half the product.
"""

import sys
import unittest
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TEAM_ROOT))

from team import code_tests  # noqa: E402


class WebClassificationTests(unittest.TestCase):
    def test_a_component_is_ui(self) -> None:
        for rel in (
            "web/src/App.tsx",
            "web/src/ui/HomePage.tsx",
            "web/src/ui/Cover.jsx",
        ):
            with self.subTest(rel=rel):
                self.assertTrue(code_tests.is_ui_source(rel, "export default () => null"))
                self.assertTrue(code_tests.is_production_source(rel))

    def test_a_module_is_code_but_not_a_screen(self) -> None:
        for rel in ("web/src/core/home.ts", "web/src/api/client.ts", "web/src/app/stores.ts"):
            with self.subTest(rel=rel):
                self.assertTrue(code_tests.is_production_source(rel))
                self.assertFalse(code_tests.is_ui_source(rel, "export const x = 1"))

    def test_web_tests_are_tests_not_application_code(self) -> None:
        for rel in ("web/tests/home.test.ts", "web/src/core/home.test.ts",
                    "web/src/ui/Home.spec.tsx"):
            with self.subTest(rel=rel):
                self.assertTrue(code_tests.is_test_path(rel))
                self.assertFalse(code_tests.is_production_source(rel))
                self.assertFalse(code_tests.is_ui_source(rel, "test('x', () => {})"))

    def test_paths_outside_the_client_are_untouched(self) -> None:
        self.assertFalse(code_tests.is_production_source("docs/web-client.md"))
        self.assertFalse(code_tests.is_production_source("web/vite.config.ts"))
        self.assertFalse(code_tests.is_ui_source("web/vite.config.ts", "export default {}"))

    def test_kotlin_ui_is_still_ui(self) -> None:
        rel = "composeApp/src/commonMain/kotlin/p/Shelf.kt"
        text = "import androidx.compose.runtime.Composable\n@Composable fun Shelf() {}"
        self.assertTrue(code_tests.is_ui_source(rel, text))
        self.assertTrue(code_tests.is_production_source(rel))


if __name__ == "__main__":
    unittest.main()
