"""EVERY BUILDABLE UNIT HAS A GATE — the repo's platforms, derived not listed.

Why (2026-11-18, owner directive)
---------------------------------
BookSwap grew a second client and nobody in the pipeline noticed. `web/` is a
React app with its own `package.json`, its own `tsc --noEmit && vite build` and
its own hand-copy of the product's location rule. It merged while `verify` ran
Gradle, CI ran Gradle, and `doctor` asked nothing — so a change to a shared
concept could PASS, ship, and leave the browser on the old rule, silently,
because no gate knew a second client existed.

The remedy is an inventory, not a checklist: a directory that carries a build
file IS a platform, and a platform nothing builds is a refusal. Detection comes
from the tree (`git ls-files`, so ignored directories and dependencies are not
platforms), and the remedy is one profile line — `verify.tasks`.

Two severities, the same split the plan uses for a stale queue:

  * `verify` REFUSES a diff that touches an ungated unit. The failure is the
    change, which is the only moment the gap can hurt.
  * `doctor` WARNS that a unit has no gate, so the gap is visible before a run
    reaches it. It does not block the machine over a directory this diff never
    opened.

A project that declares one Gradle task is unchanged: `verify.compile` is still
one gate, covering the root Gradle unit. A second platform is declared, not
assumed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import git, run

#: Directories that never hold a buildable unit of the repository itself:
#: dependencies, build output, machine state, and the pipeline's own worktrees.
SKIP_DIRS = {
    ".git", ".github", ".pipeline", ".gradle", ".kotlin", ".idea", ".vscode",
    "build", "node_modules", "dist", "out", "vendor", "Pods", "target",
    "venv", ".venv", "__pycache__", "coverage", ".next", ".turbo",
}

#: Kind → the file that makes a directory a unit. The kind is the vocabulary of
#: the remedy: each ecosystem is built and tested by a different command.
MARKERS: dict[str, tuple[str, ...]] = {
    "gradle": ("settings.gradle", "settings.gradle.kts"),
    "node": ("package.json",),
    "swift": ("Package.swift",),
    "rust": ("Cargo.toml",),
    "go": ("go.mod",),
    "flutter": ("pubspec.yaml",),
    "python": ("pyproject.toml",),
}

#: Where a kind's own manifest lives, for a message that names the file found.
MANIFEST_OF = {
    "gradle": "settings.gradle(.kts)",
    "node": "package.json",
    "swift": "Package.swift or *.xcodeproj",
    "rust": "Cargo.toml",
    "go": "go.mod",
    "flutter": "pubspec.yaml",
    "python": "pyproject.toml",
}

_MSG_REMEDY = (
    'declare it in profile.json: "verify": {{"tasks": [{{"label": "{label}", '
    '"cmd": "{cmd}", "cwd": "{cwd}"}}]}}'
)


def _tracked(project: Path) -> list[str]:
    """Every tracked path, or a walked tree when this is not a git checkout.

    Tracked-and-only-tracked is the point: `node_modules`, `build/` and a stale
    `.pipeline/worktrees/<id>` copy are all on disk and none of them is a
    platform.
    """
    res = git(["ls-files"], Path(project), timeout=120)
    if res.returncode == 0:
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    out: list[str] = []
    for path in Path(project).rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(project)
        if SKIP_DIRS & set(rel.parts):
            continue
        out.append(str(rel))
    return out


def _suggest_cmd(kind: str) -> str:
    return {
        "node": "npm run build",
        "swift": "xcodebuild -quiet build",
        "rust": "cargo build",
        "go": "go build ./...",
        "flutter": "flutter build apk --debug",
        "python": "python3 -m compileall -q .",
        "gradle": "./gradlew assembleDebug",
    }.get(kind, "true")


def _label(kind: str, directory: str) -> str:
    """A unit's name: its directory when it has one, else its ecosystem."""
    return directory or kind


def units(project: Path) -> list[dict[str, Any]]:
    """Every buildable unit in the tree: `[{label, dir, kind, manifest}]`.

    `dir` is the unit's root relative to the repository ("" for the root), which
    is also the `cwd` a gate must name to cover it.
    """
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for rel in _tracked(project):
        parts = rel.split("/")
        if set(parts[:-1]) & SKIP_DIRS:
            continue
        name = parts[-1]
        # An Xcode project keeps its manifest a level down: `iosApp/iosApp.xcodeproj/...`.
        bundle = next((i for i, part in enumerate(parts)
                       if part.endswith((".xcodeproj", ".xcworkspace"))), None)
        if bundle is not None:
            directory = "/".join(parts[:bundle])
            found.setdefault(("swift", directory), {
                "label": _label("swift", directory), "dir": directory,
                "kind": "swift", "manifest": "/".join(parts[:bundle + 1]),
            })
            continue
        kind = next((k for k, names in MARKERS.items() if name in names), None)
        if kind is None:
            continue
        directory = "/".join(parts[:-1])
        found.setdefault((kind, directory), {
            "label": _label(kind, directory), "dir": directory,
            "kind": kind, "manifest": rel,
        })
    return sorted(found.values(), key=lambda u: (u["dir"], u["kind"]))


def _dir(value: str) -> str:
    """A gate's directory, normalised: '' / '.' / 'web/' all usable."""
    cleaned = value.strip().strip("/")
    return "" if cleaned in ("", ".") else cleaned


