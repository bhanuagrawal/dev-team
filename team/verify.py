"""Deterministic gates. The pipeline's authority — an agent's self-report is
never accepted; acceptance = diff in scope + these gates green, run by this
code, not by the agent.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .core import profile_of, run

JAVA_HOME = "/Applications/Android Studio.app/Contents/jbr/Contents/Home"
GRADLE_ENV = {"JAVA_HOME": JAVA_HOME, "TERM": "dumb", "CI": "true"}


def changed_files(worktree: Path, base: str) -> list[str]:
    r = subprocess.run(
        ["git", "diff", "--name-only", base, "HEAD"] + ["--", "."],
        cwd=worktree, capture_output=True, text=True)
    r2 = subprocess.run(
        ["git", "diff", "--name-only"], cwd=worktree, capture_output=True, text=True)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=worktree, capture_output=True, text=True)
    out = set(x for x in (r.stdout + r2.stdout + untracked.stdout).splitlines() if x.strip())
    return sorted(out)


def scope_check(profile: dict, files: list[str]) -> tuple[bool, str]:
    """(ok, message). Hard rule: nothing under frozen_paths may appear."""
    import fnmatch
    frozen = []
    for f in files:
        for pat in profile["frozen_paths"]:
            if fnmatch.fnmatch(f, pat):
                frozen.append(f"{f}  (matches {pat})")
    if frozen:
        return False, "FROZEN PATH VIOLATION:\n  " + "\n  ".join(frozen)
    if not files:
        return False, "no code changes at all"
    return True, f"{len(files)} files changed, none frozen"


def localization_check(project_root: Path, files: list[str]) -> tuple[bool, str]:
    """Expertise-as-script: every NEW R.string reference in Kotlin changes must
    exist in every locale's strings.xml (this app ships 15 locales)."""
    script = Path(__file__).resolve().parent.parent / "skills" / "checks" / "check_localization.py"
    r = subprocess.run(
        ["python3", str(script), str(project_root)] + files,
        capture_output=True, text=True, timeout=120)
    return (r.returncode == 0), (r.stdout + r.stderr)


def gradle_task(worktree: Path, task: str, timeout: int, log) -> tuple[bool, str]:
    log(f"~ gradle {task} (≤{timeout}s)")
    r = run(["./gradlew", task], worktree, timeout=timeout, env=GRADLE_ENV)
    if r.returncode == 0:
        log(f"  ok: {task}")
        return True, ""
    tail = (r.stdout + r.stderr)[-4000:]
    return False, f"{task} FAILED:\n{tail}"


def verify_all(project: Path, worktree: Path, base: str, log, gradle: bool = True) -> tuple[bool, list[str]]:
    """Full gate chain. Returns (passed, failure_texts). Set gradle=False to
    re-run only the diff-scoped checks (used after a rebase)."""
    profile = profile_of(project)
    files = changed_files(worktree, base)
    failures: list[str] = []

    ok, msg = scope_check(profile, files)
    log(f"scope: {msg.replace(chr(10), ' | ')}" if not ok else f"scope ok ({len(files)} files)")
    if not ok:
        failures.append(msg)
        return False, failures

    ok, msg = localization_check(worktree, files)
    log("localization: ok" if ok else "localization: FAIL")
    if not ok:
        failures.append(msg)
        return False, failures

    if not gradle:
        return True, []

    # Compile gate
    ok, msg = gradle_task(worktree, profile["verify"]["compile"], 900, log)
    if not ok:
        failures.append(msg)
        return False, failures

    # Test gate. Known environment quirk: a non-deterministic Robolectric flap in
    # UsageDashboardViewModelTest (documented in project AGENTS.md) — so a test
    # failure gets exactly ONE re-run before we call it a real failure.
    ok, msg = gradle_task(worktree, profile["verify"]["test"], 1500, log)
    if not ok:
        log("tests failed once — retry (possible Robolectric flake, per project docs)")
        ok, msg2 = gradle_task(worktree, profile["verify"]["test"], 1500, log)
        if not ok:
            failures.append(msg + "\n\nRETRY ALSO FAILED:\n" + msg2)
            return False, failures
    return True, failures
