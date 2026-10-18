"""Goal TREE rendering — where the work is, in one view.

Source of truth stays the data (profile['goals'] + state.json); this module is
a deterministic RENDERER: same inputs → byte-identical output, no LLM, no
network. It produces two views:

  text  — `devteam tree <project>` prints it (and the run banner can quote it)
  html  — `.pipeline/goals.html`, a single self-contained file (no CDN, no
          external assets) the owner can open in a browser and click through:
          vision → milestones/sub-goals → the leaves each run actually picked.

Tree shape (nothing is fixed — branches can be added, parked, dropped,
reparented; only the human changes the root):

  vision (root, owner-only)          profile['goals']['vision']
  └─ milestone / sub-goal            profile['goals']['milestones'][*] (+ nested
                                     'children' for agent-proposed sub-goals)
     └─ task (a leaf = one run)      state['queue'] entries (advances) and
                                     state['shipped'] ledger rows (delivered)
"""
from __future__ import annotations

from .core import now_iso, pipeline_dir, profile_of, state_of
from . import adapt, goals

ACTIVE, DONE, PROPOSED, PARKED, OFF_GOAL = "active", "done", "proposed", "parked", "off-goal"


_OP_WORDS = {"<=": "at most", ">=": "at least", "<": "under", ">": "over",
             "==": "exactly"}


def _measures_view(ms: dict, research: dict | None) -> list[str]:
    """Plain language only: the owner reads these, so no metric ids, no symbols."""
    out = []
    ga4 = ((research or {}).get("signals", {}) or {}).get("ga4", {}) or {}
    for m in ms.get("measures") or []:
        if not isinstance(m, dict):
            continue
        if m.get("metric"):
            val = ga4.get(m["metric"])
            op = str(m.get("op", "<="))
            target = f'{_OP_WORDS.get(op, "around")} {m.get("target")}'
            if val is None:
                out.append(f'we will watch {goals.pretty_metric(m["metric"])} '
                           f'(wanted {target}) — no numbers back yet')
            else:
                ok = bool(goals._OPS.get(op, goals._OPS["<="])(val, m.get("target")))
                out.append(f'{goals.pretty_metric(m["metric"])} is {val} — '
                           f'{"where we want it" if ok else "not there yet"} '
                           f'(wanted {target})')
        elif m.get("text"):
            out.append(str(m["text"]))
    return out


def _task_node(label: str, target, advances: str, status: str,
               version: str = "", date: str = "", task_id: str = "",
               detail: str = "", kind: str = "") -> dict:
    return {"kind": "task", "id": label, "title": label, "status": status,
            "advances": advances, "target": target, "version": version,
            "date": date, "task_id": task_id, "detail": detail, "task_kind": kind}


