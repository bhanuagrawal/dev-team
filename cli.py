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
        # Play rejects a release note longer than this, and the pipeline only
        # REWRITES the note when the owner does not curate it by hand.
        "whatsnew_max_chars": 500,
        "whatsnew_curated": False,
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


def print_owner_asks(project: Path) -> None:
    """Questions ONLY the owner can answer, printed by code on EVERY record.

    A run that ships is still a run that must ask: if the app has no long-term
    goal, the banner carries the ask for that one sentence instead of quietly
    shipping a goal-less build. `adapt`/`doctor` print the same ask at the start —
    this is the copy the run REPORT must contain, so the question cannot be
    buried in step-0 output."""
    asks = goals.owner_asks(profile_of(project), state_of(project), project.name)
    if not asks:
        return
    print("--- OWNER ASKS (only the owner can answer these — "
          "quote this block in the run banner) ---")
    for a in asks:
        print(f"  ? {a['what']}")
        if a.get("how"):
            print(f"    how: {a['how']}")


def latest_shipped_version(state: dict) -> str:
    """The version this project last delivered, from the ledger (authoritative —
    the local checkout may lag origin/main by many commits)."""
    rows = state.get("shipped") or []
    if rows:
        v = str(rows[-1].get("version") or "")
        if v and v != "?":
            return v
    return str((state.get("last_run") or {}).get("version") or "")


def rc_verdict(state: dict, version: str) -> dict | None:
    """The release-candidate CALL recorded for `version` — newest wins, so a later
    call (an owner override) supersedes an earlier one. None until someone
    records one."""
    for e in reversed(state.get("release_candidates") or []):
        if str(e.get("version") or "") == str(version):
            return e
    return None


def _prev_shipped_entry(state: dict, version: str) -> dict | None:
    """The most recent shipped ledger entry OTHER than `version`, newest first.

    Used only to detect a release note that repeats the previous release
    verbatim — the case a curated note silently freezes into."""
    for entry in reversed(state.get("shipped") or []):
        if str(entry.get("version")) != str(version):
            return entry
    return None


def print_release_handover(project: Path, task: dict, version: str,
                           commit: str = "") -> None:
    """Hand the owner the shipped build: its release note, what to test by hand,
    and the release-candidate call. Printed by code on every shipped `record` —
    the run banner quotes this block verbatim.

    WHY these three and nothing else: the pipeline's gates prove the build
    compiles, passes its tests and installs; they cannot feel the app. This run's
    sibling lessons are the proof — a device gate passed while a success screen
    printed a MediaStore id, and a hand walk found seven defects the crawler's
    "0 blocked taps" had hidden. So the run reports WHAT a user will read, WHAT
    to walk by hand, and the release-candidate call.

    The CALL IS NOT COMPUTED HERE (owner directive 2026-10-24, second pass:
    *"let llm decide this, ask this to llm only which made the changes, that's
    it, no eval nothing"*): nothing in this codebase rules on whether a build
    stands on its own — that judgment is the LLM's, and the one to ask is the LLM
    THAT MADE THE CHANGE. So when no call is recorded we print the question and
    the one command that records the answer, and leave it. Nothing acts on the
    verdict either: promoting beyond internal testing stays the owner's manual
    step in Play Console."""
    profile = profile_of(project)
    state = state_of(project)
    wf_rel = profile.get("whatsnew_file")
    print("--- RELEASE HANDOVER (v" + version + " → internal testing — "
          "quote this block in the run banner) ---")
    if wf_rel:
        cap = shipper.whatsnew_max_chars(profile)
        note = shipper.whatsnew_read(project, commit).rstrip("\n")
        if profile.get("whatsnew_curated"):
            source = (f"owner-curated in {wf_rel} — the pipeline read it, did not "
                      "rewrite it; update that file for the next release")
        elif isinstance(task, dict) and (task.get("whatsnew") or "").strip():
            source = (f"written into the task spec by the orchestrator, used "
                      f"verbatim into {wf_rel}")
        else:
            source = f"pipeline-generated from the task into {wf_rel}"
        if not note:
            print(f"  what's new: NO note found at {wf_rel} — the store would show "
                  "nothing for this release")
        else:
            print(f"  what's new ({len(note)}/{cap} chars, {source}) — what Play shows the user:")
            for ln in note.splitlines():
                print(f"    {ln}")
            # An owner-curated note the pipeline may not rewrite can silently
            # freeze: if it is byte-for-byte the previous release's note, Play
            # will show the same "what's new" again and only the owner can fix
            # it. Say so instead of printing it as if it were this release's.
            prev = _prev_shipped_entry(state, version)
            if profile.get("whatsnew_curated") and prev:
                prev_note = (shipper.whatsnew_read(project, prev.get("commit") or "")
                             or "").rstrip("\n")
                if prev_note and prev_note == note:
                    print(f"    ⚠ STALE: this note is identical to v{prev.get('version')}'s — "
                          "the store will show the previous release's wording again. "
                          f"It is owner-curated, so the pipeline cannot change it: edit "
                          f"{wf_rel} before the next release.")
    areas = shipper.whatsnew_test_areas(task)
    claim = re.split(r"(?<=[.!?])\s", (task.get("reason") or "").strip())[0]
    if len(claim) > 180:
        claim = claim[:180].rsplit(" ", 1)[0].rstrip(" ,;") + " …"
    if areas or claim:
        print("  manually test — the gates cover compile/unit/device, never the "
              "real-device feel:")
        for a in areas:
            print(f"    · {a}")
        if claim:
            print(f"    · what this release claims to fix: {claim}")
        print(f"    how: update the app from the internal-testing track (v{version}) and "
              "walk it; report what you find back to the agent — a defect here becomes "
              "the next cycle's queue head")
    verdict = rc_verdict(state, version)
    if verdict:
        who = "your override" if verdict.get("by") == "owner" else "the agent that made the change"
        print(f"  release candidate?  {'YES ✅' if verdict.get('rc') else 'NO'} — "
              f"{verdict.get('why') or verdict.get('note') or ''}  [{who}]")
    else:
        print("  release candidate?  NOT CALLED YET — let the LLM that made this change "
              "decide (no rule, no scoring); record its one-line answer:")
        print(f"    ask it: \"you made these changes — does this build stand on its own "
              f"in front of users, yes or no?\"")
        print(f"    then: python3 cli.py rc {project.name} --version {version} "
              '--yes|--no --note "<its one line>"')


