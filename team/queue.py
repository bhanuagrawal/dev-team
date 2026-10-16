"""Queue admission + impact review — generic, deterministic, no project names.

Standing rule (owner requirement 2026-10-16): the moment a task enters the
queue — whether the research synthesis refills it or the orchestrator adds one
explicitly — the pipeline MUST judge the impact on the tasks ALREADY queued,
because a new task can make an older one irrelevant (already covered, the same
target+work, or a better replacement).

Contract:
- An agent PROPOSES (research's `supersedes`/`dropped` fields, or the
  orchestrator's `--supersedes`); CODE judges and applies — same pattern as the
  rest of the team ("LLM proposes, code validates and applies").
- Nothing is ever silently dropped: every affected entry is preserved in
  `state['queue_events']` with a verdict + reason, and the impact block is
  printed so the run banner can relay it verbatim.

Verdicts per already-queued entry:
- superseded — a new entry explicitly lists it, re-queues the same label, or
  does the same type of work on the same target(s).
- redundant  — a MOTION entry whose surface already shipped (nothing to do).
- keep       — unaffected by the new work.
A queued entry that simply disappears from a research plan without any verdict
is recorded as `dropped` (unexplained) — the honest signal that a plan dropped
work without judging it.
"""
from __future__ import annotations

import re

from .core import now_iso


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def label_of(entry: dict) -> str:
    """Queue entries carry 'label'; task specs carry surface.label. Accept both."""
    return (entry.get("label") or (entry.get("surface") or {}).get("label")
            or entry.get("id") or "?")


def targets_of(entry: dict) -> list[str]:
    """Queue entries carry 'target' (str|list); task specs carry
    surface.targets. Accept both so assessment works on either shape."""
    t = entry.get("target")
    if isinstance(t, list):
        return [str(x) for x in t if x]
    if isinstance(t, str) and t.strip():
        return [t.strip()]
    t = (entry.get("surface") or {}).get("targets")
    if isinstance(t, list):
        return [str(x) for x in t if x]
    return []


def verdict_for(old: dict, incoming: list[dict],
                shipped: set[str]) -> tuple[str, str, str]:
    """Judge ONE already-queued entry against the incoming plan.
    Returns (verdict, reason, superseded_by); verdict ∈ keep|superseded|redundant."""
    olabel = label_of(old)
    otargs = set(targets_of(old))
    otype = old.get("type")
    # A MOTION task whose surface already shipped has nothing left to do. A
    # bugfix/feature on shipped code is normal work, so only motion is judged
    # obsolete this way.
    if otype == "motion" and otargs and otargs <= shipped:
        return ("redundant",
                f"its target(s) already shipped: {', '.join(sorted(otargs))}", "")
    for new in incoming or []:
        supersedes = {_norm(x) for x in (new.get("supersedes") or [])}
        oid = _norm(old.get("id", ""))
        if _norm(olabel) in supersedes or (oid and oid in supersedes):
            return ("superseded",
                    new.get("reason") or "explicitly superseded by the new task",
                    label_of(new))
        nlabel, ntargs, ntype = label_of(new), set(targets_of(new)), new.get("type")
        if _norm(nlabel) == _norm(olabel):
            return ("superseded", "the same task re-queued under an updated entry",
                    nlabel)
        # Same kind of work on an overlapping target is folded into the new task.
        if otargs and ntargs and otype and otype == ntype and (otargs & ntargs):
            shared = ", ".join(sorted(otargs & ntargs))
            return ("superseded",
                    f"same type ({ntype}) on {shared} — folded into the new task",
                    nlabel)
    return ("keep", "", "")


def judge(existing: list[dict], incoming: list[dict],
          shipped_targets=()) -> list[dict]:
    """Verdicts for every already-queued entry, in queue order."""
    shipped = {str(x) for x in shipped_targets}
    out = []
    for old in existing or []:
        verdict, reason, by = verdict_for(old, incoming or [], shipped)
        out.append({"label": label_of(old), "entry": old, "verdict": verdict,
                    "reason": reason, "superseded_by": by})
    return out


def _record(state: dict, source: str, action: str, label: str,
            reason: str = "", superseded_by: str = "") -> None:
    state.setdefault("queue_events", []).append({
        "at": now_iso(), "source": source, "action": action, "label": label,
        "reason": reason, "superseded_by": superseded_by,
    })


