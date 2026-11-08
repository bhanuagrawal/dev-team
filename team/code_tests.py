"""Tests are part of the change — enforced from the DIFF, not from a promise.

Why this exists (2026-11-05): a run shipped a signature field whose only
exercise was a screenshot preview. `verify` ran compile + `testDebugUnitTest`,
both green, so "tested" meant "nothing asserted" and "UI tested" meant a PNG
nobody checked. The developer prompt asked for tests in prose; the gate never
read the answer.

This module is the deterministic gate. It reads the task diff, never the
agent's self-report:

  * every changed production source file must be REFERENCED by a test file that
    is part of the same diff and contains a real assertion;
  * a changed UI file (a composable, Activity/Fragment/Dialog/View, or a layout
    XML) must be referenced by a UI test in that diff — a Compose rule (JVM
    Robolectric or on-device) or Espresso/UiAutomator — with an assertion.
    A screenshot render with no assertion is not a test and does not count.

Only the classification ("is this file UI?") is heuristic. The test file being
in the diff, naming the changed symbol, and asserting are exact. Team code names
no project, module, screen or app.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# source sets — Android + Kotlin Multiplatform conventions
# --------------------------------------------------------------------------- #
UNIT_TEST_DIRS = (
    "src/test", "src/androidUnitTest", "src/commonTest", "src/desktopTest",
    "src/jsTest", "src/jvmTest", "src/iosTest", "src/nativeTest",
    "src/wasmJsTest", "src/wasmTest",
)
UI_TEST_DIRS = ("src/androidTest", "src/androidInstrumentedTest")
TEST_DIRS = UNIT_TEST_DIRS + UI_TEST_DIRS

SOURCE_EXTS = (".kt", ".java")
SKIP_DIR_PARTS = {"build", "generated", "__pycache__", ".pipeline", ".gradle",
                  ".git", ".idea", "node_modules"}

# A production file that DECLARES UI: Compose, an Android component, or a
# classic XML layout. Path alone is not enough — ViewModels and repositories
# live under ui/ too and need unit tests, not UI tests.
_UI_CODE_RE = re.compile(
    r"@Composable\b"
    r"|:\s*(?:AppCompat|Component)?Activity\b"
    r"|:\s*Fragment\b"
    r"|:\s*Dialog\b"
    r"|:\s*View\b"
    r"|\bsetContent\s*\{"
    r"|\bAndroidView\s*\("
    r"|\bextends\s+(?:AppCompat)?Activity\b"
    r"|\bextends\s+Fragment\b"
    r"|\bextends\s+Dialog\b"
    r"|\bextends\s+View\b"
)

# A test that can actually fail.
_TEST_FN_RE = re.compile(
    r"@(?:Test|ParameterizedTest|TestFactory|RepeatedTest|Theory)\b")
_ASSERT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:assert[A-Za-z]*|assertEquals|assertThat|verify|"
    r"expect|shouldBe|shouldNotBe)\s*\(")

# A test that drives the UI, rather than just calling a function.
_UI_TEST_RE = re.compile(
    r"createAndroidComposeRule|createComposeRule|createEmptyComposeRule"
    r"|runComposeUiTest|ComposeTestRule"
    r"|\bonNodeWith[A-Za-z]*\s*\(|\bonAllNodes[A-Za-z]*\s*\("
    r"|\bonView\s*\(|\bonData\s*\(|\bEspresso\."
    r"|\bUiDevice\b|\bUiAutomator\b|\bActivityScenario\b"
)

# Top-level-ish declarations. Nested captures are harmless: a test naming any
# declaration in the file is evidence the file is exercised.
_DECL_RE = re.compile(
    r"\b(?:class|interface|object|fun|val|var|typealias)\s+"
    r"(?:<[^>]*>\s*)?(?:[A-Za-z_][\w.]*\.)?([A-Za-z_]\w*)"
)


@dataclass(frozen=True)
class TestFile:
    path: str
    text: str
    in_ui_set: bool
    is_asserting: bool
    is_ui: bool


@dataclass(frozen=True)
class SourceFile:
    path: str
    text: str
    is_ui: bool
    symbols: tuple[str, ...]


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
def _parts(rel: str) -> list[str]:
    return rel.split("/")


def _in_dirs(rel: str, dirs: tuple[str, ...]) -> bool:
    parts = _parts(rel)
    for i in range(len(parts) - 1):
        if "/".join(parts[i:i + 2]) in dirs:
            return True
    return False


def is_test_path(rel: str) -> bool:
    return _in_dirs(rel, TEST_DIRS)


def is_ui_source(rel: str, text: str) -> bool:
    if rel.endswith(".xml"):
        return "/res/layout" in rel
    return bool(_UI_CODE_RE.search(text))


def is_production_source(rel: str) -> bool:
    if is_test_path(rel):
        return False
    if any(p in SKIP_DIR_PARTS for p in _parts(rel)):
        return False
    if not "/src/" in f"/{rel}":
        return False
    # Code, plus the one resource kind that IS UI. Strings, colors and drawables
    # are covered by the localization/asset gates, not by a test file.
    if rel.endswith(".xml"):
        return "/res/layout" in rel
    return rel.endswith(SOURCE_EXTS)


def _symbols(rel: str, text: str) -> tuple[str, ...]:
    names = {Path(rel).stem}
    names |= {m.group(1) for m in _DECL_RE.finditer(text)}
    return tuple(sorted(n for n in names if len(n) >= 3))


def test_file(rel: str, text: str) -> TestFile:
    in_ui_set = _in_dirs(rel, UI_TEST_DIRS)
    asserting = bool(_TEST_FN_RE.search(text) and _ASSERT_RE.search(text))
    return TestFile(
        path=rel, text=text, in_ui_set=in_ui_set, is_asserting=asserting,
        is_ui=asserting and bool(_UI_TEST_RE.search(text)))


def source_file(rel: str, text: str) -> SourceFile:
    return SourceFile(path=rel, text=text, is_ui=is_ui_source(rel, text),
                      symbols=_symbols(rel, text))


def _read(worktree: Path, rel: str) -> str:
    try:
        return (worktree / rel).read_text(errors="ignore")
    except OSError:
        return ""


def _names(symbols: tuple[str, ...], text: str) -> bool:
    return any(re.search(rf"\b{re.escape(s)}\b", text) for s in symbols)


def _covered(src: SourceFile, t: TestFile) -> bool:
    """A test names the changed file — by test-file convention or in its body.

    `SignaturePadTest.kt` is evidence even when the body launches a parent screen
    and never writes `SignaturePad`; the naming convention is exact and common.
    """
    if Path(src.path).stem in Path(t.path).stem:
        return True
    return _names(src.symbols, t.text)


# --------------------------------------------------------------------------- #
# what the developer should create, derived from the project layout
# --------------------------------------------------------------------------- #
def _has_sources(directory: Path) -> bool:
    return any(p.is_file() and p.suffix in SOURCE_EXTS for p in directory.rglob("*"))


def suggest_test_dirs(root: Path) -> tuple[list[str], list[str]]:
    """(unit dirs, UI dirs) that EXIST, else the conventional ones.

    Derived from the module layout so the message names a real place to put the
    test — never a hard-coded module name. KMP modules get `commonTest` +
    `androidInstrumentedTest`; Android modules get `test` + `androidTest`.

    Two shapes the naive "src/main or src/commonMain" test got wrong, both seen
    in the wild and both of which point a developer at a directory that does not
    exist (2026-11-06: a prompt told a developer to put tests in
    `composeApp/src/commonTest` on a project whose only source module was `app`
    with `androidMain` + flavour source sets, and hid the `app/src/androidUnitTest`
    that really existed):
      * an Android/KMP module whose sources live in `androidMain`/`jvmMain`/
        `iosMain` (or flavour sets) and has no `src/main` at all — it is still a
        module, and any test dir it really has must be named;
      * an EMPTY `src/` tree left behind by a rename (build output, untracked and
        ignored, with no build file and no sources) — it is not a module, and its
        conventional dirs must not be recommended.
    """
    unit: list[str] = []
    ui: list[str] = []
    for src in sorted(root.glob("*/src")):
        mod = src.parent.name
        found_unit = [f"{mod}/{d}" for d in UNIT_TEST_DIRS
                      if (root / mod / d).is_dir()]
        found_ui = [f"{mod}/{d}" for d in UI_TEST_DIRS
                    if (root / mod / d).is_dir()]
        declares_itself = any((root / mod / f).is_file()
                              for f in ("build.gradle", "build.gradle.kts",
                                        "pom.xml", "Cargo.toml", "package.json"))
        source_sets = {p.name for p in src.iterdir() if p.is_dir()}
        real_module = (declares_itself or _has_sources(src)
                       or any(_has_sources(root / mod / d)
                              for d in found_unit + found_ui))
        if not real_module:
            continue
        if "commonMain" in source_sets:
            conventional_unit, conventional_ui = "commonTest", "androidInstrumentedTest"
        else:
            conventional_unit, conventional_ui = "test", "androidTest"
        unit += found_unit or [f"{mod}/src/{conventional_unit}"]
        ui += found_ui or [f"{mod}/src/{conventional_ui}"]
    return sorted(dict.fromkeys(unit)), sorted(dict.fromkeys(ui))


def _selftest() -> int:
    """The two layouts that broke suggest_test_dirs (run: python3 -m team.code_tests)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "app/src/androidMain/kotlin/p").mkdir(parents=True)
        (root / "app/src/androidMain/kotlin/p/A.kt").write_text("class A")
        (root / "app/src/androidUnitTest/kotlin/p").mkdir(parents=True)
        (root / "app/src/androidUnitTest/kotlin/p/ATest.kt").write_text("class ATest")
        # an empty leftover from a rename: no build file, no sources
        (root / "leftover/src/commonMain").mkdir(parents=True)
        (root / "leftover/src/androidTest").mkdir(parents=True)
        unit, ui = suggest_test_dirs(root)
        assert unit == ["app/src/androidUnitTest"], unit
        assert ui == ["app/src/androidTest"], ui
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
def gate(worktree: Path, files: list[str]) -> tuple[bool, str]:
    """(ok, message). Every changed production file carries a test in the diff."""
    sources: list[SourceFile] = []
    tests: list[TestFile] = []
    for rel in files:
        if not (worktree / rel).is_file():
            continue
        text = _read(worktree, rel)
        if is_test_path(rel) and rel.endswith(SOURCE_EXTS):
            tests.append(test_file(rel, text))
        elif is_production_source(rel):
            sources.append(source_file(rel, text))

    if not sources:
        return True, "no production source changed"
    if not tests:
        unit_dirs, ui_dirs = suggest_test_dirs(worktree)
        return False, (
            "TESTS ARE PART OF THE CHANGE — production code changed but no test "
            "file did.\n"
            f"  changed: {', '.join(s.path for s in sources[:6])}"
            f"{' …' if len(sources) > 6 else ''}\n"
            "  add a @Test with at least one assertion in: "
            f"{', '.join(unit_dirs) or 'the project test source set'}"
            + (f"; UI tests in: {', '.join(ui_dirs)}" if ui_dirs else "") + "\n"
            "  a screenshot/render with no assertion does not count.")

    missing: list[str] = []
    for src in sources:
        if src.is_ui:
            ok = any(t.is_ui and _covered(src, t) for t in tests)
            if not ok:
                unit_dirs, ui_dirs = suggest_test_dirs(worktree)
                where = " or ".join(
                    x for x in (", ".join(unit_dirs), ", ".join(ui_dirs)) if x
                ) or "a UI test source set"
                missing.append(
                    f"{src.path} (UI): no changed UI test names "
                    f"{', '.join(src.symbols[:3])} — add a Compose UI test "
                    f"(createComposeRule/createAndroidComposeRule + assertion) "
                    f"or Espresso/UiAutomator under {where}; a render with no "
                    f"assertion does not count")
        else:
            ok = any(t.is_asserting and _covered(src, t) for t in tests)
            if not ok:
                unit_dirs, _ = suggest_test_dirs(worktree)
                where = ", ".join(unit_dirs) or "a unit test source set"
                missing.append(
                    f"{src.path} (logic): no changed test names "
                    f"{', '.join(src.symbols[:3])} with @Test + an assertion — "
                    f"add one under {where}")

    if missing:
        return False, ("TESTS ARE PART OF THE CHANGE — "
                       f"{len(missing)} file(s) have no matching test in this diff:\n  - "
                       + "\n  - ".join(missing))
    ui = sum(1 for s in sources if s.is_ui)
    return True, (f"{len(sources)} source file(s) carry a changed test "
                  f"({ui} UI, {len(sources) - ui} logic)")


