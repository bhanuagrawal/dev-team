"""REPLAN — an out-of-band change is a fact, not a fault.

Why this exists (2026-11-09, owner directive)
---------------------------------------------
The owner edits the project between runs: a file in the checkout, a commit pushed
by hand, a design doc, a profile key, a whole screen. Until now the team only
*reported* that as drift (`status`: DIVERGED / unpushed) and left the queue
pointing at the old world — the recovery was a hand-typed sequence nobody
remembered, or a plan that quietly served a task whose target no longer exists.

So one verb does the recovery, in this order and no other:

    detect  → what changed outside the pipeline (read-only, quoted)
    adopt   → record it; NEVER revert the owner's work
    adapt   → shape the project back into the current team (deterministic)
    plan    → re-derive the queue around the new world

Nothing is deleted. Every queue verdict is recorded (`queue.judge`), every
adopted change is logged in `state['adoptions']`, and the re-plan is stamped with
the delivery sha it was built from (`plan_base_sha`) — so a plan that no longer
matches `origin/main` is visible in `status` instead of being followed.

The rule this encodes: the owner's change wins. The plan is what moves.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import (checkout_dirty, delivery_notice, git_ref_state, now_iso,
                   profile_of, save_state, state_of)


def detect(project: Path) -> list[dict[str, Any]]:
    """Everything that happened outside the pipeline, as facts to adopt."""
    from . import scan

    profile, state = profile_of(project), state_of(project)
    out: list[dict[str, Any]] = []
    ref = git_ref_state(project, ref=f"origin/{profile.get('main_branch', 'main')}")
    if ref.get("error"):
        out.append({"what": "delivery ref unreadable", "detail": ref["error"]})
    elif ref.get("ahead"):
        out.append({"what": "main moved by hand",
                    "detail": f"checkout is {ref['ahead']} commit(s) ahead of "
                              f"{ref['ref']} @ {str(ref.get('ref_sha'))[:12]} — the plan "
                              f"was built before those"})
    elif ref.get("behind") or ref.get("stale"):
        out.append({"what": "delivery is newer than the checkout",
                    "detail": f"checkout is {ref['behind']} behind "
                              f"{ref['ref']} @ {str(ref.get('ref_sha'))[:12]}"})

    dirty = checkout_dirty(project)
    if dirty:
        out.append({"what": "owner's uncommitted work",
                    "detail": f"{len(dirty)} file(s) exist only on this disk: "
                              + ", ".join(dirty[:5]) + (" …" if len(dirty) > 5 else "")})

    delivered = [q.get("label") for q in (state.get("queue") or [])
                 if scan.already_delivered(q, state)]
    if delivered:
        out.append({"what": "queued work is already delivered",
                    "detail": ", ".join(str(x) for x in delivered[:5])})

    base = str(state.get("plan_base_sha") or "")
    if base and ref.get("ref_sha") and not str(ref["ref_sha"]).startswith(base[:12]):
        out.append({"what": "plan built from an older main",
                    "detail": f"plan base {base[:12]} ≠ delivery "
                              f"{str(ref['ref_sha'])[:12]}"})
    return out


def run(project: Path, log, use_llm: bool = True, apply: bool = True) -> dict[str, Any]:
    """detect → adopt → adapt → plan, and stamp what the new plan was built from."""
    from . import adapt as adapt_mod
    from . import research as research_mod

    found = detect(project)
    if found:
        log("replan: OUTSIDE THE PIPELINE (adopted, never reverted)")
        for item in found:
            log(f"  - {item['what']}: {item['detail']}")
    else:
        log("replan: nothing changed outside the pipeline")

    state = state_of(project)
    if apply and found:
        # The adoption record is the receipt: it says the owner's change was seen
        # and the plan moved around it. It is never an undo list.
        state.setdefault("adoptions", []).extend(
            {"at": now_iso(), **item} for item in found)
        save_state(project, state)

    shaped: dict[str, Any] = {}
    if apply:
        try:
            shaped = adapt_mod.run(project, log, apply=True)
        except (Exception, SystemExit) as e:   # a broken checkout must not eat the receipt
            log(f"  adapt: could not run ({e}) — the adoption is recorded; "
                f"fix the project and re-run `devteam replan`")
    planned = False
    if apply:
        try:
            planned = research_mod.research_run(project, log, use_llm=use_llm)
        except (Exception, SystemExit) as e:   # a planner refusal is news, not a crash
            log(f"  planner: {e}")

    ref = git_ref_state(project, ref="origin/main")
    state = state_of(project)
    state["plan_base_sha"] = str(ref.get("ref_sha") or "")[:12]
    state["replanned_at"] = now_iso()
    if apply:
        save_state(project, state)

    head = ((state.get("queue") or [{}])[0] or {}).get("label") if state.get("queue") else None
    return {"found": found, "adapt": shaped, "planned": planned,
            "base": state["plan_base_sha"], "head": head, "apply": apply}


def format_result(res: dict[str, Any], project_name: str) -> str:
    lines = ["REPLAN — the owner's change wins; the plan moves",
             "=" * 66,
             f"  outside:    {len(res['found'])} change(s) adopted"
             + ("" if res["apply"] else "  (dry run — nothing written)")]
    for item in res["found"]:
        lines.append(f"    - {item['what']}: {item['detail']}")
    lines.append(f"  plan base:  {res.get('base') or '(no delivery ref — could not stamp)'}"
                 f"  {'→ the plan is now built on it' if res['apply'] else ''}")
    lines.append(f"  new head:   {res.get('head') or 'nothing waiting — the planner decides next'}")
    if not res["apply"]:
        lines.append("  NEXT:       re-run without --dry-run to adopt and replan")
    elif res.get("head"):
        lines.append(f"  NEXT:       devteam pick {project_name}   (then worktree-start)")
    else:
        lines.append(f"  NEXT:       devteam plan {project_name} --focus \"<what to build>\"")
    return "\n".join(lines)