def gates(profile: dict) -> list[dict[str, Any]]:
    """The declared gates: `verify.tasks` when present, else the one Gradle task.

    Backwards compatible by contract: a project that never declares a list keeps
    exactly the gate it had (`verify.compile`), and that gate covers the root
    Gradle unit and nothing else.
    """
    cfg = dict(profile.get("verify") or {})
    declared = cfg.get("tasks")
    out: list[dict[str, Any]] = []
    if isinstance(declared, list) and declared:
        for index, item in enumerate(declared):
            if isinstance(item, str):
                out.append({"label": f"task-{index + 1}", "cwd": "", "cmd": item.strip(),
                            "timeout": 900, "gradle": False})
                continue
            if not isinstance(item, dict):
                continue
            cmd = str(item.get("cmd") or "").strip()
            if not cmd:
                continue
            out.append({
                "label": str(item.get("label") or f"task-{index + 1}").strip(),
                "cwd": _dir(str(item.get("cwd") or "")),
                "cmd": cmd,
                "timeout": int(item.get("timeout") or 900),
                "gradle": bool(item.get("gradle")),
            })
        return out
    compile_task = str(cfg.get("compile") or "").strip()
    if compile_task:
        out.append({"label": "gradle", "cwd": "", "cmd": compile_task,
                    "timeout": 900, "gradle": True})
    return out


def _covers(gate: dict[str, Any], unit: dict[str, Any]) -> bool:
    """A gate covers the unit it runs in — one directory, no inheritance.

    Not "an ancestor covers a child": the root Gradle gate would then cover
    `web/`, which is precisely the bug this module exists to report.
    """
    return gate["cwd"] == unit["dir"]


def unit_for(inventory: list[dict[str, Any]], path: str) -> dict[str, Any] | None:
    """The DEEPEST unit a changed file belongs to (None for repo-level files)."""
    best: dict[str, Any] | None = None
    for unit in inventory:
        directory = unit["dir"]
        if directory and not (path == directory or path.startswith(directory + "/")):
            continue
        if best is None or len(directory) > len(best["dir"]):
            best = unit
    return best


def ungated(inventory: list[dict[str, Any]], profile: dict) -> list[dict[str, Any]]:
    declared = gates(profile)
    return [u for u in inventory if not any(_covers(g, u) for g in declared)]


def _remedy(unit: dict[str, Any]) -> str:
    return _MSG_REMEDY.format(
        label=unit["label"], cmd=_suggest_cmd(unit["kind"]), cwd=unit["dir"],
    )


def report(project: Path, profile: dict) -> dict[str, Any]:
    inventory = units(project)
    missing = ungated(inventory, profile)
    return {"units": inventory, "gates": gates(profile), "ungated": missing}


def diff_gate(worktree: Path, profile: dict, files: list[str]) -> tuple[bool, str]:
    """Refuse a diff that opens a platform nothing builds.

    The failure names the unit and the exact profile line, because the remedy is
    a declaration, not a code change. Detection runs on the WORKTREE: a task
    that introduces a unit is the task that must gate it.
    """
    inventory = units(worktree)
    declared = gates(profile)
    touched: dict[str, dict[str, Any]] = {}
    for path in files:
        unit = unit_for(inventory, path)
        if unit is None:
            continue
        if any(_covers(g, unit) for g in declared):
            continue
        touched[unit["label"]] = unit
    if not touched:
        return True, ""
    lines = [
        "A PLATFORM CHANGED WITH NO GATE: " + ", ".join(sorted(touched)) +
        " — this diff edits a buildable unit that `verify` never builds, so the "
        "change cannot be proven on that platform."
    ]
    for label in sorted(touched):
        unit = touched[label]
        lines.append(f"  {unit['manifest']} is a {unit['kind']} unit; "
                     f"{_remedy(unit)}")
    return False, "\n".join(lines)


def run_gates(worktree: Path, profile: dict, log) -> tuple[bool, str]:
    """Run every declared gate. The first failure stops the chain, with output."""
    declared = gates(profile)
    if not declared:
        return True, "no verify gate declared"
    for gate in declared:
        cwd = Path(worktree) / gate["cwd"] if gate["cwd"] else Path(worktree)
        if not cwd.is_dir():
            return False, (f"verify gate '{gate['label']}' names cwd '{gate['cwd']}', "
                           "which does not exist in this worktree")
        if gate["gradle"]:
            # The Gradle runner owns the JDK env, the wrapper download and the
            # renamed-task fallback; a second implementation would drift.
            from .verify import gradle_task
            ok, message = gradle_task(Path(worktree), gate["cmd"], gate["timeout"], log)
        else:
            log(f"~ {gate['label']}: {gate['cmd']} (cwd {gate['cwd'] or '.'})")
            result = run(["/bin/sh", "-c", gate["cmd"]], cwd, timeout=gate["timeout"])
            ok = result.returncode == 0
            message = "" if ok else (
                f"{gate['label']} FAILED: {gate['cmd']}\n"
                + (result.stdout + result.stderr)[-4000:]
            )
            log(f"  ok: {gate['cmd']}" if ok else f"  FAIL: {gate['cmd']}")
        if not ok:
            return False, message
    return True, ""
