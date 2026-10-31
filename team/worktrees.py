"""Worktree lifecycle — a local tree is a CACHE, never the only copy.

Every task runs in its own worktree on branch `dt/<task-id>`, anchored at
origin/main, and `release` pushes that branch straight onto main. Nothing ever
removed the tree afterwards, so completed runs accumulated: 20 merged trees and
3.5 GB in one project (2026-10-30), each ~230 MB because it owns a Gradle build.

A stale tree that looks like the project is not just disk — it is a trap. Two
wrong answers in one session came from reading one: a brand audit that reported
the checkout's palette instead of the shipped one, and store screenshots rendered
from code Play never received.

WHY THE OLD RULE COULD NEVER RECLAIM ANYTHING
The previous rule kept every unmerged tree "because it is the only evidence of
what was attempted". That is a storage problem wearing a safety rule: a blocked
or abandoned session (which never reaches `release` and never reaches `record`)
left a tree that no later run could ever delete. The invariant that fixes it:

    a worktree is disposable the moment its branch exists at origin.

So the branch is pushed at CREATION, not at release, and a tree may be removed
whenever one of these holds:

  * its HEAD is an ancestor of origin/main (the work actually landed), or
  * its branch is at origin and contains its HEAD (the attempt is parked there).

Anything else is parked first — commit the working tree onto `dt/<task-id>` and
push it — and only removed once the park succeeded. A tree is never removed
while a park is the only thing that would preserve the work, and never while its
own run looks alive (locked + touched inside the settle window).

Parked branches are therefore the record, and they are cheap (refs, not 230 MB).
They replace `git branch -D` on unmerged work, and `parked()` makes them visible
to `status` so they cannot rot silently.

Zero project names — generic across all projects.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .core import git

RUN_BASE = ".pipeline/worktrees"
KEEP_DIRS = {".publish"}  # long-lived by design
PARK_NAMESPACE = "dt/parked/"
MAIN_REF = "origin/main"
DEFAULT_SETTLE_MIN = 30   # a tree touched this recently may have a live run
TREE_CAP = 3              # more dev trees than this per project is a smell


def _settle_seconds() -> int:
    raw = os.environ.get("DEVTEAM_WORKTREE_SETTLE_MIN", "")
    try:
        return max(0, int(raw)) * 60
    except ValueError:
        return DEFAULT_SETTLE_MIN * 60


def _size_kb(path: Path) -> int:
    try:
        r = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True, timeout=600)
        return int(r.stdout.split()[0]) if r.returncode == 0 and r.stdout.strip() else 0
    except Exception:
        return 0


def _dirty(tree: Path) -> bool:
    r = git(["status", "--porcelain"], cwd=tree, timeout=60)
    return bool(r.returncode == 0 and r.stdout.strip())


def _head(tree: Path) -> str | None:
    r = git(["rev-parse", "HEAD"], cwd=tree, timeout=30)
    return r.stdout.strip() if r.returncode == 0 else None


def _branch(tree: Path) -> str | None:
    r = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tree, timeout=30)
    if r.returncode != 0:
        return None
    name = r.stdout.strip()
    return name if name and name != "HEAD" else None


def _contains(project: Path, ancestor: str, ref: str) -> bool:
    """Is `ancestor` already inside `ref`? (i.e. nothing would be lost)"""
    if not ancestor:
        return False
    r = git(["merge-base", "--is-ancestor", ancestor, ref], cwd=project, timeout=60)
    return r.returncode == 0


def _merged(project: Path, sha: str) -> bool:
    return _contains(project, sha, MAIN_REF)


def _remote_sha(project: Path, branch: str | None) -> str | None:
    if not branch:
        return None
    r = git(["rev-parse", "--verify", "--quiet", f"origin/{branch}"], cwd=project, timeout=30)
    return r.stdout.strip() or None if r.returncode == 0 else None


def _last_activity(tree: Path) -> float:
    """Newest mtime that a live session would keep touching.

    The worktree's git index is the useful one: any `git status/add/commit` from
    an agent refreshes it, and a dead session stops refreshing it. Falls back to
    the directory itself.
    """
    newest = 0.0
    try:
        r = git(["rev-parse", "--git-path", "index"], cwd=tree, timeout=30)
        if r.returncode == 0:
            idx = Path(r.stdout.strip())
            if not idx.is_absolute():
                idx = tree / idx
            if idx.exists():
                newest = idx.stat().st_mtime
    except Exception:
        pass
    try:
        newest = max(newest, tree.stat().st_mtime)
    except OSError:
        pass
    return newest


def _locks(project: Path) -> dict[str, str]:
    """`git worktree list --porcelain` → {path: lock_reason} for locked trees.

    `worktree-start` locks a tree for the life of its run, so "is this tree
    abandoned?" has an answer that does not depend on a human closing a session.
    """
    r = git(["worktree", "list", "--porcelain"], cwd=project, timeout=60)
    if r.returncode != 0:
        return {}
    out: dict[str, str] = {}
    path = ""
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
        elif line.startswith("locked"):
            out[path] = line[len("locked"):].strip() or "locked"
    return out


def _commit_all(tree: Path, message: str) -> tuple[bool, str]:
    git(["add", "-A"], cwd=tree, timeout=120)
    r = git(["commit", "-m", message], cwd=tree, timeout=120)
    if r.returncode == 0:
        return True, ""
    out = (r.stdout or "") + (r.stderr or "")
    if "nothing to commit" in out or "no changes added" in out:
        return True, ""
    return False, out.strip()[-300:]


def _push_branch(project: Path, tree: Path, branch: str) -> tuple[bool, str]:
    r = git(["push", "origin", f"HEAD:refs/heads/{branch}"], cwd=tree, timeout=180)
    if r.returncode == 0:
        return True, ""
    return False, ((r.stdout or "") + (r.stderr or "")).strip()[-300:]


def park(project: Path, tree: Path, branch: str, log=None) -> dict[str, Any]:
    """Make the attempt survive without the tree: commit, then push it.

    Order matters. A committed-but-unpushed tree is still the only copy, so the
    tree must not be deleted unless the push succeeded.
    """
    say = log or (lambda *_: None)
    ok, why = _commit_all(tree, f"wip({branch}): attempt parked when the run "
                                f"ended without a release")
    if not ok:
        return {"ok": False, "why": f"could not commit the working tree: {why}"}
    sha = _head(tree) or ""
    ok, why = _push_branch(project, tree, branch)
    if not ok:
        return {"ok": False, "why": f"could not push {branch}: {why}", "sha": sha}
    say(f"  ↑ parked {branch} at origin @ {sha[:8]}")
    return {"ok": True, "sha": sha, "branch": branch}


def inventory(project: Path) -> list[dict[str, Any]]:
    """Every task worktree with the facts the prune decision needs."""
    root = project / RUN_BASE
    if not root.is_dir():
        return []
    git(["fetch", "origin"], cwd=project, timeout=120)
    locks = _locks(project)
    settle = _settle_seconds()
    now = time.time()
    out: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name in KEEP_DIRS:
            continue
        sha = _head(entry)
        branch = _branch(entry)
        dirty = _dirty(entry) if sha else False
        merged = bool(sha) and _merged(project, sha or "")
        remote = _remote_sha(project, branch)
        # parked == the branch at origin already holds everything in this tree
        parked = bool(remote) and _contains(project, sha or "", f"origin/{branch}")
        idle_min = int(max(0, now - _last_activity(entry)) // 60)
        locked = entry.resolve().as_posix() in locks or str(entry) in locks
        live = locked and idle_min * 60 < settle
        keep_branch = False

        if live:
            decision = "keep"
            reason = f"looks live — locked, touched {idle_min}m ago (settle {settle // 60}m)"
        elif branch is None:
            decision = "remove" if merged and not dirty else "keep"
            reason = ("detached at origin/main, clean" if decision == "remove"
                      else "detached with no branch to park the work on")
        elif merged and not dirty:
            decision, reason = "remove", "merged into origin/main, clean"
        elif merged and dirty:
            decision = "park-and-remove"
            reason = "merged, but has uncommitted work — committed to its branch first"
            keep_branch = True
        elif parked:
            decision, reason = "remove", f"parked at origin {branch} @ {(remote or '')[:8]}"
            keep_branch = True
        else:
            decision = "park-and-remove"
            reason = "not merged — commit + push its branch, then drop the tree"
            keep_branch = True

        if locked:
            reason += " · stale run lock" if not live else ""

        out.append({
            "task": entry.name, "path": str(entry), "branch": branch, "sha": (sha or "")[:12],
            "merged": merged, "dirty": dirty, "pushed": parked, "locked": locked,
            "idle_min": idle_min, "decision": decision, "reason": reason,
            "keep_branch": keep_branch, "size_kb": _size_kb(entry),
        })
    return out


def _gc_branches(project: Path, apply: bool, log=None) -> list[str]:
    """Merge-landed `dt/*` branches whose tree is gone: refs nobody needs.

    Unmerged (parked) branches are NEVER deleted here — they are the record.
    Parked branches are named by `parked()` instead, so the owner can see them.
    """
    say = log or (lambda *_: None)
    r = git(["for-each-ref", "--format=%(refname:short) %(objectname)",
             "refs/heads/dt/"], cwd=project, timeout=60)
    if r.returncode != 0:
        return []
    checked_out = set()
    wl = git(["worktree", "list", "--porcelain"], cwd=project, timeout=60)
    for line in wl.stdout.splitlines():
        if line.startswith("branch refs/heads/"):
            checked_out.add(line.split("refs/heads/", 1)[1].strip())
    gone: list[str] = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        name, sha = parts
        if name in checked_out:
            continue
        if not _merged(project, sha):
            continue
        gone.append(name)
        if apply:
            git(["branch", "-D", name], cwd=project, timeout=60)
            say(f"  - branch {name} (merged, tree gone)")
    return gone


def prune(project: Path, apply: bool = False, force: bool = False, log=None) -> dict[str, Any]:
    """Remove task worktrees whose work is safe somewhere else. Never guesses.

    `force` means "remove even when the work cannot be parked" — it is the only
    way to drop uncommitted work, and it is never implied.
    """
    trees = inventory(project)
    removable = [t for t in trees if t["decision"] == "remove"]
    parkable = [t for t in trees if t["decision"] == "park-and-remove"]
    kept = [t for t in trees if t["decision"] == "keep"]
    candidates = removable + parkable
    freed_kb = sum(t["size_kb"] for t in candidates)

    if log:
        for t in removable:
            log(f"  {'- ' if apply else '~ '}{t['task']}  "
                f"({round(t['size_kb'] / 1024)} MB, {t['reason']})")
        for t in parkable:
            log(f"  {'- ' if apply else '~ '}{t['task']}  "
                f"({round(t['size_kb'] / 1024)} MB, {t['reason']})")
        for t in kept:
            log(f"  = kept {t['task']}  ({t['reason']})")

    removed: list[str] = []
    parked: list[str] = []
    failed: list[dict[str, str]] = []
    if apply:
        for t in candidates:
            path = Path(t["path"])
            if t["decision"] == "park-and-remove" and not force:
                res = park(project, path, t["branch"] or "", log)
                if not res.get("ok"):
                    failed.append({"task": t["task"], "why": res.get("why") or "park failed"})
                    if log:
                        log(f"  ! kept {t['task']} — {res.get('why')}")
                    continue
                parked.append(t["task"])
            if t.get("locked"):
                git(["worktree", "unlock", str(path)], cwd=project, timeout=60)
            r = git(["worktree", "remove", "--force", str(path)], cwd=project, timeout=300)
            if r.returncode != 0:
                # A wedged directory is not worth failing a run over: report it.
                if log:
                    log(f"  ! could not remove {t['task']}: {(r.stderr or r.stdout).strip()[-160:]}")
                continue
            if t["branch"] and t["branch"].startswith("dt/") and not t.get("keep_branch"):
                git(["branch", "-D", t["branch"]], cwd=project, timeout=60)
            removed.append(t["task"])
        git(["worktree", "prune"], cwd=project, timeout=120)
        _gc_branches(project, apply=True, log=log)
        # `shutil` is only used to drop an empty container; the trees are gone.
        root = project / RUN_BASE
        if root.is_dir() and not any(p for p in root.iterdir() if p.is_dir() and p.name not in KEEP_DIRS):
            shutil.rmtree(root / "__empty__", ignore_errors=True)
    else:
        _gc_branches(project, apply=False, log=None)

    return {
        "applied": apply,
        "removable": [t["task"] for t in candidates],
        "kept": [{"task": t["task"], "reason": t["reason"]} for t in kept],
        "removed": removed,
        "parked": parked,
        "failed": failed,
        "reclaimable_mb": round(freed_kb / 1024),
    }


def parked(project: Path) -> list[dict[str, Any]]:
    """Attempts that exist only as a branch: shipped nowhere, deleted nowhere.

    This is the replacement for "keep the 230 MB tree just in case" — the work
    is here, and `status` shows it so an abandoned session is visible to the
    next one instead of hidden in a directory tree nobody lists. Branches that
    are checked out in a live tree are excluded: those are running, not parked.
    """
    r = git(["for-each-ref", "--format=%(refname:short) %(objectname) %(committerdate:unix)",
             "refs/heads/dt/"], cwd=project, timeout=60)
    if r.returncode != 0:
        return []
    checked_out: set[str] = set()
    wl = git(["worktree", "list", "--porcelain"], cwd=project, timeout=60)
    for line in wl.stdout.splitlines():
        if line.startswith("branch refs/heads/"):
            checked_out.add(line.split("refs/heads/", 1)[1].strip())
    now = time.time()
    out: list[dict[str, Any]] = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        name, sha, when = parts
        if name in checked_out or _merged(project, sha):
            continue
        try:
            age_days = int((now - float(when)) // 86400)
        except ValueError:
            age_days = 0
        remote = _remote_sha(project, name)
        out.append({
            "branch": name, "sha": sha[:8], "age_days": age_days,
            "at_origin": bool(remote) and _contains(project, sha, f"origin/{name}"),
        })
    return out


def summary(project: Path) -> dict[str, Any]:
    """One cheap block for `status`/`doctor`: trees on disk + parked attempts.

    Deliberately does NOT call `inventory()`: `status` runs on every request and
    a `git status` per 230 MB tree is not worth it for a count and a size.
    """
    root = project / RUN_BASE
    trees = [d for d in root.iterdir() if d.is_dir() and d.name not in KEEP_DIRS] \
        if root.is_dir() else []
    return {
        "count": len(trees),
        "mb": round(sum(_size_kb(t) for t in trees) / 1024),
        "over_cap": len(trees) > TREE_CAP,
        "parked": parked(project),
    }


def resolve_attempt(project: Path, task_id: str, base: str, log=None) -> str | None:
    """Keep an older attempt on the same branch id from being overwritten.

    `worktree-start` reuses `dt/<task-id>` for a re-run. If origin still holds a
    previous attempt there (a blocked session that died before release), the new
    empty branch would force-push it out of existence. Move it to
    `dt/parked/<task>-<sha>` first, and return the reason if that failed.
    """
    say = log or (lambda *_: None)
    branch = f"dt/{task_id}"
    r = git(["rev-parse", "--verify", "--quiet", f"origin/{branch}"], cwd=project, timeout=30)
    if r.returncode != 0:
        return None
    sha = r.stdout.strip()
    if not sha or _contains(project, sha, base):
        return None                      # nothing unique — safe to reuse the name
    parked_ref = f"{PARK_NAMESPACE}{task_id}-{sha[:7]}"
    p = git(["push", "origin", f"{sha}:refs/heads/{parked_ref}"], cwd=project, timeout=180)
    if p.returncode != 0:
        return (f"could not preserve the previous attempt {sha[:7]} as {parked_ref}: "
                + ((p.stdout or "") + (p.stderr or "")).strip()[-200:])
    git(["branch", "-f", parked_ref, sha], cwd=project, timeout=60)
    say(f"worktree: previous attempt on {branch} preserved at origin {parked_ref}")
    d = git(["push", "origin", f":refs/heads/{branch}"], cwd=project, timeout=180)
    if d.returncode != 0:
        return (f"preserved {parked_ref}, but could not free {branch}: "
                + ((d.stdout or "") + (d.stderr or "")).strip()[-200:])
    return None


def start_attempt(project: Path, tree: Path, task_id: str, log=None) -> dict[str, Any]:
    """Durability at CREATION: lock the tree for its run, push its branch.

    After this returns, the tree may be deleted at any moment without losing
    anything — which is what makes cleanup safe instead of an act of faith.
    """
    say = log or (lambda *_: None)
    branch = f"dt/{task_id}"
    lock = git(["worktree", "lock", "--reason", f"devteam run {task_id}", str(tree)],
               cwd=project, timeout=60)
    if lock.returncode != 0:
        say(f"worktree: could not lock {tree.name} (a stale run lock is reclaimed by prune)")
    p = git(["push", "-u", "origin", branch], cwd=tree, timeout=180)
    if p.returncode != 0:
        why = ((p.stdout or "") + (p.stderr or "")).strip()[-300:]
        say(f"worktree: ! could not push {branch} — this tree is the only copy "
            f"until it is pushed: {why}")
        return {"branch": branch, "pushed": False, "why": why}
    say(f"worktree: {branch} exists at origin (the tree is now disposable)")
    return {"branch": branch, "pushed": True, "why": ""}
