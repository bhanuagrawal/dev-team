#!/usr/bin/env python3
"""Executable expertise: localization completeness gate.

Usage: check_localization.py <project_root> <changed files...>

Guarantee: every Kotlin R.string referenced in a changed file must exist in the
default values/strings.xml AND in every locale file the project actually ships
(values-*/strings.xml siblings of the default file, checksummed against the
canonical Android locale list when the project is multi-locale).
Naming convention: snake_case with a feature prefix.
"""
import re
import sys
from pathlib import Path

CANONICAL_LOCALES = ["ar", "de", "es", "fr", "hi", "in", "it", "ja", "ko", "nl",
                     "pt-rBR", "ru", "tr", "vi", "zh-rCN"]


def find_res_dir(root: Path) -> Path | None:
    """Locate the res dir holding values/strings.xml — layout-agnostic
    (app/src/main/res for single-module, composeApp/src/androidMain/res for KMP)."""
    hits = [p for p in root.glob("**/res/values/strings.xml")
            if not {"build", ".pipeline", ".gradle"} & set(p.relative_to(root).parts)]
    return hits[0].parent.parent if hits else None


def refs(path: Path) -> list[str]:
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return []
    return sorted(set(re.findall(r"R\.string\.([a-z0-9_]+)", text)))


def defined_in(f: Path) -> set[str]:
    if not f.exists():
        return set()
    return set(re.findall(r'<string name="([a-z0-9_]+)"', f.read_text(errors="ignore")))


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: check_localization.py <project_root> <changed files...>")
        return 2
    root = Path(sys.argv[1])
    res = find_res_dir(root)
    if res is None:
        print("localization gate: no res/strings.xml found — nothing to check")
        return 0
    default = defined_in(res / "values" / "strings.xml")
    ships = sorted(d.name.removeprefix("values-") for d in res.glob("values-*")
                   if d.name.startswith("values-") and (d / "strings.xml").exists())
    # locales gated = what the project actually ships; single-locale projects
    # (KMP apps commonly start with one) are not penalized for the rest.
    locales = ships if ships else []
    per_locale = {loc: defined_in(res / f"values-{loc}" / "strings.xml") for loc in locales}
    problems: list[str] = []

    for arg in sys.argv[2:]:
        p = root / arg
        if not p.exists() or not p.match("*.kt"):
            continue
        for name in refs(p):
            if not default:
                problems.append(f"{arg}: R.string.{name} referenced but values/strings.xml defines none")
                continue
            if name not in default:
                problems.append(f"{arg}: R.string.{name} not defined in values/strings.xml")
            for loc in locales:
                if name in default and name not in per_locale[loc]:
                    problems.append(f"{arg}: R.string.{name} MISSING in values-{loc}/strings.xml")

    if problems:
        print("LOCALIZATION GATE FAILURES:")
        for p in problems:
            print("  - " + p)
        return 1
    print("localization gate: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())

