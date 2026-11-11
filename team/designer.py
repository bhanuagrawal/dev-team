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
import re
import subprocess
from pathlib import Path
from typing import Any

from .core import load_json, now_iso, pipeline_dir, profile_of, save_json

DECISIONS = ("existing_screen", "tweak_screen", "new_screen", "not_applicable")

_UI_TARGET_MARKERS = (
    "composeApp/src", "androidMain", "commonMain", "src/main/java",
    "src/main/res", "/ui/", "Screen", "Activity", "Fragment", "Composable",
)
_NON_UI_TARGET_PREFIXES = ("docs/", "distribution/", ".github/", "gradle", "build.gradle")
_LAYOUT_LINE_RE = re.compile(
    r"\b(?:Column|Row|Box|Button|OutlinedButton|TextButton|Dialog|AlertDialog|"
    r"ModalBottomSheet|Card|LazyColumn|LazyRow|Surface|Scaffold)\s*\("
)
_COMPOSABLE_FUN_RE = re.compile(r"@Composable\b|\bfun\s+([A-Z]\w*)\s*\(")


def _clean_id(value: Any) -> str:
    return str(value or "").strip().split("/")[-1]


def design_profile(project: Path) -> dict:
    return (profile_of(project).get("design") or {})


def manifest(project: Path, root: Path | None = None) -> dict:
    """The screen set: docs/stitch/manifest.json is the tracked source."""
    base = root or project
    tracked = base / "docs" / "stitch" / "manifest.json"
    if tracked.is_file():
        return load_json(tracked) or {}
    if root and root != project:
        proj_tracked = project / "docs" / "stitch" / "manifest.json"
        if proj_tracked.is_file():
            return load_json(proj_tracked) or {}
    return load_json(pipeline_dir(project) / "stitch_manifest.json") or {}


def manifest_project_mismatch(project: Path, root: Path | None = None) -> str | None:
    """Return a refusal message when docs/stitch/manifest.json belongs to a different Stitch project."""
    pid = _clean_id(design_profile(project).get("stitch_project_id"))
    m = manifest(project, root=root)
    mpid = _clean_id(m.get("project_id"))
    if pid and mpid and pid != mpid:
        return (
            f"STITCH MANIFEST MISMATCH — docs/stitch/manifest.json tracks project {mpid}, "
            f"but .pipeline/profile.json declares stitch_project_id {pid}. "
            f"Run `devteam stitch-export {project.name}` (or `devteam stitch-curate {project.name}`) first."
        )
    return None


def find_manifest_screen(manifest_data: dict, screen: dict | None) -> dict | None:
    """Resolve a designer screen reference against manifest.json canonical_screens."""
    if not isinstance(screen, dict):
        return None
    sid = _clean_id(screen.get("id") or screen.get("stitch_id"))
    title = str(screen.get("title") or "").strip().lower()
    screens = [s for s in (manifest_data.get("canonical_screens") or []) if isinstance(s, dict)]
    if sid:
        for s in screens:
            if _clean_id(s.get("id")) == sid or _clean_id(s.get("stitch_id")) == sid:
                return s
    if title:
        for s in screens:
            if str(s.get("title") or "").strip().lower() == title:
                return s
    return None


def snapshot_paths(
    root: Path,
    screen: dict | None,
    manifest_data: dict | None = None,
) -> tuple[Path | None, Path | None, str]:
    """Locate (screen.png, index.html, resolved_id) inside root/docs/stitch/screens/."""
    screens_dir = root / "docs" / "stitch" / "screens"
    if not screens_dir.is_dir():
        return None, None, ""
    entry = find_manifest_screen(manifest_data or {}, screen)
    candidates: list[str] = []
    for raw in (
        (entry or {}).get("id"),
        (screen or {}).get("id"),
        (entry or {}).get("stitch_id"),
        (screen or {}).get("stitch_id"),
    ):
        cid = _clean_id(raw)
        if cid and cid not in candidates:
            candidates.append(cid)
    for cid in candidates:
        d = screens_dir / cid
        if d.is_dir():
            png = d / "screen.png"
            html = d / "index.html"
            return (png if png.is_file() else None), (html if html.is_file() else None), cid
    title = str(((entry or {}).get("title") or (screen or {}).get("title") or "")).strip().lower()
    if title:
        for d in sorted(screens_dir.iterdir()):
            if not d.is_dir():
                continue
            html = d / "index.html"
            if html.is_file() and title in html.read_text(errors="ignore")[:4000].lower():
                png = d / "screen.png"
                return (png if png.is_file() else None), html, d.name
    return None, None, ""