def build(project) -> dict:
    """Deterministic tree model from profile goals + pipeline state."""
    profile, state = profile_of(project), state_of(project)
    try:
        import json
        research = json.loads((project / ".pipeline" / "research.json").read_text())
    except Exception:
        research = None
    plan = goals.plan(profile)
    st = goals.status_of(profile, state) if plan else {}
    verdicts: dict[str, dict] = {}
    for e in state.get("goal_log", []):
        if e.get("milestone"):
            verdicts[str(e["milestone"])] = e

    queue = [q for q in state.get("queue", [])]
    shipped = list(state.get("shipped", []))
    # Delivered leaves, de-duplicated by task label (the ledger stores one row
    # per touched target).
    delivered: dict[str, dict] = {}
    for s in shipped:
        key = str(s.get("label") or "")
        if key and key not in delivered:
            delivered[key] = s

    changes = [e for e in (state.get("tree_changes") or []) if not e.get("reviewed")][-5:]
    asks = list((state.get("adapted") or {}).get("asks") or [])

    root = {
        "kind": "vision", "id": "vision",
        "title": (plan or {}).get("vision") or "Nobody has said what this is for yet",
        "status": "active" if plan else "unset",
        "decided_by": "owner", "children": [],
    }
    if not plan:
        root["note"] = ("This app has no long-term goal yet. Tell the team one "
                        "sentence about what it is for and it becomes the top of "
                        "this page. Until then the team keeps picking from what it "
                        "already knows (goal-less mode).")
        # everything still gets shown, honestly, under a pre-goal branch
        pre = {"kind": "branch", "id": "pre-goal",
               "title": "Earlier work — done before we set any direction",
               "status": "history", "decided_by": "agent", "children": []}
        for q in queue:
            pre["children"].append(_task_node(
                str(q.get("label")), q.get("target"), "", "queued",
                detail=str(q.get("reason") or "")[:160],
                kind=str(q.get("type") or "")))
        for lbl, s in delivered.items():
            pre["children"].append(_task_node(
                lbl, s.get("target"), "", "shipped", s.get("version", ""),
                s.get("date", ""), kind=""))
        if pre["children"]:
            root["children"].append(pre)
        _annotate(root)
        return {"root": root, "generated_at": now_iso(), "changes": changes,
                "asks": asks, "goal_less": True}

    def attach(leaf: dict, mid: str) -> None:
        # anywhere in the tree — a leaf can hang off a sub-goal just as well as
        # off a top-level goal
        if mid in index:
            index[mid]["children"].append(leaf)
            return
        for existing in root["children"]:
            if existing["kind"] == "branch" and existing["id"] == (mid or "unattributed"):
                existing["children"].append(leaf)
                return
        root["children"].append({"kind": "branch", "id": mid or "unattributed",
                                 "title": (mid or "Earlier work — from before we "
                                           "wrote this plan down"),
                                 "status": "history", "decided_by": "—",
                                 "children": [leaf]})

    def make_node(raw: dict, kind: str) -> dict:
        mid = str(raw["id"])
        node = {"kind": kind, "id": mid,
                "title": str(raw.get("title") or mid),
                "status": ((st.get(mid, "pending") if kind == "milestone" else "")
                           or str(raw.get("status") or "")
                           or (PROPOSED if kind != "milestone" else "pending")),
                "decided_by": str(raw.get("decided_by") or "owner"),
                "outcome": str(raw.get("outcome") or ""),
                "measures": _measures_view(raw, research),
                "verdict": (verdicts.get(mid) or {}).get("verdict", ""),
                "evidence": (verdicts.get(mid) or {}).get("evidence", ""),
                "children": []}
        for sub in raw.get("children") or []:
            if isinstance(sub, dict) and sub.get("id"):
                node["children"].append(make_node(sub, "subgoal"))
        return node

    nodes: list[dict] = []
    for m in plan["milestones"]:
        node = make_node(m, "milestone")
        nodes.append(node)
        root["children"].append(node)

    def _index(n: dict) -> dict:
        out = {n["id"]: n}
        for c in n.get("children") or []:
            out.update(_index(c))
        return out

    index: dict[str, dict] = {}
    for n in root["children"]:
        index.update(_index(n))

    queued_labels = set()
    next_marked = [False]
    for order, q in enumerate(queue):
        label = str(q.get("label") or "")
        queued_labels.add(label)
        ship = delivered.get(label)
        leaf = _task_node(label, q.get("target"),
                          str(q.get("advances") or ""),
                          "shipped" if ship else "queued",
                          ship.get("version", "") if ship else "",
                          ship.get("date", "") if ship else "",
                          detail=str(q.get("reason") or "")[:160],
                          kind=str(q.get("type") or ""))
        leaf["order"] = order
        if not ship and not next_marked[0]:
            leaf["is_next"] = True   # deterministic: the queue head is next run
            next_marked[0] = True
        attach(leaf, str(q.get("advances") or ""))
    for lbl, s in delivered.items():
        if lbl in queued_labels:
            continue
        # Delivered work whose queue row is gone: it belongs to the milestone it
        # was tagged with at ship time, else to pre-goal history.
        attach(_task_node(lbl, s.get("target"), s.get("advances", ""), "shipped",
                          s.get("version", ""), s.get("date", ""),
                          s.get("task_id", "")),
               str(s.get("advances") or ""))
    _annotate(root)
    return {"root": root, "generated_at": now_iso(), "changes": changes,
            "asks": asks, "goal_less": False}


