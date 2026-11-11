"""The context block of a task spec — the facts a developer is told to read.

Why this exists (owner directive 2026-11-11)
--------------------------------------------
`skills/dev-team/references/develop.md` tells a coding agent to read
`context.goal`, `context.conventions`, `context.build.compile`, `context.design`
and `context.journeys` from the task spec. For a while none of those keys was
ever written: `pick` copied the queue entry and stopped, so the instruction
layer described a spec the data layer did not produce. The worst consequence was
the design answer — the one gate that refuses UI written without a Stitch design
— arriving only at `verify`, after the screen had already been invented.

This module builds that block once, at pick time, from the same sources the
gates read:

  * goal           → the milestone the task advances, with its outcome/measures
  * conventions    → the skills and in-repo docs that define the idiom
  * build          → the profile's own compile/test commands
  * design         → `designer.status` for THIS task, plus the exact command
                     that unblocks it (a UI task with no answer is BLOCKED)
  * journeys       → the declared user flows the change must not break

It is deterministic and offline; nothing here calls a model.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import designer, goals
from .core import TEAM_ROOT, profile_of


def _goal_context(profile: dict, advances: str) -> dict:
    want = str(advances or "").strip()
    if not want:
        return {}
    node = next((n for n in goals.nodes(profile) if n["id"] == want), None)
    if node is None:
        return {}
    raw, _ = goals.raw_node(profile, want)
    raw = raw or {}
    return {
        "id": want,
        "title": str(raw.get("title") or node.get("title") or ""),
        "outcome": str(raw.get("outcome") or ""),
        "measures": [
            str(m.get("text"))
            for m in (raw.get("measures") or [])
            if isinstance(m, dict) and str(m.get("text") or "").strip()
        ],
    }


def _conventions(project: Path) -> list[str]:
    paths: list[str] = [str(TEAM_ROOT / "skills" / "android-code" / "SKILL.md")]
    if (project / "composeApp").is_dir():
        paths.append(str(Path.home() / ".agents" / "skills" / "kmp-compose-multiplatform" / "SKILL.md"))
    for name in ("ARCHITECTURE.md", "AGENTS.md"):
        candidate = project / name
        if candidate.exists() and candidate.stat().st_size < 60_000:
            paths.append(str(candidate))
    out: list[str] = []
    for path in paths:
        out.append(path if Path(path).exists() else f"SKIP: not found — {path}")
    return out


def _journeys(project: Path, limit: int = 12) -> list[dict]:
    try:
        data = json.loads((project / "docs" / "ux-flows.json").read_text())
    except Exception:
        return []
    flows = data.get("flows") if isinstance(data, dict) else data
    if not isinstance(flows, list):
        return []
    out: list[dict] = []
    for flow in flows:
        if not isinstance(flow, dict) or not flow.get("id"):
            continue
        out.append({
            "id": str(flow.get("id")),
            "surface": str(flow.get("surface") or ""),
            "goal": str(flow.get("goal") or ""),
        })
    return out[:limit]


def design_context(project: Path, task: dict) -> dict:
    """What the design gate will require for THIS task, said before any UI code."""
    label = designer.task_label(task)
    try:
        ui_task = designer.is_ui_task(task)
        st = designer.status(project, task)
    except Exception as exc:  # never lose a spec to a reading error
        return {
            "state": "unknown",
            "required": True,
            "task": label,
            "instruction": f"The design state could not be read ({exc}). Run "
                           f"`devteam design-consult {project.name} --task {label} --prompt` "
                           "before writing UI.",
        }

    stitch = str((profile_of(project).get("design") or {}).get("stitch_project_id") or "")
    if not ui_task:
        return {
            "state": "not_applicable",
            "required": False,
            "task": label,
            "reason": st.get("reason") or "no UI targets",
            "instruction": "No UI surface — no design needed. Do not add UI structure anyway.",
        }

    state = str(st.get("state") or "unknown")
    decision = str(st.get("decision") or "")
    screen = st.get("screen") if isinstance(st.get("screen"), dict) else {}
    sid = str((screen or {}).get("id") or (screen or {}).get("screen_id") or "")
    title = str((screen or {}).get("title") or "")
    command = f"devteam design-consult {project.name} --task {label} --prompt"
    base = {
        "task": label,
        "state": state,
        "required": True,
        "stitch_project_id": stitch,
        "command": command,
        "decision": decision,
        "screen": screen or {},
        "screens_dir": f"docs/stitch/screens/{sid}/" if sid else "",
    }

    if state in ("pending", "stale"):
        base["blocked"] = True
        base["instruction"] = (
            ("UI code is BLOCKED: the designer has not answered for this task."
             if state == "pending"
             else "UI code is BLOCKED: the Stitch project changed since the answer.")
            + f" Run `{command}`, record the answer with `--record`, and"
            " generate/tweak + export the Stitch screen (index.html + screen.png)"
            " before writing any composable or layout. `devteam verify` refuses a"
            " UI diff with no recorded design."
        )
        return base

    if state == "answered" and decision == "existing_screen":
        base["blocked"] = False
        base["instruction"] = (
            f"Reuse the existing screen '{title}' ({sid}) at {base['screens_dir']}"
            " — copy/wiring fixes only."
            " Adding new composables or layout structure is refused by `verify`;"
            " use `tweak_screen` instead when the surface really changes."
        )
        return base

    if state == "answered" and decision in ("tweak_screen", "new_screen"):
        base["blocked"] = False
        base["instruction"] = (
            f"Build to the exported Stitch screen '{title}' ({sid}) at"
            f" {base['screens_dir']} — match its layout, type, spacing and imagery,"
            " and include that snapshot in this change's diff."
        )
        base["actions"] = list(st.get("actions") or [])
        return base

    if state == "answered":
        base["blocked"] = False
        base["instruction"] = f"Design answered: {decision or 'recorded'}."
        return base

    base["blocked"] = True
    base["instruction"] = (
        "The design state is unreadable. Run `" + command + "` and record the answer"
        " before writing UI."
    )
    return base


def context(project: Path, task: dict) -> dict:
    """The whole `context` block. Deterministic; safe to call more than once."""
    profile = profile_of(project)
    verify = profile.get("verify") or {}
    return {
        "goal": _goal_context(profile, str(task.get("advances") or "")),
        "conventions": _conventions(project),
        "build": {
            "compile": str(verify.get("compile") or ""),
            "test": str(verify.get("test") or ""),
        },
        "design": design_context(project, task),
        "journeys": _journeys(project),
    }


def attach(project: Path, task: dict) -> dict:
    """Return [task] with its context attached (never crashing the picker)."""
    try:
        task = dict(task)
        task["context"] = context(project, task)
    except Exception as exc:  # a context read must not cost the run
        task = dict(task)
        task.setdefault("context", {})["error"] = str(exc)
    return task


def refresh(project: Path, spec_path: Path) -> dict:
    """Rewrite one existing spec's context in place (for a task already picked)."""
    spec = json.loads(Path(spec_path).read_text())
    spec = attach(project, spec)
    Path(spec_path).write_text(json.dumps(spec, indent=2) + "\n")
    return spec
