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

from .core import now_iso, path_items, paths_overlap

_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "==": lambda a, b: a == b}


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


def descends_from(profile: dict, node_id: str, ancestor_id: str) -> bool:
    """Is `node_id` the ancestor itself, or a branch under it?
    A milestone's PLAN is delivered by work queued anywhere in its sub-tree, so
    'is this part of that milestone's series' is a tree question, not a tag
    comparison."""
    cur, seen = node_id, set()
    while cur and cur not in seen:
        if cur == ancestor_id:
            return True
        seen.add(cur)
        n = find_node(profile, cur)
        cur = str(n["parent"]) if n else ""
    return False


def milestone_ancestor(profile: dict, node_id: str) -> str:
    """The top-level milestone `node_id` sits under (itself, when it already IS
    one). The tree hangs off 'vision', so the first ancestor whose parent is
    'vision' is the milestone — arbitrarily deep sub-goals collapse onto it, which
    is exactly the grouping a release-candidate call needs."""
    cur, seen = node_id, set()
    while cur and cur not in seen:
        seen.add(cur)
        n = find_node(profile, cur)
        if not n:
            return ""
        if str(n["parent"]) == "vision":
            return cur
        cur = str(n["parent"])
    return ""


def _clip(s: str, n: int = 70) -> str:
    """A label shortened on a word boundary, for one-line owner-facing reasons."""
    s = str(s)
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def rc_call(profile: dict, state: dict, entry: dict,
            shipped_label: str = "", shipped_task_id: str = "") -> dict:
    """THE release-candidate call — computed by code from the plan, not asked.

    LENIENT BY DEFAULT (owner directive 2026-10-24, second pass): *"if something
    can be shipped it should [be a release candidate], even if it is part 2/7 …
    let's say 5 tasks were creating 1 setting per task, if shipping 2 settings
    only makes sense then it is release candidate."* So position in the plan does
    NOT decide it. What decides it is whether the shipped increment STANDS ALONE:
    a task in this pipeline is required to be independently verifiable and
    releasable, so the normal case is a coherent slice the owner could promote —
    being the 2nd of 7 parts is a fact worth reporting, never a reason to withhold.

    The ONE thing that withholds the call is evidence the change is still in
    flight: a part still queued for the same milestone that edits the SAME FILES
    as the one just shipped. Five settings in five files are five independent
    increments (candidate at 2 of 5); a shared component plus the screens that
    consume it are one change in pieces (not a candidate until the consumers
    land). Paths are compared segment-wise via `core.paths_overlap`, and when the
    shipped files are unknown the call is lenient — absence of evidence is not
    evidence of half-done work.

    Returns {rc, why, part, total, scope, scope_title, withheld_by} — stored per
    version by `record` so the call cannot drift as the plan moves on."""
    act = active_milestone(profile, state)
    mid = queue_entry_milestone(entry, profile, state)
    if mid == "none":
        return {"rc": False, "part": 0, "total": 0, "scope": "", "scope_title": "",
                "withheld_by": [],
                "why": ("off-goal work (the entry declared 'advances: none') — it "
                        "completes no milestone, so no build of it is a candidate")}
    # The series is the TASK'S OWN MILESTONE, found by walking up the tree — a
    # sub-goal's parent, not the tag itself.
    scope = milestone_ancestor(profile, mid) if mid else ""
    if not scope:
        scope = act["id"] if act else ""
    title = (find_node(profile, scope) or {}).get("title", scope) if scope else ""

    def in_scope(e: dict) -> bool:
        m = str(e.get("advances") or "").strip()
        if m == "none" or not scope:
            return False
        return descends_from(profile, m, scope)

    if not scope:
        return {"rc": False, "part": 0, "total": 0, "scope": "", "scope_title": "",
                "withheld_by": [],
                "why": ("no milestone to complete — the app has no goal tree, so a "
                        "build cannot be 'the build' for anything")}

    # Parts of this milestone's plan already shipped (by task_id; labels are the
    # fallback for pre-task_id rows), with THIS ship removed then added back — so
    # the count is right whether or not the caller already appended its row.
    done_ids, done_labels = set(), set()
    for s in state.get("shipped") or []:
        if not in_scope(s):
            continue
        tid = str(s.get("task_id") or "")
        if tid:
            done_ids.add(tid)
        elif s.get("label"):
            done_labels.add(str(s["label"]))
    tid_now = str(shipped_task_id or entry.get("id") or "")
    done_ids.discard(tid_now)
    if shipped_label:
        done_labels.discard(shipped_label)
    done = len(done_ids) + len(done_labels) + 1

    # Parts of the same milestone's plan still waiting (a plan can lag a delivery,
    # so the just-shipped entry is excluded explicitly by label and task id).
    left = [e for e in (state.get("queue") or [])
            if in_scope(e) and str(e.get("label") or "") != shipped_label
            and not (tid_now and str(e.get("task_id") or "") == tid_now)]
    total = done + len(left)

    # What this release touched, compared against what the remaining parts touch.
    shipped_paths = path_items((entry.get("surface", {}) or {}).get("targets"))
    if not shipped_paths:
        # A recompute from a ledger row has no targets: recover them from the
        # queue entry carrying the same label (a stale plan often still holds it).
        for q in state.get("queue") or []:
            if str(q.get("label") or "") == shipped_label:
                shipped_paths = path_items(q.get("target"))
                break
    withheld_by = []
    if shipped_paths:
        for e in left:
            if any(paths_overlap(sp, lp)
                   for sp in shipped_paths for lp in path_items(e.get("target"))):
                withheld_by.append(str(e.get("label") or "?"))

    if withheld_by:
        n_hold = len(withheld_by)
        preview = "; ".join(_clip(l) for l in withheld_by[:2]) + (" …" if n_hold > 2 else "")
        verb = "edits" if n_hold == 1 else "edit"
        return {"rc": False, "scope": scope, "scope_title": title, "part": done,
                "total": total, "withheld_by": withheld_by,
                "why": (f"this is part of a change still in flight — "
                        f"{n_hold} more part{'s' if n_hold != 1 else ''} of the plan "
                        f'for "{title}" {verb} the same files: {preview}')}

    if not left:
        return {"rc": True, "scope": scope, "scope_title": title, "part": done,
                "total": total, "withheld_by": [],
                "why": (f'this completes the plan for "{title}" — nothing else is '
                        f"queued for it (part {done} of {total})")}

    n_left = f"{len(left)} part{'s' if len(left) != 1 else ''}"
    preview = "; ".join(_clip(str(e.get("label") or "?")) for e in left[:2]) \
        + (" …" if len(left) > 2 else "")
    return {"rc": True, "scope": scope, "scope_title": title, "part": done,
            "total": total, "withheld_by": [],
            "why": (f'this increment stands on its own (part {done} of {total} of '
                    f"the plan for \"{title}\") — the remaining {n_left} touch other "
                    f"surfaces: {preview}")}


