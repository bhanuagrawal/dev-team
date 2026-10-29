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
RELEASE_KNOBS: tuple[tuple[str, str], ...] = (
    (r'versionName\s*[=:]\s*["\']([^"\']*)["\']', "versionName"),
    (r'versionCode\s*[=:]\s*(\d+)', "versionCode"),
    (r'\b(minSdk|targetSdk|compileSdk)\s*[=:]\s*([^\n,]+)', "SDK level"),
    (r'\b(storeFile|storePassword|keyAlias|keyPassword)\s*[=:]\s*([^\n]+)', "signing material"),
    (r'(signingConfig\s*=\s*[^\n]+)', "signing config"),
)

# The catalog is where KMP projects actually keep their SDK levels, so checking
# only the version file would leave the most consequential knob unguarded.
RELEASE_FILES_EXTRA = ("gradle/libs.versions.toml",)


def _knobs(text: str) -> dict[str, list[str]]:
    """Every release knob in a file, as label -> the values found, in order.

    Lists rather than single values: a label can legitimately appear more than
    once (several versionCodes across flavours), and losing the extra ones would
    make a change to them invisible. Every pattern captures the VALUE as well as
    the name — capture the name alone and a changed value looks unchanged, which
    is exactly how this check silently passed a targetSdk change the first time
    it was exercised."""
    out: dict[str, list[str]] = {}
    for pattern, label in RELEASE_KNOBS:
        vals = ["=".join(g for g in m.groups() if g).strip()
                for m in re.finditer(pattern, text)]
        out[label] = vals
    return out


def release_knobs_check(profile: dict, worktree: Path, base: str) -> tuple[bool, str]:
    """Refuse a change to the release knobs, wherever the project keeps them.

    Returns (ok, message). Generic: it reads `profile['version_file']` (every
    project has one) plus the Gradle version catalog when the project has one.
    A file that is new in this change, or absent, is skipped — there is no
    baseline to compare, and inventing one would fail honest work."""
    rels = [profile.get("version_file")]
    rels += [r for r in RELEASE_FILES_EXTRA if (worktree / r).exists()]
    rels = [r for r in rels if r]

    checked, changes = [], []
    for rel in rels:
        wt_file = worktree / rel
        if not wt_file.exists():
            continue
        base_r = git(["show", f"{base}:{rel}"], cwd=worktree, timeout=60)
        if base_r.returncode != 0:
            continue          # new file in this change — nothing to protect
        before, after = _knobs(base_r.stdout), _knobs(wt_file.read_text())
        for label in sorted(set(before) | set(after)):
            if before.get(label, []) != after.get(label, []):
                changes.append(
                    f"{label} in {rel}:\n"
                    f"      before: {before.get(label, []) or '(absent)'}\n"
                    f"      after:  {after.get(label, []) or '(absent)'}")
        checked.append(rel)

    if changes:
        return False, (
            "RELEASE KNOB CHANGED — this belongs to the release step and the "
            "owner, not to a code task:\n    " + "\n    ".join(changes) +
            "\n\n  The pipeline bumps the version itself, and the SDK levels and "
            "signing material are owner decisions. Editing the build file to add "
            "a dependency is fine; changing these fields needs a constraint lift, "
            "not an edit."
            "\n\n  (If this worktree has ALREADY been released, the pipeline's own "
            "version bump is the cause — both real callers verify before the "
            "release, so a re-check of a shipped worktree will always show this. "
            "Verify a fresh worktree instead.)")
    return True, ("release knobs unchanged in " + ", ".join(checked)) if checked \
        else "no release file had a baseline — knobs not checked"


# --------------------------------------------------------------------------- #
# third-party dependencies — the hole the build-file unfreeze opened
# --------------------------------------------------------------------------- #
# Editing the build file to add a LIBRARY is now allowed, which is the point of
# protecting its release knobs instead of the whole file. A dependency is not
# neutral, though: it is a third party, a supply chain, an account to create, a
# licence, a revenue share and permanent maintenance. And the plan DID adopt one
# from a spec's stack line without anyone asking what it bought — a hosted
# billing service, for a single non-consumable, in an app whose own spec demanded
# "no backend". So the gate asks the question the plan forgot: a NEW third-party
# dependency must be DECLARED in the task spec. Project-internal modules are
# exempt — `project(":design")` is our own code, not a third party.
DEP_CALL_RE = re.compile(
    r'\b(implementation|api|compileOnly|runtimeOnly|testImplementation|'
    r'androidTestImplementation|debugImplementation|ksp|kapt|annotationProcessor|'
    r'classpath)\s*\(\s*(?:project\s*\(\s*)?["\']([^"\']+)["\']')
ALIAS_RE = re.compile(r'\blibs\.((?:[A-Za-z0-9_]+\.)*[A-Za-z0-9_]+)')
CATALOG_SECTION_RE = re.compile(r'^\[([A-Za-z]+)\]\s*$')
CATALOG_ENTRY_RE = re.compile(
    r'^([A-Za-z0-9_.-]+)\s*=\s*(?:\{[^}]*?(?:module|id)\s*=\s*"([^"]+)"[^}]*\}|"([^"]+)")')


