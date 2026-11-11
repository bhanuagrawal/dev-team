"""One CI shape for every project: a version tag → signed AAB → Play internal.

Projects had drifted into tag-triggered and bump-triggered variants, with a
production-promotion workflow sitting in one of them and upload steps that
differed in ways no one could review. This module owns the single canonical
workflow (team/templates/ci-release.yml) and renders it from the project's own
`profile["ci"]` block, so a new app gets the same delivery path as the old ones
and a project copy is never hand-edited.

Nothing here can promote to production — by design, promotion is a human action.
"""
from __future__ import annotations

import re
from pathlib import Path

from .core import profile_of, profile_root, save_json

TEMPLATE = Path(__file__).parent / "templates/ci-release.yml"
WORKFLOW_REL = ".github/workflows/release.yml"

# Bumped whenever the rendered text changes meaning (not formatting). A project's
# copy carries it in its header, so a reader can tell which generation a workflow
# came from even without running doctor.
TEMPLATE_VERSION = 2

KNOWN_PLACEHOLDERS = (
    "__BUNDLE_TASKS__", "__RELEASE_FILES__", "__PACKAGE_NAME__",
    "__WHATSNEW_FILE__", "__WHATSNEW_DIR__", "__WHATSNEW_LIMIT__",
    "__VERSION_FILE__",
    "__MODULE__", "__DEBUG_APK__", "__FIREBASE_APP_ID__", "__FIREBASE_GROUPS__",
)

# The optional QA channel. Everything between these markers is rendered ONLY for a
# project whose profile declares ci.firebase {app_id, groups}; for every other
# project the block is removed, so their workflow stays byte-identical and nothing
# is distributed by accident.
FIREBASE_BLOCK = re.compile(r"^#@@FIREBASE_JOB@@\n(.*?)^#@@/FIREBASE_JOB@@\n", re.S | re.M)
RETIRED_WORKFLOWS = ("ship-on-version-bump.yml", "promote-to-production.yml")


def ci_config(project: Path) -> dict:
    """The project's CI facts, from the profile (defaults inferred when absent)."""
    profile = profile_of(project)
    ci = dict(profile.get("ci") or {})
    ci.setdefault("module", "composeApp" if (project / "composeApp").is_dir() else "app")
    module = ci["module"]
    if not ci.get("bundle_tasks"):
        flavors = ci.get("flavors") or []
        if flavors:
            ci["bundle_tasks"] = " ".join(f":{module}:bundle{f.title()}Release" for f in flavors)
            ci.setdefault("release_files", ",".join(
                f"{module}/build/outputs/bundle/{f}Release/{module}-{f}-release.aab" for f in flavors))
        else:
            ci["bundle_tasks"] = f":{module}:bundleRelease"
            ci.setdefault("release_files", f"{module}/build/outputs/bundle/release/{module}-release.aab")
    ci.setdefault("whatsnew_file", profile.get("whatsnew_file") or "distribution/whatsnew/whatsnew-en-US")
    ci.setdefault("whatsnew_dir", "distribution/whatsnew")
    ci.setdefault("whatsnew_limit", profile.get("whatsnew_max_chars") or 500)
    ci.setdefault("version_file", profile.get("version_file") or "app/build.gradle")
    ci.setdefault("debug_apk", f"{module}/build/outputs/apk/debug/{module}-debug.apk")
    if not ci.get("package_name"):
        ci["package_name"] = profile.get("package_name") or profile.get("application_id") or ""
    return ci