def impact_block(source: str, added: list[dict], verdicts: list[dict],
                 unexplained: list[dict] | None = None) -> str:
    """Human/agent-readable impact summary. Printed on every admission and
    quotable verbatim in the run banner."""
    lines = [f"QUEUE IMPACT ({source}):"]
    for a in added:
        lines.append(f"  + added      {label_of(a)}")
    unexplained = unexplained or []
    gone = {_norm(label_of(u)) for u in unexplained}
    for v in verdicts:
        if v["verdict"] == "superseded":
            if _norm(v["superseded_by"]) == _norm(v["label"]):
                lines.append(f"  x replaced   {v['label']}  ({v['reason']})")
            else:
                lines.append(f"  x superseded {v['label']}  ← by {v['superseded_by']}"
                             f"  ({v['reason']})")
        elif v["verdict"] == "redundant":
            lines.append(f"  x obsolete   {v['label']}  ({v['reason']})")
        elif _norm(v["label"]) in gone:
            lines.append(f"  ! dropped    {v['label']}  "
                         "(vanished from the plan with no verdict — review)")
    kept = [v["label"] for v in verdicts
            if v["verdict"] == "keep" and _norm(v["label"]) not in gone]
    if kept:
        lines.append(f"  = kept       {', '.join(kept)}")
    if len(lines) == 1:
        lines.append("  (nothing queued was affected)")
    return "\n".join(lines)


def add_task(state: dict, entry: dict, source: str = "orchestrator",
             position: str = "head") -> str:
    """Admit ONE task: judge the existing queue against it, drop what it makes
    irrelevant (recording every verdict), and place it head|tail. Mutates
    `state` in place; the caller saves."""
    existing = list(state.get("queue", []))
    shipped = [s.get("target") for s in state.get("shipped", []) if s.get("target")]
    verdicts = judge(existing, [entry], shipped)
    kept = [v["entry"] for v in verdicts if v["verdict"] == "keep"]
    queue = [entry] + kept if position == "head" else kept + [entry]
    state["queue"] = queue
    _record(state, source, "added", label_of(entry),
            entry.get("reason", ""))
    for v in verdicts:
        if v["verdict"] in ("superseded", "redundant"):
            _record(state, source, v["verdict"], v["label"], v["reason"],
                    v["superseded_by"])
    return impact_block(source, [entry], verdicts)


def apply_plan(state: dict, incoming: list[dict], source: str = "research",
               explicit_drops: list[dict] | None = None) -> str:
    """Replace the queue with a new plan, judging what it makes irrelevant.
    Any previously-queued entry that the plan neither carries forward nor
    explicitly drops is recorded as an unexplained drop (never silent)."""
    existing = list(state.get("queue", []))
    new_queue = [dict(q) for q in incoming]
    # Explicit drops become synthetic supersede-markers so judge() can explain
    # them; they are signals for the verdict pass, never queue entries.
    markers = [{"label": d.get("label"), "type": d.get("type"),
                "target": d.get("target"), "supersedes": [d.get("label")],
                "reason": d.get("reason") or "dropped from the new plan"}
               for d in (explicit_drops or []) if d.get("label")]
    shipped = [s.get("target") for s in state.get("shipped", []) if s.get("target")]
    verdicts = judge(existing, new_queue + markers, shipped)
    carried = {_norm(label_of(q)) for q in new_queue}
    unexplained = [v["entry"] for v in verdicts
                   if v["verdict"] == "keep" and _norm(v["label"]) not in carried]
    state["queue"] = new_queue
    _record(state, source, "plan", f"{len(new_queue)} entries")
    for v in verdicts:
        if v["verdict"] in ("superseded", "redundant"):
            _record(state, source, v["verdict"], v["label"], v["reason"],
                    v["superseded_by"])
    for u in unexplained:
        _record(state, source, "dropped", label_of(u),
                "not carried forward by the new plan")
    return impact_block(source, new_queue, verdicts, unexplained)


def review(state: dict) -> str:
    """On-demand standing check: does the CURRENT queue still make sense?
    Flags entries whose target already shipped and internal duplicates (an
    earlier copy is shadowed by a later one) — the same judgments, applied
    without admitting anything new."""
    queue = list(state.get("queue", []))
    shipped = {s.get("target") for s in state.get("shipped", []) if s.get("target")}
    verdicts = []
    for i, q in enumerate(queue):
        # later entries supersede an earlier duplicate of the same task
        verdict, reason, by = verdict_for(q, queue[i + 1:], shipped)
        verdicts.append({"label": label_of(q), "entry": q, "verdict": verdict,
                         "reason": reason, "superseded_by": by})
    return impact_block("queue-review", [], verdicts)
