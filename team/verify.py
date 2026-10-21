"""Deterministic gates. The pipeline's authority — an agent's self-report is
never accepted; acceptance = diff in scope + these gates green, run by this
code, not by the agent.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .core import profile_of, run

JAVA_HOME = "/Applications/Android Studio.app/Contents/jbr/Contents/Home"
GRADLE_ENV = {"JAVA_HOME": JAVA_HOME, "TERM": "dumb", "CI": "true"}


def android_sdk_env(start: Path | None = None) -> dict:
    """{"ANDROID_HOME": <sdk>} when the SDK can be located, else {}.

    Resolved the way Gradle itself resolves it: `$ANDROID_HOME` /
    `$ANDROID_SDK_ROOT`, else `sdk.dir` in the nearest `local.properties` at or
    above `start`. This exists because the gates run in a WORKTREE, and a
    worktree is a fresh checkout: `local.properties` is gitignored, so it is
    never there, and every gradle gate then dies with "SDK location not found"
    — a machine-local gap that reads exactly like a code failure and stops a
    healthy run at step 8. (Lesson 2026-10-22.)"""
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        val = os.environ.get(var)
        if val and Path(val).is_dir():
            return {"ANDROID_HOME": val}
    if start:
        for d in [Path(start).resolve(), *Path(start).resolve().parents]:
            lp = d / "local.properties"
            if not lp.is_file():
                continue
            for line in lp.read_text(errors="ignore").splitlines():
                if not line.strip().startswith("sdk.dir"):
                    continue
                val = line.split("=", 1)[-1].strip()
                if val and Path(val).is_dir():
                    return {"ANDROID_HOME": val}
    return {}


def gradle_env(worktree: Path | None = None) -> dict:
    """The environment gradle tasks run with (JAVA_HOME + the Android SDK)."""
    return {**GRADLE_ENV, **android_sdk_env(worktree)}


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
    """(ok, message). Hard rule: nothing under frozen_paths may appear —
    except paths covered by an owner-approved constraint_lift recorded in the
    profile ({"paths": [...], "decided_by": "..."} — engine reads the profile,
    the OWNER records the decision; orchestrator cannot invent a lift)."""
    import fnmatch
    lifted = []
    for lift in profile.get("constraint_lifts", []) or []:
        lifted += lift.get("paths", [])
    frozen = []
    for f in files:
        for pat in profile["frozen_paths"]:
            if not fnmatch.fnmatch(f, pat):
                continue
            if any(fnmatch.fnmatch(f, lp) for lp in lifted):
                continue
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
    r = run(["./gradlew", task], worktree, timeout=timeout, env=gradle_env(worktree))
    if r.returncode == 0:
        log(f"  ok: {task}")
        return True, ""
    out = (r.stdout + r.stderr)
    tail = out[-4000:]
    # Task-name fallback: Kotlin variants got renamed `compileDebugKotlinAndroid`
    # (KGP 2.x) while pure-Android modules keep `compileDebugKotlin` — profiles
    # written for one scheme shouldn't fail gate runs under the other.
    if "Cannot locate tasks that match" in out or "Task not found" in out:
        alt = (task.replace("KotlinAndroid", "Kotlin")
               if "KotlinAndroid" in task
               else (task + "Android" if task.endswith("Kotlin") else None))
        if alt:
            log(f"  {task} not found in this project — retrying as {alt}")
            r = run(["./gradlew", alt], worktree, timeout=timeout, env=gradle_env(worktree))
            if r.returncode == 0:
                log(f"  ok: {alt}")
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
