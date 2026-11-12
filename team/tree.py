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
  └─ goal                            a top-level node with kind='goal' — an END
     └─ milestone / sub-goal         profile['goals']['milestones'][*] children
        └─ task (a leaf = one run)   state['queue'] entries (advances) and
                                     state['shipped'] ledger rows (delivered)
"""
from __future__ import annotations

import re

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
               detail: str = "", kind: str = "", pinned: bool = False) -> dict:
    return {"kind": "task", "id": label, "title": label, "status": status,
            "advances": advances, "target": target, "version": version,
            "date": date, "task_id": task_id, "detail": detail,
            "task_kind": kind, "pinned": pinned,
            "lane": "priority" if pinned else "plan"}


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
    prd = _prd_of(project)
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
                "asks": asks, "goal_less": True, "prd": prd}

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

    def make_node(raw: dict, kind: str, parent_id: str = "") -> dict:
        mid = str(raw["id"])
        title = str(raw.get("title") or mid)
        phase = raw.get("phase")
        if phase is None:
            hit = re.search(r"\(Phase ([12])\)", title)
            phase = int(hit.group(1)) if hit else None
        serves = [str(x) for x in (raw.get("serves") or []) if x]
        if not serves and parent_id:
            serves = [parent_id]
        node = {"kind": kind, "id": mid,
                "title": title,
                "phase": phase,
                "serves": serves,
                "status": ((st.get(mid, "pending")
                            if kind in ("milestone", "goal", "subgoal")
                            else "")
                           or str(raw.get("status") or "")
                           or (PROPOSED if kind not in ("milestone", "goal")
                               else "pending")),
                "decided_by": str(raw.get("decided_by") or "owner"),
                "outcome": str(raw.get("outcome") or ""),
                "measures": _measures_view(raw, research),
                "design": (raw.get("design") if isinstance(raw.get("design"), dict)
                           else {}),
                "verdict": (verdicts.get(mid) or {}).get("verdict", ""),
                "evidence": (verdicts.get(mid) or {}).get("evidence", ""),
                "children": []}
        for sub in raw.get("children") or []:
            if isinstance(sub, dict) and sub.get("id"):
                subkind = "goal" if str(sub.get("kind") or "") == goals.GOAL_KIND \
                    else "subgoal"
                node["children"].append(make_node(sub, subkind, mid))
        return node

    nodes: list[dict] = []
    for m in plan["milestones"]:
        topkind = "goal" if str(m.get("kind") or "") == goals.GOAL_KIND else "milestone"
        node = make_node(m, topkind)
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
                          kind=str(q.get("type") or ""),
                          pinned=bool(q.get("pinned")))
        leaf["design"] = str(q.get("design") or "")
        leaf["design_source"] = str(q.get("design_source") or "")
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
    _annotate_design(project, root)
    return {"root": root, "generated_at": now_iso(), "changes": changes,
            "asks": asks, "goal_less": False, "prd": prd}


def _annotate_design(project, root: dict) -> None:
    """Attach the CURRENT designer answer to each feature.

    No hardcoded ready/derive/needed: a feature with UI tasks is `pending` until
    the designer is asked, and `stale` when the Stitch project or design doc
    changed after the answer. Features with no UI tasks are `not_applicable`.
    """
    from . import designer

    def walk(n: dict) -> None:
        if n.get("kind") == "task":
            return
        if n.get("kind") != "vision":
            tasks = [c for c in n.get("children") or [] if c.get("kind") == "task"]
            if not tasks:
                n["design_consult"] = {"state": "no_work"}
                for c in n.get("children") or []:
                    walk(c)
                return
            ui = [t for t in tasks if designer.is_ui_task(t, project)]
            if not ui:
                n["design_consult"] = {"state": "not_applicable"}
            else:
                states = [designer.status(project, t) for t in ui]
                answered = next((x for x in states if x.get("state") == "answered"), None)
                if answered:
                    n["design_consult"] = answered
                elif any(x.get("state") == "stale" for x in states):
                    n["design_consult"] = {"state": "stale"}
                else:
                    n["design_consult"] = {"state": "pending"}
        for c in n.get("children") or []:
            walk(c)

    walk(root)


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
        elif kind == "goal":
            mark = {"active": ">", "done": "✓", "pending": "·", "proposed": "?",
                    "parked": "x", "history": "o"}.get(status, "·")
            tail = goals.STATUS_WORDS.get(status, status)
            if who:
                tail += f", set by {who}"
            if node.get("progress"):
                tail += f" · {node['progress']}"
            if node.get("phase"):
                tail += f" · Phase {node['phase']}"
            lines.append(f"{pad}{mark} {node['title']}   "
                         f"(a goal · {tail})")
        else:
            mark = {"active": ">", "done": "✓", "pending": "·", "proposed": "?",
                    "parked": "x", "history": "o"}.get(status, "·")
            tail = goals.STATUS_WORDS.get(status, status)
            if who:
                tail += f", set by {who}"
            if node.get("progress"):
                tail += f" · {node['progress']}"
            if node.get("phase"):
                tail += f" · Phase {node['phase']}"
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
<title>{title} — plan</title>
<style>
 :root {{ --bg:#fbfaf7; --card:#ffffff; --ink:#191c22; --soft:#6b7280;
          --line:#e7e2d9; --now:#2f6fed; --good:#1f9d63; --later:#b45309;
          --wash:#f4f7ff; }}
 * {{ box-sizing:border-box; }}
 body {{ max-width:900px; margin:0 auto; padding:40px 22px 80px;
        background:var(--bg); color:var(--ink);
        font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
        -webkit-font-smoothing:antialiased; }}
 .eyebrow {{ font-size:12px; text-transform:uppercase; letter-spacing:.12em; color:var(--soft); }}
 h1 {{ font-size:30px; line-height:1.2; margin:6px 0 30px; letter-spacing:-.02em; }}
 h2 {{ font-size:13px; font-weight:700; text-transform:uppercase; letter-spacing:.1em;
       color:var(--soft); margin:40px 0 12px; display:flex; gap:9px; align-items:baseline; }}
 h2 .count {{ font-weight:400; letter-spacing:0; text-transform:none; color:#9aa1ab; }}
 .vision {{ background:var(--card); border:1px solid var(--line); border-left:4px solid var(--ink);
           border-radius:14px; padding:20px 22px; }}
 .vision .k, .next .k {{ font-size:11px; font-weight:700; letter-spacing:.11em;
                          text-transform:uppercase; color:var(--soft); margin-bottom:6px; }}
 .vision .vt {{ font-size:20px; font-weight:650; line-height:1.45; }}
 .next {{ background:var(--wash); border:1px solid #dbe6ff; border-left:4px solid var(--now);
          border-radius:14px; padding:18px 20px; margin-top:14px; }}
 .next .nt {{ font-size:18px; font-weight:750; }}
 .next .nm {{ color:var(--soft); font-size:13px; margin-top:2px; }}
 .next .np {{ color:#374151; font-size:14px; margin-top:9px; }}
 .priority {{ background:#fff4f2; border:1px solid #f3cfc7; border-left:4px solid #c2410c;
               border-radius:14px; padding:16px 18px; margin-top:14px; }}
 .priority .k {{ font-size:11px; font-weight:700; letter-spacing:.11em;
                 text-transform:uppercase; color:#9a3412; margin-bottom:4px; }}
 .priority .nt {{ font-size:16px; font-weight:750; }}
 .priority .np {{ color:#6b4a3d; font-size:13.5px; margin-top:5px; }}
 .priority .nm {{ color:#8a5a45; font-size:13px; margin-top:7px; }}
 .pill.resume {{ background:#fdf1e3; color:#9a4b06; }}
 .designsum {{ color:var(--soft); font-size:12.5px; margin:14px 0 0; }}
 .designbadge {{ margin-top:7px; }}
 .pill.design.pending {{ background:#fdf1e3; color:#9a4b06; }}
 .pill.design.stale {{ background:#fdecec; color:#b42318; }}
 .pill.design.answered {{ background:#e9f7ef; color:#14764a; }}
 .pill.design.tweak_screen {{ background:#eff6ff; color:#1d4ed8; }}
 .pill.design.derive {{ background:#f3f1ec; color:#4b5563; }}
 .pill.design.new_screen {{ background:#fdf1e3; color:#9a4b06; }}
 .subgoals {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr));
              gap:10px; }}
 .sg {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
        padding:15px 16px; }}
 .sg-h {{ display:flex; gap:8px; justify-content:space-between; align-items:center; }}
 .sgt {{ font-size:15.5px; font-weight:750; line-height:1.3; }}
 .sgo {{ color:var(--soft); font-size:13px; margin-top:6px; }}
 .sg-feats {{ display:flex; flex-wrap:wrap; gap:5px; margin-top:9px; }}
 .chip {{ font-size:11.5px; border-radius:999px; padding:2px 8px;
          background:#f3f1ec; color:#4b5563; }}
 .chip em {{ font-style:normal; color:#9aa1ab; }}
 .pill.phase {{ background:#eef2ff; color:#3730a3; }}
 .serves {{ display:flex; flex-wrap:wrap; gap:5px; align-items:center;
            color:var(--soft); font-size:12px; margin-top:8px; }}
 .tf {{ color:#9aa1ab; font-size:11.5px; margin-top:2px; }}
 .ask {{ background:#fff8ec; border:1px solid #f0dcb8; border-radius:12px;
         padding:13px 16px; margin:10px 0; font-size:14px; }}
 .goal {{ margin-top:30px; }}
 .goal-h {{ display:flex; gap:10px; align-items:baseline; flex-wrap:wrap;
            border-bottom:1px solid var(--line); padding-bottom:8px; }}
 .goal-h .gt {{ font-size:18px; font-weight:750; }}
 .goal-o {{ color:var(--soft); font-size:14px; margin:7px 0 2px; }}
 .feat {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
          padding:17px 20px; margin:10px 0; }}
 .feat.active {{ border-left:4px solid var(--now); }}
 .feat.done {{ border-left:4px solid var(--good); }}
 .feat.parked {{ border-left:4px solid var(--later); }}
 .feat-h {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
 .feat-t {{ font-size:16.5px; font-weight:700; flex:1; min-width:220px; line-height:1.35; }}
 .feat-o {{ color:var(--soft); font-size:14px; margin-top:6px; }}
 .pill {{ font-size:11.5px; font-weight:600; border-radius:999px; padding:2px 9px;
          background:#f3f1ec; color:#6b7280; white-space:nowrap; }}
 .pill.now {{ background:#eaf1ff; color:#1d4fd7; }}
 .pill.done {{ background:#e9f7ef; color:#14764a; }}
 .pill.later {{ background:#fdf1e3; color:#9a4b06; }}
 .bar {{ height:5px; background:#f0ede7; border-radius:99px; overflow:hidden;
         max-width:300px; margin:12px 0 2px; }}
 .bar i {{ display:block; height:100%; background:var(--good); }}
 .prog {{ color:var(--soft); font-size:12px; }}
 .tasks {{ margin:15px 0 0; border-left:2px solid #efece5; padding-left:15px; }}
 .task {{ display:flex; gap:11px; padding:9px 0; border-top:1px solid #f4f1eb; }}
 .task:first-child {{ border-top:0; padding-top:2px; }}
 .tn {{ flex:0 0 24px; height:24px; border-radius:50%; background:#f2efe9;
        color:var(--soft); font-size:12px; font-weight:700; display:flex;
        align-items:center; justify-content:center; margin-top:1px; }}
 .task.next .tn {{ background:var(--now); color:#fff; }}
 .task.shipped .tn {{ background:#e9f7ef; color:#14764a; }}
 .tt {{ font-weight:650; font-size:14.5px; line-height:1.4; }}
 .task.shipped .tt {{ font-weight:500; color:#4b5563; }}
 .tm {{ color:var(--soft); font-size:13px; margin-top:2px; }}
 .none {{ color:var(--soft); font-size:14px; padding:6px 0; }}
 details {{ margin-top:6px; }}
 summary {{ cursor:pointer; color:var(--soft); font-size:12.5px; }}
 summary::marker {{ color:#c3c0b8; }}
 .inner {{ color:var(--soft); font-size:13px; margin-top:6px; }}
 .inner li {{ margin:0 0 4px; }}
 .log {{ background:transparent; border-top:1px solid var(--line); margin-top:44px;
         padding-top:14px; }}
 .prd {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
         padding:14px 18px; margin-top:14px; }}
 .prd summary {{ font-size:13px; font-weight:600; color:var(--ink); }}
 .prd .prd-body {{ margin-top:12px; border-top:1px solid var(--line);
                   padding-top:10px; font-size:14px; color:#374151; }}
 .prd-body h1, .prd-body h2, .prd-body h3 {{ margin:18px 0 8px; text-transform:none;
                                             letter-spacing:0; color:var(--ink); }}
 .prd-body h1 {{ font-size:18px; }} .prd-body h2 {{ font-size:16px; }}
 .prd-body h3 {{ font-size:14.5px; }}
 .prd-body p, .prd-body li {{ margin:0 0 8px; }}
 .prd-body pre {{ background:#f6f4ef; border:1px solid var(--line); border-radius:8px;
                  padding:10px 12px; overflow-x:auto; font-size:12px; }}
 .prd-body code {{ font-size:12.5px; }}
 .prd-body table {{ border-collapse:collapse; width:100%; margin:10px 0; font-size:13px; }}
 .prd-body th, .prd-body td {{ border:1px solid var(--line); padding:6px 9px; text-align:left; }}
 .prd-body blockquote {{ border-left:3px solid var(--line); margin:8px 0;
                         padding:2px 12px; color:var(--soft); }}
 .prd-body ul, .prd-body ol {{ padding-left:22px; margin:6px 0; }}
 .prd-body hr {{ border:none; border-top:1px solid var(--line); margin:16px 0; }}
 .foot {{ color:var(--soft); font-size:12.5px; margin-top:34px;
          border-top:1px solid var(--line); padding-top:14px; }}
 code {{ background:#f3f1ec; padding:1px 5px; border-radius:4px; font-size:12.5px; }}
 @media (max-width:620px) {{
   body {{ padding:26px 14px 60px; }} h1 {{ font-size:24px; }}
   .feat {{ padding:14px 15px; }} .vision, .next {{ padding:16px; }}
 }}
</style></head><body>
<div class="eyebrow">{title} · plan</div>
<h1>{h1}</h1>
{body}
<p class="foot">{foot}</p>
</body></html>
"""