def _annotate(node: dict) -> tuple[int, int]:
    """Count built vs planned leaves under every branch (progress bars).
    Deterministic; a branch with no leaves gets no bar rather than a fake 0%."""
    done = total = 0
    for c in node.get("children") or []:
        if c.get("kind") == "task":
            total += 1
            done += 1 if c.get("status") == "shipped" else 0
        else:
            d, t = _annotate(c)
            done, total = done + d, total + t
    if node.get("kind") != "task" and total:
        node["bar"] = round(100 * done / total)
        node["progress"] = (f"{done} of {total} built"
                            + (" — all of them" if done == total else ""))
    return done, total


def render_text(model: dict) -> str:
    """The same tree for the terminal: plain words, no ids, no jargon."""
    lines: list[str] = []

    def walk(node: dict, depth: int) -> None:
        pad = "   " * depth
        kind = node.get("kind")
        status = node.get("status", "")
        who = "you" if node.get("decided_by") == "owner" else (
            "team" if node.get("decided_by") == "agent" else "")
        if kind == "vision":
            lines.append(f"{pad}THE GOAL:  {node['title']}")
        elif kind == "task":
            mark = "✓" if status == "shipped" else "." if node.get("is_next") else "·"
            tail = (f"built in v{node['version']}" if node.get("version")
                    else "NEXT RUN" if node.get("is_next") else "in the queue")
            lines.append(f"{pad}{mark} {node['title']}   ({tail})")
        else:
            mark = {"active": ">", "done": "✓", "pending": "·", "proposed": "?",
                    "parked": "x", "history": "o"}.get(status, "·")
            tail = goals.STATUS_WORDS.get(status, status)
            if who:
                tail += f", set by {who}"
            if node.get("progress"):
                tail += f" · {node['progress']}"
            lines.append(f"{pad}{mark} {node['title']}   ({tail})")
        if node.get("note"):
            lines.append(f"{pad}   {node['note']}")
        if node.get("outcome"):
            lines.append(f"{pad}   what it means: {node['outcome']}")
        for m in node.get("measures") or []:
            lines.append(f"{pad}   how we'll know: {m}")
        for child in node.get("children") or []:
            walk(child, depth + 1)

    lines.append("THE PLAN — you set the goal at the top; the team fills in "
                 "everything under it")
    lines.append("")
    walk(model["root"], 0)
    lines.append("")
    lines.append(f"(each run builds one item from the bottom; rebuilt "
                 f"{model['generated_at'][:16].replace('T', ' ')} — `devteam tree "
                 f"<project> --open` opens the clickable page)")
    return "\n".join(lines)