def cmd_rc(args: argparse.Namespace) -> None:
    """Record the release-candidate call for a shipped version.

    The call belongs to the LLM that MADE the change (owner directive
    2026-10-24, second pass: *"let llm decide this, ask this to llm only which
    made the changes, that's it, no eval nothing"*) — no rule computes it, no
    scoring, no validation of the answer. This command is only the pen: the agent
    (or the owner, with --owner) states yes/no and one line of why.

    Asked of the orchestrator's developer session — the agent that wrote the code
    — because it is the one that knows whether the work it did is a complete
    thing a user could receive; if that session is closed, the orchestrating LLM
    answers, having seen the diff and the developer's report."""
    project = resolve_project(args.project)
    if bool(args.yes) == bool(args.no):
        raise SystemExit("pass exactly one of --yes / --no")
    state = state_of(project)
    version = str(args.version or latest_shipped_version(state))
    if not version:
        raise SystemExit("no shipped version to answer for — pass --version <ver>")
    by = "owner" if args.owner else "agent"
    entry = {"version": version, "rc": bool(args.yes), "note": args.note or "",
             "why": args.note or "", "date": today(), "by": by}
    state.setdefault("release_candidates", []).append(entry)
    save_state(project, state)
    verdict = "a RELEASE CANDIDATE (✅)" if entry["rc"] else "NOT a release candidate"
    print(f"recorded ({by}): v{version} is {verdict}"
          + (f" — {entry['note']}" if entry["note"] else ""))
    prev = [v for v in reversed(state["release_candidates"][:-1])
            if str(v.get("version") or "") == version]
    if prev and bool(prev[-1].get("rc")) != entry["rc"]:
        print(f"  (this replaces an earlier call on v{version}: "
              f"{'YES' if prev[-1].get('rc') else 'NO'} by {prev[-1].get('by')})")
    print_page(project, "refreshed by this call")


def print_page(project: Path, note: str = "", file=None,
               published: dict | None = None) -> None:
    """Print where the plan lives — after EVERY change and on EVERY run.

    Two lines, because there are two copies of the same plan:
      goal page:   file://…/.pipeline/goals.html      (this machine, click to open)
      shared plan: https://github.com/…/docs/plan.md  (the pushed copy)
    The GitHub line is only printed when it is TRUE (mirror committed, unchanged,
    on origin) — never a guessed URL. `published` is the result of a publish()
    that just ran; without it we check the local refs (no network).
    """
    from team import publish as publish_mod, tree as tree_mod
    dest = pipeline_dir(project) / "goals.html"
    if dest.exists():
        line = f"goal page:  {tree_mod.url(project)}"
        if note:
            line += f"   ({note})"
    else:
        line = f"goal page:  not built yet — python3 cli.py tree {project.name}"
    lines = [line]
    res = published or {}
    if res and not res.get("url"):
        lines.append(f"shared plan: NOT pushed — {res.get('why')}")
    else:
        shared, why = publish_mod.published_url(project)
        lines.append(f"shared plan: {shared}" if shared
                     else f"shared plan: not published yet — {why}")
    print("\n".join(lines), file=file or sys.stdout)


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
        from team import tree as tree_mod
        tree_mod.write(project)
        print_page(project, "shows the new queue item", file=sys.stderr)
        return
    if action == "review":
        from team import queue as qmod
        print(qmod.review(state))
        return
    from team import queue as qmod
    queue = state.get("queue", [])
    if args.done:
        before = len(queue)
        queue = [q for q in queue if q.get("label") != args.done]
        if len(queue) != before:
            state["queue"] = queue
            # A pop is a queue verdict, so it must leave an audit trail: without
            # this the next plan review saw an empty queue where plan_review had
            # recorded "1 waiting", with no event explaining the disappearance —
            # exactly the silent drop the impact review exists to prevent.
            qmod._record(state, "orchestrator", "dropped", args.done,
                         reason="popped by hand (`queue --done`) — removed "
                                "outside a plan review")
            save_state(project, state)
        print(f"queue: {len(queue)} left (popped '{args.done}')" if len(queue) != before
              else f"'{args.done}' not found in queue — nothing popped")
        if len(queue) != before:
            from team import tree as tree_mod
            tree_mod.write(project)
            print_page(project, "the queue changed")
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
    spec = None
    if getattr(args, "task_spec", None):
        sp = Path(args.task_spec)
        if not sp.is_absolute():
            sp = project / args.task_spec
        if sp.exists():
            import json as _json
            spec = _json.loads(sp.read_text())
    ok, failures = gates.verify_all(project, tree, base, log,
                                    gradle=not args.skip_gradle, spec=spec)
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
    shipper.write_whatsnew(worktree, task, log)
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
        entry["version"] = args.version or ""
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
    # A shipped run recorded WITHOUT its task-spec writes no shipped-ledger rows,
    # so the queue entry it delivered cannot later be recognised as delivered
    # (lesson 2026-10-18: exactly this let a delivered entry stay at the head of
    # the plan). Never silent — the orchestrator sees it where it happens.
    if args.result == "shipped" and not (task.get("surface", {}) or {}).get("targets"):
        print("WARNING: shipped recorded with no task-spec targets — no shipped-ledger "
              "rows written; pass --task-spec .pipeline/tasks/<id>.json so the queue "
              "entry is marked delivered")
    # Where this release sits in the goal tree — computed from data, no LLM.
    line = goals.report_line(profile_of(project), state, task)
    if line:
        print(line)
    # The orchestrator relays the research agents' flags mechanically —
    # code prints them here on EVERY record; the run banner quotes this block.
    print_agent_flags(project)
    # …and the owner's own open questions (no long-term goal yet) are printed on
    # EVERY record too: a shipped build must not swallow the ask.
    print_owner_asks(project)
    # A shipped run also hands the owner the BUILD itself: the note Play will
    # show, what to walk by hand, and the release-candidate call (asked of the
    # LLM that made the change — see SKILL 10d). Same rule as the blocks above:
    # code prints it, the banner quotes it.
    if args.result == "shipped":
        print_release_handover(project, task,
                              str(args.version or latest_shipped_version(state)),
                              str(args.commit or ""))
    block = goals.review_block(state_of(project))
    if block:
        print("\n" + block)
    # Every run ends by handing the owner the page: "where we are now", one paste.
    # The plan itself is published too, so what the owner opens is what the repo
    # says — see team/publish.py (best-effort: never fails a run).
    from team import publish as publish_mod
    published = publish_mod.publish(project)
    print_page(project, "refreshed by this record", published=published)