def _short(title: str, n: int = 42) -> str:
    """One-line summary: first clause only, ellipsized. The full text stays in
    the technical detail behind a click."""
    t = str(title or "")
    for sep in (" — ", " – ", ": ", " ("):
        if sep in t:
            t = t.split(sep, 1)[0]
            break
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


_ACRONYMS = {"qr": "QR", "prd": "PRD", "rpc": "RPC", "ui": "UI", "api": "API",
             "ml": "ML", "ai": "AI", "sdui": "SDUI", "a2a": "A2A", "kmp": "KMP"}


def _humanize(label: str) -> str:
    """Old ledger rows sometimes recorded an internal slug as the label
    (`ux-ax1-first-run-value`). Show it readably rather than as code — the page
    is for the owner; the raw label stays in the technical detail."""
    t = str(label or "")
    if " " not in t and ("-" in t or "_" in t):
        words = t.replace("-", " ").replace("_", " ").split()
        t = " ".join(_ACRONYMS.get(w.lower(), w) for w in words).strip()
        return t[:1].upper() + t[1:]
    return t


def _details(summary: str, items: list[str]) -> str:
    if not items:
        return ""
    body = "".join(f"<li>{_esc(i)}</li>" for i in items)
    return (f'<details><summary>{_esc(summary)}</summary>'
            f'<div class="inner"><ul>{body}</ul></div></details>')