# --------------------------------------------------------------------------- #
# posture — doctor/capabilities read the same classification
# --------------------------------------------------------------------------- #
def posture(root: Path) -> dict:
    """Counts of production sources and asserting tests on the delivery tree.

    Not a gate; it makes a project's test debt visible BEFORE the next change
    fails verify. Reads origin/main (via the caller), because the checkout drifts
    and a stale count misleads exactly when a release just changed it.
    """
    sources = ui_sources = 0
    unit_tests = ui_tests = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        # Judge the RELATIVE path: the delivery tree lives INSIDE the project
        # (`.pipeline/scan`), so testing absolute parts against SKIP_DIR_PARTS
        # skipped every file and reported 0 sources — a silent zero is worse than
        # the stale count this tree switch was meant to fix.
        rel_parts = p.relative_to(root).parts[:-1]
        if any(x in SKIP_DIR_PARTS for x in rel_parts):
            continue
        rel = p.relative_to(root).as_posix()
        if is_production_source(rel):
            text = p.read_text(errors="ignore")
            sources += 1
            if is_ui_source(rel, text):
                ui_sources += 1
        elif is_test_path(rel) and rel.endswith(SOURCE_EXTS):
            text = p.read_text(errors="ignore")
            tf = test_file(rel, text)
            if tf.is_asserting:
                unit_tests += 1
                if tf.is_ui:
                    ui_tests += 1
    return {"sources": sources, "ui_sources": ui_sources,
            "unit_tests": unit_tests, "ui_tests": ui_tests}


def posture_checks(p: dict) -> list[tuple[str, bool, str]]:
    """(requirement, ok, fix) rows for doctor. Empty when there is no code."""
    if not p["sources"]:
        return []
    rows = [(
        f"asserting unit tests exist ({p['unit_tests']} test file(s) for "
        f"{p['sources']} source file(s))",
        p["unit_tests"] > 0,
        "every production change must ship a test in the same diff — "
        "`devteam verify` refuses otherwise")]
    if p["ui_sources"]:
        rows.append((
            f"asserting UI tests exist ({p['ui_tests']} test file(s) for "
            f"{p['ui_sources']} UI file(s))",
            p["ui_tests"] > 0,
            "UI changes need a Compose/Espresso test with assertions — "
            "a screenshot render is not a test"))
    return rows
