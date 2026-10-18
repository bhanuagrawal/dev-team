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


def _measures_view(ms: dict, research: dict | None) -> list[str]:
    out = []
    verdict, evidence = goals.check_measures(ms, research)
    for m in ms.get("measures") or []:
        if not isinstance(m, dict):
            continue
        if m.get("metric"):
            ga4 = ((research or {}).get("signals", {}) or {}).get("ga4", {}) or {}
            val = ga4.get(m["metric"])
            shown = "no data" if val is None else str(val)
            out.append(f'{m["metric"]} = {shown} (target {m.get("op","<=")} '
                       f'{m.get("target")})')
        elif m.get("text"):
            out.append(str(m["text"]))
    if evidence and verdict is not None:
        out.append(("[met] " if verdict else "[not met] ") + evidence)
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

    root = {
        "kind": "vision", "id": "vision",
        "title": (plan or {}).get("vision") or "(no long-term goal declared yet)",
        "status": "active" if plan else "unset",
        "decided_by": "owner", "children": [],
    }
    if not plan:
        root["note"] = ("OWNER ASK at setup: what is this app for long term? "
                        "Until it is answered the pipeline works from its own "
                        "inventory (goal-less mode).")
        # everything still gets shown, honestly, under a pre-goal branch
        pre = {"kind": "branch", "id": "pre-goal", "title": "pre-goal work",
               "status": "parked", "decided_by": "agent", "children": []}
        for q in queue:
            pre["children"].append(_task_node(
                str(q.get("label")), q.get("target"), "", "queued",
                detail=str(q.get("reason") or "")[:160]))
        for lbl, s in delivered.items():
            pre["children"].append(_task_node(
                lbl, s.get("target"), "", "shipped", s.get("version", ""),
                s.get("date", "")))
        if pre["children"]:
            root["children"].append(pre)
        return {"root": root, "generated_at": now_iso(), "goal_less": True}

    def attach(leaf: dict, mid: str) -> None:
        for node in nodes:
            if node["id"] == mid:
                node["children"].append(leaf)
                return
        for existing in root["children"]:
            if existing["kind"] == "branch" and existing["id"] == (mid or "unattributed"):
                existing["children"].append(leaf)
                return
        root["children"].append({"kind": "branch", "id": mid or "unattributed",
                                 "title": (mid or "unattributed (pre-goal work — "
                                           "delivered before the goal tree existed)"),
                                 "status": "parked", "decided_by": "—",
                                 "children": [leaf]})

    nodes: list[dict] = []
    for m in plan["milestones"]:
        node = {"kind": "milestone", "id": m["id"], "title": m["title"],
                "status": st.get(m["id"], "pending"), "decided_by": "owner",
                "outcome": m["outcome"],
                "measures": _measures_view(m, research),
                "verdict": (verdicts.get(m["id"]) or {}).get("verdict", ""),
                "evidence": (verdicts.get(m["id"]) or {}).get("evidence", ""),
                "children": []}
        for sub in (m.get("children") or []):
            if isinstance(sub, dict) and sub.get("id"):
                node["children"].append({
                    "kind": "subgoal", "id": str(sub["id"]),
                    "title": str(sub.get("title") or sub["id"]),
                    "status": str(sub.get("status") or PROPOSED),
                    "decided_by": str(sub.get("decided_by") or "agent"),
                    "outcome": str(sub.get("outcome") or ""),
                    "measures": [str(x.get("text") or x.get("metric"))
                                 for x in (sub.get("measures") or [])
                                 if isinstance(x, dict)],
                    "children": []})
        nodes.append(node)
        root["children"].append(node)

    queued_labels = set()
    for q in queue:
        label = str(q.get("label") or "")
        queued_labels.add(label)
        ship = delivered.get(label)
        attach(_task_node(label, q.get("target"),
                          str(q.get("advances") or ""),
                          "shipped" if ship else "queued",
                          ship.get("version", "") if ship else "",
                          ship.get("date", "") if ship else "",
                          detail=str(q.get("reason") or "")[:160],
                          kind=str(q.get("type") or "")),
               str(q.get("advances") or ""))
    for lbl, s in delivered.items():
        if lbl in queued_labels:
            continue
        # Delivered work whose queue row is gone: it belongs to the milestone it
        # was tagged with at ship time, else to pre-goal history.
        attach(_task_node(lbl, s.get("target"), s.get("advances", ""), "shipped",
                          s.get("version", ""), s.get("date", ""),
                          s.get("task_id", "")),
               str(s.get("advances") or ""))
    return {"root": root, "generated_at": now_iso(), "goal_less": False}


