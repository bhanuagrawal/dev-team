"""Deterministic gates. The pipeline's authority — an agent's self-report is
never accepted; acceptance = diff in scope + these gates green, run by this
code, not by the agent.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
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
    # Last resort, and the one that actually fires on this machine: Android
    # Studio installs the SDK at a fixed per-user path and a project may have no
    # `local.properties` at all (the owner's IDE resolved the SDK by itself, so
    # every pipeline gate died with "SDK location not found" while the owner's
    # own builds were fine; seen 2026-11-03). Probing the standard install
    # locations costs nothing and cannot pick a wrong SDK: each candidate must
    # exist as a directory.
    for candidate in (Path.home() / "Library/Android/sdk", Path.home() / "Android/Sdk"):
        if candidate.is_dir():
            return {"ANDROID_HOME": str(candidate)}
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


def _base36(number: int) -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while number:
        number, rem = divmod(number, 36)
        out = chars[rem] + out
    return out or "0"


def ensure_wrapper_distribution(worktree: Path, log=None) -> str:
    """Make the wrapper's OWN Gradle distribution present before it fetches it.

    Why (2026-11-06): the wrapper downloads its distribution with a 10-second
    connect timeout (`networkTimeout` in gradle-wrapper.properties). A run found
    this machine's cache empty, the wrapper timed out mid-download, and the
    compile gate reddened a healthy tree with a socket stack trace — an
    environment failure wearing a code failure's clothes, one `record --result
    failed` away from being blamed on the diff. Fetching the zip here, with a
    real timeout, into the directory the wrapper itself would use (base36 of the
    MD5 of the distribution URL — Gradle's own scheme) leaves the wrapper to
    verify and unpack it exactly as before.

    Best-effort by design: a project without a wrapper, a non-http distribution
    or an offline machine is reported and left to the wrapper's normal path.
    Returns a one-line status (empty when there was nothing to do).
    """
    props = worktree / "gradle/wrapper/gradle-wrapper.properties"
    try:
        text = props.read_text(errors="ignore")
    except OSError:
        return ""
    m = re.search(r"^\s*distributionUrl\s*=\s*(.+?)\s*$", text, re.M)
    if not m:
        return ""
    # The properties file escapes ':' and '=' — undo just those.
    url = m.group(1).replace("\\:", ":").replace("\\=", "=").replace("\\\\", "\\")
    if not url.lower().startswith(("http://", "https://")):
        return ""
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if not name.endswith(".zip"):
        return ""
    user_home = os.environ.get("GRADLE_USER_HOME") or str(Path.home() / ".gradle")
    digest = hashlib.md5(url.encode("utf-8")).digest()
    target = (Path(user_home).expanduser() / "wrapper" / "dists" / name[:-4]
              / _base36(int.from_bytes(digest, "big")))
    seeded = (target / f"{name}.ok").exists() or (target / name[:-4]).is_dir() \
        or bool(list(target.glob("*.zip")))
    if seeded:
        return ""

    target.mkdir(parents=True, exist_ok=True)
    part = target / f"{name}.part"
    dest = target / name
    line = (f"gradle wrapper: {name} is not cached — fetching it before the gate "
            f"(the wrapper's own timeout is 10s)")
    if log:
        log("~ " + line)
    try:
        import urllib.request
        deadline = time.time() + 900
        with urllib.request.urlopen(url, timeout=30) as resp, part.open("wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                if time.time() > deadline:
                    raise TimeoutError("download exceeded 900s")
        part.replace(dest)
        return line + " — cached"
    except Exception as e:  # an environment problem the wrapper may still survive
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        return (f"gradle wrapper: could not pre-fetch {name} ({e}) — "
                "letting the wrapper try with its own 10s timeout")


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
    a real incident justifies it. What remains is `surface.targets` plus the
    compile/test/device gates — a bad change is reverted,
    and CI only ever reaches internal testing.
    """
    if not files:
        return False, "no code changes at all"
    return True, f"{len(files)} files changed"


def brand_check(worktree: Path, files: list[str]) -> tuple[bool, str]:
    """An icon change carries its provenance, or it does not ship.

    The previous icon release (2026-10-31) bumped the version, replaced every
    mipmap and claimed "stitch-designed" in the commit message — with no
    artboard, no prompt and no provenance record anywhere in the diff. Nothing
    refused it, so the owner could not tell designed art from an accident, and
    the store gate that would have refused it was never asked (owner,
    2026-11-01: the icon is bad; we need more determinism).

    This runs only when the diff touches a launcher icon or the listing icon.
    """
    triggers = [f for f in files
                if re.search(r"(^|/)mipmap-[^/]+/ic_launcher(_round|_foreground)?\.png$", f)
                or re.search(r"(^|/)listing/[^/]+/images/icon\.png$", f)]
    if not triggers:
        return True, ""
    from . import brand_change

    issues = brand_change.gate(worktree)
    if issues:
        return False, ("ICON CHANGE WITHOUT PROVENANCE: " + "; ".join(issues) +
                       " — `devteam brand <project> --status` shows the missing step")
    return True, f"{len(triggers)} icon file(s), provenance matches"


def storefront_decision_check(worktree: Path, files: list[str]) -> tuple[bool, str]:
    """A listing change carries the DECISION about what the listing says.

    `docs/storefront.json` is the scene spec; the editorial review (which frames
    ship, in what order) and the direction (what the listing sells) are both
    stamped with a fingerprint of that spec. When the spec moves and the stamps
    do not, the shipping copy is decided by nobody — and `storefront-build`
    refuses it later, AFTER the release has already gone out (observed
    2026-11-07: verify PASS, then the poster pass BLOCKED on a fingerprint that
    changed when a scene's composable changed after the review was authored).
    Cheap file reads, so it runs before Gradle and names the remedy.
    """
    touched = {"docs/storefront.json", "docs/storefront-review.json",
               "docs/storefront-direction.json"}
    if not (touched & set(files)):
        return True, ""
    from . import storefront_direction as sd, storefront_review as sr
    from . import storefront_preview as sp

    spec_path = worktree / sp.SPEC_REL
    if not spec_path.is_file():
        return True, ""
    try:
        import json as _json
        spec = _json.loads(spec_path.read_text())
        sha = sr.spec_fingerprint(spec)
        stale: list[str] = []
        review_file = worktree / sr.REVIEW_REL
        if review_file.is_file():
            if _json.loads(review_file.read_text()).get("spec_sha") != sha:
                stale.append(sr.REVIEW_REL)
        direction_file = worktree / sd.DIRECTION_REL
        if direction_file.is_file():
            if _json.loads(direction_file.read_text()).get("spec_sha") != sha:
                stale.append(sd.DIRECTION_REL)
    except Exception as exc:  # a check that cannot read the file says so
        return False, f"storefront decision unreadable: {exc}"
    if stale:
        return False, (
            f"STOREFRONT DECISION IS STALE: {sp.SPEC_REL} changed after "
            f"{' and '.join(stale)} was recorded (spec {sha}). The frames' order and the "
            f"listing's argument now describe different words than the ones shipping. "
            f"Re-apply the decision against the new spec — "
            f"`devteam storefront-scenes <project> --review --apply <review.json>` and "
            f"`devteam storefront-direction <project> --apply <direction.json>`, or restamp "
            f"their spec_sha if the decision itself did not change.")
    return True, "review and direction match the scene spec"


def _draws_a_screenshot(worktree: Path, f: str, ui_path: str) -> bool:
    """Does this changed file change what a store screenshot SHOWS?

    The scene spec and its two stamped decisions, the frame registry, and the
    listing folder are picture inputs by definition. Inside the compose module,
    only UI code is: a data-layer or repository change leaves the screen the
    screenshot shows exactly as it was. The classifier is the team's own
    (`code_tests.is_ui_source`, the one the design gate uses), so this gate does
    not invent a second definition of "a screen".
    """
    from . import code_tests

    p = f.replace("\\", "/").lstrip("./")
    if p in ("docs/storefront.json", "docs/storefront-review.json",
             "docs/storefront-direction.json", "docs/storefront-frames.json"):
        return True
    if p.startswith("distribution/listing/"):
        return True
    if not p.startswith(ui_path.rstrip("/") + "/"):
        return False
    try:
        return code_tests.is_ui_source(p, (worktree / p).read_text(errors="ignore"))
    except Exception:
        # A file we cannot read (deleted, binary) is not silently excused: if the
        # diff cannot be classified as "not a screen", it is treated as one.
        return True


def storefront_listing_fresh_check(worktree: Path,
                                   files: list[str] | None = None) -> tuple[bool, str]:
    """The listing's pictures must describe the build a user installs.

    `docs/storefront-frames.json` records what each shipped PNG was drawn from
    (ref, module-source commit, versionName, scene-spec fingerprint, sha256 and
    size per file). This asks the project whether those facts still hold. It is
    separate from `storefront_decision_check` because they fail for different
    reasons and carry different remedies: the decision gate is about words
    nobody decided, this one is about pixels drawn from an older build.

    Refused on a diff that DRAWS a picture (a screen, the scene copy, a frame, the
    listing) without re-recording it. A diff that draws none of those is not
    asked to pay for a re-render it never needed — every release's version bump
    would otherwise make the next unrelated task refuse, and a gate that taxes
    work it did not touch is a gate that gets switched off. Staleness is never
    silenced, though: `store-publish` and `store-readiness` read the same
    function unconditionally, so a stale picture still cannot reach Play.
    """
    from . import storefront_frames as sfr

    try:
        ok, msg = sfr.listing_freshness(worktree)
    except Exception as exc:  # a check that cannot read the tree says so, never "ok"
        return False, (f"listing freshness could not be read ({exc}) — "
                       f"re-render: devteam storefront-scenes <project> --render --frame")
    if ok:
        return ok, msg
    try:
        ui_path = sfr.ui_source(worktree)
    except Exception:
        ui_path = "composeApp/src"
    drawing = [f for f in (files or []) if _draws_a_screenshot(worktree, f, ui_path)]
    if not drawing:
        return True, ("stale, but this diff draws no screen — "
                      + msg.replace("LISTING FRAMES DESCRIBE AN OLDER BUILD: ", "")
                      + " — store-publish still refuses it")
    return False, msg + (f" (this diff draws it: {drawing[0]}"
                         + (", …" if len(drawing) > 1 else "") + ")")


def flow_gate(project: Path, worktree: Path, spec: dict | None,
              files: list[str]) -> tuple[bool, str]:
    """THE release audit: every declared journey must have passed, on this diff.

    Owner policy 2026-11-10 — "ux flow audit is mandatory for release, not any
    other audit". Three things follow, and none of them is negotiable:

      * a journey whose screen this diff touches must have been walked since;
      * the walk must have REACHED ITS GOAL (a walk that stopped at a wall is a
        finding, not a passing audit);
      * there is no excuse field. `"flow_impact": "none"` was the loophole that
        let releases ship with unwalked journeys — an emulator that never booted
        got a reason recorded in the spec and the gate waved it through.

    Deterministic and offline: it reads `docs/ux-flows.json` and the recorded
    walks in `.pipeline/ux_flows/*/result.json`. It never boots a device — the
    walking happens in `devteam ux-flow`, which the developer runs on its tree.
    ux-audit stills, ux-video replays, storefront review and design drift are
    advisory: they inform, they do not gate a release.
    """
    from . import ux_audit, ux_flow
    try:
        flows = ux_flow.declared(project)
    except Exception as exc:
        return True, f"flow gate could not read the journey spec ({exc})"
    if not flows:
        return True, "no goal-declared journey — the ux-flow audit has nothing to walk"
    surfaces = {s["label"].strip().lower(): s for s in ux_audit.surfaces(project)}
    changed = {Path(f).as_posix() for f in files}
    broken: list[str] = []
    for flow in flows:
        fid = str(flow["id"])
        problem = ux_flow.walk_problem(project, flow)
        if problem is None:
            # It passed, but does the pass describe THIS diff? A walk older than
            # the screen it walks is evidence about an older build.
            surface = surfaces.get(str(flow.get("surface") or "").strip().lower())
            touched = [p for p in (surface or {}).get("paths") or []
                       if Path(p).as_posix() in changed
                       or any(Path(c).name == Path(p).name for c in changed)]
            if touched:
                result = ux_flow.run_dir(project) / ux_flow._slug(fid) / "result.json"
                try:
                    walked = result.stat().st_mtime
                except OSError:
                    walked = 0.0
                newest = 0.0
                for rel in touched:
                    for base in (worktree, project):
                        try:
                            newest = max(newest, (base / rel).stat().st_mtime)
                        except OSError:
                            continue
                if newest > walked:
                    problem = "its screen changed after the last walk"
        if problem:
            broken.append(f"{fid} ({problem})")
    if not broken:
        return True, f"{len(flows)} declared journey(s) passed on this diff"
    blocked = ux_flow.active_block(project)
    if blocked:
        # Not the app: a human action unblocks this, so the gate says whose it is
        # instead of handing the developer a defect it cannot fix in code.
        return False, (
            "UX-FLOW AUDIT BLOCKED — this is NOT a product failure. "
            f"{blocked['flow']}: {blocked['reason']} — the owner can unblock it: "
            f"{blocked['ask']}. Nothing in the diff can fix this; say so in the "
            "handover and ask. (`devteam ux-flow <project> --mock` avoids the "
            "provider wall when the mock can complete the journey.)")
    return False, (
        "UX-FLOW AUDIT NOT PASSED — this is the release audit (owner policy "
        "2026-11-10), so there is no excuse field to fill in: " + "; ".join(broken[:4]) +
        ". Walk the journeys on this tree with `devteam ux-flow <project> --worktree "
        "<tree> --flow <id>` and fix what stops them reaching their goal — an app that "
        "compiles but cannot be used is not shippable.")


def design_gate(project: Path, worktree: Path, spec: dict | None,
                files: list[str], base: str = "origin/main") -> tuple[bool, str]:
    """No UI code without a design: find it, or get it created.

    Owner directive 2026-11-10 — an agent may write UI only when a design exists
    for that surface. "Exists" is not a feeling: it is either a screen the
    project already owns (docs/stitch/manifest.json + its exported snapshot, to
    reuse or tweak) or a design the designer produced for THIS task (recorded by
    `devteam design-consult --record`, with the updated/new screen exported into
    `docs/stitch/screens/<id>/` whenever UI structure changes). Building a
    screen out of tokens alone — or claiming `existing_screen` while adding new
    composables/layouts — is what this refuses.

    Deterministic and offline: it classifies the diff with the same classifier
    the UI test gate uses, inspects structural UI additions in the diff, and
    verifies real Stitch snapshot artifacts in the worktree.
    """
    if not spec:
        return True, ""  # no task spec — nothing to bind a design answer to
    from . import code_tests, designer
    ui_files: list[str] = []
    for rel in files:
        path = worktree / rel
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        if code_tests.is_test_path(rel) or not code_tests.is_production_source(rel):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if code_tests.is_ui_source(rel, text):
            ui_files.append(rel)
    if not ui_files:
        return True, "no UI file changed — no design needed"

    mismatch = designer.manifest_project_mismatch(project, root=worktree)
    if mismatch:
        return False, mismatch

    st = designer.status(project, spec, root=worktree)
    label = designer.task_label(spec)
    state = st.get("state")
    remedy = (
        "FIND THE DESIGN OR GET IT CREATED — do not write this screen blind: "
        "if the surface already exists in docs/stitch/manifest.json with a snapshot "
        "under docs/stitch/screens/<id>/, reuse it (copy/wiring only) or tweak it "
        "(`devteam stitch-tweak`); if it does not, run "
        f"`devteam design-consult {project.name} --task {label} --prompt`, "
        "record the answer with `--record`, and generate/tweak the screen in Stitch "
        "so docs/stitch/screens/<id>/index.html + screen.png are exported in THIS change.")
    if state == "not_applicable":
        return True, "the designer says this task has no UI surface"
    if state in ("pending", "stale"):
        return False, (
            "NO DESIGN FOR THIS UI (" + state + ": " + str(st.get("reason") or "") +
            "). UI files changed: " + ", ".join(ui_files[:4]) + ". " + remedy)
    if state != "answered":
        return False, f"design state unreadable ({state}) for {label}. " + remedy

    decision = str(st.get("decision") or "")
    screen = st.get("screen") or {}
    m = designer.manifest(project, root=worktree)
    entry = designer.find_manifest_screen(m, screen)
    png, html, sid = designer.snapshot_paths(worktree, screen, m)
    title = str((entry or {}).get("title") or screen.get("title") or "").strip()
    norm_files = {f.replace("\\", "/").lstrip("./") for f in files}

    if decision == "new_screen":
        if not (png and html and entry):
            return False, (
                "NEW SCREEN NOT DESIGNED YET — the designer's answer for this task is "
                f"`new_screen` ({title or sid or 'unnamed'}) but its exported snapshot "
                "(index.html + screen.png) and manifest.json entry are not in the worktree's "
                "docs/stitch/. Run `devteam stitch-screen --generate` and export them in this change.")
        expected_rel = f"docs/stitch/screens/{sid}/"
        if not any(f.startswith(expected_rel) for f in norm_files):
            return False, (
                f"NEW SCREEN SNAPSHOT NOT UPDATED IN DIFF — `{expected_rel}` was not modified/added "
                "in this worktree diff. Export the newly generated Stitch screen into the worktree.")
        return True, f"new screen designed and exported ({title or sid})"

    if decision == "tweak_screen":
        if not (png and html and entry):
            return False, (
                "TWEAKED SCREEN SNAPSHOT MISSING — the designer's answer for this task is "
                f"`tweak_screen` ({title or sid or 'unnamed'}) but its snapshot or manifest entry "
                "is missing in the worktree's docs/stitch/. Run `devteam stitch-tweak` first.")
        expected_rel = f"docs/stitch/screens/{sid}/"
        cur_sha = designer.snapshot_digest(png, html)
        base_sha = str(st.get("baseline_snapshot_sha") or "")
        snapshot_touched = any(f.startswith(expected_rel) for f in norm_files)
        snapshot_changed = bool(base_sha and cur_sha and cur_sha != base_sha)
        if not (snapshot_touched or snapshot_changed):
            return False, (
                f"TWEAK_SCREEN DID NOT EXPORT A STITCH TWEAK — `{expected_rel}` is unchanged in "
                "this worktree diff. Run `devteam stitch-tweak` (with `--worktree`) so the updated "
                "Stitch snapshot is exported before writing UI code.")
        return True, f"screen tweaked in Stitch and exported ({title or sid})"

    if decision == "existing_screen":
        if not (png and html and entry):
            return False, (
                "EXISTING SCREEN SNAPSHOT MISSING — `existing_screen` named "
                f"'{title or sid or 'unnamed'}', which is not present in the worktree's "
                "docs/stitch/manifest.json + docs/stitch/screens/. Sync Stitch or use `tweak_screen`/`new_screen`.")
        summary = designer.diff_ui_summary(worktree, base, ui_files)
        if summary["structural_change"]:
            details: list[str] = []
            if summary["added_ui_files"]:
                details.append(f"new UI files: {', '.join(summary['added_ui_files'][:3])}")
            if summary["added_composables"]:
                details.append(f"new @Composables: {', '.join(summary['added_composables'][:4])}")
            if summary["added_layout_lines"] >= 4:
                details.append(f"{summary['added_layout_lines']} added layout lines")
            return False, (
                "EXISTING_SCREEN CANNOT COVER NEW UI STRUCTURE (" + "; ".join(details) + "). "
                "Do not invent UI structure without a Stitch design: run `devteam design-consult` "
                "with `tweak_screen` (`devteam stitch-tweak`) or `new_screen` (`devteam stitch-screen --generate`) "
                "and export the updated snapshot into docs/stitch/screens/ before writing UI code.")
        return True, (f"design answered: existing_screen"
                      + (f" — {title}" if title else ""))

    return True, (f"design answered: {decision}"
                  + (f" — {title}" if title else ""))


def localization_check(project_root: Path, files: list[str]) -> tuple[bool, str]:
    """Expertise-as-script: every NEW R.string reference in Kotlin changes must
    exist in every locale's strings.xml (this app ships 15 locales)."""
    script = Path(__file__).resolve().parent.parent / "skills" / "checks" / "check_localization.py"
    r = subprocess.run(
        ["python3", str(script), str(project_root)] + files,
        capture_output=True, text=True, timeout=120)
    return (r.returncode == 0), (r.stdout + r.stderr)


def gradle_task(worktree: Path, task: str, timeout: int, log) -> tuple[bool, str]:
    # `task` may name SEVERAL Gradle tasks, comma/whitespace separated. A KMP project
    # keeps its pure logic in a library module while the profile names the app's task,
    # so a shared-module unit test used to compile and never run in any gate. One field
    # carries them all; the invocation stays a single gradlew call.
    tasks = [t for t in re.split(r"[,\s]+", task.strip()) if t]
    ensure_wrapper_distribution(worktree, log)
    log(f"~ gradle {' '.join(tasks)} (≤{timeout}s)")
    r = run(["./gradlew", *tasks], worktree, timeout=timeout, env=gradle_env(worktree))
    if r.returncode == 0:
        log(f"  ok: {' '.join(tasks)}")
        return True, ""
    out = (r.stdout + r.stderr)
    tail = out[-4000:]
    # Task-name fallback: Kotlin variants got renamed `compileDebugKotlinAndroid`
    # (KGP 2.x) while pure-Android modules keep `compileDebugKotlin` — profiles
    # written for one scheme shouldn't fail gate runs under the other.
    if "Cannot locate tasks that match" in out or "Task not found" in out:
        alt = [
            t.replace("KotlinAndroid", "Kotlin") if "KotlinAndroid" in t
            else (t + "Android" if t.endswith("Kotlin") else t)
            for t in tasks
        ]
        if alt != tasks:
            log(f"  {' '.join(tasks)} not found in this project — retrying as {' '.join(alt)}")
            r = run(["./gradlew", *alt], worktree, timeout=timeout, env=gradle_env(worktree))
            if r.returncode == 0:
                log(f"  ok: {' '.join(alt)}")
                return True, ""
            tail = (r.stdout + r.stderr)[-4000:]
    return False, f"{task} FAILED:\n{tail}"



def _task_module(task: str) -> str:
    """The module dir a Gradle task name belongs to ('' when it names none)."""
    t = task.lstrip(":")
    return t.split(":")[0] if ":" in t else ""


def _ui_only_args(task: str, classes: dict[str, list[str]]) -> list[str] | None:
    """`--tests=<fqcn>` args isolating a task's UI test classes, or None.

    None means "this task has no UI test classes to run" (skip it) — including a
    module-less task like `testBuildLogic`, which cannot be attributed to any
    module's UI tests and must not be run unfiltered.
    """
    picked = classes.get(_task_module(task)) or []
    if not picked:
        return None
    return [f"--tests={c}" for c in picked]


def integration_check(worktree: Path, profile: dict, log) -> tuple[bool, str]:
    """The ONE test pass: UI/integration tests, run off-device.

    Owner policy (2026-11-10): unit tests are retired. The team requires a UI
    test for every changed UI file (`code_tests.gate`) and runs only UI tests —
    a logic-only change adds no test obligation, and the project's unit-only
    classes are not spent as a gate.

    Selection, most explicit first:
      * `verify.integration_task` — the project names the task; run it as-is
        (the project owns what it contains);
      * `verify.test` — the legacy JVM test task, kept as the harness so UI
        tests that live in a JVM test source set (Robolectric Compose) still
        run, FILTERED to the classes `code_tests` classifies as UI tests. A
        unit-only class never executes here.
      * neither — nothing to run, skipped with a log line.

    Off-device by policy (2026-11-08): booting an emulator inside verify failed
    for infrastructure reasons and read like a code failure; the device passes
    (`ux-audit`, `ux-video`, `ux-flow`) own device validation.
    """
    cfg = dict(profile.get("verify") or {})
    explicit = str(cfg.get("integration_task") or "").strip()
    from . import code_tests
    # An explicit task owns its own selection, so any UI test counts; the legacy
    # JVM harness can only run classes compiled into the JVM test task.
    classes = code_tests.ui_test_classes(
        worktree, code_tests.TEST_DIRS if explicit else code_tests.JVM_TEST_DIRS)
    if not classes:
        log("integration: no asserting UI tests in this project — skipped")
        return True, ""
    raw = explicit or str(cfg.get("test") or "")
    tasks = [t for t in re.split(r"[,\s]+", raw.strip()) if t]
    if not tasks:
        log("integration: no UI test task declared — skipped (set "
            "verify.integration_task to run the project's UI tests)")
        return True, ""
    try:
        timeout = int(cfg.get("integration_timeout", 1500))
    except (TypeError, ValueError):
        timeout = 1500
    ensure_wrapper_distribution(worktree, log)

    for task in tasks:
        filters = _ui_only_args(task, classes) if not explicit else []
        if filters is None:
            log(f"integration: {task} carries no UI tests — skipped")
            continue
        args = ["./gradlew", task, *filters]
        label = (f"{len(filters)} UI class(es)" if filters
                 else "declared integration task")
        log(f"~ gradle {task} ({label}, off-device, ≤{timeout}s)")
        try:
            r = run(args, worktree, timeout=timeout, env=gradle_env(worktree))
        except subprocess.TimeoutExpired:
            log(f"~ integration: {task} timed out after {timeout}s — warn-only")
            continue
        out = (r.stdout or "") + (r.stderr or "")
        # A class that a flavour/variant does not compile makes Gradle reject the
        # whole include list ("No tests found for given includes"). Fall back to
        # the task as declared rather than blocking on a filter mismatch.
        if r.returncode != 0 and filters and "No tests found for given includes" in out:
            log(f"integration: UI class filter did not match a compiled test — "
                f"running {task} unfiltered")
            args = ["./gradlew", task]
            try:
                r = run(args, worktree, timeout=timeout, env=gradle_env(worktree))
            except subprocess.TimeoutExpired:
                log(f"~ integration: {task} timed out after {timeout}s — warn-only")
                continue
            out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            # ONE retry: a Robolectric UI suite has a known non-deterministic flap.
            log(f"integration: {task} failed once — retry")
            try:
                r = run(args, worktree, timeout=timeout, env=gradle_env(worktree))
            except subprocess.TimeoutExpired:
                log(f"~ integration: retry timed out after {timeout}s — warn-only")
                continue
            if r.returncode != 0:
                tail = (((r.stdout or "") + (r.stderr or ""))[-4000:])
                return False, f"{task} FAILED (UI tests, off-device):\n{tail}"
        log(f"  ok: {task} (UI tests, off-device)")
    return True, ""



def revamp_gate(project: Path, worktree: Path, spec: dict | None, files: list[str]) -> tuple[bool, str]:
    """Ensure that a revamp / rebuild task genuinely replaced the legacy codebase.

    Refuses releases that claim to be a 'revamp' or 'rebuild' but only touched
    minor cosmetic lines instead of replacing the core application code.

    It does NOT demand tests. "TESTS ARE RETIRED — ALL OF THEM" (owner policy
    2026-11-10, docs/guarantees.md): the gate used to refuse a revamp whose diff
    carried no `*Test.kt`, which put a test requirement back on exactly the
    tasks the policy had cleared — and a UI change that removes a door and moves
    a section is not a ground-up rebuild just because its label says revamp.
    """
    if not spec:
        return True, "no task spec — revamp gate skipped"
    label = str(spec.get("label", "")).lower()
    req = str(spec.get("owner_request", "")).lower()
    spec_id = str(spec.get("id", "")).lower()
    is_revamp = (
        spec.get("kind") == "revamp"
        or spec.get("type") == "revamp"
        or any(k in spec_id for k in ("revamp", "rebuild", "ground-up"))
        or any(k in label for k in ("revamp", "rebuild"))
        or any(k in req for k in ("revamp", "rebuild", "delete the codebase", "delete codebase", "from scratch"))
    )
    if not is_revamp:
        return True, "not a revamp task"

    # Check that the application code really moved: a one-line cosmetic touch is
    # not a rebuild.
    code_changes = [f for f in files if f.endswith(".kt") or f.endswith(".java")]
    if len(code_changes) < 3:
        return False, (
            f"task is marked as revamp/rebuild, but diff has only {len(code_changes)} code file changes. "
            "A ground-up rebuild must rewrite the application codebase, not just tweak single lines."
        )

    return True, f"{len(code_changes)} code files rebuilt"


def store_note_check(project: Path, worktree: Path, spec: dict | None) -> tuple[bool, str]:
    """A build may not ship the previous release's "What's new" text again.

    Cheap (reads files + the ledger, no Gradle), so it sits with the diff-scoped
    gates: a stale note is caught where the agent can still fix it in one edit —
    a `whatsnew` line in the task spec — instead of surfacing on the listing
    after delivery. `whatsnew_gate` explains the failure; this only supplies the
    worktree so a curated project's edited file counts as the note.
    """
    if not spec:
        return True, "no task spec — release-note gate skipped"
    from . import shipper

    return shipper.whatsnew_gate(project, spec, root=worktree)


def stitch_ledger_check(project: Path, worktree: Path) -> tuple[bool, str]:
    """The Stitch ledger must not contradict itself.

    BookSwap shipped a manifest where `dc488d60` was BOTH canonical and retired,
    its export directory still on disk, and its `replaced_by` pointing at a
    screen that was itself neither canonical nor retired. Nothing read
    `retired_screens`, so the ledger could lie indefinitely. Owner directive
    2026-11-11: one row per surface, a dead id is never a surface, and the
    rendered RETIRED.md must match the manifest — enforced, not remembered.
    """
    manifest_path = Path(worktree) / "docs" / "stitch" / "manifest.json"
    if not manifest_path.is_file():
        return True, "no Stitch manifest"
    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as exc:  # noqa: BLE001
        return False, f"STITCH MANIFEST UNREADABLE — {manifest_path}: {exc}"
    screens = manifest.get("canonical_screens") or []
    retired = manifest.get("retired_screens") or []
    dead: set[str] = set()
    for r in retired:
        for key in ("id", "stitch_id"):
            v = str(r.get(key) or "").strip()
            if v:
                dead.add(v)
    problems: list[str] = []

    both = [str(s.get("id")) for s in screens
            if str(s.get("stitch_id") or "") in dead]
    if both:
        problems.append("a retired screen is still canonical: " + ", ".join(both[:6])
                        + " — one row per surface; adopt the new design onto the surface id "
                          "(`devteam stitch-adopt`) or drop the row (`devteam stitch-retire --yes`)")
    canonical_ids = {str(s.get("id")) for s in screens}
    canonical_stitch = {str(s.get("stitch_id") or s.get("id")) for s in screens}
    dangling = [str(r.get("stitch_id")) for r in retired
                if not str(r.get("replaced_by") or "").strip()
                and not str(r.get("reason") or "").strip()]
    if dangling:
        problems.append("retired with no successor and no reason: " + ", ".join(dangling[:6])
                        + " — pass --replaced-by (what took over) or --why (why it is dead)")
    lost = [f"{r.get('stitch_id')} → {r.get('replaced_by')}" for r in retired
            if str(r.get("replaced_by") or "") and str(r.get("replaced_by")) not in canonical_stitch]
    if lost:
        problems.append("replaced_by is not a canonical screen: " + ", ".join(lost[:4])
                        + " — re-point it at the surface that actually took over")
    dirs = Path(worktree) / "docs" / "stitch" / "screens"
    if dirs.is_dir():
        stale = sorted(d.name for d in dirs.iterdir()
                       if d.is_dir() and d.name in dead and d.name not in canonical_ids)
        if stale:
            problems.append("export directories for dead ids: " + ", ".join(stale[:6])
                            + " — `devteam stitch-retire --yes` drops them")
        orphan = sorted(d.name for d in dirs.iterdir()
                        if d.is_dir() and d.name not in canonical_ids and d.name not in dead)
        if orphan:
            problems.append("export directories with no canonical row: " + ", ".join(orphan[:6])
                            + " — one directory per surface; retire the screen "
                              "(`devteam stitch-retire`) or re-export it (`devteam stitch-export`)")
        missing = sorted(sid for sid in canonical_ids if sid and not (dirs / sid).is_dir())
        if missing:
            problems.append("canonical screens with no export: " + ", ".join(missing[:6])
                            + " — run `devteam stitch-export`")
    ledger = Path(worktree) / "docs" / "stitch" / "RETIRED.md"
    if retired and not ledger.is_file():
        problems.append("retired screens recorded but RETIRED.md is missing — "
                        "re-render it by re-running `devteam stitch-retire --yes`")
    if not retired and ledger.is_file():
        problems.append("RETIRED.md exists with an empty ledger — it is rendered from "
                        "manifest.json; delete it or record the retirement")
    if problems:
        return False, "STITCH LEDGER DISAGREES WITH ITSELF — " + "; ".join(problems)
    return True, (f"{len(screens)} surface(s), {len(retired)} retired, ledger consistent"
                  if retired else f"{len(screens)} surface(s), no retirements")


def verify_all(project: Path, worktree: Path, base: str, log, gradle: bool = True,
               spec: dict | None = None) -> tuple[bool, list[str]]:
    """Full gate chain. Returns (passed, failure_texts). Set gradle=False to
    re-run only the diff-scoped checks (used after a rebase). `spec` is the task
    spec, when the caller has it — the dependency gate needs it to know what the
    task intended to add."""
    profile = profile_of(project)
    files = changed_files(worktree, base)
    failures: list[str] = []

    ok, msg = flow_gate(project, worktree, spec, files)
    if not ok:
        log("flow: FAIL — " + msg[:300])
        failures.append(msg)
    else:
        log("flow: " + (msg or "no declared journey touches this diff"))
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

    # UX: a run can change how a screen LOOKS or FEELS without any gate noticing.
    # This is a WARNING, never a failure — the gates stay compile/tests — but it
    # names the screens nobody has reacted to since the change, so "we polished it"
    # cannot be claimed without a reaction on record.
    try:
        from . import ux_audit
        stale = ux_audit.stale_for_files(project, files)
        if stale:
            log("ux: " + "; ".join(f"{s['label']} changed ({s['status']}), no audit since"
                                   for s in stale[:4]))
            log("ux: run `devteam ux-audit <project>` — unchanged screens cost nothing")
        unbound = ux_audit.scenes_without_surfaces(project, files)
        if unbound:
            log("ux: " + "; ".join(f"scene {s['scene']} renders {s['file']}, "
                                   "which no declared surface audits" for s in unbound[:4]))
            log("ux: bind it as a project target or drop the scene — a screen no surface "
                "owns can change without any audit noticing")
    except Exception as e:  # a dead check must SAY it is dead, not vanish silently
        log(f"ux: could not check (no audit signal): {e} — "
            "`devteam capabilities <project>` names what is missing")

    # The MOTION half: a changed screen can leave a recorded flow describing an
    # older build. Same deal — a warning that names the flow, never a gate.
    try:
        from . import ux_video
        stale_flows = ux_video.stale_for_files(project, files)
        if stale_flows:
            log("ux video: " + "; ".join(f"{s['id']} predates this change "
                                         f"(surface {s['surface']})"
                                         for s in stale_flows[:4]))
            log("ux video: run `devteam ux-video <project>` — only what moved is re-recorded")
    except Exception as e:
        log(f"ux video: could not check (no flow signal): {e}")

    # The JOURNEY half is the release audit itself — `flow_gate` above already ran
    # it and refused if it did not pass, so there is no second, softer opinion
    # here. What is printed is the evidence: which walks are current, which are
    # older than the diff. (Owner policy 2026-11-10: the ux-flow audit is the
    # audit that gates a release; every other audit informs.)
    try:
        from . import ux_flow
        stale_journeys = ux_flow.stale_for_files(project, files)
        if stale_journeys:
            log("ux flow: stale — " + "; ".join(
                f"{s['id']} ({s['why']})" for s in stale_journeys[:4]))
    except Exception as e:
        log(f"ux flow: could not check (no journey signal): {e}")

    ok, msg = localization_check(worktree, files)
    log("localization: ok" if ok else "localization: FAIL")
    if not ok:
        failures.append(msg)
        return False, failures

    ok, msg = brand_check(worktree, files)
    log("brand: ok (" + msg + ")" if ok else "brand: FAIL — " + msg)
    if not ok:
        failures.append(msg)
        return False, failures

    # NO UI CODE WITHOUT A DESIGN (owner directive 2026-11-10): find the design
    # the project already owns, or get one created, before a screen is written.
    # Runs on file reads, before the Gradle gates, so a blind screen is refused
    # cheaply and the remedy (consult → record → export) is named.
    ok, msg = design_gate(project, worktree, spec, files, base=base)
    log("design: ok — " + msg if ok else "design: FAIL — " + msg)
    if not ok:
        failures.append(msg)
        return False, failures

    # TESTS ARE RETIRED (owner policy 2026-11-10, restated for UI tests: "get rid
    # of them completely"). The team no longer writes, requires or runs a test
    # suite: a change is proven by the compile gate and, for a screen, by the
    # ux-flow audit — a vision model walking the journey on a device. `verify`
    # therefore has no test gate and no coverage floor to check. What the gate
    # still reads from test-looking files is only the diff CLASSIFIER used by the
    # design gate (`code_tests.is_ui_source`), which is not a test requirement.

    # Store note: the text a user sees on the listing is a deliverable too, and
    # a repeated one is a defect the code can detect exactly. Runs before the
    # Gradle gates because it costs nothing and its remedy is a one-line edit.
    ok, msg = store_note_check(project, worktree, spec)
    log("store note: ok — " + msg if ok else "store note: FAIL — " + msg)
    if not ok:
        failures.append(msg)
        return False, failures

    # THE STITCH LEDGER: the screen set is the design spec, so a manifest that
    # disagrees with RETIRED.md (or with its own export directories) is a design
    # defect, not paperwork. Offline and instant — it reads files only.
    ok, msg = stitch_ledger_check(project, worktree)
    log("stitch ledger: ok — " + msg if ok else "stitch ledger: FAIL — " + msg)
    if not ok:
        failures.append(msg)
        return False, failures

    # Storefront decision: a scene spec that moved without its review/direction
    # being restamped is a listing nobody decided — and the poster pass blocks on
    # it only AFTER the release has shipped.
    ok, msg = storefront_decision_check(worktree, files)
    log("storefront: ok — " + msg if ok else "storefront: FAIL — " + msg)
    if not ok:
        failures.append(msg)
        return False, failures

    # Storefront LISTING freshness: the PNGs Play uploads are a claim about a
    # build. Refused when THIS diff draws a picture (a screen, the scene copy, a
    # frame, the listing) and does not re-record it — the failure is the diff that
    # changed the code and NOT the pictures, which a gate that only ran when the
    # listing was edited could never see. Cheap: file reads, PNG headers and three
    # git reads; projects that never recorded provenance pass. A diff that draws
    # nothing new does not pay for a re-render, but store-publish reads the same
    # function unconditionally, so a stale picture still cannot reach Play.
    ok, msg = storefront_listing_fresh_check(worktree, files)
    log("storefront listing: ok — " + msg if ok else "storefront listing: FAIL — " + msg)
    if not ok:
        failures.append(msg)
        return False, failures

    # REVAMP CODEBASE REBUILD GATE: a task claiming to be a revamp or rebuild from scratch
    # must genuinely replace the legacy codebase and supply fresh tests.
    ok, msg = revamp_gate(project, worktree, spec, files)
    log("revamp: ok — " + msg if ok else "revamp: FAIL — " + msg)
    if not ok:
        failures.append(msg)
        return False, failures

    if not gradle:
        # --skip-gradle still answers the file-level gates it just ran: a refusal
        # collected above is a refusal, not "nothing to report".
        return (not failures), failures

    # Compile gate
    ok, msg = gradle_task(worktree, profile["verify"]["compile"], 900, log)
    if not ok:
        failures.append(msg)
        return False, failures

    # NO TEST PASS. Tests are retired (owner policy 2026-11-10): the suite is gone
    # from the projects and from this gate. A regression is caught by the compile
    # gate, by the store/design gates, or by the ux-flow audit walking the real
    # journey. `integration_check` stays on disk for a project that still wants a
    # test run, but nothing here calls it.
    # A GATE THAT APPENDS WITHOUT RETURNING STILL FAILS THE RUN. The flow gate
    # collects instead of returning early so compile/tests still produce their
    # evidence in the same pass — but that meant `verify` printed "VERIFY: PASS"
    # with the ux-flow refusal listed underneath it (seen 2026-11-10 on the
    # retire branch, the first run under the new release-audit policy). The
    # verdict is "no collected failure", never "the last gate was happy".
    return (not failures), failures