def _prd_of(project) -> dict:
    """The product's own spec, when one exists (`devteam revamp` synthesizes
    docs/PRD.md). It belongs on the plan page — the goal says WHY, the PRD says
    WHAT — but collapsed, so it never competes with the build order: the
    summary (title, path, size) is visible even when the document is folded."""
    path = project / "docs" / "PRD.md"
    try:
        text = path.read_text()
    except Exception:
        return {}
    nonblank = [l for l in text.splitlines() if l.strip()]
    title = next((l.lstrip("# ").strip() for l in nonblank
                  if l.startswith("# ")), "")
    return {"path": "docs/PRD.md", "text": text,
            "lines": len(nonblank), "title": title}


def _md_html(md: str) -> str:
    """Tiny deterministic markdown → HTML for the PRD block: headings, lists,
    tables, fenced code, blockquotes and inline emphasis. No CDN, no JS —
    anything it does not recognise renders as plain text."""
    def inline(s: str) -> str:
        s = _esc(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
        s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                   r'<a href="\2">\1</a>', s)
        return s

    out: list[str] = []
    lines = md.splitlines()
    i = 0
    para: list[str] = []

    def flush() -> None:
        nonlocal para
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para = []

    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("```"):
            flush()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            out.append("<pre>" + _esc("\n".join(block)) + "</pre>")
            continue
        if not s:
            flush()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,})$", s):
            flush()
            out.append("<hr/>")
            i += 1
            continue
        if s.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>"
                       + "".join(f"<p>{inline(q)}</p>" for q in quote if q)
                       + "</blockquote>")
            continue
        if "|" in s and i + 1 < len(lines) and "-" in lines[i + 1] \
                and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]):
            flush()
            header = [c.strip() for c in s.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in
                             lines[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table><tr>"
                       + "".join(f"<th>{inline(h)}</th>" for h in header)
                       + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>"
                                                  for c in r) + "</tr>"
                                 for r in rows)
                       + "</table>")
            continue
        if re.match(r"^[-*]\s+(.*)$", s):
            flush()
            items: list[str] = []
            while i < len(lines):
                mm = re.match(r"^\s*[-*]\s+(.*)$", lines[i])
                if mm:
                    items.append(mm.group(1))
                    i += 1
                elif items and re.match(r"^\s{2,}\S", lines[i]) \
                        and lines[i].strip():
                    # an indented continuation of the previous item
                    items[-1] += " " + lines[i].strip()
                    i += 1
                else:
                    break
            out.append("<ul>"
                       + "".join(f"<li>{inline(it)}</li>" for it in items)
                       + "</ul>")
            continue
        if re.match(r"^\d+[.)]\s+(.*)$", s):
            flush()
            items = []
            while i < len(lines):
                mm = re.match(r"^\s*\d+[.)]\s+(.*)$", lines[i])
                if mm:
                    items.append(mm.group(1))
                    i += 1
                elif items and re.match(r"^\s{2,}\S", lines[i]) \
                        and lines[i].strip():
                    items[-1] += " " + lines[i].strip()
                    i += 1
                else:
                    break
            out.append("<ol>"
                       + "".join(f"<li>{inline(it)}</li>" for it in items)
                       + "</ol>")
            continue
        para.append(s)
        i += 1
    flush()
    return "".join(out)