def cmd_pick(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    profile = profile_of(project)
    state = state_of(project)
    seq = len(state.get("runs", [])) + 1
    # GOAL GATE — owner directive 2026-10-18 ("I should have been prompted to
    # provide long term goals. There should not be no goal-less mode"). A project
    # with no vision gets NO work picked: the run's terminal state is the OWNER
    # ASK, handed back as a distinct rc so the orchestrator must put the question
    # to the owner instead of building on an assumption nobody made.
    if goals.vision_required(profile):
        ask = goals.direction_ask(profile, state, project.name) or {}
        print(json.dumps({"pick": None, "goal_required": True, "owner_ask": ask,
                          "note": "no long-term goal — ask the owner for one sentence; "
                                  "nothing is picked or built until it is set"},
                         indent=2))
        print_owner_asks(project)
        print_page(project, "this project's plan (no goal yet)")
        return 4
    from team.scan import next_task, scan_surfaces
    surfaces = scan_surfaces(project)
    unsup_only = (not any(s["status"] == "unanimated" and s.get("risk") != "supervised"
                          for s in surfaces)
                  and any(s.get("risk") == "supervised" and s["status"] == "unanimated"
                          for s in surfaces))
    task = next_task(project, seq, supervised=getattr(args, "supervised", False))
    if task is None and getattr(args, "auto_refill", False):
        from team import refill
        log = RunLog(project, "pick-refill-" + today())
        r = refill.auto_refill(project, log=log)
        if r.get("status") == "refilled":
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
    # Carry the queue entry's dependency declaration into the task spec: the
    # verify gate REFUSES a new third-party dependency that the spec does not
    # declare, and the spec is what the gate reads. Without this copy the
    # declaration could never reach it and every dependency change would fail.
    label = (task.get("surface") or {}).get("label")
    for entry in (state_of(project).get("queue") or []):
        if entry.get("label") == label and entry.get("dependencies_added"):
            task["dependencies_added"] = entry["dependencies_added"]
            break
    spec_path = pdir / f"{task['id']}.json"
    spec_path.write_text(json.dumps(task, indent=2) + "\n")
    print(json.dumps({"pick": task, "spec": str(spec_path),
                      "worktree_cmd": f"python3 cli.py worktree-start {project.name} --task-id {task['id']}",
                      # .pipeline/tasks/ is relative to the PROJECT dir, which
                      # developer-prompt already searches — a bare filename
                      # resolves nowhere (CWD/team dir hold no tasks/).
                      "prompt_cmd": f"python3 cli.py developer-prompt {project.name} .pipeline/tasks/{spec_path.name}"},
                     indent=2))
    # The pick executes the head the planner marked. If the plan has not been
    # reviewed today, say so on stderr (the JSON above stays clean for agents) —
    # step 2 of the run is the fix, and this is a nudge, never a block.
    from team import research as research_mod
    if not research_mod.reviewed_today(state):
        print(f"note: {research_mod.plan_review_line(project)}", file=sys.stderr)
    # stdout above is a machine payload (the orchestrator parses it) — but the
    # run should also hand the owner the page. stderr, so the JSON stays clean.
    print_page(project, "the queue this pick came from", file=sys.stderr)
    return 0


def cmd_tree(args: argparse.Namespace) -> None:
    """Deterministic view of the goal tree: vision → milestones → leaves."""
    from team import tree as tree_mod
    project = resolve_project(args.project)
    if args.html:
        tree_mod.write(project, open_after=args.open)
        print(tree_mod.url(project))
        return
    model = tree_mod.build(project)
    print(tree_mod.render_text(model))
    tree_mod.write(project, open_after=args.open)
    print_page(project, "add --open to open it in the browser")


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
        from team import tree as tree_mod
        tree_mod.write(project)   # the page's banner clears with the review
        print_page(project, "change notice cleared")
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
        print_page(project, "the page shows the restored plan")
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
    print_page(project, "shows this change under the plan/goal it belongs to")
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
        print_page(project, "refreshed by this alignment record")
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


def cmd_plan(args: argparse.Namespace) -> int:
    """THE PLANNER — one agent, one job, run by step 2 of EVERY run.

    It looks at where the work is heading against fresh data, changes the plan
    when the plan no longer matches reality, maintains the queue (nothing vanishes
    without a verdict), and hands this run its task: the head of the queue. Its
    verdicts are advisory and always shown; the owner reviews it afterwards, never
    before. Deterministic collectors plus ONE synthesis call — and code validates
    every word of the result before anything is applied.
    """
    from team import research, doctor
    from team.core import agent_argv, role_cfg
    project = resolve_project(args.project)
    log = RunLog(project, "plan-" + today())
    doctor.run_doctor(project, log)
    # ONE agent owns the plan, and it must always be able to run. If this host has
    # no synthesis command configured, the hand-off IS the normal path: write the
    # prompt for the host's own native agent (never an error, never a failed run).
    can_spawn = bool(agent_argv(role_cfg("product_synthesis"), "x"))
    if not args.no_llm and not args.apply_result and not can_spawn:
        print("no synthesis command configured — handing the review to your own "
              "native agent (prompt written below)")
    applied = True
    try:
        if args.apply_result:
            research.research_apply_file(project, Path(args.apply_result), log)
        else:
            applied = research.research_run(project, log,
                                            use_llm=not args.no_llm and can_spawn,
                                            focus=getattr(args, "focus", None))
    except SystemExit as e:
        code = e.code
        if code in (0, None):
            return 0
        # The plan still stands — show the owner where things are, say plainly
        # that this cycle changed nothing, and let the run continue to its ask.
        print(f"plan review could not finish ({e})")
        print()
        print(research.plan_report(project, applied=False))
        print_page(project, "unchanged — the planner could not finish")
        return 2 if code == 2 else 1
    if not applied:
        print("nothing applied yet — the planner's prompt is ready for an agent:")
        print(f"  {pipeline_dir(project) / 'research-prompt.txt'}")
        print(f"then: devteam plan {project.name} --apply-result <the json it wrote>")
        print()
    print(research.plan_report(project, applied=bool(applied)))
    if not applied:
        print_page(project, "nothing applied yet — this is the plan as it stands")
        return 0
    from team import publish as publish_mod, tree as tree_mod
    tree_mod.write(project)          # the page the owner opens, this cycle's plan
    published = publish_mod.publish(project)
    print_page(project, "rewritten by this plan review", published=published)
    return 0 if (state_of(project).get("queue") or []) else 2


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
    # The plan is reviewed by one agent on every run (step 2). Show when that
    # last happened, so a skipped review is visible instead of silent.
    from team import research as research_mod
    print(research_mod.plan_review_line(project))
    if state.get("queue"):
        from team import scan as scan_mod
        # Plain: group the queue by the goal it serves, by TITLE not id — the
        # owner should not have to hold the codes in their head. Any branch counts
        # (work may serve a sub-goal deeper in the tree), not just milestones.
        # A shipped entry STAYS in the queue on purpose (removing it is the
        # planner's verdict, so no task vanishes silently) and the picker skips
        # it — but reporting it as "waiting" tells the owner work is pending that
        # is already on internal testing, so it is counted out and named.
        delivered = [q for q in state["queue"]
                     if scan_mod.already_delivered(q, state)]
        groups: dict[str, int] = {}
        for q in state["queue"]:
            if scan_mod.already_delivered(q, state):
                continue
            key = str(q.get("advances") or "").strip()
            groups[key] = groups.get(key, 0) + 1
        parts = []
        for mid, n in groups.items():
            node = goals.find_node(profile, mid) if mid else None
            what = node["title"] if node else (
                "off-goal (declared)" if mid == "none" else
                ("no goal declared" if not mid else mid))
            parts.append(f"{n} serving \"{what}\"")
        line = f"queue:      {len(state['queue']) - len(delivered)} waiting"
        if parts:
            line += " — " + "; ".join(parts)
        if delivered:
            line += (" · " + str(len(delivered)) + " already delivered (skipped "
                     "by the picker, dropped by the next plan review): "
                     + ", ".join(f"\"{q['label']}\"" for q in delivered))
        print(line)
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
    # The shipped build's release-candidate call: stated by every
    # shipped run, so it must be visible here too — and visibly answered once the
    # owner has said so (an ask nobody can see the answer to repeats).
    rel = latest_shipped_version(state)
    if rel:
        verdict = rc_verdict(state, rel)
        if verdict:
            who = "your override" if verdict.get("by") == "owner" else "the agent that made it"
            print(f"last release: v{rel} — release candidate "
                  f"{'✅ yes' if verdict.get('rc') else 'no'} ({who}) — "
                  f"{verdict.get('why') or verdict.get('note') or ''}")
        else:
            print(f"last release: v{rel} — release-candidate call not recorded yet "
                  f"(`devteam rc {project.name} --yes|--no` records the making agent's call)")
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
    # Every run starts here — so the page URL is printed on every run, even the
    # ones that change nothing (the tree is regenerated by adapt/record).
    print_page(project)


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
    if getattr(args, "remove", None):
        project = resolve_project(args.remove)
        scheduler.uninstall_launchd(project.name)
        print(f"Removed nightly schedule for {project.name} (app remains in dev-team in manual mode).")
        return
    installed = scheduler.list_installed_schedules()
    if not installed:
        print("no auto schedules registered — all projects run manually")
        return
    for s in sorted(installed, key=lambda x: (x["hour"], x["minute"])):
        print(f"  {s['hour']:02d}:{s['minute']:02d}  {s['label']}")
    print("(default trigger is MANUAL; auto is opt-in per project via "
          "`devteam setup --schedule auto [--at HH:MM]`)")


def cmd_market(args: argparse.Namespace) -> int:
    """Tavily web & market research for an app thesis, feature, or design pattern."""
    project = resolve_project(args.project)
    log = RunLog(project, "market-" + today())
    from team import tavily
    query = (args.query or "").strip()
    if not query:
        profile = profile_of(project)
        query = (profile.get("goals") or {}).get("vision", "").strip()
        if not query:
            raise SystemExit("provide a query or topic, e.g.:\n"
                             f"  devteam market {project.name} \"competitor features\"")
    report = tavily.search_market(project, query, search_depth=args.depth,
                                  max_results=args.results, log=log)
    if report.get("status") == "error":
        print(f"ERROR: {report.get('error')}", file=sys.stderr)
        return 1
    print(f"\nMarket Research: {query}")
    if report.get("answer"):
        print(f"\nSummary:\n{report['answer']}\n")
    print(f"Sources ({report.get('results_count', 0)}):")
    for r in report.get("results", [])[:5]:
        print(f" - {r.get('title')}: {r.get('url')}")
    print(f"\nSaved to .pipeline/market/report.json (folded into research signals)")
    return 0


def cmd_design(args: argparse.Namespace) -> int:
    """Synthesize or enforce docs/design.md style law for an app."""
    project = resolve_project(args.project)
    log = RunLog(project, "design-" + today())
    from team import design
    res = design.ensure_design(project, topic=args.topic or "", force=args.force, log=log)
    if res.get("status") == "exists":
        print(f"\nDesign System: already active at {res['path']}")
        print(f"Registered in .pipeline/profile.json conventions (mandatory read for developer agent).")
        print(f"Use --force to re-synthesize from fresh market research.")
    else:
        print(f"\nDesign System synthesized and enforced:")
        print(f"  Doc: {res['path']}")
        print(f"  Query: {res.get('query')}")
        print(f"  Market Sources: {res.get('sources', 0)}")
        print(f"  Registered in .pipeline/profile.json conventions.")
    return 0


def cmd_refill(args: argparse.Namespace) -> int:
    """Scan market/competitor horizon and refill the queue when active tasks are exhausted."""
    project = resolve_project(args.project)
    log = RunLog(project, "refill-" + today())
    from team import refill
    res = refill.auto_refill(project, topic=getattr(args, "topic", "") or "",
                             force=getattr(args, "force", False), log=log)
    if res.get("status") == "refilled":
        print(f"\nAutonomous Horizon Refilled:")
        print(f"  Milestone: {res.get('milestone')}")
        print(f"  Tasks Added: {res.get('tasks_added')}")
        print(f"  Active Queue Total: {res.get('total_queued')}")
        print_page(project, "horizon refilled")
        return 0
    elif res.get("status") == "not_needed":
        print("refill: queue already has actionable tasks (use --force to override)")
        return 0
    elif res.get("status") == "no_vision":
        print("refill: cannot formulate next horizon without profile.goals.vision set")
        return 1
    else:
        print(f"refill: status={res.get('status')}")
        return 1


def cmd_loop(args: argparse.Namespace) -> int:
    """Inspect and run autonomous workflow cycles back-to-back."""
    project = resolve_project(args.project)
    cycles = getattr(args, "cycles", 1) or 1
    auto_refill = not getattr(args, "no_auto_refill", False)
    log = RunLog(project, "loop-" + today())
    from team import refill, scan
    print(f"\n=== Autonomous Dev-Team Loop: {project.name} ({cycles} cycle{'s' if cycles > 1 else ''}) ===")

    for i in range(1, cycles + 1):
        print(f"\n--- Cycle {i}/{cycles} ---")
        if auto_refill and refill.needs_refill(project):
            print("Queue empty — triggering auto-refill from market & competitor horizon...")
            r = refill.auto_refill(project, log=log)
            if r.get("status") == "refilled":
                print(f"  Refilled milestone: {r.get('milestone')} (+{r.get('tasks_added')} tasks)")
            else:
                print(f"  Refill result: {r.get('status')}")

        state = state_of(project)
        seq = len(state.get("runs", [])) + 1
        task = scan.next_task(project, seq, supervised=getattr(args, "supervised", False))
        if not task:
            print("No actionable tasks in queue. Cycle halted.")
            break
        print(f"Next Task: [{task.get('id')}] {task.get('surface', {}).get('label')}")
        print(f"  Type: {task.get('type')} · Effort: {task.get('effort_min', 150)} min · Priority: {task.get('priority', '-')}")
        print(f"  Targets: {task.get('surface', {}).get('targets')}")
        print(f"  Worktree: dt/{task.get('id')}")

    return 0


def cmd_research(args: argparse.Namespace) -> int:
    """The planner's ENGINE, raw: collectors + one synthesis, or --apply-result.

    `devteam plan` is the same work with the owner-facing review block and the
    handover (the queue head). Use this one when you want the JSON back.
    """
    project = resolve_project(args.project)
    log = RunLog(project, "research-" + today())
    from team import research, doctor
    doctor.run_doctor(project, log)
    try:
        if args.apply_result:
            research.research_apply_file(project, Path(args.apply_result), log)
        else:
            research.research_run(project, log, use_llm=not args.no_llm,
                                  focus=getattr(args, "focus", None))
    except SystemExit as e:
        if e.code in (0, None):
            return 0
        print(str(e))
        return 2
    s = state_of(project)
    print(json.dumps({"queue": s.get("queue", []),
                      "insights": s.get("insights", {})}, indent=2))
    # stdout above is a machine payload (agents parse it) — the plan lines go to
    # stderr. Research refilled the queue, so the shared plan changed too.
    from team import publish as publish_mod
    published = publish_mod.publish(project)
    print_page(project, "refilled by this research", file=sys.stderr,
               published=published)
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
    if args.dry_run:
        print_page(project, "dry run — nothing written")
    else:
        # adapt is step 0 of every run, so even a run that does nothing else
        # leaves the published plan current.
        from team import publish as publish_mod
        published = publish_mod.publish(project)
        print_page(project, "regenerated now", published=published)
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """Commit the plan mirror (docs/plan.md + docs/plan.html) and push it.

    Every run's bookkeeping (`record`, `research`, `adapt`) does this by itself;
    the command exists so the owner can put the current plan on the repo without
    waiting for a run. Best-effort by design — see team/publish.py."""
    from team import publish as publish_mod, tree as tree_mod
    project = resolve_project(args.project)
    tree_mod.write(project)            # the mirror must match the tree we publish
    res = publish_mod.publish(project)
    print(publish_mod.line(project, res))
    print_page(project, "the plan this publish committed", published=res)
    return 0 if res.get("pushed") else 1


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
    print_page(project, "the goal is at the top")
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
        print_page(project)
        return 1
    print_page(project)
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
        scheduler.uninstall_launchd(project.name)
        print(f"MANUAL mode installed for {project.name}: nothing scheduled (not part of nightly workflow).")
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


def _shipped_tree_or_none(project: Path):
    """The origin/main worktree, for audits that must judge what is SHIPPED.

    The owner's checkout can lag origin/main or carry unrelated WIP; auditing it
    reports a blocker the released app does not have. Falls back to the checkout
    (returning None) when the worktree cannot be refreshed, e.g. offline.
    """
    try:
        from team import scan
        return scan.origin_tree(project)
    except Exception:
        return None


def cmd_store_readiness(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    from team.store_readiness import (run_store_readiness_audit, format_readiness_report,
                                      generate_store_readiness_pack)
    if getattr(args, "generate", False):
        res = generate_store_readiness_pack(project)
        print(f"STORE-READINESS GENERATE: {res.get('summary', 'done')}")
        for item in res.get("generated", []):
            print(f"  + {item}")
        for req in res.get("orchestrator_requests", []):
            print(f"  * ORCHESTRATOR REQUEST ({req.get('tool')}): {req.get('action')}")
        print()
    if getattr(args, "score_assets", False):
        from team.asset_critic import review_store_assets, format_critic_report
        crit_rep = review_store_assets(project)
        print(format_critic_report(crit_rep))
        print()
    checks = run_store_readiness_audit(project, tree=_shipped_tree_or_none(project))
    report_text, is_ready = format_readiness_report(project, checks)
    print(report_text)
    return 0 if is_ready else 1


def cmd_store_assets(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    if getattr(args, "generate", False):
        from team.marketing_asset_generator import generate_all_marketing_templates, render_storefront_assets
        print(f"~ synthesizing marketing storefront templates for {project.name}")
        generate_all_marketing_templates(project)
        print(f"~ rendering marketing storefront PNGs via headless Chrome")
        res = render_storefront_assets(project, log=print)
        print(f"  rendered {res.get('count', 0)} visual assets")
    elif getattr(args, "render_only", False):
        from team.marketing_asset_generator import render_storefront_assets
        print(f"~ rendering marketing storefront PNGs via headless Chrome")
        res = render_storefront_assets(project, log=print)
        print(f"  rendered {res.get('count', 0)} visual assets")

    from team.asset_critic import review_store_assets, format_critic_report
    report = review_store_assets(project)
    print(format_critic_report(report))
    return 0 if report.get("score", 0) >= 80 else 1


def cmd_stitch_prompt(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    from team.design import compile_stitch_screen_prompt
    screen_type = getattr(args, "screen", "main")
    prompt = compile_stitch_screen_prompt(project, screen_type=screen_type)
    print(f"STITCH MCP SCREEN PROMPT ({project.name.upper()} • {screen_type.upper()}):")
    print("=" * 65)
    print(prompt)
    print("=" * 65)
    return 0


def cmd_stitch_curate(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    from team.design import curate_stitch_manifest
    screens = []
    if getattr(args, "screens_json", None) and Path(args.screens_json).is_file():
        import json
        raw = json.loads(Path(args.screens_json).read_text())
        screens = raw.get("screens", raw if isinstance(raw, list) else [])
    else:
        # Fall back to saved metadata JSON files under projects/*/screens/
        devteam_dir = Path(__file__).resolve().parent
        p_name = project.name.lower()
        profile = profile_of(project)
        stitch_pid = profile.get("design", {}).get("stitch_project_id", "")
        for pdir in sorted((devteam_dir / "projects").glob("*/screens/*.json")):
            try:
                import json
                meta = json.loads(pdir.read_text())
                pid = pdir.parent.parent.name
                t_low = meta.get("title", "").lower()
                # If project explicitly declares stitch_project_id, match it
                if stitch_pid and pid == stitch_pid:
                    screens.append(meta)
                elif not stitch_pid:
                    # Filter by project domain/name to avoid cross-project contamination
                    if "pdf" in p_name and ("pdf" in t_low or "compress" in t_low or "obsidian" in t_low or "split" in t_low or "merge" in t_low):
                        screens.append(meta)
                    elif "mindful" in p_name and ("mindful" in t_low or "atmosphere" in t_low or "pause" in t_low or "wellbeing" in t_low):
                        screens.append(meta)
                    elif any(k in p_name for k in ("score", "pickle", "sport")) and any(k in t_low for k in ("pickleball", "scoreboard", "court")):
                        screens.append(meta)
            except Exception:
                continue

    manifest = curate_stitch_manifest(project, screens)
    print("STITCH CANVAS CURATION MANIFEST")
    print("=" * 60)
    print(f"PROJECT: {manifest.get('project')}")
    print(f"CANONICAL SCREENS ({manifest.get('screen_count')}):")
    for s in manifest.get("canonical_screens", []):
        print(f"  ✓ {s.get('id')[:12]}: {s.get('title')}")
    print(f"\nCANDIDATES FOR CLEANUP / OBSOLETE ({manifest.get('stale_count')}):")
    for s in manifest.get("stale_screens", []):
        print(f"  ✗ {s.get('id')[:12]}: {s.get('title')}")
    print("=" * 60)
    print(f"Saved canonical manifest to: {project}/.pipeline/stitch_manifest.json")
    return 0


def cmd_validate_design(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    from team.design import compile_stitch_screen_prompt
    from team.design_validator import audit_design_drift, format_drift_report
    screen_type = getattr(args, "screen", "main")
    prompt = compile_stitch_screen_prompt(project, screen_type=screen_type)

    content = ""
    target_file = getattr(args, "file", None)
    if target_file and Path(target_file).is_file():
        content = Path(target_file).read_text(errors="ignore")
    else:
        t_dir = project / ".pipeline/storefront_templates"
        for candidate in (t_dir / f"{screen_type}.html", t_dir / "01_hero.html", project / "docs/design.md"):
            if candidate.is_file():
                content = candidate.read_text(errors="ignore")
                break

    report = audit_design_drift(project, prompt_text=prompt, generated_content=content, screen_name=screen_type)
    print(format_drift_report(report))
    return 0 if report.get("verdict") in ("PASS", "WARNING") else 1


def cmd_console(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    from team.console import generate_console_setup_plan, generate_playwright_script
    if getattr(args, "script", False):
        print(generate_playwright_script(project))
        return 0

    plan = generate_console_setup_plan(project)
    print(f"GOOGLE PLAY CONSOLE SETUP PLAN: {plan.get('project')}")
    print("=" * 60)
    print("\n[STORE LISTING PAYLOAD]")
    sl = plan.get("store_listing", {})
    print(f"  Title:              {sl.get('title') or '(not set)'}")
    print(f"  Short description:  {sl.get('short_description') or '(not set)'}")
    print(f"  Full description:   {sl.get('full_description_length', 0)} chars")
    print(f"  Icon (512x512):     {'✓ Present' if sl.get('icon_present') else '✗ Missing'}")
    print(f"  Banner (1024x500):  {'✓ Present' if sl.get('feature_graphic_present') else '✗ Missing'}")
    print(f"  Screenshots:        {sl.get('screenshots_count', 0)} present")

    print("\n[MANDATORY APP CONTENT DECLARATIONS]")
    for dec in plan.get("declarations", []):
        mark = "✓" if dec.get("status") == "ready" else "!"
        print(f"  {mark} {dec.get('id'):<22}: {dec.get('value')}")

    print("\n[CLOSED TESTING REQUIREMENT]")
    ct = plan.get("closed_testing", {})
    print(f"  Track:        {ct.get('track')}")
    print(f"  Rule:         {ct.get('requirement')}")
    print(f"  Google Group: {ct.get('group_email')}")

    print("\n[AUTHENTICATION & OWNER INTERVENTION]")
    for act in plan.get("owner_actions", []):
        print(f"  {act.get('type')}: {act.get('title')}")
        print(f"    Trigger:     {act.get('trigger')}")
        print(f"    Instruction: {act.get('instruction')}")
    print("=" * 60)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="devteam")
    sub = ap.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("onboard");     o.add_argument("project"); o.set_defaults(fn=cmd_onboard)
    s = sub.add_parser("status");      s.add_argument("project"); s.add_argument("--skip-gradle", action="store_true"); s.set_defaults(fn=cmd_status)
    sc = sub.add_parser("scan");       sc.add_argument("project"); sc.add_argument("--skip-gradle", action="store_true"); sc.set_defaults(fn=cmd_scan)
    pk = sub.add_parser("pick"); pk.add_argument("project"); pk.add_argument("--supervised", action="store_true"); pk.add_argument("--auto-refill", action="store_true", help="synthesize next horizon and refill queue if empty"); pk.set_defaults(fn=cmd_pick)
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
    v = sub.add_parser("verify");      v.add_argument("project"); v.add_argument("--worktree"); v.add_argument("--task-spec"); v.add_argument("--skip-gradle", action="store_true"); v.set_defaults(fn=cmd_verify)
    em = sub.add_parser("emulators");  em.add_argument("project"); em.add_argument("--record", action="store_true"); em.add_argument("--reset", action="store_true"); em.set_defaults(fn=cmd_emulators)
    r = sub.add_parser("release");     r.add_argument("project"); r.add_argument("--worktree", required=True); r.add_argument("--task-spec", required=True); r.set_defaults(fn=cmd_release)
    rec = sub.add_parser("record"); rec.add_argument("project")
    rec.add_argument("--task"); rec.add_argument("--task-spec"); rec.add_argument("--result", required=True,
              choices=["shipped", "failed", "blocked"])
    rec.add_argument("--note"); rec.add_argument("--version"); rec.add_argument("--commit")
    rec.set_defaults(fn=cmd_record)
    rcp = sub.add_parser("rc", help="record the release-candidate call for a shipped "
                                    "build — the LLM that made the change decides it")
    rcp.add_argument("project")
    rcp.add_argument("--version", help="defaults to the last shipped version")
    g = rcp.add_mutually_exclusive_group(required=True)
    g.add_argument("--yes", action="store_true", help="this build IS a release candidate")
    g.add_argument("--no", action="store_true", help="this build is NOT a release candidate")
    rcp.add_argument("--note", help="the agent's one line of why")
    rcp.add_argument("--owner", action="store_true",
                     help="mark this as the owner's own call rather than the agent's")
    rcp.set_defaults(fn=cmd_rc)
    po = sub.add_parser("poll");       po.add_argument("project"); po.add_argument("--tail", type=int, default=60); po.set_defaults(fn=cmd_poll)
    a = sub.add_parser("alarm");       a.add_argument("project"); a.set_defaults(fn=cmd_alarm)
    rs = sub.add_parser("research");   rs.add_argument("project"); rs.add_argument("--no-llm", action="store_true")
    rs.add_argument("--apply-result", help="synthesis JSON produced by the host tool's native agent; applies through the same validation + goal gate")
    rs.add_argument("--focus", help="the owner's scoped review request for this cycle (e.g. 'review the existing UX'); injected into the prompt as a labelled block")
    rs.set_defaults(fn=cmd_research)
    m = sub.add_parser("market", help="run Tavily web & market research for an app thesis, feature, or design pattern")
    m.add_argument("project")
    m.add_argument("query", nargs="?", default="", help="topic, question or feature to research (defaults to project vision)")
    m.add_argument("--depth", choices=["basic", "advanced"], default="advanced")
    m.add_argument("--results", type=int, default=5)
    m.set_defaults(fn=cmd_market)
    rf = sub.add_parser("refill", help="scan market/competitor horizon and refill queue with next milestone tasks")
    rf.add_argument("project")
    rf.add_argument("--topic", help="optional topic or focus area (defaults to project vision)")
    rf.add_argument("--force", action="store_true", help="refill even if queue still has pending tasks")
    rf.set_defaults(fn=cmd_refill)
    lp = sub.add_parser("loop", help="autonomous multi-cycle runner with auto-refill")
    lp.add_argument("project")
    lp.add_argument("--cycles", type=int, default=1, help="number of cycles to execute (default: 1)")
    lp.add_argument("--supervised", action="store_true", help="allow picking supervised tasks")
    lp.add_argument("--no-auto-refill", action="store_true", help="do not auto-refill when queue empties")
    lp.set_defaults(fn=cmd_loop)
    des = sub.add_parser("design", help="synthesize or enforce docs/design.md style law for an app")
    des.add_argument("project")
    des.add_argument("--topic", help="focus topic or aesthetic thesis (defaults to app vision)")
    des.add_argument("--force", action="store_true", help="overwrite existing docs/design.md with fresh synthesis")
    des.set_defaults(fn=cmd_design)
    pl = sub.add_parser("plan", help="THE PLANNER — step 2 of every run: review the plan "
                                     "against fresh data, maintain the tree and the queue, "
                                     "and hand this run its task (the queue head)")
    pl.add_argument("project")
    pl.add_argument("--no-llm", action="store_true",
                    help="stop after collection and write the prompt for the host "
                         "tool's own agent (then: --apply-result <json>)")
    pl.add_argument("--apply-result", help="planner JSON produced by the host tool's native agent")
    pl.add_argument("--focus", help="the owner's scoped review request for this cycle (e.g. 'review the existing UX'); injected into the prompt as a labelled block, so a focused review needs no hand-edited prompt")
    pl.set_defaults(fn=cmd_plan)
    do = sub.add_parser("doctor");     do.add_argument("project"); do.set_defaults(fn=cmd_doctor)
    ad = sub.add_parser("adapt");      ad.add_argument("project")
    ad.add_argument("--dry-run", action="store_true",
                    help="report what would change without touching anything")
    ad.set_defaults(fn=cmd_adapt)
    pb = sub.add_parser("publish", add_help=True,
                        help="commit docs/plan.md + docs/plan.html and push them "
                             "(every run's bookkeeping does this automatically)")
    pb.add_argument("project")
    pb.set_defaults(fn=cmd_publish)
    vi = sub.add_parser("vision");     vi.add_argument("project")
    vi.add_argument("text", nargs="?", help="the owner's long-term goal, one sentence")
    vi.add_argument("--why", help="why the goal changed (optional)")
    vi.set_defaults(fn=cmd_vision)
    st = sub.add_parser("setup");  st.add_argument("--project", required=True)
    st.add_argument("--schedule", choices=["manual", "auto"], default="manual")
    st.add_argument("--at", default=None); st.add_argument("--strict", action="store_true"); st.set_defaults(fn=cmd_setup)
    sch = sub.add_parser("schedules", help="list or manage registered nightly schedules")
    sch.add_argument("--remove", help="remove a project's nightly schedule, keeping it in manual mode")
    sch.set_defaults(fn=cmd_schedules)
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
    sr = sub.add_parser("store-readiness", help="audit project for Google Play Store launch readiness")
    sr.add_argument("project")
    sr.add_argument("--generate", action="store_true", help="generate missing storefront listing files, hosted privacy HTML, and starter assets")
    sr.add_argument("--score-assets", action="store_true", help="score visual storefront assets against ASO quality criteria")
    sr.set_defaults(fn=cmd_store_readiness)
    sa = sub.add_parser("store-assets", help="audit, generate, and score Google Play visual storefront assets (icon, feature graphic, screenshots)")
    sa.add_argument("project")
    sa.add_argument("--generate", action="store_true", help="synthesize marketing HTML templates and render PNG assets to distribution/listing/en-US/images")
    sa.add_argument("--render-only", action="store_true", help="render existing HTML templates to PNGs without regenerating templates")
    sa.set_defaults(fn=cmd_store_assets)
    cs = sub.add_parser("console", help="plan or drive Google Play Console setup")
    cs.add_argument("project")
    cs.add_argument("--script", action="store_true", help="output automated Playwright JS script")
    cs.set_defaults(fn=cmd_console)
    stp = sub.add_parser("stitch-prompt", help="compile specific, pre-locked, anti-generic screen prompt for Stitch MCP")
    stp.add_argument("project")
    stp.add_argument("--screen", default="main", help="screen type: main, scoreboard, summary, setup")
    stp.set_defaults(fn=cmd_stitch_prompt)
    scur = sub.add_parser("stitch-curate", help="inventory and curate Stitch canvas screens into canonical manifest vs stale candidate screens")
    scur.add_argument("project")
    scur.add_argument("--screens-json", help="path to raw Stitch list_screens JSON dump")
    scur.set_defaults(fn=cmd_stitch_curate)
    vd = sub.add_parser("validate-design", help="audit Stitch-generated screens for drift against locked prompt specifications")
    vd.add_argument("project")
    vd.add_argument("--screen", default="scoreboard", help="screen type to audit (scoreboard, summary, setup)")
    vd.add_argument("--file", help="path to generated screen HTML, DOM, or text file")
    vd.set_defaults(fn=cmd_validate_design)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
