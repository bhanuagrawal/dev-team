"""Shipper — generic. Version bump + whatsnew + commit + push origin main.
CI (owned by the project, not this team) then delivers to the Play
internal-testing track. Production promotion is human-only and this team
structurally has no path to it.

All project facts come from profile.json: version file, tag scheme, repo slug,
whatsnew path. The version bump regexes are the ONLY project-shaped code here
and they read the actual file rather than assuming its shape.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .core import RunLog, git, profile_of, run


def remote_latest_version(project: Path) -> tuple[int, int, int] | None:
    prefix = profile_of(project).get("tag_prefix", "v")
    r = git(["ls-remote", "--tags", "origin", f"refs/tags/{prefix}*"],
            cwd=project, timeout=60)
    if r.returncode != 0:
        return None
    # patch segment optional: v1.5 counts as (1,5,0)
    rx = rf"refs/tags/{re.escape(prefix)}(\d+)\.(\d+)(?:\.(\d+))?$"
    versions = [(int(a), int(b), int(c or 0)) for a, b, c in re.findall(rx, r.stdout, re.M)]
    return max(versions) if versions else None


def bump_version_file(worktree: Path, new: tuple[int, int, int], log: RunLog) -> str:
    profile = profile_of(worktree)
    gf = worktree / profile["version_file"]
    text = gf.read_text()
    name = ".".join(map(str, new))
    code = new[0] * 10000 + new[1] * 100 + new[2]

    # Generic: bump whichever versionName/versionCode constants exist in the file
    # (Android Gradle convention used by this project; CI parses them back).
    # versionName may be 1-3 segments (some projects use "1.4", most "1.2.3");
    # always write major.minor.patch — CI computes the code from the same scheme.
    new_text, n_name = re.subn(
        r'(versionName[\s\S]{0,70}?["\'])(\d+(?:\.\d+){0,2})(["\'])',
        rf"\g<1>{name}\g<3>", text, count=1)

    new_text, n_code = re.subn(
        r'(versionCode[\s\S]{0,70}?[?:=\s])(\d{4,})(\s*\)?)',
        rf"\g<1>{code}\g<3>", new_text, count=1)
    if n_name != 1:
        log(f"FATAL: could not bump versionName in {profile['version_file']} — refusing to ship")
        raise SystemExit("version bump failed — inspect the version file")
    if n_code == 1:
        log(bump_version_file.__name__ + ": also bumped versionCode")
    gf.write_text(new_text)
    return name


def write_whatsnew(worktree: Path, label: str) -> None:
    profile = profile_of(worktree)
    wf_rel = profile.get("whatsnew_file")
    if not wf_rel:
        return
    wf = worktree / wf_rel
    if wf.exists():
        wf.write_text(
            f"✓ Motion polish on the {label.lower()} — smoother transitions and clearer "
            "feedback on actions, matching the recent design pass\n"
            "✓ No functional changes — all existing features behave exactly as before\n")


def push_and_deliver(project: Path, worktree: Path, task: dict, new_version: str,
                     log: RunLog) -> dict:
    profile = profile_of(project)
    label = task["surface"]["label"]
    git(["add", "-A"], cwd=worktree)
    rc = git(["commit", "-m",
              f"chore: bump version to {new_version} for {label.lower()} motion (dev-team)"],
             cwd=worktree)
    if rc.returncode != 0:
        return {"delivered": False, "error": "bump commit failed", "ci": {}}

    sha = git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
    log(f"pushing {sha[:8]} → origin {profile['main_branch']} "
        "(CI → build → internal testing)")
    branch = profile["main_branch"]
    r = git(["push", "origin", f"HEAD:{branch}"], cwd=worktree, timeout=180)
    if r.returncode != 0:
        log("push rejected — remote moved since base; rebasing")
        git(["fetch", "origin"], cwd=project, timeout=120)
        rb = git(["rebase", f"origin/{branch}"], cwd=worktree, timeout=180)
        if rb.returncode != 0:
            git(["rebase", "--abort"], cwd=worktree)
            return {"delivered": False, "error": "push rejected and rebase failed", "ci": {}}
        r = git(["push", "origin", f"HEAD:{branch}"], cwd=worktree, timeout=180)
        if r.returncode != 0:
            return {"delivered": False, "error": "push failed twice", "ci": {}}
    log("pushed to origin main")
    # Tag-gated CI: some projects release on `v*` tags only — the bump push
    # alone would never deliver. Push the version tag when profile says so.
    if profile_of(project).get("ci_trigger", "bump") == "tag":
        tag = f'{profile_of(project).get("tag_prefix", "v")}{new_version}'
        r = git(["tag", tag], cwd=worktree)
        if r.returncode != 0:
            log(f"tag {tag} not created locally (may already exist)")
        else:
            r = git(["push", "origin", tag], cwd=worktree, timeout=120)
            if r.returncode == 0:
                log(f"pushed tag {tag} → CI (tag-gated delivery)")
            else:
                log(f"tag push failed: {(r.stdout + r.stderr)[-200:]}")
    ci = await_ci(project, sha, log)

    return {"delivered": True, "commit": sha, "version": new_version, "ci": ci}


def await_ci(project: Path, sha: str, log: RunLog, max_wait_s: int = 1800) -> dict:
    profile = profile_of(project)
    slug = profile["repo_slug"]
    wf = profile["ci_workflow"]
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        r = run(["gh", "run", "list", "-R", slug, "--workflow", wf, "-L", "5",
                 "--json", "status,conclusion,headSha"], project, timeout=60)
        if r.returncode == 0:
            import json
            for e in json.loads(r.stdout or "[]"):
                if e.get("headSha") == sha and e["status"] == "completed":
                    log(f"CI {sha[:8]}: {e['conclusion']} — internal testing "
                        + ("updated" if e["conclusion"] == "success" else "NOT updated"))
                    return {"status": "completed", "conclusion": e["conclusion"]}
        time.sleep(60)
    log("CI still running — alarm will reconcile later")
    return {"status": "pending"}