def _targets(value) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) or "—"
    return str(value or "—")


def _pill(status: str) -> str:
    words = {"active": "In progress", "done": "Done", "pending": "Waiting",
             "parked": "Later", "proposed": "Suggested", "history": "History",
             "queued": "Queued", "shipped": "Built"}
    words["pending"] = "Planned"
    cls = {"active": "now", "done": "done", "parked": "later",
           "proposed": "later"}.get(status, "")
    return (f'<span class="pill {cls}">'
            f'{_esc(words.get(status, status or "—"))}</span>')


def _collect(model: dict) -> dict:
    """Compatibility summary used by team/publish.py and older callers.

    The page itself renders the nested tree directly; this keeps the flat
    counts (branches, queue, done) available without duplicating the walk.
    """
    branches: list[dict] = []
    queue: list[dict] = []
    done: list[dict] = []

    def walk(n: dict) -> None:
        if n.get("kind") == "task":
            (done if n.get("status") == "shipped" else queue).append(n)
            return
        if n.get("kind") != "vision" and n.get("status") != "history":
            branches.append(n)
        for c in n.get("children") or []:
            walk(c)

    walk(model["root"])
    return {"branches": branches, "queue": queue, "done": done, "earlier": []}


def _task_ids(node: dict) -> list[dict]:
    out: list[dict] = []
    for c in node.get("children") or []:
        if c.get("kind") == "task":
            out.append(c)
        else:
            out.extend(_task_ids(c))
    return out


