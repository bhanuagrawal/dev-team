"""Local delivery — when CI cannot start, a DEBUG build still reaches testers.

Why this exists (owner directive 2026-11-16)
--------------------------------------------
GitHub Actions can refuse to start a job for a reason no commit can fix: the
account's Actions spending limit is reached, or a payment failed. Before this
module a release in that state delivered NOTHING — the version was bumped, the
tag pushed, every run recorded `failed`, and Play went untouched while the
owner's only feedback was a red run a month of retries could not clear
(observed 2026-11-16: every job refused in 4s, three retries included).

So the fallback is a NARROWER channel, not a pretend release:

  * it builds the DEBUG apk — debug keystore, no release secrets, nothing that
    can be published to a store;
  * it distributes through Firebase App Distribution to the group the profile
    declares (`ci.firebase`), the same channel CI's debug job uses;
  * it NEVER claims a Play delivery. Internal testing still needs a real CI
    run; `release` keeps returning "not delivered to Play" and the run says so
    in those words. Saying "shipped" while the store is unchanged is exactly
    the defect the gates exist to prevent.

Every project fact comes from the profile — module, apk path, Firebase app and
group, project id. No project is named here.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .core import RunLog, profile_of, run

# The exact words GitHub puts on a run whose jobs never started because the
# account cannot pay for minutes. Matched case-insensitively; both phrases are
# emitted today, and a subset match is enough to name the cause rather than
# guess it.
BILLING_SIGNS = (
    "recent account payments have failed",
    "spending limit needs to be increased",
)

# `firebase-tools` is the only thing that speaks App Distribution without us
# re-implementing its upload protocol; pin the major so a nightly cannot change
# behaviour under us.
FIREBASE_TOOLS_PACKAGE = "firebase-tools@15"
DEFAULT_DEBUG_APK = "{module}/build/outputs/apk/debug/{module}-debug.apk"


def _slug(profile: dict) -> str:
    return str(profile.get("repo_slug") or "")


def _workflow(profile: dict) -> str:
    return str(profile.get("ci_workflow") or "release.yml")


def firebase_config(profile: dict) -> dict | None:
    """The declared App Distribution target, or None when the project never
    set one up. `app_id` is required; the group is optional (a distribute with
    no groups uploads to the console only)."""
    ci = profile.get("ci") or {}
    fb = ci.get("firebase") or {}
    app_id = str(fb.get("app_id") or "").strip()
    if not app_id:
        return None
    project_id = str(
        fb.get("project_id")
        or (profile.get("connections") or {}).get("firebase", {}).get("project_id")
        or ""
    ).strip()
    return {
        "app_id": app_id,
        "groups": str(fb.get("groups") or "").strip(),
        "project_id": project_id,
    }


def debug_apk_path(project: Path, profile: dict) -> Path:
    ci = profile.get("ci") or {}
    declared = str(ci.get("debug_apk") or "").strip()
    if declared:
        return project / declared
    module = str(ci.get("module") or "").strip()
    if not module:
        return project / "app/build/outputs/apk/debug/app-debug.apk"
    return project / DEFAULT_DEBUG_APK.format(module=module)


def billing_blocked(project: Path, run_id: str | None, log: RunLog | None = None) -> tuple[bool, str]:
    """Is the CI run red because GitHub refused to bill for the job?

    Reads the run's own annotations — the only place GitHub states the cause —
    and returns (blocked, the line that says so). A missing run id or an
    unreadable run answers False: a fallback must never fire on a guess.
    """
    rid = str(run_id or "").strip()
    if not rid:
        return (False, "")
    profile = profile_of(project)
    slug = _slug(profile)
    r = run(["gh", "run", "view", rid, "-R", slug], project, timeout=90)
    text = ((r.stdout or "") + (r.stderr or "")).lower()
    if r.returncode != 0:
        return (False, "")
    for sign in BILLING_SIGNS:
        if sign in text:
            if log:
                log("CI is blocked by GitHub Actions billing — this is not a code failure")
            return (True, sign)
    return (False, "")


def _run_gradle(project: Path, profile: dict, log: RunLog,
                tree: Path | None = None) -> Path:
    """Build the debug apk in a tree, never the owner's checkout.

    Default is the delivery-ref scan tree. A caller that just VERIFIED a worktree
    passes that tree instead: building origin/main there sends testers the build
    they already have while the change under test sits on a branch — observed
    2026-11-16, when an offer-flow fallback delivered the previous app under the
    new version's name.
    """
    from . import scan as scan_mod

    if tree is None:
        tree = scan_mod.origin_tree(project, log)
    else:
        tree = Path(tree)
        if not (tree / ".git").exists():
            raise SystemExit(f"local delivery: not a worktree: {tree}")
    module = str((profile.get("ci") or {}).get("module") or "").strip() or "app"
    # A flavored module has no `assembleDebug` (ZenList builds zenlist/zencoach),
    # so the profile may name the exact debug task and apk path it produces.
    task = str((profile.get("ci") or {}).get("debug_task") or "").strip() \
        or f":{module}:assembleDebug"
    log(f"local delivery: building {task} in {tree}")
    r = run(
        ["./gradlew", task, "--console=plain",
         "-Dorg.gradle.internal.http.socketTimeout=120000",
         "-Dorg.gradle.internal.http.connectionTimeout=120000"],
        tree, timeout=2400,
    )
    apk = debug_apk_path(tree, profile)
    if r.returncode != 0 or not apk.is_file():
        tail = ((r.stdout or "") + (r.stderr or ""))[-700:]
        raise SystemExit(f"local debug build failed (rc={r.returncode}): {tail}")
    log(f"local delivery: apk built — {apk} ({apk.stat().st_size // 1024} KB)")
    return apk


def _firebase_argv() -> list[str] | None:
    """The CLI that can talk to App Distribution on this machine."""
    direct = shutil.which("firebase")
    if direct:
        return [direct]
    if shutil.which("npx"):
        return ["npx", "--yes", FIREBASE_TOOLS_PACKAGE]
    return None


def _distribute(project: Path, profile: dict, apk: Path, version: str,
                notes: str, log: RunLog) -> dict:
    fb = firebase_config(profile) or {}
    argv = _firebase_argv()
    if not argv:
        raise SystemExit("no firebase CLI and no npx on this machine — cannot reach "
                         "App Distribution; install firebase-tools once (`npm i -g firebase-tools`)")
    cmd = argv + ["appdistribution:distribute", str(apk),
                  "--app", fb["app_id"],
                  "--release-notes", notes]
    if fb.get("groups"):
        cmd += ["--groups", fb["groups"]]
    if fb.get("project_id"):
        cmd += ["--project", fb["project_id"]]
    log(f"local delivery: uploading to Firebase App Distribution "
        f"(app {fb['app_id']}, groups {fb.get('groups') or '—'})")
    r = run(cmd, project, timeout=900)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        raise SystemExit(f"App Distribution upload failed (rc={r.returncode}): {out[-700:]}")
    log("local delivery: testers notified")
    return {"app_id": fb["app_id"], "groups": fb.get("groups") or "",
            "version": version, "output_tail": out[-400:]}


def deliver(project: Path, log: RunLog, version: str = "", reason: str = "",
            notes_extra: str = "", distribute: bool = True,
            dry_run: bool = False, tree: Path | None = None) -> dict:
    """Build a debug apk locally and (optionally) send it to the declared
    Firebase App Distribution group. Returns a dict; `release` merges it into
    its own result WITHOUT turning it into a Play delivery."""
    profile = profile_of(project)
    fb = firebase_config(profile)
    version = str(version or "").strip() or "dev"
    result: dict = {"version": version, "reason": reason,
                    "delivered_to_play": False}

    module = str((profile.get("ci") or {}).get("module") or "app")
    if dry_run:
        result.update({"dry_run": True, "module": module,
                       "apk": str(debug_apk_path(tree or project, profile)),
                       "tree": str(tree) if tree else "",
                       "firebase": fb or {}})
        return result
    if not fb:
        log("local delivery: this project declares no ci.firebase app — nothing to "
            "distribute to; build only")
        distribute = False

    apk = _run_gradle(project, profile, log, tree=tree)
    result["apk"] = str(apk)
    result["apk_kb"] = apk.stat().st_size // 1024
    if tree is not None:
        from .core import git as _git
        head = (_git(["rev-parse", "HEAD"], cwd=Path(tree)).stdout or "").strip()
        result["tree"] = str(tree)
        result["commit"] = head[:12]
        notes_extra = (notes_extra + f"\nBuilt from {head[:12]} in {Path(tree).name}.").strip()

    if not distribute:
        result["distributed"] = False
        return result

    notes = (
        f"Debug build {version} — test-only affordances enabled "
        f"(email/password sign-in, seeded readers, mock backend)."
    )
    if notes_extra.strip():
        notes += "\n" + notes_extra.strip()
    if reason:
        notes += f"\nLocal build: GitHub Actions refused to run ({reason})."
    result["firebase"] = _distribute(project, profile, apk, version, notes, log)
    result["distributed"] = True
    return result


def summary_line(result: dict) -> str:
    if result.get("dry_run"):
        return (f"LOCAL DELIVERY (dry run) — module {result.get('module')}, apk "
                f"{result.get('apk')}, firebase {json.dumps(result.get('firebase') or {})}")
    if result.get("distributed"):
        fb = result.get("firebase") or {}
        return (f"LOCAL DELIVERY — debug apk v{result.get('version')} "
                f"({result.get('apk_kb')} KB) sent to Firebase App Distribution "
                f"app {fb.get('app_id')} groups {fb.get('groups') or '—'}. "
                "PLAY IS UNCHANGED: internal testing still needs a delivered release.")
    return (f"LOCAL DELIVERY — debug apk v{result.get('version')} built at "
            f"{result.get('apk')} but not distributed (no ci.firebase declared).")
