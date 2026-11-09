"""Goal model — long-term vision → goals → mid-term milestones → short-term queue.

The owner's ask (2026-10-18): "we need to run a workflow that leads towards
goals, like companies run: long-term, mid-term, short-term. Otherwise we are
blindly wasting workflow." The answer here is DATA + DETERMINISTIC CODE, not a
new agent:

  vision      (long term, owner-authored, rarely changes)  → profile['goals']['vision']
  goals       (the ENDS: what the product must be for users, each with ONE
               number — a great experience, users stay, users pay)
                                                           → profile['goals']['milestones']
                                                             nodes with kind='goal'
  milestones  (mid term: a MEANS — an outcome + measures)  → children of a goal
  queue       (short term: the existing research queue)     → state['queue'], each entry
                                                              may carry 'advances': <milestone id>

The middle tier exists because a milestone is a MEANS (bedtime grayscale,
notification shielding) and nothing in the data named the ENDS those means
serve: a feature stood where a goal should, and "keep users before you sell to
them" was a sentence, not a gate. Now a goal whose number fails keeps later
goals' milestones out of the plan (`validate_advances`), and a new milestone
must sit under a goal (`apply_one`).

Division of labour, deliberately asymmetric:
  - the LLM (research synthesis) only PROPOSES: it tags each queue entry with
    the milestone it advances, and (for milestones whose measures are not
    machine-checkable) states a verdict;
  - CODE DECIDES everything derivable: which milestone is active, whether a
    metric-measured goal/milestone is met, which queue entries are goal-linked,
    and in what order work is served. No LLM call is needed to answer "where is
    this app going?" or "what gets built next?".

Everything degrades: a project with no profile['goals'] behaves exactly as
before (goal-less mode).
"""
from __future__ import annotations

from .core import now_iso

_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "==": lambda a, b: a == b}


# --------------------------------------------------------------------------- #
# THE MIDDLE TIER — goals (the ENDS) between the vision and the milestones
# --------------------------------------------------------------------------- #
# Owner, 2026-11-07: "long term goal is fine, short term looks like a task — we
# should have one more layer in between: next to the long-term goal could be
# 'great user experience', 'retaining customers', and below it what we have
# today."
#
# The gap was real: the old tree was vision → milestones, and a MILESTONE is a
# MEANS (a feature: bedtime grayscale, notification shielding). Nothing in the
# data named the ENDS those features serve, so a feature could be mistaken for a
# goal, a plan could sell before it kept anyone, and "does this work serve
# anything?" had no answer a machine could check.
#
#   vision      (owner, one sentence, rarely changes)
#   goals       (the ends; each carries ONE number — experience, retention, revenue)
#   milestones  (the means; the old top level, now filed under a goal)
#   tasks       (the queue)
#
# A goal node is a normal tree node with kind="goal": the SAME add/park/reparent
# engine, the SAME rendering, the SAME audit log. No new storage format.
GOAL_KIND = "goal"

GOAL_DEFAULTS = (
    {"id": "goal-experience", "title": "A great experience",
     "outcome": "Users get a product that does the thing it promises, without "
                "friction, confusion or a crash.",
     "why": "everything else is measured on top of it — a user who cannot use the "
            "app never becomes a retained one, let alone a paying one"},
    {"id": "goal-retention", "title": "Users stay",
     "outcome": "People who install keep using it — the product earns a place in "
                "their day.",
     "why": "demand cannot be measured on users who leave on day one; retention is "
            "the first half of the owner's thesis"},
    {"id": "goal-revenue", "title": "Users pay",
     "outcome": "The product earns subscription revenue from the users it keeps.",
     "why": "the second half of the owner's thesis, and it only means anything "
            "after the first two hold"},
)

# Which goal a MILESTONE's title names. Deliberately title-only and dumb: a
# reparent is one auditable change, and the planner can move any branch it
# disagrees with. Guessing from outcomes/measures would need judgement, which
# adapt must never do.
_REVENUE_HINTS = ("subscri", "paywall", "conversion", "revenue", "mrr", "ltv",
                  "trial", "monetiz", "monetis", "purchase", "billing", "price",
                  "funnel", "upsell", "checkout", "upgrade sheet")
_RETENTION_HINTS = ("retention", "churn", "uninstall", "day-1", "day 1", "day-30",
                    "day 30", "streak", "re-engage", "reengage", "come back",
                    "active users", "weekly active", "keep users", "detox")


def _classify(title: str) -> str:
    t = (title or "").lower()
    if any(w in t for w in _REVENUE_HINTS):
        return "goal-revenue"
    if any(w in t for w in _RETENTION_HINTS):
        return "goal-retention"
    return "goal-experience"


def goal_tier(profile: dict) -> list[dict]:
    """The goal nodes — top-level branches with kind='goal' (empty on a flat tree)."""
    p = plan(profile)
    if not p:
        return []
    return [m for m in p["milestones"] if str(m.get("kind") or "") == GOAL_KIND]


def tree_gaps(profile: dict) -> list[str]:
    """What is structurally wrong with the plan's SHAPE, in plain words.

    Empty when the tree has a goal tier and every milestone sits under one, or
    when there is nothing to organise yet (no vision / no milestones — the
    existing blocking owner ask covers that case)."""
    p = plan(profile)
    if not p:
        return []
    out: list[str] = []
    goals = goal_tier(profile)
    if not goals:
        out.append("no goal tier: milestones sit directly under the vision, so a "
                   "feature stands where a goal should")
    for m in p["milestones"]:
        if str(m.get("kind") or "") != GOAL_KIND:
            out.append(f"milestone '{m['id']}' sits outside any goal")
    return out


