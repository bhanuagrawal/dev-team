"""Import-aware surface scan — fully profile-driven, zero project names.

A surface counts as animated if its file contains motion primitives (raw
Compose motion APIs) or the project's shared motion components (collected into
profile['motion_tokens'] at onboarding). Unanimated composable files matching
profile['target_name_re'] become tasks in the human-reviewed queue.
"""
from __future__ import annotations

import re
from pathlib import Path

from .core import now_iso, profile_of, state_of

from . import goals

CORE_MOTION_TOKENS = [
    "AnimatedVisibility", "AnimatedContent", "Animatable", "crossfade",
    "animateContentSize", "animateFloatAsState", "animateColorAsState",
    "animateDpAsState", "animateIntAsState",
]

RISK_NAME_RE = re.compile(r"customi[sz]e|grid|drag|gesture", re.I)

SCAN_TREE = ".pipeline/scan"  # worktree holding origin/main for reading


def origin_tree(project: Path, log=None) -> Path:
    """A worktree at origin/main. Reading project code for scans/discovery is
    ALWAYS done here — never in the user's local checkout, which may be stale,
    diverged, or carry WIP. Developers/release also work in worktrees, so the
    entire pipeline sees origin state and never the local file system state."""
    from .core import git
    wt = project / SCAN_TREE
    git(["fetch", "origin"], cwd=project, timeout=120)
    if (wt / ".git").exists():
        git(["checkout", "--detach", "--force", "origin/main"], cwd=wt, timeout=60)
        git(["clean", "-fd"], cwd=wt, timeout=60)
    else:
        r = git(["worktree", "add", "--detach", str(wt), "origin/main"], cwd=project)
        if r.returncode != 0:
            raise SystemExit("cannot create scan worktree: " + (r.stdout + r.stderr)[-500:])
        git(["clean", "-fd"], cwd=wt, timeout=60)
    return wt


def _targets_of(t: dict) -> list[str]:
    return t.get("paths") or ([t["path"]] if t.get("path") else [])


def _label_from_name(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).strip().lower()


def _rel_to_repo(profile: dict, path_text: str) -> str:
    """Paths in profile/tasks are repo-relative."""
    src_prefix = profile["src_root"].rsplit("/", 1)[0]
    return path_text.removeprefix(src_prefix.rstrip("/") + "/")


def discover_surfaces(project: Path) -> list[dict]:
    """Onboarding discovery: unanimated composable files = draft queue.
    Always scans the origin/main snapshot, never the local checkout."""
    profile = profile_of(project)
    tree = origin_tree(project)
    src = tree / profile["src_root"]
    skip = profile.get("skip_name_re")
    out = []
    for p in sorted(src.rglob("*.kt")):
        text = p.read_text(errors="ignore")
        if "@Composable" not in text:
            continue
        if skip and re.search(skip, p.stem):
            continue
        if not re.search(profile.get("target_name_re", "."), p.stem):
            continue
        if any(t in text for t in profile["motion_tokens"] + CORE_MOTION_TOKENS):
            continue
        risk = "supervised" if RISK_NAME_RE.search(p.stem) else "safe"
        out.append({"label": _label_from_name(p.stem),
                    "paths": [p.relative_to(tree).as_posix()],
                    "risk": risk})
    return out


def task_for(profile: dict, t: dict, seq: int) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "-", t["label"].lower()).strip("-")
    return {
        "id": f"dt-{slug}-{seq:03d}",
        "type": "motion",
        "surface": {"label": t["label"], "targets": list(_targets_of(t))},
        "risk": t.get("risk", "safe"),
        "created": now_iso(),
    }


def scan_surfaces(project: Path) -> list[dict]:
    profile = profile_of(project)
    state = state_of(project)
    tree = origin_tree(project)
    shipped = {s["target"] for s in state.get("shipped", [])}
    out = []
    for t in profile["targets"]:
        targets = _targets_of(t)
        if any(x in shipped for x in targets):
            out.append({**t, "targets": targets, "status": "shipped"})
            continue
        per = []
        for x in targets:
            p = tree / x
            if not p.exists():
                per.append("missing")
                continue
            text = p.read_text(errors="ignore")
            hits = [tok for tok in profile["motion_tokens"] + CORE_MOTION_TOKENS if tok in text]
            per.append("motion" if hits else "STATIC")
        status = ("animated" if all(d != "STATIC" for d in per) else "unanimated")
        out.append({**t, "targets": targets, "status": status,
                    "detail": "; ".join(per)})
    return out


def _norm_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _shipped_labels(state: dict) -> set[str]:
    """Normalized labels of delivered work (the ledger's task identity)."""
    return {_norm_label(s.get("label")) for s in state.get("shipped", [])
            if s.get("label")}