_MARKS = {"active": "→", "done": "✓", "pending": "·", "proposed": "?", 
          "parked": "⊘", "queued": "·", "shipped": "✓", "unset": "!"}


def render_text(model: dict) -> str:
    lines: list[str] = []

    def walk(node: dict, depth: int, prefix: str = "") -> None:
        mark = _MARKS.get(node.get("status", ""), "·")
        who = node.get("decided_by", "")
        meta = []
        if who and node["kind"] in ("vision", "milestone", "subgoal"):
            meta.append(f"{who}")
        if node["kind"] == "task":
            if node.get("task_kind"):
                meta.append(str(node["task_kind"]))
            if node.get("version"):
                meta.append(f"v{node['version']} {node.get('date','')}".strip())
            elif node.get("status") == "queued":
                meta.append("queued for a run")
        head = (f"{'  ' * depth}{mark} {node.get('id','')}  {node['title']}"
                if node["kind"] != "task" else f"{'  ' * depth}{mark} {node['title']}")
        if node["kind"] in ("milestone", "subgoal"):
            head = f"{'  ' * depth}{mark} [{node.get('id')}] {node['title']}"
        if meta:
            head += "   " + " · ".join(m for m in meta if m)
        lines.append(head)
        if node.get("note"):
            lines.append(f"{'  ' * (depth + 1)}! {node['note']}")
        if node.get("outcome"):
            lines.append(f"{'  ' * (depth + 1)}outcome: {node['outcome']}")
        for m in node.get("measures") or []:
            lines.append(f"{'  ' * (depth + 1)}measure: {m}")
        for child in node.get("children") or []:
            walk(child, depth + 1)

    lines.append("GOAL TREE — root is human-only; branches are grown/pruned by agents")
    walk(model["root"], 0)
    lines.append("")
    lines.append(f"(generated {model['generated_at']}; leaves = the work each run picks)")
    return "\n".join(lines)


