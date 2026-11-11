"""ADMIT — the one gate between an owner sentence and a run.

Why this exists (2026-11-09, owner directive)
---------------------------------------------
The prompt handler used to CLASSIFY. `team/router.py` matched keywords against
eight kinds and printed a seven-step chain for each. Both halves of that were
wrong, and one week proved it:

  * the classifier and the verifier were the same weak signal. A P0 bugfix whose
    text said "use the code from before the revamp" was routed to the
    destructive rebuild chain (the module carried its own post-mortem), and the
    patch for it was more keywords (`_STRONG_REBUILD_PHRASES`).
  * the agent using the team already reads the sentence, the project and the plan.
    Intent is precisely what a model does better than a term list, so a keyword
    table was the team doing the agent's job with worse information.

The split moved to where each side is strong: **the agent decides intent, code
decides consequences.** This module is the consequences, and it never guesses a
kind — the agent must state one. It then checks what a wrong statement would
cost: a milestone that is unknown, already done, parked/retired, or sits past an
unmet earlier goal (`goals.validate_advances` — the middle tier is a gate), a
project with no vision to align to, a reason that says nothing. Any of those
overrules the agent's verdict to FLAG. It can never overrule the other way.

The command, one shape for every request:

    devteam admit <project> --query "<owner words>" --kind <kind> \
        --advances <milestone> --why "<one line>" --verdict aligned

  no --verdict    → pre-check: findings + the question. Nothing is written.
  aligned         → checks pass → task pinned at the HEAD of the queue, then the
                    normal loop (plan → pick → develop → verify → release) runs it.
  gap             → the plan does not cover it: add the branch (`devteam goal`),
                    then admit again. Nothing is queued.
  against/neutral → nothing is queued; an owner question is recorded. REPLY ONLY.

Owner rule this encodes (2026-11-09): "the coding tool must not just execute;
first see whether it aligns with the plan. If it does not, flag it and reply —
let the owner take the call."
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .core import now_iso, pipeline_dir, profile_of, save_state, state_of, today

# The kinds an owner request can be. Deliberately small and action-shaped: each
# one names WORK, not a tool chain — the chain is the agent's to choose from the
# toolbox (`devteam tools`).
KINDS: dict[str, str] = {
    "bugfix": "a defect: reproduce it, fix it, prove it",
    "feature": "a capability the app does not have yet",
    "visual": "a UI change on a screen the app already has",
    "revamp": "a ground-up rebuild or UI re-imagination",
    "brand": "the icon: Stitch first, then the app, then the listing",
    "store": "the Play listing: screenshots, copy, art — no app code",
    "release": "deliver work that is already on the branch",
    "direction": "a change of goal or priority — the owner's own sentence",
}

VERDICTS = ("aligned", "neutral", "against", "gap")


def _slug(text: str, n: int = 4) -> str:
    words = [w for w in re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()
             if len(w) >= 3][:n]
    return "-".join(words)


def _label(query: str, kind: str) -> str:
    return f"{kind}-{_slug(query) or kind}-{today()}"[:60].strip("-")


def _research(project: Path) -> dict | None:
    """The latest collected signals, so an earlier goal's NUMBER can gate later
    work at admission time (the same evidence the planner uses)."""
    path = pipeline_dir(project) / "research.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _targets(profile: dict, kind: str) -> list[str]:
    allowed = [str(p) for p in (profile.get("allowed_paths") or []) if p]
    base = list(allowed) or ([str(profile["src_root"])] if profile.get("src_root") else [])
    if kind in ("visual", "revamp", "store"):
        for extra in ("docs/storefront*.json", "docs/storefront-direction.json",
                      "distribution/listing/**/images/**"):
            if extra not in base:
                base.append(extra)
    return base


def entry_for(profile: dict, query: str, kind: str, advances: str,
              why: str) -> dict[str, Any]:
    """The queue entry an admitted request becomes. One shape, always."""
    from . import goals

    # A task spec is a mini-PRD (`problem`, `done_when`), and `pick` copies the
    # entry's fields onto it. An admitted owner request used to arrive with
    # neither, so the developer's spec had no statement of the defect and no
    # acceptance bar. The owner's sentence IS the problem; the milestone's
    # measures are the best available "done when" until the developer refines it.
    entry: dict[str, Any] = {
        "type": "bugfix" if kind == "bugfix" else "feature",
        "label": _label(query, kind),
        "target": ", ".join(_targets(profile, kind)),
        "risk": "safe",
        "reason": f'Owner request: "{query}" — {why}' if why else f'Owner request: "{query}"',
        "owner_request": query,
        "problem": query,
        "pinned": True,
        "priority": "owner",
        "kind": kind,
        # the agent's own words: who decided this request serves that outcome.
        "why": why,
        "decided_by": "agent",
        "advances": advances or "none",
    }
    raw, _ = goals.raw_node(profile, str(advances or ""))
    measures = [
        str(m.get("text")).strip()
        for m in ((raw or {}).get("measures") or [])
        if isinstance(m, dict) and str(m.get("text") or "").strip()
    ]
    if measures:
        entry["done_when"] = measures[:3]
    return entry


def check(project: Path, query: str, kind: str | None, advances: str,
          why: str, verdict: str | None) -> dict[str, Any]:
    """Deterministic findings + the agent's verdict → one decision.

    Returns {ok, verdict, who, findings, question, next, milestone, title}.
    `verdict` is "ok" | "flag" | "precheck" — never a guess about the work.
    """
    from . import goals

    profile, state = profile_of(project), state_of(project)
    findings: list[str] = []

    def flag(who: str, question: str, nxt: str = "") -> dict[str, Any]:
        return {"ok": False, "verdict": "flag", "who": who, "findings": findings,
                "question": question, "next": nxt, "milestone": advances or "",
                "title": ""}

    # 1. there must be something to align to.
    if goals.vision_required(profile) and not goals.vision(profile):
        findings.append("the project has no long-term goal")
        return flag("owner", "what is this app for, in one sentence? "
                             "(nothing can be aligned until the goal exists)",
                    f"devteam vision {project.name} \"<one sentence>\"")

    # 2. a direction change is the owner's sentence, never an agent's inference.
    if kind == "direction":
        findings.append("a direction change is the owner's call")
        return flag("owner", f"is this the new goal? — \"{query}\"",
                    f"devteam goal {project.name} set --vision \"{query}\"   (owner only)")

    # 3. the kind must be one the team knows. Code refuses to guess it.
    if not kind or kind not in KINDS:
        findings.append(f"no known kind (got {kind!r}; known: {', '.join(KINDS)})")
        return flag("agent", "what kind of work is this: a defect, a feature, a UI "
                             "change, a rebuild, the icon, the store page, or a release?",
                    "re-run admit with --kind <kind>")

    # 4. the milestone it claims to serve must survive the plan's own gate.
    title = ""
    if goals.plan(profile):
        node = goals.milestone(profile, advances) if advances else None
        title = str((node or {}).get("title") or "")
        if not advances or advances == "none":
            findings.append("no milestone named")
            return flag("agent", "which milestone does this serve? (the plan has one "
                                 "for it, or it needs a branch first)",
                        f"devteam tree {project.name} — pick an id, or make one "
                        f"with devteam goal")
        problem = goals.validate_advances(
            {"queue": [{"label": _label(query, kind), "advances": advances}],
             "off_goal": []},
            profile, state, research=_research(project))
        if problem:
            findings.append(problem)
            return flag("agent", problem, f"devteam tree {project.name}")
        findings.append(f"serves {advances} ({title or 'milestone'}) — active")
    else:
        findings.append("no goal tree yet — the planner has not built one")

    # 5. the record: a decision with no reason is not auditable.
    if not (why or "").strip():
        findings.append("no --why")
        return flag("agent", "say in one line which outcome this serves — the "
                             "decision is recorded with the task",
                    "re-run admit with --why \"<one line>\"")

    # 6. the agent's own verdict. It is the only judgment here — and it is on
    #    the record. Code cannot make it, so code refuses to proceed without it.
    if verdict is None:
        return {"ok": False, "verdict": "precheck", "who": "agent",
                "findings": findings,
                "question": "does this serve the milestone it names? verdict: "
                            "aligned | neutral | against | gap",
                "next": "re-run the same command with --verdict <verdict>",
                "milestone": advances or "", "title": title}
    if verdict == "gap":
        findings.append("verdict: gap — the plan does not cover this")
        return flag("agent", "the plan is missing a branch for this work — add it "
                             "first, then admit the task under it",
                    f"devteam goal {project.name} ...   (add the branch, then re-run admit)")
    if verdict in ("against", "neutral"):
        findings.append(f"verdict: {verdict}")
        return flag("owner", f"you asked for \"{query}\" and it {verdict} the plan "
                             f"(milestone: {title or advances or 'none'}) — keep it, "
                             "park it, or drop it?", "REPLY ONLY — nothing was queued")

    findings.append("verdict: aligned")
    return {"ok": True, "verdict": "ok", "who": "agent", "findings": findings,
            "question": "", "next": f"devteam plan {project.name}",
            "milestone": advances or "", "title": title}


def admit(project: Path, query: str, kind: str | None, advances: str, why: str,
          verdict: str | None, dry_run: bool = False) -> dict[str, Any]:
    """Run the gate; on OK pin the task at the head of the queue (once)."""
    from . import queue as qmod

    profile, state = profile_of(project), state_of(project)
    res = check(project, query, kind, advances, why, verdict)
    res["query"] = query
    res["kind"] = kind or ""

    if res["verdict"] == "precheck":
        return res

    if not res["ok"]:
        # Flagged, not executed: the owner (or the agent, for a gap/kind) answers.
        # Recorded so a reply cannot be "forgotten" — no rumour, no queue entry.
        state.setdefault("admit_log", []).append({
            "at": now_iso(), "query": query, "kind": kind or "",
            "advances": advances or "", "verdict": verdict or "",
            "who": res["who"], "question": res["question"],
        })
        if not dry_run:
            save_state(project, state)
        return res

    entry = entry_for(profile, query, kind or "", advances, why)
    if dry_run:
        res["entry"] = entry
        res["dry_run"] = True
        return res

    impact = qmod.add_task(state, entry, source="admit", position="head")
    # A UI rebound makes the shipped screenshots a depiction of an older app, so
    # the storefront regeneration is queued WITH it, at the next slot — the same
    # rule the release gates enforce, stated once, here.
    if kind in ("visual", "revamp"):
        store_entry = entry_for(profile, query, "store", advances, why)
        store_entry["label"] = f"storefront-regenerate-assets-{today()}"
        store_entry["reason"] = f"Regenerate the Play listing art to match: {query}"
        queue = state.get("queue") or []
        if queue and queue[0].get("label") == entry["label"]:
            queue.insert(1, store_entry)
        else:
            queue.append(store_entry)
        impact += f"\n  linked:         {store_entry['label']} (queued next — the listing follows the UI)"
    save_state(project, state)
    res["entry"] = entry
    res["impact"] = impact
    return res


def format_admit(res: dict[str, Any]) -> str:
    lines = ["ADMIT — the plan gate", "=" * 66,
             f"  query:      {res.get('query')}",
             f"  kind:       {res.get('kind') or '(not stated)'}"]
    if res.get("milestone"):
        lines.append(f"  advances:   {res['milestone']}" +
                     (f"  ({res['title']})" if res.get("title") else ""))
    lines.append("  findings:")
    for f in res.get("findings") or ["  (none)"]:
        lines.append(f"    - {f}")
    lines.append("")
    verdict = res.get("verdict")
    if verdict == "ok":
        task = res.get("entry") or {}
        head = "OK (dry run — nothing queued)" if res.get("dry_run") else \
               "OK — pinned at the HEAD of the queue"
        lines.append(f"  VERDICT:    {head}")
        lines.append(f"  task:       {task.get('label')}")
        lines.append(f"  NEXT:       {res.get('next')}")
    elif verdict == "precheck":
        lines.append("  VERDICT:    NEEDS YOUR JUDGMENT (nothing written)")
        lines.append(f"  QUESTION:   {res.get('question')}")
        lines.append(f"  NEXT:       {res.get('next')}")
    else:
        who = "OWNER" if res.get("who") == "owner" else "AGENT"
        lines.append(f"  VERDICT:    FLAG — nothing was queued ({who} decides)")
        lines.append(f"  QUESTION:   {res.get('question')}")
        if res.get("next"):
            lines.append(f"  NEXT:       {res.get('next')}")
        if who == "OWNER":
            lines.append("  RULE:       reply to the owner and stop — do not execute")
    return "\n".join(lines)
