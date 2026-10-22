"""Deterministic gates. The pipeline's authority — an agent's self-report is
never accepted; acceptance = diff in scope + these gates green, run by this
code, not by the agent.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from . import emulator
from .core import profile_of, run

JAVA_HOME = "/Applications/Android Studio.app/Contents/jbr/Contents/Home"
GRADLE_ENV = {"JAVA_HOME": JAVA_HOME, "TERM": "dumb", "CI": "true"}

# On-device test source sets: KMP names the Android instrumented set
# `androidInstrumentedTest` (KGP 2.x), a pure-Android module calls it `androidTest`.
INSTRUMENTED_DIRS = ("androidInstrumentedTest", "androidTest")
INSTRUMENTED_TASK_DEFAULT = ":composeApp:connectedDebugAndroidTest"


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



def instrumented_modules(worktree: Path) -> list[str]:
    """Gradle module dirs (first path segment) that ship on-device tests."""
    mods: set[str] = set()
    for d in INSTRUMENTED_DIRS:
        for p in worktree.glob(f"*/src/{d}/**/*.kt"):
            parts = p.relative_to(worktree).parts
            if parts:
                mods.add(parts[0])
    return sorted(mods)


def has_instrumented_tests(worktree: Path) -> bool:
    """True when the project ships on-device tests. The switch that keeps the
    device gate from spending a boot on a project (or a change) with nothing to
    run — the gate's "cheap" half."""
    return bool(instrumented_modules(worktree))


def default_instrumented_task(worktree: Path) -> str:
    """The instrumented gradle task to run when the profile declares none.

    Derived from the project layout instead of assumed: `:composeApp:` is the
    KMP convention but a pure-Android project's module is `:app`, and a
    hardcoded `:composeApp:connectedDebugAndroidTest` makes the device gate die
    with "Cannot locate tasks that match ... project 'composeApp' not found" on
    every single-module project — a guaranteed FAIL that reads like a code
    failure. With exactly one module shipping instrumented tests, name it; with
    none or several, fall back to the KMP-typical default (several test-bearing
    modules is rare and the profile should declare `verify.instrumented`)."""
    mods = instrumented_modules(worktree)
    if len(mods) == 1:
        return f":{mods[0]}:connectedDebugAndroidTest"
    return INSTRUMENTED_TASK_DEFAULT


def available_avds() -> list[str]:
    """AVD names, via the `android` CLI then the SDK emulator binary. [] means
    "cannot boot here"; an agent-only machine still runs the pipeline."""
    out: list[str] = []
    try:
        r = run(["android", "emulator", "list"], None, timeout=30)
        if r.returncode == 0:
            out = [ln.strip() for ln in r.stdout.splitlines() if ln.strip() and " " not in ln.strip()]
    except (OSError, subprocess.SubprocessError):
        out = []
    if out:
        return out
    exe = shutil.which("emulator") or str(Path.home() / "Library/Android/sdk/emulator/emulator")
    try:
        r = run([exe, "-list-avds"], None, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def instrumented_check(worktree: Path, profile: dict, log) -> tuple[bool, str]:
    """The device gate: run the project's instrumented tests on an emulator.

    Added 2026-10-22, after v1.5.7 shipped a page-render regression that no gate
    could see. The chain was `compileDebugKotlin` + `testDebugUnitTest`, and that
    project has ZERO JVM-test sources — so `testDebugUnitTest` ran as NO-SOURCE
    ("tests green" meant "nothing ran") and the whole verify was compile-only. A
    runtime-only framework contract (PdfRenderer rejects every bitmap config but
    ARGB_8888) therefore survived compile, "tests", review and a Play upload, and
    every user got a spinner that never stops. A compile gate cannot see a
    contract that is only checked when the code runs on a device.

    Deliberately CHEAP (owner, 2026-10-22: "use emulator available on mac, do not
    test aggressive and waste time"):
      * no instrumented test sources -> skipped before spending anything;
      * an emulator already booted (e.g. the owner's) -> reused, never re-booted
        and never stopped;
      * otherwise exactly ONE bounded boot through `with-emulator`, whose EXIT
        trap guarantees teardown on success, failure and timeout, so the run-end
        leak check stays clean.
    """
    if not has_instrumented_tests(worktree):
        log("instrumented: no on-device tests in this project — skipped (no boot)")
        return True, ""
    cfg = profile.get("verify") or {}
    task = cfg.get("instrumented") or "<auto>"
    if task == "<auto>":
        task = default_instrumented_task(worktree)
        log(f"instrumented: task derived from layout → {task}")
    timeout = int(cfg.get("instrumented_timeout", 1200))

    booted = emulator.running()
    if booted:
        where = ", ".join(e.get("avd") or e["serial"] for e in booted)
        log(f"~ instrumented: reusing the emulator already running ({where}) — no boot")
        argv = ["./gradlew", task]
    else:
        avd = cfg.get("avd") or next(iter(available_avds()), "")
        if not avd:
            return False, ("instrumented: on-device tests exist but no AVD is "
                           "available — cannot claim they pass (install an AVD or "
                           "set verify.avd in the project profile)")
        wrapper = shutil.which("with-emulator") or str(Path.home() / ".local/bin/with-emulator")
        if not Path(wrapper).exists():
            return False, ("instrumented: on-device tests exist but `with-emulator` "
                           "is missing — refusing to boot an emulator that might leak")
        log(f"~ instrumented: booting '{avd}' via with-emulator (teardown guaranteed)")
        argv = [wrapper, avd, "--", "./gradlew", task]

    log(f"~ gradle {task} on the device (≤{timeout}s)")
    try:
        r = run(argv, worktree, timeout=timeout, env=gradle_env(worktree))
    except subprocess.TimeoutExpired:
        return False, f"{task} TIMED OUT after {timeout}s on the emulator"
    if r.returncode == 0:
        log(f"  ok: {task}")
        return True, ""
    return False, f"{task} FAILED on the emulator:\n{(r.stdout + r.stderr)[-4000:]}"



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

    # Device gate: runtime-only contracts (framework API rules, rendering,
    # permissions) are invisible to compile + JVM tests. Cheap by design — see
    # instrumented_check. Skipped entirely when the project ships no such tests.
    ok, msg = instrumented_check(worktree, profile, log)
    if not ok:
        failures.append(msg)
        return False, failures
    return True, failures