_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Goal tree — {title}</title>
<style>
 :root {{ --bg:#0f1115; --fg:#e7e9ee; --dim:#9aa3b2; --line:#2a2f3a;
          --done:#3ecf8e; --active:#7aa2f7; --pend:#6b7280; --off:#e0af68;
          --human:#c792ea; --agent:#7dcfff; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; padding:28px 32px 60px; background:var(--bg); color:var(--fg);
        font:14px/1.5 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif; }}
 h1 {{ font-size:19px; margin:0 0 4px; }}
 .sub {{ color:var(--dim); font-size:12px; margin-bottom:22px; }}
 .legend {{ display:flex; gap:16px; flex-wrap:wrap; color:var(--dim);
            font-size:12px; margin-bottom:20px; }}
 .legend b {{ color:var(--fg); font-weight:600; }}
 ul {{ list-style:none; margin:0; padding-left:22px; position:relative; }}
 li {{ position:relative; padding:3px 0 3px 16px; }}
 li:before {{ content:""; position:absolute; left:0; top:0; bottom:0;
              border-left:1px solid var(--line); }}
 li:after {{ content:""; position:absolute; left:0; top:17px; width:12px;
             border-top:1px solid var(--line); }}
 li:last-child:before {{ bottom:auto; height:18px; }}
 .node {{ border:1px solid var(--line); border-radius:9px; padding:8px 11px;
          background:#151822; display:inline-block; max-width:min(1050px,92vw); }}
 .vision > .node {{ border-color:var(--human); background:#191428; }}
 .active > .node {{ border-color:var(--active); }}
 .done > .node {{ border-color:var(--done); opacity:.92; }}
 .off-goal > .node {{ border-color:var(--off); border-style:dashed; }}
 .parked > .node, .proposed > .node {{ opacity:.62; }}
 .id {{ color:var(--dim); font-size:11px; letter-spacing:.04em; }}
 .title {{ font-weight:600; }}
 .task .title {{ font-weight:500; }}
 .meta {{ color:var(--dim); font-size:12px; margin-top:3px; }}
 .meas {{ color:var(--dim); font-size:12px; margin-top:4px; }}
 .pill {{ font-size:10px; border:1px solid var(--line); border-radius:999px;
          padding:1px 7px; margin-left:6px; color:var(--dim); }}
 .owner {{ color:var(--human); border-color:#4a3660; }}
 .agent {{ color:var(--agent); }}
 .toggle {{ cursor:pointer; user-select:none; color:var(--dim); margin-right:5px;
            font-size:11px; }}
 .count {{ color:var(--dim); font-size:11px; margin-left:6px; }}
 footer {{ color:var(--dim); font-size:12px; margin-top:26px; }}
 code {{ color:var(--fg); background:#1b1f2a; padding:1px 5px; border-radius:4px; }}
</style></head><body>
<h1>Goal tree — {title}</h1>
<div class="sub">Root is <b>human-only</b>. Agents grow, prune, re-parent and
 propose sub-goals; every run picks one <b>leaf</b>. Generated {generated}.</div>
<div class="legend">
 <span><b>→</b> active</span><span><b>✓</b> done/shipped</span>
 <span><b>·</b> pending/queued</span><span><b>?</b> proposed (agent)</span>
 <span><b>⊘</b> parked/off-goal</span>
 <span><span class="pill owner">owner</span> human-decided</span>
 <span><span class="pill agent">agent</span> agent-proposed</span>
</div>
{body}
<footer>Source of truth: <code>.pipeline/profile.json</code> (goals) +
 <code>.pipeline/state.json</code> (queue, ledger) — this page is a generated
 mirror. Regenerated by every <code>devteam record</code> /
 <code>research</code>; open with <code>devteam tree {title} --open</code>.</footer>
<script>
document.querySelectorAll('li').forEach(function(li){{
  var kids = li.querySelector(':scope > ul');
  if(!kids) return;
  var box = li.querySelector(':scope > .node');
  if(!box) return;
  var n = kids.querySelectorAll('li').length;
  var t = document.createElement('span');
  t.className = 'toggle'; t.textContent = '▾';
  t.title = 'collapse / expand (' + n + ' below)';
  var c = document.createElement('span');
  c.className = 'count'; c.textContent = n + ' below';
  box.insertBefore(t, box.firstChild);
  box.appendChild(c);
  t.addEventListener('click', function(e){{
    e.stopPropagation();
    var open = kids.style.display !== 'none';
    kids.style.display = open ? 'none' : '';
    t.textContent = open ? '▸' : '▾';
  }});
}});
</script>
</body></html>
"""


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_html(model: dict, title: str) -> str:
    def walk(node: dict) -> str:
        kind = node.get("kind", "branch")
        status = node.get("status", "")
        who = node.get("decided_by", "")
        badge = ""
        if who:
            cls = "owner" if who == "owner" else "agent"
            badge = f'<span class="pill {cls}">{_esc(who)}</span>'
        head = (f'<span class="id">{_esc(node.get("id",""))}</span> '
                f'<span class="title">{_esc(node.get("title",""))}</span> {badge}')
        meta = []
        if node.get("outcome"):
            meta.append(f'<div class="meta">{_esc(node["outcome"])}</div>')
        if node.get("note"):
            meta.append(f'<div class="meta">! {_esc(node["note"])}</div>')
        if node.get("version"):
            meta.append(f'<div class="meta">shipped v{_esc(node["version"])} '
                        f'{_esc(node.get("date",""))}</div>')
        elif node.get("detail"):
            meta.append(f'<div class="meta">{_esc(node["detail"])}</div>')
        if node.get("evidence"):
            meta.append(f'<div class="meta">verdict: {_esc(node.get("verdict",""))} '
                        f'— {_esc(node["evidence"])}</div>')
        for m in node.get("measures") or []:
            meta.append(f'<div class="meas">measure: {_esc(m)}</div>')
        kids = "".join(walk(c) for c in (node.get("children") or []))
        ul = f"<ul>{kids}</ul>" if kids else ""
        return (f'<li class="{kind} {status}"><div class="node">{head}'
                f'{"".join(meta)}</div>{ul}</li>')

    body = f'<ul>{walk(model["root"])}</ul>'
    return _HTML.format(title=_esc(title), generated=_esc(model["generated_at"]),
                        body=body)


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
