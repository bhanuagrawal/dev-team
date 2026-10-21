"""Shared runtime for the dev-team pipeline.

Everything that is not role logic lives here: paths, JSON IO, subprocess
runner, run-log convention. No LLM calls happen in this module by design —
the orchestrator layer is deterministic.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TEAM_ROOT = Path(__file__).resolve().parent.parent

# Projects may live beside the team (Documents/AI), in the Android Studio
# folder, or in the KMP projects folder. Order matters; DEVTEAM_PROJECTS_HOME
# overrides everything.
PROJECTS_BASES = [
    Path(os.environ["DEVTEAM_PROJECTS_HOME"]) if os.environ.get("DEVTEAM_PROJECTS_HOME") else None,
    TEAM_ROOT.parent,
    Path.home() / "AndroidStudioProjects",
    Path.home() / "KMPProjects",
]
PROJECTS_BASES = [b for b in PROJECTS_BASES if b is not None]

RUNTIMES_PATH = TEAM_ROOT / "config" / "runtimes.json"

# one token: no whitespace (so a sentence never matches) and path-ish
# characters only. Glob-ish targets ("app/src/main/res/values*/strings.xml")
# count; a bare file name does not.
_PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_.*\-]+(?:/[A-Za-z0-9_.*\-]+)+")


def path_tokens(text: Any) -> list[str]:
    """The repo-relative path-ish tokens inside free text (diagnostics only)."""
    return _PATH_TOKEN_RE.findall(str(text or ""))


def target_items(target: Any) -> list[str]:
    """A queue entry's `target` as candidate path items: a list stays a list, a
    string splits on commas/newlines (so 'a.kt, b.kt' is two items)."""
    if isinstance(target, (list, tuple)):
        raw = [str(t) for t in target]
    else:
        raw = re.split(r"[,\n]", str(target or ""))
    return [p.strip() for p in raw if p.strip()]


def target_looks_like_paths(target: Any) -> bool:
    """Is this `target` usable as the developer's file allow-list?

    ONE definition, shared by the planner gate (`research._validate`) and the
    developer prompt (`developer._check_targets_are_paths`): every item must be
    a single whitespace-free, repo-relative path (or glob). A sentence is prose
    even when it names files. Lesson 2026-10-18: a feature entry whose target
    was a whole sentence passed the plan gate and only blew up at
    developer-prompt time, wasting the run."""
    items = target_items(target)
    if not items:
        return False
    return all("/" in t and not any(c.isspace() for c in t) for t in items)



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def resolve_project(name_or_path: str) -> Path:
    """Accept a project name (searched in PROJECTS_BASES) or an absolute path."""
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


# role token -> env var (~/agentconfig/agentconfig.env). Provider badalne pe
# sirf llmctl switch chalta hai; yahan kuch nahi badalta.
ROLE_ENV = {"llm": "LLM_MODEL", "thinker": "THINKER_LLM_MODEL"}


# --------------------------------------------------------------------------- #
# roles: model + the tool-agnostic headless launch contract
#
# The team is agentic-tool agnostic BY DESIGN: the orchestrator is whatever
# agentic tool loads the dev-team skill, and it develops through its own native
# subagents. The model is the ONLY thing the team pins per role. Nothing here
# names a vendor, a binary or a CLI.
#
# For tools that cannot spawn subagents (and for A/B bakeoffs) a headless
# launch is available — but the command is supplied by the ENVIRONMENT, never
# baked in:
#   * roles.<role>.command  — an argv template in config/runtimes.json, or
#   * DEVTEAM_AGENT_CMD     — the same template from the environment.
# Templates may use {model} and {prompt} placeholders.
# --------------------------------------------------------------------------- #

def role_cfg(role: str) -> dict:
    """Config for one role (developer | reviewer | orchestrator |
    product_synthesis). Missing roles come back as {}."""
    return (runtimes().get("roles") or {}).get(role) or {}


def resolve_model(cfg: dict) -> str:
    """Role token ('llm' | 'thinker') -> ~/agentconfig env se qualified model id.
    Warna jo diya hai wahi literal id maano."""
    token = (cfg or {}).get("model", "") or ""
    if token in ROLE_ENV:
        return os.environ.get(ROLE_ENV[token], "")
    return token


def agent_argv(cfg: dict, prompt: str) -> list[str] | None:
    """Headless launch argv for a role, or None when the environment supplies
    no agent command (the caller must then use its tool's native subagent
    mechanism — the primary path)."""
    template = (cfg or {}).get("command")
    if isinstance(template, str):
        template = shlex.split(template)
    if not template:
        raw = (os.environ.get("DEVTEAM_AGENT_CMD") or "").strip()
        if not raw:
            return None
        template = shlex.split(raw)
    model = resolve_model(cfg)
    return [prompt if a == "{prompt}" else a.replace("{model}", model)
            for a in template]


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
