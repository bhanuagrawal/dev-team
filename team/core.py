"""Shared runtime for the dev-team pipeline.

Everything that is not role logic lives here: paths, JSON IO, subprocess
runner, run-log convention. No LLM calls happen in this module by design —
the orchestrator layer is deterministic.
"""
from __future__ import annotations

import json
import fnmatch
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


def target_matches(path: str, targets: Any) -> bool:
    """Is `path` inside the task's allow-list (`surface.targets`)?

    ONE definition of the allow-list match, so code that reasons about "the
    files this task was allowed to touch" (the release-time untracked-file check)
    agrees with the task spec instead of guessing. An item may be a file,
    a directory, a `dir/**` subtree or a glob; a bare top-level name never
    matches, which is why the spec insists on a path separator."""
    p = str(path or "").strip().lstrip("./")
    if not p:
        return False
    for item in target_items(targets):
        t = item.strip().lstrip("./").rstrip("/")
        if not t:
            continue
        if t.endswith("/**"):
            root = t[:-3]
            if p == root or p.startswith(root + "/"):
                return True
        elif any(ch in t for ch in "*?["):
            if fnmatch.fnmatch(p, t):
                return True
        elif p == t or p.startswith(t + "/"):
            return True
    return False


def target_looks_like_paths(target: Any) -> bool:
    """Is this `target` usable as the task's focus paths?

    ONE definition, shared by the planner gate (`research._validate`) and the
    task spec a coding agent reads: every item must be a single whitespace-free,
    repo-relative path (or glob). A sentence is prose even when it names files.
    Lesson 2026-10-18: a feature entry whose target was a whole sentence passed
    the plan gate and only blew up when the task was picked, wasting the run."""
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
    match a different repository that merely shares the spelling: a bare project
    name landed on an un-onboarded sibling checkout while the project the team
    runs is named differently, and the run died at `adapt` with 'no profile above
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
        if len(hint) == 1:
            return hint[0]
        nearby = ("; onboarded projects that look similar: "
                  + ", ".join(str(h) for h in hint)) if hint else ""
        raise SystemExit(
            f"found {bare} — it is a git repo but not an ONBOARDED dev-team project "
            f"(no .pipeline/profile.json){nearby}. Pass the onboarded project's path "
            "or run from its folder")
    hint = _onboarded_hint(Path(name_or_path).name)
    if len(hint) == 1:
        return hint[0]
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
    """Write JSON atomically.

    `state.json` is read by every command and written by several, and two agents (or
    an agent and its own background step) can overlap: a plain `write_text` truncates
    the file before the new bytes land, so a concurrent reader sees an EMPTY file and
    dies on `json.loads('')`. Seen 2026-11-04 — `devteam rc` and `devteam asks` run
    side by side, and the review crashed on a state.json that was valid a second
    before and after. Writing to a sibling temp file and renaming it into place makes
    readers see either the old bytes or the new ones, never a hole.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def profile_root(path: Path) -> Path:
    """The directory that owns the `.pipeline/profile.json` governing `path`.

    `profile_of` reads a worktree's profile by walking up to its parent project;
    a writer must resolve the SAME directory, or `devteam ci <worktree> --write`
    drops a bogus `<worktree>/.pipeline/profile.json` and leaves the real profile
    untouched (seen 2026-10-31).
    """
    p = Path(path).resolve() if path.exists() else Path(path)
    start = p if p.is_dir() else p.parent
    for cur in [start, *start.parents]:
        if (cur / ".pipeline" / "profile.json").exists():
            return cur
    return start


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
    """Write the ledger, then refresh the page that describes it.

    The report page is a VIEW of the ledger, so the refresh belongs to the WRITE,
    not to the two commands (`record`, `rc`) that happened to remember it. It used
    to be attached there only, which left `doctor`, `plan`, `refill`, `promote` and
    `research` writing state that the page never saw, and left the rebuild itself
    wrapped in `except Exception: pass` — a stale page and a silent one.

    Never fatal: a broken view must not fail the thing it describes. Never silent:
    a view nobody rebuilt is an answer the owner reads as current when it is not.
    """
    save_json(pipeline_dir(project) / "state.json", state)
    _refresh_report(project)


def _refresh_report(project: Path) -> None:
    """Rebuild the report page; report a failure where it happens, don't swallow it."""
    try:
        from . import report  # local import: report reads the ledger through this module
        report.build()
    except Exception as exc:
        print(f"report: rebuild FAILED after writing {project.name} — the page is now "
              f"stale ({exc.__class__.__name__}: {exc}); fix: python3 cli.py site",
              file=sys.stderr)


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
# agentic tool loads the dev-team skill, and the few roles that are not code
# (designer, vision review, storefront review) run through a model. The model
# is the ONLY thing the team pins per role. Nothing here names a vendor, a
# binary or a CLI.
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