def snapshot_digest(png_path: Path | None, html_path: Path | None) -> str:
    """Content digest of a screen snapshot pair (empty string when missing)."""
    if not (png_path and png_path.is_file() and html_path and html_path.is_file()):
        return ""
    h = hashlib.sha256()
    h.update(png_path.read_bytes())
    h.update(b"\0")
    h.update(html_path.read_bytes())
    return h.hexdigest()[:16]


def diff_ui_summary(worktree: Path, base: str, ui_files: list[str]) -> dict[str, Any]:
    """Inspect the actual UI diff to distinguish copy/wiring tweaks from new/restructured UI."""
    added_ui_files: list[str] = []
    added_composables: list[str] = []
    added_layout_lines = 0

    if not ui_files:
        return {
            "added_ui_files": added_ui_files,
            "added_composables": added_composables,
            "added_layout_lines": added_layout_lines,
            "structural_change": False,
        }

    untracked = set(
        subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=worktree, capture_output=True, text=True,
        ).stdout.splitlines()
    )

    for rel in ui_files:
        in_base = subprocess.run(
            ["git", "cat-file", "-e", f"{base}:{rel}"],
            cwd=worktree, capture_output=True,
        ).returncode == 0
        if rel in untracked or not in_base:
            added_ui_files.append(rel)

    diff_text = subprocess.run(
        ["git", "diff", "-U1", base, "--", *ui_files],
        cwd=worktree, capture_output=True, text=True, errors="ignore",
    ).stdout

    # Collect added lines from git diff plus full contents of new/untracked UI files.
    added_lines: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            added_lines.append(line[1:])
    for rel in added_ui_files:
        p = worktree / rel
        if p.is_file():
            added_lines.extend(p.read_text(errors="ignore").splitlines())

    saw_composable = False
    for raw in added_lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("*"):
            continue
        if "@Composable" in stripped:
            saw_composable = True
            m_inline = re.search(r"@Composable\s+(?:private\s+|internal\s+)?fun\s+([A-Z]\w*)\s*\(", stripped)
            if m_inline and m_inline.group(1) not in added_composables:
                added_composables.append(m_inline.group(1))
                saw_composable = False
            continue
        if saw_composable:
            m_fun = re.search(r"\bfun\s+([A-Z]\w*)\s*\(", stripped)
            if m_fun and m_fun.group(1) not in added_composables:
                added_composables.append(m_fun.group(1))
            if "fun " in stripped or not stripped.startswith("@"):
                saw_composable = False
        if _LAYOUT_LINE_RE.search(stripped):
            added_layout_lines += 1

    structural = bool(added_ui_files or added_composables or added_layout_lines >= 4)
    return {
        "added_ui_files": added_ui_files,
        "added_composables": added_composables,
        "added_layout_lines": added_layout_lines,
        "structural_change": structural,
    }


