"""Worktree lifecycle — create is per-task, so prune must be too.

Every task runs in its own worktree on branch `dt/<task-id>`, anchored at
origin/main, and `release` pushes that branch straight onto main. Nothing ever
removed the tree afterwards, so completed runs accumulated: 20 merged trees and
3.5 GB in one project (2026-10-30), each ~230 MB because it owns a Gradle build.

A stale tree that looks like the project is not just disk — it is a trap. Two
wrong answers in one session came from reading one: a brand audit that reported
the checkout's palette instead of the shipped one, and store screenshots rendered
from code Play never received.

So the rule is narrow and checkable: a tree may be removed only when its HEAD is
an ancestor of origin/main (its commits actually landed) AND it has no
uncommitted work (unless --force). Unmerged trees are kept and named, because a
failed or blocked task's tree is the only evidence of what was attempted.

Zero project names — generic across all projects.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from .core import git

RUN_BASE = ".pipeline/worktrees"
KEEP_DIRS = {".publish"}  # long-lived by design


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


def _merged(project: Path, sha: str) -> bool:
    r = git(["merge-base", "--is-ancestor", sha, "origin/main"], cwd=project, timeout=60)
    return r.returncode == 0


def inventory(project: Path) -> list[dict[str, Any]]:
    """Every task worktree with the facts the prune decision needs."""
    root = project / RUN_BASE
    if not root.is_dir():
        return []
    git(["fetch", "origin"], cwd=project, timeout=120)
    out: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name in KEEP_DIRS:
            continue
        sha = _head(entry)
        branch = _branch(entry)
        dirty = _dirty(entry) if sha else False
        merged = bool(sha) and _merged(project, sha or "")
        if merged and not dirty:
            decision, reason = "remove", "merged into origin/main, clean"
        elif merged and dirty:
            decision, reason = "keep", "merged, but has uncommitted work (use --force)"
        else:
            decision, reason = "keep", "NOT merged into origin/main — evidence of an unshipped attempt"
        out.append({
            "task": entry.name, "path": str(entry), "branch": branch, "sha": (sha or "")[:12],
            "merged": merged, "dirty": dirty, "decision": decision, "reason": reason,
            "size_kb": _size_kb(entry),
        })
    return out


def prune(project: Path, apply: bool = False, force: bool = False, log=None) -> dict[str, Any]:
    """Remove task worktrees whose work already landed. Never guesses."""
    trees = inventory(project)
    removable = [t for t in trees if t["decision"] == "remove" or (force and t["merged"])]
    kept = [t for t in trees if t not in removable]
    freed_kb = sum(t["size_kb"] for t in removable)

    if log:
        for t in removable:
            log(f"  {'- ' if apply else '~ '}{t['task']}  ({round(t['size_kb'] / 1024)} MB, {t['reason']})")
        for t in kept:
            log(f"  = kept {t['task']}  ({t['reason']})")

    removed: list[str] = []
    if apply:
        for t in removable:
            path = Path(t["path"])
            r = git(["worktree", "remove", "--force", str(path)], cwd=project, timeout=300)
            if r.returncode != 0:
                # A wedged directory is not worth failing a run over: report it.
                if log:
                    log(f"  ! could not remove {t['task']}: {(r.stderr or r.stdout).strip()[-160:]}")
                continue
            if t["branch"] and t["branch"].startswith("dt/"):
                git(["branch", "-D", t["branch"]], cwd=project, timeout=60)
            removed.append(t["task"])
        git(["worktree", "prune"], cwd=project, timeout=120)
        # `shutil` is only used to drop an empty container; the trees are gone.
        root = project / RUN_BASE
        if root.is_dir() and not any(p for p in root.iterdir() if p.is_dir() and p.name not in KEEP_DIRS):
            shutil.rmtree(root / "__empty__", ignore_errors=True)

    return {
        "applied": apply,
        "removable": [t["task"] for t in removable],
        "kept": [{"task": t["task"], "reason": t["reason"]} for t in kept],
        "removed": removed,
        "reclaimable_mb": round(freed_kb / 1024),
    }
