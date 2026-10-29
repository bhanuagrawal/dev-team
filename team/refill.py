"""Autonomous Horizon Planning & Queue Refill Engine for dev-team.

Ensures that the engineering loop never stalls at an empty queue.
When all roadmap tasks under active milestones are shipped, the research agent:
1. Conducts an autonomous market & competitor gap scan via Tavily.
2. Evaluates the owner's long-term vision.
3. Formulates the next milestone horizon.
4. Appends the milestone to the goal tree and refills the queue with sized, executable tasks.

Zero project names in code. Pure standard library.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import goals, publish, tavily, tree
from .core import RunLog, now_iso, pipeline_dir, profile_of, save_json, state_of
from .scan import already_delivered


def needs_refill(project: Path) -> bool:
    """Returns True if the queue has zero actionable (un-delivered) tasks."""
    state = state_of(project)
    queue = state.get("queue") or []
    if not queue:
        return True
    return all(already_delivered(q, state) for q in queue)


def _detect_code_targets(project: Path, profile: dict) -> list[str]:
    """Derive valid repo-relative code target globs for tasks."""
    allowed = profile.get("allowed_paths") or []
    code_allowed = [
        a for a in allowed
        if not any(k in a for k in ("whatsnew", "distribution", ".github", "gradle", "version"))
    ]
    if code_allowed:
        return code_allowed

    src_root = profile.get("src_root")
    if src_root:
        return [f"{src_root}/**"]

    for d in ("composeApp", "app", "src", "lib"):
        if (project / d).is_dir():
            return [f"{d}/**"]
    return ["src/**"]


def _derive_horizon(project: Path,
                    profile: dict,
                    answer: str,
                    results: list[dict],
                    existing_titles: list[str]) -> tuple[str, str, list[str], list[dict]]:
    """Synthesize next milestone horizon and concrete tasks from market research and project vision."""
    project_name = profile.get("name") or project.name
    clean_text = f"{answer} " + " ".join(r.get("content", "")[:300] for r in results[:3])
    low = clean_text.lower()

    candidates = []
    if any(k in low for k in ("analytic", "stat", "history", "trend", "insight", "chart", "breakdown")):
        candidates.append((
            f"{project_name} Performance Insights & Session Analytics",
            f"Provides deep session analytics, performance metric breakdowns, and historical trends for {project_name}.",
            [
                "Users can review detailed per-session run distribution and efficiency statistics.",
                "Visual timelines render session momentum using the app design system tokens.",
                "Historical summaries can be filtered by format and exported.",
            ],
            [
                ("Session Analytics Engine: metric collection, streaks, and run efficiency tracking", 150),
                ("Visual Performance Dashboard: interactive timeline charts per docs/design.md", 180),
                ("Export & Historical Insights: session card generation and trend breakdowns", 150),
            ]
        ))

    if any(k in low for k in ("tournament", "round robin", "rotation", "group", "bracket", "pool")):
        candidates.append((
            f"{project_name} Tournament & Group Rotation Mode",
            f"Enables organizers and recreational groups to manage round-robin rotations and pool brackets in {project_name}.",
            [
                "Organizers can configure round-robin and pool rotation play.",
                "Automated standings update dynamically as match results are confirmed.",
                "Court rotation schedules can be shared across participating players.",
            ],
            [
                ("Group Rotation Engine: round-robin scheduling and dynamic standings calculations", 180),
                ("Tournament Overview Screen: pool tables and court rotation view", 180),
                ("Match Result Confirmation: fast scoring workflow into group standings", 150),
            ]
        ))

    if any(k in low for k in ("wearable", "watch", "wrist", "companion", "remote", "voice", "audio")):
        candidates.append((
            f"{project_name} Hands-Free & Companion Play Mode",
            f"Streamlines active gameplay with audio feedback, wrist companion integration, and hands-free interaction.",
            [
                "Active state is broadcast to companion/wearable endpoints or remote displays.",
                "Voice and audio cues notify players during state transitions and match events.",
                "Quick-action remote controls allow rapid score progression without device unlocking.",
            ],
            [
                ("Audio Cue & Announcement Engine: contextual event notifications", 150),
                ("Companion Remote Protocol: lightweight sync state for secondary displays", 180),
                ("Hands-Free Interaction Flow: high-contrast large-target quick-action layout", 150),
            ]
        ))

    chosen = None
    for cand in candidates:
        if cand[0] not in existing_titles:
            chosen = cand
            break

    if not chosen:
        horizon_title = f"{project_name} Player Platform & Advanced Workflows"
        if horizon_title in existing_titles:
            horizon_title = f"{horizon_title} Phase {len(existing_titles) + 1}"
        outcome = f"Expands {project_name} beyond core tracking into an integrated player experience."
        measures = [
            f"Advanced workflows are accessible from the main navigation.",
            f"User settings and historical records persist reliably across sessions.",
            f"All UI interactions adhere to high contrast, haptic feedback, and design tokens.",
        ]
        task_tuples = [
            ("Core Workflow Expansion: state persistence and player preference management", 150),
            ("Interactive Overview Screen: dashboard summaries and custom session setup", 180),
            ("Performance & Accessibility Hardening: focus traversal and responsive layouts", 150),
        ]
    else:
        horizon_title, outcome, measures, task_tuples = chosen

    return horizon_title, outcome, measures, task_tuples


def auto_refill(project: Path,
                topic: str = "",
                force: bool = False,
                log: RunLog | None = None) -> dict[str, Any]:
    """Refills the queue by researching and proposing the next milestone horizon."""
    if not needs_refill(project) and not force:
        if log:
            log("~ queue already has buildable tasks — auto-refill not required")
        return {"status": "not_needed"}

    profile = profile_of(project)
    state = state_of(project)
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    if not vision:
        if log:
            log("~ cannot refill queue: no long-term goal defined in profile.goals.vision")
        return {"status": "no_vision"}

    project_name = profile.get("name") or project.name
    if log:
        log(f"~ auto-refill triggered: scanning market & competitor horizon for {project_name}")

    query = topic or f"{project_name} {vision} modern mobile app top user feature requests competitor gaps 2026"
    market_report = tavily.search_market(project, query, log=log)
    answer = market_report.get("answer", "")
    results = market_report.get("results", []) or []

    existing_milestones = (profile.get("goals") or {}).get("milestones", [])
    existing_titles = [m.get("title", "") for m in existing_milestones]

    horizon_title, outcome, measures, task_tuples = _derive_horizon(
        project=project,
        profile=profile,
        answer=answer,
        results=results,
        existing_titles=existing_titles,
    )

    # 1. Apply new milestone under vision
    change = {
        "action": "add",
        "under": "vision",
        "title": horizon_title,
        "outcome": outcome,
        "measures": [{"text": m} for m in measures],
    }
    node_id, what, why, err = goals.apply_one(profile, change, by="agent")
    if err:
        if log:
            log(f"~ auto-refill failed to add milestone: {err}")
        return {"status": "goal_error", "error": err}

    # 2. Build tasks linked to node_id
    targets = _detect_code_targets(project, profile)
    primary_target = ",".join(targets[:3]) if targets else "src/**"

    tasks = []
    for idx, (label, effort) in enumerate(task_tuples, start=1):
        tasks.append({
            "type": "feature",
            "label": label,
            "target": primary_target,
            "effort_min": effort,
            "priority": idx,
            "reason": f"Advances {horizon_title}: implements core capability per specification",
            "advances": node_id,
            "supersedes": [],
        })

    # 3. Append new tasks to queue
    current_queue = state.get("queue") or []
    # Drop already delivered tasks from queue head to keep queue tidy
    active_queue = [q for q in current_queue if not already_delivered(q, state)]
    active_queue.extend(tasks)
    state["queue"] = active_queue
    state["last_research_at"] = now_iso()

    # 4. Persist changes
    pdir = pipeline_dir(project)
    save_json(pdir / "profile.json", profile)
    save_json(pdir / "state.json", state)

    # 5. Refresh plan mirrors
    tree.write(project)
    publish.publish(project)

    if log:
        log(f"~ auto-refill complete: added milestone '{horizon_title}' and queued {len(tasks)} tasks")

    return {
        "status": "refilled",
        "milestone": horizon_title,
        "tasks_added": len(tasks),
        "total_queued": len(active_queue),
    }
