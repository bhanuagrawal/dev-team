"""CLOSURE — "all tasks done" is not "the feature worked".

Why this exists (2026-11-09, owner directive)
---------------------------------------------
A feature is retired when its last queued task ships, and a goal is retired when
its last feature is retired. Both of those are tests of a LIST, not of the
product: the list can empty while the thing the feature promised was never
proven. BookSwap shipped "A great experience: 15 of 15 built — all of them" while
one of its own measures ("zero dead-end discovery feed") had no evidence at all.

So completion has to be EARNED, and the check is per node:

  built     = every task queued under it has been delivered (the old test)
  complete  = built AND every measure the node declared has evidence
  gap       = built AND some measure has no evidence — the gap is the next task

"Evidence" for a measure is one of two things, and nothing else counts:
  * a machine-checkable measure (`metric`/`op`/`target`) that signals satisfy —
    `goals.check_measures` reads it, so no judgment is involved;
  * a recorded verdict for that node (`goals.record_verdict` → state['goal_log'])
    carrying a non-empty evidence string — a text-only measure ("users stay")
    can only be closed by someone stating what they saw, on the record.

Code cannot judge "users stay"; it can refuse to call it done without a recorded
judgment. That is the whole design: no silent completion, and the gap is named
in words the next task can be written from.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import pipeline_dir, profile_of, state_of


def _research(project: Path) -> dict | None:
    import json

    try:
        return json.loads((pipeline_dir(project) / "research.json").read_text())
    except Exception:
        return None


def _verdicts(state: dict, node_id: str) -> list[dict]:
    return [e for e in (state.get("goal_log") or [])
            if str(e.get("milestone") or "") == node_id
            and str(e.get("verdict") or "") == "done"
            and str(e.get("evidence") or "").strip()]


def _measure_evidence(ms: dict, research: dict | None,
                      verdicts: list[dict]) -> tuple[bool, str]:
    """(has evidence, how) for ONE measure line."""
    from . import goals

    text = str(ms.get("text") or "").strip()
    if ms.get("metric"):
        ok, how = goals.check_measures({"measures": [ms]}, research)
        if ok is True:
            return True, f"measured: {how}"
        if ok is False:
            return False, f"measured and FAILING: {how}"
        return False, f"no data for {ms.get('metric')} — collect it or record a verdict"
    if verdicts:
        return True, f"recorded verdict: {str(verdicts[-1].get('evidence'))[:90]}"
    return False, f'no evidence for "{text[:70]}" — a recorded verdict or a number is needed'


def node_report(profile: dict, state: dict, research: dict | None,
                node: dict, status: dict[str, str]) -> dict[str, Any]:
    """One node: was its work built, and is its promise backed by evidence?"""
    nid = str(node.get("id") or "")
    raw = node.get("raw") or {}
    kind = str(node.get("kind") or "milestone")
    children = [c for c in (raw.get("children") or []) if isinstance(c, dict)]
    verdicts = _verdicts(state, nid)
    measures = [m for m in (raw.get("measures") or []) if isinstance(m, dict)]

    if children:
        built = all(status.get(str(c.get("id"))) == "done" for c in children)
        # a container is complete only when every child is, and its own measures
        # (if it declares any) are backed too
        kids_complete = True
        missing: list[str] = []
        for c in children:
            sub = node_report(profile, state, research, {"raw": c,
                              "id": str(c.get("id")), "kind": str(c.get("kind") or "milestone")},
                              status)
            if not sub["complete"]:
                kids_complete = False
                missing.extend(sub["missing"])
    else:
        built = status.get(nid) == "done"
        kids_complete = True
        missing = []

    evidence: list[str] = []
    for m in measures:
        ok, how = _measure_evidence(m, research, verdicts)
        evidence.append(("✓ " if ok else "✗ ") + how)
        if not ok:
            missing.append(how)

    complete = bool(built or children) and kids_complete and not missing
    return {"id": nid, "title": str(node.get("title") or nid), "kind": kind,
            "built": built, "complete": complete, "evidence": evidence,
            "missing": missing, "children": len(children)}


def audit(project: Path, node_id: str | None = None) -> list[dict[str, Any]]:
    """Every node (or one branch), leaf-first, with built/complete/gap."""
    from . import goals

    profile, state = profile_of(project), state_of(project)
    research = _research(project)
    status = goals.status_of(profile, state)
    flat = goals.nodes(profile)
    if node_id:
        flat = [n for n in flat if n["id"] == str(node_id)]
    return [node_report(profile, state, research, n, status) for n in flat]


def gaps(project: Path) -> list[dict[str, Any]]:
    """Nodes whose tasks are all delivered but whose measures are not backed."""
    return [r for r in audit(project) if r["built"] and not r["complete"]]


def gaps_from(profile: dict, state: dict, research: dict | None) -> list[dict[str, Any]]:
    """The same check without a project path — for the planner's validator, which
    already holds profile + state + signals and must not read the disk twice."""
    from . import goals

    status = goals.status_of(profile, state)
    return [r for r in (node_report(profile, state, research, n, status)
                        for n in goals.nodes(profile))
            if r["built"] and not r["complete"]]


def format_audit(rows: list[dict[str, Any]]) -> str:
    lines = ["CLOSURE — is every 'done' backed by evidence?", "=" * 66]
    if not rows:
        lines.append("  no plan nodes yet")
        return "\n".join(lines)
    for r in rows:
        if r["complete"]:
            mark = "✓ complete  "
        elif r["built"]:
            mark = "⚠ GAP       "
        else:
            mark = "· open      "
        lines.append(f"  {mark} {r['title'][:70]}")
        if r["kind"] == "goal" and r["children"]:
            lines.append(f"               ({r['children']} feature(s) under it)")
        for ev in r["evidence"]:
            lines.append(f"               {ev}")
        if r["built"] and r["missing"]:
            lines.append(f"               the tasks are done, the promise is not: "
                         f"{len(r['missing'])} missing")
    open_gaps = [r for r in rows if r["built"] and not r["complete"]]
    lines.append("")
    if open_gaps:
        lines.append(f"  {len(open_gaps)} node(s) look done by exhaustion but have no "
                     "evidence — the NEXT task is the gap, not the next feature.")
    else:
        lines.append("  every built node carries its evidence.")
    return "\n".join(lines)
