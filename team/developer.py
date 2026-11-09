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
import shutil
import subprocess
import sys
from pathlib import Path

from . import goals, verify, worktrees
from .core import (
    RunLog, agent_argv, git, now_iso, profile_of, role_cfg as _role_cfg,
    run, state_of, target_looks_like_paths,
)

RUN_BASE = ".pipeline/worktrees"

# A developer session may be given more time than roles.developer.timeout_s when
# the entry is sized large (effort_min > 180 → effort*20 s), but never more than
# this. The nightly wrapper kills the whole orchestrator at 5400 s
# (scripts/nightly.sh), so a single session must stay well inside it: a session
# that outlives the run turns one late entry into a lost night.
DEV_SESSION_CEILING_S = 4800

# Machine-local build inputs a fresh worktree does not inherit: a git checkout
# only materializes tracked files, so a worktree has no local.properties (gradle
# then dies with "SDK location not found") and no signing material. They are
# copied from the owner's checkout — but ONLY when the repo gitignores them, so
# the copy can never be staged into a commit by the release step.
MACHINE_LOCAL_INPUTS = ("local.properties", "keystore.properties")


def _copy_machine_local_inputs(project: Path, worktree: Path, log: RunLog) -> None:
    for name in MACHINE_LOCAL_INPUTS:
        src, dst = project / name, worktree / name
        if not src.exists() or dst.exists():
            continue
        ignored = git(["check-ignore", "-q", name], cwd=project)
        if ignored.returncode != 0:
            log(f"worktree: not copying {name} — not gitignored, it would be committable")
            continue
        shutil.copy2(src, dst)
        log(f"worktree: copied gitignored build input {name} from the checkout")
        _copy_referenced_inputs(project, worktree, name, log)


def _copy_referenced_inputs(project: Path, worktree: Path, name: str, log: RunLog) -> None:
    """Copy the machine-local file a properties file NAMES, not just the file.

    `keystore.properties` says `storeFile=upload-keystore.jks`; copying the
    properties without the key leaves `bundleRelease` dying in the worktree with
    "Keystore file '…/upload-keystore.jks' not found for signing config
    'release'" — which reads like a code defect and is really a half-copied
    environment (seen 2026-10-31). Every `*File` value is copied too, under the
    same gitignore guard, so a copy can never become committable.
    """
    try:
        text = (project / name).read_text(errors="replace")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not key.strip().endswith("File"):
            continue
        ref = value.strip()
        if not ref:
            continue
        path = Path(ref)
        if not path.is_absolute():
            path = project / ref
        if not path.is_file():
            continue
        try:
            rel = path.resolve().relative_to(project.resolve())
        except ValueError:
            continue  # outside the project: not ours to copy
        dst = worktree / rel
        if dst.exists():
            continue
        ignored = git(["check-ignore", "-q", str(rel)], cwd=project)
        if ignored.returncode != 0:
            log(f"worktree: not copying {rel} — not gitignored, it would be committable")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        log(f"worktree: copied gitignored build input {rel} (named by {name})")

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
        live = worktrees.live_run_reason(project, wt)
        if live:
            log(f"worktree: REFUSED to replace {wt.name} — {live}")
            log("worktree: another session is working in that tree. Its work is "
                "safe at origin/dt/<task-id> once it pushes; wait for it (or for "
                "the settle window) instead of wiping an uncommitted change.")
            return None
        git(["worktree", "unlock", str(wt)], cwd=project, timeout=60)
        git(["worktree", "remove", "--force", str(wt)], cwd=project)
    for ref in (f"dt/{task_id}", "dt-base"):
        git(["branch", "-D", ref], cwd=project, timeout=60)
    r = git(["worktree", "add", "-b", f"dt/{task_id}", str(wt), base], cwd=project)
    if r.returncode != 0:
        log("FATAL: worktree add failed: " + (r.stdout + r.stderr)[-800:])
        return None
    git(["branch", "-f", "dt-base", base], cwd=project, timeout=60)
    _copy_machine_local_inputs(project, wt, log)
    # A previous attempt on this task id may still be the only copy of its work
    # (a blocked session that never reached release). Preserve it as
    # dt/parked/<task>-<sha> before this run owns the branch name again.
    rescue = worktrees.resolve_attempt(project, task_id, base, log)
    if rescue:
        log(f"worktree: ! {rescue} — NOT pushing this attempt yet")
        return wt, base
    # Durability at creation: after this, the tree is a cache, not the record.
    worktrees.start_attempt(project, wt, task_id, log)
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
    timeout = max(base_timeout, min(effort * 20, DEV_SESSION_CEILING_S)) if effort > 180 else base_timeout

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
            f"prose — got {prose[0]!r}. The paths are rendered verbatim as the task's "
            "focus area, so a sentence there tells the coding agent nothing about "
            "where to look. Move the description to the queue entry's `reason` (shown "
            "as RESEARCH CONTEXT) or to a plan doc, and list real paths in `targets` "
            "(a TOP-LEVEL file with no '/' — e.g. settings.gradle.kts — must be "
            "written './settings.gradle.kts').")


