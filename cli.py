#!/usr/bin/env python3
"""dev-team CLI — deterministic bookkeeping and gates ONLY.

The orchestrator is an AGENTIC TOOL (opencode / claude / ...) running with the
dev-team skill: it decides and drives; native subagents develop. This CLI
exposes the deterministic actions that agent needs, and nothing project-shaped
is hardcoded here — every project fact comes from <project>/.pipeline/profile.json.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from team import alarm, scribe, scan, scheduler, shipper  # noqa: E402
from team.core import (  # noqa: E402
    RunLog, TEAM_ROOT, load_json, now_iso, pipeline_dir, profile_of,
    resolve_project, run as sys_run, runtimes, save_json, save_state, state_of, today,
)

CORE_MOTION_TOKENS = scan.CORE_MOTION_TOKENS


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
             "created": now_iso()}
    save_json(pdir / "state.json", state)

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
    task = load_json(Path(args.task_spec))
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


def cmd_developer_prompt(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    profile = profile_of(project)
    task = load_json(Path(args.task_spec)) or json.loads(Path(args.task_spec).read_text())
    paths = task["surface"].get("targets") or []
    from team.developer import build_prompt
    print(build_prompt(project, profile, task, paths,
                       tpl_override=getattr(args, "template", None)))


def cmd_queue(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    state = state_of(project)
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
    print("VERIFY: PASS" if ok else "VERIFY: FAIL")
    for f in failures:
        print(f)
    return 0 if ok else 1


def cmd_release(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    log = RunLog(project, "release-" + today())
    worktree = Path(args.worktree).resolve()
    latest = shipper.remote_latest_version(project)
    if latest is None:
        raise SystemExit("no v* tags on origin; cannot compute next version")
    new = (latest[0], latest[1], latest[2] + 1)
    task = json.loads(Path(args.task_spec).read_text())
    name = shipper.bump_version_file(worktree, new, log)
    shipper.write_whatsnew(worktree, task["surface"]["label"])
    res = shipper.push_and_deliver(project, worktree, task, name, log)
    print(json.dumps(res, indent=2))
    return 0 if res.get("delivered") else 1


def cmd_record(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    state = state_of(project)
    task = json.loads(Path(args.task_spec).read_text()) if args.task_spec else {}
    entry = {"date": today(), "task_id": args.task or task.get("id", "?"),
             "result": args.result, "note": args.note or "",
             "finished": now_iso()}
    state.setdefault("runs", []).append(entry)
    if args.result == "shipped":
        label = task.get("surface", {}).get("label", args.task or "?")
        targets = task.get("surface", {}).get("targets", [])
        already = {s["target"] for s in state.get("shipped", [])}
        for t in targets:
            if t in already:
                continue  # concurrent runs may both ship; keep one ledger row per target
            state.setdefault("shipped", []).append({
                "target": t, "label": label, "version": args.version or "?",
                "commit": args.commit or "", "date": today()})
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
    # The orchestrator relays the research agents' flags mechanically —
    # code prints them here on EVERY record; the run banner quotes this block.
    print_agent_flags(project)


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


def cmd_status(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    profile = profile_of(project)
    state = state_of(project)
    surfaces = scan.scan_surfaces(project)
    print(f"== {profile['name']} ==")
    print(f"repo:       {profile['repo_slug']} (branch {profile['main_branch']})")
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


def cmd_doctor(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    log = RunLog(project, "doctor-" + today())
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
    q = sub.add_parser("queue"); q.add_argument("project"); q.add_argument("--done"); q.set_defaults(fn=cmd_queue)
    rh = sub.add_parser("run-headless"); rh.add_argument("project"); rh.add_argument("--task-spec", required=True); rh.add_argument("--supervised", action="store_true"); rh.set_defaults(fn=cmd_run_headless)
    v = sub.add_parser("verify");      v.add_argument("project"); v.add_argument("--worktree"); v.add_argument("--skip-gradle", action="store_true"); v.set_defaults(fn=cmd_verify)
    r = sub.add_parser("release");     r.add_argument("project"); r.add_argument("--worktree", required=True); r.add_argument("--task-spec", required=True); r.set_defaults(fn=cmd_release)
    rec = sub.add_parser("record"); rec.add_argument("project")
    rec.add_argument("--task"); rec.add_argument("--task-spec"); rec.add_argument("--result", required=True,
              choices=["shipped", "failed", "blocked"])
    rec.add_argument("--note"); rec.add_argument("--version"); rec.add_argument("--commit")
    rec.set_defaults(fn=cmd_record)
    po = sub.add_parser("poll");       po.add_argument("project"); po.add_argument("--tail", type=int, default=60); po.set_defaults(fn=cmd_poll)
    a = sub.add_parser("alarm");       a.add_argument("project"); a.set_defaults(fn=cmd_alarm)
    rs = sub.add_parser("research");   rs.add_argument("project"); rs.add_argument("--no-llm", action="store_true"); rs.set_defaults(fn=cmd_research)
    do = sub.add_parser("doctor");     do.add_argument("project"); do.set_defaults(fn=cmd_doctor)
    st = sub.add_parser("setup");  st.add_argument("--project", default="MindfulLauncher")
    st.add_argument("--schedule", choices=["manual", "auto"], default="manual")
    st.add_argument("--at", default=None); st.add_argument("--strict", action="store_true"); st.set_defaults(fn=cmd_setup)
    sch = sub.add_parser("schedules"); sch.set_defaults(fn=cmd_schedules)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