def goal_of(profile: dict, node_id: str) -> str:
    """The goal a node sits under ('' when none / when node_id is itself a goal)."""
    flat = {n["id"]: n for n in nodes(profile)}
    n = flat.get(str(node_id or ""))
    while n is not None and n["kind"] != GOAL_KIND:
        n = flat.get(n["parent"])
    return n["id"] if n is not None else ""


def ensure_goal_tier(profile: dict, state: dict | None = None,
                     by: str = "agent") -> list[str]:
    """Insert/finish the middle tier deterministically — no LLM, idempotent.

    Flat tree (milestones directly under the vision): create the three ends and
    file every existing milestone under the goal its TITLE names, keeping their
    order. Tier already present: file any top-level orphan under a goal.
    Returns plain-English notes ([] when already shaped)."""
    p = plan(profile)
    if not p:
        return []
    top = profile.setdefault("goals", {}).setdefault("milestones", [])
    notes: list[str] = []

    if not any(str(m.get("kind") or "") == GOAL_KIND
               for m in top if isinstance(m, dict)):
        created: dict[str, dict] = {}
        for spec in GOAL_DEFAULTS:
            created[spec["id"]] = {
                "id": spec["id"], "kind": GOAL_KIND, "title": spec["title"],
                "outcome": spec["outcome"], "decided_by": by, "status": "pending",
                "why": spec["why"], "children": [],
            }
        moved: list[tuple[str, str]] = []
        for m in list(top):
            if not isinstance(m, dict) or not m.get("id"):
                continue
            gid = _classify(str(m.get("title") or ""))
            created[gid]["children"].append(m)
            moved.append((str(m["id"]), gid))
        profile["goals"]["milestones"] = [created[s["id"]] for s in GOAL_DEFAULTS]
        notes.append("added the goal tier between the vision and the milestones "
                     "(" + ", ".join(s["title"] for s in GOAL_DEFAULTS) + ")")
        for mid, gid in moved:
            notes.append(f"milestone '{mid}' → goal '{gid}'")
        if state is not None:
            change(
                state, "add", "goal tier",
                "the plan had no middle tier: milestones sat directly under the "
                "vision, so a feature stood where a goal should and no goal number "
                "could gate the next one. Code inserted the three ends (experience "
                "→ retention → revenue) and filed each milestone under the goal its "
                "title names; the planner may move any of them.",
                before="vision → milestones", after="vision → goals → milestones",
                by=by)
        return notes

    # tier exists — close the remaining gap (a milestone added at the top level)
    goals = {str(m["id"]): m for m in top
             if isinstance(m, dict) and str(m.get("kind") or "") == GOAL_KIND}
    orphans = [m for m in top
               if isinstance(m, dict) and m.get("id")
               and str(m.get("kind") or "") != GOAL_KIND]
    for m in orphans:
        gid = _classify(str(m.get("title") or ""))
        target = goals.get(gid) or next(iter(goals.values()), None)
        if target is None:
            break
        top.remove(m)
        target.setdefault("children", []).append(m)
        notes.append(f"milestone '{m['id']}' moved under goal '{target['id']}'")
        if state is not None:
            change(state, "reparent", str(m["id"]),
                   "a milestone must sit under a goal; code filed it under the "
                   "goal its title names", before="vision", after=str(target["id"]),
                   by=by)
    return notes


def ensure_shape(project, log=None) -> list[str]:
    """Apply ensure_goal_tier to a PROJECT's files (no-op when already shaped).

    The one entry point `adapt`, the planner and a manual run all call, so the
    next run of ANY app re-shapes its plan the same way."""
    from .core import pipeline_dir, profile_of, save_json, state_of
    profile, state = profile_of(project), state_of(project)
    notes = ensure_goal_tier(profile, state)
    if notes:
        pdir = pipeline_dir(project)
        save_json(pdir / "profile.json", profile)
        save_json(pdir / "state.json", state)
        if log:
            for n in notes:
                log("plan: " + n)
    return notes


def vision(profile: dict) -> str:
    """The owner's own one-sentence thesis — the top of the tree ('' if unset)."""
    return str((profile.get("goals") or {}).get("vision") or "").strip()


def vision_required(profile: dict) -> bool:
    """True when the owner has never said what this app is for.

    This is the ONE thing no agent may supply, so a project in this state does
    not get work picked for it: `pick` refuses (rc=4) and the run's honest
    terminal state is the OWNER ASK. Owner directive 2026-10-18: "I should have
    been prompted to provide long term goals. There should not be no goal-less
    mode." Eighteen runs shipped against uncounted assumptions before this gate
    existed — the failure was design, not diligence."""
    return not vision(profile)


def direction_ask(profile: dict, state: dict, project_name: str = "<project>") -> dict | None:
    """The ONE question only the owner can answer: what is this app for, long term?

    Returned (not printed) so every surface says the SAME thing — adapt's report,
    `doctor`, the goal page, `pick`, the planner's review block and `record`'s
    OWNER ASKS block. None once the vision exists (a vision without milestones is
    NOT an owner ask: proposing the first milestone is the planner's job).

    It is a BLOCKING ask by owner directive 2026-10-18 — the run ends here rather
    than shipping work aimed at nothing."""
    if not vision_required(profile):
        return None
    return {
        "what": "BLOCKING: nobody has said what this app is for long term — one "
                "sentence from you becomes the goal every release is aimed at. "
                "Until you answer, no work is picked or built (the team will not "
                "invent a direction)",
        "how": f'python3 cli.py vision {project_name} "what this app is for, in '
               f'one sentence"',
    }