def _all_branches(root: dict) -> list[dict]:
    """Every non-task node, in tree order (goals, features, history)."""
    out: list[dict] = []

    def walk(n: dict) -> None:
        for c in n.get("children") or []:
            if c.get("kind") == "task":
                continue
            out.append(c)
            walk(c)
    walk(root)
    return out


def _body(model: dict) -> str:
    root = model["root"]
    out: list[str] = []

    # order is the build order: the queue head is the only entry a run builds
    order = {t["id"]: int(t.get("order") or 0) for t in _task_ids(root)
             if t.get("status") != "shipped" and t.get("order") is not None}
    all_tasks = _task_ids(root)
    next_task = next((t for t in all_tasks if t.get("is_next")), None)

    branches = [b for b in _all_branches(root) if b.get("status") != "history"]
    goals = [b for b in branches if b.get("kind") == "goal"]
    features = [b for b in branches if b.get("kind") != "goal"]
    goal_title = {g["id"]: g["title"] for g in goals}
    feature_of: dict[str, dict] = {}
    for f in features:
        for t in f.get("children") or []:
            if t.get("kind") == "task":
                feature_of[t["id"]] = f

    # 1. tier 1 — the one thing the owner writes
    out.append('<div class="vision"><div class="k">Long-term goal</div>'
               f'<div class="vt">{_esc(root["title"])}</div></div>')
    if root.get("note"):
        out.append(f'<div class="ask">{_esc(root["note"])}</div>')

    # 1b. the product's own spec: visible although collapsed — the summary
    # names the PRD even when the document itself is folded away
    prd = model.get("prd") or {}
    if prd.get("text"):
        label = prd.get("title") or "Product spec"
        out.append('<details class="prd"><summary>Product spec (PRD) — '
                   f'{_esc(label)} · {_esc(prd["path"])} · '
                   f'{prd["lines"]} lines · collapsed — expand to read'
                   '</summary><div class="prd-body">'
                   + _md_html(prd["text"]) + "</div></details>")

    # 2. what one run builds next
    if next_task:
        feat = feature_of.get(next_task["id"], {})
        out.append('<div class="next"><div class="k">Next run</div>'
                   f'<div class="nt">{_esc(_humanize(next_task["title"]))}</div>'
                   + (f'<div class="nm">in {_esc(feat.get("title"))}</div>'
                      if feat.get("title") else "")
                   + (f'<div class="np">{_esc(next_task.get("detail"))}</div>'
                      if next_task.get("detail") else "")
                   + _details("technical detail",
                              [f'id: {next_task["id"]}',
                               f'type: {next_task.get("task_kind") or "—"}',
                               f'advances: {next_task.get("advances") or "—"}',
                               f'target: {_targets(next_task.get("target"))}'])
                   + "</div>")

    # 2b. priority lane: an interrupt outranks the phase, but pauses it —
    # it never re-plans the phase or moves its resume pointer
    priority = [t for t in all_tasks if t.get("pinned")
                and t.get("status") != "shipped"]
    first_plan = next((t for t in all_tasks
                       if not t.get("pinned") and t.get("status") != "shipped"),
                      None)
    if priority:
        first_plan_id = first_plan["id"] if first_plan else ""
        out.append('<div class="priority"><div class="k">Priority interrupt · '
                   'jumps the queue</div>')
        for p in priority[:3]:
            out.append(f'<div class="nt">{_esc(_humanize(p["title"]))}</div>'
                       + (f'<div class="np">{_esc(p.get("detail"))}</div>'
                          if p.get("detail") else ""))
        if first_plan:
            out.append(f'<div class="nm">The phase is paused, not re-planned: '
                       f'it resumes at <b>{_esc(_humanize(first_plan["title"]))}</b>.</div>')
            first_plan["_resume"] = True
        out.append("</div>")

    # 2c. design is CONSULTED, not hardcoded: show the current state per feature
    cons = [f.get("design_consult") or {} for f in features]
    ui = [d for d in cons if d.get("state") in ("pending", "stale", "answered")]
    answered = [d for d in ui if d.get("state") == "answered"]
    n_na = sum(1 for d in cons if d.get("state") == "not_applicable")
    if features:
        out.append('<div class="designsum">Design: '
                   f'{len(ui)} UI feature{"s" if len(ui) != 1 else ""} ask the designer at build time · '
                   f'{len(answered)} answered'
                   + (f' · {n_na} backend-only' if n_na else '')
                   + '</div>')

    # 3. tier 2 — sub-goals, the ends, with the features that serve each
    out.append('<h2>Sub-goals <span class="count">the ends · order is '
               'priority</span></h2>')
    out.append('<div class="subgoals">')
    for g in goals:
        served = [f for f in features if g["id"] in (f.get("serves") or [])]
        n_tasks = sum(len([t for t in f.get("children") or []
                           if t.get("kind") == "task"]) for f in served)
        n_done = sum(1 for f in served for t in f.get("children") or []
                     if t.get("kind") == "task" and t.get("status") == "shipped")
        pct = round(100 * n_done / n_tasks) if n_tasks else 0
        chips = "".join(
            f'<span class="chip">{_esc(_short(f["title"], 34))}'
            + (f' <em>P{f["phase"]}</em>' if f.get("phase") else "")
            + "</span>" for f in served)
        out.append('<div class="sg"><div class="sg-h">'
                   f'<span class="sgt">{_esc(g["title"])}</span>'
                   + _pill(g.get("status")) + "</div>"
                   + (f'<div class="sgo">{_esc(g.get("outcome"))}</div>'
                      if g.get("outcome") else "")
                   + f'<div class="bar"><i style="width:{pct}%"></i></div>'
                   f'<div class="prog">{n_done} of {n_tasks} tasks built · '
                   f'{len(served)} feature{"s" if len(served) != 1 else ""}'
                   '</div>'
                   + (f'<div class="sg-feats">{chips}</div>' if chips else
                      '<div class="sgo" style="margin-top:8px">No feature yet.</div>')
                   + "</div>")
    out.append("</div>")

    # 4. tiers 3–4 — features grouped into phase lanes, each holding its tasks
    goalless = [f for f in features if not f.get("serves")]
    lanes: dict[object, list[dict]] = {}
    for f in features:
        lanes.setdefault(f.get("phase"), []).append(f)
    ordered = sorted([p for p in lanes if p is not None])
    ordered += [None] if None in lanes else []
    for ph in ordered:
        lane = lanes[ph]
        # Live work first, finished work last: the page is a queue, not an
        # archive. A feature whose task is queued ranks by that task's position,
        # so the page order IS the build order; work with nothing queued follows;
        # done work sinks to the bottom.
        def _rank(f: dict) -> tuple:
            done = str(f.get("status") or "") in ("done", "shipped")
            spots = [order[t["id"]] for t in (f.get("children") or [])
                     if t.get("kind") == "task" and t.get("id") in order]
            return (done, min(spots) if spots else 10 ** 5, 0)
        lane.sort(key=_rank)
        lane_tasks = sum(len([t for t in f.get("children") or []
                              if t.get("kind") == "task"]) for f in lane)
        lane_done = sum(1 for f in lane for t in f.get("children") or []
                        if t.get("kind") == "task" and t.get("status") == "shipped")
        if ph == 2:
            plural = "feature" if len(lane) == 1 else "features"
            tasks_word = "task" if lane_tasks == 1 else "tasks"
            out.append('<details class="laterwrap"><summary>Phase 2 — frozen '
                       'until Phase 1 ships '
                       f'({len(lane)} {plural} · {lane_tasks} {tasks_word})'
                       '</summary>')
        else:
            # An unphased feature is NOT a third plan: it is work nobody has
            # placed yet. Naming it "The plan" made a phantom section that read
            # like a competing lane and could say "4 of 4 built" while six
            # unbuilt features sat under it.
            label = f"Phase {ph}" if ph else \
                "No phase yet — needs a phase before it can be built"
            paused = " · paused, resumes below" if priority else ""
            if ph:
                count = f"{lane_done} of {lane_tasks} built{paused}"
            else:
                plural = "feature" if len(lane) == 1 else "features"
                count = (f"{len(lane)} {plural} · no phase set — these do NOT "
                         "ship in Phase 1")
            out.append(f'<h2>{label} <span class="count">{count}</span></h2>')
        for f in lane:
            out.append(_feature_card(f, order, goal_title))
        if ph == 2:
            out.append("</details>")

    # 5. history is a footnote, not a list that competes with the plan
    history = [b for b in _all_branches(root) if b.get("status") == "history"]
    shipped = [t for h in history for t in _task_ids(h)]
    shipped += [t for t in all_tasks if t.get("status") == "shipped"]
    seen: set[str] = set()
    uniq = []
    for t in shipped:
        if t["id"] not in seen:
            seen.add(t["id"])
            uniq.append(t)
    if uniq:
        out.append('<details class="log"><summary>Already shipped '
                   f'({len(uniq)})</summary><div class="inner"><ul>')
        for t in uniq:
            v = f' · v{_esc(t["version"])}' if t.get("version") else ""
            d = f' · {_esc(str(t.get("date"))[:10])}' if t.get("date") else ""
            out.append(f'<li>{_esc(_humanize(t["title"]))}{v}{d}</li>')
        out.append("</ul></div></details>")

    changes = model.get("changes") or []
    if changes:
        items = [f'{e.get("action")} — {e.get("why")} '
                 f'({str(e.get("at"))[:10]}, by {e.get("by")})' for e in changes]
        out.append(f'<details class="log"><summary>Plan changes '
                   f'({len(changes)})</summary><div class="inner"><ul>'
                   + "".join(f"<li>{_esc(i)}</li>" for i in items)
                   + "</ul></div></details>")

    return "".join(out)