def render(project: Path) -> str:
    ci = ci_config(project)
    missing = [k for k in ("package_name", "bundle_tasks", "release_files", "version_file")
               if not ci.get(k)]
    if missing:
        raise SystemExit("cannot render CI — profile is missing: " + ", ".join(missing)
                         + f". Add them to {project}/.pipeline/profile.json under 'ci'.")
    text = TEMPLATE.read_text()
    # The QA job is optional: pull it out, then put it back only when the project
    # declared a Firebase app + tester group.
    firebase = ci.get("firebase") or {}
    block = FIREBASE_BLOCK.search(text)
    job_text = block.group(1) if block else ""
    if block:
        # Normalise the ending: removing the block leaves the blank line that
        # preceded it, which would make every project without a Firebase app
        # drift by one byte.
        text = text.replace(block.group(0), "").rstrip("\n") + "\n"
        if firebase.get("app_id") and firebase.get("groups"):
            text = text + job_text
    for key, placeholder in (
        ("bundle_tasks", "__BUNDLE_TASKS__"),
        ("release_files", "__RELEASE_FILES__"),
        ("package_name", "__PACKAGE_NAME__"),
        ("whatsnew_file", "__WHATSNEW_FILE__"),
        ("whatsnew_dir", "__WHATSNEW_DIR__"),
        ("whatsnew_limit", "__WHATSNEW_LIMIT__"),
        ("version_file", "__VERSION_FILE__"),
        ("module", "__MODULE__"),
        ("debug_apk", "__DEBUG_APK__"),
        ("firebase_app_id", "__FIREBASE_APP_ID__"),
        ("firebase_groups", "__FIREBASE_GROUPS__"),
    ):
        if key == "firebase_app_id":
            value = firebase.get("app_id") or ""
        elif key == "firebase_groups":
            value = firebase.get("groups") or ""
        else:
            value = ci[key]
        text = text.replace(placeholder, str(value))
    # The job is re-inserted BEFORE the substitutions above ran, so its own
    # placeholders are filled too. (They used to survive as literal __TOKENS__ in
    # the committed workflow — a distribution to an app id of "__FIREBASE_APP_ID__".)
    return text


def self_check() -> list[str]:
    """Problems with the TEMPLATE itself — the invariants a project copy inherits.

    doctor runs this before comparing any project, so a broken generator is
    reported once, as a generator bug, instead of as five mysterious project
    drifts. No test suite exists in this repo; this is where a template invariant
    is enforced instead of remembered.
    """
    problems: list[str] = []
    text = TEMPLATE.read_text()
    if "$((" in text:
        problems.append("the template computes a version with shell arithmetic "
                        "($((...))) — a prerelease name would break the build; read the "
                        "release commit instead")
    for token in sorted(set(re.findall(r"__[A-Z_]+__", text))):
        if token not in KNOWN_PLACEHOLDERS:
            problems.append(f"the template uses {token}, which ci_config() never fills")
    for token in KNOWN_PLACEHOLDERS:
        if token not in text:
            problems.append(f"KNOWN_PLACEHOLDERS lists {token} but the template does not use it")
    if f"TEMPLATE_VERSION {TEMPLATE_VERSION}" not in text:
        problems.append(f"the template header does not declare TEMPLATE_VERSION {TEMPLATE_VERSION}")
    if text.count("#@@FIREBASE_JOB@@") != 1 or text.count("#@@/FIREBASE_JOB@@") != 1:
        problems.append("the optional Firebase job must be wrapped in exactly one "
                        "#@@FIREBASE_JOB@@ / #@@/FIREBASE_JOB@@ pair")
    return problems


def write(project: Path, dry_run: bool = False) -> dict:
    """Render the canonical workflow, retire the old ones, fix the profile triggers."""
    profile = profile_of(project)
    ci = ci_config(project)
    target = project / WORKFLOW_REL
    text = render(project)
    changed = []
    if not target.is_file() or target.read_text() != text:
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        changed.append(WORKFLOW_REL)

    retired = []
    for name in RETIRED_WORKFLOWS:
        f = project / ".github/workflows" / name
        if f.is_file():
            retired.append(name)
            if not dry_run:
                f.unlink()

    # The workflow fires on a tag, so the profile must say so — release pushes one.
    if not dry_run:
        profile.setdefault("ci", {}).update({
            "workflow": "release.yml",
            "trigger": "tag",
            "module": ci["module"],
            "package_name": ci["package_name"],
            "bundle_tasks": ci["bundle_tasks"],
            "release_files": ci["release_files"],
        })
        profile["ci_workflow"] = "release.yml"
        profile["ci_trigger"] = "tag"
        save_json(profile_root(project) / ".pipeline/profile.json", profile)
    return {"workflow": WORKFLOW_REL, "written": bool(changed), "retired": retired,
            "trigger": "tag", "package": ci["package_name"]}