def owner_asks(profile: dict, state: dict, project_name: str = "<project>") -> list[dict]:
    """Every open question that only the owner can answer, in plain words."""
    out: list[dict] = []
    a = direction_ask(profile, state, project_name)
    if a:
        out.append(a)
    return out


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
            "children": m.get("children") or [],
            "status": str(m.get("status") or ""),
            # WHO wrote it is part of the plan, not decoration: an agent-added
            # branch shown as "set by you" would be a lie the owner cannot catch.
            "decided_by": str(m.get("decided_by") or "owner"),
            "kind": str(m.get("kind") or "milestone"),
            "order": i,
        })
    if not vision and not milestones:
        return None
    return {"vision": vision, "milestones": milestones}


def has_goals(profile: dict) -> bool:
    return plan(profile) is not None


def has_milestones(profile: dict) -> bool:
    """True when the tree holds at least one MEANS (a non-goal branch)."""
    return any(n["kind"] != GOAL_KIND for n in nodes(profile))


def _log(state: dict) -> list[dict]:
    return state.get("goal_log") or []


def _leaves_under(nid: str, kids: dict, kind: dict) -> list[str]:
    """Every leaf (a node that is nobody's parent) under a branch, in tree order."""
    out: list[str] = []
    for c in kids.get(nid) or []:
        cid = str((c or {}).get("id") or "")
        if not cid:
            continue
        if kids.get(cid):
            out += _leaves_under(cid, kids, kind)
        else:
            out.append(cid)
    return out


def status_of(profile: dict, state: dict) -> dict[str, str]:
    """node id → 'active' | 'pending' | 'done' | 'parked'.

    Deterministic, over the WHOLE tree (goal tier included):
      * a milestone is done when the owner said so in the profile OR code/the
        synthesis recorded a met verdict; a branch the owner set aside is
        'parked' and can never be promoted;
      * the active pointer is the first LEAF the owner/planner explicitly marked
        active, else the first still-open leaf in tree order — a leaf, because a
        container (a goal, or a milestone holding sub-goals) is not the thing a
        run builds;
      * a GOAL derives its status from what is under it: done when everything
        under it is done, active when it holds the active pointer, else pending.

    Position alone used to decide, which meant parking a milestone changed its
    label and nothing else: the very branch the owner set aside stayed the one
    `status` named as "working on", the planner aimed every cycle at it, and a
    brand-new active milestone sat unusued at the end of the list.
    """
    p = plan(profile) or {"milestones": []}
    latest: dict[str, str] = {}
    for e in _log(state):
        if e.get("milestone"):
            latest[str(e["milestone"])] = str(e.get("verdict", ""))
    flat = nodes(profile)
    raw = {n["id"]: n["raw"] for n in flat}
    kind = {n["id"]: n["kind"] for n in flat}
    kids = {n["id"]: [c for c in (n["raw"].get("children") or [])
                      if isinstance(c, dict) and c.get("id")]
            for n in flat}
    out: dict[str, str] = {}

    for n in flat:
        nid = n["id"]
        if kind[nid] == GOAL_KIND:
            continue
        rs = str(raw[nid].get("status") or "").lower()
        if rs in ("done", "dropped") or latest.get(nid) in ("done", "dropped"):
            out[nid] = "done"
        elif rs in ("parked", "retired"):
            out[nid] = "parked"
        else:
            out[nid] = "pending"

    def explicit_active(i: str) -> bool:
        return (kind.get(i) != GOAL_KIND
                and str(raw.get(i, {}).get("status") or "").lower() == "active"
                and out.get(i) == "pending")

    leaves = [n["id"] for n in flat
              if kind[n["id"]] != GOAL_KIND and not kids.get(n["id"])]
    active = next((i for i in leaves if explicit_active(i)), None)
    if active is None:
        active = next((i for i in leaves if out.get(i) == "pending"), None)
    if active is None:  # a container-only tree: any open node is better than none
        active = next((n["id"] for n in flat
                       if kind[n["id"]] != GOAL_KIND
                       and out.get(n["id"]) == "pending"), None)
    if active:
        out[active] = "active"

    # goals last (post-order: children appear after parents in a depth-first walk)
    for n in reversed(flat):
        nid = n["id"]
        if kind[nid] != GOAL_KIND:
            continue
        sts = [out[i] for i in _leaves_under(nid, kids, kind) if i in out]
        rs = str(raw[nid].get("status") or "").lower()
        if rs in ("done", "dropped") or latest.get(nid) in ("done", "dropped"):
            out[nid] = "done"
        elif rs in ("parked", "retired"):
            out[nid] = "parked"
        elif sts and all(s == "done" for s in sts):
            out[nid] = "done"
        elif "active" in sts:
            out[nid] = "active"
        elif sts and all(s in ("done", "parked") for s in sts):
            out[nid] = "parked"
        else:
            out[nid] = "pending"
    return out


def active_milestone(profile: dict, state: dict) -> dict | None:
    """The leaf this run should be building (a goal or a container is never it)."""
    p = plan(profile)
    if not p:
        return None
    st = status_of(profile, state)
    for n in nodes(profile):
        if n["kind"] == GOAL_KIND or st.get(n["id"]) != "active":
            continue
        raw = n["raw"]
        return {"id": n["id"], "title": n["title"],
                "outcome": str(raw.get("outcome") or ""),
                "measures": raw.get("measures") or [],
                "children": raw.get("children") or [],
                "status": str(raw.get("status") or ""),
                "decided_by": str(raw.get("decided_by") or "owner"),
                "kind": n["kind"], "order": 0}
    return None


