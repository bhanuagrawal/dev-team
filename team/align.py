"""Alignment — "does this serve a goal?" The judgment is LLM-only (it cannot be
code), so code does exactly two things: hand the question to an agent with the
plan attached, and record/show the answer. Nothing is silently blocked; the
owner is always told, and `against`/`gap` verdicts are loud.

Verdicts (plain words the owner sees):
  aligned  — moves a goal forward
  neutral  — doesn't move a goal, but it is cheap and harmless
  against  — works against a goal (or spends the night on something we decided
             not to do)
  gap      — the work may be right but the PLAN doesn't cover it: the plan is
             missing a branch. The agent should change the tree (devteam goal)
             rather than squeeze the work under an unrelated milestone.
"""
from __future__ import annotations

import json
from pathlib import Path

from .core import now_iso, profile_of, state_of
from . import goals

VERDICT_WORDS = {
    "aligned": "moves a goal forward",
    "neutral": "doesn't move a goal, but cheap",
    "against": "works against a goal",
    "gap": "the plan doesn't cover this",
}


def alignment_prompt(project: Path, request: str, task: dict | None = None) -> str:
    """The exact question handed to an agent, with the plan and the request."""
    profile, state = profile_of(project), state_of(project)
    plan = goals.nodes(profile)
    lines = [
        "You are the alignment check for a small software team.",
        "",
        "THE PLAN (the owner set the goal; the team keeps the rest current):",
    ]
    if plan:
        p = goals.plan(profile)
        st = goals.status_of(profile, state)
        lines.append(f'  GOAL: "{p["vision"]}"')
        for n in plan:
            # show the DERIVED status (a milestone with no explicit status is
            # still the one being worked on), never a bare "unset"
            shown = st.get(n["id"]) or n["status"] or "unset"
            lines.append(f'  - {n["kind"]}: {n["title"]}  (id={n["id"]}, '
                         f'status={shown})')
            for x in (n["raw"].get("measures") or []):
                if isinstance(x, dict):
                    lines.append("      how we'd know: "
                                 + (f'{x.get("metric")} {x.get("op","<=")} '
                                    f'{x.get("target")}' if x.get("metric")
                                    else str(x.get("text") or "")))
    else:
        lines.append("  (no plan declared — say so, and answer 'gap' with a "
                     "proposed first goal rather than inventing one silently)")
    q = state.get("queue") or []
    if q:
        lines.append("")
        lines.append("WORK ALREADY QUEUED (for context, newest plan first):")
        for e in q[:8]:
            lines.append(f'  - {e.get("label")} → {e.get("advances") or "(nothing)"}')
    if task:
        lines.append("")
        lines.append("THE PIECE OF WORK BEING CHECKED:")
        lines.append(f'  task id: {task.get("id")}')
        lines.append(f'  label: {task.get("surface", {}).get("label")}')
        lines.append(f'  type: {task.get("type")}  risk: {task.get("risk")}')
        lines.append(f'  why the team picked it: {task.get("reason", "")}')
    lines += [
        "",
        "THE REQUEST THAT HAS TO BE JUDGED:",
        f'  "{request.strip()}"',
        "",
        "Answer with a single JSON object, nothing else:",
        "  verdict    one of: aligned | neutral | against | gap",
        "  milestone  the id it serves (empty string if none). For 'gap', empty.",
        "  why        one or two sentences a non-technical owner understands,",
        "             naming the goal it helps or the goal it fights.",
        "  cost       what this spends (a night? a week? a rewrite?) — plain words.",
        "  options    other ways to get the same thing, shortest first (may be []).",
        "",
        "Rules: judge against the plan above, not against 'is this good software'.",
        "Cosmetic polish that serves no goal is 'neutral' at best. If the work is",
        "genuinely right but no branch covers it, answer 'gap' AND change the tree:",
        f"  `python3 cli.py goal {project} add --parent <id> --title ... "
        "--why ...` (or park/set-status) — then say which change you made in `why`.",
        "Do not refuse to judge: 'no plan declared' is itself an answer ('gap').",
    ]
    return "\n".join(lines)


def apply_file(project: Path, path: Path) -> dict:
    """Record an alignment verdict. Validates shape; the LLM cannot fake the
    recording — malformed answers are refused, not guessed at."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        data = data[0] if data else {}
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in VERDICT_WORDS:
        raise SystemExit(f"alignment verdict must be one of "
                         f"{sorted(VERDICT_WORDS)} — got {verdict!r}")
    if not str(data.get("why") or "").strip():
        raise SystemExit("alignment verdict needs 'why' — an unexplained verdict "
                         "is not usable by the owner")
    profile, state = profile_of(project), state_of(project)
    request = ""
    sidecar = project / ".pipeline" / "alignment-request.txt"
    if sidecar.exists():
        request = sidecar.read_text().strip()
    entry = {"at": now_iso(), "request": request, "verdict": verdict,
             "milestone": str(data.get("milestone") or ""),
             "why": str(data.get("why") or "").strip(),
             "cost": str(data.get("cost") or "").strip(),
             "options": [str(o) for o in (data.get("options") or [])][:5],
             "by": str(data.get("by") or "agent")}
    state.setdefault("alignment_log", []).append(entry)
    from .core import save_state
    save_state(project, state)
    return entry


def line(entry: dict) -> str:
    """One plain sentence for the owner."""
    w = VERDICT_WORDS.get(entry.get("verdict"), entry.get("verdict"))
    named = f' (serves: {entry["milestone"]})' if entry.get("milestone") else ""
    out = [f'"{str(entry.get("request"))[:90]}" → {w}{named}',
           f'   {entry.get("why")}']
    if entry.get("cost"):
        out.append(f'   costs: {entry["cost"]}')
    for o in entry.get("options") or []:
        out.append(f'   alternative: {o}')
    return "\n".join(out)
