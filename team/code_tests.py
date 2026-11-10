"""UI tests are part of the change — enforced from the DIFF, not from a promise.

Why this exists (2026-11-05): a run shipped a signature field whose only
exercise was a screenshot preview. `verify` ran compile + `testDebugUnitTest`,
both green, so "tested" meant "nothing asserted" and "UI tested" meant a PNG
nobody checked. The developer prompt asked for tests in prose; the gate never
read the answer.

OWNER POLICY (2026-11-10): unit tests are RETIRED from the team. The only test
the pipeline requires is a UI/integration test — the thing a person actually
sees and taps. A logic-only change carries no test obligation: it is exercised
through the screens that use it. This module was the unit-test gate; the unit
half is gone, and only the UI half remains.

This module is the deterministic gate. It reads the task diff, never the
agent's self-report:

  * a changed UI file (a composable, Activity/Fragment/Dialog/View, or a layout
    XML) must be referenced by a UI test in that diff — a Compose rule (JVM
    Robolectric or on-device) or Espresso/UiAutomator — with an assertion.
    A screenshot render with no assertion is not a test and does not count;
  * a changed non-UI file needs nothing in particular. Any test in the diff that
    names it satisfies nothing, because nothing is asked of it.

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

_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.M)
_CLASS_DECL_RE = re.compile(
    r"^[ \t]*(?:(?:public|internal|private|protected|open|abstract|final|"
    r"sealed|data|enum|annotation|inner|value)\s+)*class\s+([A-Za-z_]\w*)", re.M)


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


def ui_test_classes(root: Path) -> dict[str, list[str]]:
    """module dir -> fully-qualified names of the UI test classes it declares.

    `verify` uses this to run the UI half of a JVM test task without dragging the
    project's unit-only classes along: the owner retired unit tests (2026-11-10)
    and the team must not spend a gate on them. A class qualifies when it lives
    in a file this module classifies as an asserting UI test and its name follows
    the JUnit convention (`*Test`) or matches the file stem — a helper declared
    in the same file is not a test and is not selected.
    """
    out: dict[str, list[str]] = {}
    root = Path(root)
    for p in sorted(root.glob("*/src/**/*.kt")):
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        if not is_test_path(rel):
            continue
        text = _read(root, rel)
        if not test_file(rel, text).is_ui:
            continue
        pkg = _PACKAGE_RE.search(text)
        module = rel.split("/")[0]
        for m in _CLASS_DECL_RE.finditer(text):
            name = m.group(1)
            if "Test" not in name and name != p.stem:
                continue
            fq = f"{pkg.group(1)}.{name}" if pkg else name
            out.setdefault(module, []).append(fq)
    return {k: sorted(dict.fromkeys(v)) for k, v in out.items()}


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
    """(ok, message). Every changed UI file carries a UI test in the diff.

    Unit tests are not required (owner policy 2026-11-10): a logic-only diff
    passes with no test file at all. The classification still counts the test
    files in the diff, because a UI test may live in any test source set.
    """
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
    ui_sources = [s for s in sources if s.is_ui]
    if not ui_sources:
        return True, (f"{len(sources)} source file(s) changed, none UI — "
                      "no test required (unit tests are retired)")

    missing: list[str] = []
    for src in ui_sources:
        ok = any(t.is_ui and _covered(src, t) for t in tests)
        if not ok:
            _, ui_dirs = suggest_test_dirs(worktree)
            where = ", ".join(ui_dirs) or "a UI test source set"
            missing.append(
                f"{src.path} (UI): no changed UI test names "
                f"{', '.join(src.symbols[:3])} — add a Compose UI test "
                f"(createComposeRule/createAndroidComposeRule + assertion) "
                f"or Espresso/UiAutomator under {where}; a render with no "
                f"assertion does not count")

    if missing:
        return False, ("UI TESTS ARE PART OF THE CHANGE — "
                       f"{len(missing)} UI file(s) have no matching UI test in "
                       "this diff:\n  - " + "\n  - ".join(missing))
    return True, (f"{len(sources)} source file(s) changed — {len(ui_sources)} UI "
                  f"file(s) carry a changed UI test (unit tests are not required)")


# --------------------------------------------------------------------------- #
# posture — doctor/capabilities read the same classification
# --------------------------------------------------------------------------- #
def posture(root: Path) -> dict:
    """Counts of production sources and asserting UI tests on the delivery tree.

    Not a gate; it makes a project's UI-test debt visible BEFORE the next change
    fails verify. Reads origin/main (via the caller), because the checkout drifts
    and a stale count misleads exactly when a release just changed it.
    """
    sources = ui_sources = 0
    ui_tests = 0
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
            if test_file(rel, text).is_ui:
                ui_tests += 1
    return {"sources": sources, "ui_sources": ui_sources, "ui_tests": ui_tests}


def posture_checks(p: dict) -> list[tuple[str, bool, str]]:
    """(requirement, ok, fix) rows for doctor. Empty when there is no code."""
    if not p["sources"]:
        return []
    if not p["ui_sources"]:
        return []
    return [(
        f"asserting UI tests exist ({p['ui_tests']} test file(s) for "
        f"{p['ui_sources']} UI file(s))",
        p["ui_tests"] > 0,
        "UI changes need a Compose/Espresso test with assertions — "
        "a screenshot render is not a test")]