def milestone(profile: dict, mid: str) -> dict | None:
    """Any branch by id, at ANY depth (a milestone now lives under a goal)."""
    n = find_node(profile, str(mid or ""))
    if n is None:
        return None
    raw = n["raw"]
    return {"id": n["id"], "title": n["title"],
            "outcome": str(raw.get("outcome") or ""),
            "measures": raw.get("measures") or [],
            "children": raw.get("children") or [],
            "status": str(raw.get("status") or ""),
            "decided_by": str(raw.get("decided_by") or "owner"),
            "kind": n["kind"], "order": 0}


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
    for legacy entries that never carried one — the active milestone's id (so
    every shipped release still reports a direction).

    An EXPLICIT "none" is returned as-is: the synthesis declared that work
    off-goal (and had to list it in `off_goal`), so silently re-pointing it at
    the active milestone would make the auditable mapping a lie."""
    mid = str(entry.get("advances") or "").strip()
    if mid == "none":
        return "none"
    if mid and find_node(profile, mid):
        return mid   # any branch counts, including a sub-goal deeper in the tree
    act = active_milestone(profile, state)
    return act["id"] if act else ""


def validate_advances(result: dict, profile: dict, state: dict,
                      extra_ids: tuple = (),
                      research: dict | None = None) -> str | None:
    """Deterministic gate on the research plan: with a goal tree defined, every
    queue entry must declare the milestone it advances ('none' is allowed but
    must also be listed in off_goal so the drift is visible and recorded).

    `extra_ids` are branches the SAME result is about to add (goal_changes), so a
    synthesis can create a branch and queue work for it in one plan.
    `research` carries the metric signals that let an EARLIER goal's number gate
    later work (the middle tier is a gate, not a label)."""
    p = plan(profile)
    if not p:
        return None
    flat = nodes(profile)
    known = {n["id"] for n in flat} | {str(i) for i in extra_ids}
    derived = status_of(profile, state)
    done = {n["id"] for n in flat
            if (derived.get(n["id"]) or n["status"]) == "done"}
    # A parked/retired branch is "not now": queueing work for it would contradict
    # the plan the same cycle set it aside. Set its status instead.
    shelved = {n["id"] for n in flat
               if n["status"].lower() in ("parked", "retired", "dropped")}
    off = {str(x.get("label") or "").strip()
           for x in (result.get("off_goal") or []) if isinstance(x, dict)}
    # A branch whose tasks are all delivered but whose measures have no evidence is
    # NOT done — it is a gap, and queuing work against it is exactly what should
    # happen next (owner directive 2026-11-09). Computed once, before the per-entry
    # checks, because it changes what "already done" means below.
    try:
        from . import closure as closure_mod
        built_gaps = closure_mod.gaps_from(profile, state, research)
    except Exception:
        built_gaps = []
    gap_ids = {r["id"] for r in built_gaps}
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
        node = find_node(profile, mid)
        if node is not None and node["kind"] == GOAL_KIND:
            return (f"queue entry '{q.get('label')}' advances the GOAL '{mid}' — a "
                    "task serves a milestone under it, not the goal itself; name "
                    "the milestone")
        if mid in done and mid not in gap_ids:
            return (f"queue entry '{q.get('label')}' advances milestone '{mid}', "
                    "which is already done")
        if mid in gap_ids:
            gap = next(r for r in built_gaps if r["id"] == mid)
            if not str(q.get("problem") or "").strip():
                return (f"queue entry '{q.get('label')}' closes the gap in '{mid}' "
                        f"({gap['missing'][0]}) — say WHAT the gap is in 'problem' "
                        f"so the task is written from evidence, not from the label")
        if mid in shelved:
            return (f"queue entry '{q.get('label')}' advances '{mid}', which is "
                    "parked/retired — either set it active or queue work that "
                    "serves the active step")
        # THE MIDDLE TIER IS A GATE: work filed under a later goal is not planned
        # while an earlier goal's NUMBER is unmet. "Keep users before you sell to
        # them" then holds by construction, not by a sentence nobody re-reads.
        gid = goal_of(profile, mid)
        if gid and research is not None:
            for g in goal_tier(profile):
                if g["id"] == gid:
                    break
                ok, evidence = check_measures(g, research)
                if ok is False:
                    return (f"queue entry '{q.get('label')}' advances '{mid}', but "
                            f"the earlier goal '{g['title']}' is not met "
                            f"({evidence}) — the plan may not sell what it has not "
                            f"kept: finish that goal before working past it")

    # NOTHING IS RETIRED BY EXHAUSTION (owner directive 2026-11-09): a branch whose
    # tasks are all delivered but whose measures have no evidence is a GAP, not a
    # finish. Work may still go to that branch (to close it), or to another goal —
    # but the plan may not step OVER it. That is how a feature ships "done" while
    # the thing it promised was never proven. The refusal names the gap so the next
    # task can be written from its own words.
    try:
        from . import closure
        built_gaps = closure.gaps_from(profile, state, research)
    except Exception:
        built_gaps = []
    if built_gaps:
        order = [n["id"] for n in flat]
        gap_ids = {r["id"] for r in built_gaps}
        for q in (result.get("queue") or [])[:1]:
            mid = str(q.get("advances") or "").strip()
            if mid in gap_ids or mid not in order:
                continue
            gid = goal_of(profile, mid)
            for g in built_gaps:
                if goal_of(profile, g["id"]) != gid or g["id"] not in order:
                    continue
                if order.index(g["id"]) < order.index(mid):
                    return (f"queue entry '{q.get('label')}' advances '{mid}', but "
                            f"'{g['title']}' is built and NOT proven "
                            f"({g['missing'][0]}) — queue the gap against "
                            f"'{g['id']}' first, or correct that branch's measures "
                            f"with a goal change")
    return None


def goal_block(profile: dict, state: dict) -> str:
    """Owner-facing PLAN block (status/report). Purely derived, no LLM.

    Read as ORDERED LISTS (owner directive 2026-11-09): long-term goal, the
    sub-goals under it, the features under those, and the task queue — each
    numbered, because order IS priority. The tree diagram is gone; the links are
    explicit (`serves=`), so one feature may serve more than one sub-goal.
    """
    p = plan(profile)
    if not p:
        return ("PLAN: none yet — BLOCKING: nobody has said what this app is for "
                "long term, so no work is picked until the owner answers "
                "(`devteam vision <project> \"<one sentence>\"`).")
    st = status_of(profile, state)
    lines = [f'PLAN  the long-term goal: {p["vision"] or "(none)"}', ""]

    # "done" by exhaustion vs "done" with evidence (2026-11-09): a branch whose
    # tasks all shipped but whose measures have no proof is a GAP, and it marks
    # ⚠ above it too — a sub-goal cannot read done while a feature under it has
    # no evidence. Loaded once and used by both lists below.
    try:
        from . import closure as closure_mod
        _gaps = {r["id"]: r for r in closure_mod.gaps_from(profile, state, None)}
    except Exception:
        _gaps = {}

    def _goal_gap(gid: str) -> bool:
        return any(r["id"] == gid or goal_of(profile, r["id"]) == gid for r in _gaps.values())

    goals_list = goal_tier(profile)
    lines.append("SUB-GOALS  (why — in order)")
    if not goals_list:
        lines.append("  (none yet)")
    num: dict[str, int] = {}
    for i, g in enumerate(goals_list, 1):
        gid = str(g.get("id") or "")
        num[gid] = i
        mark = {"done": "✓", "active": ">", "pending": "·", "parked": "x"}.get(
            st.get(gid, "·"), "·")
        if gid in _gaps:
            mark = "⚠"
        line = f"  {i}. {mark} {g.get('title')}"
        if gid in _gaps:
            line += "   (built, NOT proven)"
        lines.append(line)
        for ln in _measure_lines(g, "       "):
            lines.append(ln)
    lines.append("")

    lines.append("FEATURES  (what we build — in order, top = next)")
    counter = {"n": 0}

    def emit(node: dict, serves: list[int], depth: int) -> None:
        counter["n"] += 1
        nid = str(node.get("id") or "")
        mark = {"done": "✓", "active": ">", "pending": "·", "parked": "x"}.get(
            st.get(nid, "·"), "·")
        if nid in _gaps:
            mark = "⚠"
        pad = "   " * depth
        line = f"  {pad}{counter['n']}. {mark} {node.get('title')}"
        if nid in _gaps:
            line += "   (built, NOT proven — the gap is the next task)"
        if serves:
            line += "   (serves " + ", ".join(str(s) for s in serves) + ")"
        lines.append(line)

    for top in p["milestones"]:
        if str(top.get("kind") or "") == GOAL_KIND:
            gid = str(top.get("id") or "")
            for c in top.get("children") or []:
                if isinstance(c, dict) and c.get("id"):
                    emit(c, [num[gid]] if gid in num else [], 0)
        else:
            emit(top, [], 0)
    if counter["n"] == 0:
        lines.append("  (none yet)")
    lines.append("")

    queue = state.get("queue") or []
    lines.append("TASKS  (what runs next — in order; only #1 ships per run)")
    if not queue:
        lines.append("  (empty — the next plan fills it)")
    for i, q in enumerate(queue[:6], 1):
        tag = "  ← NEXT RUN" if i == 1 else ""
        lines.append(f"  {i}. {q.get('label')}{tag}")
        prob = str(q.get("problem") or "").strip()
        if prob:
            lines.append(f"       problem: {prob}")
        dw = [str(x) for x in (q.get("done_when") or []) if str(x).strip()]
        if dw:
            lines.append("       done when: " + "; ".join(dw[:3]))
    if len(queue) > 6:
        lines.append(f"  … {len(queue) - 6} more queued")

    act = active_milestone(profile, state)
    if act:
        lines.append(f"  working on: {act['title']}")
    elif has_milestones(profile):
        lines.append("  working on: nothing — every feature is done; the team "
                     "should propose the next one")
    else:
        lines.append("  working on: no feature yet — the planner proposes the "
                     "first one under a sub-goal")
    pend = [e for e in (state.get("tree_changes") or []) if not e.get("reviewed")]
    if pend:
        lines.append(f"  the team changed the plan {len(pend)}x since you last "
                     f"looked — check it (`devteam goal <project> log`)")
    return "\n".join(lines)


def _measure_lines(node: dict, pad: str) -> list[str]:
    """A node's numbers, one line each — the only part of a feature code can check."""
    out: list[str] = []
    for x in node.get("measures") or []:
        if not isinstance(x, dict):
            continue
        if x.get("metric"):
            out.append(f"{pad}number: {x['metric']} {x.get('op', '<=')} "
                       f"{x.get('target')}")
        elif x.get("text"):
            out.append(f"{pad}number: {x['text']}")
    return out


