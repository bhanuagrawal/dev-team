"""Designer consultation — the developer ASKS the designer; the plan does not
hardcode a design answer.

Why this exists (owner directive 2026-11-09): a per-feature `design: ready|
derive|needed` label is a snapshot of a design that can change the next day. The
plan records *intent* ("this needs to reuse the detail screen" at most); the
authority is a consultation with the designer, who reads the CURRENT Stitch
project and its exported manifest, and answers for THIS task.

Contract:
- `brief()` compiles the designer prompt from disk: project id, design system,
  canonical screens, their exported snapshots, the task. The host agent runs the designer
  (native subagent or the `designer` role), optionally using the Stitch MCP.
- `record()` stores the designer's answer with a fingerprint of the design state.
- `status()` returns answered | pending | stale | not_applicable. If the Stitch
  project or the manifest changed, an old answer is STALE — ask again.
- Prefer `tweak_screen` (`stitch-tweak` -> `edit_screens` / `generate_variants`)
  over `new_screen` whenever an existing screen already covers the surface.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .core import load_json, now_iso, pipeline_dir, profile_of, save_json

DECISIONS = ("existing_screen", "tweak_screen", "new_screen", "not_applicable")

_UI_TARGET_MARKERS = (
    "composeApp/src", "androidMain", "commonMain", "src/main/java",
    "src/main/res", "/ui/", "Screen", "Activity", "Fragment", "Composable",
)
_NON_UI_TARGET_PREFIXES = ("docs/", "distribution/", ".github/", "gradle", "build.gradle")


def design_profile(project: Path) -> dict:
    return (profile_of(project).get("design") or {})


def manifest(project: Path) -> dict:
    """The screen set: docs/stitch/manifest.json is the tracked source."""
    tracked = project / "docs" / "stitch" / "manifest.json"
    if tracked.is_file():
        return load_json(tracked) or {}
    return load_json(pipeline_dir(project) / "stitch_manifest.json") or {}


def fingerprint(project: Path) -> str:
    """What the designer's answer was based on. Any change → re-consult."""
    d = design_profile(project)
    m = manifest(project)
    payload = json.dumps(
        {"project": d.get("stitch_project_id"),
         "system": d.get("design_system_id"),
         "screens": m.get("canonical_screens") or [],
         "synced_at": m.get("synced_at") or m.get("updated_at"),
         "snapshots": sorted(
             p.name for p in (project / "docs" / "stitch" / "screens").glob("*")
             if p.is_dir()) if (project / "docs" / "stitch" / "screens").is_dir() else []},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def targets_of(task: dict) -> list[str]:
    surface = task.get("surface") or {}
    t = surface.get("targets") or task.get("target") or task.get("targets") or []
    if isinstance(t, str):
        return [t]
    return [str(x) for x in t if x]


def is_ui_task(task: dict) -> bool:
    """Does this task write UI? Derived from targets, not from a hand label."""
    label = task_label(task)
    if label.startswith("design-"):
        return False  # this task produces design law/screens, it does not consume one
    targets = targets_of(task)
    if targets and all(t.startswith(_NON_UI_TARGET_PREFIXES) for t in targets):
        return False
    text = " ".join(targets)
    return any(m in text for m in _UI_TARGET_MARKERS)


def _records_path(project: Path) -> Path:
    return pipeline_dir(project) / "design_consult.json"


def load_records(project: Path) -> dict:
    return load_json(_records_path(project)) or {}


def status(project: Path, task: dict) -> dict:
    """answered | pending | stale | not_applicable for THIS task."""
    label = task_label(task)
    if not is_ui_task(task):
        return {"task": label, "state": "not_applicable",
                "reason": "no UI targets — design consultation not needed"}
    rec = (load_records(project).get("tasks") or {}).get(label)
    fp = fingerprint(project)
    if not rec:
        return {"task": label, "state": "pending", "fingerprint": fp,
                "reason": "the designer has not been asked for this task"}
    if str(rec.get("fingerprint") or "") != fp:
        return {"task": label, "state": "stale", "fingerprint": fp,
                "previous": rec,
                "reason": "the Stitch project or manifest changed since the answer"}
    return {"task": label, "state": "answered", "fingerprint": fp, **rec}


def compile_brief(project: Path, task: dict) -> dict:
    """The designer's prompt. The host agent runs it and records the answer."""
    d = design_profile(project)
    m = manifest(project)
    screens = m.get("canonical_screens") or []
    label = str((task.get("surface") or {}).get("label") or task.get("label") or "")
    targets = targets_of(task)
    screen_lines = "\n".join(
        f'- {s.get("title")} (id: {s.get("id") or s.get("screen_id")})'
        + (f' — surface: {s.get("surface")}' if s.get("surface") else "")
        for s in screens) or "- (manifest empty — list the Stitch project first)"
    prompt = f"""You are the designer for this project. Answer for ONE task, from the CURRENT design state.

CURRENT DESIGN STATE
- Stitch project: {d.get("stitch_project_id") or "(not registered)"}
- Design system: {d.get("design_system_id") or "(not registered)"}
- Snapshot dir: docs/stitch/screens/<screen-id>/ (screen.png + index.html)
- Canonical screens:
{screen_lines}

TASK
- label: {label}
- problem: {task.get("problem") or task.get("reason") or ""}
- user sees: {task.get("user_sees") or ""}
- targets: {", ".join(targets) or "—"}

Before answering, inspect the live Stitch project with the Stitch MCP tools and
refresh the manifest if it is stale. Then choose the LEAST DESTRUCTIVE option (ONE):
1. existing_screen — an existing screen covers it with no design change needed; name
   the screen id/title and what to reuse. Do not redesign it.
2. tweak_screen — an existing screen already covers this surface and only needs a
   targeted tweak (add/adjust/refine elements while preserving the rest). Name the
   existing screen id/title and give the exact surgical change for `devteam stitch-tweak`
   (uses `edit_screens` / `generate_variants --range REFINE`). Do NOT redesign from scratch.
3. new_screen — no existing screen covers this surface at all. Give the screen title
   and a locked prompt for `devteam stitch-screen --generate` (passing the project's
   design system). Export its snapshot before writing UI.
4. not_applicable — no UI surface.

Answer as JSON only:
{{"decision":"existing_screen|tweak_screen|new_screen|not_applicable",
  "screen":{{"id":"...","title":"..."}},
  "tweak":"exact surgical change instruction if tweak_screen, else empty string",
  "reason":"one short paragraph",
  "actions":["what the developer must do"],
  "fingerprint":"{fingerprint(project)}"}}
"""
    return {"task": label, "fingerprint": fingerprint(project),
            "project": d.get("stitch_project_id"),
            "design_system": d.get("design_system_id"),
            "screens": screens, "prompt": prompt}


def record(project: Path, task: dict, answer: dict) -> dict:
    label = task_label(task)
    decision = str(answer.get("decision") or "").strip()
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    if decision in ("existing_screen", "tweak_screen", "new_screen") and not (
            (answer.get("screen") or {}).get("title") or (answer.get("screen") or {}).get("id")):
        raise ValueError(f"{decision} needs screen.title or screen.id")
    rec = {
        "task": label,
        "decision": decision,
        "screen": answer.get("screen") or {},
        "tweak": str(answer.get("tweak") or "")[:1000],
        "reason": str(answer.get("reason") or "")[:1000],
        "actions": [str(a) for a in (answer.get("actions") or [])][:10],
        "fingerprint": fingerprint(project),
        "consulted_at": now_iso(),
        "consulted_by": str(answer.get("consulted_by") or "designer"),
    }
    data = load_records(project)
    data.setdefault("tasks", {})[label] = rec
    save_json(_records_path(project), data)
    return rec


def line(project: Path, task: dict) -> str:
    st = status(project, task)
    if st["state"] == "not_applicable":
        return "design: not applicable (no UI)"
    if st["state"] == "pending":
        return "design: ASK THE DESIGNER before UI work"
    if st["state"] == "stale":
        return "design: the design changed — ask the designer again"
    d = st.get("decision")
    if d == "existing_screen":
        return f'design: use {st.get("screen", {}).get("title") or "the existing screen"}'
    if d == "tweak_screen":
        return (f'design: tweak {st.get("screen", {}).get("title") or "the existing screen"} '
                f'(apply targeted Stitch edit, do not redesign)')
    if d == "new_screen":
        return f'design: new screen required — {st.get("screen", {}).get("title") or "not named"}'
    return f"design: {d}"


def task_label(task: dict) -> str:
    return str((task.get("surface") or {}).get("label")
               or task.get("label") or task.get("id") or "")