def _test_contract(project: Path) -> str:
    """The test requirement the verify gate enforces, with real test paths.

    Appended to every developer prompt instead of a new `{placeholder}`: projects
    may override prompts/*.md, and a fresh placeholder would KeyError inside an
    owner's own template. The gate itself is `team/code_tests.py`; this only
    tells the developer where the gate expects the files.
    """
    from . import code_tests
    unit, ui = code_tests.suggest_test_dirs(project)
    unit_txt = ", ".join(unit) or "the project's unit-test source set"
    ui_txt = ", ".join(ui) or "the project's UI-test source set"
    return f"""

TESTS ARE PART OF THIS DIFF — `devteam verify` REFUSES the change without them:
  - Every production file you add or change MUST be named by a test file you also
    add or change in this same task. The gate reads the diff, not your summary.
  - A UI file (a @Composable, Activity/Fragment/Dialog/View, or layout XML) needs
    a UI test using createComposeRule/createAndroidComposeRule (Robolectric or
    on-device) or Espresso/UiAutomator, WITH at least one assertion.
  - Every other production file needs a unit test with @Test and at least one
    assertion.
  - A screenshot/render with no assertion is NOT a test and does not count.
  - Test files under {unit_txt} (unit) and {ui_txt} (UI) are always the right
    place for the tests this change owes. Never weaken an existing test to make
    the gate pass.
"""


def _flow_contract(project: Path) -> str:
    """The journeys a change has to keep walking.

    The audit tools can only catch a broken flow AFTER someone runs them; the agent
    that breaks it is the one writing the screen. So the developer is handed the
    journeys up front, as part of its definition of done, and told how to prove
    them on the tree it is editing.
    """
    from . import ux_flow
    flows = ux_flow.declared(project)
    if not flows:
        return ""
    lines = ["", "JOURNEYS THIS CHANGE MUST KEEP WORKING — a screen that compiles but",
             "cannot be used is a failed task, not a follow-up:"]
    for f in flows:
        lines.append(f"  - {f['id']} (surface: {f.get('surface') or '—'}): "
                     f"{str(f.get('goal') or '').strip()[:160]}")
    lines += [
        "  If your change touches a screen one of these walks, the journey must still",
        "  reach its goal. Prove it on THIS tree (not main):",
        "    devteam ux-flow <project> --worktree <this worktree> --flow <id>",
        "  If you ADD a screen or a flow that no journey covers, add its journey to",
        "  docs/ux-flows.json in this same change (id, surface, goal) — an undeclared",
        "  flow is invisible to every later audit.",
        "  No device available? Say so in your closing note AND put",
        '  `"flow_impact": "none"` plus `"flow_impact_reason": "…"` in the task spec.',
        "  `devteam verify` refuses an unexercised journey either way — silently",
        "  breaking a flow is not an option the gates leave open.",
    ]
    return "\n".join(lines)


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
        frozen_paths="",
        compile_cmd=v["compile"],
        test_cmd=v.get("test", "(skip; profile defines no test gate)"),
        conventions="\n  - ".join(conventions),
        locales=n_locales,
        research_context=research_ctx or "see research.json constraints",
        project_name=profile["name"],
    ) + _test_contract(project) + _flow_contract(project) + _design_contract(
        project, profile, task, task_paths)