def prompt_block(profile: dict, state: dict) -> str:
    """The PLAN section for the research synthesis prompt.

    The owner's shape (2026-11-09): read as ORDERED LISTS, not a tree diagram.
    A long-term goal, the sub-goals under it, the features under those, and the
    task list (the queue) — each numbered, because order IS priority. Links are
    ids (`serves`), so one feature may serve more than one sub-goal.
    """
    p = plan(profile)
    if not p:
        return ("PLAN: this project declares no long-term goal. Do not invent one; "
                "queue executable work as before, and if the app clearly needs a "
                "direction, propose the first feature via issues_to_file.")
    st = status_of(profile, state)
    act = active_milestone(profile, state)

    body = [f'LONG-TERM GOAL (owner): "{p["vision"]}"', ""]

    goals_list = goal_tier(profile)
    body.append("SUB-GOALS — the ENDS, in order (each carries ONE number; code "
                "refuses work under a later sub-goal while an earlier number fails):")
    if not goals_list:
        body.append("  (none yet — CODE inserts experience → retention → revenue)")
    num: dict[str, int] = {}
    for i, g in enumerate(goals_list, 1):
        gid = str(g.get("id") or "")
        num[gid] = i
        body.append(f"  {i}. {g.get('title')}   id={gid}  "
                    f"status={st.get(gid, 'pending')}")
        if g.get("outcome"):
            body.append(f"     what it means: {g['outcome']}")
        body += _measure_lines(g, "     ")
    body.append("")

    body.append("FEATURES — the MEANS, in order (a feature may serve more than one "
                "sub-goal; queue entries point at these ids):")
    counter = {"n": 0}

    def emit(node: dict, serves: list[int], depth: int) -> None:
        counter["n"] += 1
        nid = str(node.get("id") or "")
        pad = "   " * depth
        line = (f"  {pad}{counter['n']}. {node.get('title')}   id={nid}  "
                f"status={st.get(nid, 'pending')}")
        if serves:
            line += "  serves=" + ",".join(str(s) for s in serves)
        body.append(line)
        if node.get("outcome"):
            body.append(f"  {pad}   outcome: {node['outcome']}")
        body.extend(_measure_lines(node, f"  {pad}   "))
        for c in node.get("children") or []:
            if isinstance(c, dict) and c.get("id"):
                emit(c, serves, depth + 1)

    for top in p["milestones"]:
        if str(top.get("kind") or "") == GOAL_KIND:
            gid = str(top.get("id") or "")
            for c in top.get("children") or []:
                if isinstance(c, dict) and c.get("id"):
                    emit(c, [num[gid]] if gid in num else [], 0)
        else:
            emit(top, [], 0)
    if counter["n"] == 0:
        body.append("  (none yet — propose the first feature under a sub-goal)")
    body.append("")

    body.append("TASKS — you write this list as `queue`; order IS execution order "
                "(queue[0] is the only entry a run builds).")
    if act:
        body.append(f'ACTIVE FEATURE: {act["id"]} — aim the head at it')
    elif has_milestones(profile):
        body.append("ACTIVE FEATURE: none (every feature is done — retiring one "
                    "advances the pointer; proposing the next is an owner call)")
    else:
        body.append("ACTIVE FEATURE: none yet — the vision has no features. "
                    "YOUR JOB THIS CYCLE: propose the first one under a sub-goal "
                    "(goal_changes: action=add, kind=milestone, parent=<a sub-goal "
                    "id from the list above>, with an outcome and measures) and aim "
                    "the queue at it. Never ask the owner for features — you own "
                    "everything below the long-term goal. Give each SUB-GOAL its one "
                    "number (action=update, measures=[{metric, op, target}]) as soon "
                    "as a signal can read it.")
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
    if act:
        return f"goal: {act['title']}"
    # A vision with no milestones under it yet is NOT "all done" — the planner
    # still has to propose the first one (owner directive: the team proposes
    # milestones, the owner owns only the vision).
    if has_milestones(profile):
        return "goal: all milestones done — the next one needs the owner or the planner"
    return "goal: vision set, no milestone yet — the planner proposes the first one"


