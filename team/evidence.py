"""EVIDENCE — a shipped task must have a commit that touched what it claims.

Why this exists (2026-11-15, owner directive)
---------------------------------------------
`shipped` was a list of labels. A task entered it when a run finished, and
nothing checked that the commit it named had anything to do with the work it
described. Three times in one day that was wrong:

  * a task recorded at 1.0.61 whose acceptance said "one composer, no second
    reply sheet" — the reply sheet's files were untouched in that commit;
  * every direct offer 400ed for three releases because a migration re-created
    a function with a dropped column, and no check ever executed a write RPC;
  * a branch sat unmerged while three releases passed over it.

This is the first, and the cheapest: the commit a shipped entry names must
exist, and its diff must touch at least one path under the entry's target.
It does not prove the work is correct — it proves the entry is not a rumour.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Iterable


def target_prefix(target: str) -> str:
    """`composeApp/src/**` → `composeApp/src`; a bare path stays itself."""
    text = str(target or "").split(",")[0].strip()
    prefix = text.split("*")[0].rstrip("/")
    return prefix


def touched(files: Iterable[str], target: str) -> bool:
    """Did this diff touch anything under the entry's target?

    An entry with no target cannot be checked, so it is accepted — the defect
    this guards is a commit that plainly touches somewhere else.
    """
    prefix = target_prefix(target)
    if not prefix:
        return True
    return any(str(f).startswith(prefix) for f in files)


def shipped_without_evidence(project: Path) -> list[dict[str, Any]]:
    """Every shipped entry whose commit is missing or touched none of its target."""
    from .core import state_of

    state = state_of(Path(project))
    bad: list[dict[str, Any]] = []
    for entry in state.get("shipped") or []:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "?")
        commit = str(entry.get("commit") or "")
        target = str(entry.get("target") or "")
        if not commit:
            bad.append({"label": label, "why": "no commit recorded"})
            continue
        run = subprocess.run(
            ["git", "-C", str(project), "show", "--name-only", "--format=", commit],
            capture_output=True, text=True,
        )
        if run.returncode != 0:
            bad.append({"label": label, "why": f"commit {commit[:8]} is not in this repo"})
            continue
        files = [line for line in run.stdout.splitlines() if line.strip()]
        if not touched(files, target):
            bad.append({
                "label": label,
                "why": f"commit {commit[:8]} touched nothing under {target_prefix(target) or 'its target'}",
            })
    return bad