def design_brief(project: Path, profile: dict, task: dict,
                 focus_paths: list[str]) -> dict:
    """Ask the designer, do not assume.

    The plan records intent; the authority is a consultation with the designer
    against the CURRENT Stitch project + docs/design.md (owner directive
    2026-11-09). A task with no recorded answer is not "ready" and is not "na" —
    it is PENDING, and the developer must ask before writing UI. A recorded
    answer goes STALE when the Stitch project, manifest or design doc changes.
    """
    from . import designer

    st = designer.status(project, task)
    state = st.get("state")
    required = state in ("pending", "stale") or (
        state == "answered" and st.get("decision") == "new_screen")

    available: list[dict] = []
    if (project / "docs" / "design.md").is_file():
        available.append({"kind": "design_doc", "path": "docs/design.md",
                          "use": "the style law — read it before writing UI"})
    stitch = profile.get("design") or {}
    if stitch.get("stitch_project_id"):
        available.append({"kind": "stitch_project",
                          "id": stitch["stitch_project_id"],
                          "use": "the designer reads this project; do not guess its screens"})
    if stitch.get("design_system_id"):
        available.append({"kind": "stitch_design_system", "id": stitch["design_system_id"]})
    manifest = project / ".pipeline" / "stitch_manifest.json"
    if manifest.is_file():
        try:
            screens = json.loads(manifest.read_text()).get("canonical_screens") or []
        except Exception:
            screens = []
        if screens:
            available.append({"kind": "stitch_manifest",
                              "path": ".pipeline/stitch_manifest.json",
                              "screens": [s.get("title") for s in screens[:8]]})

    label = designer.task_label(task)
    command = f"devteam design-consult {project.name} --task {label} --prompt"
    if state == "not_applicable":
        action = "no design needed for this task (logic/data/CI work)"
        why = st.get("reason") or "no UI targets"
    elif state in ("pending", "stale"):
        why = st.get("reason") or "the designer has not answered for this task"
        action = ("ASK THE DESIGNER before writing UI. Run: " + command
                  + "; then record the answer with --record. If the answer is "
                    "'new_screen', generate it in Stitch and adopt it into "
                    "docs/design.md before writing UI. Do not invent a visual language.")
    elif st.get("decision") == "existing_screen":
        why = st.get("reason") or "an existing screen covers this task"
        screen = st.get("screen") or {}
        action = (f"build from the existing screen '{screen.get('title') or 'named by the designer'}'"
                  + (f" (id {screen.get('id')})" if screen.get("id") else "")
                  + "; do not redesign it")
    elif st.get("decision") == "derive":
        why = st.get("reason") or "the design system covers this task"
        action = "follow docs/design.md tokens/components; no new screen"
    elif st.get("decision") == "new_screen":
        why = st.get("reason") or "no existing screen covers this task"
        screen = st.get("screen") or {}
        action = (f"a new screen is required '{screen.get('title') or 'named by the designer'}': "
                  "generate/adopt it into docs/design.md BEFORE writing UI")
    else:
        why = "design decision unclear"
        action = "ask the designer: " + command

    return {"required": required, "status": st, "why": why,
            "available": available, "action": action,
            "consult_command": command}