# --------------------------------------------------------------------------- #
# changing the tree (shared engine: `devteam goal` AND research's goal_changes)
# --------------------------------------------------------------------------- #

def _norm_title(title: str) -> str:
    """Titles compared for sameness: case/punctuation-insensitive."""
    import re as _re
    return _re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _slug(title: str, profile: dict) -> str:
    import re as _re
    slug = _re.sub(r"[^a-z0-9]+", "-", (title or "branch").lower()).strip("-")
    slug = slug or "branch"
    taken = {n["id"] for n in nodes(profile)}
    base, i = slug[:40], 2
    while slug in taken:
        slug = f"{base}-{i}"
        i += 1
    return slug


def apply_one(profile: dict, change: dict, by: str = "agent") -> tuple[str, str, str, str]:
    """Apply ONE tree change to profile['goals'] in place.

    Returns (node_id, what_happened_in_plain_words, why, error). `error` is ''
    when the change landed. This is the single engine behind `devteam goal ...`
    and the synthesizer's `goal_changes`, so both mutate the plan the same way —
    never through `plan()`/`nodes()` copies, which would silently lose the edit."""
    def good(nid: str, what: str, why: str) -> tuple[str, str, str, str]:
        return (nid, what, why, "")

    def bad(msg: str) -> tuple[str, str, str, str]:
        return ("", "", "", msg)

    action = str(change.get("action") or "").strip()
    why = str(change.get("why") or "").strip()
    if action == "add":
        title = str(change.get("title") or "").strip()
        if not title:
            return bad("a new branch needs a title (what it is for, in words)")
        parent = str(change.get("parent") or "vision")
        kind = str(change.get("kind") or
                   (GOAL_KIND if parent == "vision" else "subgoal"))
        if parent == "vision" and kind != GOAL_KIND:
            return bad("a branch directly under the vision is a GOAL (an end the "
                       "product must reach for users) — pass kind=\"goal\", or add "
                       "the milestone under an existing goal")
        kids = holder_list(profile, parent)
        if kids is None:
            return bad(f"no branch '{parent}' to add under "
                       f"(known: {[n['id'] for n in nodes(profile)]})")
        # MAINTENANCE, NOT REGENERATION: the planner re-reads the plan every run,
        # so an `add` for a branch that is ALREADY there (same title, same parent)
        # is a no-op, not a twin. Re-queueing under it still works because the
        # existing id comes back.
        for k in kids:
            if _norm_title(k.get("title")) == _norm_title(title):
                return good(str(k.get("id")),
                            f"already existed under {parent} (nothing added)", why)
        node_id = str(change.get("id") or _slug(title, profile))
        new = {"id": node_id, "kind": kind,
               "title": title, "decided_by": by,
               "status": str(change.get("status") or "active"),
               "why": why[:300]}
        if change.get("outcome"):
            new["outcome"] = str(change["outcome"])
        meas = list(change.get("measures") or [])
        if change.get("measure"):
            meas.append({"text": change["measure"]})
        if meas:
            new["measures"] = [
                m if isinstance(m, dict) else {"text": str(m)} for m in meas]
        kids.append(new)
        return good(node_id, f"added under {parent}", why)

    node_id = str(change.get("id") or "")
    raw, parent = raw_node(profile, node_id)
    if raw is None:
        return bad(f"no goal or sub-goal with id '{node_id}' "
                   f"(known: {[n['id'] for n in nodes(profile)]})")
    if action in ("park", "set-status"):
        st = str(change.get("status") or ("parked" if action == "park" else ""))
        if st not in ("active", "pending", "done", "parked", "proposed"):
            return bad("status must be one of: active, pending, done, parked, "
                       "proposed")
        if st == "proposed" and str(raw.get("decided_by") or "") != "agent":
            # 'proposed' only means something for agent-authored branches; keep
            # the distinction honest instead of silently accepting anything.
            return bad("'proposed' is for agent-authored branches; owner-set "
                       "goals use active/pending/done/parked")
        raw["status"] = st
        return good(node_id, "set aside" if st == "parked" else f"now {st}", why)
    if action == "retire":
        kids = holder_list(profile, parent or "vision") or []
        if raw in kids:
            kids.remove(raw)
        return good(node_id, "removed from the plan", why)
    if action == "update":
        # The planner owns the tree BELOW the vision: it may restate a branch's
        # outcome, or replace a text measure with the number now that a signal
        # exists. The vision itself is not a node here, so it stays the owner's.
        for key in ("title", "outcome"):
            if isinstance(change.get(key), str) and change[key].strip():
                raw[key] = change[key].strip()
        if change.get("measures") is not None:
            raw["measures"] = [m if isinstance(m, dict) else {"text": str(m)}
                               for m in change["measures"]]
        if change.get("measure"):
            raw.setdefault("measures", []).append({"text": str(change["measure"])})
        if change.get("status"):
            st = str(change["status"])
            if st not in ("active", "pending", "done", "parked", "proposed"):
                return bad("status must be one of: active, pending, done, parked, "
                           "proposed")
            raw["status"] = st
        return good(node_id, "updated", why)
    if action == "reparent":
        target = str(change.get("parent") or "")
        if raw_node(profile, target)[0] is None:
            return bad(f"no branch '{target}' to move it under")
        kids = holder_list(profile, parent or "vision") or []
        if raw in kids:
            kids.remove(raw)
        (holder_list(profile, target) or []).append(raw)
        return good(node_id, f"moved under {target}", why)
    return bad(f"unknown change '{action}' — use add, park, set-status, retire "
               "or reparent")


