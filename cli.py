#!/usr/bin/env python3
"""dev-team CLI — deterministic bookkeeping and gates ONLY.

The orchestrator is ANY agentic tool running with the dev-team skill: it decides
and drives; native subagents develop. This CLI exposes the deterministic actions
that agent needs, and nothing project-shaped is hardcoded here — every project
fact comes from <project>/.pipeline/profile.json. No vendor or binary is baked
in either: headless launches come from roles.<role>.command or DEVTEAM_AGENT_CMD.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from team import alarm, emulator, goals, scribe, scan, scheduler, shipper  # noqa: E402
from team.core import (  # noqa: E402
    RunLog, TEAM_ROOT, agent_argv, load_json, now_iso, pipeline_dir, profile_of,
    resolve_model, resolve_project, role_cfg, run as sys_run, runtimes,
    save_json, save_state, state_of, today,
)

CORE_MOTION_TOKENS = scan.CORE_MOTION_TOKENS


def task_spec_path(project: Path, spec: str) -> Path:
    """Resolve a task-spec argument the way every doc names it.

    The runbook/README name specs project-relative (`.pipeline/tasks/<id>.json`),
    but a shell resolves that against the CWD — which for an orchestrator is the
    TEAM dir, not the project. A correct command then dies with a bare
    FileNotFoundError (observed live on `developer-prompt`). Accept the
    documented shape: CWD first, then the project, then the team dir.
    """
    p = Path(spec)
    if p.exists():
        return p
    for base in (project, TEAM_ROOT):
        cand = base / spec
        if cand.exists():
            return cand
    raise SystemExit(f"task spec not found: {spec} (tried CWD, {project}, {TEAM_ROOT})")


def language_conventions(project: Path) -> list[str]:
    """Convention sources for the developer subagent: the team's universal
    android-code skill, plus any in-repo architecture/conventions doc.
    KMP (composeApp) projects additionally get the kmp-compose-multiplatform
    skill (Google-architecture-grounded) — KMP is not Android-idiom territory."""
    conv = [str(TEAM_ROOT / "skills" / "android-code" / "SKILL.md")]
    if (project / "composeApp").is_dir():
        conv.append(str(Path.home() / ".agents/skills/kmp-compose-multiplatform/SKILL.md"))
    for name in ("ARCHITECTURE.md", "AGENTS.md"):
        p = project / name
        if p.exists() and p.stat().st_size < 60_000:
            conv.append(str(p))
    return conv


# --------------------------------------------------------------------------- #
# onboarding: detect everything generic; write profile draft + starter state
# --------------------------------------------------------------------------- #

def cmd_onboard(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    pdir = pipeline_dir(project)
    pdir.mkdir(parents=True, exist_ok=True)

    # --- layout: standard Android (app/src/main) or KMP (composeApp) -----------
    kmp = False
    if not (project / "app/src/main/java").exists():
        kmp_srcs = [d for d in ("androidMain/kotlin", "commonMain/kotlin")
                    if any((project / "composeApp/src" / d).rglob("*.kt"))]
        if not kmp_srcs:
            raise SystemExit(f"v1 onboarding supports standard Android (app/src/main) "
                             f"or KMP (composeApp) layouts — got none in {project}")
        kmp = True
        module_src = project / "composeApp/src"
        # UI (Compose) lives in androidMain when present; commonMain is fallback
        src_leaf = kmp_srcs[0]
        src_tree = module_src / src_leaf

    # --- environment detection -------------------------------------------------
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=project,
                            capture_output=True, text=True).stdout.strip()
    repo_slug = remote.removeprefix("git@github.com:").removeprefix(
        "https://github.com/").removesuffix(".git")

    src_root = None
    gradle = project / "app" / "build.gradle"
    if not gradle.exists():
        cands = list(project.rglob("build.gradle.kts"))
        cands += [p for p in project.rglob("*.gradle*") if p.name in ("build.gradle", "build.gradle.kts")]
        gradle = next((g for g in cands if re.search(r"versionCode|versionName", g.read_text(errors="ignore"))), None)
    if kmp:
        pkg_paths = [p for p in src_tree.glob("*/*/*") if p.is_dir() and any(p.rglob("*.kt"))]
        pkg = pkg_paths[0].relative_to(src_tree) if pkg_paths else None
        src_root = (f"composeApp/src/{src_leaf}/" + pkg.as_posix()
                    if pkg else f"composeApp/src/{src_leaf}")
    else:
        pkg_paths = [p for p in (project / "app/src/main/java").glob("*/*/*") if p.is_dir()]
        src_root = "app/src/main/java"
        if pkg_paths:
            pkg = pkg_paths[0].relative_to(project / "app/src/main/java")
            src_root = "app/src/main/java/" + pkg.as_posix()

    # --- project source facts --------------------------------------------------
    from team import scan as tscan
    tree = tscan.origin_tree(project)
    kotlin = tree / src_root
    kt_files = list(kotlin.rglob("*.kt"))

    comps_dir = next((p for p in [
        kotlin / "common/ui/components", kotlin / "common/uicomponents",
        kotlin / "common/ui"] if (p / ".").exists() and any(p.glob("*.kt"))), None)

    theme_dir = next((p for p in [kotlin / "common/theme"] if p.exists()), None)

    # motion tokens = the app's own shared component names + universal Compose APIs
    tokens = list(CORE_MOTION_TOKENS)
    comps = []
    if comps_dir:
        for f in sorted(comps_dir.glob("*.kt")):
            for m in re.finditer(r"^\s*fun\s+([A-Z]\w+)", f.read_text(errors="ignore"), re.M):
                name = m.group(1)
                if name in (t for t in tokens):
                    continue
                if re.match(r"(Entrance|Mindful|Settings|Animated|Press|Step|Icon|Badge|Stat)", name) or \
                   "Animated" in name or "Fade" in name:
                    tokens.append(name)
                    comps.append(name)
    tokens = sorted(set(tokens))

    # reference screen = the .kt file with the MOST distinct motion tokens
    ref_screen, ref_picker, best = None, None, -1
    for f in kt_files:
        text = f.read_text(errors="ignore")
        score = sum(1 for t in tokens if t in text)
        if score > best:
            best = score
            ref_screen = f.relative_to(tree).as_posix()
    if best > 0:
        for f in kt_files:
            text = f.read_text(errors="ignore")
            if "PickerSheet" in f.name or "Picker" in f.name:
                n = sum(1 for t in tokens if t in text)
                if n > 0:
                    ref_picker = f.relative_to(tree).as_posix()
                    break

    version_file = gradle.relative_to(project).as_posix()

    # CI workflow + trigger detection: prefer the version-bump shipper workflow,
    # else take the first workflow that exists; a tag-gated `on.push.tags`
    # trigger means delivery requires pushing the version tag, not just main.
    ci_workflow, ci_trigger = "ship-on-version-bump.yml", "bump"
    wf_dir = project / ".github/workflows"
    if wf_dir.is_dir():
        workflows = sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))
        chosen = next((w for w in workflows if "ship-on-version-bump" in w.name),
                      workflows[0] if workflows else None)
        if chosen:
            ci_workflow = chosen.name
            if re.search(r"^\s*tags:\s*$", chosen.read_text(errors="ignore"), re.M) \
                    and not re.search(r"^\s*(branches|paths):\s*$", chosen.read_text(errors="ignore"), re.M):
                ci_trigger = "tag"
    module = version_file.split("/")[0] if kmp else "app"
    if kmp:
        frozen = [f"{module}/src/commonMain/**/common/**",
                  f"{module}/src/commonMain/**/domain/**",
                  f"{module}/src/iosMain/**",
                  f"{module}/src/androidMain/**/data/**",
                  version_file, ".github/workflows/**"]
        allowed = [f"{module}/src/androidMain/**/ui/**", f"{module}/src/androidMain/navigation/**",
                   f"{module}/src/commonMain/**/ui/events/**", f"{module}/src/androidMain/res/values*/*.xml"]
    else:
        frozen = [f"{module}/src/main/java/**/data/**", f"{module}/src/main/java/**/domain/**",
                  f"{module}/src/main/java/**/model/**", f"{module}/src/main/java/**/repository/**",
                  f"{module}/src/main/java/**/config/FeatureFlags.kt",
                  f"{module}/build.gradle"]
        allowed = [f"{module}/src/main/java/**/ui/**", f"{module}/src/main/java/**/theme/**",
                   f"{module}/src/main/res/values*/strings.xml"]

    # --- draft queue from discovery --------------------------------------------
    profile = {
        "name": project.name,
        # Data shape this profile conforms to. `devteam adapt` (step 0 of every
        # workflow run) stamps/upgrades it, so a project tracks the team's own
        # changes without anyone hand-editing profile.json.
        "profile_schema": 2,
        "repo_slug": repo_slug,
        "main_branch": "main",
        "src_root": src_root,
        "version_file": version_file,
        "tag_prefix": "v",
        "whatsnew_file": ("distribution/whatsnew/whatsnew-en-US"
                          if (project / "distribution/whatsnew/whatsnew-en-US").exists()
                          else None),
        "ci_workflow": ci_workflow,
        "ci_trigger": ci_trigger,
        "java_home": "/Applications/Android Studio.app/Contents/jbr/Contents/Home",
        "verify": {"compile": f":{module}:compileDebugKotlin",
                   "test": f":{module}:testDebugUnitTest"},
        "frozen_paths": frozen,
        "allowed_paths": allowed,

        "shared_components_dir": (comps_dir.relative_to(tree).as_posix()
                                  if comps_dir else None),
        "components_dir": (comps_dir.relative_to(tree).as_posix()
                           if comps_dir else None),  # alias: prompts read this key
        "theme_dir": (theme_dir.relative_to(tree).as_posix() if theme_dir else None),
        "motion_tokens": tokens,
        "target_name_re": r"Screen|Sheet|Dialog|Menu|Banner|Overlay|Picker|Coach|Bar",
        "skip_name_re": r"^(Test|Theme|Color|Type|Preview|Demo|Token)",
        "pickers": None,
        "reference_screen": ref_screen,
        "reference_picker": ref_picker,
        "conventions": language_conventions(project),
        "targets": [],           # promoted from discovery below; prune freely
        "discovery_draft": [],
        "notes": "Review discovery_draft; hand-curate 'targets' (add risk overrides, "
                 "exclude dark flags) before first unattended run.",
    }
    profile_reference_fill(profile, project)
    save_json(pdir / "profile.json", profile)          # discovery reads the profile
    draft = scan.discover_surfaces(project)
    # v1: promote discovery directly into the working queue; the human prunes
    # dark features / sets risk overrides in profile['targets'] whenever.
    profile["targets"] = [dict(t) for t in draft]
    profile["discovery_draft"] = [dict(t) for t in draft]
    save_json(pdir / "profile.json", profile)

    state = {"runs": [], "shipped": [], "remaining": [], "last_run": {},
             "created": now_iso(),
             # the current team's full state shape, so a fresh project never
             # starts out "needing adapt"
             "queue": [], "goal_log": [], "tree_changes": [], "alignment_log": []}
    save_json(pdir / "state.json", state)
    (pdir / "tasks").mkdir(parents=True, exist_ok=True)

    # keep local working tree clean: exclude pipeline dir repo-locally
    excl = project / ".git" / "info" / "exclude"
    excl.parent.mkdir(parents=True, exist_ok=True)
    text = excl.read_text() if excl.exists() else ""
    if ".pipeline/" not in text:
        excl.write_text(text + ("\n" if text and not text.endswith("\n") else "") + ".pipeline/\n")

    print(f"onboarded {project.name}:")
    print(f"  profile:     {pdir / 'profile.json'}")
    print(f"  repo slug:   {repo_slug}")
    print(f"  src root:    {src_root}")
    print(f"  refs:        screen={ref_screen}")
    print(f"               picker={ref_picker}")
    print(f"  components:  {len(comps)} motion-carrying shared components")
    print(f"  discovery:   {len(draft)} unanimated candidate surfaces")
    print("  review the draft (exclude dark features, set risk) then fill profile['targets']")


def profile_reference_fill(profile: dict, project: Path) -> None:
    """pickers key cleanup (kept for profile shape stability)."""
    profile.pop("pickers", None)


# outcome contract helpers — the orchestrator's duty is to relay, not remember:
# upgrades/capability-asks flagged by the research agents are printed BY CODE
# at record time; the run banner must quote what was printed.
def print_agent_flags(project: Path) -> None:
    state = state_of(project)
    flags = state.get("upgrades", [])
    if not flags:
        try:
            flags = json.loads((Path(project) / ".pipeline" / "research.json")
                               .read_text()).get("upgrades", [])
        except Exception:
            flags = []
    if not flags:
        return
    print("--- AGENT FLAGS (upgrades surfaced by research — quote these in the run banner) ---")
    for u in flags:
        print(f"  · {u.get('title', '?')} — {u.get('benefit', '?')}")
        print(f"    how: {u.get('how', '?')}")


# --------------------------------------------------------------------------- #
# orchestrator-facing deterministic commands
# --------------------------------------------------------------------------- #

def cmd_worktree_start(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    if not args.task_id:
        raise SystemExit("--task-id required")
    from team.developer import prepare_worktree
    log = RunLog(project, "worktree-" + args.task_id)
    prepared = prepare_worktree(project, args.task_id, log)
    if not prepared:
        raise SystemExit("worktree prep failed (see log)")
    wt, base = prepared
    print(f"WORKTREE_PATH={wt}")
    print(f"WORKTREE_BRANCH=dt/{args.task_id}")
    print(f"WORKTREE_BASE={base}")


def cmd_run_headless(args: argparse.Namespace) -> int:
    """Skill-documented fallback for tools without subagents + bakeoff harness.
    Deterministic spawn from runtimes.json with identical gates."""
    from team import developer
    project = resolve_project(args.project)
    log = RunLog(project, "headless-" + today())
    # Emulator bookkeeping (run start): remember what was ALREADY running so the
    # run end can stop only what this run started, never the owner's emulator.
    emulator.record_baseline(project, log, reset=True)
    task = load_json(task_spec_path(project, args.task_spec))
    if not task:
        raise SystemExit(f"bad task spec: {args.task_spec}")
    if not args.supervised and task.get("risk") == "supervised":
        raise SystemExit("task is risk:supervised — it may only run with --supervised "
                         "(a human present)")
    res = developer.execute(project, task, log)
    payload = {k: v for k, v in res.items() if k != "worktree"} | \
              {"worktree": res.get("worktree")}
    task_out = Path(args.task_spec)
    print(json.dumps(payload, indent=2))
    if res.get("status") == "failed":
        return 1
    return 0 if res.get("status") in ("done", "blocked") else 1


def cmd_run_orchestrator(args: argparse.Namespace) -> int:
    """Boot the orchestrator: an agentic tool of the ENVIRONMENT's choosing,
    running with the dev-team skill. No vendor is baked in — the launch command
    comes from roles.orchestrator.command or DEVTEAM_AGENT_CMD."""
    project = resolve_project(args.project)
    cfg = role_cfg("orchestrator")
    log = RunLog(project, "orchestrator-" + today())
    # Emulator bookkeeping (run start) — see run_headless for why it is a baseline.
    emulator.record_baseline(project, log, reset=True)
    prompt = args.prompt or (
        "You are the dev-team orchestrator. Load and follow the dev-team skill in "
        "full. Run tonight's pipeline: status → pick the next safe unanimated "
        "surface → worktree-start → developer-prompt → spawn the developer as a "
        "native subagent (model from runtimes.json) → verify the worktree → "
        "release on green → record → alarm. Project: "
        f"{project.name} (absolute path: {project}). Report a compact summary of "
        "what shipped.")
    argv = agent_argv(cfg, prompt)
    if not argv:
        raise SystemExit(
            "no orchestrator command — set roles.orchestrator.command in "
            "config/runtimes.json or export DEVTEAM_AGENT_CMD "
            "(e.g. '<your-tool> run --model {model} {prompt}')")
    print(f"orchestrator: {argv[0]} | model {resolve_model(cfg) or 'tool default'} "
          f"| project {project}", flush=True)
    # capture=False: the orchestrator's output streams to the caller's stdio
    # (launchd log) instead of being buffered until exit.
    r = sys_run(argv, None, timeout=args.timeout, capture=False)
    return r.returncode


def cmd_developer_prompt(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    profile = profile_of(project)
    task = load_json(task_spec_path(project, args.task_spec)) \
        or json.loads(task_spec_path(project, args.task_spec).read_text())
    paths = task["surface"].get("targets") or []
    from team.developer import build_prompt
    print(build_prompt(project, profile, task, paths,
                       tpl_override=getattr(args, "template", None)))


def cmd_queue(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    state = state_of(project)
    action = getattr(args, "action", None)
    if action == "add":
        from team import queue as qmod
        if args.spec:
            entry = json.loads(Path(args.spec).read_text())
        else:
            if not args.label:
                raise SystemExit("queue add needs --spec or --label")
            entry = {"type": args.type or "feature", "label": args.label,
                     "target": args.target or "",
                     "reason": args.reason or "added by the orchestrator"}
            if args.risk:
                entry["risk"] = args.risk
            if getattr(args, "advances", None):
                entry["advances"] = args.advances
        if args.supersedes:
            known = {qmod._norm(qmod.label_of(q)) for q in state.get("queue", [])}
            known |= {qmod._norm(q.get("id", "")) for q in state.get("queue", [])}
            unknown = [s for s in args.supersedes if qmod._norm(s) not in known]
            if unknown:
                raise SystemExit(f"--supersedes names not in the queue: {unknown} "
                                 "— judge only tasks that are actually queued")
            entry["supersedes"] = list(entry.get("supersedes") or []) + list(args.supersedes)
        block = qmod.add_task(state, entry, source="orchestrator",
                              position=args.position)
        save_state(project, state)
        print(block)
        print(json.dumps(state.get("queue", []), indent=2))
        return
    if action == "review":
        from team import queue as qmod
        print(qmod.review(state))
        return
    queue = state.get("queue", [])
    if args.done:
        before = len(queue)
        queue = [q for q in queue if q.get("label") != args.done]
        if len(queue) != before:
            state["queue"] = queue
            save_state(project, state)
        print(f"queue: {len(queue)} left (popped '{args.done}')" if len(queue) != before
              else f"'{args.done}' not found in queue — nothing popped")
    else:
        print(json.dumps(queue, indent=2))


def cmd_verify(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    profile = profile_of(project)
    from team import verify as gates
    log = RunLog(project, "verify-" + today())
    tree = Path(args.worktree).resolve() if args.worktree else project
    if args.worktree:
        base = subprocess.run(["git", "rev-parse", "dt-base"], cwd=tree,
                              capture_output=True, text=True).stdout.strip()
    else:
        base = "HEAD"
    ok, failures = gates.verify_all(project, tree, base, log,
                                    gradle=not args.skip_gradle)
    # Run-end emulator teardown: warn-only by owner decision (never flips the
    # gate). A skill asks an agent to stop its emulator; this is what holds when
    # it forgets — orphans are stopped and named in the run log. leak_check
    # emits through `log`, so there is no second print here.
    emulator.leak_check(project, log)
    print("VERIFY: PASS" if ok else "VERIFY: FAIL")
    for f in failures:
        print(f)
    return 0 if ok else 1


def cmd_emulators(args: argparse.Namespace) -> int:
    """Emulator lifecycle bookkeeping. `--record` at run start (snapshot what is
    already running), plain invocation at run end (stop anything that appeared
    since, warn-only). Never touches a pre-existing emulator."""
    project = resolve_project(args.project)
    log = RunLog(project, "emulators-" + today())
    if args.record:
        emulator.record_baseline(project, log, reset=args.reset)
        print("emulator baseline recorded")
        return 0

    clean, msg = emulator.leak_check(project)
    print(msg)
    return 0  # warn-only: a leak is hygiene debt, not a failed run


def cmd_release(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    log = RunLog(project, "release-" + today())
    worktree = Path(args.worktree).resolve()
    latest = shipper.remote_latest_version(project)
    if latest is None:
        raise SystemExit("no v* tags on origin; cannot compute next version")
    new = (latest[0], latest[1], latest[2] + 1)
    task = json.loads(task_spec_path(project, args.task_spec).read_text())
    name = shipper.bump_version_file(worktree, new, log)
    shipper.write_whatsnew(worktree, task)
    res = shipper.push_and_deliver(project, worktree, task, name, log)
    print(json.dumps(res, indent=2))
    return 0 if res.get("delivered") else 1


def cmd_record(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    state = state_of(project)
    task = json.loads(task_spec_path(project, args.task_spec).read_text()) \
        if args.task_spec else {}
    entry = {"date": today(), "task_id": args.task or task.get("id", "?"),
             "result": args.result, "note": args.note or "",
             "finished": now_iso()}
    state.setdefault("runs", []).append(entry)
    if args.result == "shipped":
        label = task.get("surface", {}).get("label", args.task or "?")
        targets = task.get("surface", {}).get("targets", [])
        # Attribute the delivery to the milestone the queue entry named, so the
        # goal tree can show WHICH goal each shipped version advanced. The spec
        # carries the branch (written at pick time); the label lookup is only the
        # fallback for specs picked before the tag existed.
        adv = str(task.get("advances") or "")
        if not adv:
            for q in state.get("queue", []):
                if (q.get("label") or "") == label:
                    adv = str(q.get("advances") or "")
                    break
        already = {s["target"] for s in state.get("shipped", [])}
        for t in targets:
            if t in already:
                continue  # concurrent runs may both ship; keep one ledger row per target
            state.setdefault("shipped", []).append({
                "target": t, "label": label, "version": args.version or "?",
                "commit": args.commit or "", "date": today(),
                "task_id": args.task or task.get("id", ""),
                "advances": adv})
        state["last_success_date"] = today()
        state["last_run"] = {**entry, "pushed_sha": args.commit or ""}
    elif args.result in ("failed", "blocked") :
        state["last_run"] = entry
    state["remaining"] = [
        {k: t[k] for k in ("label", "paths", "risk") if k in t}
        for t in scan.scan_surfaces(project) if t["status"] == "unanimated"]
    save_state(project, state)
    scribe.update(project)
    print(f"recorded: {entry['result']} {entry['task_id']}")
    # Where this release sits in the goal tree — computed from data, no LLM.
    line = goals.report_line(profile_of(project), state, task)
    if line:
        print(line)
    # The orchestrator relays the research agents' flags mechanically —
    # code prints them here on EVERY record; the run banner quotes this block.
    print_agent_flags(project)
    block = goals.review_block(state_of(project))
    if block:
        print("\n" + block)


def cmd_pick(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    profile = profile_of(project)
    state = state_of(project)
    seq = len(state.get("runs", [])) + 1
    from team.scan import next_task, scan_surfaces
    surfaces = scan_surfaces(project)
    unsup_only = (not any(s["status"] == "unanimated" and s.get("risk") != "supervised"
                          for s in surfaces)
                  and any(s.get("risk") == "supervised" and s["status"] == "unanimated"
                          for s in surfaces))
    task = next_task(project, seq, supervised=getattr(args, "supervised", False))
    if task is not None and unsup_only and not getattr(args, "supervised", False):
        print(json.dumps({"pick": None,
                          "note": "only supervised tasks remain — needs a human present",
                          "would_be": task["id"]}, indent=2))
        return 3
    if task is None:
        print(json.dumps({"pick": None,
                          "note": "nothing actionable: all targets shipped/animated "
                                  "and no executable queue entry (motion/bugfix/feature)"},
                         indent=2))
        return 2
    pdir = pipeline_dir(project) / "tasks"
    pdir.mkdir(parents=True, exist_ok=True)
    spec_path = pdir / f"{task['id']}.json"
    spec_path.write_text(json.dumps(task, indent=2) + "\n")
    print(json.dumps({"pick": task, "spec": str(spec_path),
                      "worktree_cmd": f"python3 cli.py worktree-start {project.name} --task-id {task['id']}",
                      "prompt_cmd": f"python3 cli.py developer-prompt {project.name} {spec_path.name}"},
                     indent=2))
    return 0


def cmd_tree(args: argparse.Namespace) -> None:
    """Deterministic view of the goal tree: vision → milestones → leaves."""
    from team import tree as tree_mod
    project = resolve_project(args.project)
    if args.html:
        print(tree_mod.write(project, open_after=args.open))
        return
    model = tree_mod.build(project)
    print(tree_mod.render_text(model))
    path = tree_mod.write(project, open_after=args.open)
    print(f"\nhtml: {path}")


def cmd_goal(args: argparse.Namespace) -> int:
    """The team CHANGES the tree; the owner reviews afterwards (never waits).

    Every change stores the whole goals block before/after, so `revert` is exact
    and the audit line is readable by a non-engineer."""
    from team.core import save_json
    project = resolve_project(args.project)
    profile, state = profile_of(project), state_of(project)
    action = args.action
    pfile = pipeline_dir(project) / "profile.json"

    if action == "log":
        entries = state.get("tree_changes") or []
        if not entries:
            print("the plan has never been changed since it was written")
            return 0
        for e in entries[-int(args.limit or 20):]:
            flag = "reverted" if e.get("reverted") else (
                "seen" if e.get("reviewed") else "TO REVIEW")
            print(f"  [{flag}] {str(e.get('at'))[:16]} {e.get('action')} "
                  f"'{e.get('node')}' — {e.get('why')}")
        return 0
    if action == "review":
        n = 0
        for e in state.get("tree_changes") or []:
            if not e.get("reviewed"):
                e["reviewed"], n = True, n + 1
        save_state(project, state)
        print(f"marked {n} plan change(s) as seen")
        return 0
    if action == "revert":
        entries = [e for e in (state.get("tree_changes") or [])
                   if not e.get("reverted") and e.get("goals_before") is not None]
        if not entries:
            raise SystemExit("nothing to revert — no plan change is outstanding")
        last = entries[-1]
        profile["goals"] = last.get("goals_before") or {}
        last["reverted"] = True
        goals.change(state, "reverted by owner", str(last.get("node")),
                     f"undo of: {last.get('action')} — {last.get('why')}",
                     before=last.get("after") or "", after=last.get("before") or "",
                     by="owner")
        state["tree_changes"][-1]["reviewed"] = True
        save_json(pfile, profile)
        save_state(project, state)
        from team import tree as tree_mod
        tree_mod.write(project)
        print(f"reverted: {last.get('action')} '{last.get('node')}' — the plan is "
              f"back to how it was before that change")
        print(f"  (it was made because: {last.get('why')})")
        return 0

    # ---- mutating actions below: snapshot first, then mutate, then record ----
    before = json.loads(json.dumps(profile.get("goals") or {}))
    if not before.get("milestones") and action != "add":
        raise SystemExit("this project has no plan yet — the owner must state the "
                         "long-term goal first (devteam onboard / profile['goals'])")
    node_id = args.id or ""

    def _new_id(title: str) -> str:
        import re as _re
        slug = _re.sub(r"[^a-z0-9]+", "-", (title or "branch").lower()).strip("-")
        taken = {n["id"] for n in goals.nodes(profile)}
        base, i = (slug or "branch")[:40], 2
        while slug in taken:
            slug = f"{base}-{i}"
            i += 1
        return slug

    if action == "add":
        parent = args.parent or "vision"
        title = args.title or ""
        if not title:
            raise SystemExit("goal add needs --title (what this branch is for)")
        new = {"id": args.id or _new_id(title), "kind": args.kind or "subgoal",
               "title": title, "decided_by": "agent" if args.by == "agent" else "owner",
               "why": (args.why or "")[:300], "status": args.status or "active"}
        if args.outcome:
            new["outcome"] = args.outcome
        if args.measure:
            new["measures"] = [{"text": m} for m in args.measure]
        kids = goals.holder_list(profile, parent)
        if kids is None:
            raise SystemExit(f"no branch '{parent}' to add under "
                             f"(known: {[n['id'] for n in goals.nodes(profile)]})")
        kids.append(new)
        node_id = new["id"]

    elif action in ("park", "set-status"):
        st = args.status or ("parked" if action == "park" else "")
        raw, _parent = goals.raw_node(profile, args.id or "")
        if raw is None:
            raise SystemExit(f"no goal or sub-goal with id '{args.id}' "
                             f"(known: {[n['id'] for n in goals.nodes(profile)]})")
        if st not in ("active", "pending", "done", "parked", "proposed"):
            raise SystemExit("status must be one of: active, pending, done, "
                             "parked, proposed")
        if st == "proposed" and str(raw.get("decided_by") or "") != "agent":
            # 'proposed' only means something for agent-authored branches; keep
            # the distinction honest instead of silently accepting anything.
            raise SystemExit("'proposed' is for agent-authored branches; "
                             "owner-set goals use active/pending/done/parked")
        raw["status"] = st
        node_id = args.id

    elif action == "retire":
        raw, parent = goals.raw_node(profile, args.id or "")
        if raw is None:
            raise SystemExit(f"no goal or sub-goal with id '{args.id}'")
        kids = goals.holder_list(profile, parent or "vision") or []
        if raw in kids:
            kids.remove(raw)
        node_id = args.id

    elif action == "reparent":
        raw, parent = goals.raw_node(profile, args.id or "")
        if raw is None:
            raise SystemExit(f"no goal or sub-goal with id '{args.id}'")
        if goals.raw_node(profile, args.parent or "")[0] is None:
            raise SystemExit(f"no branch '{args.parent}' to move it under")
        kids = goals.holder_list(profile, parent or "vision") or []
        if raw in kids:
            kids.remove(raw)
        (goals.holder_list(profile, args.parent or "vision") or []).append(raw)
        node_id = args.id
    else:
        raise SystemExit("goal needs an action: add | park | set-status | retire | "
                         "reparent | revert | review | log")

    after = profile.get("goals") or {}
    human = {"add": f"added under {args.parent or 'vision'}",
             "park": "set aside", "set-status": f"status → {args.status}",
             "retire": "removed from the plan",
             "reparent": f"moved under {args.parent}"}.get(action, action)
    e = goals.change(state, human, node_id, args.why or "(no reason given)",
                     before="", after="", by=args.by)
    e["goals_before"], e["goals_after"] = before, after
    save_json(pfile, profile)
    save_state(project, state)
    from team import tree as tree_mod
    tree_mod.write(project)
    print(f"plan changed: {human} '{node_id}' (why: {e['why']})")
    print("the owner will see this on the next status/page — no approval needed; "
          f"`devteam goal {project} revert` undoes it")
    return 0


def cmd_align(args: argparse.Namespace) -> int:
    """Does this serve a goal? The verdict comes from an agent; code records it."""
    from team import align as align_mod
    project = resolve_project(args.project)
    if args.apply:
        entry = align_mod.apply_file(project, Path(args.apply))
        print(align_mod.line(entry))
        if entry["verdict"] in ("against", "gap"):
            print("\nOWNER, THIS ONE NEEDS YOUR EYES — the team is not hiding it: "
                  + ("this spends time against a goal you set."
                     if entry["verdict"] == "against" else
                     "the work may be right but the plan has no place for it; "
                     "the team changed the tree and is telling you here."))
        from team import tree as tree_mod
        tree_mod.write(project)
        return 1 if entry["verdict"] == "against" else 0
    request = args.request or ""
    if not request:
        raise SystemExit("align needs the request to judge as text, e.g.\n"
                         "  devteam align <project> \"add a dark mode toggle\"")
    task = None
    if args.task_spec:
        task = json.loads(task_spec_path(project, args.task_spec).read_text())
    prompt = align_mod.alignment_prompt(project, request, task)
    side = pipeline_dir(project) / "alignment-prompt.txt"
    side.write_text(prompt + "\n")
    (pipeline_dir(project) / "alignment-request.txt").write_text(request.strip() + "\n")
    print(prompt)
    print(f"\n[written to {side} — hand it to your agent, then:\n"
          f" python3 cli.py align {project} --apply <the json it wrote>]")
    return 0


def cmd_status(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    profile = profile_of(project)
    state = state_of(project)
    surfaces = scan.scan_surfaces(project)
    print(f"== {profile['name']} ==")
    print(f"repo:       {profile['repo_slug']} (branch {profile['main_branch']})")
    # Where the work is going (deterministic, LLM-free): vision → milestone →
    # queue. A project with no goal tree prints a one-line goal-less notice.
    print(goals.goal_block(profile, state))
    if state.get("queue"):
        # Plain: group the queue by the goal it serves, by TITLE not id — the
        # owner should not have to hold the codes in their head.
        titled = {m["id"]: m["title"] for m in (goals.plan(profile) or {}).get("milestones", [])}
        groups: dict[str, int] = {}
        for q in state["queue"]:
            key = str(q.get("advances") or "").strip()
            groups[key] = groups.get(key, 0) + 1
        parts = []
        for mid, n in groups.items():
            what = titled.get(mid, "no goal declared" if not mid else mid)
            parts.append(f"{n} serving \"{what}\"")
        print(f"queue:      {len(state['queue'])} waiting — " + "; ".join(parts))
    print(f"targets:    {len(profile['targets'])} shipped-do: "
          f"{sum(1 for s in surfaces if s['status'] == 'shipped')} "
          f"animated: {sum(1 for s in surfaces if s['status'] == 'animated')} "
          f"unanimated: {sum(1 for s in surfaces if s['status'] == 'unanimated')}")
    for s in surfaces:
        risk = s.get("risk", "safe")
        mark = {"shipped": "✓", "animated": "≈", "unanimated": "·"}[s["status"]]
        warn = "  ⚠ supervised-only" if risk == "supervised" and s["status"] == "unanimated" else ""
        print(f"  {mark} {s['label']:<34} {s['status']}{warn}")
    last = state.get("last_run", {})
    if last:
        print(f"last run:   {last.get('date')} → {last.get('result')} {last.get('note','')[:80]}")
    r = subprocess.run(["git", "log", "--oneline", "-1"], cwd=project,
                       capture_output=True, text=True)
    print(f"local HEAD: {r.stdout.strip()}")
    # Read-only: status never migrates, but the owner must know the project is
    # behind the team (the workflow's own step 0 fixes it).
    try:
        from team import adapt as adapt_mod
        todo = adapt_mod.needed(project)
        if todo:
            print(f"\nADAPT NEEDED ({len(todo)}): " + "; ".join(todo[:3])
                  + f"\n  the workflow's first step fixes this: devteam adapt "
                    f"{project.name}")
    except Exception:
        pass
    # What the team changed about the plan since the owner last looked, and the
    # honest alignment verdicts (both advisory: nothing is silently blocked).
    for block in (goals.review_block(state), goals.alignment_block(state)):
        if block:
            print("\n" + block)


def cmd_scan(args: argparse.Namespace) -> None:
    cmd_status(args)


def cmd_poll(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    logs = sorted((pipeline_dir(project) / "runs").glob("*.log"))
    if not logs:
        print("no run logs yet")
        return
    lines = logs[-1].read_text().splitlines()
    print("\n".join(lines[-args.tail:]))


def cmd_schedules(args: argparse.Namespace) -> None:
    """Owner-visible clash map: every registered devteam schedule."""
    installed = scheduler.list_installed_schedules()
    if not installed:
        print("no auto schedules registered — all projects run manually")
        return
    for s in sorted(installed, key=lambda x: (x["hour"], x["minute"])):
        print(f"  {s['hour']:02d}:{s['minute']:02d}  {s['label']}")
    print("(default trigger is MANUAL; auto is opt-in per project via "
          "`devteam setup --schedule auto [--at HH:MM]`)")


def cmd_research(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    log = RunLog(project, "research-" + today())
    from team import research, doctor
    doctor.run_doctor(project, log)
    try:
        if args.apply_result:
            research.research_apply_file(project, Path(args.apply_result), log)
        else:
            research.research_run(project, log, use_llm=not args.no_llm)
    except SystemExit as e:
        if e.code in (0, None):
            return 0
        print(str(e))
        return 2
    s = state_of(project)
    print(json.dumps({"queue": s.get("queue", []),
                      "insights": s.get("insights", {})}, indent=2))
    return 0


def cmd_adapt(args: argparse.Namespace) -> int:
    """STEP 0 of any workflow: make the project match the CURRENT team.

    Deterministic, idempotent, never invents direction — a missing long-term goal
    comes back as an OWNER ASK, not as a drafted vision."""
    from team import adapt as adapt_mod
    project = resolve_project(args.project)
    res = adapt_mod.run(project, apply=not args.dry_run)
    print(adapt_mod.report(project, res, profile_of(project).get("name"),
                           dry=args.dry_run))
    return 0


def cmd_vision(args: argparse.Namespace) -> int:
    """The ONE thing only the owner writes: the long-term goal (tree root)."""
    from team.core import save_json
    project = resolve_project(args.project)
    profile, state = profile_of(project), state_of(project)
    text = (args.text or "").strip()
    if not text:
        raise SystemExit("vision needs the owner's sentence, e.g.\n"
                         "  devteam vision <project> \"what this app is for, "
                         "in one sentence\"")
    goals_block = profile.setdefault("goals", {})
    old = str(goals_block.get("vision") or "")
    if old == text:
        print("the goal is already exactly that — nothing changed")
        return 0
    goals_block["vision"] = text
    goals_block.setdefault("milestones", [])
    goals.change(state, "set the long-term goal", "vision", args.why or
                 "the owner wrote the goal the team works toward",
                 before=old or "(none)", after=text, by="owner")
    save_json(pipeline_dir(project) / "profile.json", profile)
    save_state(project, state)
    from team import tree as tree_mod
    tree_mod.write(project)
    print(f"the goal is now: {text}")
    if not goals_block["milestones"]:
        print("  next: the team's job is to propose the milestones under it "
              "(`devteam research`), not to ask you for them")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    log = RunLog(project, "doctor-" + today())
    # Adapt FIRST: the readiness check must measure the project against the team
    # it is actually being run by, not the one it was onboarded under.
    from team import adapt as adapt_mod
    try:
        res = adapt_mod.run(project, log)
        print(adapt_mod.report(project, res, profile_of(project).get("name")))
    except SystemExit:
        pass   # not onboarded at all — the doctor report below says so
    from team import doctor
    report = doctor.run_doctor(project, log)
    blocked = {a: r for a, r in report.items() if r["status"] == "blocked"}
    if blocked:
        for agent, r in blocked.items():
            for b in r["blocked"]:
                print(f"ASK OWNER ({agent}): {b['requirement']} → {b['fix']}")
        return 1
    return 0


def cmd_alarm(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    log = RunLog(project, "alarm-" + today())
    return alarm.check(project, log)


def cmd_setup(args: argparse.Namespace) -> None:
    problems = scheduler.env_checks()
    if problems:
        print("environment problems:")
        for p in problems:
            print("  - " + p)
        if args.strict:
            raise SystemExit(1)
    made, kept = scheduler.install_skill_symlinks()
    for m in made:
        print("skill linked: " + m)
    for k in kept:
        print("skill already present: " + k)
    project = resolve_project(args.project)
    if not (pipeline_dir(project) / "profile.json").exists():
        print("onboarding first...")
        cmd_onboard(argparse.Namespace(project=str(project)))
    if args.schedule == "manual":
        print(f"MANUAL mode installed for {project.name}: nothing scheduled.")
        print("  run it whenever with: 'follow the dev-team skill — MODE A run on "
              + project.name + "' in any agent session")
        print("  to enable auto later: devteam setup --project " + str(project)
              + " --schedule auto [--at HH:MM]")
        print("  current registered schedules:")
        for s in scheduler.list_installed_schedules():
            print(f"    - {s['label']}: {s['hour']:02d}:{s['minute']:02d}")
        return
    # opt-in auto: clash-checked slot (explicit --at wins if free)
    proposed = scheduler.pick_free_slot(args.at)
    hh, mm = proposed
    conflicts = [s["label"] for s in scheduler.list_installed_schedules()
                 if s["hour"] == hh and s["minute"] == mm]
    if conflicts:
        raise SystemExit(f"slot {hh:02d}:{mm:02d} collides with {conflicts}; choose another --at")
    plist = scheduler.install_launchd(project.name, f"{hh:02d}:{mm:02d}")
    print(f"AUTO schedule installed: {plist.stem} — daily {hh:02d}:{mm:02d}")
    print("  clash-checked against: " + (", ".join(
        f"{s['label']}@{s['hour']:02d}:{s['minute']:02d}"
        for s in scheduler.list_installed_schedules()) or "(none)"))
    print("  disable with: launchctl bootout gui/$(id -u)/" + plist.stem)
    print(f"  manual-mode alternative remains possible anytime".strip())


def main() -> None:
    ap = argparse.ArgumentParser(prog="devteam")
    sub = ap.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("onboard");     o.add_argument("project"); o.set_defaults(fn=cmd_onboard)
    s = sub.add_parser("status");      s.add_argument("project"); s.add_argument("--skip-gradle", action="store_true"); s.set_defaults(fn=cmd_status)
    sc = sub.add_parser("scan");       sc.add_argument("project"); sc.add_argument("--skip-gradle", action="store_true"); sc.set_defaults(fn=cmd_scan)
    pk = sub.add_parser("pick"); pk.add_argument("project"); pk.add_argument("--supervised", action="store_true"); pk.set_defaults(fn=cmd_pick)
    w = sub.add_parser("worktree-start"); w.add_argument("project"); w.add_argument("--task-id", required=True); w.set_defaults(fn=cmd_worktree_start)
    dp = sub.add_parser("developer-prompt"); dp.add_argument("project"); dp.add_argument("task_spec"); dp.add_argument("--template", choices=["motion", "generic", "auto"], default="auto"); dp.set_defaults(fn=cmd_developer_prompt)
    q = sub.add_parser("queue"); q.add_argument("project"); q.add_argument("--done")
    q.add_argument("action", nargs="?", choices=["add", "review"])
    q.add_argument("--spec"); q.add_argument("--type")
    q.add_argument("--label"); q.add_argument("--target"); q.add_argument("--reason")
    q.add_argument("--risk"); q.add_argument("--position", choices=["head", "tail"],
                                             default="head")
    q.add_argument("--advances", help="milestone id this task serves "
                                      "(profile.goals.milestones[].id)")
    q.add_argument("--supersedes", action="append", default=[])
    q.set_defaults(fn=cmd_queue)
    rh = sub.add_parser("run-headless"); rh.add_argument("project"); rh.add_argument("--task-spec", required=True); rh.add_argument("--supervised", action="store_true"); rh.set_defaults(fn=cmd_run_headless)
    ro = sub.add_parser("run-orchestrator"); ro.add_argument("project"); ro.add_argument("--prompt"); ro.add_argument("--timeout", type=int, default=5400); ro.set_defaults(fn=cmd_run_orchestrator)
    v = sub.add_parser("verify");      v.add_argument("project"); v.add_argument("--worktree"); v.add_argument("--skip-gradle", action="store_true"); v.set_defaults(fn=cmd_verify)
    em = sub.add_parser("emulators");  em.add_argument("project"); em.add_argument("--record", action="store_true"); em.add_argument("--reset", action="store_true"); em.set_defaults(fn=cmd_emulators)
    r = sub.add_parser("release");     r.add_argument("project"); r.add_argument("--worktree", required=True); r.add_argument("--task-spec", required=True); r.set_defaults(fn=cmd_release)
    rec = sub.add_parser("record"); rec.add_argument("project")
    rec.add_argument("--task"); rec.add_argument("--task-spec"); rec.add_argument("--result", required=True,
              choices=["shipped", "failed", "blocked"])
    rec.add_argument("--note"); rec.add_argument("--version"); rec.add_argument("--commit")
    rec.set_defaults(fn=cmd_record)
    po = sub.add_parser("poll");       po.add_argument("project"); po.add_argument("--tail", type=int, default=60); po.set_defaults(fn=cmd_poll)
    a = sub.add_parser("alarm");       a.add_argument("project"); a.set_defaults(fn=cmd_alarm)
    rs = sub.add_parser("research");   rs.add_argument("project"); rs.add_argument("--no-llm", action="store_true")
    rs.add_argument("--apply-result", help="synthesis JSON produced by the host tool's native agent; applies through the same validation + goal gate")
    rs.set_defaults(fn=cmd_research)
    do = sub.add_parser("doctor");     do.add_argument("project"); do.set_defaults(fn=cmd_doctor)
    ad = sub.add_parser("adapt");      ad.add_argument("project")
    ad.add_argument("--dry-run", action="store_true",
                    help="report what would change without touching anything")
    ad.set_defaults(fn=cmd_adapt)
    vi = sub.add_parser("vision");     vi.add_argument("project")
    vi.add_argument("text", nargs="?", help="the owner's long-term goal, one sentence")
    vi.add_argument("--why", help="why the goal changed (optional)")
    vi.set_defaults(fn=cmd_vision)
    st = sub.add_parser("setup");  st.add_argument("--project", required=True)
    st.add_argument("--schedule", choices=["manual", "auto"], default="manual")
    st.add_argument("--at", default=None); st.add_argument("--strict", action="store_true"); st.set_defaults(fn=cmd_setup)
    sch = sub.add_parser("schedules"); sch.set_defaults(fn=cmd_schedules)
    tt = sub.add_parser("tree"); tt.add_argument("project")
    tt.add_argument("--html", action="store_true", help="write the html view and print its path")
    tt.add_argument("--open", action="store_true", help="open the html view in the browser")
    tt.set_defaults(fn=cmd_tree)
    g = sub.add_parser("goal")
    g.add_argument("project")
    g.add_argument("action", choices=["add", "park", "set-status", "retire",
                                      "reparent", "revert", "review", "log"])
    g.add_argument("--id", help="the branch to change (see `goal log` / the tree page)")
    g.add_argument("--title", help="add: what this branch is for, in plain words")
    g.add_argument("--parent", help="add/reparent: vision or another branch id")
    g.add_argument("--kind", default="subgoal", choices=["subgoal", "milestone"])
    g.add_argument("--status", help="set-status: active|pending|done|parked|proposed")
    g.add_argument("--outcome", help="add: what will be true when this is done")
    g.add_argument("--measure", action="append",
                   help="add: how we will know (repeatable, plain words)")
    g.add_argument("--why", help="the reason the team is changing the plan")
    g.add_argument("--by", default="agent", choices=["agent", "owner"])
    g.add_argument("--limit", type=int, default=20)
    g.add_argument("--last", action="store_true",
                   help="revert: undo the most recent change (the only mode)")
    g.set_defaults(fn=cmd_goal)
    al = sub.add_parser("align")
    al.add_argument("project")
    al.add_argument("request", nargs="?", help="the task or owner request to judge")
    al.add_argument("--task-spec", help="judge a picked task spec instead of free text")
    al.add_argument("--apply", help="apply a verdict JSON the agent wrote")
    al.set_defaults(fn=cmd_align)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