def _declared_deps(text: str) -> set[str]:
    """Third-party dependencies a build file or a version catalogue declares."""
    out: set[str] = set()
    for m in DEP_CALL_RE.finditer(text):
        target = m.group(2)
        if target.startswith(":"):        # project(":design") — our own module
            continue
        out.add(target)
    for m in ALIAS_RE.finditer(text):
        out.add("libs." + m.group(1))
    section = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            section = (CATALOG_SECTION_RE.match(s) or [None, None])[1]
            continue
        if section in ("libraries", "plugins"):
            em = CATALOG_ENTRY_RE.match(s)
            if em:
                out.add(em.group(1))
                if em.group(2) or em.group(3):
                    out.add(em.group(2) or em.group(3))
    return out


def _norm_dep(s: str) -> str:
    """One normal form for comparing a declaration against a dependency.

    Declaring the catalogue KEY must cover the KEY'S COORDINATE and the ALIAS a
    build file uses to reach it — otherwise the honest declaration is refused for
    naming the thing the way the project names it. So strip the `libs.` prefix
    and treat `-`/`_` as `.`; the catalogue expansion below supplies the rest."""
    s = str(s).strip().lower()
    if s.startswith("libs."):
        s = s[5:]
    return s.replace("-", ".").replace("_", ".")


def _catalog_pairs(profile: dict, worktree: Path, base: str) -> dict[str, str]:
    """key -> coordinate/id, for every catalogue entry in the base AND the tree."""
    pairs: dict[str, str] = {}
    for rel in [r for r in RELEASE_FILES_EXTRA if (worktree / r).exists()]:
        texts = [(worktree / rel).read_text()]
        base_r = git(["show", f"{base}:{rel}"], cwd=worktree, timeout=60)
        if base_r.returncode == 0:
            texts.append(base_r.stdout)
        for text in texts:
            section = None
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("["):
                    section = (CATALOG_SECTION_RE.match(s) or [None, None])[1]
                    continue
                if section in ("libraries", "plugins"):
                    em = CATALOG_ENTRY_RE.match(s)
                    if em and (em.group(2) or em.group(3)):
                        pairs[_norm_dep(em.group(1))] = _norm_dep(em.group(2) or em.group(3))
    return pairs


def dependency_check(profile: dict, worktree: Path, base: str,
                     spec: dict | None = None) -> tuple[bool, str]:
    """Report new third-party dependencies; REFUSE them if undeclared.

    With no task spec there is nothing to check a declaration against, so this
    only reports — a caller that cannot say what the task intended must not fail
    the task for it."""
    rels = [profile.get("version_file")]
    rels += [r for r in RELEASE_FILES_EXTRA if (worktree / r).exists()]
    rels = [r for r in rels if r]

    added: dict[str, set[str]] = {}
    for rel in rels:
        wt = worktree / rel
        if not wt.exists():
            continue
        base_r = git(["show", f"{base}:{rel}"], cwd=worktree, timeout=60)
        if base_r.returncode != 0:
            continue                      # new file in this change — no baseline
        new = _declared_deps(wt.read_text()) - _declared_deps(base_r.stdout)
        if new:
            added[rel] = new
    if not added:
        return True, "no new third-party dependency"

    where = "; ".join(f"{rel}: {', '.join(sorted(d))}" for rel, d in sorted(added.items()))
    if spec is None:
        return True, f"NEW dependency (no spec to check a declaration against): {where}"

    # A declaration may name the alias, the catalogue key, or the coordinate —
    # expand it through the catalogue so any of those covers the dependency.
    pairs = _catalog_pairs(profile, worktree, base)
    declared: set[str] = set()
    for d in (spec.get("dependencies_added") or []):
        n = _norm_dep(d)
        declared.add(n)
        if len(n) >= 3:
            if n in pairs:
                declared.add(pairs[n])
            for key, coord in pairs.items():
                # CONTAINMENT, not equality: a declaration often names the
                # coordinate without its version ("g:a") while the catalogue
                # carries "g:a:1.2.3", and both are the same dependency.
                if n in coord or coord in n:
                    declared.add(key)
                    declared.add(coord)
    declared = {d for d in declared if len(d) >= 3}

    flat = sorted({d for deps in added.values() for d in deps})
    undeclared = [d for d in flat
                  if not any(dcl in _norm_dep(d) or _norm_dep(d) in dcl
                             for dcl in declared)]
    if undeclared:
        return False, (
            "UNDECLARED THIRD-PARTY DEPENDENCY:\n    " + where +
            "\n\n  This task adds " + ", ".join(undeclared) + " and the task spec does "
            "not declare it. A dependency is a third party, a supply chain, an account "
            "to create, a licence and permanent maintenance — so it has to be a "
            "DECISION, not a side effect. Declare it in the spec as "
            "\"dependencies_added\": [\"<coordinate or alias>\", ...] together with the "
            "reason it is needed and what the platform cannot already do — or drop it. "
            "The question this gate exists to force: what does this buy that we cannot "
            "get without it?")
    return True, f"new DECLARED dependency: {where}"


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

    # The fields the file freeze used to protect by blocking the whole file.
    ok, msg = release_knobs_check(profile, worktree, base)
    log(f"release knobs: {msg}" if ok else "release knobs: FAIL")
    if not ok:
        failures.append(msg)
        return False, failures

    # A new library has to be a declared decision, not a side effect.
    ok, msg = dependency_check(profile, worktree, base, spec)
    log(f"dependencies: {msg}" if ok else "dependencies: FAIL")
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
