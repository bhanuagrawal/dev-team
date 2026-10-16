#!/usr/bin/env python3
"""Executable expertise: localization completeness gate.

Usage: check_localization.py <project_root> <changed files...>

Guarantee: every Kotlin R.string referenced in a changed file must exist in the
default values/strings.xml AND in every locale file (ar de es fr hi in it ja ko
nl pt-rBR ru tr vi zh-rCN). Naming convention: snake_case with a feature prefix.
"""
import re
import sys
from pathlib import Path

LOCALES = ["ar", "de", "es", "fr", "hi", "in", "it", "ja", "ko", "nl",
           "pt-rBR", "ru", "tr", "vi", "zh-rCN"]


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
    res = root / "app" / "src" / "main" / "res"
    default = defined_in(res / "values" / "strings.xml")
    per_locale = {loc: defined_in(res / f"values-{loc}" / "strings.xml") for loc in LOCALES}
    missing_files = [loc for loc in LOCALES if not (res / f"values-{loc}" / "strings.xml").exists()]
    problems: list[str] = []

    for arg in sys.argv[2:]:
        p = root / arg
        if not p.exists() or not p.match("*.kt"):
            continue
        for name in refs(p):
            if name not in default:
                problems.append(f"{arg}: R.string.{name} not defined in values/strings.xml")
            for loc in LOCALES:
                if name in default and name not in per_locale[loc]:
                    problems.append(f"{arg}: R.string.{name} MISSING in values-{loc}/strings.xml")

    for loc in missing_files:
        problems.append(f"MISSING LOCALE FILE: values-{loc}/strings.xml")

    if problems:
        print("LOCALIZATION GATE FAILURES:")
        for p in problems:
            print("  - " + p)
        return 1
    print("localization gate: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