_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — where we are going</title>
<style>
 :root {{ --bg:#fcfbf9; --ink:#1b1e24; --soft:#6c7480; --line:#e7e3dc;
          --good:#1f9d63; --now:#2f6fed; }}
 * {{ box-sizing:border-box; }}
 body {{ max-width:820px; margin:0 auto; padding:42px 22px 90px; background:var(--bg);
        color:var(--ink); font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",
        Roboto,sans-serif; -webkit-font-smoothing:antialiased; }}
 h1 {{ font-size:24px; margin:0 0 6px; letter-spacing:-.015em; }}
 .sub {{ color:var(--soft); font-size:14px; margin:0 0 30px; }}
 h2 {{ font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.09em;
       color:var(--soft); margin:36px 0 12px; }}
 .count {{ color:var(--soft); font-weight:400; letter-spacing:0; text-transform:none; }}
 .goal {{ background:#fff; border:1px solid var(--line); border-left:3px solid var(--now);
         border-radius:10px; padding:19px 21px; font-size:19px; font-weight:600; line-height:1.45; }}
 .goal .who {{ display:block; font-size:12px; font-weight:500; color:var(--soft);
               text-transform:uppercase; letter-spacing:.07em; margin-bottom:7px; }}
 .row {{ display:flex; gap:12px; align-items:flex-start; background:#fff;
         border:1px solid var(--line); border-radius:10px; padding:13px 16px; margin-bottom:8px; }}
 .row.quiet {{ background:#fdfdfc; }}
 .row.done {{ background:transparent; border-color:transparent; padding:7px 16px;
              margin-bottom:0; }}
 .row .n {{ color:var(--soft); font-variant-numeric:tabular-nums; min-width:18px; }}
 .row .body {{ flex:1; min-width:0; }}
 .t {{ font-weight:600; }}
 .row.done .t {{ font-weight:400; color:#3c434e; }}
 .m {{ color:var(--soft); font-size:14px; margin-top:3px; }}
 .m b {{ color:#4b5461; font-weight:600; }}
 .dot {{ width:8px; height:8px; border-radius:50%; flex:0 0 auto; margin-top:8px;
         background:#cfd4db; }}
 .row.done .dot {{ background:var(--good); }}
 .dot.now {{ background:var(--now); }}
 .pill {{ font-size:12px; border-radius:999px; padding:2px 10px; white-space:nowrap;
          background:#f2f1ef; color:#6c7480; }}
 .pill.now {{ background:#eaf1ff; color:#2050c8; }}
 .pill.done {{ background:#eaf7f0; color:#177a4c; }}
 .bar {{ height:5px; background:#eeebe5; border-radius:99px; overflow:hidden;
         max-width:260px; margin:9px 0 4px; }}
 .bar > i {{ display:block; height:100%; background:var(--good); }}
 .ask {{ background:#fff8ec; border:1px solid #f2e0c2; border-radius:10px;
         padding:14px 17px; margin-bottom:9px; }}
 .change {{ background:#f4f7ff; border:1px solid #dde6fb; border-radius:10px;
            padding:14px 17px; margin-bottom:26px; }}
 details {{ margin-top:7px; }}
 summary {{ cursor:pointer; color:var(--soft); font-size:13px; }}
 summary::marker {{ color:#c3c8d0; }}
 .inner {{ color:var(--soft); font-size:13px; margin-top:7px; }}
 .inner li {{ margin:0 0 5px; }}
 .empty {{ color:var(--soft); font-size:14px; background:#fff; border:1px dashed var(--line);
           border-radius:10px; padding:14px 16px; }}
 .foot {{ color:var(--soft); font-size:13px; margin-top:44px; border-top:1px solid var(--line);
          padding-top:16px; }}
 code {{ background:#f4f2ee; padding:1px 5px; border-radius:4px; font-size:13px; }}
</style></head><body>
<h1>{h1}</h1>
<p class="sub">{lede}</p>
{body}
<p class="foot">{foot}</p>
</body></html>
"""


def _collect(model: dict) -> dict:
    """One pass over the tree model → the page's business sections.

    The recursive model stays the single source (the terminal view walks the same
    object); this only regroups it into what a non-engineer reads: the plan, the
    queue in build order, what is done, and what sits before the plan existed."""
    branches: list[dict] = []
    queue: list[dict] = []
    done: list[dict] = []
    earlier: list[dict] = []

    def walk(n: dict, depth: int, path: list[str]) -> None:
        kind = n.get("kind")
        here = path + ([n["title"]] if kind != "vision" else [])
        if kind in ("milestone", "subgoal", "branch") and n.get("status") != "history":
            # "Earlier work" is history, not a plan item: its leaves belong to the
            # done list, and counting it as a goal would inflate the plan.
            branches.append({**n, "depth": depth, "path": path})
        for c in n.get("children") or []:
            if c.get("kind") != "task":
                walk(c, depth + (0 if kind == "vision" else 1), here)
                continue
            rec = {**c, "for": here[-1] if here else "",
                   "in_history": n.get("status") == "history"}
            # WHAT SHIPPED IS DONE, WHAT IS QUEUED IS WAITING — position in the
            # tree never decides that. (Filing pending work under "done" because
            # it sat in the pre-goal bucket is how a project looks finished with
            # six items still waiting.)
            if c.get("status") == "shipped":
                done.append(rec)
            else:
                queue.append(rec)

    walk(model["root"], 0, [])
    for rec in queue + done + earlier:
        rec["title"] = _humanize(rec.get("title"))
    queue.sort(key=lambda r: r.get("order", 0))
    done.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    return {"branches": branches, "queue": queue, "done": done, "earlier": earlier}


def _short(title: str, n: int = 42) -> str:
    """The branch label repeated on six queue rows must not be the six-verb
    sentence it is in the plan: first clause only (the full text stays in the
    technical detail)."""
    t = str(title or "")
    for sep in (" — ", " – ", ": ", " ("):
        if sep in t:
            t = t.split(sep, 1)[0]
            break
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def _humanize(label: str) -> str:
    """Old ledger rows sometimes recorded an internal slug as the label
    (`ux-ax1-first-run-value`). Show it readably rather than as a code — the page
    is for the owner; the raw label stays in the technical detail."""
    t = str(label or "")
    if " " not in t and ("-" in t or "_" in t):
        t = t.replace("-", " ").replace("_", " ").strip()
        return t[:1].upper() + t[1:]
    return t


def _details(summary: str, items: list[str]) -> str:
    if not items:
        return ""
    body = "".join(f"<li>{_esc(i)}</li>" for i in items)
    return (f'<details><summary>{_esc(summary)}</summary>'
            f'<div class="inner"><ul>{body}</ul></div></details>')


def _body(model: dict) -> str:
    root = model["root"]
    col = _collect(model)
    out: list[str] = []

    # 1. the team's own note: it changed the plan (never a request for approval)
    changes = model.get("changes") or []
    if changes:
        items = [f'{e.get("action")} — {e.get("why")} ({str(e.get("at"))[:10]}, '
                 f'by {e.get("by")})' for e in changes]
        out.append(f'<div class="change"><b>The team changed the plan '
                   f'{len(changes)}x since you last looked.</b> Nothing is waiting '
                   f'on you — this is so you can disagree:'
                   + _details("show the changes", items) + "</div>")

    # 2. the goal — the one thing the owner writes
    goal = root["title"]
    if model.get("goal_less") or not (root.get("children") or []):
        out.append('<h2>The goal</h2><div class="goal">'
                   '<span class="who">you set this — nobody else may change it</span>'
                   f'{_esc(goal)}</div>')
        if root.get("note"):
            out.append(f'<div class="empty" style="margin-top:10px">'
                       f'{_esc(root["note"])}</div>')
    else:
        out.append('<h2>The goal <span class="count">yours — nobody else changes '
                   'it</span></h2>')
        out.append(f'<div class="goal">{_esc(goal)}</div>')

    # 3. what needs the owner before the plan is worth reading
    asks = model.get("asks") or []
    if asks:
        out.append('<h2>Waiting on you <span class="count">'
                   f'{len(asks)}</span></h2>')
        for a in asks:
            what, how = adapt.ask_text(a)
            body = _esc(what) + (_details("how to fix it", [how]) if how else "")
            out.append(f'<div class="ask">{body}</div>')

    # 4. the plan: the goals under it, in order
    if col["branches"]:
        working = [b for b in col["branches"] if b.get("status") == "active"]
        out.append(f'<h2>The plan <span class="count">{len(col["branches"])} '
                   f'goal{"s" if len(col["branches"]) != 1 else ""}, '
                   f'{len(working)} in progress</span></h2>')
        for b in col["branches"]:
            status = b.get("status", "")
            if status == "active":
                pill, dot = "current", "now"
            elif b.get("status") == "done" or b.get("progress", "").endswith("all of them"):
                pill, dot = "done", "done"
            else:
                pill, dot = "", ""
            who = ("you" if b.get("decided_by") == "owner"
                   else "the team" if b.get("decided_by") == "agent" else "")
            meta = []
            if b.get("outcome"):
                meta.append(f'<div class="m">{_esc(b["outcome"])}</div>')
            if b.get("bar") is not None:
                meta.append(f'<div class="bar"><i style="width:{int(b["bar"])}%"></i>'
                            f'</div><div class="m">{_esc(b.get("progress", ""))}</div>')
            if b.get("measures"):
                meta.append(_details("how we will know it is done", b["measures"]))
            tech = ([f'id: {b["id"]}', f'set by: {who or "—"}']
                    + [f'status: {b.get("status")}'])
            meta += [_details("technical detail", tech)]
            style = f' style="margin-left:{min(int(b.get("depth") or 0), 3) * 26}px"'
            out.append(f'<div class="row quiet"{style}><span class="dot {dot}"></span>'
                       f'<div class="body"><div class="t">{_esc(b["title"])}'
                       + (f' <span class="pill {pill}">{_esc(goals.STATUS_WORDS.get(b.get("status"), b.get("status")))}</span>' if pill else "")
                       + "</div>" + "".join(meta) + "</div></div>")

    # 5. the queue — what gets built, in order, one per night
    out.append('<h2>The queue <span class="count">'
               + (f'{len(col["queue"])} waiting — one is built per night, top first'
                  if col["queue"] else "empty — the team refills it before the next run")
               + "</span></h2>")
    if not col["queue"]:
        out.append('<div class="empty">Nothing waiting right now.</div>')
    for i, leaf in enumerate(col["queue"], 1):
        why = ([leaf["detail"]] if leaf.get("detail") else [])
        tech = [f'type: {leaf.get("task_kind") or "—"}',
                f'serves: {leaf.get("for") or "—"}',
                f'goal id: {leaf.get("advances") or "—"}',
                f'target: {leaf.get("target")}']
        out.append(f'<div class="row"><span class="n">{i}</span>'
                   f'<div class="body"><div class="t">{_esc(leaf["title"])}'
                   + (f' <span class="pill now">next</span>' if leaf.get("is_next") else "")
                   + "</div><div class=\"m\">"
                   + (f'for: <b>{_esc(_short(leaf.get("for")))}</b>'
                      if leaf.get("for") and not leaf.get("in_history")
                      else "not tied to a goal yet (none is set)")
                   + "</div>"
                   + _details("why the team picked this", why)
                   + _details("technical detail", tech)
                   + "</div></div>")

    # 6. what is already done
    if col["done"] or col["earlier"]:
        total = len(col["done"]) + len(col["earlier"])
        out.append(f'<h2>Already done <span class="count">{total} shipped</span></h2>')
        for leaf in col["done"] + col["earlier"]:
            tech = [f'serves: {leaf.get("for") or "—"}',
                    f'task: {leaf.get("task_id") or leaf.get("title")}',
                    f'target: {leaf.get("target")}']
            if leaf.get("detail"):
                tech.append(f'why: {leaf["detail"]}')
            note = (" — done before this plan existed"
                    if leaf.get("in_history") else "")
            out.append(f'<div class="row done"><span class="dot done"></span>'
                       f'<div class="body"><div class="t">{_esc(leaf["title"])}'
                       + (f' <span class="pill done">v{_esc(leaf.get("version"))}</span>'
                          if leaf.get("version") else "")
                       + f'</div><div class="m">{_esc(leaf.get("date") or "")}'
                       f'{_esc(note)}'
                       + _details("technical detail", tech) + "</div></div></div>")

    return "".join(out)


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_html(model: dict, title: str) -> str:
    batched = ("The goal you set, the team's plan under it, and the queue a nightly "
               "run works through from the top. Technical detail is behind a click.")
    foot = ("Rebuilt automatically every run from the team's own records in this "
            "project. Disagree with anything? Tell the team — it changes, and you "
            f"are told — or run <code>devteam goal &lt;project&gt; revert</code>. "
            "Everything on this page, including the technical detail behind each "
            "item, is generated by code: no LLM writes it and nothing here is "
            "tuned for looks.")
    return _HTML.format(title=_esc(title),
                        h1=f"{_esc(title)} — where we are going",
                        lede=batched, foot=foot, body=_body(model))


def url(project) -> str:
    """The goal page as a `file://` URL (clickable in a terminal, pasteable into
    a browser). Printed after EVERY change and on EVERY run, so the owner is one
    paste away from the current picture — see cli.print_page()."""
    return (pipeline_dir(project) / "goals.html").as_uri()


def write(project, open_after: bool = False) -> str:
    """Render both views into the project's .pipeline dir; returns the html path."""
    profile = profile_of(project)
    model = build(project)
    (project / ".pipeline" / "goals.txt").write_text(
        render_text(model) + "\n\nwhere we are going: " + url(project) + "\n")
    dest = project / ".pipeline" / "goals.html"
    dest.write_text(render_html(model, profile.get("name", "project")))
    if open_after:
        import subprocess
        subprocess.run(["open", str(dest)], check=False)
    return str(dest)