def _feature_card(f: dict, order: dict, goal_title: dict) -> str:
    tasks = [c for c in f.get("children") or [] if c.get("kind") == "task"]
    tasks.sort(key=lambda t: (t.get("status") == "shipped",
                              order.get(t["id"], 999)))
    status = str(f.get("status") or "")
    out = [f'<div class="feat {_esc(status)}"><div class="feat-h">'
           f'<div class="feat-t">{_esc(f["title"])}</div>'
           + (f'<span class="pill phase">Phase {f["phase"]}</span>'
              if f.get("phase") else "")
           + _pill(status)
           + (f'<span class="prog">{_esc(f.get("progress"))}</span>'
              if f.get("progress") else "")
           + "</div>"]
    serves = [goal_title.get(x, x) for x in (f.get("serves") or [])]
    if serves:
        out.append('<div class="serves">Serves: '
                   + "".join(f'<span class="chip">{_esc(s)}</span>' for s in serves)
                   + "</div>")
    d = f.get("design_consult") or {}
    state = str(d.get("state") or "")
    if state in ("pending", "stale", "answered"):
        if state == "pending":
            label, cls = "Design · ask the designer", "pending"
        elif state == "stale":
            label, cls = "Design · ask again (design changed)", "stale"
        else:
            decision = str(d.get("decision") or "")
            screen = d.get("screen") or {}
            label = {"existing_screen": "Design · " + str(screen.get("title") or "existing screen"),
                     "tweak_screen": "Design · tweak " + str(screen.get("title") or "existing screen"),
                     "derive": "Design · derive from system",
                     "new_screen": "Design · new screen required",
                     "not_applicable": "Design · no UI"}.get(decision, "Design · " + decision)
            cls = decision if decision in ("derive", "tweak_screen", "new_screen") else "answered"
        out.append(f'<div class="designbadge"><span class="pill design '
                   f'{_esc(cls)}">{_esc(label)}</span></div>')
    if f.get("outcome"):
        out.append(f'<div class="feat-o">{_esc(f["outcome"])}</div>')
    if f.get("bar") is not None:
        out.append(f'<div class="bar"><i style="width:{int(f["bar"])}%"></i></div>')
    if tasks:
        out.append('<div class="tasks">')
        for t in tasks:
            out.append(_task_row(t, order, f.get("title", "")))
        out.append("</div>")
    else:
        out.append('<div class="none">No task queued for this feature.</div>')
    if f.get("measures"):
        out.append(_details("how we will know it is done", f["measures"]))
    tech = [f'id: {f.get("id")}', f'status: {status or "—"}',
            f'phase: {f.get("phase") or "—"}',
            'serves: ' + (", ".join(f.get("serves") or []) or "—")]
    out.append(_details("technical detail", tech))
    out.append("</div>")
    return "".join(out)


