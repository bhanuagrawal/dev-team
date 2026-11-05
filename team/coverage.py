"""Test-coverage collector — deterministic, no Gradle run, no LLM.

The planner needs two numbers it can spend a release on, measured the same way for
every project:

  unit — of the main-source files that carry behaviour, what share is reached by the
         JVM test set. File-level on purpose (a source file counts when a test names
         its type): a project without a JaCoCo task still gets a number, and the
         number moves the moment a test file is added.
  ui   — of the UI source files, what share has a test that mounts it. A Robolectric
         Compose test is a real UI test even though it runs on the JVM, so this
         counts `src/test` AND `src/androidTest` (and their KMP equivalents).

When a project ALREADY produces a JaCoCo report, its real LINE coverage replaces
the unit proxy — a project with instrumented line coverage must not be judged by
an approximation.

The split is by denominator: every main file lands in exactly one of the two, so
`unit` and `ui` are the two halves of "how much of this codebase is tested", not
two guesses at the same whole.

Nothing here knows a project's name or layout beyond the standard Gradle/KMP
source-set names; a project that needs a different report path or floor declares
it in its own profile (`coverage` block), never in this module.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# Default floors. Owner-tunable in config/coverage.json; a project may override its
# own in profile["coverage"] (a project fact, so it lives there, not here).
DEFAULT_UNIT_FLOOR = 60
DEFAULT_UI_FLOOR = 50

# Standard Gradle + Kotlin Multiplatform source-set names. Only the ones that hold
# shipped code or tests; generated sets (build/) are never scanned.
MAIN_SETS = ("main", "commonMain", "androidMain", "jvmMain", "desktopMain", "iosMain")
UNIT_TEST_SETS = ("test", "commonTest", "jvmTest", "desktopTest", "androidUnitTest")
UI_TEST_SETS = ("androidTest", "androidInstrumentedTest", "iosTest", "iosSimulatorArm64Test")
SRC_LANGS = ("java", "kotlin")
SOURCE_SUFFIXES = (".kt", ".java")

# Files whose name is not a type a test would name, or is generated.
SKIP_STEMS = {"R", "BuildConfig"}

# A file is "UI" when it lives under a ui/ package or its type is one of the
# screen-shaped roles the team's design law names.
UI_STEM_SUFFIXES = ("Screen", "Sheet", "Card", "Dialog")
UI_PATH_SEGMENT = "ui"

_JACOCO_GLOB = "**/build/reports/jacoco/**/*.xml"


def _config() -> dict:
    f = Path(__file__).resolve().parent.parent / "config" / "coverage.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return {}


def floors(profile: dict | None = None) -> tuple[int, int]:
    """(unit_floor, ui_floor) — profile override, then team config, then defaults."""
    cfg = _config()
    prof = ((profile or {}).get("coverage") or {})
    unit = int(prof.get("unit_floor", cfg.get("unit_floor", DEFAULT_UNIT_FLOOR)))
    ui = int(prof.get("ui_floor", cfg.get("ui_floor", DEFAULT_UI_FLOOR)))
    return unit, ui


def _source_files(tree: Path, sets: tuple[str, ...]) -> list[Path]:
    """Every .kt/.java file under any of the named source sets, sorted."""
    out: list[Path] = []
    tree = Path(tree)
    for p in tree.rglob("*"):
        if p.suffix not in SOURCE_SUFFIXES or not p.is_file():
            continue
        # Judge the path RELATIVE to the tree: an absolute path may contain the
        # scan worktree's own `.pipeline` segment, which would exclude every file.
        try:
            parts = p.relative_to(tree).parts
        except ValueError:
            continue
        if "build" in parts or ".pipeline" in parts or ".gradle" in parts:
            continue
        # .../<module>/src/<sourceSet>/<lang>/...
        try:
            src_i = parts.index("src")
        except ValueError:
            continue
        if src_i + 2 >= len(parts):
            continue
        if parts[src_i + 1] not in sets or parts[src_i + 2] not in SRC_LANGS:
            continue
        if p.stem in SKIP_STEMS or p.stem.startswith("Generated"):
            continue
        out.append(p)
    return sorted(out)


def _references(tree: Path, sets: tuple[str, ...]) -> str:
    """One blob of every test source in the named sets, for name matching."""
    chunks: list[str] = []
    for p in _source_files(tree, sets):
        try:
            chunks.append(p.read_text(errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks)


def _is_ui(rel: Path) -> bool:
    if UI_PATH_SEGMENT in rel.parts:
        return True
    stem = rel.stem
    if stem.endswith(UI_STEM_SUFFIXES):
        return True
    return False


def _jacoco_line_percent(tree: Path) -> int | None:
    """Real LINE coverage from a JaCoCo XML report already on disk, if any."""
    reports = sorted(tree.glob(_JACOCO_GLOB))
    if not reports:
        return None
    # The aggregate report is the largest file; parse its last LINE counter.
    report = max(reports, key=lambda p: p.stat().st_size)
    try:
        text = report.read_text(errors="ignore")
    except OSError:
        return None
    hits = re.findall(r'<counter type="LINE" missed="(\d+)" covered="(\d+)"/>', text)
    if not hits:
        return None
    missed, covered = (int(x) for x in hits[-1])
    total = missed + covered
    return round(covered * 100 / total) if total else None


def _leg(signal: dict) -> dict:
    explicit = signal.get("_percent")
    total = signal.get("_total") or 0
    percent = explicit if explicit is not None else (
        round(signal.get("_covered", 0) * 100 / total) if total else None)
    return {
        "percent": percent,
        "covered": None if explicit is not None else signal.get("_covered", 0),
        "total": None if explicit is not None else total,
        "measured_by": signal.get("measured_by", "structural"),
        "missing_examples": signal.get("missing_examples", []),
    }


def collect(tree: Path, profile: dict | None = None) -> dict:
    """Measure unit and ui coverage over the SHIPPED tree (origin/main)."""
    tree = Path(tree)
    unit_floor, ui_floor = floors(profile)

    main_files = _source_files(tree, MAIN_SETS)
    if not main_files:
        return {
            "status": "no-source",
            "floor": {"unit": unit_floor, "ui": ui_floor},
            "unit": {"percent": None, "covered": 0, "total": 0,
                     "measured_by": "structural", "missing_examples": []},
            "ui": {"percent": None, "covered": 0, "total": 0,
                   "measured_by": "structural", "missing_examples": []},
            "below_floor": [],
            "note": "no main sources found under the standard source sets",
        }

    unit_tests = _references(tree, UNIT_TEST_SETS)
    all_tests = unit_tests + "\n" + _references(tree, UI_TEST_SETS)
    if not all_tests.strip():
        return {
            "status": "no-tests",
            "floor": {"unit": unit_floor, "ui": ui_floor},
            "unit": {"percent": 0, "covered": 0, "total": len(main_files),
                     "measured_by": "structural", "missing_examples": []},
            "ui": {"percent": None, "covered": 0, "total": 0,
                   "measured_by": "structural", "missing_examples": []},
            "below_floor": ["unit"],
            "note": "main sources exist but no test sources do — coverage is 0 by definition",
        }

    unit_cov = unit_tot = 0
    ui_cov = ui_tot = 0
    unit_missing: list[str] = []
    ui_missing: list[str] = []
    for f in main_files:
        rel = f.relative_to(tree)
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(f.stem) + r"(?![A-Za-z0-9_])")
        if _is_ui(rel):
            ui_tot += 1
            if pattern.search(all_tests):
                ui_cov += 1
            elif len(ui_missing) < 6:
                ui_missing.append(str(rel))
        else:
            unit_tot += 1
            if pattern.search(unit_tests):
                unit_cov += 1
            elif len(unit_missing) < 6:
                unit_missing.append(str(rel))

    unit = {"_covered": unit_cov, "_total": unit_tot, "measured_by": "structural",
            "missing_examples": unit_missing}
    ui = {"_covered": ui_cov, "_total": ui_tot, "measured_by": "structural",
          "missing_examples": ui_missing}

    # A real JaCoCo line report replaces the unit proxy when the project ships one.
    jacoco = _jacoco_line_percent(tree)
    if jacoco is not None:
        unit = {"_percent": jacoco, "_total": 0, "measured_by": "jacoco",
                "missing_examples": []}

    unit_leg, ui_leg = _leg(unit), _leg(ui)
    below = []
    if unit_leg["percent"] is not None and unit_leg["percent"] < unit_floor:
        below.append("unit")
    if ui_leg["percent"] is not None and ui_leg["percent"] < ui_floor:
        below.append("ui")

    return {
        "status": "ok",
        "floor": {"unit": unit_floor, "ui": ui_floor},
        "unit": unit_leg,
        "ui": ui_leg,
        "below_floor": below,
        "note": ("unit = share of non-UI main-source files a JVM test names "
                 "(file-level; JaCoCo line coverage replaces it when a report exists); "
                 "ui = share of UI source files a test mounts, counting src/test "
                 "Robolectric tests and src/androidTest alike"),
    }


def stored(project: Path) -> dict | None:
    """The coverage signal from the last collected research.json, if any.

    Cheap read for `status`/reports: the numbers were measured against the shipped
    tree at collect time, and re-measuring on every status call would re-walk the
    codebase for a line of text.
    """
    f = Path(project) / ".pipeline" / "research.json"
    try:
        sig = (json.loads(f.read_text()).get("signals") or {}).get("coverage")
    except (OSError, ValueError):
        return None
    return sig if isinstance(sig, dict) and sig.get("status") else None


def summary_line(sig: dict) -> str:
    """One log/report line: `coverage: unit 37% (floor 60), ui 55% (floor 50) — below: unit`."""

    def pct(leg: dict) -> str:
        v = leg.get("percent")
        return "n/a" if v is None else f"{v}%"

    floor = sig.get("floor") or {}
    below = sig.get("below_floor") or []
    line = (f"coverage: unit {pct(sig.get('unit') or {})} "
            f"(floor {floor.get('unit')}), ui {pct(sig.get('ui') or {})} "
            f"(floor {floor.get('ui')})")
    if below:
        line += " — below: " + ", ".join(below)
    return line


# A queue entry raises coverage when its edit allow-list names test sources.
_TEST_TARGET_RE = re.compile(
    r"(^|/)src/(test|androidTest|commonTest|jvmTest|desktopTest|androidUnitTest|"
    r"androidInstrumentedTest|iosTest|iosSimulatorArm64Test)(/|$)")


def targets_tests(target: str | None) -> bool:
    """True when an entry's allow-list writes tests (the coverage-raising shape)."""
    for item in re.split(r"[,\n]", target or ""):
        if _TEST_TARGET_RE.search(item.strip()):
            return True
    return False
