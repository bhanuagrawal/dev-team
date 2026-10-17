"""Developer role executor — HEADLESS FALLBACK + BAKEOFF UTILITY.

PRIMARY PATH (per design): the orchestrator IS an agentic tool; per the
dev-team skill it spawns the developer as its own NATIVE SUBAGENT (Claude
Task-style, opencode agents, etc.) with the prompt from prompts/developer.md
and the model from config/runtimes.json roles.developer.model.

This module exists only for:
  a) tools that cannot spawn subagents (the skill's documented fallback:
     `spawn = subprocess of the configured runtime`), and
  b) A/B model bakeoffs on the same task spec.

Gates never change with the spawn mechanism: acceptance = diff in scope +
verify gates green, run by team code.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import verify
from .core import RunLog, git, now_iso, profile_of, run, runtimes, state_of

RUN_BASE = ".pipeline/worktrees"


def _candidate_invocations(role_cfg: dict, prompt: str) -> list[tuple[str, list[str]]]:
    out = []
    for v in [role_cfg] + role_cfg.get("fallback", []):
        rt = v.get("runtime")
        if not rt or rt not in role_cfg.get("invocation", {}):
            continue
        model = v.get("model", "")
        argv = []
        for a in role_cfg["invocation"][rt]:
            argv.append(prompt if a == "{prompt}" else a.replace("{model}", model))
        out.append((rt, argv))
    return out


def _spawn(prompt: str, worktree: Path, log: RunLog, role_cfg: dict,
           timeout: int = 3600) -> tuple[bool, str]:
    env = {"JAVA_HOME": verify.GRADLE_ENV["JAVA_HOME"], "TERM": "dumb"}
    for rt, argv in _candidate_invocations(role_cfg, prompt):
        log(f"~ spawning developer runtime: {rt} (≤{timeout}s)")
        try:
            r = run(argv, worktree, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            log(f"  developer runtime {rt} TIMEOUT")
            return False, f"runtime {rt} timed out after {timeout}s"
        if r.returncode == 0:
            log("  developer finished (rc=0)")
            return True, ""
        tail = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 127 or "not found" in tail.lower()[:200]:
            log(f"  runtime {rt} not launched — trying fallback")
            continue
        return False, tail[-4000:] or f"runtime {rt} exited rc={r.returncode}"
    return False, "no developer runtime could be launched"


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
    role_cfg = runtimes()["roles"]["developer"]
    timeout = role_cfg.get("timeout_s", 3600)

    attempts: list[dict] = []
    files: list[str] = []
    passed = False
    err = ""
    for attempt in range(1, max_attempts + 1):
        log(f"=== attempt {attempt}/{max_attempts} ===")
        ok_spawn, _ = _spawn(prompt, worktree, log, role_cfg, timeout)
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
    rc = git(["commit", "-m", f"feat(ui): add motion to {task['surface']['label'].lower()}"],
             cwd=worktree)
    if rc.returncode != 0:
        return {"status": "failed", "worktree": str(worktree), "attempts": attempts,
                "error": "commit failed: " + (rc.stdout + rc.stderr)[-500:]}
    log(f"committed on dt/{task_id}")
    return {"status": "done", "worktree": str(worktree), "branch": f"dt/{task_id}",
            "files": files, "attempts": attempts, "error": ""}


def build_prompt(project: Path, profile: dict, task: dict, task_paths: list[str],
                 tpl_override: str | None = None) -> str:
    """Deterministic prompt builder — used for BOTH the native-subagent path
    (the orchestrator calls it via `devteam developer-prompt`) and the
    watchdog/subprocess fallback. The orchestrator must not re-derive it."""
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
            + ("\n  (decided_by: " + "; ".join(decisions) + ")" if decisions else ""))
    return tpl.format(
        task_json=json.dumps(task_spec, indent=2),
        reference_screen=profile["reference_screen"],
        reference_picker=profile["reference_picker"],
        components=profile["components_dir"],
        theme=profile["theme_dir"],
        target_area="\n  - ".join(task_paths),
        allowed_paths="\n  - ".join(profile["allowed_paths"]),
        frozen_paths="\n  - ".join(profile["frozen_paths"]),
        compile_cmd=v["compile"],
        test_cmd=v.get("test", "(skip; profile defines no test gate)"),
        conventions="\n  - ".join(profile.get("conventions", [])),
        locales=n_locales,
        research_context=research_ctx or "see research.json constraints",
        project_name=profile["name"],
    )
