"""Shipper — generic. Version bump + whatsnew + commit + push origin main.
CI (owned by the project, not this team) then delivers to the Play
internal-testing track. Production promotion is human-only — this team
structurally has no path to it. The ONE owner-facing release question it raises
is "is this build a release candidate?": it is asked, recorded and shown, and
nothing in the pipeline acts on the answer (promoting stays the owner's manual
step in Play Console), so asking it never becomes a promotion request.

All project facts come from profile.json: version file, tag scheme, repo slug,
whatsnew path (and whether the owner curates those notes by hand), and the
allowed release-note length. The version bump regexes are the ONLY
project-shaped code here and they read the actual file rather than assuming its
shape.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .core import RunLog, git, profile_of, run, target_items


def _parse_version(s: str) -> tuple[int, int, int] | None:
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?$", str(s or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


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

    if profile.get("version_code_bump") == "increment":
        # Some projects encode info in the versionCode beyond the semver value —
        # a per-flavor minSdk prefix (so Play routes each device to the right
        # bundle) is one example. Overwriting those with the single semver `code`
        # would collapse two flavors onto one code, so bump EVERY versionCode
        # literal by +1 instead (profile-driven; default stays the semver
        # overwrite below).
        new_text, n_code = re.subn(
            r'(versionCode[\s\S]{0,30}?[?:=\s])(\d{4,})(\s*\)?)',
            lambda m: f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}",
            new_text)
        if n_name != 1:
            log(f"FATAL: could not bump versionName in {profile['version_file']} — refusing to ship")
            raise SystemExit("version bump failed — inspect the version file")
        if n_code:
            log(f"{bump_version_file.__name__}: bumped {n_code} versionCode(s) by +1")
        else:
            log(f"{bump_version_file.__name__}: WARNING no versionCode literal bumped "
                "(version_code_bump=increment)")
        gf.write_text(new_text)
        return name

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


# Play Console rejects a release note longer than this (per locale). It is a
# hard store limit, not a style choice — the reference CI workflow carries the
# same number as a guard step, and this is the copy the pipeline writes.
WHATS_NEW_MAX_CHARS = 500

# A "what's new" entry is a storefront blurb, not the task title: the first
# clause of the label is the user-facing gist, and the whole line stays short so
# the note has room for a second bullet inside the cap above.
_WHATS_NEW_LABEL_CHARS = 60


def whatsnew_max_chars(profile: dict) -> int:
    """The allowed length of a release note, per project (default 500)."""
    try:
        n = int(profile.get("whatsnew_max_chars") or WHATS_NEW_MAX_CHARS)
    except (TypeError, ValueError):
        n = WHATS_NEW_MAX_CHARS
    return n if n > 0 else WHATS_NEW_MAX_CHARS


def _short_label(label, limit: int = _WHATS_NEW_LABEL_CHARS) -> str:
    """An internal task label as a storefront phrase.

    Labels are written for the pipeline, not for a user: they run to 200+ chars
    and often carry the mechanism after a separator ("One result surface for all
    five jobs: a shared ResultCard + action stack showing …"). Shipping the raw
    label put that whole sentence in front of users. Take the first clause and
    cut on a word boundary.

    The label's own casing is KEPT: lowercasing the first letter to fit a
    mid-sentence slot turned "PDF-Kit: home identity" into "pDF-Kit", and a
    mangled product name in a store listing is worse than a capital letter after
    a colon. The templates below label the line instead of embedding it.
    """
    text = re.split(r"\s*[—–:|]\s*", str(label or "").strip())[0].strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = (cut or text[:limit]).rstrip(" ,;.")
    return text or "the app"


def _trim_whatsnew(text: str, max_chars: int) -> tuple[str, bool]:
    """Clamp a note to the allowed length, cutting on a line boundary when the
    note is bulleted so no bullet is lost mid-word. Returns (text, trimmed)."""
    if len(text) <= max_chars:
        return text, False
    body = text[:max_chars]
    lines = body.rstrip().splitlines()
    if len(lines) > 1:
        lines = lines[:-1] or [body]
        kept = "\n".join(lines)
        return kept + "\n", True
    cut = body.rsplit(" ", 1)[0] if " " in body else body
    return cut.rstrip() + "\n", True


def _whatsnew_text(task: dict, max_chars: int = WHATS_NEW_MAX_CHARS) -> str:
    """Honest Play release notes per task TYPE, within the store's length limit.

    The motion template used to be written for every release, so a feature or
    bugfix release claimed "No functional changes — all existing features behave
    exactly as before" (false, and exactly backwards for a behaviour change).
    A spec may supply an explicit `whatsnew` string; otherwise this per-type
    default is used. Everything is clamped to `max_chars`, because Play rejects
    an over-long note and a rejected upload is a failed release.
    """
    explicit = (task.get("whatsnew") or "").strip()
    if explicit:
        text = explicit if explicit.endswith("\n") else explicit + "\n"
    else:
        label = _short_label((task.get("surface", {}) or {}).get("label"))
        ttype = task.get("type", "motion")
        if ttype == "motion":
            text = (f"✓ Polished: {label}\n"
                    "✓ No functional changes — everything works exactly as before\n")
        elif ttype == "bugfix":
            text = (f"✓ Fixed: {label}\n"
                    "✓ Nothing else changed — everything you already use behaves the same way\n")
        else:
            # feature / anything else that changes behaviour: never claim "no changes"
            text = (f"✓ New in this update: {label}\n"
                    "✓ Everything you already use keeps working the same way\n")
    return _trim_whatsnew(text, max_chars)[0]


def write_whatsnew(worktree: Path, task, log: RunLog | None = None) -> str | None:
    """Write the release note for `task`. Accepts a task spec dict, or a bare
    label string (legacy caller shape → motion template). Returns what the file
    now holds (None when the project ships no note).

    A project whose profile sets `whatsnew_curated` owns these notes by hand: the
    pipeline READS the file for its report but never rewrites it. (A curated note
    was silently replaced by the generated boilerplate on the 2026-10-23 run —
    the owner lost their wording to a release they never asked to change it.)
    """
    if isinstance(task, str):
        task = {"type": "motion", "surface": {"label": task}}
    profile = profile_of(worktree)
    wf_rel = profile.get("whatsnew_file")
    if not wf_rel:
        return None
    wf = worktree / wf_rel
    if not wf.exists():
        return None
    if profile.get("whatsnew_curated"):
        text = wf.read_text()
        if log:
            log(f"whatsnew: '{wf_rel}' is owner-curated — kept as-is "
                f"({len(text)} chars); the pipeline does not rewrite it")
        return text
    cap = whatsnew_max_chars(profile)
    text = _whatsnew_text(task, cap)
    if log:
        raw = _whatsnew_text(task, 10 ** 9)
        if len(raw) > cap:
            log(f"whatsnew: trimmed {len(raw)} → {len(text)} chars (limit {cap})")
        else:
            log(f"whatsnew: {len(text)}/{cap} chars")
    wf.write_text(text)
    return text


def whatsnew_read(project: Path, commit: str = "") -> str:
    """The release note that actually SHIPPED for `commit` (the committed copy,
    not whatever is in a working tree), so a run report quotes the real note.
    Falls back to the project's own file, then to ''."""
    rel = profile_of(project).get("whatsnew_file")
    if not rel:
        return ""
    if commit:
        r = git(["show", f"{commit}:{rel}"], cwd=project, timeout=60)
        if r.returncode == 0:
            return r.stdout
    p = Path(project) / rel
    return p.read_text() if p.exists() else ""


def whatsnew_test_areas(task: dict, limit: int = 6) -> list[str]:
    """Human-readable places this release changed, for the run report's manual
    test list. Derived from the task's target paths — never invented: a glob
    shows as the folder it covers, a file as its own name, and the common
    directory prefix is dropped so the list reads as 'feature/split/…'.

    A spec's `targets` is sometimes a LIST holding one comma-joined string (the
    queue stores `target` that way and `pick` wraps it), so each item is split
    again on commas — a path can never contain one, but a joined list always
    does."""
    raw_items: list[str] = []
    for item in target_items((task.get("surface", {}) or {}).get("targets")):
        raw_items.extend(p.strip() for p in item.split(",") if p.strip())
    items: list[str] = []
    for raw in raw_items:
        t = raw.rstrip("/")
        if t.endswith("/**"):
            t = t[:-3]
        if t and t not in items:
            items.append(t)
    if len(items) > 1:
        prefix = os.path.commonprefix(items).rsplit("/", 1)[0]
        if prefix:
            items = [i[len(prefix) + 1:] if i.startswith(prefix + "/") else i for i in items]
    return [i[3:] if i.startswith("**/") else i for i in items][:limit]


def bump_subject(task: dict, new_version: str) -> str:
    """Commit subject for the version-bump commit, named for the task's real
    kind. It used to say "... motion" for every release, so a feature or bugfix
    release mislabelled history (`git log`) even though the Play release notes
    were already type-aware."""
    label = (task.get("surface", {}) or {}).get("label") or "app"
    kind = task.get("type", "motion")
    return f"chore: bump version to {new_version} for {str(label).lower()} {kind} (dev-team)"


def push_and_deliver(project: Path, worktree: Path, task: dict, new_version: str,
                     log: RunLog, max_retries: int = 3) -> dict:
    """Push version bump and monitor CI until success, retrying up to max_retries times.

    Each retry creates a fresh version bump commit (the previous one is already on
    main, so the next bump increments from there). CI is monitored after each
    attempt; if it fails or times out, the loop retries with a new push.
    """
    profile = profile_of(project)
    branch = profile["main_branch"]

    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            log(f"retry {attempt}/{max_retries}: re-bumping version and re-pushing")
            # Increment from the previously attempted version so each retry produces a fresh commit and version
            cur = _parse_version(new_version)
            if cur is None:
                latest = remote_latest_version(project)
                if latest is None:
                    return {"delivered": False, "error": "no remote tags for retry", "ci": {}}
                cur = latest
            next_ver = (cur[0], cur[1], cur[2] + 1)
            new_version = ".".join(map(str, next_ver))
            bump_version_file(worktree, next_ver, log)

        # An untracked file in the worktree is staged by `git add -A` and rides
        # into the release commit even though it is part of no task. A crashed or
        # interrupted developer session can leave scratch files behind (a stray
        # `.diskprobe` was found this way), and scope_check cannot catch them:
        # it blocks FROZEN paths, and a stray file is under no path at all.
        # Not a gate (generated artifacts are sometimes legitimate) — just loud.
        untracked = [f for f in git(["ls-files", "--others", "--exclude-standard"],
                                    cwd=worktree).stdout.splitlines() if f.strip()]
        if untracked:
            shown = ", ".join(untracked[:8]) + (" …" if len(untracked) > 8 else "")
            log(f"WARNING: {len(untracked)} untracked file(s) will be committed with this "
                f"change and belong to no task — check they are not scratch left by a "
                f"crashed session: {shown}")

        git(["add", "-A"], cwd=worktree)
        rc = git(["commit", "-m", bump_subject(task, new_version)], cwd=worktree)
        if rc.returncode != 0:
            return {"delivered": False, "error": "bump commit failed", "ci": {}}

        sha = git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        log(f"pushing {sha[:8]} → origin {branch} "
            "(CI → build → internal testing)")
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
        if profile.get("ci_trigger", "bump") == "tag":
            tag = f'{profile.get("tag_prefix", "v")}{new_version}'
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

        if ci.get("conclusion") == "success":
            return {"delivered": True, "commit": sha, "version": new_version, "ci": ci}

        if attempt < max_retries:
            log(f"CI {ci.get('conclusion', 'unknown')} on attempt {attempt}/{max_retries} — retrying")
        else:
            log(f"CI failed after {max_retries} attempts — giving up")

    return {"delivered": False, "error": f"CI failed after {max_retries} attempts",
            "ci": ci, "commit": sha, "version": new_version}


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