def _design_contract(project: Path, profile: dict, task: dict,
                     task_paths: list[str]) -> str:
    """The design answer, appended to the legacy prose template as well."""
    d = design_brief(project, profile, task, task_paths)
    lines = ["", "DESIGN — " + ("required" if d["required"] else "not required")
             + f" ({d['why']})"]
    for a in d["available"]:
        detail = a.get("id") or a.get("path") or ""
        lines.append(f"  - {a['kind']}: {detail} — {a.get('use') or ''}")
    lines.append("  " + d["action"])
    for cmd in d.get("generate_with") or []:
        lines.append(f"    {cmd}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# THE HANDOFF — the only thing a coding tool is told
# --------------------------------------------------------------------------- #
def task_context(project: Path, profile: dict, task: dict,
                 task_paths: list[str]) -> dict:
    """Everything the instruction template used to say, as DATA beside the task.

    The owner's objection (2026-11-09): a long prose prompt is a second copy of
    facts the pipeline already owns, and prose rules are exactly what an agent
    forgets. So the code decides WHICH task (`pick`) and writes the facts an agent
    cannot discover cheaply onto disk: the goal, the conventions to read, the
    build/test commands, the locale set, and the test contract `verify` enforces.
    The coding tool reads one file and works in its own context.
    """
    from . import code_tests, ux_flow

    unit, ui = code_tests.suggest_test_dirs(project)
    v = profile.get("verify") or {}
    st = state_of(project)

    # WHICH GOAL THIS RUN SERVES — the plan tree is not in the worktree.
    mid = str(task.get("advances") or "").strip()
    if not mid:
        for q in st.get("queue", []):
            if q.get("label") == (task.get("surface") or {}).get("label"):
                mid = str(q.get("advances") or "")
                break
    goal = {}
    node = goals.find_node(profile, mid) if mid else None
    if node:
        goal = {"id": mid, "title": node["title"]}
        if node["raw"].get("outcome"):
            goal["done_means"] = node["raw"]["outcome"]
        vision = (goals.plan(profile) or {}).get("vision")
        if vision:
            goal["vision"] = vision

    ctx = {
        "project": profile.get("name"),
        "focus_paths": list(task_paths),
        "goal": goal,
        "conventions": [_annotate_path(str(p))
                        for p in (profile.get("conventions") or [])],
        "architecture": (
            "Use the platform skills you have (android-code, android-build, "
            "android-test) and match the nearest existing feature: state holder, "
            "DI, navigation, error handling, design tokens. Read before writing."),
        "locales": profile.get("locales") or None,
        "build": {
            "compile": v.get("compile"),
            "test": v.get("test"),
            "unit_test_dirs": unit,
            "ui_test_dirs": ui,
        },
        "tests_enforced_by": "devteam verify",
        "test_contract": (
            "Every production file you add or change must be named by a test file "
            "you also add or change in this same diff. A UI file (Composable, "
            "Activity/Fragment/Dialog/View, layout XML) needs a UI test using "
            "createComposeRule/createAndroidComposeRule or Espresso/UiAutomator, "
            "with at least one assertion. Everything else needs a unit test with "
            "@Test and an assertion. A screenshot/render is not a test."),
        "design": design_brief(project, profile, task, task_paths),
    }
    reason = str(task.get("reason") or "").strip()
    if reason:
        ctx["why"] = reason
    # THE MINI-PRD — what this task is FOR, in the owner's/planner's words. The
    # acceptance lines are what the tests must assert; verify reads the diff, not
    # this block, so it can only help the tool pick the right tests.
    intent = {k: task[k] for k in ("problem", "user_sees", "done_when",
                                   "not_in_scope") if task.get(k)}
    if intent:
        ctx["intent"] = intent
    flows = ux_flow.declared(project)
    if flows:
        ctx["journeys"] = [{"id": f["id"], "surface": f.get("surface"),
                            "goal": str(f.get("goal") or "")[:160]}
                           for f in flows]
    return ctx


def write_brief(project: Path, spec_path: Path, profile: dict, task: dict,
                task_paths: list[str]) -> Path:
    """Attach the context block to the task spec in place — one file is the brief."""
    _check_targets_are_paths(task_paths)
    brief = {**task, "context": task_context(project, profile, task, task_paths)}
    spec_path.write_text(json.dumps(brief, indent=2) + "\n")
    return spec_path


def handoff(spec_path: Path, worktree: str | None = None) -> str:
    """The whole instruction: where the work is, and which file says what it is.

    Deliberately not a prompt. The tool reads the spec and works in its own
    context; the gates (not prose) decide whether the result ships.
    """
    lines = []
    if worktree:
        lines.append(f"WORKTREE_PATH={worktree}")
    lines.append(f"TASK_SPEC={spec_path}")
    lines.append("Implement TASK_SPEC in the worktree. Do not commit — "
                 "devteam verify decides.")
    return "\n".join(lines)
