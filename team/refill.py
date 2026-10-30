"""Market-horizon scanning for the queue-refill path — evidence, never invention.

Original design: when the queue emptied, this module synthesized a NEW milestone
by keyword-matching a web search, appended it to the goal tree, and refilled the
queue under it. Two failures made that untenable in practice:

1. **It invented direction.** The keyword buckets were shaped like one app's
   domain (players, sessions, tournaments, wearables) and the no-match fallback
   minted "<app> Player Platform & Advanced Workflows" — so a PDF tool with five
   offline jobs got three tasks about session dashboards and court rotations.
   The team's own contract says the opposite: no goal-less mode, no invented work
   to fill silence, and the PLANNER (step 2 of every run) owns the milestone
   decision. Minting a branch here bypassed the goal gate, the impact review and
   the planner entirely.
2. **It resurrected parked branches.** Dedupe compared titles exactly, so
   re-minting "X" as "X Phase 2", "X Phase 6"… walked past an owner or planner
   decision to set X aside, once per empty queue.

So the scan stays (it writes .pipeline/market/report.json, which the research
collector folds into signals.market and the planner reads as evidence) and the
invention is gone: an empty queue now reports `no_horizon` and the run stops
honestly — planner review, then OWNER ASK — instead of shipping off-goal work.

Zero project names in code. Pure standard library.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import goals, tavily
from .core import RunLog, now_iso, profile_of, save_state, state_of
from .scan import already_delivered


def needs_refill(project: Path) -> bool:
    """Returns True if the queue has zero actionable (un-delivered) tasks."""
    state = state_of(project)
    queue = state.get("queue") or []
    if not queue:
        return True
    return all(already_delivered(q, state) for q in queue)


def _norm_title(title: str) -> str:
    """Title identity for dedupe: casefolded, phase-suffixed variants collapse.

    "Widget Platform Phase 6" and "Widget Platform" are the SAME branch to a
    human; treating them as different is how a set-aside branch comes back.
    """
    t = re.sub(r"\s*[—–-]?\s*phase\s*\d+\s*$", "", (title or "").strip(), flags=re.I)
    t = re.sub(r"\s*\((\d+)(st|nd|rd|th) pass\)\s*$", "", t, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", t.casefold()).strip()


def auto_refill(project: Path,
                topic: str = "",
                force: bool = False,
                log: RunLog | None = None) -> dict[str, Any]:
    """Scan the market horizon for evidence and REFUSE to mint a milestone.

    Returns one of:
      not_needed  — the queue still has actionable work
      no_vision   — there is no long-term goal to aim at
      no_horizon  — the scan ran, but nothing here may decide new direction

    `no_horizon` is the normal, healthy answer: the planner proposes milestones
    under the owner's vision on every run, and a queue that empties is a signal
    for the planner (or an owner ask), not a licence for a keyword matcher to
    write the roadmap.
    """
    if not needs_refill(project) and not force:
        if log:
            log("~ queue already has buildable tasks — auto-refill not required")
        return {"status": "not_needed"}

    profile = profile_of(project)
    state = state_of(project)
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    if not vision:
        if log:
            log("~ cannot scan a horizon: no long-term goal in profile.goals.vision")
        return {"status": "no_vision"}

    project_name = profile.get("name") or project.name
    query = topic or f"{project_name} {vision} modern mobile app top user feature requests competitor gaps 2026"
    market_report = tavily.search_market(project, query, log=log)

    # The scan is EVIDENCE for the planner (research.py folds market/report.json
    # into signals.market). It is not a decision.
    live = goals.active_milestone(profile, state)
    known = [_norm_title(n.get("title", ""))
             for n in goals.nodes(profile)
             if n.get("status") not in ("done", "parked", "retired")]
    if log:
        where = (f"the active milestone '{live['title'][:60]}'" if live
                 else "no unmet milestone")
        why = ("queue is empty" if needs_refill(project) else "refill forced")
        log(f"~ market scan recorded ({len(market_report.get('results') or [])} "
            f"sources) — direction stays with the planner; this module will not "
            f"mint a branch ({why}; {where} is where work belongs)")
        if known:
            log(f"~ {len(known)} branch title(s) already in the tree are not "
                "resurrected by a refill")
    state["last_research_at"] = now_iso()
    save_state(project, state)

    return {
        "status": "no_horizon",
        "reason": ("the queue is empty but a milestone may only be proposed by "
                   "the planner under the owner's vision — run `devteam plan`, "
                   "then `pick`; if the plan has nothing executable, the run's "
                   "deliverable is an owner ask"),
        "market_sources": len(market_report.get("results") or []),
        "active_milestone": (live["id"] if live else None),
    }
