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
import subprocess
from pathlib import Path

from .core import now_iso, pipeline_dir, profile_of

AUDIT_VERSION = 2
# Bump when the prompt changes shape. A stored audit under an older rubric is not
# comparable, so the screens become "stale" rather than silently carrying a grade
# measured by a different question.
RUBRIC = "ux-reactions-v2"
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

    A declared path is resolved against the DELIVERY ref (`origin/<main>`) first
    and the owner's checkout second. This audit judges what shipped, and the
    checkout legitimately lags it. Preferring the checkout while it merely HAD the
    file was not enough: a checkout one release behind still holds the old bytes,
    so both fingerprints matched the last audit and the screen it had just changed
    reported "unchanged" (seen 2026-11-07 — the audit skipped exactly the release
    that needed it). The delivery ref wins whenever it holds the file; the
    checkout is the fallback for a project whose main is not fetched.
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
            p = None
            if tree is not None and (tree / rel).exists():
                p = tree / rel
            elif (project / rel).exists():
                p = project / rel
            if p is not None:
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


def _blank_render(png: Path) -> bool:
    """True when the picture is a single flat colour — a render that composed nothing.

    A blank file is not evidence of a blank screen: it usually means the scene did
    not compose (wrong theme, a composable that drew nothing, a failed capture).
    Judging it would record an audit against pixels that are not a screen, so the
    pass reports the capture fault and judges nothing.
    """
    from . import pngcmp
    try:
        w, h, pixels = pngcmp.decode_png(png)
    except Exception:
        return False
    if not pixels or w < 8 or h < 8:
        return False
    x0, x1 = int(w * 0.15), int(w * 0.85)
    y0, y1 = int(h * 0.15), int(h * 0.85)
    sx = max(1, (x1 - x0) // 50)
    sy = max(1, (y1 - y0) // 50)
    colours = set()
    for y in range(y0, y1, sy):
        base = y * w
        for x in range(x0, x1, sx):
            colours.add(pixels[base + x][:3])
            if len(colours) > 2:
                return False
    return len(colours) <= 2


def _store_capture_issue(data: dict, row: dict, line: str) -> None:
    """Attach a capture fault to the surface, so it is visible to the planner.

    A capture fault is not a UI defect — it says "this screen was not reviewed".
    The fingerprints are deliberately NOT written: the surface must stay owed an
    audit, so the next run retries instead of accepting the blank as current.
    """
    label = row["label"]
    surf = (data.get("surfaces") or {}).get(label) or {}
    gripes = [g for g in (surf.get("gripes") or []) if g.get("kind") != "capture"]
    gripes.append({
        "id": gripe_id(row.get("scene") or label[:12], line),
        "line": line,
        "element": "",
        "kind": "capture",
        "severity": "P1_DEGRADED",
        "status": "open",
        "source": "capture",
        "at": now_iso(),
    })
    surf["gripes"] = gripes
    surf["capture_issue"] = line
    data.setdefault("surfaces", {})[label] = surf


def capture_emulator_screenshot(project: Path, label: str) -> Path | None:
    """Capture a screenshot from a booted Android emulator via adb.

    Used when a surface has no off-device Compose preview, or when live emulator
    inspection is requested. Pure stdlib + adb.
    """
    from . import emulator
    running = emulator.running()
    if not running:
        return None
    serial = running[0]["serial"]
    adb_bin = emulator.ADB
    try:
        r = subprocess.run([adb_bin, "-s", serial, "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=20)
        if r.returncode != 0 or not r.stdout.startswith(b"\x89PNG"):
            return None
        out_dir = pipeline_dir(project) / "emulator_scenes"
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-zA-Z0-9_\-]+", "_", label).strip("_")
        out_path = out_dir / f"{slug}.png"
        out_path.write_bytes(r.stdout)
        return out_path
    except Exception:
        return None


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
    render_dir = pipeline_dir(project) / SCENES_NAME
    emu_dir = pipeline_dir(project) / "emulator_scenes"
    if not spec.exists():
        # Even without storefront.json, check emulator_scenes
        if emu_dir.exists():
            for p in emu_dir.glob("*.png"):
                sid = p.stem
                out[sid] = {"id": sid, "surface": sid, "composable": "", "png": str(p)}
        return out
    try:
        data = json.loads(spec.read_text())
    except Exception:
        return out
    for scene in data.get("scenes") or []:
        sid = str(scene.get("id") or "")
        if not sid:
            continue
        png = render_dir / f"{sid}.png"
        if not png.exists() and emu_dir.exists():
            emu_png = emu_dir / f"{sid}.png"
            if emu_png.exists():
                png = emu_png
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
        if isinstance(row.get("gripes"), list):
            row["gripes"] = _demote_unverifiable_still(row["gripes"])
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
    emu_dir = pipeline_dir(project) / "emulator_scenes"
    rows = []
    for s in surfaces(project):
        prev = known.get(s["label"]) or {}
        sid = _scene_for(s["label"], s["paths"], scene_map)
        render = (scene_map.get(sid) or {}).get("png", "") if sid else ""
        if not render and emu_dir.exists():
            slug = re.sub(r"[^a-zA-Z0-9_\-]+", "_", s["label"]).strip("_")
            emu_png = emu_dir / f"{slug}.png"
            if emu_png.exists():
                render = str(emu_png)
        # A render older than the code that drew it is not evidence: the audit
        # would store a fresh fingerprint against stale pixels and hide the very
        # change it exists to catch. It needs a re-render before a reaction.
        stale_render = _render_is_stale(s, render)
        if not prev:
            status = "new"
        elif prev.get("rubric") not in (RUBRIC, "ux-reactions-v1"):
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
    # A scene the project rendered but no declared surface owns is a screen nobody
    # can audit: `stale_for_files` walks surfaces only, so a run that changes such
    # a screen gets no warning and polish on it is never measured (seen 2026-11-09:
    # merge/split/rearrange working screens had scenes and no surface, so a run
    # that changed all three passed `verify`'s UX check silently). Deterministic:
    # it is the set difference between rendered scenes and surface bindings.
    bound = {r["scene"] for r in rows if r.get("scene")}
    unbound = sorted(sid for sid in scene_map if sid not in bound)
    return {"rows": rows, "counts": counts, "rubric": RUBRIC,
            "unbound_scenes": unbound}


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


def scenes_without_surfaces(project: Path, changed: list[str]) -> list[dict]:
    """Rendered scenes this diff changed whose screen no declared surface audits.

    `stale_for_files` can only warn about surfaces, because a surface is what the
    audit is keyed on. A scene the project renders for a screen nobody declared is
    therefore invisible: the run changes the screen, `verify` stays silent, and
    the audit's "nothing changed" reading is about a surface that never existed.
    Matching is exact (the scene's composable identifier against the changed file
    stem), never fuzzy, so a scene mapped to a wrapper cannot claim a file.
    """
    scene_map = scenes(project)
    unbound = set(plan(project).get("unbound_scenes") or [])
    changed_stems = {Path(c).stem.lower() for c in changed}
    out = []
    for sid in sorted(unbound):
        comp = (scene_map.get(sid) or {}).get("composable", "")
        root = comp.split("(")[0].strip().lower()
        if root and root in changed_stems:
            out.append({"scene": sid, "file": root})
    return out


# --------------------------------------------------------------------------- #
# the reaction pass
# --------------------------------------------------------------------------- #
PROMPT = """You are inspecting this mobile screen from an app for quality, usability, and visual blunders.

First, react the way a real user would when opening this screen for the first time (out loud, moment-by-moment).
Second, strictly classify any defects by severity:
- BLUNDERS (P0): Critical visual/interaction blockers that make the screen look broken or unusable:
  * Overlapping text, labels, or buttons
  * Clipped or cut-off primary buttons or inputs
  * Blank/white unrendered surfaces or broken cards
  * Crash dialogs, error stacktraces, or ANR overlays
  * Completely unreadable contrast (e.g. light gray on white)
- DEGRADED (P1): Major usability friction or confusing UX:
  * Unclear hierarchy or confusing affordances (e.g. two competing primary buttons)
  * Missing empty states or unclear loading states
  * Poor readability or accessibility issues
- POLISH (P2): Cosmetic and aesthetic refinements:
  * Minor padding/margin inconsistencies
  * Font weight or sizing nuances
  * Spacing rhythm or micro-copy improvements

Rules:
- Be blunt and specific. Point directly at visible elements (e.g. "Save button in bottom bar", "Top card title").
- If the screen looks solid and has no blunders or degraded issues, leave those lists empty. Do not invent problems.
- Never guess the app's name or brand.

Screen: {label}

Return ONLY this JSON:
{{
  "reactions": [
    {{"at": "00:02", "line": "what does this button do?",
      "kind": "confusing|slow|cheap|inconsistent|frustrating|premium|clear"}}
  ],
  "blunders": [
    {{"line": "exact description of broken layout or blunder", "element": "element name or location"}}
  ],
  "degraded": [
    {{"line": "exact description of major usability flaw", "element": "element name or location"}}
  ],
  "polish": [
    {{"line": "exact description of cosmetic refinement", "element": "element name or location"}}
  ],
  "premium": ["what genuinely feels well-crafted"],
  "verdict": "one sentence verdict on whether this screen is production-grade"
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

# A still render is a static composition with no window and no interaction. Three
# classes of complaint are therefore UNPROVABLE from one, and on 2026-11-08 all
# three were filed as P0 blockers against one dashboard render: a header "drawn
# 37px inside a 63px status bar" (the image contains no status bar at all),
# "the third card is cut off by the bottom navigation bar" (a scrollable list
# legitimately continues), and "CERTIFICATES wraps to two lines" (it renders on
# one). Three releases would have been aimed at a phantom. The device path
# measures what a picture cannot: `ux_video` reads the device's reported
# InsetsSource values and records the scroll. So a still-sourced gripe in these
# classes stays recorded (never silently dropped) but is demoted out of the P0
# blocker set, with the reason attached for the owner.
_UNVERIFIABLE_STILL_CLAIMS = (
    (("status bar", "status-bar", "system bar", "notification bar",
      "system clock", "battery icon", "notch", "home indicator"),
     "system chrome is not in a still render — device insets are measured by "
     "`devteam ux-video`, never judged from a picture"),
    (("cut off", "clipped", "hidden behind", "below the fold", "off-screen",
      "offscreen", "cut by the"),
     "a still cannot tell a scrollable list that continues from content that is "
     "clipped — `devteam ux-video` records the scroll"),
    (("floating action button", "fab ", "fab over", "button overlaps the"),
     "a floating control floats above scrolling content by design; only a "
     "recorded flow shows whether the content underneath stays reachable"),
    (("wraps", "wrapped", "two lines", "ellipsis", "truncat"),
     "text wrapping depends on measured line width, which one still cannot "
     "prove — a hierarchy test or a recorded flow is the check"),
)


def _demote_unverifiable_still(gripes: list[dict]) -> list[dict]:
    """Never let a still render's guess about the device block a release."""
    out: list[dict] = []
    for g in gripes:
        if not isinstance(g, dict):
            out.append(g)
            continue
        text = f"{g.get('line', '')} {g.get('element', '')}".lower()
        if str(g.get("severity") or "") == "P0_BLUNDER" and not g.get("source"):
            why = next((note for terms, note in _UNVERIFIABLE_STILL_CLAIMS
                        if any(t in text for t in terms)), "")
            if why:
                g = {**g, "severity": "P1_DEGRADED",
                     "kind": "unverifiable_still", "note": why}
        out.append(g)
    return out



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


def audit_surface(project: Path, row: dict, prev: dict | None = None, log=None) -> dict:
    """One neutral reaction over the rendered screen. Raises on a model failure."""
    from . import vision_layout, pngcmp
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

    current_dhash = None
    try:
        current_dhash = pngcmp._dhash(png)
    except Exception:
        pass

    # Zero-cost shortcut: if previous audit has recorded dhash matching current render,
    # the visual layout and pixels have not structurally moved despite code touches.
    # We update fingerprints without spending a vision model call ($0.00).
    if (prev and current_dhash is not None and prev.get("dhash") is not None
            and prev.get("dhash") == current_dhash
            and prev.get("rubric") in (RUBRIC, "ux-reactions-v1")):
        if log:
            log(f"~ ux: '{row['label']}' render structurally identical to last audit (dhash={current_dhash}) — skipping model call ($0.00)")
        return {
            "label": row["label"],
            "scene": row.get("scene"),
            "render": str(png),
            "dhash": current_dhash,
            "visual": row["visual"],
            "behaviour": row["behaviour"],
            "rubric": RUBRIC,
            "model": prev.get("model", ""),
            "audited_at": now_iso(),
            "skipped_model": True,
            "reactions": prev.get("reactions") or [],
            "premium": prev.get("premium") or [],
            "verdict": prev.get("verdict", ""),
            "gripes": prev.get("gripes") or [],
        }

    cfg = vision_layout.config()
    if log:
        log(f"~ ux: reacting to '{row['label']}' ({png.name}) via {cfg.get('model','?')}")
    raw = vision_layout._call(cfg, PROMPT.format(label=row["label"]), png)
    result = vision_layout._json_block(raw)
    reactions = result.get("reactions") or []
    blunders = result.get("blunders") or []
    degraded = result.get("degraded") or []
    polish = result.get("polish") or []

    gripes = []
    seen_keys = set()

    for item in blunders:
        line = (item.get("line") or "").strip() if isinstance(item, dict) else str(item).strip()
        if not line:
            continue
        key = " ".join(sorted(_norm_line(line)))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        elem = item.get("element", "") if isinstance(item, dict) else ""
        gripes.append({
            "id": gripe_id(row["scene"] or row["label"][:12], line),
            "line": line,
            "element": elem,
            "kind": "blunder",
            "severity": "P0_BLUNDER",
            "status": "open",
            "at": now_iso(),
        })

    for item in degraded:
        line = (item.get("line") or "").strip() if isinstance(item, dict) else str(item).strip()
        if not line:
            continue
        key = " ".join(sorted(_norm_line(line)))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        elem = item.get("element", "") if isinstance(item, dict) else ""
        gripes.append({
            "id": gripe_id(row["scene"] or row["label"][:12], line),
            "line": line,
            "element": elem,
            "kind": "degraded",
            "severity": "P1_DEGRADED",
            "status": "open",
            "at": now_iso(),
        })

    for item in polish:
        line = (item.get("line") or "").strip() if isinstance(item, dict) else str(item).strip()
        if not line:
            continue
        key = " ".join(sorted(_norm_line(line)))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        elem = item.get("element", "") if isinstance(item, dict) else ""
        gripes.append({
            "id": gripe_id(row["scene"] or row["label"][:12], line),
            "line": line,
            "element": elem,
            "kind": "polish",
            "severity": "P2_POLISH",
            "status": "open",
            "at": now_iso(),
        })

    for r in reactions:
        if not isinstance(r, dict):
            continue
        line = (r.get("line") or "").strip()
        kind = (r.get("kind") or "").strip().lower()
        if not line or kind not in ("confusing", "slow", "cheap", "inconsistent", "frustrating"):
            continue
        key = " ".join(sorted(_norm_line(line)))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        sev = "P1_DEGRADED" if kind in ("confusing", "frustrating") else "P2_POLISH"
        gripes.append({
            "id": gripe_id(row["scene"] or row["label"][:12], line),
            "line": line,
            "element": "",
            "kind": kind,
            "severity": sev,
            "status": "open",
            "at": now_iso(),
        })

    for line in (result.get("annoying") or []):
        if not isinstance(line, str) or not line.strip():
            continue
        clean_line = line.strip()
        key = " ".join(sorted(_norm_line(clean_line)))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        gripes.append({
            "id": gripe_id(row["scene"] or row["label"][:12], clean_line),
            "line": clean_line,
            "element": "",
            "kind": "annoying",
            "severity": "P2_POLISH",
            "status": "open",
            "at": now_iso(),
        })

    return {
        "label": row["label"],
        "scene": row.get("scene"),
        "render": str(png),
        "dhash": current_dhash,
        "visual": row["visual"],
        "behaviour": row["behaviour"],
        "rubric": RUBRIC,
        "model": cfg.get("model", ""),
        "audited_at": now_iso(),
        "reactions": reactions,
        "premium": result.get("premium") or [],
        "verdict": result.get("verdict", ""),
        "gripes": _demote_unverifiable_still(gripes),
    }


DEVICE_DIR = "ux_device"


def _foreground_package(serial: str) -> str:
    """The package Android is actually showing, straight from the window manager."""
    from .emulator import ADB
    try:
        r = subprocess.run(
            [ADB, "-s", serial, "shell", "dumpsys", "window", "windows"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return ""
    text = r.stdout or ""
    focuses = re.findall(r"mCurrentFocus=Window\{[^}]*?\s([A-Za-z][\w.]+)/", text)
    return focuses[-1] if focuses else ""


def launch_app(project: Path, serial: str, log=None, tries: int = 3) -> bool:
    """Bring the app under test to the front, and say whether it really is.

    An audit is evidence about the app, so a frame of the LAUNCHER must never be
    accepted as one: on 2026-11-08 every screen of one project was judged from the
    launcher because the capture path installed the APK and photographed whatever
    was on screen. `install` alone opens nothing. The launch is followed by a
    foreground check against the app's own package id, so a device that refuses
    to start the app yields NO evidence (and the audit says so) instead of a
    confident verdict on somebody else's pixels.
    """
    from . import ux_video
    import time as _time

    try:
        pkg = ux_video.package_name(project)
    except SystemExit as e:
        if log:
            log(f"~ ux: cannot launch the app to photograph it — {e}")
        return False

    for _attempt in range(max(1, tries)):
        ux_video._adb(serial, ["shell", "am", "force-stop", pkg], timeout=30)
        ux_video._adb(
            serial,
            ["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"],
            timeout=45,
        )
        for _ in range(16):
            _time.sleep(0.5)
            if _foreground_package(serial) == pkg:
                if log:
                    log(f"~ ux: '{pkg}' is in front")
                return True

    if log:
        log(f"~ ux: '{pkg}' never came to the front — refusing to photograph the launcher")
    return False


def _device_still(project: Path, serial: str, log=None) -> Path | None:
    """The app's own screen as the device is showing it right now."""
    from .emulator import ADB
    import subprocess as _sp

    # The frame is only evidence if the app is what is on screen. Ask the window
    # manager first, so a launcher/notification shade is never stored as "Score".
    if not launch_app(project, serial, log=log):
        return None

    out_dir = pipeline_dir(project) / DEVICE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"live-{now_iso().replace(':', '')}.png"
    r = _sp.run([ADB, "-s", serial, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=90)
    data = r.stdout or b""
    if not data.startswith(b"\x89PNG"):
        return None
    if not launch_app(project, serial, log=None, tries=1):
        if log:
            log("~ ux: the app left the foreground during the capture — frame discarded")
        return None
    dest.write_bytes(data)
    if log:
        log(f"~ ux: captured the app's own screen -> {dest.name}")
    return dest


def _flow_still_for(project: Path, surface: str) -> Path | None:
    """The newest DEVICE still a declared journey produced for this surface.

    This is why the flow pass exists: it walks the app and keeps a frame per step,
    so a still audit has real device evidence for a screen instead of a render.
    """
    flows_dir = pipeline_dir(project) / "ux_flows"
    best: tuple[float, Path] | None = None
    if not flows_dir.is_dir():
        return None
    for result in flows_dir.glob("*/result.json"):
        try:
            data = json.loads(result.read_text())
        except Exception:
            continue
        if str(data.get("surface") or "").strip().lower() != surface.strip().lower():
            continue
        for still in data.get("stills") or []:
            p = Path(still)
            if p.is_file() and (best is None or p.stat().st_mtime > best[0]):
                best = (p.stat().st_mtime, p)
    return best[1] if best else None


def run_audit(project: Path, only_scene: str | None = None,
              everything: bool = False, capture_emulator: bool = True,
              log=None, avd: str | None = None, device: bool = True) -> dict:
    """Audit the screens this run needs, from DEVICE frames, and record it.

    Owner policy 2026-11-08: ux-audit, ux-video and ux-flow all judge a DEVICE, not
    an off-device render. A picture composed on the JVM cannot show a keyboard over
    the submit button, an inset collision, a session that failed to persist, or a
    network the app cannot reach — which is exactly the class of defect that
    mattered here. `storefront-scenes` keeps the off-device renderer for Play
    screenshots; it is not UX evidence.
    """
    targets = to_audit(project, only_scene=only_scene, everything=everything)
    if device and targets:
        from . import emulator, ux_video
        serial, started = emulator.require_device(avd=avd, log=log, purpose="ux-audit")
        try:
            ux_video.build_and_install(project, serial, log)
            live = _device_still(project, serial, log=log)
            # ONE live frame cannot be evidence for several screens: it shows the
            # screen the app opens on, and nothing else. Assigning it to every
            # label made the audit judge the same picture four times (2026-11-08:
            # a launcher frame, then the same setup form, reported against Score,
            # Summary and History alike). A multi-screen pass therefore needs a
            # per-surface frame, which a declared journey supplies; a single-screen
            # pass — the normal "this screen changed" case — may use the live one.
            per_surface = {row["label"]: _flow_still_for(project, row["label"]) for row in targets}
            have_journey = any(p is not None for p in per_surface.values())
            for row in targets:
                still = per_surface.get(row["label"])
                if still is None and len(targets) == 1:
                    still = live
                if still is not None:
                    row["render"] = str(still)
                elif log:
                    log(f"~ ux: '{row['label']}' has no frame of its own — "
                        "declare a journey (`docs/ux-flows.json` + `devteam ux-flow`) "
                        "so this screen is judged from itself")
            if log:
                log("~ ux: evidence = device stills"
                    + (" (per-surface journey frames)" if have_journey else ""))
        finally:
            if started:
                stopped = emulator.stop(serial)
                if log:
                    log(f"~ ux: emulator {serial} "
                        f"{'stopped' if stopped else 'DID NOT STOP'}")
    data = load(project)
    data["rubric"] = RUBRIC
    data["audit_version"] = AUDIT_VERSION
    done, skipped = [], []
    for row in targets:
        prev = (data.get("surfaces") or {}).get(row["label"]) or {}
        png = Path(row["render"]) if row.get("render") else None
        if png is not None and png.is_file() and _blank_render(png):
            reason = ("the render is a flat colour — the scene composed nothing; "
                      "re-render before judging")
            _store_capture_issue(data, row, reason)
            skipped.append({"label": row["label"], "reason": reason})
            if log:
                log(f"~ ux: '{row['label']}' — {reason}")
            continue
        try:
            res = audit_surface(project, row, prev=prev, log=log)
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
    unbound = p.get("unbound_scenes") or []
    if unbound:
        # Not a gate: the owner decides whether a scene is a screen worth judging
        # or a marketing crop. It is a fact, so a screen the app renders and no
        # surface audits is named instead of silently skipped by every audit.
        lines.append(f"  ⚑ {len(unbound)} rendered scene(s) belong to no declared surface "
                     f"(no changed-screen warning can reach them): {', '.join(unbound[:8])}")
        lines.append("    bind one with a project target named after its composable, "
                     "or drop the scene from docs/storefront.json")
    if open_gripes:
        p0_list = [(l, g) for l, g in open_gripes if g.get("severity") == "P0_BLUNDER"]
        p1_list = [(l, g) for l, g in open_gripes if g.get("severity") == "P1_DEGRADED"]
        p2_list = [(l, g) for l, g in open_gripes if g.get("severity") not in ("P0_BLUNDER", "P1_DEGRADED")]

        lines.append(f"  open gripes: {len(open_gripes)} "
                     f"({len(p0_list)} P0 blunders · {len(p1_list)} P1 degraded · {len(p2_list)} P2 polish)")
        if p0_list:
            lines.append("  🚨 P0 UI BLUNDERS (BLOCKERS):")
            for label, g in p0_list[:6]:
                elem = f" [{g.get('element')}]" if g.get('element') else ""
                lines.append(f"    {g['id']:18} 🚨 {label}{elem}: {g['line'][:80]}")
        if p1_list:
            lines.append("  ⚠️ P1 DEGRADED UX:")
            for label, g in p1_list[:4]:
                lines.append(f"    {g['id']:18} [{g.get('kind', 'degraded')}] {g['line'][:80]}")
        if p2_list:
            lines.append("  ✦ P2 POLISH:")
            for label, g in p2_list[:4]:
                lines.append(f"    {g['id']:18} [{g.get('kind', 'polish')}] {g['line'][:80]}")
    lines.append("  audit now: `devteam ux-audit <project>` (unchanged screens cost $0.00)")
    return "\n".join(lines)