def _queue_task(q: dict, seq: int) -> dict:
    """A code task derived from one queue entry (bugfix|feature)."""
    return {
        "id": "dt-" + re.sub(r"[^a-z0-9]+", "-", q["label"].lower())[:30].strip("-")
              + f"-{seq:03d}",
        "type": q["type"],
        "surface": {"label": q["label"],
                    "targets": (q["target"] if isinstance(q["target"], list)
                                else [q["target"]])},
        "risk": q.get("risk", "safe"),
        # The leaf carries its branch with it: the developer prompt shows which
        # goal this run serves, and `record` attributes the shipped leaf from the
        # spec instead of re-matching the label (which the next refill rewrites).
        "advances": str(q.get("advances") or ""),
        "reason": str(q.get("reason") or ""),
        "created": now_iso(),
    }


def _label_task_slug(label: str) -> str:
    """The task-id slug a label gets at pick time (see `_queue_task`) — kept
    identical on purpose: it is the only thing that ties a queue label to the
    task id in the run ledger."""
    return re.sub(r"[^a-z0-9]+", "-", str(label or "").lower())[:30].strip("-")


def _delivered_slugs_from_runs(state: dict) -> set[str]:
    """Task-id slugs of delivered work, read from the RUN ledger instead of the
    shipped ledger.

    Belt for a real gap (lesson 2026-10-18): `record --result shipped` writes the
    shipped-ledger rows from the task-spec's targets, so a shipped run recorded
    WITHOUT its spec leaves no ledger rows — and then the label filter above is
    blind, so `pick` re-serves already-delivered work as the run's head (a queue
    entry outlived the release that delivered it this way, and the plan's "next
    task" was work that had already shipped). The runs ledger always carries the
    task id, and that id is derived from the label, so the id is the identity."""
    out: set[str] = set()
    for r in state.get("runs") or []:
        if r.get("result") != "shipped":
            continue
        tid = str(r.get("task_id") or "")
        if not tid.startswith("dt-"):
            continue
        slug = re.sub(r"-\d+$", "", tid[3:]).strip("-")
        if slug:
            out.add(slug)
    return out


def next_task(project: Path, seq: int, supervised: bool = False) -> dict | None:
    state = state_of(project)
    profile = profile_of(project)
    # GOAL GATE (owner directive 2026-10-18): a project nobody has aimed does not
    # get a task. `pick` refuses with its own rc and owner-ask payload; this is the
    # belt for every other caller.
    if goals.vision_required(profile):
        return None
    # A queue entry whose label is already delivered is delivered work — never
    # serve it again (label-level; a feature may legitimately re-touch a shipped
    # path, so path matching stays out of this check). Both ledgers are consulted:
    # the shipped ledger is authoritative, the run ledger catches the runs that
    # were recorded without a task-spec.
    shipped_labels = _shipped_labels(state)
    delivered_slugs = _delivered_slugs_from_runs(state)
    queue = state.get("queue") or []

    def _eligible(q: dict) -> bool:
        return (q.get("type") in ("bugfix", "feature", "motion")
                and q.get("target")
                and _norm_label(q.get("label")) not in shipped_labels
                and _label_task_slug(q.get("label")) not in delivered_slugs)

    def _buildable(q: dict, surfaces: list[dict]) -> bool:
        """Eligible AND not already obsolete: a motion entry whose surface has
        since animated (or is missing) has nothing left to do, so it is skipped —
        the planner's own impact review would have dropped it, this is the belt."""
        if not _eligible(q):
            return False
        if q.get("risk") == "supervised" and not supervised:
            return False
        if q.get("type") != "motion":
            return True
        lab = _norm_label(q.get("label"))
        for s in surfaces:
            if _norm_label(s.get("label")) == lab:
                return (s.get("status") == "unanimated"
                        and not (s.get("risk") == "supervised" and not supervised))
        return False

    # Delivery-first escalation (owner decision 2026-10-16: the pipeline is
    # self-healing — when the last run FAILED, restoring delivery (the research
    # queue's executable head) outranks everything; the orchestrator reruns
    # without re-asking, max 2 redelivery loops).
    if (state.get("last_run", {}) or {}).get("result") == "failed":
        for q in queue:
            if _eligible(q) and q.get("type") in ("bugfix", "feature"):
                return _queue_task(q, seq)
    # THE PLANNER'S HANDOVER (owner decision 2026-10-18): the head of the queue is
    # what this run builds. The planner re-reads reality and maintains the queue
    # every cycle, so its order IS the decision — the un-animated-surface list
    # below is only the fallback for when nothing queued is buildable. This holds
    # whether or not the project declares a goal tree: a maintained queue that the
    # runner ignored was dead weight (goal-less projects never built a queued
    # task while any polish surface was left).
    surfaces = scan_surfaces(project)
    for q in queue:
        if _buildable(q, surfaces):
            return _queue_task(q, seq)
    for t in surfaces:
        if t.get("status") != "unanimated":
            continue
        if t.get("risk") == "supervised" and not supervised:
            continue
        return task_for(profile, t, seq)
    return None
