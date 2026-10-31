"""The takeover brief: the machine states where a run stands, from state alone.

Why this exists
---------------
Resuming work used to require reading several hundred lines of procedure to work
out what is true *right now*: which ref the code should be read from, whether the
checkout is dirty in a way that matters, which worktrees are live or parked, what
the queue head is, what the last run actually did, and which owner asks are still
open. Every one of those facts is already in `.pipeline/state.json`, git and the
worktree registry — the prose was doing the job of a join.

This module is that join. It answers with data, and it ends in ONE next command, so
the agent that takes over executes rather than interprets. Nothing here decides
policy: it reports, and the command it prints still goes through the same gates.

Rule of thumb this encodes: if a fact can be read, the docs must not restate it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _last(items: list[dict] | None, n: int = 1) -> list[dict]:
    return (items or [])[-n:]


def brief(project: Path) -> dict[str, Any]:
    from . import worktrees
    from .core import checkout_dirty, git_ref_state, profile_of, state_of

    profile = profile_of(project)
    state = state_of(project)

    delivery = git_ref_state(project)
    dirty = checkout_dirty(project)
    try:
        trees = worktrees.inventory(project)
    except Exception:
        trees = []
    parked = worktrees.parked(project)

    runs = state.get("runs") or []
    last_run = runs[-1] if runs else {}
    queue = state.get("queue") or []
    head = queue[0] if queue else {}

    # An owner ask is OPEN only when the LAST thing recorded for that task is a
    # blocked run. A later run on the same task (answered, shipped, abandoned)
    # closes it — so the list is per-task, not per-blocked-record.
    last_by_task: dict[str, dict] = {}
    for r in runs:
        if r.get("task_id"):
            last_by_task[str(r["task_id"])] = r
    open_asks = [r for r in last_by_task.values() if str(r.get("result")) == "blocked"][-3:]

    plan_review = state.get("plan_review") or {}
    shipped = _last(state.get("shipped"), 1)

    # The next command is derived, not remembered: resume a parked attempt, or
    # start the queue head, or plan (an empty queue is the planner's job).
    if parked:
        nxt = (f"devteam worktree-start {project.name} --task-id {parked[-1].get('task_id')} "
               f"   # resume parked branch {parked[-1].get('branch')}")
    elif head:
        nxt = (f"devteam pick {project.name}   →   devteam worktree-start {project.name} "
               f"--task-id <the id pick prints>")
    else:
        nxt = f"devteam plan {project.name}"

    return {
        "project": project.name,
        "delivery": {
            "ref": delivery.get("ref"),
            "sha": delivery.get("ref_sha"),
            "checkout_ahead": delivery.get("ahead"),
            "checkout_behind": delivery.get("behind"),
            "stale": delivery.get("stale"),
            "read_code_from": delivery.get("ref"),
        },
        "checkout": {"dirty_files": dirty, "clean": not dirty},
        "worktrees": {"count": len(trees), "trees": trees},
        "parked": parked,
        "queue": {"size": len(queue), "head": {"label": head.get("label"), "type": head.get("type")}},
        "last_run": last_run,
        "last_shipped": shipped[0] if shipped else {},
        "open_asks": [{"task_id": r.get("task_id"), "note": r.get("note")} for r in open_asks],
        "plan_review": plan_review,
        "next_command": nxt,
        "goal": (profile.get("goals") or {}).get("vision", ""),
    }


def format_brief(b: dict[str, Any]) -> str:
    d = b["delivery"]
    lines = [f"TAKEOVER — {b['project']}"]
    stale = "  ⚠ checkout is behind/diverged — read code from the ref, not the tree" if d.get("stale") else ""
    lines.append(f"  delivery:  {d['ref']} @ {str(d['sha'])[:12]} "
                 f"(checkout {d['checkout_ahead']} ahead / {d['checkout_behind']} behind){stale}")
    dirty = b["checkout"]["dirty_files"]
    lines.append("  checkout:  clean" if not dirty
                 else f"  checkout:  {len(dirty)} uncommitted file(s) — {', '.join(dirty[:4])}"
                      + (" …" if len(dirty) > 4 else ""))
    wt = b["worktrees"]
    lines.append(f"  worktrees: {wt.get('count', 0)} — " +
                 (", ".join(f"{w.get('task_id')}{'(locked)' if w.get('locked') else ''}"
                            for w in (wt.get('trees') or [])[:4]) or "none active"))
    if b["parked"]:
        lines.append("  parked:    " + ", ".join(
            f"{p.get('task_id')} ({p.get('branch')})" for p in b["parked"][:4]))
    q = b["queue"]
    lines.append(f"  queue:     {q['size']} waiting" +
                 (f" — head: {q['head']['label']}" if q["head"].get("label") else " (empty)"))
    lr = b["last_run"]
    if lr:
        lines.append(f"  last run:  {lr.get('date')} {lr.get('result')} — {str(lr.get('note') or '')[:110]}")
    ls = b["last_shipped"]
    if ls:
        lines.append(f"  shipped:   v{ls.get('version')} — {str(ls.get('label') or '')[:90]}")
    if b["open_asks"]:
        lines.append("  OWNER ASKS OPEN:")
        for ask in b["open_asks"]:
            lines.append(f"    ? {ask['task_id']}: {str(ask['note'] or '')[:120]}")
    pr = b["plan_review"] or {}
    if pr.get("date"):
        lines.append(f"  plan:      last reviewed {pr.get('date')}")
    lines.append("")
    lines.append(f"  NEXT:      {b['next_command']}")
    return "\n".join(lines)
