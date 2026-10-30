"""Developer role executor — HEADLESS FALLBACK + BAKEOFF UTILITY.

PRIMARY PATH (per design): the orchestrator IS an agentic tool; per the
dev-team skill it spawns the developer as its own NATIVE SUBAGENT (whatever
subagent mechanism that tool provides) with the prompt from prompts/developer.md
and the model from config/runtimes.json roles.developer.model.

This module exists only for:
  a) tools that cannot spawn subagents (the skill's documented fallback:
     a headless spawn from the environment-provided agent command), and
  b) A/B model bakeoffs on the same task spec.

No vendor, binary or CLI is named here — the headless command is supplied by
the environment (roles.developer.command or DEVTEAM_AGENT_CMD).

Gates never change with the spawn mechanism: acceptance = diff in scope +
verify gates green, run by team code.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import goals, verify
from .core import (
    RunLog, agent_argv, git, now_iso, profile_of, role_cfg as _role_cfg,
    run, state_of, target_looks_like_paths,
)

RUN_BASE = ".pipeline/worktrees"

# A profile `conventions` entry is a path the developer is told to read before
# writing code ("MANDATORY READS"). Paths go stale (a team/skill dir moves), and
# a dead path either wastes the developer's context hunting for it or makes it
# skip the style law entirely. We can't fix the path from team code (it is
# project data), but we can say so in the prompt and warn the orchestrator.
_MISSING_MARK = "  (SKIP: not found on disk — do not search for it)"


def _annotate_path(p: str) -> str:
    """Convention path + an explicit skip note when it no longer exists."""
    if not p:
        return p
    try:
        return p if Path(p).exists() else p + _MISSING_MARK
    except OSError:
        return p + _MISSING_MARK


def _spawn(prompt: str, worktree: Path, log: RunLog, cfg: dict,
           timeout: int = 3600) -> tuple[bool, str]:
    argv = agent_argv(cfg, prompt)
    if not argv:
        log("no agent command configured — the host tool must spawn the developer "
            "natively (or set roles.developer.command / DEVTEAM_AGENT_CMD)")
        return False, ("no agent command configured for a headless spawn — "
                       "use the native subagent path")
    log(f"~ spawning developer headless ({argv[0]}, ≤{timeout}s)")
    try:
        r = run(argv, worktree, timeout=timeout,
                env={"JAVA_HOME": verify.GRADLE_ENV["JAVA_HOME"], "TERM": "dumb",
                     **verify.android_sdk_env(worktree)})
    except subprocess.TimeoutExpired:
        log("  developer TIMEOUT")
        return False, f"developer timed out after {timeout}s"
    if r.returncode == 0:
        log("  developer finished (rc=0)")
        return True, ""
    tail = (r.stdout or "") + (r.stderr or "")
    return False, tail[-4000:] or f"agent command exited rc={r.returncode}"


def prepare_worktree(project: Path, task_id: str, log: RunLog) -> tuple[Path, str] | None:
    """Worktree branch anchored at origin/main. The user's local checkout and
    its uncommitted work are never touched."""
    git(["fetch", "origin"], cwd=project, timeout=120)
    r = git(["rev-parse", "origin/main"], cwd=project)
    if r.returncode != 0:
        log("FATAL: cannot resolve origin/main")
        return None
    base = r.stdout.strip()
    wt = project / RUN_BASE / task_id
    if wt.exists():
        git(["worktree", "remove", "--force", str(wt)], cwd=project)
    for ref in (f"dt/{task_id}", "dt-base"):
        git(["branch", "-D", ref], cwd=project, timeout=60)
    r = git(["worktree", "add", "-b", f"dt/{task_id}", str(wt), base], cwd=project)
    if r.returncode != 0:
        log("FATAL: worktree add failed: " + (r.stdout + r.stderr)[-800:])
        return None
    git(["branch", "-f", "dt-base", base], cwd=project, timeout=60)
    return wt, base


def execute(project: Path, task: dict, log: RunLog, max_attempts: int = 2) -> dict:
    """Headless fallback execution. Same gates as the native-subagent path."""
    profile = profile_of(project)
    task_id = task["id"]
    prepared = prepare_worktree(project, task_id, log)
    if not prepared:
        return {"status": "error", "worktree": None, "error": "worktree prep failed"}
    worktree, base = prepared
    log(f"worktree: {worktree.relative_to(project)} (base {base[:8]})")

    task_paths = task["surface"].get("targets") or []
    prompt = build_prompt(project, profile, task, task_paths)
    cfg = _role_cfg("developer")
    base_timeout = int(cfg.get("timeout_s", 3600))
    effort = int(task.get("effort_min") or task.get("surface", {}).get("effort_min") or 0)
    timeout = max(base_timeout, effort * 20) if effort > 180 else base_timeout

    attempts: list[dict] = []
    files: list[str] = []
    passed = False
    err = ""
    for attempt in range(1, max_attempts + 1):
        log(f"=== attempt {attempt}/{max_attempts} ===")
        ok_spawn, spawn_err = _spawn(prompt, worktree, log, cfg, timeout)
        if not ok_spawn:
            log(f"FATAL: headless spawn unavailable: {spawn_err}")
            return {"status": "error", "worktree": str(worktree),
                    "error": spawn_err, "files": [], "attempts": attempts}
        files = verify.changed_files(worktree, base)
        if (worktree / "PIPELINE_BLOCKER.md").exists():
            msg = (worktree / "PIPELINE_BLOCKER.md").read_text()[:2000]
            log("DEVELOPER REPORTED BLOCKER — needs a human")
            return {"status": "blocked", "worktree": str(worktree), "error": msg,
                    "files": files, "attempts": attempts}
        ok, failures = verify.verify_all(project, worktree, base, log)
        if ok:
            passed = True
            break
        err = "\n\n".join(failures)
        attempts.append({"attempt": attempt, "files": files, "error": err[:4000]})
        log(f"attempt {attempt} FAILED: {err[:300].replace(chr(10), ' | ')}")
        if attempt < max_attempts:
            prompt += ("\n\nPREVIOUS ATTEMPT FAILED WITH OUTPUT BELOW — fix its "
                       "problems; do not repeat them:\n" + err[:6000])
    if not passed:
        log(f"task {task_id} FAILED after {max_attempts} attempts (worktree kept)")
        return {"status": "failed", "worktree": str(worktree), "files": files,
                "attempts": attempts, "error": err}

    git(["add", "-A"], cwd=worktree)
    # Type-aware subject — headless work is not always "motion" (see shipper).
    prefix = {"motion": "feat", "feature": "feat", "bugfix": "fix"}.get(
        task.get("type", "motion"), "chore")
    rc = git(["commit", "-m", f"{prefix}(ui): {task['surface']['label'].lower()}"],
             cwd=worktree)
    if rc.returncode != 0:
        return {"status": "failed", "worktree": str(worktree), "attempts": attempts,
                "error": "commit failed: " + (rc.stdout + rc.stderr)[-500:]}
    log(f"committed on dt/{task_id}")
    return {"status": "done", "worktree": str(worktree), "branch": f"dt/{task_id}",
            "files": files, "attempts": attempts, "error": ""}


def _check_targets_are_paths(task_paths: list[str]) -> None:
    """Fail fast when surface.targets holds prose instead of repo-relative paths.

    The first target is rendered verbatim as the developer's allow-list
    ("Edit ONLY files under: <targets>"), so a descriptive sentence there does not
    just mislead — it silently REPLACES the file allow-list and can hard-block the
    real files. Narrative belongs in the queue entry's `reason` (injected as
    "RESEARCH CONTEXT TO SOLVE") or in a plan doc under docs/progress/.
    """
    prose = [str(t) for t in (task_paths or [])
             if not target_looks_like_paths(str(t))]
    if prose:
        raise ValueError(
            "surface.targets must be repo-relative PATHS (files, dirs or globs), not "
            f"prose — got {prose[0]!r}. This is rendered verbatim as the developer's "
            "'Edit ONLY files under:' allow-list. Move the description to the queue "
            "entry's `reason` (shown as RESEARCH CONTEXT) or to a plan doc, and list "
            "real paths in `targets` (a TOP-LEVEL file with no '/' — e.g. "
            "settings.gradle.kts — must be written './settings.gradle.kts').")


def build_prompt(project: Path, profile: dict, task: dict, task_paths: list[str],
                 tpl_override: str | None = None) -> str:
    """Deterministic prompt builder — used for BOTH the native-subagent path
    (the orchestrator calls it via `devteam developer-prompt`) and the
    watchdog/subprocess fallback. The orchestrator must not re-derive it."""
    _check_targets_are_paths(task_paths)
    ttype = tpl_override or task.get("type", "motion")
    tpl_name = ("developer.md" if ttype == "motion" else "developer-generic.md")
    tpl = (Path(__file__).resolve().parent.parent / "prompts" / tpl_name).read_text()
    task_spec = {**task}
    task_spec["surface"] = {"label": task["surface"]["label"],
                            "targets": list(task_paths),
                            "risk": task.get("risk", "safe")}
    task_spec["rules_addendum"] = (
        "RISK NOTE: this surface sits next to gesture/drag/grid layout code. Add only "
        "entrance/press/state feedback; never alter gesture, drag, or grid layout logic."
    ) if task.get("risk") == "supervised" else ""
    v = profile["verify"]
    research_ctx = ""
    rj = project / ".pipeline" / "research.json"
    if rj.exists():
        try:
            r = json.loads(rj.read_text())
            g = r["signals"].get("ga4", {})
            research_ctx = ("GA4: new=" + str(g.get("new_users_7d")) + ", active="
                            + str(g.get("active_users_7d")) + ", removes30d="
                            + str(g.get("app_remove_events_30d")))
        except Exception:
            research_ctx = ""
    st = state_of(project)
    for q in st.get("queue", []):
        if q.get("label") == task["surface"].get("label") and q.get("reason"):
            research_ctx = (research_ctx + " · queue reason: " + q["reason"])[:900]
            break
    # WHICH GOAL THIS RUN SERVES. The developer never sees the tree itself, so
    # code states the branch in plain words: without it the implementer optimizes
    # a diff it cannot connect to anything (the reason polish accumulated).
    # (No new template placeholder: projects override prompts/*.md, and a fresh
    # {placeholder} would KeyError inside an owner's own template.)
    mid = str(task.get("advances") or "").strip()
    if not mid:
        for q in st.get("queue", []):
            if q.get("label") == task["surface"].get("label"):
                mid = str(q.get("advances") or "")
                break
    node = goals.find_node(profile, mid) if mid else None
    if node:
        goal_txt = f"GOAL THIS SERVES: {node['title']}"
        if node["raw"].get("outcome"):
            goal_txt += f" — done here means: {node['raw']['outcome']}"
        vision = (goals.plan(profile) or {}).get("vision")
        if vision:
            goal_txt += f'\nLONG-TERM (owner): {vision}'
        goal_txt += ("\nDo not widen scope to 'help' this goal: a smaller change "
                     "that serves it is better than a bigger one that does not.")
        research_ctx = goal_txt + ("\n" + research_ctx if research_ctx else "")
    n_locales = profile.get("locales") or \
        "the skill set of locale strings.xml files shipped by this project"
    # Owner-recorded scope widening (profile['constraint_lifts']). The ENGINE
    # exempts these paths from the frozen gate (verify.scope_check), so the
    # PROMPT must say so too — otherwise the developer is told that a file the
    # task must edit is "hard-blocked and will be REJECTED", and a compliant
    # agent stops. Same source of truth as the gate: the profile.
    lifted, decisions = [], []
    for lift in profile.get("constraint_lifts", []) or []:
        lifted += [str(p) for p in lift.get("paths", [])]
        if lift.get("decided_by"):
            decisions.append(str(lift["decided_by"]))
    frozen_txt = "\n  - ".join(profile["frozen_paths"])
    if lifted:
        frozen_txt += (
            "\n  OWNER-APPROVED LIFT for THIS task — the paths below are NOT blocked "
            "here; move/edit them as the task requires (verify already exempts "
            "them):\n  - " + "\n  - ".join(lifted)
            + ("\n  (decided_by: " + "; ".join(decisions) + ")" if decisions else "")
            + "\n  The lift is PERMISSION, not instruction: touch a lifted path ONLY "
              "if this task genuinely needs it. A lifted file in the diff that this "
              "task did not require (a pin/wrapper/dependency bump for an unrelated "
              "reason) fails the run."
        )
    conventions = [_annotate_path(str(p)) for p in profile.get("conventions", [])]
    missing = [c for c in conventions if _MISSING_MARK in c]
    if missing:
        print("WARNING: profile conventions point at files that do not exist on "
              "disk — the developer prompt marks them '(SKIP: not found)'. Fix the "
              "profile:\n  - " + "\n  - ".join(missing), file=sys.stderr)
    return tpl.format(
        task_json=json.dumps(task_spec, indent=2),
        reference_screen=profile["reference_screen"],
        reference_picker=profile["reference_picker"],
        components=profile["components_dir"],
        theme=profile["theme_dir"],
        target_area="\n  - ".join(task_paths),
        allowed_paths="\n  - ".join(profile["allowed_paths"]),
        frozen_paths=frozen_txt,
        compile_cmd=v["compile"],
        test_cmd=v.get("test", "(skip; profile defines no test gate)"),
        conventions="\n  - ".join(conventions),
        locales=n_locales,
        research_context=research_ctx or "see research.json constraints",
        project_name=profile["name"],
    )
