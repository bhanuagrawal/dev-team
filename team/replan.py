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

A sha is a receipt, not a reason. "Main moved" only says the plan is old; it does
not say WHAT the planner must reconcile, and a replan that is told nothing but a
hash re-derives the same queue against a tree it cannot see the changes in. So the
drift is quoted as the last few COMMITS since the plan base (`recent_commits`), the
planner receives that same list as `signals.repo.commits.since_plan_base`, and the
result names the commits the new plan was built around.

The rule this encodes: the owner's change wins. The plan is what moves.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import (checkout_dirty, commit_summary, delivery_notice, git_ref_state,
                   now_iso, profile_of, recent_commits, save_state, state_of)

# How many commits one drift line names. Three fits a terminal line beside the
# sha it explains; the planner sees the longer list, `status` does not need it.
DRIFT_COMMITS = 3


def detect(project: Path) -> list[dict[str, Any]]:
    """Everything that happened outside the pipeline, as facts to adopt."""
    from . import scan

    profile, state = profile_of(project), state_of(project)
    out: list[dict[str, Any]] = []
    ref = git_ref_state(project, ref=f"origin/{profile.get('main_branch', 'main')}")
    if ref.get("error"):
        out.append({"what": "delivery ref unreadable", "detail": ref["error"]})
    elif ref.get("ahead"):
        # The commits exist on this disk only — name them, or the adoption record
        # says "something moved" and the owner has to run git to find out what.
        brought = recent_commits(project, ref="HEAD", since=ref.get("ref_sha"),
                                 limit=DRIFT_COMMITS)
        out.append({"what": "main moved by hand",
                    "detail": f"checkout is {ref['ahead']} commit(s) ahead of "
                              f"{ref['ref']} @ {str(ref.get('ref_sha'))[:12]} — the plan "
                              f"was built before those"
                              + (f": {commit_summary(brought)}" if brought else "")})
    elif ref.get("behind") or ref.get("stale"):
        delivered = recent_commits(project, ref=ref["ref"], since=ref.get("head"),
                                   limit=DRIFT_COMMITS)
        out.append({"what": "delivery is newer than the checkout",
                    "detail": f"checkout is {ref['behind']} behind "
                              f"{ref['ref']} @ {str(ref.get('ref_sha'))[:12]}"
                              + (f": {commit_summary(delivered)}" if delivered else "")})

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
        # The plan's OWN commit list, not the last few on main generally: these are
        # the commits that landed after the queue was derived, so these are the
        # ones it has not accounted for.
        missed = recent_commits(project, ref=ref["ref"], since=base,
                                limit=DRIFT_COMMITS)
        out.append({"what": "plan built from an older main",
                    "detail": f"plan base {base[:12]} ≠ delivery "
                              f"{str(ref['ref_sha'])[:12]}"
                              + (f" — the plan has not seen: {commit_summary(missed)}"
                                 if missed else "")})

    # A team change to the plan FORMAT (2026-11-09: a task is a mini-PRD — problem,
    # done_when, user_sees, not_in_scope) reaches a project on its next plan
    # review, because old entries are grandfathered so a rule cannot stall a run
    # it did not plan. That is correct but invisible: the owner cannot see which
    # apps still run the older shape. This check is the visible half.
    legacy = [q for q in (state.get("queue") or [])
              if q.get("type") in ("feature", "bugfix")
              and not (q.get("problem") and q.get("done_when"))]
    if legacy:
        out.append({"what": "plan on the older format",
                    "detail": f"{len(legacy)} task(s) carry no problem/done_when "
                              f"(e.g. {legacy[0].get('label')}) — grandfathered, so they "
                              f"still run; the next plan review rewrites them, or run "
                              f"`devteam replan` now"})
    return out


def run(project: Path, log, use_llm: bool = True, apply: bool = True) -> dict[str, Any]:
    """detect → adopt → adapt → plan, and stamp what the new plan was built from."""
    from . import adapt as adapt_mod
    from . import research as research_mod

    prior_base = str(state_of(project).get("plan_base_sha") or "")
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
    # The commits the NEW plan was written around. `since=prior_base` is the honest
    # question: what landed after the queue the planner was asked to maintain? With
    # no prior stamp it falls back to the last few on the delivery ref, so the
    # receipt is never empty just because a project never stamped a base.
    landed = recent_commits(project, ref=ref.get("ref") or "origin/main",
                            since=prior_base or None, limit=6)
    state = state_of(project)
    state["plan_base_sha"] = str(ref.get("ref_sha") or "")[:12]
    state["replanned_at"] = now_iso()
    if apply:
        save_state(project, state)

    head = ((state.get("queue") or [{}])[0] or {}).get("label") if state.get("queue") else None
    return {"found": found, "adapt": shaped, "planned": planned,
            "base": state["plan_base_sha"], "prior_base": prior_base,
            "commits": landed, "head": head, "apply": apply}


def format_result(res: dict[str, Any], project_name: str) -> str:
    lines = ["REPLAN — the owner's change wins; the plan moves",
             "=" * 66,
             f"  outside:    {len(res['found'])} change(s) adopted"
             + ("" if res["apply"] else "  (dry run — nothing written)")]
    for item in res["found"]:
        lines.append(f"    - {item['what']}: {item['detail']}")
    lines.append(f"  plan base:  {res.get('base') or '(no delivery ref — could not stamp)'}"
                 f"  {'→ the plan is now built on it' if res['apply'] else ''}")
    landed = res.get("commits") or []
    if res.get("prior_base"):
        lines.append(f"  commits:    {commit_summary(landed, limit=4)}"
                     if landed else "  commits:    nothing landed since the last plan base")
    elif landed:
        lines.append(f"  commits:    last {len(landed)} on the delivery ref — "
                     f"{commit_summary(landed, limit=4)}")
    lines.append(f"  new head:   {res.get('head') or 'nothing waiting — the planner decides next'}")
    if not res["apply"]:
        lines.append("  NEXT:       re-run without --dry-run to adopt and replan")
    elif res.get("head"):
        lines.append(f"  NEXT:       devteam pick {project_name}   (then worktree-start)")
    else:
        lines.append(f"  NEXT:       devteam plan {project_name} --focus \"<what to build>\"")
    return "\n".join(lines)