def validate_changes(result: dict) -> str:
    """Schema gate for a synthesis result's goal_changes (no mutation)."""
    changes = result.get("goal_changes") or []
    if not isinstance(changes, list):
        return "goal_changes must be a list"
    for i, c in enumerate(changes):
        if not isinstance(c, dict):
            return f"goal_changes[{i}] must be an object"
        if str(c.get("action") or "") not in ("add", "park", "set-status",
                                              "retire", "reparent", "update"):
            return (f"goal_changes[{i}] has action '{c.get('action')}' — use add, "
                    "park, set-status, retire, reparent or update")
        if not str(c.get("why") or "").strip():
            return (f"goal_changes[{i}] has no 'why' — the owner MUST be able to "
                    "read why the plan moved")
        if c.get("action") == "add" and not str(c.get("title") or "").strip():
            return f"goal_changes[{i}] add needs a title"
        if c.get("action") == "add":
            parent = str(c.get("parent") or "vision")
            kind = str(c.get("kind") or
                       (GOAL_KIND if parent == "vision" else "subgoal"))
            if parent == "vision" and kind != GOAL_KIND:
                return (f"goal_changes[{i}] adds a '{kind}' directly under the "
                        "vision — a top-level branch is a GOAL (kind=\"goal\"); "
                        "milestones sit under a goal")
    return ""


