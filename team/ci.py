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

from pathlib import Path

from .core import profile_of, save_json

TEMPLATE = Path(__file__).parent / "templates/ci-release.yml"
WORKFLOW_REL = ".github/workflows/release.yml"
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
    if not ci.get("package_name"):
        ci["package_name"] = profile.get("package_name") or profile.get("application_id") or ""
    return ci


def render(project: Path) -> str:
    ci = ci_config(project)
    missing = [k for k in ("package_name", "bundle_tasks", "release_files") if not ci.get(k)]
    if missing:
        raise SystemExit("cannot render CI — profile is missing: " + ", ".join(missing)
                         + f". Add them to {project}/.pipeline/profile.json under 'ci'.")
    text = TEMPLATE.read_text()
    for key, placeholder in (
        ("bundle_tasks", "__BUNDLE_TASKS__"),
        ("release_files", "__RELEASE_FILES__"),
        ("package_name", "__PACKAGE_NAME__"),
        ("whatsnew_file", "__WHATSNEW_FILE__"),
        ("whatsnew_dir", "__WHATSNEW_DIR__"),
        ("whatsnew_limit", "__WHATSNEW_LIMIT__"),
    ):
        text = text.replace(placeholder, str(ci[key]))
    return text


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
        save_json(project / ".pipeline/profile.json", profile)
    return {"workflow": WORKFLOW_REL, "written": bool(changed), "retired": retired,
            "trigger": "tag", "package": ci["package_name"]}