# The plan page mirror is the team writing ABOUT the plan; its commit is never
# work a queue must reconcile, and on an active project it is most of history
# (BookSwap 2026-11-13: 4 of the last 5 commits were `chore(plan): …`). A drift
# list that is all bookkeeping is a drift list nobody can act on.
PLAN_MIRROR_PREFIX = "chore(plan):"


def is_plan_mirror(commit: dict[str, Any]) -> bool:
    return str(commit.get("subject") or "").startswith(PLAN_MIRROR_PREFIX)


def recent_commits(project: Path, ref: str = DELIVERY_REF,
                   since: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
    """The last few commits on a ref, newest first — WHAT landed, not just that it did.

    A sha mismatch ("plan base abc ≠ delivery def") is the fact that main moved and
    nothing else: the run still cannot tell whether the queue is out of date or the
    tree is unchanged since the last plan. So every reader of delivery drift quotes
    the commits themselves (`sha`, `date`, `subject`), and `since` narrows the list
    to the commits AFTER a point — the plan's base — which are exactly the commits
    the current plan has not seen.

    The plan's own page mirror is skipped (`is_plan_mirror`): it is the only commit
    kind the team writes about itself, and it would otherwise crowd out the work.
    `limit` counts REAL commits, so the git read over-fetches to leave room.

    Never raises: an unreadable ref is an empty list, and a caller must treat that
    as "could not tell", never as "nothing moved".
    """
    want = max(1, int(limit))
    rng = f"{since}..{ref}" if since else ref
    r = git(["log", rng, f"-n{want * 3}",
             "--pretty=format:%h%x09%ad%x09%s", "--date=short"], cwd=project, timeout=30)
    if r.returncode != 0:
        return []
    out: list[dict[str, Any]] = []
    for line in (r.stdout or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0].strip():
            row = {"sha": parts[0].strip(), "date": parts[1].strip(),
                   "subject": parts[2].strip()}
            if not is_plan_mirror(row):
                out.append(row)
        if len(out) >= want:
            break
    return out


def commit_summary(commits: list[dict[str, Any]], limit: int = 3) -> str:
    """`a1b2c3d subject; e4f5g6h subject (+2 more)` — one line, same everywhere.

    `status`, `replan` and `verify` name the same commits the same way, so an
    owner reading two blocks does not have to match two spellings of one fact.
    """
    shown = [f"{c['sha']} {c['subject']}" for c in (commits or [])[:limit]]
    extra = len(commits or []) - len(shown)
    return "; ".join(shown) + (f" (+{extra} more)" if extra > 0 else "")


def _git_lines(args: list[str], cwd: Path, timeout: int = 60) -> list[str]:
    r = git(args, cwd=cwd, timeout=timeout)
    if r.returncode != 0:
        return []
    return [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]



def _mirror_on_origin(project: Path, branch: str, rel: str) -> bool:
    """Is this file's exact content already on origin/<branch>?

    The plan mirror is rendered into the checkout on every run and committed
    from an origin-anchored worktree, so the checkout legitimately differs from
    its own HEAD while origin serves the identical bytes. Content, not topology.
    """
    there = git(["rev-parse", "--verify", "--quiet", f"origin/{branch}:{rel}"],
                cwd=project, timeout=30)
    if there.returncode != 0:
        return False
    local = git(["hash-object", "--", rel], cwd=project, timeout=30)
    if local.returncode != 0:
        return False
    return (local.stdout or "").strip() == (there.stdout or "").strip()


def checkout_dirty(project: Path) -> list[str]:
    """Files in the checkout carrying work that exists NOWHERE else.

    AGENTS.md says nothing may exist only on this disk. This is the checkable
    form of it, used by `status` and `doctor`: every changed or untracked file
    whose exact content is not already on origin/<main>. A file whose bytes
    origin already serves is a COPY (the checkout is deliberately never rebased),
    not uncommitted work; `.pipeline/` is machine-local by contract.

    The team never commits the owner's WIP on their behalf — it reports it, and
    the owner (or the agent that wrote it) commits or parks it.
    """
    prof = load_json(project / ".pipeline" / "profile.json", {}) or {}
    branch = str(prof.get("main_branch") or "main")
    changed = _git_lines(["diff", "--name-only", "HEAD"], project)
    changed += _git_lines(["ls-files", "--others", "--exclude-standard"], project)
    out: list[str] = []
    for rel in dict.fromkeys(changed):
        if rel == ".pipeline" or rel.startswith(".pipeline/"):
            continue
        if _mirror_on_origin(project, branch, rel):
            continue
        out.append(rel)
    return out


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