def change_ids(result: dict, profile: dict) -> list[str]:
    """Branches this result WOULD add, so its own queue entries can point at
    them in the same plan (validate-before-apply would otherwise reject them)."""
    out = []
    for c in result.get("goal_changes") or []:
        if isinstance(c, dict) and c.get("action") == "add":
            out.append(str(c.get("id") or _slug(str(c.get("title") or ""), profile)))
    return out


STATUS_WORDS = {
    "active": "In progress", "done": "Done", "pending": "Coming later",
    "proposed": "The team suggests this", "parked": "Set aside",
    "queued": "In the queue", "shipped": "Done", "unset": "Not set yet",
    "dropped": "Dropped", "next": "Next run",
    "history": "kept for the record",
}

KIND_WORDS = {"motion": "polish", "bugfix": "fix", "feature": "new feature",
              "investigation": "research"}


def pretty_metric(name: str) -> str:
    """`active_users_7d` → 'active users (last 7 days)'. Generic, no maps."""
    n = str(name or "")
    window = ""
    for suf, word in (("_7d", "last 7 days"), ("_30d", "last 30 days"),
                      ("_28d", "last 28 days"), ("_24h", "last 24 hours")):
        if n.endswith(suf):
            n, window = n[:-len(suf)], word
            break
    n = n.replace("_", " ").strip()
    return n + (f" ({window})" if window else "")


def nodes(profile: dict) -> list[dict]:
    """Every milestone/sub-goal as a flat list (id, parent, kind), depth-first —
    the tree can be arbitrarily deep: agents add branches wherever they fit."""
    out: list[dict] = []

    def walk(items: list, parent: str) -> None:
        for m in items or []:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            out.append({"id": str(m["id"]), "parent": parent,
                        "kind": str(m.get("kind") or "milestone"),
                        "title": str(m.get("title") or m["id"]),
                        "status": str(m.get("status") or ""),
                        "raw": m})
            walk(m.get("children") or [], str(m["id"]))

    p = plan(profile)
    walk((p or {}).get("milestones") or [], "vision")
    return out


def find_node(profile: dict, node_id: str) -> dict | None:
    for n in nodes(profile):
        if n["id"] == node_id:
            return n
    return None


def raw_node(profile: dict, node_id: str) -> tuple[dict | None, str | None]:
    """The ACTUAL dict inside profile['goals'] plus its parent's id.

    Mutations MUST land on the real object: `plan()`/`nodes()` hand out
    normalized COPIES (and synthesize an empty 'children' list when a branch has
    none), so writing to those silently loses the change — the bug this exists
    to prevent. Reads use `nodes()`; writes use this."""
    def walk(items: list, parent: str) -> tuple[dict | None, str | None]:
        for m in items or []:
            if not isinstance(m, dict):
                continue
            if str(m.get("id") or "") == node_id:
                return m, parent
            hit = walk(m.get("children") or [], str(m.get("id") or parent))
            if hit[0] is not None:
                return hit
        return None, None

    p = profile.get("goals") or {}
    return walk(p.get("milestones") or [], "vision")


def holder_list(profile: dict, parent: str) -> list | None:
    """The list a branch's children live in ('vision' = top-level milestones)."""
    if parent == "vision":
        return profile.setdefault("goals", {}).setdefault("milestones", [])
    node, _ = raw_node(profile, parent)
    if node is None:
        return None
    return node.setdefault("children", [])


def change(state: dict, action: str, node: str, why: str,
           before: str = "", after: str = "", by: str = "agent") -> dict:
    """Agents CHANGE the tree; the owner reviews afterwards. Every change is
    recorded with the reason and the previous value so it is reversible and
    visible — the review is an audit, not a permission gate."""
    e = {"at": now_iso(), "by": by, "action": action, "node": node,
         "before": before, "after": after, "why": (why or "")[:300],
         "reviewed": False}
    state.setdefault("tree_changes", []).append(e)
    return e


def review_block(state: dict) -> str:
    """Printed by status/record/tree: what the team changed and why, waiting
    only for the owner's scan (never for approval)."""
    allpend = [e for e in (state.get("tree_changes") or []) if not e.get("reviewed")]
    if not allpend:
        return ""
    pend = allpend[-5:]
    lines = [f"THE TEAM CHANGED THE PLAN ({len(allpend)} change"
             f"{'s' if len(allpend) != 1 else ''} since you last looked — no "
             "action needed, just check they look right):"]
    for i, e in enumerate(pend, 1):
        detail = ""
        if e.get("before") or e.get("after"):
            detail = f'   {e.get("before") or "—"} → {e.get("after") or "—"}'
        lines.append(f"  {i}. {e.get('action')} '{e.get('node')}'{detail}"
                     f"\n     why: {e.get('why')}  (by {e.get('by')}, "
                     f"{str(e.get('at'))[:10]})")
    lines.append("  disagree? `devteam goal <project> revert --last` undoes the "
                 "latest; `devteam goal <project> log` shows them all; "
                 "`... review` says you have seen them")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# alignment (LLM judgment — code only records and shows it)
# --------------------------------------------------------------------------- #

def alignment_block(state: dict, limit: int = 3) -> str:
    entries = (state.get("alignment_log") or [])[-limit:]
    if not entries:
        return ""
    lines = ["ALIGNMENT CHECK (team's judgment, recorded):"]
    for e in entries:
        lines.append(f"  · \"{str(e.get('request'))[:70]}\" → {e.get('verdict')}"
                     f" ({e.get('milestone') or 'no goal'}) — {str(e.get('why'))[:110]}")
    return "\n".join(lines)
