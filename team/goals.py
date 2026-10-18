"""Goal model — long-term vision → mid-term milestones → short-term queue.

The owner's ask (2026-10-18): "we need to run a workflow that leads towards
goals, like companies run: long-term, mid-term, short-term. Otherwise we are
blindly wasting workflow." The answer here is DATA + DETERMINISTIC CODE, not a
new agent:

  vision      (long term, owner-authored, rarely changes)  → profile['goals']['vision']
  milestones  (mid term: an outcome + measures)            → profile['goals']['milestones']
  queue       (short term: the existing research queue)     → state['queue'], each entry
                                                              may carry 'advances': <milestone id>

Division of labour, deliberately asymmetric:
  - the LLM (research synthesis) only PROPOSES: it tags each queue entry with
    the milestone it advances, and (for milestones whose measures are not
    machine-checkable) states a verdict;
  - CODE DECIDES everything derivable: which milestone is active, whether a
    metric-measured milestone is met, which queue entries are goal-linked, and
    in what order work is served. No LLM call is needed to answer "where is
    this app going?" or "what gets built next?".

Everything degrades: a project with no profile['goals'] behaves exactly as
before (goal-less mode).
"""
from __future__ import annotations

from .core import now_iso

_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "==": lambda a, b: a == b}


def plan(profile: dict) -> dict | None:
    """The owner-authored goal tree, normalised; None when the project has none."""
    g = profile.get("goals") or {}
    vision = (g.get("vision") or "").strip()
    milestones = []
    for i, m in enumerate(g.get("milestones") or []):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        milestones.append({
            "id": str(m["id"]),
            "title": (m.get("title") or m["id"]).strip(),
            "outcome": (m.get("outcome") or "").strip(),
            "measures": m.get("measures") or [],
            "order": i,
        })
    if not vision and not milestones:
        return None
    return {"vision": vision, "milestones": milestones}


def has_goals(profile: dict) -> bool:
    return plan(profile) is not None


def _log(state: dict) -> list[dict]:
    return state.get("goal_log") or []


def status_of(profile: dict, state: dict) -> dict[str, str]:
    """milestone id → 'active' | 'done' | 'dropped'. Deterministic: a milestone
    is done when the owner said so in the profile OR code/LLM recorded a met
    verdict; otherwise the first not-done milestone in order is the active one."""
    p = plan(profile) or {"milestones": []}
    latest: dict[str, str] = {}
    for e in _log(state):
        if e.get("milestone"):
            latest[str(e["milestone"])] = str(e.get("verdict", ""))
    owner_done = {str(m.get("id")) for m in (profile.get("goals") or {}).get("milestones", [])
                  if isinstance(m, dict) and str(m.get("status", "")).lower() in
                  ("done", "dropped")}
    out: dict[str, str] = {}
    for m in p["milestones"]:
        mid = m["id"]
        if mid in owner_done or latest.get(mid) in ("done", "dropped"):
            out[mid] = "done"
        else:
            out[mid] = "pending"
    for m in p["milestones"]:
        if out[m["id"]] == "pending":
            out[m["id"]] = "active"
            break
    return out


def active_milestone(profile: dict, state: dict) -> dict | None:
    p = plan(profile)
    if not p:
        return None
    st = status_of(profile, state)
    for m in p["milestones"]:
        if st.get(m["id"]) == "active":
            return m
    return None


def milestone(profile: dict, mid: str) -> dict | None:
    p = plan(profile)
    if not p:
        return None
    for m in p["milestones"]:
        if m["id"] == mid:
            return m
    return None


def check_measures(ms: dict, research: dict | None) -> tuple[bool | None, str]:
    """Evaluate a milestone's MEASURES against collected signals.
    Returns (verdict, evidence): (True|False, ...) when at least one measure is
    machine-checkable and all checkable ones pass/fail; (None, ...) when the
    milestone is text-only, i.e. a judgment call (the LLM verdict decides)."""
    ga4 = ((research or {}).get("signals", {}) or {}).get("ga4", {}) or {}
    checked, failed, notes = 0, [], []
    for m in ms.get("measures") or []:
        metric = (m or {}).get("metric")
        if not metric:
            continue
        value = ga4.get(metric)
        if value is None or not isinstance(value, (int, float)):
            notes.append(f"{metric}=? (no data)")
            continue
        checked += 1
        op = str(m.get("op", "<="))
        target = m.get("target")
        ok = bool(_OPS.get(op, _OPS["<="])(value, target))
        notes.append(f"{metric}={value} {op} {target} → {'ok' if ok else 'FAIL'}")
        if not ok:
            failed.append(metric)
    if not checked:
        return None, "; ".join(notes) or "no machine-checkable measure"
    return (not failed), "; ".join(notes)


def record_verdict(state: dict, mid: str, verdict: str, evidence: str,
                   by: str) -> None:
    """Append-only milestone verdict. 'done'/'dropped' retire the milestone and
    therefore advance the active pointer (see status_of)."""
    state.setdefault("goal_log", []).append({
        "at": now_iso(), "milestone": mid, "verdict": verdict,
        "evidence": (evidence or "")[:400], "by": by,
    })