def _task_row(t: dict, order: dict, feature_label: str = "") -> str:
    shipped = t.get("status") == "shipped"
    is_next = bool(t.get("is_next"))
    cls = "shipped" if shipped else ("next" if is_next else "")
    if shipped:
        marker = "✓"
    elif t["id"] in order:
        marker = str(order[t["id"]] + 1)
    else:
        marker = "·"
    out = [f'<div class="task {cls}"><span class="tn">{marker}</span>'
           '<div class="tb"><div class="tt">'
           f'{_esc(_humanize(t["title"]))}'
           + (' <span class="pill now">next</span>' if is_next else "")
           + (' <span class="pill resume">resume here</span>'
              if t.get("_resume") else "")
           + (f' <span class="pill done">v{_esc(t.get("version"))}</span>'
              if shipped and t.get("version") else "")
           + "</div>"]
    if feature_label:
        out.append(f'<div class="tf">in {_esc(_short(feature_label, 56))}</div>')
    if str(t.get("design") or "") == "needed":
        out.append('<div class="tf"><span class="pill design needed">'
                   'design first</span></div>')
    if t.get("detail"):
        out.append(f'<div class="tm">{_esc(_short(t["detail"], 150))}</div>')
    tech = [f'id: {t["id"]}',
            f'type: {t.get("task_kind") or "—"}',
            f'advances: {t.get("advances") or "—"}',
            f'target: {_targets(t.get("target"))}']
    out.append(_details("technical detail", tech))
    if t.get("detail") and len(str(t["detail"])) > 150:
        out.append(_details("full reason", [str(t["detail"])]))
    out.append("</div></div>")
    return "".join(out)


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_html(model: dict, title: str) -> str:
    batched = ("One goal, one plan, one build order: goal → feature → task. Technical detail is behind a click.")
    foot = ("Rebuilt automatically every run from the team's own records in this "
            "project. Disagree with anything? Tell the team — it changes, and you "
            f"are told — or run <code>devteam goal &lt;project&gt; revert</code>. "
            "Everything on this page, including the technical detail behind each "
            "item, is generated by code: no LLM writes it and nothing here is "
            "tuned for looks.")
    return _HTML.format(title=_esc(title),
                        h1="Where we are going",
                        lede=batched, foot=foot, body=_body(model))


