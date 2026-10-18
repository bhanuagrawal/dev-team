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

from .core import now_iso, profile_of, state_of
from . import goals

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
                "goal_less": True}

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
    for q in queue:
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
            "goal_less": False}


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
 :root {{ --bg:#fbfaf8; --ink:#1d2027; --soft:#6b7280; --line:#e3e0da;
          --card:#ffffff; --good:#1f9d63; --now:#2f6fed; --soon:#9aa3b2;
          --agent:#8a5cf6; --owner:#c2410c; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; padding:34px 20px 70px; background:var(--bg); color:var(--ink);
        font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
 .wrap {{ max-width:1000px; margin:0 auto; }}
 h1 {{ font-size:22px; margin:0 0 6px; letter-spacing:-.01em; }}
 .lede {{ color:var(--soft); margin:0 0 26px; }}
 .level {{ margin:0 0 10px; color:var(--soft); font-size:12px;
           text-transform:uppercase; letter-spacing:.08em; }}
 ul {{ list-style:none; margin:0; padding:0; }}
 li {{ margin:0 0 10px; }}
 .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:13px 16px; box-shadow:0 1px 2px rgba(0,0,0,.03); }}
 .card .head {{ display:flex; align-items:center; gap:9px; flex-wrap:wrap; }}
 .card h3 {{ font-size:16px; margin:0; font-weight:600; }}
 .kinder {{ font-size:12px; }} 
 .why {{ color:var(--soft); margin-top:6px; }}
 .fact {{ color:var(--soft); font-size:13px; margin-top:5px; }}
 .dot {{ width:9px; height:9px; border-radius:50%; flex:0 0 auto; }}
 .now .dot {{ background:var(--now); }} .done .dot {{ background:var(--good); }}
 .soon .dot {{ background:var(--soon); }} .aside .dot {{ background:#c9c5bd; }}
 .suggested .dot {{ background:var(--agent); }}
 .now .card {{ border-color:#bcd0fb; background:#f7faff; }}
 .done .card {{ background:#fbfdfb; }}
 .soon .card {{ background:#fdfdfd; }}
 .aside .card {{ background:#faf9f7; border-style:dashed; }}
 .suggested .card {{ border-color:#dcd0fb; background:#fbf8ff; }}
 .tag {{ font-size:11px; border:1px solid var(--line); border-radius:999px;
         padding:2px 9px; color:var(--soft); background:#fff; }}
 .tag.you {{ color:var(--owner); border-color:#f0d3c2; }}
 .tag.team {{ color:var(--agent); border-color:#ddd1fb; }}
 .tag.state {{ color:var(--now); border-color:#cddcfb; }}
 .children {{ border-left:2px dashed var(--line); margin:12px 0 4px 20px;
              padding:12px 0 0 18px; }}
 .children > .level {{ margin-bottom:8px; }}
 .old {{ margin-top:34px; }}
 .old .card {{ padding:9px 14px; }}
 .foot {{ color:var(--soft); font-size:13px; margin-top:38px; border-top:1px solid var(--line);
          padding-top:16px; }}
 .changebox {{ background:#fff9ef; border:1px solid #f0dfc2; border-radius:12px;
               padding:14px 18px; margin:0 0 26px; }}
 .changebox b {{ color:#8a5a13; }}
 .changebox ul {{ list-style:none; padding:0; margin:8px 0 6px; }}
 .changebox li {{ margin:0 0 7px; }}
 .bar {{ height:6px; background:#eeece7; border-radius:99px; overflow:hidden;
         margin:9px 0 2px; max-width:340px; }}
 .bar > i {{ display:block; height:100%; background:var(--good); }}
 details summary {{ cursor:pointer; color:var(--soft); font-size:13px; }}
</style></head><body><div class="wrap">
<h1>{title} — where we are going</h1>
<p class="lede">{lede}</p>
{body}
<p class="foot">{foot}</p>
</div></body></html>
"""


def _body(model: dict) -> str:
    out: list[str] = []
    root = model["root"]
    changes = model.get("changes") or []
    if changes:
        items = "".join(
            f'<li><b>{_esc(e.get("action"))}</b> — {_esc(e.get("why"))}'
            f' <span class="fact">({_esc(str(e.get("at"))[:10])}, by '
            f'{_esc(e.get("by"))})</span></li>' for e in changes)
        out.append(
            '<div class="changebox"><b>The team changed the plan '
            f'({len(changes)} since you last looked)</b> — nothing is waiting on '
            'you, this is just so you know:<ul>' + items + '</ul>'
            '<div class="fact">If you disagree with any of it, say so — the team '
            'puts it back. You only ever write the goal at the top.</div></div>')
    out.append('<p class="level">The long-term goal — set by you</p>')
    out.append(f'<ul class="root"><li class="now"><div class="card">'
               f'<div class="head"><span class="dot"></span><h3>{_esc(root["title"])}</h3>'
               f'{"<span class=\"tag you\">you</span>" if root.get("decided_by") == "owner" else ""}'
               f'</div>'
               + (f'<div class="why">{_esc(root["note"])}</div>' if root.get("note") else "")
               + "</div>")
    kids = "".join(_node(c) for c in (root.get("children") or []))
    if kids:
        out.append(f'<div class="children"><p class="level">How we get there</p>'
                   f'<ul>{kids}</ul></div>')
    out.append("</li></ul>")
    return "".join(out)


_CSS_CLASS = {"active": "now", "pending": "soon", "done": "done",
              "proposed": "suggested", "parked": "aside", "queued": "soon",
              "shipped": "done", "unset": "aside", "history": "aside"}


def _node(node: dict) -> str:
    kind = node.get("kind", "branch")
    status = node.get("status", "")
    cls = _CSS_CLASS.get(status, "soon")
    if kind == "task" and status == "queued":
        cls = "now" if node.get("is_next") else "soon"
    who = node.get("decided_by", "")
    tags = [f'<span class="tag state">{_esc(goals.STATUS_WORDS.get(status, status))}</span>']
    if kind == "task" and node.get("task_kind"):
        tags.append(f'<span class="tag">{_esc(goals.KIND_WORDS.get(node["task_kind"], node["task_kind"]))}</span>')
    if who == "owner":
        tags.append('<span class="tag you">you</span>')
    elif who == "agent":
        tags.append('<span class="tag team">team</span>')
    title = node.get("title", "")
    if kind == "task":
        title = (f'{title} — built in v{node["version"]}'
                 if node.get("version") else title)
    body = [f'<div class="head"><span class="dot"></span><h3>{_esc(title)}</h3>'
            + "".join(tags) + "</div>"]
    if node.get("outcome"):
        body.append(f'<div class="why">{_esc(node["outcome"])}</div>')
    if node.get("note") and kind != "vision":
        body.append(f'<div class="why">{_esc(node["note"])}</div>')
    for m in node.get("measures") or []:
        body.append(f'<div class="fact">how we will know: {_esc(m)}</div>')
    if node.get("bar") is not None:
        body.append(f'<div class="bar"><i style="width:{int(node["bar"])}%"></i></div>'
                    f'<div class="fact">{_esc(node.get("progress", ""))}</div>')
    if node.get("detail"):
        body.append(f'<div class="fact">{_esc(node["detail"])}</div>')
    kids = node.get("children") or []
    inner = ""
    if kids:
        inner = ('<div class="children"><ul>'
                 + "".join(_node(c) for c in kids) + "</ul></div>")
    return f'<li class="{cls}"><div class="card">{"".join(body)}</div>{inner}</li>'


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_html(model: dict, title: str) -> str:
    lede = ("Each night the team builds one item from the bottom of this page. "
            "You set the goal at the top. Everything in between is grown, "
            "re-parented and pruned by the team — and every change to this plan "
            "is shown to you afterwards, with the reason it was made.")
    foot = ("Where this comes from: the team's own records in this project "
            f"(<code>.pipeline/</code>). Nothing here is written by hand for looks — "
            "it is rebuilt every time a run finishes. "
            "Disagree with anything? Tell the team, or run "
            f"<code>devteam goal {_esc(title)} revert --last</code>.")
    body = _body(model)
    return _HTML.format(title=_esc(title), lede=lede, foot=foot, body=body)


def write(project, open_after: bool = False) -> str:
    """Render both views into the project's .pipeline dir; returns the html path."""
    profile = profile_of(project)
    model = build(project)
    (project / ".pipeline" / "goals.txt").write_text(render_text(model) + "\n")
    dest = project / ".pipeline" / "goals.html"
    dest.write_text(render_html(model, profile.get("name", "project")))
    if open_after:
        import subprocess
        subprocess.run(["open", str(dest)], check=False)
    return str(dest)
