"""UX audit — per SCREEN, on change, not per release.

WHY THIS EXISTS
---------------
A run usually does not touch how the app looks or feels, so auditing every release
is waste. But a NEW screen (or a changed one) ships with nobody judging it, and the
app drifts into inconsistency one orphan screen at a time. The unit is therefore
the SCREEN, and the trigger is a fingerprint, not a calendar:

  visual     the screen's composables + the theme/token files + docs/design.md
  behaviour  the state/view-model/repository files that feed that screen

Neither changed → the pixels and the feel are identical: say "unchanged" and spend
nothing. Visual changed → re-audit that screen. Behaviour changed while pixels did
not ("looks the same, feels different": a network call, a new error state, a
disabled button) → re-run that flow's `first-run` replay. Tokens changed → every
screen moved at once, so the whole set is stale.

WHAT THE AUDIT IS
-----------------
One neutral first-use reaction over the app's OWN render (the rendered scene, not
a mock): the model reacts in a user's words, one line per moment, and the gripes
are kept as a list the planner can draw from. It is deliberately NOT a 1-10 score:
a number invites optimizing the number.

The artifacts stay machine-local (`.pipeline/ux_audit.json`); nothing here is
committed, because this is evidence about a build, not a deliverable.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .core import now_iso, pipeline_dir, profile_of

AUDIT_VERSION = 1
# Bump when the prompt changes shape. A stored audit under an older rubric is not
# comparable, so the screens become "stale" rather than silently carrying a grade
# measured by a different question.
RUBRIC = "ux-reactions-v1"
STORE_NAME = "ux_audit.json"
SCENES_NAME = "storefront_scenes"

# Files that define how EVERY screen looks. A change here re-audits the whole app —
# the only cheap way to catch "the contrast dropped everywhere".
THEME_NAMES = {"color.kt", "colour.kt", "type.kt", "theme.kt", "dimens.kt",
               "dimensions.kt", "tokens.kt", "shape.kt", "shapes.kt", "typography.kt"}


# --------------------------------------------------------------------------- #
# fingerprints
# --------------------------------------------------------------------------- #
def _normalise(text: str) -> str:
    """Strip comments and collapse whitespace before hashing.

    A comment cannot move a pixel, and hashing raw bytes would re-audit a screen
    because someone reworded a TODO — the fingerprint should answer "did the
    rendering inputs change", not "was the file touched".
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _hash_files(paths: list[Path], root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(set(paths)):
        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = p
        h.update(str(rel).encode())
        try:
            h.update(_normalise(p.read_text(errors="ignore")).encode())
        except Exception:
            h.update(b"<unreadable>")
    return h.hexdigest()[:16]


def theme_paths(project: Path) -> list[Path]:
    """Theme/token inputs, declared or discovered.

    `profile['ux']['theme_files']` wins when a project declares its design system;
    otherwise the conventional names are found anywhere in the module. Generic:
    no project is named here.
    """
    declared = ((profile_of(project).get("ux") or {}).get("theme_files") or [])
    out = [project / d for d in declared if (project / d).exists()]
    if out:
        design = project / "docs" / "design.md"
        if design.exists():
            out.append(design)
        return out
    for p in project.rglob("*.kt"):
        if "__pycache__" in p.parts or "/build/" in str(p):
            continue
        if p.name.lower() in THEME_NAMES:
            out.append(p)
    design = project / "docs" / "design.md"
    if design.exists():
        out.append(design)
    return out


def _feature_files(screen: Path) -> list[Path]:
    """The behaviour inputs of a screen: its feature package, minus the UI file.

    Convention over configuration: a screen lives at `.../feature/<x>/ui/<X>Screen.kt`
    (or next to its state in a flat package), so the sibling package is where its
    view model, state, repository and use cases live. A change there can change how
    the screen FEELS (waits, errors, disabled states) with zero visual diff.
    """
    parent = screen.parent
    feature = parent.parent if parent.name == "ui" else parent
    files = []
    try:
        for p in feature.rglob("*.kt"):
            if "/build/" in str(p) or "__pycache__" in p.parts:
                continue
            if p.resolve() == screen.resolve():
                continue
            files.append(p)
    except Exception:
        pass
    return files


def surfaces(project: Path) -> list[dict]:
    """Every shipped screen with its two fingerprints. Discovery is `scan`'s job.

    A declared path is resolved against the checkout first and the DELIVERY ref
    (`origin/<main>`) second. This audit judges what shipped, and the owner's
    checkout legitimately lags it — resolving only locally made a stale checkout
    report zero surfaces, so the audit silently had nothing to look at (the same
    class of failure as an unwired capability: a check that cannot fire).
    """
    from . import scan
    theme = theme_paths(project)
    try:
        tree = scan.origin_tree(project)
    except Exception:
        tree = None
    out = []
    for s in scan.scan_surfaces(project, tree=tree):
        declared = [t for t in (s.get("paths") or s.get("targets") or [])
                    if Path(t).suffix == ".kt"]
        paths = []
        for rel in declared:
            p = project / rel
            if not p.exists() and tree is not None and (tree / rel).exists():
                p = tree / rel
            if p.exists():
                paths.append(p)
        if not paths:
            continue
        behaviour = []
        for p in paths:
            behaviour += _feature_files(p)
        out.append({
            "label": s.get("label") or paths[0].stem,
            "paths": [str(p) for p in paths],
            # The files whose mtime decides whether a render is still evidence.
            "inputs": [str(p) for p in paths + theme],
            "visual": _hash_files(paths + theme, project),
            "behaviour": _hash_files(behaviour, project),
        })
    return out


# --------------------------------------------------------------------------- #
# scenes: the render this app already ships for its own listing
# --------------------------------------------------------------------------- #
def scenes(project: Path) -> dict[str, dict]:
    """`docs/storefront.json` scenes + the PNG each one rendered.

    Reusing the storefront render is deliberate: those pixels come from the app's
    own composables (layoutlib/Robolectric, no emulator), so the audit judges the
    real screen and costs seconds. A surface with no scene needs a capture path
    (a scene binding, or the emulator replay) before it can be audited.
    """
    spec = project / "docs" / "storefront.json"
    out: dict[str, dict] = {}
    if not spec.exists():
        return out
    try:
        data = json.loads(spec.read_text())
    except Exception:
        return out
    render_dir = pipeline_dir(project) / SCENES_NAME
    for scene in data.get("scenes") or []:
        sid = str(scene.get("id") or "")
        if not sid:
            continue
        png = render_dir / f"{sid}.png"
        out[sid] = {
            "id": sid,
            # An explicit binding wins: a scene may show a screen whose composable
            # is a wrapper, and guessing from a name is how the wrong image gets
            # judged (the first run mapped six screens to the hero shot).
            "surface": str(scene.get("surface") or ""),
            "composable": str(scene.get("composable") or ""),
            "png": str(png) if png.exists() else "",
        }
    return out


def _scene_for(label: str, paths: list[str], scene_map: dict[str, dict]) -> str | None:
    """Which rendered scene shows this surface — by explicit binding, then the
    composable's own identifier. No fuzzy label matching: a wrong image is worse
    than no image, so an unmatched surface reports "no render" and waits for a
    capture path."""
    for sid, sc in scene_map.items():
        if sc.get("surface") and sc["surface"].strip().lower() == label.strip().lower():
            return sid
    stems = {Path(p).stem.lower() for p in paths}
    for sid, sc in scene_map.items():
        comp = sc["composable"].split("(")[0].strip().lower()
        if comp and comp in stems:
            return sid
    return None


# --------------------------------------------------------------------------- #
# the registry + the plan
# --------------------------------------------------------------------------- #
def store_path(project: Path) -> Path:
    return pipeline_dir(project) / STORE_NAME


def _migrate_ids(data: dict) -> dict:
    """Re-key stored gripes that still carry an ordinal id (see `gripe_id`).

    Deterministic and model-free: the complaint's line is already on disk, so the
    stable id can be derived from it without spending another reaction pass.
    """
    for label, row in (data.get("surfaces") or {}).items():
        scene = str(row.get("scene") or label[:12])
        groups = [row.get("gripes") or []]
        # History snapshots too: an old ordinal id sitting there would later read
        # as the SAME complaint as a current one, which is exactly the confusion
        # the stable id removes.
        groups += [h.get("gripes") or [] for h in (row.get("history") or [])
                   if isinstance(h, dict)]
        groups += [h.get("resolved") or [] for h in (row.get("history") or [])
                   if isinstance(h, dict)]
        for group in groups:
            for g in group:
                if not isinstance(g, dict):
                    continue
                gid = str(g.get("id") or "")
                if gid.startswith("UX-") and LEGACY_ID.search(gid):
                    g["id"] = gripe_id(scene, g.get("line") or "")
    return data


def load(project: Path) -> dict:
    p = store_path(project)
    if not p.exists():
        return {"audit_version": AUDIT_VERSION, "rubric": RUBRIC, "surfaces": {}}
    try:
        return _migrate_ids(json.loads(p.read_text()))
    except Exception:
        return {"audit_version": AUDIT_VERSION, "rubric": RUBRIC, "surfaces": {}}


def save(project: Path, data: dict) -> None:
    p = store_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def _render_is_stale(row: dict, render: str) -> bool:
    """True when a visual input is newer than the PNG that is supposed to show it."""
    if not render:
        return False
    try:
        png_mtime = Path(render).stat().st_mtime
    except OSError:
        return False
    for p in row.get("inputs") or []:
        try:
            if Path(p).stat().st_mtime > png_mtime:
                return True
        except OSError:
            continue
    return False


def plan(project: Path) -> dict:
    """What needs an audit now — the whole point: the deciding half is free."""
    data = load(project)
    known = data.get("surfaces") or {}
    scene_map = scenes(project)
    rows = []
    for s in surfaces(project):
        prev = known.get(s["label"]) or {}
        sid = _scene_for(s["label"], s["paths"], scene_map)
        render = (scene_map.get(sid) or {}).get("png", "") if sid else ""
        # A render older than the code that drew it is not evidence: the audit
        # would store a fresh fingerprint against stale pixels and hide the very
        # change it exists to catch. It needs a re-render before a reaction.
        stale_render = _render_is_stale(s, render)
        if not prev:
            status = "new"
        elif prev.get("rubric") != RUBRIC:
            status = "rubric"
        elif stale_render:
            status = "render"
        elif prev.get("visual") != s["visual"]:
            status = "visual"
        elif prev.get("behaviour") != s["behaviour"]:
            status = "behaviour"
        else:
            status = "unchanged"
        rows.append({
            **s, "status": status, "scene": sid, "render": render,
            "stale_render": stale_render,
            "audited_at": prev.get("audited_at", ""),
            "gripes": [g for g in (prev.get("gripes") or []) if g.get("status") == "open"],
        })
    counts = {
        "new": sum(1 for r in rows if r["status"] == "new"),
        "changed": sum(1 for r in rows if r["status"] in ("visual", "behaviour", "rubric")),
        "stale_render": sum(1 for r in rows if r["status"] == "render"),
        "unchanged": sum(1 for r in rows if r["status"] == "unchanged"),
        "no_render": sum(1 for r in rows if not r["render"]),
    }
    return {"rows": rows, "counts": counts, "rubric": RUBRIC}


def to_audit(project: Path, only_scene: str | None = None,
             everything: bool = False) -> list[dict]:
    """The rows this run should audit (changed + new, or all, or one scene)."""
    p = plan(project)
    rows = p["rows"]
    if only_scene:
        rows = [r for r in rows if r["scene"] == only_scene]
    elif not everything:
        rows = [r for r in rows if r["status"] != "unchanged"]
    return [r for r in rows if r["render"]]


def stale_for_files(project: Path, changed: list[str]) -> list[dict]:
    """Surfaces this diff touches whose look/feel nobody has audited since.

    Used by `verify` as a WARNING: the gates stay compile/tests, but a run that
    changes a screen should not be able to claim it polished anything without a
    reaction on record.
    """
    changed_set = {str(c).replace("\\", "/") for c in changed}
    out = []
    for r in plan(project)["rows"]:
        if r["status"] == "unchanged":
            continue
        touched = [x for x in r["paths"]
                   if any(x.replace("\\", "/").endswith(c) or c.endswith(x) for c in changed_set)]
        if touched:
            out.append({"label": r["label"], "status": r["status"],
                        "scene": r["scene"], "paths": touched})
    return out


# --------------------------------------------------------------------------- #
# the reaction pass
# --------------------------------------------------------------------------- #
PROMPT = """You are opening this app for the first time. This is one screen of it.

React the way a real person would, out loud, one line per moment — the way someone
talks while using an app for the first time. Then sort the reactions.

Rules:
- Judge UX only: clarity, hierarchy, density, spacing, contrast, affordance,
  consistency, state coverage, what feels premium and what feels cheap.
- Be blunt. If it looks unfinished, say so. Do not compliment by default.
- Every reaction must point at something visible on the screen (a control, a
  label, a gap, a missing state) — no generic praise or generic complaints.
- You do not know the app's name and must not guess it. Never mention a brand.

Screen: {label}

Return ONLY this JSON:
{{
  "reactions": [
    {{"at": "00:02", "line": "where do I start? two buttons look equally primary",
      "kind": "confusing|slow|cheap|inconsistent|frustrating|premium|clear"}}
  ],
  "premium": ["what genuinely feels considered"],
  "annoying": ["what a user would call out in the first minute"],
  "verdict": "one sentence: would this person keep using it?"
}}
"""


def _norm_line(s: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(w) > 3}


def gripe_id(scene: str, line: str) -> str:
    """A STABLE id for one complaint: derived from its words, not its position.

    Ordinal ids (`score-4`) were reused by every pass, so the same id meant a
    different complaint one audit later and `resolved` was measuring nothing —
    a queue entry could quote an id whose meaning had already changed under it.
    A hash of the normalised line moves only when the complaint's words do.
    """
    key = " ".join(sorted(_norm_line(line))) or (line or "").strip().lower()
    return f"UX-{scene}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"


LEGACY_ID = re.compile(r"-(\d+|a\d+)$")


def _resolved(old: list[dict], new_lines: list[str]) -> list[dict]:
    """Gripes the new pass no longer mentions (naive token overlap).

    The proof half of the loop: a polish run should make its own gripe disappear,
    and that has to be visible without a human re-reading two transcripts.
    """
    new_tokens = [_norm_line(x) for x in new_lines if x]
    gone = []
    for g in old:
        toks = _norm_line(g.get("line", ""))
        if not toks:
            continue
        if not any(len(toks & nt) >= max(2, len(toks) // 2) for nt in new_tokens):
            gone.append({**g, "status": "gone", "gone_at": now_iso()})
    return gone


def audit_surface(project: Path, row: dict, log=None) -> dict:
    """One neutral reaction over the rendered screen. Raises on a model failure."""
    from . import vision_layout
    png = Path(row["render"])
    if not png.exists():
        raise SystemExit(f"no render for '{row['label']}' at {png} — run "
                         f"`devteam storefront-build {project.name} --scenes` "
                         "(or bind a scene in docs/storefront.json)")
    # A render is evidence only while it is newer than the code that drew it.
    # Fingerprints cannot catch this on their own: the hash is taken from the
    # SOURCE at audit time, so reacting to a stale PNG records a FRESH fingerprint
    # against OLD pixels — and the change it was supposed to catch becomes
    # invisible forever (found 2026-11-05: four screens re-audited on renders from
    # three days and two releases earlier). Refuse; never record.
    newer = [p for p in (row.get("inputs") or [])
             if Path(p).exists() and Path(p).stat().st_mtime > png.stat().st_mtime]
    if newer:
        raise SystemExit(
            f"stale render for '{row['label']}': {len(newer)} source file(s) are "
            f"newer than {png.name} — re-render with "
            f"`devteam storefront-scenes {project.name}`, then audit again "
            f"(nothing recorded: an old picture is not evidence)")
    cfg = vision_layout.config()
    if log:
        log(f"~ ux: reacting to '{row['label']}' ({png.name}) via {cfg.get('model','?')}")
    raw = vision_layout._call(cfg, PROMPT.format(label=row["label"]), png)
    result = vision_layout._json_block(raw)
    reactions = result.get("reactions") or []
    lines = [r.get("line", "") for r in reactions if isinstance(r, dict)]
    gripes = []
    for i, line in enumerate(lines, 1):
        kind = next((r.get("kind") for r in reactions
                     if isinstance(r, dict) and r.get("line") == line), "")
        if kind in ("confusing", "slow", "cheap", "inconsistent", "frustrating"):
            gripes.append({"id": gripe_id(row["scene"] or row["label"][:12], line),
                           "line": line, "kind": kind, "status": "open",
                           "at": now_iso()})
    for line in (result.get("annoying") or []):
        if isinstance(line, str) and line.strip():
            gripes.append({"id": gripe_id(row["scene"] or row["label"][:12], line),
                           "line": line.strip(), "kind": "annoying",
                           "status": "open", "at": now_iso()})
    return {
        "label": row["label"],
        "scene": row.get("scene"),
        "render": str(png),
        "visual": row["visual"],
        "behaviour": row["behaviour"],
        "rubric": RUBRIC,
        "model": cfg.get("model", ""),
        "audited_at": now_iso(),
        "reactions": reactions,
        "premium": result.get("premium") or [],
        "verdict": result.get("verdict", ""),
        "gripes": gripes,
    }


def run_audit(project: Path, only_scene: str | None = None,
              everything: bool = False, log=None) -> dict:
    """Audit the screens this run needs, and record what it found."""
    targets = to_audit(project, only_scene=only_scene, everything=everything)
    data = load(project)
    data["rubric"] = RUBRIC
    data["audit_version"] = AUDIT_VERSION
    done, skipped = [], []
    for row in targets:
        prev = (data.get("surfaces") or {}).get(row["label"]) or {}
        try:
            res = audit_surface(project, row, log=log)
        except SystemExit as e:
            skipped.append({"label": row["label"], "reason": str(e)[:200]})
            continue
        earlier = prev.get("gripes") or []
        gone = _resolved(earlier, [g.get("line", "") for g in res["gripes"]])
        history = (prev.get("history") or [])[-4:]
        if earlier:
            history.append({"at": prev.get("audited_at", ""), "gripes": earlier,
                            "resolved": gone})
        res["history"] = history
        data.setdefault("surfaces", {})[row["label"]] = res
        done.append(res)
    save(project, data)
    return {"audited": done, "skipped": skipped, "targets": len(targets)}


def report(p: dict) -> str:
    """The owner-facing block: what changed, what it costs, what is open."""
    icon = {"new": "✦", "visual": "◆", "behaviour": "◇", "rubric": "↻",
            "render": "⟳", "unchanged": "·"}
    lines = [f"UX PLAN — {p['counts']['new']} new · {p['counts']['changed']} changed · "
             f"{p['counts']['unchanged']} unchanged · {p['counts']['no_render']} without a render"
             + (f" · {p['counts']['stale_render']} with a STALE RENDER (re-render first)"
                if p["counts"].get("stale_render") else "")]
    for r in p["rows"]:
        if r["status"] == "unchanged":
            continue
        gripes = len(r["gripes"])
        lines.append(f"  {icon.get(r['status'], '?')} {r['status']:11} {r['label'][:44]:44} "
                     f"scene={r['scene'] or '—'} · {gripes} open")
    open_gripes = [(r["label"], g) for r in p["rows"] for g in r["gripes"]]
    if open_gripes:
        lines.append(f"  open gripes: {len(open_gripes)}")
        for label, g in open_gripes[:6]:
            lines.append(f"    {g['id']:18} [{g['kind']}] {g['line'][:90]}")
    lines.append("  audit now: `devteam ux-audit <project>` (unchanged screens cost nothing)")
    return "\n".join(lines)