def fingerprint(project: Path, root: Path | None = None) -> str:
    """What the designer's answer was based on. Any change → re-consult."""
    base = root or project
    d = design_profile(project)
    m = manifest(project, root=root)
    screens_dir = base / "docs" / "stitch" / "screens"
    if not screens_dir.is_dir() and root and root != project:
        screens_dir = project / "docs" / "stitch" / "screens"
    payload = json.dumps(
        {"project": d.get("stitch_project_id"),
         "system": d.get("design_system_id"),
         "screens": m.get("canonical_screens") or [],
         "synced_at": m.get("synced_at") or m.get("updated_at"),
         "snapshots": sorted(
             p.name for p in screens_dir.glob("*")
             if p.is_dir()) if screens_dir.is_dir() else []},
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


def status(project: Path, task: dict, root: Path | None = None) -> dict:
    """answered | pending | stale | not_applicable for THIS task."""
    label = task_label(task)
    if not is_ui_task(task):
        return {"task": label, "state": "not_applicable",
                "reason": "no UI targets — design consultation not needed"}
    rec = (load_records(project).get("tasks") or {}).get(label)
    fp = fingerprint(project, root=root)
    if not rec:
        return {"task": label, "state": "pending", "fingerprint": fp,
                "reason": "the designer has not been asked for this task"}
    # For tweak_screen / new_screen, exporting the updated screen changes the
    # manifest/snapshots fingerprint AFTER record(); accept that progression
    # when the answer itself was recorded for this task and the target screen
    # now lives in the manifest/snapshots.
    if str(rec.get("fingerprint") or "") != fp:
        decision = str(rec.get("decision") or "")
        if decision in ("tweak_screen", "new_screen"):
            m = manifest(project, root=root)
            png, html, _ = snapshot_paths(root or project, rec.get("screen"), m)
            if png and html:
                return {"task": label, "state": "answered", "fingerprint": fp, **rec}
        return {"task": label, "state": "stale", "fingerprint": fp,
                "previous": rec,
                "reason": "the Stitch project or manifest changed since the answer"}
    return {"task": label, "state": "answered", "fingerprint": fp, **rec}


def compile_brief(project: Path, task: dict, root: Path | None = None) -> dict:
    """The designer's prompt. The host agent runs it and records the answer."""
    d = design_profile(project)
    m = manifest(project, root=root)
    screens = m.get("canonical_screens") or []
    label = str((task.get("surface") or {}).get("label") or task.get("label") or "")
    targets = targets_of(task)
    screen_lines = "\n".join(
        f'- {s.get("title")} (id: {s.get("id") or s.get("screen_id")})'
        + (f' — surface: {s.get("surface")}' if s.get("surface") else "")
        for s in screens) or "- (manifest empty — list the Stitch project first)"
    fp = fingerprint(project, root=root)
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
1. existing_screen — an existing screen ALREADY covers the UI with NO structural/layout
   changes (copy or wiring fixes only; adding new composables or layout sections is
   refused by `verify`). Name the screen id/title from the manifest and what to reuse.
2. tweak_screen — an existing screen covers this surface and needs a targeted visual
   change (new card, button, notice, or section while preserving the rest). Name the
   existing screen id/title and give the exact surgical change for `devteam stitch-tweak`
   (which exports the updated snapshot into `docs/stitch/screens/<id>/`).
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
  "fingerprint":"{fp}"}}
"""
    return {"task": label, "fingerprint": fp,
            "project": d.get("stitch_project_id"),
            "design_system": d.get("design_system_id"),
            "screens": screens, "prompt": prompt}


def record(project: Path, task: dict, answer: dict, root: Path | None = None) -> dict:
    mismatch = manifest_project_mismatch(project, root=root)
    if mismatch:
        raise ValueError(mismatch)
    label = task_label(task)
    decision = str(answer.get("decision") or "").strip()
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    screen = answer.get("screen") or {}
    if decision in ("existing_screen", "tweak_screen", "new_screen") and not (
            screen.get("title") or screen.get("id")):
        raise ValueError(f"{decision} needs screen.title or screen.id")

    base = root or project
    m = manifest(project, root=root)
    baseline_sha = ""
    resolved_id = _clean_id(screen.get("id"))
    if decision in ("existing_screen", "tweak_screen"):
        entry = find_manifest_screen(m, screen)
        if not entry:
            raise ValueError(
                f"{decision} references screen {screen!r}, which is not in docs/stitch/manifest.json. "
                f"Sync the manifest (`devteam stitch-export {project.name}`) or choose `new_screen`."
            )
        png, html, sid = snapshot_paths(base, entry, m)
        if not (png and html):
            raise ValueError(
                f"{decision} references screen '{entry.get('title') or entry.get('id')}', "
                "but its exported snapshot (screen.png + index.html) is missing in docs/stitch/screens/."
            )
        resolved_id = sid or _clean_id(entry.get("id"))
        baseline_sha = snapshot_digest(png, html)
        screen = {**screen, "id": resolved_id, "title": entry.get("title") or screen.get("title") or ""}

    rec = {
        "task": label,
        "decision": decision,
        "screen": screen,
        "tweak": str(answer.get("tweak") or "")[:1000],
        "reason": str(answer.get("reason") or "")[:1000],
        "actions": [str(a) for a in (answer.get("actions") or [])][:10],
        "fingerprint": fingerprint(project, root=root),
        "manifest_project_id": _clean_id(m.get("project_id")),
        "baseline_snapshot_sha": baseline_sha,
        "consulted_at": now_iso(),
        "consulted_by": str(answer.get("consulted_by") or "agent"),
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