def validate_advances(result: dict, profile: dict, state: dict,
                      extra_ids: tuple = ()) -> str | None:
    """Deterministic gate on the research plan: with a goal tree defined, every
    queue entry must declare the milestone it advances ('none' is allowed but
    must also be listed in off_goal so the drift is visible and recorded).

    `extra_ids` are branches the SAME result is about to add (goal_changes), so a
    synthesis can create a branch and queue work for it in one plan."""
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
        if mid in shelved:
            return (f"queue entry '{q.get('label')}' advances '{mid}', which is "
                    "parked/retired — either set it active or queue work that "
                    "serves the active step")
    return None


def goal_block(profile: dict, state: dict) -> str:
    """Owner-facing PLAN block (status/report). Purely derived, no LLM. Plain
    words: no ids, no bracket codes — those live in the JSON and `goal log`."""
    p = plan(profile)
    if not p:
        return ("PLAN: none yet — BLOCKING: nobody has said what this app is for "
                "long term, so no work is picked until the owner answers "
                "(`devteam vision <project> \"<one sentence>\"`).")
    st = status_of(profile, state)
    lines = [f'PLAN  the goal: {p["vision"] or "(none)"}']
    for m in p["milestones"]:
        mark = {"done": "✓", "active": ">", "pending": "·", "parked": "x"}.get(
            st[m["id"]], "·")
        extra = " — set aside" if str(m.get("status")) == "parked" else ""
        lines.append(f"  {mark} {m['title']}{extra}")
    act = active_milestone(profile, state)
    if act:
        lines.append(f"  working on: {act['title']}")
    elif p["milestones"]:
        lines.append("  working on: nothing — every goal is done; the team should "
                     "propose the next one")
    else:
        lines.append("  working on: no milestone yet — the planner proposes the "
                     "first one under your goal (you own only the goal itself)")
    pend = [e for e in (state.get("tree_changes") or []) if not e.get("reviewed")]
    if pend:
        lines.append(f"  the team changed the plan {len(pend)}x since you last "
                     f"looked — check it (`devteam goal <project> log`)")
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
    if act:
        body.append(f'ACTIVE MILESTONE: {act["id"]}')
    elif p["milestones"]:
        body.append("ACTIVE MILESTONE: none (every milestone is done — retiring one "
                    "advances the pointer; proposing the next is an owner call)")
    else:
        body.append("ACTIVE MILESTONE: none yet — the vision has no milestones. "
                    "YOUR JOB THIS CYCLE: propose the first one (goal_changes: "
                    "action=add, kind=milestone, parent=vision, with an outcome and "
                    "measures) and aim the queue at it. Never ask the owner for "
                    "milestones — you own the tree below the vision.")
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
    if plan(profile)["milestones"]:
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
        new = {"id": node_id, "kind": str(change.get("kind") or "subgoal"),
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
                                              "retire", "reparent"):
            return (f"goal_changes[{i}] has action '{c.get('action')}' — use add, "
                    "park, set-status, retire or reparent")
        if not str(c.get("why") or "").strip():
            return (f"goal_changes[{i}] has no 'why' — the owner MUST be able to "
                    "read why the plan moved")
        if c.get("action") == "add" and not str(c.get("title") or "").strip():
            return f"goal_changes[{i}] add needs a title"
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
