"""Bidirectional plan sync — makes `docs/plan.md` the single source of truth.

The owner's contract: `docs/plan.md` is committed and pushed on GitHub, readable
by every human and agent. If someone edits `docs/plan.md` directly (or if a fresh
clone has no local `.pipeline/` cache), the pipeline parses `docs/plan.md` and
synchronizes it into `.pipeline/profile.json` (goals/milestones) and
`.pipeline/state.json` (queue/shipped).

Zero project names in code. Pure standard library.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .core import now_iso, pipeline_dir, profile_of, save_json, save_state, state_of


def parse_plan_md(text: str) -> dict[str, Any]:
    """Parse a docs/plan.md file into structured vision, milestones, queue, and shipped items."""
    out: dict[str, Any] = {
        "vision": "",
        "milestones": [],
        "queue": [],
        "shipped": [],
    }

    if not text.strip():
        return out

    # 1. Parse Vision
    m_vis = re.search(r"## The goal[^\n]*\n+>\s*([^\n]+)", text)
    if m_vis:
        out["vision"] = m_vis.group(1).strip()

    # 2. Parse Milestones under "## The plan"
    plan_sec = ""
    m_plan = re.search(r"## The plan[^\n]*\n([\s\S]+?)(?=\n##\s+The queue|\n##\s+Already done|\n---|$)", text)
    if m_plan:
        plan_sec = m_plan.group(1)

    # Match milestones: "### [●○] Title — _status_" or "### Title"
    ms_matches = re.finditer(
        r"###\s+(?:[●○]\s+)?([^\n—_]+?)(?:\s+—\s+_(.+?)_)?\n+([\s\S]+?)(?=\n###|\Z)",
        plan_sec,
    )
    for m in ms_matches:
        title = m.group(1).strip()
        status_word = (m.group(2) or "active").strip().lower()
        body = m.group(3).strip()

        # Map status words to canonical status
        status = "active"
        if "progress" in status_word or "active" in status_word:
            status = "active"
        elif "done" in status_word or "shipped" in status_word or "completed" in status_word:
            status = "done"
        elif "parked" in status_word or "paused" in status_word:
            status = "parked"
        elif "proposed" in status_word:
            status = "proposed"

        # Extract technical details if present
        m_id = re.search(r"-\s*id:\s*([^\n]+)", body)
        m_status = re.search(r"-\s*status:\s*([^\n]+)", body)
        m_by = re.search(r"-\s*set by:\s*([^\n]+)", body)
        m_why = re.search(r"-\s*why:\s*([^\n]+)", body)

        mid = m_id.group(1).strip() if m_id else _slugify(title)
        if m_status:
            status = m_status.group(1).strip()
        decided_by = m_by.group(1).strip() if m_by else "owner"
        if decided_by in ("you", "owner"):
            decided_by = "owner"
        elif decided_by in ("the team", "agent"):
            decided_by = "agent"

        # Outcome is the first paragraph before details/progress
        outcome_parts = []
        for line in body.splitlines():
            line_str = line.strip()
            if line_str.startswith("<details") or line_str.startswith("`█") or line_str.startswith("`░"):
                break
            if line_str:
                outcome_parts.append(line_str)
        outcome = " ".join(outcome_parts)

        # Measures
        measures = []
        m_meas = re.search(r"<summary>how we will know it is done</summary>([\s\S]+?)</details>", body)
        if m_meas:
            measures = [re.sub(r"^-\s*", "", l).strip() for l in m_meas.group(1).strip().splitlines() if l.strip().startswith("-")]

        why = m_why.group(1).strip() if m_why else ""

        out["milestones"].append({
            "id": mid,
            "kind": "milestone",
            "title": title,
            "decided_by": decided_by,
            "status": status,
            "why": why,
            "outcome": outcome,
            "measures": [{"text": m} for m in measures] if measures else [],
            "children": [],
        })

    # 3. Parse Queue under "## The queue"
    queue_sec = ""
    m_q = re.search(r"## The queue[^\n]*\n([\s\S]+?)(?=\n##\s+Already done|\n---|$)", text)
    if m_q:
        queue_sec = m_q.group(1)

    # Technical details block for queue items
    tech_details: dict[str, dict[str, str]] = {}
    m_tech = re.search(r"<summary>technical detail for the queue</summary>([\s\S]+?)</details>", queue_sec)
    if m_tech:
        for line in m_tech.group(1).strip().splitlines():
            line = line.strip()
            # Format: "1 · Title — type: feature · serves: Milestone · goal id: m1 · target: ui · why: Reason"
            m_td = re.match(r"(\d+)\s+·\s+([^\n—]+)\s+—\s+([\s\S]+)", line)
            if m_td:
                idx = m_td.group(1)
                meta_str = m_td.group(3)
                meta: dict[str, str] = {}
                for bit in meta_str.split("·"):
                    if ":" in bit:
                        k, v = bit.split(":", 1)
                        meta[k.strip()] = v.strip()
                tech_details[idx] = meta

    q_items = re.findall(r"(?:^|\n)\s*(\d+)\.\s+\*\*([^*]+)\*\*(?:\s+·\s+for:\s*([^\n·]+))?", queue_sec)
    for idx_str, q_title, q_for in q_items:
        td = tech_details.get(idx_str, {})
        advances = td.get("goal id") or td.get("advances") or ""
        if not advances and q_for:
            # Try to match milestone title to id
            for m in out["milestones"]:
                if m["title"].lower() in q_for.lower() or q_for.lower() in m["title"].lower():
                    advances = m["id"]
                    break

        out["queue"].append({
            "type": td.get("type") or "feature",
            "label": q_title.strip(),
            "reason": td.get("why") or f"Advances {q_for.strip() if q_for else 'plan'}",
            "target": td.get("target") or "ui",
            "advances": advances,
            "effort_min": int(td.get("effort_min", 45)) if td.get("effort_min", "").isdigit() else 45,
        })

    # 4. Parse Shipped under "## Already done"
    done_sec = ""
    m_d = re.search(r"## Already done[^\n]*\n([\s\S]+?)(?=\n---|$)", text)
    if m_d:
        done_sec = m_d.group(1)

    done_items = re.findall(r"(?:^|\n)\s*-\s+✓\s+\*\*([^*]+)\*\*(?:\s+·\s+v([0-9.]+))?(?:\s+·\s+([0-9-]+))?", done_sec)
    for d_title, d_ver, d_date in done_items:
        out["shipped"].append({
            "title": d_title.strip(),
            "version": d_ver.strip() if d_ver else "",
            "date": d_date.strip() if d_date else "",
        })

    return out


def _slugify(text: str) -> str:
    """Generate a clean slug identifier from a title string."""
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return clean[:30] or "milestone"


def sync_from_plan_md(project: Path, log: Any = None) -> bool:
    """Sync goals, milestones, and queue from docs/plan.md into profile.json and state.json.

    Returns True if plan.md existed and was successfully ingested.
    """
    plan_file = project / "docs/plan.md"
    if not plan_file.is_file():
        return False

    content = plan_file.read_text(encoding="utf-8", errors="ignore")
    parsed = parse_plan_md(content)

    if not parsed["milestones"] and not parsed["vision"]:
        return False

    profile = profile_of(project)
    state = state_of(project)

    goals_block = profile.get("goals") or {}
    changed_profile = False
    changed_state = False

    # Sync Vision
    if parsed["vision"] and goals_block.get("vision") != parsed["vision"]:
        goals_block["vision"] = parsed["vision"]
        changed_profile = True

    # Sync Milestones
    if parsed["milestones"]:
        existing_ms = goals_block.get("milestones") or []
        existing_ids = {m.get("id"): m for m in existing_ms if isinstance(m, dict)}

        updated_ms = []
        for pm in parsed["milestones"]:
            mid = pm["id"]
            if mid in existing_ids:
                # Merge existing details
                curr = existing_ids[mid]
                curr["title"] = pm["title"]
                curr["status"] = pm["status"]
                if pm["outcome"]:
                    curr["outcome"] = pm["outcome"]
                if pm["measures"]:
                    curr["measures"] = pm["measures"]
                if pm["why"]:
                    curr["why"] = pm["why"]
                updated_ms.append(curr)
            else:
                updated_ms.append(pm)
                changed_profile = True

        if len(updated_ms) != len(existing_ms):
            changed_profile = True

        goals_block["milestones"] = updated_ms
        profile["goals"] = goals_block

    # Sync Queue if plan.md defines queue items
    if parsed["queue"]:
        state_queue = state.get("queue") or []
        existing_labels = {q.get("label") for q in state_queue if isinstance(q, dict)}
        
        # Check if plan.md has new queue entries
        has_new_queue = any(pq["label"] not in existing_labels for pq in parsed["queue"])
        if has_new_queue or len(parsed["queue"]) != len(state_queue):
            state["queue"] = parsed["queue"]
            changed_state = True

    # Save updates to disk
    if changed_profile:
        p_path = pipeline_dir(project) / "profile.json"
        save_json(p_path, profile)
        if log:
            log(f"plan_sync: updated profile.json goals from docs/plan.md ({len(parsed['milestones'])} milestones)")

    if changed_state:
        save_state(project, state)
        if log:
            log(f"plan_sync: updated state.json queue from docs/plan.md ({len(parsed['queue'])} tasks)")

    return changed_profile or changed_state
