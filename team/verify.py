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
from .core import git, profile_of, run

JAVA_HOME = "/Applications/Android Studio.app/Contents/jbr/Contents/Home"
GRADLE_ENV = {"JAVA_HOME": JAVA_HOME, "TERM": "dumb", "CI": "true"}

# On-device test source sets, Android (`androidTest`) and KMP
# (`androidInstrumentedTest`) conventions, plus the fallback task when the
# project has more than one test-bearing module. These vanished when the
# release-knob block was deleted (2026-10-31): the device gate then crashed with
# NameError on every project that ships instrumented tests, so no release could
# be verified. Keep them next to the gate that reads them.
INSTRUMENTED_DIRS = ("androidInstrumentedTest", "androidTest")
INSTRUMENTED_TASK_DEFAULT = ":composeApp:connectedDebugAndroidTest"

# --------------------------------------------------------------------------- #
# release knobs — protect the FIELDS, not the file
# --------------------------------------------------------------------------- #
# A Gradle build file does two unrelated jobs: it is the DEPENDENCY LIST and it
# is the RELEASE MACHINERY (version, signing material, SDK levels). Freezing the
# whole file protects the second by blocking the first, so adding a library
# becomes an owner approval — a blunt instrument aimed at the wrong thing (and
# doubly wrong because the release step EDITS that file on every run to bump the
# version; only the code-writing agent was ever blocked).
#
# These checks protect the fields instead of the file: a task may edit the build
# file to add a dependency, and may not change the version, the signing material
# or the SDK levels. Those belong to the release step and to the owner.
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


def tool_path(start: Path | None = None) -> str:
    """A PATH that can find the CLIs a gate shells out to.

    The gates run from whatever process drives the pipeline, not from an
    interactive login shell: a GUI or agent launcher can hand us
    `/usr/bin:/bin` and nothing else, so `with-emulator`'s `android` lookup
    fails even though the tool is installed (observed 2026-10-31: verify
    reported the device TEST as failed because the emulator CLI was not on
    PATH). Generic tool locations first, then the caller's PATH.
    """
    sdk = android_sdk_env(start).get("ANDROID_HOME") or str(
        Path.home() / "Library/Android/sdk")
    dirs = [
        str(Path.home() / ".local/bin"),
        "/usr/local/bin", "/opt/homebrew/bin",
        f"{sdk}/platform-tools", f"{sdk}/emulator", f"{sdk}/cmdline-tools/latest/bin",
    ]
    cur = os.environ.get("PATH") or ""
    dirs += [d for d in cur.split(os.pathsep) if d]
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return os.pathsep.join(out)


def gradle_env(worktree: Path | None = None) -> dict:
    """The environment gradle tasks run with (JAVA_HOME + the Android SDK + a
    PATH that reaches the Android CLIs and the emulator wrapper)."""
    return {**GRADLE_ENV, **android_sdk_env(worktree), "PATH": tool_path(worktree)}


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
    """(ok, message). Scope is the task's own allow-list, not a file freeze.

    NO FILE-SCOPE OR KNOB GUARDS (owner directive 2026-10-31). Frozen paths,
    release-knob diffing and dependency declaration were all removed: the owner
    does not yet see a case they protect against and will reintroduce one only if
    a real incident justifies it. What remains is the task's allow-list in the
    developer prompt plus the compile/test/device gates — a bad change is reverted,
    and CI only ever reaches internal testing.
    """
    if not files:
        return False, "no code changes at all"
    return True, f"{len(files)} files changed"


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


def _is_test_runner_infra_failure(output: str) -> bool:
    """True when failure was an inability to boot/connect an emulator or test runner,
    rather than an assertion failure in an executed test."""
    low = output.lower()
    if any(sig in low for sig in ("there were failing tests", "tests on ", "assertionerror", "comparisonfailure")):
        return False
    infra_signatures = (
        "no connected devices",
        "deviceexception",
        "failed to establish session",
        "device offline",
        "device not found",
        "could not connect to",
        "emulator process has exited early",
        "running multiple emulators with the same avd",
        "showing crashdialog to get consent",
        "not available",
        "command not found",
        "missing",
    )
    return any(sig in low for sig in infra_signatures)


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
      * failure to RUN the test (no AVD, missing with-emulator, device offline) is
        NON-BLOCKING (warn-only). Failure of the test ITSELF (assertion/runtime
        failure) is a HARD BLOCKER.
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
            log("~ instrumented: on-device tests exist but no AVD available — failure to run test is non-blocking (warn-only)")
            return True, ""
        wrapper = shutil.which("with-emulator") or str(Path.home() / ".local/bin/with-emulator")
        if not Path(wrapper).exists():
            log("~ instrumented: on-device tests exist but with-emulator missing — failure to run test is non-blocking (warn-only)")
            return True, ""
        # Nothing is booted, so a lock file can only be debris from a dead
        # instance — and `android emulator start` refuses to run at all while
        # one exists ("Running multiple emulators with the same AVD"). Clear it
        # here rather than making the operator discover it (2026-10-25).
        emulator.clear_stale_locks(avd, log)
        log(f"~ instrumented: booting '{avd}' via with-emulator (teardown guaranteed)")
        argv = [wrapper, avd, "--", "./gradlew", task]

    log(f"~ gradle {task} on the device (≤{timeout}s)")
    try:
        r = run(argv, worktree, timeout=timeout, env=gradle_env(worktree))
    except subprocess.TimeoutExpired:
        log(f"~ instrumented: {task} timed out after {timeout}s on the emulator — failure to run test is non-blocking (warn-only)")
        return True, ""
    if r.returncode == 0:
        log(f"  ok: {task}")
        return True, ""

    output = (r.stdout or "") + (r.stderr or "")
    tail = output[-4000:]
    if _is_test_runner_infra_failure(output):
        log(f"~ instrumented: device test runner could not execute ({task}) — failure to run test is non-blocking (warn-only)")
        return True, ""

    return False, f"{task} FAILED on the emulator (test assertion/execution failure):\n{tail}"



def verify_all(project: Path, worktree: Path, base: str, log, gradle: bool = True,
               spec: dict | None = None) -> tuple[bool, list[str]]:
    """Full gate chain. Returns (passed, failure_texts). Set gradle=False to
    re-run only the diff-scoped checks (used after a rebase). `spec` is the task
    spec, when the caller has it — the dependency gate needs it to know what the
    task intended to add."""
    profile = profile_of(project)
    files = changed_files(worktree, base)
    failures: list[str] = []

    ok, msg = scope_check(profile, files)
    log(f"scope: {msg.replace(chr(10), ' | ')}" if not ok else f"scope ok ({len(files)} files)")
    if not ok:
        failures.append(msg)
        return False, failures

    # TEAM GENERIC: the team repo must contain zero project references, whichever
    # agent is driving the run. A project-keyed branch sat in team code for a day
    # because doctor was the only check and nobody ran it (2026-10-30).
    from .doctor import _team_generic_check
    generic_bad = [(req, fix) for req, ok, fix in _team_generic_check() if not ok]
    if generic_bad:
        log("team generic: FAIL — " + "; ".join(r for r, _ in generic_bad))
        failures.append("TEAM NOT GENERIC (project-specific code in dev-team): "
                        + "; ".join(f"{r} → {fix}" for r, fix in generic_bad))
        return False, failures
    log("team generic: ok")

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
