"""Shared runtime for the dev-team pipeline.

Everything that is not role logic lives here: paths, JSON IO, subprocess
runner, run-log convention. No LLM calls happen in this module by design —
the orchestrator layer is deterministic.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TEAM_ROOT = Path(__file__).resolve().parent.parent

# Projects may live beside the team (Documents/AI) or in the Android Studio
# folder. Order matters; DEVTEAM_PROJECTS_HOME overrides everything.
PROJECTS_BASES = [
    Path(os.environ["DEVTEAM_PROJECTS_HOME"]) if os.environ.get("DEVTEAM_PROJECTS_HOME") else None,
    TEAM_ROOT.parent,
    Path.home() / "AndroidStudioProjects",
]
PROJECTS_BASES = [b for b in PROJECTS_BASES if b is not None]

RUNTIMES_PATH = TEAM_ROOT / "config" / "runtimes.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def resolve_project(name_or_path: str) -> Path:
    """Accept 'MindfulLauncher' (searched in PROJECTS_BASES) or an absolute path."""
    p = Path(name_or_path).expanduser()
    if p.is_absolute() and (p / ".git").exists():
        return p
    for base in PROJECTS_BASES:
        cand = base / name_or_path
        if (cand / ".git").exists():
            return cand
    raise SystemExit(
        f"not found: {name_or_path} — tried {', '.join(str(b) for b in PROJECTS_BASES)}; "
        "pass an absolute path or run from the project's folder")


def pipeline_dir(project: Path) -> Path:
    return project / ".pipeline"


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def profile_of(path: Path) -> dict:
    """Project- or worktree-aware: walks up from `path` until it finds a
    .pipeline/profile.json — worktrees '.pipeline/worktrees/<id>' resolve to
    their parent project automatically, so release steps never need a
    hand-copied profile."""
    p = Path(path).resolve() if path.exists() else path
    if not path.exists():
        p = Path(path)
    start = p if p.is_dir() else p.parent
    for cur in [start, *start.parents]:
        cand = cur / ".pipeline" / "profile.json"
        if cand.exists():
            return json.loads(cand.read_text())
    raise SystemExit(
        f"no .pipeline/profile.json above {path} — run: python3 cli.py onboard <project>")


def state_of(project: Path) -> dict:
    return load_json(pipeline_dir(project) / "state.json") or {}


def save_state(project: Path, state: dict) -> None:
    save_json(pipeline_dir(project) / "state.json", state)


def runtimes() -> dict:
    return load_json(RUNTIMES_PATH, {})


def git(args: list[str], cwd: Path, timeout: int = 300, env: dict | None = None,
        capture: bool = True) -> subprocess.CompletedProcess:
    return run(["git"] + args, cwd, timeout=timeout, env=env, capture=capture)


def run(args: list[str], cwd: Path | None, timeout: int = 300, env: dict | None = None,
        capture: bool = True) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        args, cwd=str(cwd) if cwd else None, env=e,
        capture_output=capture, text=True, timeout=timeout,
    )


class RunLog:
    """Timestamped run log: streams to stdout AND appends to .pipeline/runs/<id>.log

    Same convention as the job pipeline so any agent can tail a run long-distance."""

    def __init__(self, project: Path, run_id: str):
        self.run_id = run_id
        self.path = pipeline_dir(project) / "runs" / f"{run_id}.log"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with self.path.open("a") as f:
            f.write(line + "\n")