def url(project) -> str:
    """The goal page as a `file://` URL (clickable in a terminal, pasteable into
    a browser). Printed after EVERY change and on EVERY run, so the owner is one
    paste away from the current picture — see cli.print_page()."""
    return (pipeline_dir(project) / "goals.html").as_uri()


def write(project, open_after: bool = False, mirror: bool = False) -> str:
    """Render every view of the plan; returns the local html path.

    Two audiences, three files:
      .pipeline/goals.txt|.html  — machine-local working copies (git-excluded)
      docs/goals.html            — the SHARED mirror, committed and pushed by
                                   team/publish.py and served by GitHub Pages,
                                   so the plan travels with the repo instead of
                                   dying with this laptop.
    Same model, same renderer: the shared copy can never drift from the local one.
    """
    profile = profile_of(project)
    model = build(project)
    title = profile.get("name", "project")
    pdir = project / ".pipeline"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "goals.txt").write_text(
        render_text(model) + "\n\nwhere we are going: " + url(project) + "\n")
    dest = pdir / "goals.html"
    html = render_html(model, title)
    dest.write_text(html)
    if mirror:
        # ONLY the publish path writes the shared copy: a read-only command must
        # not dirty the owner's checkout, and a mirror rewritten on every `status`
        # was a permanent uncommitted change nobody asked for.
        docs = project / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "goals.html").write_text(html)
    if open_after:
        import subprocess
        subprocess.run(["open", str(dest)], check=False)
    return str(dest)
