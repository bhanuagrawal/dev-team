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


def _onboarded_hint(name: str) -> list[Path]:
    """Onboarded projects (they carry .pipeline/profile.json) whose folder name
    looks like the one asked for. Used only to explain a miss, never to pick."""
    needle = name.lower()
    found: list[Path] = []
    for base in PROJECTS_BASES:
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for cand in entries:
            try:
                if not (cand / ".git").exists():
                    continue
                if not (cand / ".pipeline" / "profile.json").exists():
                    continue
            except OSError:
                continue
            if needle in cand.name.lower():
                found.append(cand)
    return found


def resolve_project(name_or_path: str) -> Path:
    """Accept a project name (searched in PROJECTS_BASES) or an absolute path.

    A name search prefers a candidate that is actually ONBOARDED (.pipeline/
    profile.json). Filesystems on macOS are case-insensitive, so a bare name can
    match a different repository that merely shares the spelling: 'pdfkit'
    landed on an un-onboarded sibling checkout while the project the team runs
    is named differently, and the run died at `adapt` with 'no profile above
    <wrong dir>' — a message that reads like the project was never onboarded.
    So: keep searching for an onboarded match, and when only a non-onboarded one
    exists, say exactly that and point at the onboarded projects nearby
    (lesson 2026-10-30).
    """
    p = Path(name_or_path).expanduser()
    if p.is_absolute() and (p / ".git").exists():
        return p
    bare: Path | None = None
    for base in PROJECTS_BASES:
        cand = base / name_or_path
        if (cand / ".git").exists():
            if (cand / ".pipeline" / "profile.json").exists():
                return cand
            bare = bare or cand
    if bare is not None:
        hint = _onboarded_hint(Path(name_or_path).name)
        nearby = ("; onboarded projects that look similar: "
                  + ", ".join(str(h) for h in hint)) if hint else ""
        raise SystemExit(
            f"found {bare} — it is a git repo but not an ONBOARDED dev-team project "
            f"(no .pipeline/profile.json){nearby}. Pass the onboarded project's path "
            "or run from its folder")
    hint = _onboarded_hint(Path(name_or_path).name)
    nearby = ("; onboarded projects that look similar: "
              + ", ".join(str(h) for h in hint)) if hint else ""
    raise SystemExit(
        f"not found: {name_or_path} — tried {', '.join(str(b) for b in PROJECTS_BASES)}{nearby}; "
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
# agentconfig: role models AND provider keys are OWNER-managed, and code READS
# them from the environment — it never sources, never hardcodes an id, base_url
# or key, and has no file fallback.
#
# The values live in ~/agentconfig/{agentconfig.env,secrets.env}, which the
# owner keeps current with llmctl. agentconfig publishes them into the macOS
# login session (`llmctl publish` -> `launchctl setenv`, the
# `com.bhanu.agentconfig` LaunchAgent, and a guarded `~/.zshenv`), so NEW
# processes get them from os.environ automatically — including non-login ones
# like a launchd job or `zsh -c`. Read `os.environ` / `$VAR`; do NOT add a
# `source ~/agentconfig/agentconfig.env` line to a script.
#
# Deliberately NO file fallback (owner decision 2026-10-22, "remove fallbacks"):
# a shell that reports a role model as missing is telling the truth about a
# session that was never published (or a process that predates a switch) — and
# an empty {model} must surface as that, not be papered over. A silent fallback
# would MASK a broken publisher: the nightly would keep working while
# `launchctl getenv LLM_MODEL` was empty. Fix the session (`llmctl publish`),
# not the code.
# --------------------------------------------------------------------------- #

def role_env(name: str) -> str:
    """One agentconfig variable, read straight from this process's environment.
    Empty means the session was not published — say so, do not compensate."""
    return os.environ.get(name, "")


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
    """Role token ('llm' | 'thinker') -> qualified model id from the environment
    (agentconfig publishes it session-wide). Warna jo diya hai wahi literal id
    maano."""
    token = (cfg or {}).get("model", "") or ""
    if token in ROLE_ENV:
        return role_env(ROLE_ENV[token])
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


# --------------------------------------------------------------------------- #
# The delivery ref: one answer to "what do users actually get?"
#
# A claim about the app (its brand, its permissions, its store readiness) is a
# claim about the SHIPPED tree, and the owner's checkout is routinely stale or
# diverged — 2026-10-30: a checkout 67 commits behind origin/main made an audit
# read the on-disk theme, report the wrong brand colour with full confidence, and
# generate store screenshots from code Play would never receive. Code read for a
# DECISION goes through these helpers, and every stage says which tree it read.
# --------------------------------------------------------------------------- #
DELIVERY_REF = "origin/main"


def git_ref_state(project: Path, ref: str = DELIVERY_REF) -> dict[str, Any]:
    """How far the working checkout is from the delivery ref (never raises)."""
    state: dict[str, Any] = {"ref": ref, "head": None, "ref_sha": None,
                             "ahead": 0, "behind": 0, "stale": False,
                             "diverged": False, "error": None}
    try:
        head = git(["rev-parse", "HEAD"], cwd=project, timeout=30)
        state["head"] = head.stdout.strip()[:12] if head.returncode == 0 else None
        git(["fetch", "origin"], cwd=project, timeout=120)
        r = git(["rev-list", "--left-right", "--count", f"HEAD...{ref}"], cwd=project, timeout=30)
        if r.returncode != 0:
            state["error"] = (r.stderr or "").strip()[-200:]
            return state
        ahead, behind = (r.stdout.split() + ["0", "0"])[:2]
        state["ahead"], state["behind"] = int(ahead), int(behind)
        sha = git(["rev-parse", ref], cwd=project, timeout=30)
        state["ref_sha"] = sha.stdout.strip()[:12] if sha.returncode == 0 else None
        state["stale"] = state["behind"] > 0
        state["diverged"] = state["ahead"] > 0 and state["behind"] > 0
    except Exception as e:  # offline, no git, not a repo — never a hard failure
        state["error"] = str(e)[:200]
    return state


def delivery_notice(project: Path) -> str:
    """One line stating which tree is authoritative, for stages that read code."""
    s = git_ref_state(project)
    if s.get("error") or not s.get("ref_sha"):
        return (f"DELIVERY: {s['ref']} unavailable ({s.get('error') or 'no ref'}) "
                "— reading the checkout")
    if not s["stale"] and not s["ahead"]:
        return f"DELIVERY: checkout matches {s['ref']} @ {s['ref_sha']}"
    kind = "DIVERGED" if s["diverged"] else ("STALE" if s["stale"] else "unpushed")
    return (f"DELIVERY: {kind} — checkout {s['head'] or '?'} is {s['ahead']} ahead / "
            f"{s['behind']} behind {s['ref']} @ {s['ref_sha']}; reading {s['ref']}")


def read_delivery_file(project: Path, rel: str, fallback_to_checkout: bool = True) -> str | None:
    """Contents of a repo-relative file at the delivery ref (falls back to disk)."""
    try:
        r = git(["show", f"{DELIVERY_REF}:{rel}"], cwd=project, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    except Exception:
        pass
    if fallback_to_checkout:
        f = project / rel
        if f.is_file():
            return f.read_text(errors="ignore")
    return None


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