def queue_entry_milestone(entry: dict, profile: dict, state: dict) -> str:
    """The milestone a queue entry advances: an explicit 'advances' tag, else —
    for legacy/goal-less entries — the active milestone's id (so every shipped
    release still reports a direction). Empty when there is nothing to say."""
    mid = str(entry.get("advances") or "").strip()
    if mid and milestone(profile, mid):
        return mid
    act = active_milestone(profile, state)
    return act["id"] if act else ""


def validate_advances(result: dict, profile: dict, state: dict) -> str | None:
    """Deterministic gate on the research plan: with a goal tree defined, every
    queue entry must declare the milestone it advances ('none' is allowed but
    must also be listed in off_goal so the drift is visible and recorded)."""
    p = plan(profile)
    if not p:
        return None
    known = {m["id"] for m in p["milestones"]}
    done = {mid for mid, v in status_of(profile, state).items() if v == "done"}
    off = {str(x.get("label") or "").strip()
           for x in (result.get("off_goal") or []) if isinstance(x, dict)}
    for q in result.get("queue", []):
        mid = str(q.get("advances") or "").strip()
        if not mid:
            return (f"queue entry '{q.get('label')}' has no 'advances' milestone — "
                    f"with a goal tree defined every task must name the milestone "
                    f"it serves (known ids: {sorted(known)})")
        if mid == "none":
            if q.get("label") not in off:
                return (f"queue entry '{q.get('label')}' declares advances='none' "
                        "but is not listed in off_goal — off-goal work must be "
                        "declared so it is recorded, never silent")
            continue
        if mid not in known:
            return (f"queue entry '{q.get('label')}' advances unknown milestone "
                    f"'{mid}' (known ids: {sorted(known)})")
        if mid in done:
            return (f"queue entry '{q.get('label')}' advances milestone '{mid}', "
                    "which is already done")
    return None


def goal_block(profile: dict, state: dict) -> str:
    """Owner-facing GOALS block (status/report). Purely derived, no LLM."""
    p = plan(profile)
    if not p:
        return ("GOALS: none declared — this project runs in goal-less mode "
                "(add profile['goals'] {vision, milestones} to lead the work).")
    st = status_of(profile, state)
    lines = [f"GOALS  vision: {p['vision'] or '(none)'}"]
    for m in p["milestones"]:
        mark = {"done": "✓", "active": "→", "pending": "·"}[st[m["id"]]]
        meas = len([x for x in m["measures"] if isinstance(x, dict)])
        lines.append(f"  {mark} [{m['id']}] {m['title']}"
                     + (f"  ({meas} measure{'s' if meas != 1 else ''})" if meas else ""))
        if st[m["id"]] == "active" and m.get("outcome"):
            lines.append(f"      outcome: {m['outcome']}")
    act = active_milestone(profile, state)
    if act:
        total = len(p["milestones"])
        pos = [m["id"] for m in p["milestones"]].index(act["id"]) + 1
        lines.append(f"  active milestone: {act['id']} ({pos}/{total})")
    else:
        lines.append("  active milestone: none — all milestones are done; "
                     "research should propose the next one (issues_to_file)")
    for e in _log(state)[-3:]:
        lines.append(f"  verdict {e.get('at','')[:10]} {e.get('milestone')}: "
                     f"{e.get('verdict')} ({e.get('by')}) {e.get('evidence','')[:80]}")
    return "\n".join(lines)


def prompt_block(profile: dict, state: dict) -> str:
    """The GOALS section for the research synthesis prompt."""
    p = plan(profile)
    if not p:
        return ("GOALS: this project declares no goal tree. Do not invent one; "
                "queue executable work as before, and if the app clearly needs a "
                "direction, propose the first milestone via issues_to_file.")
    st = status_of(profile, state)
    act = active_milestone(profile, state)
    body = [f'LONG-TERM VISION (owner): "{p["vision"]}"',
            "MILESTONES (mid-term, in order):"]
    for m in p["milestones"]:
        body.append(f'  - id="{m["id"]}" status={st[m["id"]]} title="{m["title"]}"')
        if m.get("outcome"):
            body.append(f'      outcome: {m["outcome"]}')
        for x in m["measures"]:
            if isinstance(x, dict):
                if x.get("metric"):
                    body.append(f'      measure(metric): {x["metric"]} '
                                f'{x.get("op","<=")} {x.get("target")}')
                elif x.get("text"):
                    body.append(f'      measure: {x["text"]}')
    body.append(f'ACTIVE MILESTONE: {act["id"] if act else "none (all done)"}')
    return "\n".join(body)


def report_line(profile: dict, state: dict, task: dict | None = None) -> str:
    """One line for the run banner: which goal this release served."""
    p = plan(profile)
    if not p:
        return ""
    mid = ""
    label = ((task or {}).get("surface") or {}).get("label") or ""
    if label:
        for q in state.get("queue", []):
            if (q.get("label") or "") == label:
                mid = str(q.get("advances") or "")
                break
    act = active_milestone(profile, state)
    if not mid and act:
        mid = act["id"]
    ms = milestone(profile, mid) if mid else None
    if ms:
        return f"goal: [{ms['id']}] {ms['title']}" + (f" ← {label}" if label else "")
    return f"goal: {act['title'] if act else 'all milestones done'}"
