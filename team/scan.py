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


def next_task(project: Path, seq: int, supervised: bool = False) -> dict | None:
    state = state_of(project)
    # Delivery-first escalation (owner decision 2026-10-16: the pipeline is
    # self-healing — when the last run FAILED, restoring delivery (the research
    # queue's executable head) outranks motion polish; the orchestrator reruns
    # without re-asking, max 2 redelivery loops).
    if (state.get("last_run", {}) or {}).get("result") == "failed":
        for q in state.get("queue", [])[:1]:
            if q.get("type") in ("bugfix", "feature") and q.get("target"):
                return {
                    "id": "dt-" + re.sub(r"[^a-z0-9]+", "-", q["label"].lower())[:30].strip("-")
                          + f"-{seq:03d}",
                    "type": q["type"],
                    "surface": {"label": q["label"],
                                "targets": (q["target"] if isinstance(q["target"], list)
                                            else [q["target"]])},
                    "risk": q.get("risk", "safe"),
                    "created": now_iso(),
                }
    for t in scan_surfaces(project):
        if t.get("status") != "unanimated":
            continue
        if t.get("risk") == "supervised" and not supervised:
            continue
        return task_for(profile_of(project), t, seq)
    # surfaces exhausted → the research queue refills (priority-1 entry only;
    # the nightly rhythm is ONE task per run). queue entries carry their own
    # target; investigation-type stays orchestrator-run, not a code task.
    for q in state.get("queue", [])[:1]:
        if q.get("type") not in ("bugfix", "feature") or not q.get("target"):
            continue
        return {
            "id": "dt-" + re.sub(r"[^a-z0-9]+", "-", q["label"].lower())[:30].strip("-")
                  + f"-{seq:03d}",
            "type": q["type"],
            "surface": {"label": q["label"],
                        "targets": (q["target"] if isinstance(q["target"], list)
                                    else [q["target"]])},
            "risk": q.get("risk", "safe"),
            "created": now_iso(),
        }
    return None
