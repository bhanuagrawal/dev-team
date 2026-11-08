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
import os
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
    candidates = [p, project / spec, TEAM_ROOT / spec]
    # A bare task id is the shape every command PRINTS (`--task-id <id>`), while
    # the spec itself lives at <project>/.pipeline/tasks/<id>.json. Resolve that
    # documented pairing instead of killing the command on its own output
    # (observed live on `worktree-start`: the tree was prepared, then the probe
    # below raised SystemExit and the run died before printing WORKTREE_PATH).
    if "/" not in spec and "\\" not in spec:
        name = spec if spec.endswith(".json") else spec + ".json"
        candidates.append(project / ".pipeline" / "tasks" / name)
    for cand in candidates:
        if cand.exists():
            return cand
    tried = ", ".join(str(c) for c in candidates)
    raise SystemExit(f"task spec not found: {spec} (tried {tried})")


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
                if re.match(r"(Entrance|Settings|Animated|Press|Step|Icon|Badge|Stat|Card|Sheet|Chip|Pill|Bar|Glow)", name) or \
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
def _refreshed_play_store_flags(project: Path, flags: list) -> list:
    """Re-read the Play-readiness flags against the tree as it is NOW.

    The upgrade list is a snapshot taken at plan time, but `record` prints it
    after the run's change has landed — so a run that resolves the very blocker
    the snapshot named would otherwise hand the owner the same chore back, one
    minute after fixing it (seen 2026-11-02: a shipped QUERY_ALL_PACKAGES removal
    still told the owner to "resolve the Google Play Store readiness blockers").
    An upgrade is a hypothesis; before it is relayed as work, the cheap
    deterministic collector is re-run and the play-store flags are recomputed.
    Best-effort by design: an audit that errors leaves the snapshot untouched,
    because a broken audit must never erase an owner-facing flag.
    """
    from team import asks as asks_mod
    flags = asks_mod.resolve_collector_flags(Path(project), flags)
    # `resolve_collector_flags` has already re-read the Play collector, so the
    # list is current whether or not a play-store flag survived it. Returning
    # None on the play-store branch dropped the ENTIRE agent-flags block exactly
    # when the owner most needed it (2026-11-08: a shipped run printed nothing
    # while three flags were open).
    return flags


def _print_ask_review(title: str, how: str, profile: dict, tools: dict) -> str:
    """The deterministic tool verdict for ONE ask, on one indented line.

    Printed under every owner ask and every agent flag: an implementation chore
    dressed as an owner ask must never reach the owner as a chore. Returns the
    verdict so the block can count how many are actually agent work.
    """
    from team import asks as asks_mod

    row = asks_mod.review(title, how, profile=profile, tools=tools)
    if row["verdict"] == "owner":
        print(f"    agent-tool: none — {row['why']}")
        return "owner"
    where = row["tool_label"]
    state = ("available" if row["available"] else row["evidence"])
    print(f"    agent-tool: {where} CAN do this ({state})")
    if row["steps"]:
        import textwrap
        plan = " → ".join(row["steps"])
        print("      plan: " + "\n            ".join(
            textwrap.wrap(plan, width=100)))
    if row["owner_input"]:
        for note in row["owner_input"]:
            print(f"      owner supplies: {note}")
    else:
        print("      owner supplies: nothing — the agent can do this end to end")
    return row["verdict"]


def _print_ask_review_tail(verdicts: list[str]) -> None:
    agentish = sum(1 for v in verdicts if v in ("agent", "mixed"))
    if agentish:
        print(f"  ({agentish} of these are AGENT WORK, not owner chores — "
              "do them; `devteam asks <project>` prints the tool plan)")


def _project_upgrades(project: Path) -> list[dict]:
    """The agent flags this project currently carries — state first, then the
    research file, with the store-readiness ones refreshed from live checks so a
    banner never quotes a blocker the project already cleared."""
    state = state_of(project)
    flags = state.get("upgrades", [])
    if not flags:
        try:
            flags = json.loads((Path(project) / ".pipeline" / "research.json")
                               .read_text()).get("upgrades", [])
        except Exception:
            flags = []
    return _refreshed_play_store_flags(Path(project), flags)


def print_agent_flags(project: Path) -> None:
    flags = _project_upgrades(project)
    if not flags:
        return
    from team import asks as asks_mod
    profile, tools = profile_of(project), asks_mod.probe_tools()
    print("--- AGENT FLAGS (upgrades surfaced by research — quote these in the run banner) ---")
    verdicts = []
    for u in flags:
        print(f"  · {u.get('title', '?')} — {u.get('benefit', '?')}")
        print(f"    how: {u.get('how', '?')}")
        verdicts.append(_print_ask_review(u.get("title", ""), u.get("how", ""),
                                          profile, tools))
    _print_ask_review_tail(verdicts)


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
    from team import asks as asks_mod
    profile, tools = profile_of(project), asks_mod.probe_tools()
    print("--- OWNER ASKS (only the owner can answer these — "
          "quote this block in the run banner) ---")
    verdicts = []
    for a in asks:
        print(f"  ? {a['what']}")
        if a.get("how"):
            print(f"    how: {a['how']}")
        verdicts.append(_print_ask_review(a["what"], a.get("how") or "", profile, tools))
    _print_ask_review_tail(verdicts)


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


def _handover_text(project: Path, task: dict, version: str, commit: str = "") -> str:
    """The owner-facing handover for a shipped build — one block, two audiences kept apart.

    THE SPLIT MATTERS. `what's new` is the ONLY part a user sees: it is public, it is
    length-capped by the store (`whatsnew_max_chars`), and it must never carry internal
    vocabulary (file paths, "release candidate", pipeline labels). The checklist and the
    release-candidate call are for the OWNER and are not shipped anywhere, so they are
    not squeezed into that cap — a store note that tried to be a test plan would either
    overflow the limit or leak internals to users (both have already happened here).

    The CALL IS NOT COMPUTED HERE (owner directive 2026-10-24: *"let llm decide this,
    ask this to llm only which made the changes, that's it, no eval nothing"*): when no
    call is recorded the block asks the question and prints the one command that records
    the answer. Nothing acts on the verdict — promotion stays the owner's manual step.
    """
    profile = profile_of(project)
    state = state_of(project)
    out: list[str] = []

    def line(text: str = "") -> None:
        out.append(text)

    line(f"--- RELEASE HANDOVER (v{version} → internal testing — quote this block) ---")
    wf_rel = profile.get("whatsnew_file")
    if wf_rel:
        cap = shipper.whatsnew_max_chars(profile)
        note = shipper.whatsnew_read(project, commit).rstrip("\n")
        if profile.get("whatsnew_curated"):
            source = (f"owner-curated in {wf_rel} — the pipeline reads it and only "
                      "writes a per-release `whatsnew` the task spec supplies")
        elif isinstance(task, dict) and (task.get("whatsnew") or "").strip():
            source = f"written by the orchestrator into {wf_rel}"
        else:
            source = f"pipeline-generated into {wf_rel}"
        line(f"  STORE NOTE — public, {len(note) if note else 0}/{cap} chars ({source}).")
        line("  This is the only part users see; keep it user-facing.")
        if not note:
            line(f"    NO note found at {wf_rel} — the store would show nothing for this release")
        else:
            for ln in note.splitlines():
                line(f"    {ln}")
            prev = _prev_shipped_entry(state, version)
            if profile.get("whatsnew_curated") and prev:
                prev_note = (shipper.whatsnew_read(project, prev.get("commit") or "")
                             or "").rstrip("\n")
                if prev_note and prev_note == note:
                    line(f"    ⚠ STALE: identical to v{prev.get('version')}'s note — this is the "
                         "defect that once let dozens of releases share one note. `verify` and "
                         "`release` now REFUSE a repeated note, so a new build cannot ship one: "
                         "put this release's line in the task spec's `whatsnew` (or edit "
                         f"{wf_rel}) before releasing.")

    areas = shipper.whatsnew_test_areas(task)
    claim = ((task.get("surface", {}) or {}).get("label") or "").strip()
    if not claim:
        claim = re.split(r"(?<=[.!?])\s", (task.get("reason") or "").strip())[0]
    if len(claim) > 200:
        claim = claim[:200].rsplit(" ", 1)[0].rstrip(" ,;") + " …"
    line("  YOUR CHECKLIST — owner only, never shipped, not bound by the store's cap.")
    if claim:
        line(f"    confirm: {claim}")
    # A checklist that tells the owner to "walk" a Gradle file is noise. If every
    # changed path is test/build plumbing, no user-facing surface moved and the
    # honest instruction is to say so.
    plumbing = ("test", "build.gradle", "libs.versions.toml", "gradle.properties",
                "screenshottest", "androidunittest", "androidinstrumentedtest",
                ".md", ".json", ".toml", "docs/", ".yml", ".yaml")
    user_areas = [a for a in areas if not any(k in a.lower() for k in plumbing)]
    if user_areas:
        line("    walk: " + ", ".join(user_areas))
    elif areas:
        line("    no user-facing surface changed (tests/build plumbing only) — "
             "nothing new to walk by hand")
    if user_areas:
        line(f"    how: update the app from internal testing (v{version}) and walk the surfaces "
             "above; a defect you find becomes the next cycle's queue head.")
    else:
        line(f"    how: install v{version} from internal testing only if you want a smoke test; "
             "nothing in this build is supposed to look different to a user.")
    if not areas and not claim:
        line("    (no targets or claim recorded for this run yet — if this build's run is "
             f"not recorded, run `devteam record <project> --result shipped --version "
             f"{version} --task-spec .pipeline/tasks/<id>.json` first, then reprint with "
             f"`devteam rc <project> --version {version} --yes|--no --note \"...\"`; the "
             "checklist then names the change that actually shipped)")

    verdict = rc_verdict(state, version)
    if verdict:
        who = "your override" if verdict.get("by") == "owner" else "the agent that made the change"
        line(f"  RELEASE CANDIDATE?  {'YES ✅' if verdict.get('rc') else 'NO'} — "
             f"{verdict.get('why') or verdict.get('note') or ''}  [{who}]")
    else:
        line("  RELEASE CANDIDATE?  NOT CALLED YET — the LLM that made the change decides.")
        line('    ask it: "you made these changes — does this build stand on its own in front '
             'of users, yes or no?"')
        line(f"    then: python3 cli.py rc {project.name} --version {version} "
             '--yes|--no --note "<its one line>"')
        line("    (recording the call reprints this handover with the verdict filled in)")
    return "\n".join(out)


def _task_for_version(state: dict, project: Path, version: str) -> dict:
    """The task spec behind a shipped version, so the handover can name its claim.

    The ledger's `runs[].version → task_id` is the ONLY honest link between a
    version and the change that shipped it, so an unmatched version returns {}
    rather than borrowing the newest run's spec: on 2026-11-04 `rc` was called for
    a build whose run had not been recorded yet, the fallback picked the previous
    release's task, and the owner's checklist asked them to "confirm" a feature
    that build did not contain. A missing spec degrades the handover to an
    explicit instruction; it never aborts the command that was asked to print it.
    """
    def _load(task_id: str) -> dict:
        for cand in (Path(".pipeline/tasks") / f"{task_id}.json",
                     project / ".pipeline/tasks" / f"{task_id}.json"):
            try:
                if cand.is_file():
                    return json.loads(cand.read_text())
            except Exception:
                continue
        return {}

    for run in reversed(state.get("runs", [])):
        if str(run.get("version") or "") == version and run.get("task_id"):
            task = _load(run["task_id"])
            if task:
                return task
    return {}


def print_release_handover(project: Path, task: dict, version: str,
                           commit: str = "") -> None:
    """Print the handover and keep a copy beside the run log.

    The chat message is ephemeral; a handover a week later is what the owner opens
    before deciding whether to promote. Same text, so it cannot say two things.
    """
    text = _handover_text(project, task, version, commit)
    print(text)
    try:
        out = project / ".pipeline/runs" / f"{today()}-handover-v{version}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n")
    except Exception:
        pass


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
    _print_report_url(project)
    verdict = "a RELEASE CANDIDATE (✅)" if entry["rc"] else "NOT a release candidate"
    print(f"recorded ({by}): v{version} is {verdict}"
          + (f" — {entry['note']}" if entry["note"] else ""))
    # Reprint the COMPLETE handover: the block `record` printed said "NOT CALLED YET",
    # and the owner must never be handed a stale verdict beside a fresh one.
    print()
    task = _task_for_version(state, project, version)
    if not task:
        print("note: no recorded run names this version yet, so the checklist below cannot "
              "name the change that shipped. Record the run first (`devteam record <project> "
              f"--result shipped --version {version} --task-spec .pipeline/tasks/<id>.json`) "
              "and call rc again for the full handover.")
    print_release_handover(project, task, version)
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
      shared plan: https://<owner>.github.io/<repo>/goals.html  (the hosted copy)
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
        why = str(res.get("why") or "unknown reason")
        lines.append(f"shared plan: pushed, not live yet — {why}"
                     if res.get("pushed")
                     else f"shared plan: NOT pushed — {why}")
    else:
        shared, why = publish_mod.published_url(project)
        lines.append(f"shared plan: {shared}" if shared
                     else f"shared plan: not published yet — {why}")
    print("\n".join(lines), file=file or sys.stdout)


# --------------------------------------------------------------------------- #
# orchestrator-facing deterministic commands
# --------------------------------------------------------------------------- #

def _print_report_url(project: Path) -> None:
    """Name the report page after a ledger change — the page itself was already
    rebuilt by `save_state`, which every ledger writer goes through.

    Never fatal: the page is a view of the ledger, and a view that cannot name its
    own URL must not fail the thing it describes.
    """
    try:
        port = 8788
        urls = _site_urls(port)
        tail = next((u for u in urls if "ts.net" in u), urls[0] if urls else "")
        print(f"report:     {tail}   ({project.name} updated)")
    except Exception:
        pass


def cmd_site(args: argparse.Namespace) -> int:
    """Build / serve the local report page (what shipped, what to test).

    Tailnet only: the page names commit hashes, file paths and unreleased work, so
    it binds a port for Tailscale to reach and never a public interface.
    """
    from team import report

    if args.status:
        st = report.status()
        if not st["built"]:
            print("site: never built — run: python3 cli.py site")
            return 1
        print(f"site: built {st['built']}")
        if st["stale"]:
            print("site: STALE — ledger changed since: " + ", ".join(st["stale"]))
            print("  fix: python3 cli.py site")
            return 1
        print("site: current")
        return 0

    if args.uninstall:
        return _site_launchd(install=False)

    res = report.build()
    print(f"site: {res['files']} page(s) for {len(res['apps'])} app(s) -> {res['dir']}")

    if args.install:
        return _site_launchd(install=True, port=args.port)

    if args.serve:
        return _site_serve(args.port)

    for target in _site_urls(args.port):
        print(f"  {target}")
    return 0


def _site_urls(port: int) -> list[str]:
    """The URLs this page answers on: loopback, the tailnet IP, and MagicDNS."""
    urls = [f"http://127.0.0.1:{port}/"]
    try:
        from team.core import run
        r = run(["tailscale", "status", "--json"], None, timeout=20)
        import json as _json
        me = _json.loads(r.stdout or "{}").get("Self") or {}
        dns = str(me.get("DNSName") or "").rstrip(".")
        ips = [str(i) for i in (me.get("TailscaleIPs") or []) if ":" not in str(i)]
        if ips:
            urls.append(f"http://{ips[0]}:{port}/")
        if dns:
            urls.append(f"http://{dns}:{port}/")
    except Exception:
        pass
    return urls


def _site_serve(port: int) -> int:
    import functools, http.server, socketserver
    from team import report
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(report.SITE_DIR))
    class Quiet(handler.func):  # type: ignore[misc]
        def log_message(self, fmt, *a):
            pass
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", port), functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(report.SITE_DIR))) as httpd:
        print(f"site: serving {report.SITE_DIR} on 0.0.0.0:{port} (tailnet only, Ctrl-C to stop)")
        for u in _site_urls(port):
            print(f"  {u}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nsite: stopped")
    return 0


def _site_launchd(install: bool, port: int = 8788) -> int:
    """A login-surviving server for the page, mirroring the nightly's plist shape."""
    import subprocess
    from pathlib import Path as _P
    label = "com.devteam.site"
    library = _P.home() / "Library" / "LaunchAgents"
    plist = library / f"{label}.plist"
    if not install:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                       capture_output=True)
        if plist.exists():
            plist.unlink()
        print("site: service uninstalled")
        return 0
    cli = _P(__file__).resolve()
    library.mkdir(parents=True, exist_ok=True)
    logs = _P.home() / "Library" / "Logs"
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/python3</string><string>{cli}</string>
    <string>site</string><string>--serve</string><string>--port</string><string>{port}</string>
  </array>
  <key>WorkingDirectory</key><string>{cli.parent}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{logs}/devteam-site.log</string>
  <key>StandardErrorPath</key><string>{logs}/devteam-site.err.log</string>
</dict></plist>"""
    plist.write_text(body)
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"site: launchd bootstrap failed: {(r.stderr or r.stdout).strip()[:200]}")
        return 1
    print(f"site: installed {label} (auto-starts at login, keeps serving on port {port})")
    for u in _site_urls(port):
        print(f"  {u}")
    return 0


def cmd_ci(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    from team import ci
    if getattr(args, "write", False):
        res = ci.write(project)
        if res["written"]:
            print(f"ci: wrote {res['workflow']} (trigger: {res['trigger']}, package: {res['package']})")
        else:
            print(f"ci: {res['workflow']} already matches the canonical template "
                  f"(trigger: {res['trigger']}, package: {res['package']})")
        for name in res["retired"]:
            print(f"  - retired .github/workflows/{name}")
        return 0
    print(ci.render(project))
    return 0


def cmd_worktree_prune(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    from team import worktrees
    res = worktrees.prune(project, apply=getattr(args, "yes", False),
                          force=getattr(args, "force", False), log=print,
                          force_live=getattr(args, "force_live", False))
    verb = "removed" if res["applied"] else "removable"
    print(f"\nWORKTREES — {len(res['removable'])} {verb} · {res['reclaimable_mb']} MB · "
          f"{len(res['kept'])} kept")
    for k in res["kept"]:
        print(f"  = {k['task']}: {k['reason']}")
    for t in res["parked"]:
        print(f"  ⏸ {t}: attempt preserved at origin (branch kept, tree dropped)")
    for f in res["failed"]:
        print(f"  ! {f['task']}: kept — {f['why']}")
    if not res["applied"] and res["removable"]:
        print("  (dry run — re-run with --yes to remove; unmerged work is parked "
              "to its branch first, never discarded)")
    print_page(project, "shows the queue", file=sys.stderr)


def _revamp_intent(*texts: str) -> bool:
    """True only when the words ASK for a rebuild, not when they mention one.

    2026-11-08: a P0 bugfix spec whose reason read "since the revamp deleted the
    screen-time code" matched the substring "revamp", so `worktree-start` wiped
    and archived the whole worktree it had just cut. A reference to a past or
    future revamp is narration; only an explicit rebuild phrase is a request.
    """
    blob = " ".join(t for t in texts if t).lower()
    blob = re.sub(r"\b(?:pre|post|non|anti)[- ]revamp\w*", " ", blob)
    blob = re.sub(r"\b(?:before|after|since|previous|last|old|current)\s+(?:the\s+)?revamp\b", " ", blob)
    return any(p in blob for p in _REVAMP_PHRASES)


# Phrases a request to rebuild actually uses. Deliberately narrow: a bare
# "revamp"/"rebuild" anywhere in prose is exactly the false positive above.
_REVAMP_PHRASES = (
    "rebuild from scratch", "rebuild from the ground up", "ground-up rebuild",
    "rebuild the app", "rebuild the ui", "rebuild the codebase", "rebuild this app",
    "revamp the app", "revamp the ui", "revamp this app", "full revamp", "ui revamp",
    "design revamp", "ground-up revamp", "complete redesign", "redesign from scratch",
    "delete the codebase", "delete codebase", "delete the ui", "delete ui",
    "purge the codebase", "purge codebase", "wipe the codebase", "start from scratch",
    "scrap the existing design", "scrap the design",
)


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

    # CODE-ENFORCED PURGE FOR REVAMP / REBUILD TASKS
    is_revamp = any(k in args.task_id.lower() for k in ("revamp", "rebuild", "ground-up"))
    req_text = ""
    try:
        spec = task_spec_path(project, args.task_id)
        if spec and spec.is_file():
            task_data = json.loads(spec.read_text())
            kind = str(task_data.get("kind") or task_data.get("type") or "").lower()
            label = str(task_data.get("label", "")).lower()
            req_text = str(task_data.get("owner_request", "") or "")
            # A bugfix is never a rebuild, whatever its prose mentions.
            is_revamp = kind != "bugfix" and (
                is_revamp
                or kind == "revamp"
                or _revamp_intent(label, req_text)
            )
    except (Exception, SystemExit):
        # This lookup is a probe for the revamp purge, never a gate: a spec
        # this command cannot find must not kill a worktree it just prepared.
        pass

    if is_revamp:
        from team import revamp
        is_ui_only = (
            any(k in req_text for k in ("ui layer", "delete ui", "delete the ui", "recreate the ui", "recreate ui"))
            and not any(k in req_text for k in ("delete codebase", "delete the codebase", "entire codebase", "full codebase", "all code"))
        )
        purged = revamp.purge_codebase(project, target_tree=wt, full_codebase=not is_ui_only, log=log)
        if purged:
            scope_str = "legacy UI source/test" if is_ui_only else "legacy source/test"
            log(f"revamp auto-purge: wiped and archived {len(purged)} {scope_str} files from worktree")
            print(f"REVAMP AUTO-PURGE: {len(purged)} {scope_str} files archived to .pipeline/archived_codebase and purged from worktree.")

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
        "full. Run tonight's pipeline: status → asks → PLAN (the planner re-reads "
        "every signal and decides priority and which entries one run clubs into "
        "one release) → pick the run it handed over → worktree-start → "
        "developer-prompt → spawn the developer as a native subagent (model from "
        "runtimes.json) → verify the worktree → release on green → record → alarm. "
        "Project: "
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
            if getattr(args, "effort_min", None) is not None:
                entry["effort_min"] = args.effort_min
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
    if action == "verdict":
        mid = str(args.id or "").strip()
        if not mid or args.met not in ("yes", "no"):
            print("verdict needs --id <milestone> --met yes|no --evidence \"…\"")
            return 2
        if goals.milestone(profile, mid) is None:
            print(f"no such milestone: {mid}")
            return 2
        verdict = "done" if args.met == "yes" else "active"
        goals.record_verdict(state, mid, verdict,
                             str(args.evidence or ""), args.by or "agent")
        save_state(project, state)
        print(f"verdict recorded: [{mid}] {'MET' if args.met == 'yes' else 'NOT MET'} — "
              f"{str(args.evidence or '')[:140]}")
        from team import tree as tree_mod
        tree_mod.write(project)
        return 0
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
        # The base the worktree was cut from, resolved UNAMBIGUOUSLY: `rev-parse
        # dt-base` resolves a TAG called dt-base in preference to the branch of the
        # same name, and a stale tag from an old run then diffs the worktree against
        # a months-old commit — so a frozen file nobody touched (app/build.gradle)
        # shows up as a violation and the run dies on a phantom. Ask for the branch
        # first; only fall back to the loose name when no such branch exists.
        base = ""
        for ref in ("refs/heads/dt-base", "dt-base^{commit}"):
            base = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref],
                                  cwd=tree, capture_output=True, text=True).stdout.strip()
            if base:
                break
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
    # IDEMPOTENT RE-RUN: a release whose CI watch outlived the caller's timeout gets
    # invoked a second time, and nothing used to notice the work was already pushed —
    # one change produced four bumps and four tags. If the worktree's tree is already
    # what origin/main holds, there is nothing to deliver.
    import subprocess as _sp
    wt_tree = _sp.run(["git", "rev-parse", "HEAD^{tree}"], cwd=worktree,
                      capture_output=True, text=True).stdout.strip()
    main_tree = _sp.run(["git", "rev-parse", "origin/" + profile_of(project)["main_branch"] + "^{tree}"],
                        cwd=project, capture_output=True, text=True).stdout.strip()
    # A HEAD tree only describes COMMITTED work. The developer (or the
    # orchestrator) may still hold the change uncommitted in the worktree, and
    # `git add -A` is what turns it into the release commit — comparing HEAD
    # alone called that "already shipped" and exited without releasing anything
    # (2026-10-31). Only a CLEAN worktree on the same tree is a no-op.
    wt_dirty = _sp.run(["git", "status", "--porcelain"], cwd=worktree,
                       capture_output=True, text=True).stdout.strip()
    if wt_tree and wt_tree == main_tree and not wt_dirty:
        print(json.dumps({"delivered": True, "already_shipped": True,
                          "version": ".".join(map(str, latest)),
                          "note": "worktree tree already equals origin/main — nothing to ship"}, indent=2))
        return 0
    # THE DECISION, BEFORE THE BUILD. The release-candidate call used to be
    # recorded only after CI had already delivered, which made it impossible to
    # put -rc in the name it was describing. It is asked here, where the agent
    # that wrote the change is holding the diff and the verify result.
    #
    # IT IS ASKED, NOT ANSWERED. Owner directive 2026-10-24: the judgement is the
    # agent's ("let llm decide this, ask this to llm only which made the changes,
    # that's it, no eval nothing"). `release` can only carry that agent's answer
    # when it is passed --rc; without it the run used to record a mechanical "NO"
    # whose note was the version's own derivation ("next patch after 1.0.38"), i.e.
    # a rule answering a question the contract reserves for a judgement. So an
    # unpassed --rc records NOTHING: the handover says NOT CALLED YET and the agent
    # records the call with `devteam rc`.
    rc_given = args.rc is not None
    is_rc = args.rc == "yes"
    task = json.loads(task_spec_path(project, args.task_spec).read_text())
    state = state_of(project)
    name, why = shipper.next_version_name(project, rc=is_rc)
    floor, floor_note = shipper.play_max_version_code(project)
    code = shipper.next_version_code(state, floor)
    log(f"versionCode floor: {floor_note}" + (f" (max {floor})" if floor else ""))
    log(f"version: {name} (code {code}) — {why}")
    if rc_given:
        log(f"release candidate: {'YES' if is_rc else 'NO'} (decided before the tag)")
    else:
        log("release candidate: NOT CALLED YET — nothing recorded; the agent that made "
            f"this change calls `devteam rc {project.name} --version {name} --yes|--no`")

    # TESTS ARE PART OF THE CHANGE, and release is the second, unavoidable
    # answer for an agent that skipped `verify`. Diff-scoped, no Gradle — it
    # reads the worktree against the same dt-base verify uses. Refuse BEFORE the
    # bump, so a testless change never becomes a tag/CI build.
    from team import code_tests
    from team import verify as gates_mod
    _base = ""
    for _ref in ("refs/heads/dt-base", "dt-base^{commit}",
                 "origin/" + profile_of(project)["main_branch"]):
        _base = subprocess.run(["git", "rev-parse", "--verify", "--quiet", _ref],
                               cwd=worktree, capture_output=True, text=True).stdout.strip()
        if _base:
            break
    if _base:
        _ok_tests, _test_msg = code_tests.gate(
            worktree, gates_mod.changed_files(worktree, _base))
        if not _ok_tests:
            log("test gate: BLOCKED — " + _test_msg)
            print(json.dumps({"delivered": False, "version": name,
                              "error": "test gate: " + _test_msg, "ci": {}},
                             indent=2))
            return 1

    # THE STORE-NOTE GATE, BEFORE THE BUMP. The note is the one part a user
    # sees; a build that would repeat the previous release's wording is refused
    # here, while the tree is still clean and the remedy (a `whatsnew` line in
    # the task spec) is one edit away. `verify` runs the same gate, so an agent
    # that skipped it gets the second, unavoidable answer here.
    ok_note, note_msg = shipper.whatsnew_gate(project, task, root=worktree, log=log)
    if not ok_note:
        log("whatsnew gate: BLOCKED — " + note_msg)
        print(json.dumps({"delivered": False, "version": name,
                          "error": "release note gate: " + note_msg, "ci": {}},
                         indent=2))
        return 1

    name = shipper.bump_version_file(worktree, name, log, floor_code=floor, code=code)
    shipper.write_whatsnew(worktree, task, log)

    # The call is recorded BEFORE the build, so the handover and the rc command
    # read one answer instead of two competing ones — and ONLY when the agent
    # actually gave one (see rc_given above). A mechanical "NO" here would put a
    # version-derivation string in the owner's handover attributed to the agent.
    if rc_given:
        state.setdefault("release_candidates", []).append({
            "version": name, "rc": is_rc,
            "note": args.note if hasattr(args, "note") and args.note else why,
            "why": args.note if hasattr(args, "note") and args.note else why,
            "date": today(), "by": "agent", "stage": "release", "code": code,
        })
    state["last_version_code"] = code
    save_state(project, state)

    res = shipper.push_and_deliver(project, worktree, task, name, log, code=code)
    print(json.dumps(res, indent=2))
    return 0 if res.get("delivered") else 1


def _refresh_coverage_signal(project: Path) -> None:
    """Re-measure coverage against the SHIPPED tree at the end of a released run.

    A release CHANGES the tree, and the stored signal is what `status`, `doctor`
    and the report page read until the next plan re-collects it. Shipping a run
    that reached 100% and then telling the owner "unit 86%, below floor" is a
    reading of work that no longer exists (seen 2026-11-07: the release added the
    test that closed the gap and every report kept the pre-release number).
    Best-effort by design: the origin tree may be unreachable offline, and a
    stale number must never fail a run.
    """
    try:
        from team import coverage as coverage_mod, scan as scan_mod
        from team.core import profile_of

        sig = coverage_mod.collect(scan_mod.origin_tree(project), profile_of(project))
        f = project / ".pipeline" / "research.json"
        data = json.loads(f.read_text())
        data.setdefault("signals", {})["coverage"] = sig
        data["coverage_refreshed_at"] = now_iso()
        f.write_text(json.dumps(data, indent=2) + "\n")
        print("coverage: " + coverage_mod.summary_line(sig))
    except Exception as e:  # never fail a record over a report line
        print(f"coverage: refresh skipped ({e})")


def cmd_record(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    state = state_of(project)
    task = json.loads(task_spec_path(project, args.task_spec).read_text()) \
        if args.task_spec else {}
    entry = {"date": today(), "task_id": args.task or task.get("id", "?"),
             "result": args.result, "note": args.note or "",
             "finished": now_iso()}
    if args.result == "shipped":
        entry["version"] = args.version or ""
    runs = state.setdefault("runs", [])
    # Recording the same delivery twice (a retried command, a repair after the
    # ledger missed it) must UPDATE the run, never append a phantom second one.
    same_run = next(
        (r for r in runs
         if r.get("task_id") == entry["task_id"]
         and str(r.get("version") or "") == str(entry.get("version") or "")
         and r.get("result") == entry["result"]),
        None,
    )
    if same_run is not None:
        same_run.update(entry)
        entry = same_run
    else:
        runs.append(entry)
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
        # One ledger row per (target, version): a later release that re-touches a
        # path must still record itself, or the delivery is invisible — the
        # queue entry cannot be recognised as delivered, `status` names the
        # previous version, and the next plan review re-queues shipped work
        # (seen 2026-11-01: v1.0.24 wrote no rows because v1.0.23 had already
        # shipped the same ui/screens + feature/summary + commonTest targets).
        already = {(s.get("target"), str(s.get("version") or ""))
                   for s in state.get("shipped", [])}
        version_key = str(args.version or "?")
        # ONE run may carry several queue entries (pick clubs a thin head with
        # same-milestone followers). Every delivered entry must land in the
        # ledger under ITS OWN label, or `already_delivered` cannot recognise it
        # and the next pick serves the same work again — the run would be paid
        # for twice.
        rows = [{"label": label, "targets": targets, "advances": adv}]
        for c in (task.get("closes") or []):
            if not isinstance(c, dict):
                continue
            c_adv = str(c.get("advances") or adv or "")
            rows.append({"label": str(c.get("label") or label),
                         "targets": [t for t in (c.get("targets") or []) if t],
                         "advances": c_adv})
        for row in rows:
            for t in row["targets"]:
                if (t, version_key) in already:
                    continue  # concurrent runs may both ship; keep one row per target per release
                state.setdefault("shipped", []).append({
                    "target": t, "label": row["label"], "version": args.version or "?",
                    "version_code": state.get("last_version_code"),
                    "commit": args.commit or "", "date": today(),
                    "task_id": args.task or task.get("id", ""),
                    "advances": row["advances"]})
        state["last_success_date"] = today()
        state["last_run"] = {**entry, "pushed_sha": args.commit or ""}
    elif args.result in ("failed", "blocked") :
        state["last_run"] = entry
    state["remaining"] = [
        {k: t[k] for k in ("label", "paths", "risk") if k in t}
        for t in scan.scan_surfaces(project) if t["status"] == "unanimated"]
    save_state(project, state)
    if args.result == "shipped":
        # The tree just changed: re-measure before anything reads the old number.
        _refresh_coverage_signal(project)
    scribe.update(project)
    print(f"recorded: {entry['result']} {entry['task_id']}")
    _print_report_url(project)
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
        # The icon chain is a state machine with a `released` step; only a shipped
        # record knows a release happened, so this is where the step advances.
        try:
            from team import brand_change

            brand_change.mark_released(project, str(args.version or ""), str(args.commit or ""))
        except Exception:
            pass
    block = goals.review_block(state_of(project))
    if block:
        print("\n" + block)
    # Every run ends by handing the owner the page: "where we are now", one paste.
    # The plan itself is published too, so what the owner opens is what the repo
    # says — see team/publish.py (best-effort: never fails a run).
    from team import publish as publish_mod
    published = publish_mod.publish(project)
    print_page(project, "refreshed by this record", published=published)
    # Housekeeping: a run's tree is a CACHE, never the record. `record` is the
    # moment a run ends, so it is the moment to reclaim the tree: merge what
    # landed, park what did not (commit + push its branch) and delete only what
    # is safe somewhere else. Applies to failed/blocked runs too — those are the
    # ones that used to leave a 230 MB tree nobody could ever delete.
    try:
        from team import worktrees
        ended = None
        if getattr(args, "task_spec", None):
            ended = Path(str(args.task_spec)).stem
        pruned = worktrees.prune(project, apply=True, ended=ended)
        if pruned["removed"]:
            print(f"worktrees: removed {len(pruned['removed'])} tree(s), "
                  f"~{pruned['reclaimable_mb']} MB reclaimed")
        if pruned["parked"]:
            print(f"worktrees: parked {', '.join(pruned['parked'])} at origin — "
                  f"the attempt survives without the tree")
        for f in pruned["failed"]:
            print(f"worktrees: kept {f['task']} — {f['why']}")
        for k in pruned["kept"]:
            print(f"worktrees: kept {k['task']} — {k['reason']}")
    except Exception as e:  # cleanup must never fail a run
        print(f"worktrees: prune skipped ({e})")


def cmd_pick(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    profile = profile_of(project)
    state = state_of(project)
    seq = len(state.get("runs", [])) + 1
    # A dead session's leftovers are reclaimed by the NEXT run, not by the owner
    # remembering (`worktree-prune` is the manual form). Safe by construction:
    # a tree is only removed once its branch is at origin or its work landed.
    # stderr, because `pick` prints machine-readable JSON on stdout.
    try:
        from team import worktrees as worktrees_mod
        swept = worktrees_mod.prune(project, apply=True)
        if swept["removed"] or swept["parked"]:
            extra = (" · parked " + ", ".join(swept["parked"])) if swept["parked"] else ""
            print(f"worktrees: reclaimed {len(swept['removed'])} tree(s) "
                  f"(~{swept['reclaimable_mb']} MB){extra}",
                  file=sys.stderr, flush=True)
    except Exception as e:  # cleanup must never block a pick
        print(f"worktrees: prune skipped ({e})", file=sys.stderr, flush=True)
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
    from team import scan
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
    run_info = task.get("run") or {"entries": 1,
                                   "effort_min": int(task.get("effort_min") or 0),
                                   "target_min": scan.RUN_TARGET_MIN}
    closes = [c.get("label") for c in (task.get("closes") or [])]
    # The run's shape is a FACT the orchestrator must relay, not a detail the
    # owner has to reconstruct from the queue: one release ships one run, so
    # "how much work is this run carrying" is the number that decides whether
    # the release paid for itself.
    thin = (run_info.get("entries", 1) == 1
            and (run_info.get("effort_min") or 0) < scan.RUN_TARGET_MIN)
    if thin:
        print(f"run: THIN — {run_info.get('effort_min')} min of executable work; a release "
              f"pays for itself at ~{scan.RUN_TARGET_MIN} min. The plan named no clubs and "
              f"no entry on the same milestone was available to carry with it.",
              file=sys.stderr, flush=True)
    else:
        print(f"run: {run_info['entries']} queue entr"
              f"{'y' if run_info['entries'] == 1 else 'ies'} · ~{run_info.get('effort_min')} min"
              + (" · clubbed: " + " | ".join(closes) if closes else ""),
              file=sys.stderr, flush=True)
    print(json.dumps({"pick": task, "spec": str(spec_path),
                      "run": {**run_info, "closes": closes, "thin": thin},
                      "worktree_cmd": f"python3 cli.py worktree-start {project.name} --task-id {task['id']}",
                      # .pipeline/tasks/ is relative to the PROJECT dir, which
                      # developer-prompt already searches — a bare filename
                      # resolves nowhere (CWD/team dir hold no tasks/).
                      "prompt_cmd": f"python3 cli.py developer-prompt {project.name} .pipeline/tasks/{spec_path.name}"},
                     indent=2))
    # The pick executes what the planner decided. Priority and batching are the
    # planner's calls (it sees every signal); this only says whether THIS run was
    # planned in this cycle, using the run number the plan was stamped with.
    from team import research as research_mod
    lv = state.get("plan_review") or {}
    if lv.get("for_run") != seq:
        print(f"note: {research_mod.plan_review_line(project)} — run {seq} was not "
              "planned in this cycle, so priority and clubbing are the previous "
              "plan's. Step 2 is `devteam plan <project>`.", file=sys.stderr)
        if thin:
            print("note: this run is THIN and unplanned — one small fix for a whole "
                  "release, with no planner decision behind it.", file=sys.stderr)
    # stdout above is a machine payload (the orchestrator parses it) — but the
    # run should also hand the owner the page. stderr, so the JSON stays clean.
    print_page(project, "the queue this pick came from", file=sys.stderr)
    return 0


def cmd_tree(args: argparse.Namespace) -> None:
    """Deterministic view of the goal tree: vision → goals → milestones → leaves."""
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
    if action == "verdict":
        mid = str(args.id or "").strip()
        if not mid or args.met not in ("yes", "no"):
            print("verdict needs --id <milestone> --met yes|no --evidence \"…\"")
            return 2
        if goals.milestone(profile, mid) is None:
            print(f"no such milestone: {mid}")
            return 2
        verdict = "done" if args.met == "yes" else "active"
        goals.record_verdict(state, mid, verdict,
                             str(args.evidence or ""), args.by or "agent")
        save_state(project, state)
        print(f"verdict recorded: [{mid}] {'MET' if args.met == 'yes' else 'NOT MET'} — "
              f"{str(args.evidence or '')[:140]}")
        from team import tree as tree_mod
        tree_mod.write(project)
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
        kind = args.kind or ("goal" if parent == "vision" else "subgoal")
        if parent == "vision" and kind != "goal":
            raise SystemExit("a branch directly under the vision is a GOAL (an end "
                             "the product must reach for users) — use --kind goal, "
                             "or add the milestone under an existing goal")
        new = {"id": args.id or _new_id(title), "kind": kind,
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
    # Shape before readiness: the middle tier is code's job, so the doctor this
    # plan runs and the plan itself judge the SAME tree (no "blocked" line for a
    # gap the next command would have closed in a millisecond).
    from team import goals as goals_mod
    goals_mod.ensure_shape(project, log)
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
                                            focus=getattr(args, "focus", None),
                                            max_effort=getattr(args, "max_effort", None))
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
    # Which tree is authoritative. A stale/diverged checkout has twice produced a
    # confident wrong answer ("we ship red" while origin shipped cyan), so the
    # distance from the delivery ref is stated on every status, not discovered later.
    from team.core import git_ref_state
    ref = git_ref_state(project)
    if ref.get("error") or not ref.get("ref_sha"):
        print(f"delivery:   {ref['ref']} unknown ({ref.get('error') or 'no ref'}) — reads fall back to the checkout")
    elif ref["stale"] or ref["ahead"]:
        kind = "DIVERGED" if ref["diverged"] else ("STALE" if ref["stale"] else "unpushed")
        print(f"delivery:   {kind} — checkout {ref['head']} is {ref['ahead']} ahead / "
              f"{ref['behind']} behind {ref['ref']} @ {ref['ref_sha']}")
        print(f"            project code is read from {ref['ref']}; "
              f"run `git -C {project} rebase origin/{profile['main_branch']}` to sync")
    else:
        print(f"delivery:   checkout matches {ref['ref']} @ {ref['ref_sha']}")
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
    # Test coverage is a plan gate now (team/research.py), so the owner and the next
    # agent must be able to see the numbers the planner is judged on. The stored
    # number is a snapshot from the LAST plan review, so a shipped run makes it
    # visibly wrong (v3.13.74 took unit 41% → 82% and status still said 41% — below
    # floor). Re-measure structurally over the delivery tree when the scan worktree
    # is exactly at origin/main (cheap: no Gradle); fall back to the snapshot when
    # it is not, rather than reporting a number from a different revision.
    from team import coverage as coverage_mod
    cov = coverage_mod.stored(project)
    scan_tree = project / ".pipeline" / "scan"
    ref_sha = ref.get("ref_sha")
    if scan_tree.is_dir() and ref_sha:
        from team.core import git
        head = git(["rev-parse", "HEAD"], cwd=scan_tree, timeout=30)
        if head.returncode == 0 and head.stdout.strip().startswith(ref_sha):
            fresh = coverage_mod.collect(scan_tree, profile)
            if fresh.get("status") == "ok":
                cov = fresh
    if cov:
        print("coverage:   " + coverage_mod.summary_line(cov).removeprefix("coverage: "))
    # What the STORE says is not visible in git or CI: a delivery can be green and
    # still rejected by Play policy (2026-11-04). One line, with its age, so the
    # owner and the next agent know whether anyone has actually looked.
    from team import store_status as store_status_mod

    print(store_status_mod.line(project))
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
    # An open brand change is a run in progress: the next agent must see which
    # step is owed instead of re-deriving the chain from a skill (owner,
    # 2026-11-01). Reads the state file, prints the single next command.
    try:
        from team import brand_change as brand_mod
        bstate = brand_mod.state(project)
        if bstate and bstate.get("state") not in (None, "published"):
            info = brand_mod.status(project)
            print(f"brand:      open at '{bstate.get('state')}' — {info.get('label')}"
                  + (f"  ⚠ {len(info['issues'])} gate issue(s)" if info.get("issues") else ""))
            print(f"  next:     {info.get('next')}")
    except Exception:
        pass
    # Trees on disk are a cache; a stopped attempt is a BRANCH. Report both, so
    # a session that died mid-run is visible to the next agent instead of living
    # only as a 230 MB directory nobody lists.
    try:
        from team import worktrees as worktrees_mod
        wsum = worktrees_mod.summary(project)
        if wsum["count"] or wsum["parked"]:
            line = f"worktrees:  {wsum['count']} dev tree(s), {wsum['mb']} MB"
            if wsum["over_cap"]:
                line += (f"  ⚠ over the cap of {worktrees_mod.TREE_CAP} — "
                         f"`devteam worktree-prune {project.name} --yes`")
            print(line)
            for p in wsum["parked"]:
                where = "at origin" if p["at_origin"] else "LOCAL ONLY"
                print(f"  ⏸ parked  {p['branch']} @ {p['sha']} ({p['age_days']}d, "
                      f"{where}) — an attempt that never shipped")
    except Exception:
        pass
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
    """Owner-visible schedule map: three runs a night for every registered app."""
    if getattr(args, "remove", None):
        project = resolve_project(args.remove)
        scheduler.uninstall_launchd(project.name)
        print(f"Removed nightly schedule for {project.name} (app remains in dev-team in manual mode).")
        return
    if getattr(args, "rebalance", False):
        jobs = scheduler.list_installed_jobs()
        before = {j["project"]: j["slots"] for j in jobs}
        plan = scheduler.rebalance([j["project"] for j in jobs if j["project"]])
        changed = [n for n, slots in plan.items() if before.get(n) != slots]
        print(f"Rebalanced {len(plan)} app(s): {scheduler.RUNS_PER_DAY} runs/night each "
              f"inside {scheduler.WINDOW_START_HOUR:02d}:00–"
              f"{scheduler.WINDOW_END_HOUR:02d}:00 "
              f"({len(changed)} schedule(s) updated).")
    jobs = scheduler.list_installed_jobs()
    if not jobs:
        print("no auto schedules registered — all projects run manually")
        return
    gap = scheduler.plan_gap_minutes(len(jobs))
    print(f"window {scheduler.WINDOW_START_HOUR:02d}:00–{scheduler.WINDOW_END_HOUR:02d}:00 · "
          f"{scheduler.RUNS_PER_DAY} runs/app/night · "
          f"{gap:.0f} min between consecutive machine-wide runs")
    if gap < scheduler.MIN_GAP_MIN:
        print(f"⚠ {len(jobs)} apps cannot all clear {scheduler.MIN_GAP_MIN} min; an "
              "overlapping run is skipped (tolerated — each app still gets its "
              "other runs that night).")
    for job in sorted(jobs, key=lambda j: (j["project"] or j["label"]).lower()):
        slots = ", ".join(f"{h:02d}:{m:02d}" for h, m in job["slots"])
        ok = (len(job["slots"]) == scheduler.RUNS_PER_DAY
              and all(scheduler.in_window(h, m) for h, m in job["slots"]))
        print(f"{'  ⚠' if not ok else '   '} {job['project'] or job['label']:<18} {slots}")
        if not ok:
            print(f"     needs {scheduler.RUNS_PER_DAY} runs inside the window — "
                  "`devteam schedules --rebalance` fixes it")
    print("(default trigger is MANUAL; auto is opt-in per project via "
          "`devteam setup --schedule auto`; re-space everything with "
          "`devteam schedules --rebalance`)")


def cmd_capabilities(args: argparse.Namespace) -> int:
    """What the team can do, and whether THIS project is wired for it.

    A capability that lands as a new command is invisible to `adapt` (which
    compares data shape), so a project can run green for months without it. This
    is the table that says so out loud.
    """
    project = resolve_project(args.project)
    from team import capabilities as cap_mod
    rows = cap_mod.review(project)
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return 0
    print(f"== {project.name} ==")
    for r in rows:
        mark = {"ok": "✓", "n/a": "·"}.get(r["status"], "!")
        print(f"  {mark} {r['id']}: {r['status']} — {r['detail']}")
        if r["status"] not in ("ok", "n/a"):
            print(f"      what: {r['what']}")
            print(f"      why:  {r['why']}")
            print(f"      fix:  see `devteam adapt {project.name}` (it writes the "
                  f"ask and any draft) "
                  f"{'— owner-approved' if r['owner_approved'] else ''}")
    if all(r["status"] in ("ok", "n/a") for r in rows):
        print("  every capability the team ships is wired for this project")
    return 0 if all(r["status"] in ("ok", "n/a") for r in rows) else 1


def cmd_coverage(args: argparse.Namespace) -> int:
    """Unit and UI test coverage over the SHIPPED tree — the numbers the planner gates on.

    Deterministic and cheap: file-level reach of the test sets, with real JaCoCo line
    coverage substituted when the project already produces a report. Reads origin/main
    (the scan worktree), never the owner's checkout.
    """
    project = resolve_project(args.project)
    from team import coverage as coverage_mod
    from team import scan as scan_mod
    from team.core import profile_of
    tree = scan_mod.origin_tree(project)
    sig = coverage_mod.collect(tree, profile_of(project))
    if getattr(args, "json", False):
        print(json.dumps(sig, indent=2))
        return 0 if not sig.get("below_floor") else 1
    print(f"== {project.name} ==")
    print("  " + coverage_mod.summary_line(sig))
    for leg in ("unit", "ui"):
        d = sig.get(leg) or {}
        if d.get("missing_examples"):
            print(f"  {leg}: not reached by any test (e.g.) " + ", ".join(d["missing_examples"]))
    if sig.get("status") != "ok":
        print(f"  status: {sig.get('status')} — {sig.get('note')}")
    return 0 if not sig.get("below_floor") else 1


def cmd_ux_plan(args: argparse.Namespace) -> int:
    """Which screens need a UX audit — decided by fingerprints, not by calendar."""
    project = resolve_project(args.project)
    from team import ux_audit, ux_video
    p = ux_audit.plan(project)
    v = ux_video.plan(project)
    if getattr(args, "json", False):
        print(json.dumps({**p, "video": v}, indent=2))
        return 0
    print(f"== {project.name} ==")
    print(ux_audit.report(p))
    if v["rows"]:
        print(ux_video.report(v))
    return 0


def cmd_ux_video(args: argparse.Namespace) -> int:
    """Record declared flows on the app and react to the motion, not just the pixels."""
    project = resolve_project(args.project)
    log = RunLog(project, "ux-video-" + today())
    from team import ux_video
    res = ux_video.run(project, only=getattr(args, "flow", None),
                       everything=getattr(args, "all", False), log=log,
                       avd=getattr(args, "avd", None),
                       keep_emulator=getattr(args, "keep_emulator", False),
                       record_only=getattr(args, "record_only", False),
                       via=getattr(args, "via", "gemini"),
                       re_record=getattr(args, "re_record", False))
    for r in res["recorded"]:
        print(f"\n== recorded {r['flow']} — {r['seconds']}s, {r['bytes'] // 1024} KB ==")
        for a in r["actions"]:
            print(f"  · {a}")
        for d in r.get("dead_taps") or []:
            print(f"  ⚠ DEAD TAP — {d['action']}: the screen did not change")
        ins = r.get("insets") or {}
        if ins:
            hits = ins.get("collisions") or []
            print(f"  insets: status bar {ins.get('status_bar_px', 0)}px, nav bar "
                  f"{ins.get('nav_bar_px', 0)}px · "
                  + (f"{len(hits)} label(s) INSIDE the status bar: "
                     + ", ".join(h["label"] for h in hits[:3]) if hits else "no content collisions"))
    for r in res["reacted"]:
        print(f"\n== {r['id']} (surface {r['surface']}) — judged from {r.get('evidence','video')}"
              f" · {r['panels']} panels every {r['sample_every_s']:g}s ==")
        for x in r.get("reactions") or []:
            print(f"  {x.get('at', ''):6} {x.get('line', '')}")
        if r.get("premium"):
            print("  premium: " + " · ".join(r["premium"][:3]))
        for g in r.get("gripes") or []:
            elem = f" [{g.get('element')}]" if g.get("element") else ""
            print(f"  {'🚨' if g['severity'] == 'P0_BLUNDER' else '⚠️ ' if g['severity'] == 'P1_DEGRADED' else '✦'} "
                  f"{g['id']} [{g['severity']}]{elem} {g['line']}")
        if r.get("verdict"):
            print(f"  verdict: {r['verdict']}")
    for s in res["skipped"]:
        print(f"~ skipped {s['id']}: {s['reason']}")
    for f in res["failed"]:
        print(f"✗ failed {f['id']}: {f['reason']}")
    if not res["recorded"] and not res["reacted"] and not res["skipped"] and not res["failed"]:
        print("nothing to record: every declared flow is current "
              "(recordings are only re-taken when their screens change)")
    print(f"\nflow(s) planned {res['planned']} · recorded {len(res['recorded'])} · "
          f"judged {len(res['reacted'])} · skipped {len(res['skipped'])} · "
          f"stored in .pipeline/ux_audit.json")
    return 1 if res["failed"] else 0


def cmd_mock_server(args: argparse.Namespace) -> int:
    """Foreground mock backend: deterministic Supabase shapes, no real project."""
    project = resolve_project(args.project)
    from team import mock_server
    return mock_server.serve(project.name, port=args.port or mock_server.DEFAULT_PORT,
                             log=lambda m: print(f"[{project.name}] {m}"))


def cmd_ux_flow(args: argparse.Namespace) -> int:
    """Goal-declared journeys, navigated by a vision model, judged as a person."""
    project = resolve_project(args.project)
    log = RunLog(project, "ux-flow-" + today())
    from team import ux_flow
    res = ux_flow.run(project, only=getattr(args, "flow", None), log=log,
                      avd=getattr(args, "avd", None),
                      keep_emulator=getattr(args, "keep_emulator", False),
                      max_steps=getattr(args, "max_steps", None),
                      worktree=(Path(args.worktree).expanduser()
                                if getattr(args, "worktree", None) else None),
                      mock=getattr(args, "mock", False),
                      mock_port=getattr(args, "mock_port", None))
    if not res["planned"]:
        print(ux_flow.status_report(project))
        return 0
    for r in res["run"]:
        mark = "✅" if r["goal_reached"] else "❌"
        print(f"\n== {r['flow']} ({r['surface'] or 'no surface'}) — {mark} "
              f"{r['outcome']} in {len(r['steps'])} step(s) ==")
        print(f"  goal: {r['goal']}")
        for s in r["steps"]:
            print(f"  {s['n']:2}. {s['did']} → {s['then']}")
        for d in r.get("dead_taps") or []:
            print(f"  ⚠️  DEAD CONTROL — {d['label']!r} on {d['screen'] or 'screen'}")
        for c in r.get("crashes") or []:
            print(f"  🚨 CRASH — {c}")
        for x in r.get("reactions") or []:
            print(f"  {x.get('at', ''):8} {x.get('line', '')}")
        if r.get("premium"):
            print("  premium: " + " · ".join(r["premium"][:3]))
        for d in r.get("defects") or []:
            elem = f" [{d.get('element')}]" if d.get("element") else ""
            print(f"  · {d.get('severity', '')}{elem} {d.get('line', '')}")
        if r.get("verdict"):
            print(f"  verdict: {r['verdict']}")
        if r.get("mock_requests"):
            print("  mock backend: " + ", ".join(
                f"{x['method']} {x['path'].split('/')[-1]}" for x in r["mock_requests"][:8]))
        if r.get("model_error"):
            print(f"  ~ the judge could not run: {r['model_error']}")
        if not r["goal_reached"] and r.get("reason"):
            print(f"  why it stopped: {r['reason']}")
    for f in res["failed"]:
        print(f"✗ failed {f['id']}: {f['reason']}")
    print(f"\nflow(s) audited {len(res['run'])}/{res['planned']} · stored in "
          f".pipeline/ux_audit.json")
    return 1 if res["failed"] else 0


def cmd_ux_audit(args: argparse.Namespace) -> int:
    """One neutral first-use reaction over the screens this run actually touched."""
    project = resolve_project(args.project)
    log = RunLog(project, "ux-audit-" + today())
    from team import ux_audit
    res = ux_audit.run_audit(project, only_scene=getattr(args, "scene", None),
                             everything=getattr(args, "all", False),
                             capture_emulator=True, log=log,
                             avd=getattr(args, "avd", None),
                             device=not getattr(args, "no_device", False))
    if not res["targets"]:
        print("nothing to audit: no screen changed since its last audit "
              "(unchanged screens cost $0.00)")
        return 0
    for r in res["audited"]:
        print(f"\n== {r['label']} (scene {r['scene'] or '—'}) ==")
        for x in r.get("reactions") or []:
            print(f"  {x.get('at','')}  {x.get('line','')}")
        if r.get("premium"):
            print("  premium: " + " · ".join(r["premium"][:3]))
        for g in r.get("gripes") or []:
            sev = g.get("severity", "")
            elem = f" [{g.get('element')}]" if g.get("element") else ""
            if sev == "P0_BLUNDER":
                print(f"  🚨 {g['id']} [P0 BLUNDER]{elem} {g['line']}")
            elif sev == "P1_DEGRADED":
                print(f"  ⚠️  {g['id']} [P1 DEGRADED]{elem} {g['line']}")
            else:
                print(f"  ✦ {g['id']} [{g.get('kind', 'polish')}]{elem} {g['line']}")
        if r.get("verdict"):
            print(f"  verdict: {r['verdict']}")
    for s in res["skipped"]:
        print(f"~ skipped {s['label']}: {s['reason']}")
    print(f"\naudited {len(res['audited'])}/{res['targets']} screen(s) · "
          f"stored in .pipeline/ux_audit.json")
    return 0 if res["audited"] or not res["targets"] else 1


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


def cmd_competition(args: argparse.Namespace) -> int:
    """Execute competitive intelligence and market reconnaissance for an app."""
    project = resolve_project(args.project)
    log = RunLog(project, "competition-" + today())
    from team import competition
    res = competition.research_competitors(
        project,
        term=getattr(args, "term", "") or "",
        limit=getattr(args, "limit", 5) or 5,
        force=getattr(args, "force", False),
        log=log,
    )
    print(f"\nCompetitive Intelligence: {res.get('status')}")
    print(f"  Query Term: {res.get('term')}")
    print(f"  Competitors Analyzed: {res.get('count')}")
    print(f"  Document: {res.get('doc')}")
    print(f"  Summary: {res.get('summary')}")
    for c in res.get("competitors", [])[:5]:
        print(f"    • {c.get('title')} ({c.get('package')} - {c.get('rating')}★) -> {c.get('pricing_model')}")
    return 0 if res.get("status") == "ok" else 1


def cmd_revamp(args: argparse.Namespace) -> int:
    """Coordinate full-spectrum app revamp, PRD re-imagination, and legacy codebase purging."""
    project = resolve_project(args.project)
    log = RunLog(project, "revamp-" + today())
    from team import revamp

    wt = Path(args.worktree) if getattr(args, "worktree", None) else None

    if getattr(args, "purge_ui", False):
        purged = revamp.purge_codebase(project, target_tree=wt, full_codebase=False, log=log)
        print(f"\nLegacy UI Purge Complete:")
        print(f"  Target: {wt or project}")
        print(f"  Archived UI Files: {len(purged)}")
        for p in purged[:15]:
            print(f"    - {p}")
        if len(purged) > 15:
            print(f"    ... and {len(purged) - 15} more files")
        return 0

    if getattr(args, "purge_codebase", False):
        purged = revamp.purge_codebase(project, target_tree=wt, full_codebase=True, log=log)
        print(f"\nLegacy Codebase Purge Complete:")
        print(f"  Target: {wt or project}")
        print(f"  Archived Code Files: {len(purged)}")
        for p in purged[:15]:
            print(f"    - {p}")
        if len(purged) > 15:
            print(f"    ... and {len(purged) - 15} more files")
        return 0

    res = revamp.run_revamp(
        project,
        focus=getattr(args, "focus", "") or "",
        target_tree=wt,
        force=not getattr(args, "no_force", False),
        log=log,
    )
    print(f"\nApp Revamp Architecture Ready:")
    print(f"  PRD: {res.get('prd')}")
    print(f"  Competition Recon: {res.get('competition')}")
    print(f"  Design System: {res.get('design')}")
    print(f"  Invalidated Stale Assets: {res.get('invalidated_assets')}")
    print(f"  Purged Legacy Code Files: {res.get('purged_legacy_code_count')}")
    print("\nNext Steps:")
    for ns in res.get("next_steps", []):
        print(f"  {ns}")
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
        # THE PLANNER DECIDES EVERY CYCLE. Clubbing and priority are its calls —
        # it is the only component that sees every signal — so the loop re-runs it
        # here instead of only refilling an empty queue. When no synthesis path is
        # configured the collection still happens and the prompt is written for
        # the host agent; `pick` then serves the queue as it stands.
        if not getattr(args, "no_plan", False):
            print("Plan review (priority + batching)...")
            try:
                from team import research as _research
                _research.research_run(project, log,
                                       use_llm=not getattr(args, "no_llm", False),
                                       focus=getattr(args, "focus", None))
            except SystemExit as e:
                print(f"  planner: {e}")
            except Exception as e:      # a planner failure must not kill delivery
                print(f"  planner: could not run ({e}) — the queue stands as planned")
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
        from team import queue as _queue
        print(f"  Type: {task.get('type')} · Effort: {task.get('effort_min', _queue.DEFAULT_EFFORT_MIN)} min · Priority: {task.get('priority', '-')}")
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
    """Commit the plan mirror (docs/goals.html) and push it; report the hosted
    URL only once the page has been read back.

    Every run's bookkeeping (`record`, `research`, `adapt`) does this by itself;
    the command exists so the owner can put the current plan on the repo without
    waiting for a run. Best-effort by design — see team/publish.py."""
    from team import publish as publish_mod
    project = resolve_project(args.project)
    res = publish_mod.publish(project)   # renders the mirror from the live tree
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
    # The middle tier is STRUCTURE, so code supplies it the moment the owner
    # states the goal: a brand-new app plans against ends (experience →
    # retention → revenue) from its FIRST plan instead of against loose
    # features. No direction is invented here — only the shape under the sentence
    # the owner just wrote, and every move is recorded for review.
    before_tier = len(goals.goal_tier(profile))
    goals.ensure_goal_tier(profile)
    if len(goals.goal_tier(profile)) > before_tier:
        goals.change(state, "add", "goal tier",
                     "a new app gets the three ends the moment its goal exists, so "
                     "its first plan files milestones under what users need rather "
                     "than under loose features",
                     before="vision only", after="vision → goals → milestones",
                     by="agent")
    save_json(pipeline_dir(project) / "profile.json", profile)
    save_state(project, state)
    from team import tree as tree_mod
    tree_mod.write(project)
    print(f"the goal is now: {text}")
    if not goals.has_milestones(profile):
        print("  next: the team's job is to propose the milestones under the "
              "goals above (`devteam research`), not to ask you for them")
    print_page(project, "the goal is at the top")
    return 0


def cmd_asks(args: argparse.Namespace) -> int:
    """Review every open owner ask / agent flag against the tools this machine has.

    Deterministic by construction: the rule table in `team/asks.py` decides, not a
    model. An ask a browser or desktop driver can complete is AGENT WORK — the
    owner must never be handed it as a chore — and `--strict` exits non-zero while
    one remains, so a pipeline step can refuse to relay it as an ask.
    """
    project = resolve_project(args.project)
    from team import asks as asks_mod
    if args.selftest:
        failures = asks_mod.selftest()
        for f in failures:
            print(f"FAIL  {f}")
        print(f"selftest: {len(asks_mod.SELFTEST) - len(failures)}/{len(asks_mod.SELFTEST)} "
              "rule-table cases hold")
        return 1 if failures else 0
    rows = asks_mod.review_project(project, flags=_project_upgrades(project))
    if args.json:
        print(json.dumps(rows, indent=2))
        return 1 if (args.strict and any(r["verdict"] in ("agent", "mixed") for r in rows)) else 0
    print(asks_mod.render(rows))
    print()
    print(asks_mod.summary_line(rows))
    print_page(project)
    return 1 if (args.strict and any(r["verdict"] in ("agent", "mixed") for r in rows)) else 0


def cmd_keystore_backup(args: argparse.Namespace) -> int:
    """Copy the release signing keystore to the off-repo vaults, then prove it landed.

    `doctor` and `store-readiness` have always told the owner to run exactly this
    command when no off-repo backup exists — and for a while no such subcommand was
    wired, so the advice led to "invalid choice: 'keystore-backup'" and the warning
    could never clear. The check is presence-driven: a verified backup here clears
    the WARN on the next doctor/store-readiness read.
    """
    project = resolve_project(args.project)
    from team import keystore_backup
    res = keystore_backup.backup_keystore(project, github_vault_repo=args.github_vault)
    if not res.get("ok"):
        print(f"keystore-backup: FAILED — {res.get('error') or 'no backup destination available'}")
        return 1
    print(f"keystore-backup: {project.name} → {len(res['destinations'])} destination(s)")
    for dest in res["destinations"]:
        print(f"  · {dest}")
    cert = res.get("certificate") or {}
    if cert.get("sha256"):
        print(f"  certificate SHA-256: {cert['sha256']}")
    ok, detail = keystore_backup.verify_backup(project)
    print(f"keystore-backup: verify — {'verified' if ok else 'NOT verified'}: {detail}")
    return 0 if ok else 1


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
    # Disk hygiene is readiness: an unattended pipeline that cannot reclaim its
    # own trees fills the machine, and truncated probes look like app failures.
    try:
        from team import worktrees as worktrees_mod
        wsum = worktrees_mod.summary(project)
        if wsum["count"]:
            note = f"doctor worktrees: {wsum['count']} dev tree(s), {wsum['mb']} MB"
            if wsum["over_cap"]:
                note += (f" — OVER CAP ({worktrees_mod.TREE_CAP}); run `devteam "
                         f"worktree-prune {project.name} --yes`")
            print(note)
        for p in wsum["parked"]:
            print(f"doctor parked: {p['branch']} @ {p['sha']} ({p['age_days']}d, "
                  f"{'at origin' if p['at_origin'] else 'LOCAL ONLY'})")
    except Exception:
        pass
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
    # opt-in auto: three interleaved runs a night inside the owner's window.
    # An explicit --at anchors this app's runs and leaves the others alone.
    if args.at:
        slots = scheduler.propose_schedule(project.name, args.at)
        plist = scheduler.install_launchd(project.name, slots)
        print(f"AUTO schedule installed: {plist.stem} — "
              + ", ".join(f"{h:02d}:{m:02d}" for h, m in slots)
              + f" ({scheduler.RUNS_PER_DAY}x/night, anchored at {args.at})")
        close = [c for h, m in slots for c in scheduler.clashes(
            h, m, exclude=scheduler.project_label(project.name))]
        if close:
            near = ", ".join(sorted({f"{c['label']}@{c['hour']:02d}:{c['minute']:02d}"
                                     f" ({c['gap']} min)" for c in close}))
            print(f"  ⚠ close to {near}: the later run may be skipped (tolerated — "
                  "overlap is no longer fatal) or re-space with "
                  "`devteam schedules --rebalance`.")
    else:
        names = [j["project"] for j in scheduler.list_installed_jobs() if j["project"]]
        plan = scheduler.apply_plan(names + [project.name])
        slots = plan[project.name]
        job = next((j for j in scheduler.list_installed_jobs()
                    if j["project"] == project.name), None)
        plist = scheduler.plist_path(project.name,
                                     label=job["label"] if job else None)
        print(f"AUTO schedule installed: {plist.stem} — "
              + ", ".join(f"{h:02d}:{m:02d}" for h, m in slots)
              + f" ({scheduler.RUNS_PER_DAY}x/night)")
        print(f"  window {scheduler.WINDOW_START_HOUR:02d}:00–"
              f"{scheduler.WINDOW_END_HOUR:02d}:00, interleaved across "
              f"{len(plan)} app(s): {scheduler.plan_gap_minutes(len(plan)):.0f} min "
              "between consecutive machine-wide runs (overlap tolerated)")
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


def _storefront_scenes_message(project, deliverables, baseline, frames: str) -> str:
    """Commit subject that says what THIS command actually wrote.

    The message used to be hardcoded to "storefront screenshots (compositions:
    builtin)", so a run that only locked an editorial review landed a commit
    claiming it built screenshots — history said a listing was composed when
    nothing was drawn (observed 2026-11-07). Derive the subject from the
    deliverable fingerprints instead of the command's name.
    """
    from team.publish import fingerprint

    wrote = [rel for rel in deliverables
             if baseline is None or rel not in baseline
             or fingerprint(project / rel) != baseline.get(rel)]
    if any(rel.startswith("distribution/listing") for rel in wrote):
        return f"store: storefront screenshots (compositions: {frames})"
    labels: list[str] = []
    if any(rel.endswith("storefront.json") for rel in wrote):
        labels.append("scene spec")
    if any(rel.endswith("storefront-direction.json") for rel in wrote):
        labels.append("direction")
    if any(rel.endswith("storefront-review.json") for rel in wrote):
        labels.append("editorial review")
    if any(rel.endswith("storefront-assets.json") for rel in wrote):
        labels.append("brand assets")
    if any(rel.endswith("storefront-frames.json") for rel in wrote):
        labels.append("frame compositions")
    return "store: storefront " + (", ".join(labels) or "decision")


def cmd_storefront_scenes(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    from team import storefront_preview as sp
    from team import storefront_brand as sbf
    from team import storefront_direction as sd
    from team import storefront_frames as sfr
    from team import storefront_review as sr
    from team.publish import commit_paths, snapshot_paths

    # The listing's own files. Their baseline is taken BEFORE any step runs, so the
    # commit at the end carries only what this command wrote — a checkout that is
    # behind origin publishes nothing of its own (its old bytes would be a revert).
    deliverables = [sp.SPEC_REL, sd.DIRECTION_REL, sr.REVIEW_REL, sbf.BRAND_REL,
                    sfr.REGISTRY_REL, sfr.FRAMES_DIR_REL, "distribution/listing"]
    baseline = snapshot_paths(project, deliverables)

    do_all = getattr(args, "all", False)
    if not any([do_all, getattr(args, "scaffold", False), getattr(args, "render", False),
                getattr(args, "frame", False), getattr(args, "status", False),
                getattr(args, "review", False), getattr(args, "frames", False),
                getattr(args, "design_frame", None), getattr(args, "adopt_frame", None),
                getattr(args, "vision_frame", None), getattr(args, "gemini_brief", False),
                getattr(args, "assign", None)]):
        do_all = True

    if do_all or getattr(args, "scaffold", False):
        spec_file_before = (project / sp.SPEC_REL).is_file()
        spec = sp.load_spec(project, create=True, save=not getattr(args, "dry_run", False))
        module = project / spec.get("module", "")
        if not module.is_dir():
            module = sp.find_compose_module(project)
            spec["module"] = module.name
        print(f"~ storefront spec: {sp.SPEC_REL} (narrative: {spec.get('narrative')})")
        if not spec.get("theme"):
            print("  ! no theme composable detected; scenes render with default Material colours")
        # Roborazzi renders from androidUnitTest and needs no AGP preview tooling;
        # wiring the screenshot plugin anyway leaves a broken screenshotTest variant
        # (KMP androidTarget) in the project for nothing.
        if spec.get("renderer") == "roborazzi":
            print("  = renderer roborazzi — androidUnitTest harness, no AGP preview wiring")
        else:
            for change in sp.ensure_tooling(project, module, dry_run=getattr(args, "dry_run", False)):
                print(f"  + wired {change}")
        harness = sp.write_harness(project, spec, force=getattr(args, "force", False),
                                   dry_run=getattr(args, "dry_run", False))
        print(f"  + harness {harness.relative_to(project)}")
        missing = sp.unbound_scenes(spec)
        if missing:
            print(f"  ! {len(missing)} scene(s) not bound to a composable in {sp.SPEC_REL}:")
            for m in missing:
                print(f"      - {m}")
            print(f"    set \"composable\" for each (e.g. \"CompressScreen(data = CompressUIData(Fixtures.report, "
                  f"CompressQuality.MEDIUM), isLoading = false, errorMessage = null, onEvent = {{}}, "
                  f"onPickFile = {{}}, onBack = {{}})\"), then re-run --render")
        if not spec_file_before:
            print(f"  ~ {sp.SPEC_REL} was created — edit the copy and fixtures, then re-run")

    if getattr(args, "review", False):
        from team import storefront_review as sr
        spec = sp.load_spec(project, create=False)
        if getattr(args, "apply", None):
            raw = json.loads(Path(args.apply).read_text())
            try:
                review = sr.normalize_review(spec, raw, source="orchestrator",
                                             model="orchestrator-authored")
            except ValueError as exc:
                print(f"  ✗ review rejected: {exc}")
                return 1
        elif getattr(args, "llm", False):
            review = sr.llm_review(project, spec)
        else:
            prompt_file = project / ".pipeline/storefront-review-prompt.txt"
            prompt_file.parent.mkdir(parents=True, exist_ok=True)
            prompt_file.write_text(sr.build_prompt(project, spec))
            print(f"~ editorial review prompt -> {prompt_file.relative_to(project)}")
            print("  the ORCHESTRATING LLM answers it, then applies the decision:")
            print(f"  devteam storefront-scenes {args.project} --review --apply <review.json>")
            print("  (or re-run with --llm when a reviewer model is configured)")
            return 0
        path = sr.save_review(project, review)
        print(sr.format_review(review))
        print(f"  saved -> {path.relative_to(project)}")

    if do_all or getattr(args, "render", False):
        print("~ rendering composables off-device (AGP layoutlib)")
        tree = None
        if getattr(args, "from_checkout", False):
            tree = project
            print("  DELIVERY: --from-checkout — screenshots will describe the local "
                  "checkout, NOT the app CI ships")
        res = sp.render_previews(project, log=print, tree=tree)
        if res.get("status") != "OK":
            print(f"  ✗ {res.get('status')}: {res.get('error')}")
            if res.get("missing"):
                print(f"    unbound: {', '.join(res['missing'])}")
            if res.get("stderr"):
                print(res["stderr"].strip()[-1500:])
            return 1
        print(f"  {res.get('count', len(res.get('scenes', [])))} scene(s) rendered -> .pipeline/storefront_scenes")

    if getattr(args, "assign", None):
        from team import storefront_frames as sfr
        for pair in args.assign:
            if "=" not in pair:
                print(f"  ✗ --assign expects SCENE=FRAME, got {pair!r}")
                return 1
            scene_id, frame = (p.strip() for p in pair.split("=", 1))
            sfr.assign(project, scene_id, frame or None)
            print(f"  {scene_id} -> {frame or 'builtin'}")

    if getattr(args, "design_frame", None):
        from team import storefront_frames as sfr
        spec = sp.load_spec(project, create=False)
        scenes = spec.get("scenes") or []
        want = getattr(args, "frame_scene", None)
        scene = next((s for s in scenes if str(s.get("id")) == str(want)), None) if want else None
        if scene is None and not want and scenes:
            scene = scenes[0]
        if scene is None:
            print(f"  ✗ no scene {want!r} in {sp.SPEC_REL}")
            return 1
        prompt = sfr.compile_prompt(project, scene, brief=getattr(args, "brief", "") or "",
                                    name=args.design_frame, scenes=scenes)
        out = project / ".pipeline" / f"storefront-frame-prompt-{sfr.slug(args.design_frame)}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prompt)
        print(prompt)
        print(f"~ prompt -> {out.relative_to(project)}  (scene: {scene.get('id')})")
        print("  next: ask Stitch with this prompt, save the returned HTML, then")
        print(f"  devteam storefront-scenes {args.project} --adopt-frame {args.design_frame} "
              f"--frame-html <saved.html> --stitch-source <projects/…/screens/…>")

    if getattr(args, "adopt_frame", None):
        from team import storefront_frames as sfr
        html_arg = getattr(args, "frame_html", None)
        html_file = Path(html_arg).expanduser() if html_arg else None
        if not html_file or not html_file.is_file():
            print(f"  ✗ --adopt-frame needs --frame-html <path> (a saved Stitch HTML document)")
            return 1
        res = sfr.adopt(project, args.adopt_frame, html_file.read_text(),
                        source=getattr(args, "stitch_source", "") or "",
                        force=getattr(args, "force", False))
        if res.get("status") != "OK":
            print(f"ADOPT FRAME: {res.get('status')}")
            for issue in res.get("issues") or []:
                print(f"  ✗ {issue}")
            if res.get("error"):
                print(f"  ✗ {res['error']}")
            print("  the composition was NOT written; a frame that fails these checks crops or "
                  "misrepresents the app")
            return 1
        m = res.get("measured") or {}
        canvas = m.get("canvas") or [0, 0]
        slot = m.get("slot") or [0, 0, 0, 0]
        print(f"ADOPT FRAME: OK — {res['file']}")
        print(f"  canvas {canvas[0]}x{canvas[1]} · slot {slot[2]}x{slot[3]} at {slot[0]},{slot[1]}"
              f" · object-fit {m.get('fit')}")
        invented = res.get("invented") or []
        if invented:
            print(f"  ⚑ words the app's own copy never uses: {', '.join(invented[:14])}")
            print("    check each one: a poster may not advertise a feature the app does not have")
        print(f"  assign it: devteam storefront-scenes {args.project} --assign <scene>={args.adopt_frame}")

    if getattr(args, "vision_frame", None):
        from team import storefront_frames as sfr
        from team import vision_layout as vl
        spec = sp.load_spec(project, create=False)
        scenes = spec.get("scenes") or []
        want = getattr(args, "frame_scene", None)
        scene = next((s for s in scenes if str(s.get("id")) == str(want)), None) if want else None
        if scene is None and not want and scenes:
            scene = scenes[0]
        if scene is None:
            print(f"  ✗ no scene {want!r} in {sp.SPEC_REL}")
            return 1
        png = project / ".pipeline/storefront_scenes" / f"{scene.get('id')}.png"
        if not png.is_file():
            print(f"  ✗ no render at {png.relative_to(project)} — run --render first")
            return 1
        layout_file = getattr(args, "layout", None)
        if layout_file:
            layout = json.loads(Path(layout_file).read_text())
            print(f"~ layout from {layout_file}")
        else:
            print(f"~ asking the vision model to compose around {png.name}")
            layout = vl.propose(project, scene, png, brief=getattr(args, "brief", "") or "",
                                log=print)
        staged = project / ".pipeline/storefront_frames" / f"vision-{args.vision_frame}.html"
        backdrop = Path(args.backdrop).expanduser() if getattr(args, "backdrop", None) else None
        if backdrop and not backdrop.is_file():
            print(f"  ✗ no backdrop image at {backdrop}")
            return 1
        if backdrop:
            print(f"  backdrop: {backdrop.name} ({backdrop.stat().st_size // 1024} KB) — plate only, "
                  "the screenshot stays the real render")
        staged_path, plate = vl.render_to(project, scene, png, layout, staged, backdrop=backdrop)
        if plate and plate.get("removed"):
            box = plate.get("box") or [0, 0, 0, 0]
            print(f"  watermark removed at {box[0]},{box[1]} ({plate['boxSize'][0]}x{plate['boxSize'][1]} patch)")
        elif plate:
            print("  backdrop: no watermark detected")
        res = sfr.adopt(project, args.vision_frame, staged.read_text(),
                        source=f"vision:{vl.config()['model']}", force=getattr(args, "force", False))
        if res.get("status") != "OK":
            print(f"VISION FRAME: {res.get('status')}")
            for issue in res.get("issues") or []:
                print(f"  ✗ {issue}")
            if res.get("error"):
                print(f"  ✗ {res['error']}")
            return 1
        m = res.get("measured") or {}
        slot = m.get("slot") or [0, 0, 0, 0]
        print(f"VISION FRAME: OK — {res['file']}")
        print(f"  slot {slot[2]}x{slot[3]} at {slot[0]},{slot[1]} · object-fit {m.get('fit')}")
        for problem in res.get("invented") or []:
            print(f"  ⚑ {problem}")
        print(f"  layout JSON: re-run with --layout <file> to hand-tune and re-adopt")
        print(f"  assign it: devteam storefront-scenes {args.project} --assign {scene.get('id')}={args.vision_frame}")

    if getattr(args, "gemini_brief", False):
        from team import vision_layout as vl
        spec = sp.load_spec(project, create=False)
        scenes = spec.get("scenes") or []
        want = getattr(args, "frame_scene", None)
        scene = next((s for s in scenes if str(s.get("id")) == str(want)), None) if want else None
        text = vl.gemini_backdrop_prompt(project, scene, brief=getattr(args, "brief", "") or "")
        out = project / ".pipeline" / "gemini-backdrop-prompt.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print("GEMINI (browser) BACKDROP PROMPT — paste this into a Gemini chat:")
        print("=" * 66)
        print(text)
        print("=" * 66)
        print(f"~ saved -> {out.relative_to(project)}")
        print("  then save the generated image and run:")
        print(f"  devteam storefront-scenes {args.project} --vision-frame <name> "
              f"--backdrop <saved.png> [--layout <layout.json>]")
        return 0

    if getattr(args, "frames", False):
        from team import storefront_frames as sfr
        print(sfr.format_status(sfr.status(project)))

    if do_all or getattr(args, "frame", False):
        print("~ compositing marketing frames (1620x3200, 1080-wide opening)")
        res = sp.compose_frames(project, log=print, force=getattr(args, "force", False))
        if res.get("status") != "OK":
            print(f"  ✗ {res.get('error')}")
            return 1
        print(f"  {res.get('count', 0)} frame(s) -> distribution/listing/en-US/images/phoneScreenshots")

    if getattr(args, "status", False) or do_all:
        st = sp.preview_status(project)
        print(f"\nSTOREFRONT PREVIEW STATUS: {st.get('provenance') or 'not rendered'}")
        print(f"  scenes:   {', '.join(st.get('scenes') or []) or '(none)'}")
        print(f"  rendered: {st.get('rendered', 0)}")
        if st.get("unbound"):
            print(f"  unbound:  {', '.join(st['unbound'])}")
        if st.get("reviewed"):
            print(f"  review:   {' -> '.join(st.get('review_order') or [])}")
            if st.get("review_cut"):
                print(f"  cut:      {', '.join(st['review_cut'])}")
        else:
            print("  review:   NONE — run --review (an LLM decides what ships and in what order)")
    # A command that writes inside a project ends committed: the store assets and
    # dotfiles this run produced are deliverables, and a listing that exists only in
    # this checkout is a rumour. `commit_paths` copies exactly these paths into an
    # origin-anchored worktree, so the owner's unrelated WIP is never swept in.
    wrote = any([do_all, getattr(args, "scaffold", False), getattr(args, "render", False),
                 getattr(args, "frame", False), getattr(args, "adopt_frame", None),
                 getattr(args, "assign", None), getattr(args, "review", False),
                 getattr(args, "vision_frame", None)])
    if wrote:
        frames = ", ".join(sorted(sfr.load_registry(project)["assign"].values())) or "builtin"
        # The DECISIONS are deliverables too. They used to be left out of this list, so
        # a review or a direction could be locked, drive a render, and still exist only
        # in the checkout — the exact "rumour" rule 10 forbids.
        landed = commit_paths(
            project,
            deliverables,
            _storefront_scenes_message(project, deliverables, baseline, frames),
            log=print,
            baseline=baseline,
        )
        if not landed.get("pushed"):
            print(f"  ! not committed: {landed.get('why')}")

    return 0


def cmd_storefront_direction(args: argparse.Namespace) -> int:
    """Layer 1 of the asset pass: decide what the listing says, as a committed file."""
    import json as _json

    project = resolve_project(args.project)
    from team import storefront_direction as sd, storefront_preview as sp

    spec = sp.load_spec(project, create=False)

    if getattr(args, "seed", False):
        draft = sd.seed(project, spec)
        if getattr(args, "from_research", False):
            draft["evidence"] = sd.evidence_from_research(project)
        text = _json.dumps(draft, indent=2) + "\n"
        if getattr(args, "print", False):
            print(text)
            return 0
        if (project / sd.DIRECTION_REL).is_file():
            print(f"  ✗ {sd.DIRECTION_REL} already exists — edit it, or --apply a new one")
            return 1
        path = sd.save(project, draft)
        print(f"  + seeded {path.relative_to(project)}")
        print("  every judgement is blank on purpose: fill concept, audience, copy_angle, "
              "palette.emphasis, evidence, then --apply")
        return 0

    if getattr(args, "apply", None):
        raw = _json.loads(Path(args.apply).read_text())
        issues = sd.validate(raw, spec)
        if issues:
            print("DIRECTION: REJECTED")
            for issue in issues:
                print(f"  ✗ {issue}")
            return 1
        path = sd.save(project, raw)
        print(f"DIRECTION: LOCKED — {path.relative_to(project)} (concept: {raw.get('concept')})")
        return 0

    current = sd.load_raw(project)
    if current is None:
        print("DIRECTION: none — no " + sd.DIRECTION_REL)
        print(f"  → devteam storefront-direction {project.name} --seed")
        return 1
    issues = sd.validate(current, spec)
    if getattr(args, "json", False):
        print(_json.dumps({"ok": not issues, "issues": issues, "concept": current.get("concept")}, indent=2))
    else:
        print("DIRECTION: locked" if not issues else "DIRECTION: cannot drive a run")
        print(f"  concept: {current.get('concept') or '(empty)'}")
        print(f"  angles:  {len(current.get('copy_angle') or {})} scene(s), "
              f"evidence: {len(current.get('evidence') or [])}, "
              f"locked_by: {current.get('locked_by') or '(empty)'}")
        for issue in issues:
            print(f"  ✗ {issue}")
    return 1 if issues else 0


def cmd_takeover(args: argparse.Namespace) -> int:
    """The state join: what is true now, and what the next run must do."""
    import json as _json

    project = resolve_project(args.project)
    from team import takeover

    b = takeover.brief(project)
    if getattr(args, "json", False):
        print(_json.dumps(b, indent=2, default=str))
    else:
        print(takeover.format_brief(b))
    return 0


def cmd_gemini_auth(args: argparse.Namespace) -> int:
    """Manage or verify persistent Playwright login for Gemini image generation."""
    from team import gemini_browser

    ready, reason = gemini_browser.is_headless_ready()
    profile = gemini_browser.gemini_profile_dir()
    if getattr(args, "status", False):
        print(f"GEMINI PLAYWRIGHT PROFILE: {profile}")
        print(f"  ready:  {'YES' if ready else 'NO'} ({reason})")
        return 0 if ready else 1
    if ready and not getattr(args, "force", False):
        print(f"Gemini Playwright profile is already authenticated at {profile}.")
        print("  pass --force to re-authenticate or re-open the browser.")
        return 0
    print(f"Starting Gemini authentication flow for profile at {profile}...")
    ok = gemini_browser.auth_flow(log=print)
    return 0 if ok else 1


def cmd_storefront_status(args: argparse.Namespace) -> int:
    """Did the storefront pass finish, die, or never run — and may it ship?"""
    import json as _json

    project = resolve_project(args.project)
    from team import storefront_build as sb

    brief = sb.status(project)
    if getattr(args, "json", False):
        print(_json.dumps(brief, indent=2, default=str))
    else:
        print(sb.format_status(brief))
    return 0 if brief.get("publishable") else 1


def cmd_storefront_build(args: argparse.Namespace) -> int:
    """One command, one order, no skippable steps — see team/storefront_build.py."""
    project = resolve_project(args.project)
    from team.core import delivery_notice
    from team import storefront_build as sb
    from team import storefront_brand as sbf
    from team import storefront_direction as sd
    from team import storefront_frames as sfr
    from team import storefront_preview as sp
    from team import storefront_review as sr
    from team.publish import commit_paths, snapshot_paths

    # Taken BEFORE the pass opens a browser or renders anything: the commit at the
    # end must describe what this pass wrote, not whatever the checkout happens to
    # carry (a stale checkout's old copy of a decision file is a silent revert).
    deliverables = [sp.SPEC_REL, sd.DIRECTION_REL, sr.REVIEW_REL, sbf.BRAND_REL,
                    sfr.REGISTRY_REL, sfr.FRAMES_DIR_REL, "distribution/listing"]
    baseline = snapshot_paths(project, deliverables)

    print(delivery_notice(project))
    if getattr(args, "plan", False):
        choice = sb.resolve_path(frame=args.frame, plate=args.plate, gemini=args.gemini,
                                 layout=args.layout, frame_html=args.frame_html,
                                 builtin=args.builtin, poster=args.poster, why=args.why,
                                 owner_approved=getattr(args, "owner_approved", False))
        print(f"STOREFRONT BUILD PLAN: {'BLOCKED' if not choice['ok'] else 'OK'}")
        print(f"  path: {choice['path']}" + (f" ({', '.join(choice['flags'])})" if choice['flags'] else ""))
        if not choice["ok"]:
            print(f"  ✗ {choice['why']}")
            print(f"  → {choice['hint']}")
            return 1
        if choice["path"] == "code":
            print(f"  why: {choice['why']}")
        return 0
    choice = sb.resolve_path(frame=args.frame, plate=args.plate, gemini=args.gemini,
                             layout=args.layout, frame_html=args.frame_html,
                             builtin=args.builtin, poster=args.poster, why=args.why,
                             owner_approved=getattr(args, "owner_approved", False))
    # The run record is opened BEFORE anything renders and closed after the gates,
    # so a killed process (an agent that backgrounded this and ended its turn)
    # leaves "interrupted at step X" instead of no trace at all.
    sb.run_begin(project, choice.get("path") or "?", log=print)
    res: dict = {}
    try:
        res = sb.build(
            project, frame=args.frame, plate=args.plate, gemini=args.gemini,
            layout=args.layout, frame_html=args.frame_html, stitch_source=args.stitch_source,
            brief=args.brief, builtin=args.builtin, force=args.force,
            poster=args.poster, strict=args.strict, publish=False, why=args.why,
            owner_approved=getattr(args, "owner_approved", False), log=print,
        )
    finally:
        sb.run_end(project, res, log=print)
    # One machine-readable line: an orchestrator checks this, not the agent's prose.
    print("STOREFRONT BUILD JSON: " + json.dumps(
        {k: res.get(k) for k in ("status", "error", "frame", "score", "order", "chat")},
        default=str))
    if res.get("status") != "OK":
        print(f"STOREFRONT BUILD: {res.get('status')}")
        print(f"  ✗ {res.get('error')}")
        if res.get("hint"):
            print(f"  → {res['hint']}")
        return 1
    print(f"STOREFRONT BUILD: OK — composition {res.get('frame')}, critic {res.get('score')}/100")
    print(f"  order: {', '.join(res.get('order') or [])}")
    # The pass writes deliverables; a pass that ends with them uncommitted is not a
    # pass (rule 10). Committed here, after the critic gate, so only a listing that
    # actually passed the gate can land on main.
    # docs/storefront-assets.json is the PROVENANCE of the brand files this commit
    # carries. A pass that REUSES a banner an earlier `storefront-brand` run painted
    # must still publish that record: the baseline marks it "not written by this
    # command", so the listing image landed on origin without the provenance that
    # makes it shippable, and store-readiness reported source: none for an approved
    # asset (observed 2026-11-06). This pass certifies the brand gate, so it always
    # commits the record it certified.
    certified = {k: v for k, v in baseline.items() if k != sbf.BRAND_REL}
    landed = commit_paths(
        project,
        deliverables,
        f"store: storefront assets (composition: {res.get('frame')}, critic {res.get('score')})",
        log=print,
        baseline=certified,
    )
    if not landed.get("pushed"):
        print(f"  ! not committed: {landed.get('why')}")
    if args.publish:
        # Upload only AFTER the commit: a store upload must describe a revision, so
        # `publish_listing_assets` refuses a dirty asset tree (and it is right to).
        from team.play_listing import publish_listing_assets
        pub = publish_listing_assets(project, dry_run=False, log=print)
        print(f"  publish: {pub.get('status')} {pub.get('error') or ''}".rstrip())
    else:
        print(f"  next: devteam store-publish {args.project} --yes   (upload + commit the listing)")
    return 0


def cmd_docs_audit(args: argparse.Namespace) -> int:
    """The team's procedure documents must not state the same rule twice.

    `--fix` deletes the OLDER copy (git-blame order), because the newest commit is
    the source of truth; a contradiction is never auto-removed — the two sentences
    disagree, so somebody has to decide which one is right.
    """
    import json as _json

    from team import docaudit

    root = Path(__file__).resolve().parent
    scoped = getattr(args, "file", None)
    groups = docaudit.audit(root, files=scoped)
    if getattr(args, "new_only", False):
        from team.core import run as _run

        staged = [ln[3:] for ln in (_run(["git", "diff", "--cached", "--name-only"],
                                         root, timeout=60).stdout or "").splitlines() if ln]
        groups = docaudit.new_overlaps(root, groups, staged)
    exited = 0
    if getattr(args, "fix", False):
        doomed = docaudit.plan_removals(root, groups)
        removed = docaudit.apply_removals(root, doomed) if doomed else []
        for r in removed:
            print(f"  − {r['file']}:{r['line']}  {r['text']}")
        if removed:
            print(f"  removed {len(removed)} superseded statement(s); newest text kept")
        # Re-audit after removal. Contradictions always survive a fix, and anything
        # that survives besides them is a removal this tool refused to make — it
        # must still be reported, or "none" would be a lie.
        groups = docaudit.audit(root)
    if getattr(args, "rules", False):
        print(docaudit.format_rules(root))
        return 0
    if getattr(args, "update_baseline", False):
        path = docaudit.save_baseline(root, docaudit.audit(root))
        print(f"~ baseline -> {path.relative_to(root)} ({len(docaudit.audit(root))} known overlap(s))")
        return 0
    if getattr(args, "json", False):
        print(_json.dumps([
            {"kind": g.kind, "subject": g.subject,
             "keep": {"file": g.newest.file, "line": g.newest.line, "text": g.newest.text},
             "older": [{"file": m.file, "line": m.line, "text": m.text} for m in g.older]}
            for g in groups], indent=2))
    else:
        print(docaudit.format_report(root, groups))
    if groups and (getattr(args, "check", False) or not getattr(args, "fix", False)):
        exited = 1
    return exited


def cmd_store_assets(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    from team.core import delivery_notice
    print(delivery_notice(project))
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


def cmd_storefront_brand(args: argparse.Namespace) -> int:
    """The listing's brand assets — icon and feature graphic — painted, not templated."""
    project = resolve_project(args.project)
    from team.core import delivery_notice
    from team import storefront_brand as sbf
    from team.publish import commit_paths, snapshot_paths

    # The brand files ARE the listing's art plus the record of where it came from.
    # Neither used to be committed here: the record only reached origin if a later
    # `storefront-build` happened to write it, and when the build reused the banner
    # it explicitly skipped it — leaving an approved asset on origin with no
    # provenance (observed 2026-11-06). Snapshot first, commit what this command
    # wrote after the gate passes.
    deliverables = [sbf.BRAND_REL, "distribution/listing"]
    baseline = snapshot_paths(project, deliverables)

    print(delivery_notice(project))

    if getattr(args, "stitch_prompt", False):
        prompt = sbf.icon_stitch_prompt(project)
        out = project / ".pipeline/stitch-icon-prompt.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prompt)
        print("STITCH ICON PROMPT — generate this in Stitch, then adopt the HTML:")
        print("=" * 66)
        print(prompt)
        print("=" * 66)
        print(f"~ saved -> {out.relative_to(project)}")
        print(f"  devteam storefront-brand {args.project} --adopt-stitch <saved.html> "
              f"--stitch-source <projects/…/screens/…>")
        return 0

    if getattr(args, "launcher", False):
        # The launcher is APP code with its own provenance contract
        # (docs/launcher-icon.json), so this delegates to the brand chain's one
        # implementation and STOPS: the task branch and `devteam release` own the
        # commit. Committing it here pushed provenance to main from a worktree
        # while the pixels stayed on a dt branch (seen 2026-11-08).
        from team import brand_change as bc

        play_icon = project / sbf.IMAGES_REL / sbf.FILES["icon"]
        design_html = project / sbf.ICON_HTML_REL
        if not play_icon.is_file():
            print("STOREFRONT BRAND: BLOCKED — no listing icon to derive the launcher from")
            print(f"  ✗ {play_icon} is missing — adopt the Stitch artboard first")
            return 1
        if not design_html.is_file():
            print("STOREFRONT BRAND: BLOCKED — the adopted artboard is not kept in the project")
            print(f"  ✗ {design_html} is missing — `--adopt-stitch` keeps it")
            return 1
        # The brief that produced the artboard is part of the claim `verify` reads.
        prompt_file = project / bc.PROMPT_REL
        if not prompt_file.is_file():
            prompt_file.parent.mkdir(parents=True, exist_ok=True)
            prompt_file.write_text(sbf.icon_stitch_prompt(project).strip() + "\n")
            print(f"  + recorded the icon brief at {bc.PROMPT_REL}")
        icon = ((sbf.load(project).get("assets") or {}).get("icon") or {})
        res = bc.apply_launcher(
            project, play_icon,
            str(getattr(args, "launcher_background", "") or "") or bc._edge_color(play_icon),
            stitch={"source": icon.get("source") or "stitch",
                    "screen": icon.get("stitch_source") or "",
                    "artboard": icon.get("html") or sbf.ICON_HTML_REL},
            design_html=design_html)
        print(f"  + launcher rendered from {play_icon.name} into "
              f"{len(res.get('densities') or {})} density(ies) "
              f"(provenance: {bc.LAUNCHER_REL})")
        print("  ~ NOT committed: launcher files are app code — commit them on the task "
              "branch and let `devteam release` land them")
        return 0

    if getattr(args, "adopt_stitch", None):
        res = sbf.adopt_stitch_icon(project, Path(str(args.adopt_stitch)).expanduser(),
                                    stitch_source=args.stitch_source or "",
                                    note=args.note or "")
        if res.get("status") != "OK":
            print(f"STOREFRONT BRAND: {res.get('status')}")
            print(f"  ✗ {res.get('error')}")
            if res.get("hint"):
                print(f"  → {res['hint']}")
            return 1
        print(f"  + adopted the Stitch design as {res['file']} (kept at {res['html']})")


    elif getattr(args, "adopt", None):
        kind, _, path = str(args.adopt).partition("=")
        if not kind.strip() or not path.strip():
            print("STOREFRONT BRAND: --adopt needs kind=path, e.g. --adopt icon=/tmp/icon.png")
            return 1
        res = sbf.adopt(project, kind.strip(), Path(path.strip().strip('"')), note=args.note or "")
        if res.get("status") != "OK":
            print(f"STOREFRONT BRAND: {res.get('status')}")
            print(f"  ✗ {res.get('error')}")
            if res.get("hint"):
                print(f"  → {res['hint']}")
            return 1
        print(f"  + recorded the owner's {res['kind']} ({res['file']})")

    elif getattr(args, "generate", False):
        res = sbf.generate(project, force=getattr(args, "force", False), log=print)
        if res.get("status") != "OK":
            print(f"STOREFRONT BRAND: {res.get('status')}")
            print(f"  ✗ {res.get('error')}")
            if res.get("hint"):
                print(f"  → {res['hint']}")
            return 1
        print(f"  generated: {', '.join(res.get('generated') or []) or '(none)'}"
              + (f"  reused: {', '.join(res.get('reused') or [])}" if res.get("reused") else ""))

    print(sbf.format_status(project))
    issues = sbf.gate(project)
    if issues:
        return 1
    # Rule 10: a command that writes into a project ends with a commit on a pushed
    # ref. Committed only after the gate, so a listing that is not shippable cannot
    # land on main.
    landed = commit_paths(
        project, deliverables,
        "store: brand assets (icon/feature graphic + provenance)",
        log=print, baseline=baseline,
    )
    if not landed.get("pushed"):
        print(f"  ! not committed: {landed.get('why')}")
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    """THE PROMPT HANDLER: one owner sentence → ordered tool plan (optionally queued).

    The planner (`devteam plan`) is a different step and runs INSIDE the plan's
    first step when the request is admitted — the handler decides the order, the
    planner decides the queue's shape. See team/router.py.
    """
    project = resolve_project(args.project)
    from team.core import delivery_notice
    from team import router

    print(delivery_notice(project))
    res = router.apply(project, args.prompt) if getattr(args, "apply", False) \
        else router.plan(project, args.prompt)
    if getattr(args, "json", False):
        print(json.dumps(res, indent=2))
    else:
        print(router.format_plan(res))
        if res.get("queue_impact"):
            print()
            print(res["queue_impact"])
    if res.get("blocked"):
        print("\nROUTE: blocked — answer the question above before any tool runs")
        return 2
    if not getattr(args, "apply", False):
        print("\n  nothing was queued; re-run with --apply to admit the task and pin it")
    return 0


def cmd_brand(args: argparse.Namespace) -> int:
    """Owner feedback → Stitch → app icon → Play icon → publish, one chain.

    Every step is a file or a hash; `verify` refuses an icon change whose design,
    prompt or provenance is not in the commit. See team/brand_change.py.
    """
    project = resolve_project(args.project)
    from team.core import delivery_notice
    from team import brand_change as bc

    print(delivery_notice(project))

    if getattr(args, "reset", False):
        path = project / bc.STATE_REL
        if path.is_file():
            path.replace(path.with_suffix(".bak.json"))
            print("brand: state cleared (kept as .pipeline/brand.bak.json)")
        return 0

    if getattr(args, "feedback", None):
        res = bc.intake(project, args.feedback)
        if res.get("status") != "OK":
            print(f"BRAND: {res.get('status')}")
            print(f"  ✗ {res.get('error')}")
            if res.get("hint"):
                print(f"  → {res['hint']}")
            return 1
        print(f"BRAND: queued '{res['label']}' at the head of the plan")
        print("  the brief Stitch will receive (locked in the commit with the design):")
        print("=" * 66)
        print(res["prompt"])
        print("=" * 66)
        print(f"  next: devteam worktree-start {project.name} --task-id <id>")
        print(f"        devteam brand {project.name} --design --worktree <WORKTREE_PATH>")
        return 0

    if getattr(args, "design", False):
        if not args.worktree:
            print("BRAND: --design writes the app icon, so it needs the task worktree")
            print(f"  → devteam worktree-start {project.name} --task-id <id>")
            print(f"    devteam brand {project.name} --design --worktree <WORKTREE_PATH>")
            return 1
        res = bc.design(project, Path(args.worktree).expanduser(),
                        from_file=getattr(args, "from_file", None),
                        stitch_source=getattr(args, "stitch_source", ""),
                        timeout=getattr(args, "timeout", 900), log=print)
        if res.get("status") != "OK":
            print(f"BRAND DESIGN: {res.get('status')}")
            print(f"  ✗ {res.get('error')}")
            if res.get("hint"):
                print(f"  → {res['hint']}")
            return 1
        print(f"  ✓ design {res['design']['sha256'][:16]}  play icon "
              f"{str((res.get('launcher') or {}).get('play_icon', {}).get('sha256', ''))[:16]}"
              f"  ground {res['background']}")
        print(f"  ✓ launcher: {', '.join(sorted(res['launcher']['densities']))}")
        print(bc.format_status(project, root=Path(args.worktree).expanduser()))
        return 0

    if getattr(args, "publish", False):
        res = bc.publish(project, yes=getattr(args, "yes", False), log=print)
        status = res.get("status")
        if status in ("BLOCKED", "ERROR"):
            print(f"BRAND PUBLISH: {status}")
            for b in res.get("blockers") or [res.get("error")]:
                if b:
                    print(f"  ✗ {b}")
            if res.get("hint"):
                print(f"  → {res['hint']}")
            return 1
        print(f"BRAND PUBLISH: {status} — {res.get('package')} ({res.get('locale')})")
        if status == "DRY-RUN":
            print("  nothing was written; re-run with --yes to upload and commit")
        return 0

    info = bc.status(project, root=Path(args.worktree).expanduser() if args.worktree else None)
    if getattr(args, "json", False):
        print(json.dumps(info, indent=2))
        return 0 if info.get("status") in ("OK", "NONE") else 1
    print(bc.format_status(project, root=Path(args.worktree).expanduser()
                           if args.worktree else None))
    return 0 if info.get("status") in ("OK", "NONE") else 1


def cmd_store_publish(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    from team.core import delivery_notice
    from team.play_listing import publish_listing_assets
    print(delivery_notice(project))
    res = publish_listing_assets(project, dry_run=not getattr(args, "yes", False), log=print)
    status = res.get("status")
    if status in ("BLOCKED", "ERROR"):
        print(f"STOREFRONT PUBLISH: {status}")
        for b in res.get("blockers") or []:
            print(f"  ✗ {b}")
        if res.get("error"):
            print(f"  ✗ {res['error']}")
        if res.get("hint"):
            print(f"  → {res['hint']}")
        return 1
    print(f"STOREFRONT PUBLISH: {status} — {res.get('package')} ({res.get('locale')})")
    print(f"  order: {', '.join(res.get('order') or [])}")
    print(f"  score: {res.get('score')}")
    if status == "DRY-RUN":
        print("  nothing was written; re-run with --yes to upload and commit")
    return 0


def cmd_store_status(args: argparse.Namespace) -> int:
    """What Play says about the last delivery — recorded so the planner can see it.

    The store is the one gate the pipeline cannot read from git or CI: a release
    can be green, uploaded and still rejected by policy (2026-11-04). This writes
    an explicitly aged snapshot; `research` injects it and treats a stale one as
    unknown rather than as approval.
    """
    project = resolve_project(args.project)
    from team import store_status as ss

    snapshot = {}
    if getattr(args, "sync", False):
        package = _package_of(project)
        if not package:
            print("STORE STATUS: no package id in the profile — cannot find the app in the console")
            return 1
        conn = ((profile_of(project).get("connections") or {}).get("play_store") or {})
        res = ss.sync(project, package, account=str(conn.get("developer") or ""))
        if res.get("status") != "OK":
            print(f"STORE STATUS: {res.get('status')} — {res.get('reason')}")
            print("  → record it by hand instead: devteam store-status <project> "
                  "--state rejected --note \"what Play said\"")
            return 1
        snapshot = res["snapshot"]
    elif getattr(args, "state", None) or getattr(args, "decision", ""):
        prev = ss.read(project)
        snapshot = ss.write(project, getattr(args, "state", None) or prev.get("state", "unknown"),
                            note=getattr(args, "note", "") or prev.get("note", ""),
                            location=getattr(args, "location", "") or prev.get("location", ""),
                            decision=getattr(args, "decision", "") or "",
                            source="agent")
    else:
        snapshot = ss.read(project)

    if getattr(args, "json", False):
        print(json.dumps(snapshot, indent=2))
        return 0
    if not snapshot:
        print("STORE STATUS: no snapshot recorded")
        print("  → devteam store-status <project> --sync   (reads Play Console in your browser)")
        return 0
    print(ss.line(project))
    for key in ("location", "version", "evidence", "source"):
        if snapshot.get(key):
            print(f"  {key}: {snapshot[key]}")
    if snapshot.get("stale"):
        print(f"  ⚠ older than {ss.MAX_HOURS:g}h — the planner must not read this as approval")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    """The owner's one word, executed — and recorded as his word.

    Production used to be a console-only act. It is now a command because the
    owner asked for it (2026-11-05), and the command keeps the guarantees that
    made the old rule safe: it never runs as a side effect of anything, it only
    promotes a build that is already on internal, and it stores who asked, what
    note went out and which edit carried it.
    """
    project = resolve_project(args.project)
    from team.core import delivery_notice
    from team.promote import promote
    print(delivery_notice(project))
    res = promote(project, args.version, requested_by=args.requested_by,
                  notes_file=args.notes_file, dry_run=not getattr(args, "yes", False),
                  console=getattr(args, "console", False), log=print)
    status = res.get("status")
    if getattr(args, "json", False):
        print(json.dumps(res, indent=2))
        return 0 if status in ("promoted", "promoted-console", "already-promoted", "DRY-RUN") else 1
    if status in ("refused", "ERROR"):
        print(f"PROMOTE: {status} — {res.get('error')}")
        return 1
    if status == "already-promoted":
        print(f"PROMOTE: v{res.get('version')} is already on production — nothing sent")
        return 0
    print(f"PROMOTE: {status} — v{res.get('version')} ({', '.join(res.get('versionCodes') or [])})")
    print("  note:")
    for line in str(res.get("note") or "").splitlines():
        print(f"    {line}")
    if status == "DRY-RUN":
        print("  nothing was committed; re-run with --yes to send it for Play review")
        return 0
    print(f"  edit: {res.get('edit')} · read back: {res.get('read_back')}")
    if res.get("managed_publishing"):
        print("  ⚠ read back as DRAFT — managed publishing is ON: press Publish in Play "
              "Console, or the release waits there untouched")
    else:
        print("  → Google now reviews it; the console's Publishing overview shows the verdict")
    return 0


def _package_of(project: Path) -> str:
    profile = profile_of(project)
    for conn in (profile.get("connections") or {}).values():
        if isinstance(conn, dict) and conn.get("package"):
            return str(conn["package"])
    # Not declared as a connection yet: the app id in the build file is the same
    # string Play knows it by, so read it rather than refusing to look.
    try:
        from team.store_readiness import _find_gradle_files, _strip_code_comments

        text = "\n".join(f.read_text(errors="ignore") for f in _find_gradle_files(project))
        m = re.search(r"applicationId\s*=?\s*[\"']([A-Za-z0-9._]+)[\"']",
                      _strip_code_comments(text))
        return m.group(1) if m else ""
    except Exception:
        return ""


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


def cmd_stitch_screen(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    from team.design import compile_stitch_screen_prompt
    from team import stitch_mcp
    from team.design_validator import audit_design_drift, format_drift_report

    screen_type = getattr(args, "screen", "main")
    device = getattr(args, "device", "MOBILE")
    model_id = getattr(args, "model", "GEMINI_3_8_FLASH")
    prompt = compile_stitch_screen_prompt(project, screen_type=screen_type)

    if getattr(args, "generate", False):
        profile = profile_of(project)
        stitch_pid = (profile.get("design") or {}).get("stitch_project_id", "")
        if not stitch_pid:
            print(f"  ✗ No stitch_project_id declared in {project.name}/.pipeline/profile.json under 'design'.")
            print("    Please set 'stitch_project_id' in profile.json first.")
            return 1
        clean_pid = stitch_pid.split("/")[-1]
        print(f"Generating Stitch screen '{screen_type}' for project {clean_pid} using {model_id}...")
        try:
            screen = stitch_mcp.generate_screen(clean_pid, prompt, device=device, model_id=model_id, log=print)
            raw, mime = stitch_mcp.download(screen)
            out_dir = project / ".pipeline/stitch_screens"
            out_dir.mkdir(parents=True, exist_ok=True)
            suffix = ".html" if "html" in mime else ".svg" if "svg" in mime else ".png"
            out_file = out_dir / f"{screen_type}{suffix}"
            out_file.write_bytes(raw)
            print(f"✓ Saved generated Stitch screen -> {out_file.relative_to(project)} ({mime}, {len(raw)} bytes)")

            content = raw.decode(errors="ignore") if ("html" in mime or "svg" in mime) else ""
            if content:
                report = audit_design_drift(project, prompt_text=prompt, generated_content=content, screen_name=screen_type)
                print("\n" + format_drift_report(report))
                if report.get("verdict") == "FAIL":
                    print("  ✗ Generated screen drifted or violated anti-AI rules.")
                    return 1
            return 0
        except stitch_mcp.StitchError as exc:
            print(f"STITCH GENERATION FAILED: {exc}")
            return 1
    else:
        out = project / f".pipeline/stitch-prompt-{screen_type}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prompt)
        print(f"STITCH SCREEN PROMPT ({project.name.upper()} • {screen_type.upper()}):")
        print("=" * 65)
        print(prompt)
        print("=" * 65)
        print(f"~ prompt saved -> {out.relative_to(project)}")
        print("  To generate via Stitch MCP using Gemini 3.8 Flash, run:")
        print(f"  python3 cli.py stitch-screen {project.name} --screen {screen_type} --generate")
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
        # Vocabulary the project itself declares — the team must not know what any
        # project "is", only what its profile says.
        try:
            _prof = profile_of(project)
            vision = ((_prof.get("goals") or {}).get("vision") or "") + " " + (_prof.get("name") or "")
        except Exception:
            vision = project.name
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
                    # No declared stitch_project_id: match the screen to the project by
                    # SHARED VOCABULARY from its own name + vision, never by a hardcoded
                    # domain word — the team does not know what any project "is".
                    p_tokens = set(re.findall(r"[a-z]{4,}", p_name))
                    p_tokens |= set(re.findall(r"[a-z]{4,}", vision.lower()))
                    if p_tokens & set(re.findall(r"[a-z]{4,}", t_low)):
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


def cmd_ads(args: argparse.Namespace) -> int:
    """Paid acquisition for one app: plan first, then create — never the other way.

    The plan (docs/ads-plan.json) carries every judgement: who pays, how much a
    click may cost, which searches are worth buying, what the ad says. This
    command carries the mechanics and refuses to invent any of it.
    """
    import json as _json

    from team import ads

    project = resolve_project(args.project)

    def fail(message: str) -> int:
        print(f"ADS: {message}")
        return 1

    try:
        if getattr(args, "seed", False):
            if ads.plan_path(project).is_file():
                return fail(f"{ads.PLAN_REL} already exists — edit it, or --apply a new one")
            plan = ads.seed(project)
            path = ads.save(project, plan)
            print(f"  + seeded {path.relative_to(project)}")
            print("  app identity is read from the repo; every judgement is blank on purpose")
            print(f"  fill account/campaign/ad_groups/negatives, then: "
                  f"devteam ads {project.name} --check")
            return 0

        if getattr(args, "apply", None):
            raw = _json.loads(Path(args.apply).read_text())
            issues = ads.validate(raw)
            if issues:
                print("ADS PLAN: REJECTED")
                for issue in issues:
                    print(f"  ✗ {issue}")
                return 1
            path = ads.save(project, raw)
            print(f"ADS PLAN: LOCKED — {path.relative_to(project)} "
                  f"({raw['campaign']['name']})")
            return 0

    except ads.AdsError as exc:
        return fail(str(exc))

    if getattr(args, "status", False):
        try:
            st = ads.status(project)
        except ads.AdsError as exc:
            return fail(str(exc))
        if not st["exists"]:
            print(f"ADS: not created — '{st['name']}' does not exist in {st['customer_id']}")
            return 0
        print(f"ADS: {st['name']}")
        print(f"  campaign  {st['campaign_id']}  {st['status']} ({st['primary_status']})")
        print(f"  budget    ₹{st['daily_budget']:.0f}/day · account {st['customer_id']}")
        print(f"  built     {st['ad_groups']} ad groups · {st['ads']} ads · {st['keywords']} keywords")
        return 0

    plan = ads.load_plan(project)
    issues = ads.validate(plan)
    if getattr(args, "check", False):
        if issues:
            print("ADS PLAN: cannot create")
            for issue in issues:
                print(f"  ✗ {issue}")
            return 1
        print(f"ADS PLAN: valid — {plan['campaign']['name']} "
              f"(₹{plan['campaign']['daily_budget']:.0f}/day, "
              f"{len(plan['ad_groups'])} ad groups, "
              f"{sum(len(g['keywords']) for g in plan['ad_groups'])} keywords)")
        return 0

    if getattr(args, "start", False) or getattr(args, "pause", False):
        try:
            result = ads.set_status(project, enable=bool(args.start))
        except ads.AdsError as exc:
            return fail(str(exc))
        if result["status"] == "unchanged":
            print(f"ADS: '{result['name']}' is already {result['after']} — nothing changed")
            return 0
        path = ads.record(project, plan, result)
        print("=" * 60)
        print("ADS HANDOVER")
        print("=" * 60)
        print(f"  campaign   {result['campaign_id']}  {result['before']} → "
              f"{result['live_status']}")
        print(f"  budget     ₹{result['daily_budget']:.0f}/day · account {result['customer_id']}")
        print(f"  record     {path.relative_to(project)}")
        if args.start:
            print(f"  to stop    devteam ads {project.name} --pause")
        print("  note       spend starts on the next auction; ads already approved go live "
              "immediately, new ones wait for policy review")
        return 0

    if getattr(args, "create", False):
        if issues:
            print("ADS PLAN: cannot create")
            for issue in issues:
                print(f"  ✗ {issue}")
            return 1
        try:
            result = ads.create(project, plan, commit=bool(args.commit),
                                enable=bool(args.enable))
        except ads.AdsError as exc:
            return fail(str(exc))
        if result["status"] == "exists":
            camp = result["campaign"]
            print(f"ADS: already exists — {camp['id']} {camp['name']} ({camp['status']})")
            print("  nothing was created; edit campaign.name to build a second campaign")
            return 0
        if result["status"] == "dry_run":
            print(f"ADS DRY RUN: {result['name']} would be created in {result['customer_id']}")
            print(f"  {result['operations']} operations · status "
                  f"{'ENABLED' if result['enabled'] else 'PAUSED'} · nothing committed")
            print(f"  → devteam ads {project.name} --create --commit"
                  f"{' --enable' if result['enabled'] else ''}")
            return 0
        path = ads.record(project, plan, result)
        camp = next((r.get("resourceName") for r in result["responses"]
                     if "campaigns" in (r.get("resourceName") or "")), "")
        print("=" * 60)
        print("ADS HANDOVER")
        print("=" * 60)
        print(f"  campaign   {camp}  ({'ENABLED' if result['enabled'] else 'PAUSED'})")
        print(f"  account    {result['customer_id']} (₹{plan['campaign']['daily_budget']:.0f}/day)")
        print(f"  built      {len(plan['ad_groups'])} ad groups · "
              f"{sum(len(g['keywords']) for g in plan['ad_groups'])} keywords · "
              f"{result['operations']} operations")
        print(f"  record     {path.relative_to(project)}")
        if not result["enabled"]:
            print(f"  to start   devteam ads {project.name} --create --commit --enable"
                  f"  (or enable in the Ads UI)")
        print("  note       new ads sit in policy review for minutes to hours")
        return 0

    # Default: describe, do not act.
    exists = None
    try:
        exists = ads.find_campaign(str(plan["account"]["customer_id"]).replace("-", ""),
                                  plan["campaign"]["name"])
    except ads.AdsError as exc:
        print(f"  (live check skipped: {exc})")
    print(f"ADS PLAN: {plan['campaign']['name']}")
    print(f"  app       {plan['app'].get('title') or plan['app'].get('name')}"
          f"  {plan['app'].get('store_url', '')}")
    print(f"  account   {plan['account']['customer_id']}"
          f"  ₹{plan['campaign']['daily_budget']:.0f}/day  {plan['campaign']['currency']}"
          f"  geo {plan['campaign']['geo']['country']}"
          f"  lang {','.join(plan['campaign']['languages'])}")
    for group in plan["ad_groups"]:
        print(f"  group     {group['name']:34s} ₹{group['cpc_bid']:.0f} max cpc · "
              f"{len(group['keywords'])} kw · {len(group['headlines'])} h / "
              f"{len(group['descriptions'])} d")
    print(f"  guards    {len(plan['negatives'])} negatives")
    print(f"  live      {('campaign ' + exists['id'] + ' ' + exists['status']) if exists else 'not created yet'}")
    if issues:
        print("  cannot create yet:")
        for issue in issues:
            print(f"    ✗ {issue}")
        return 1
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
    from team import queue as _queue
    q.add_argument("--effort-min", type=int, default=None,
                   help=f"minutes of developer work for this entry "
                        f"(target {_queue.DEFAULT_EFFORT_MIN}, band "
                        f"{_queue.EFFORT_BAND[0]}-{_queue.EFFORT_BAND[1]})")
    q.add_argument("--supersedes", action="append", default=[])
    q.set_defaults(fn=cmd_queue)
    rh = sub.add_parser("run-headless"); rh.add_argument("project"); rh.add_argument("--task-spec", required=True); rh.add_argument("--supervised", action="store_true"); rh.set_defaults(fn=cmd_run_headless)
    ro = sub.add_parser("run-orchestrator"); ro.add_argument("project"); ro.add_argument("--prompt"); ro.add_argument("--timeout", type=int, default=5400); ro.set_defaults(fn=cmd_run_orchestrator)
    v = sub.add_parser("verify");      v.add_argument("project"); v.add_argument("--worktree"); v.add_argument("--task-spec"); v.add_argument("--skip-gradle", action="store_true"); v.set_defaults(fn=cmd_verify)
    ci_p = sub.add_parser("ci", help="print or write the canonical release workflow (tag → AAB → Play internal)")
    ci_p.add_argument("project")
    ci_p.add_argument("--write", action="store_true", help="render .github/workflows/release.yml, retire the old workflows and set the profile trigger to tag")
    ci_p.set_defaults(fn=cmd_ci)
    st_ = sub.add_parser("site", help="build and serve the local report page: every app, "
                                      "what changed, what to test (tailnet only)")
    st_.add_argument("--port", type=int, default=8788)
    st_.add_argument("--serve", action="store_true", help="serve it (Ctrl-C to stop)")
    st_.add_argument("--install", action="store_true", help="install a launchd service so it keeps serving")
    st_.add_argument("--uninstall", action="store_true")
    st_.add_argument("--status", action="store_true", help="is the page current? exits 1 when stale")
    st_.set_defaults(fn=cmd_site)
    wp = sub.add_parser("worktree-prune", help="remove task worktrees whose work already landed (dry run unless --yes)")
    wp.add_argument("project")
    wp.add_argument("--yes", action="store_true", help="actually remove merged worktrees and their dt/ branches")
    wp.add_argument("--force", action="store_true", help="also remove merged worktrees that still have uncommitted work (this DISCARDS it)")
    wp.add_argument("--force-live", dest="force_live", action="store_true",
                    help="also act on a tree touched inside the settle window — a run may be using it "
                         "(lock state is not proof: an unlocked live tree is the incident this guards)")
    wp.set_defaults(fn=cmd_worktree_prune)
    em = sub.add_parser("emulators");  em.add_argument("project"); em.add_argument("--record", action="store_true"); em.add_argument("--reset", action="store_true"); em.set_defaults(fn=cmd_emulators)
    r = sub.add_parser("release");     r.add_argument("project"); r.add_argument("--worktree", required=True); r.add_argument("--task-spec", required=True)
    r.add_argument("--rc", choices=["yes", "no"], default=None,
                   help="is this build a release candidate? Decided HERE, before the tag, "
                        "so the name can carry -rc.N (it used to be recorded after the build). "
                        "OMIT IT when the agent has not judged the build yet: no verdict is then "
                        "recorded, because the call belongs to the agent that made the change "
                        "(`devteam rc <project> --version <v> --yes|--no`) and a version-derived "
                        "note is not a judgement")
    r.set_defaults(fn=cmd_release)
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
    lp.add_argument("--no-plan", action="store_true",
                    help="skip the per-cycle plan review (priority + batching is then stale)")
    lp.add_argument("--no-llm", action="store_true",
                    help="collect signals and write the planner prompt instead of calling a model")
    lp.add_argument("--focus", help="scoped review request for this cycle's planner")
    lp.set_defaults(fn=cmd_loop)
    des = sub.add_parser("design", help="synthesize or enforce docs/design.md style law for an app")
    des.add_argument("project")
    des.add_argument("--topic", help="focus topic or aesthetic thesis (defaults to app vision)")
    des.add_argument("--force", action="store_true", help="overwrite existing docs/design.md with fresh synthesis")
    des.set_defaults(fn=cmd_design)
    comp = sub.add_parser("competition", help="run competitive recon on rivals, pricing models, and weakness analysis")
    comp.add_argument("project")
    comp.add_argument("--term", help="search term for competitor discovery (defaults to vision-derived domain)")
    comp.add_argument("--limit", type=int, default=5, help="number of competitors to analyze (default: 5)")
    comp.add_argument("--force", action="store_true", help="force fresh recon even if report exists")
    comp.set_defaults(fn=cmd_competition)
    rvp = sub.add_parser("revamp", help="execute full-spectrum app revamp, PRD re-imagination, and legacy codebase purging")
    rvp.add_argument("project")
    rvp.add_argument("--focus", help="focus topic or domain thesis (defaults to app vision)")
    rvp.add_argument("--worktree", help="path to task worktree for isolated development / codebase purging")
    rvp.add_argument("--purge-ui", action="store_true", help="safely purge and archive obsolete legacy UI screens from worktree")
    rvp.add_argument("--purge-codebase", action="store_true", help="mechanically purge and archive all legacy Kotlin/Java application code and tests")
    rvp.add_argument("--no-force", action="store_true", help="do not force asset invalidation or doc re-synthesis")
    rvp.set_defaults(fn=cmd_revamp)
    pl = sub.add_parser("plan", help="THE PLANNER — step 2 of every run: review the plan "
                                     "against fresh data, maintain the tree and the queue, "
                                     "and hand this run its task (the queue head)")
    pl.add_argument("project")
    pl.add_argument("--no-llm", action="store_true",
                    help="stop after collection and write the prompt for the host "
                         "tool's own agent (then: --apply-result <json>)")
    pl.add_argument("--apply-result", help="planner JSON produced by the host tool's native agent")
    pl.add_argument("--focus", help="the owner's scoped review request for this cycle (e.g. 'review the existing UX'); injected into the prompt as a labelled block, so a focused review needs no hand-edited prompt")
    pl.add_argument("--max-effort", type=int, default=None,
                    help="extended effort ceiling in minutes (owner request to give a task more time / revamp in one go)")
    pl.set_defaults(fn=cmd_plan)
    do = sub.add_parser("doctor");     do.add_argument("project"); do.set_defaults(fn=cmd_doctor)
    kb = sub.add_parser(
        "keystore-backup",
        help="back up the release signing keystore to the off-repo vaults "
             "(Google Drive + ~/.config/devteam/keystores) and verify it — the "
             "exact command doctor/store-readiness print when no backup exists")
    kb.add_argument("project")
    kb.add_argument("--github-vault", help="optional private GitHub repo to hold the backup")
    kb.set_defaults(fn=cmd_keystore_backup)
    ak = sub.add_parser(
        "asks",
        help="review every open owner ask/flag: can browser-control or desktop-use "
             "do it instead of the owner? (deterministic rule table, no model)")
    ak.add_argument("project")
    ak.add_argument("--json", action="store_true", help="machine payload, one row per ask")
    ak.add_argument("--strict", action="store_true",
                    help="exit 1 while any open ask is agent work (a step can refuse to relay it)")
    ak.add_argument("--selftest", action="store_true",
                    help="run the rule table's own fixed cases and exit non-zero on a misfile")
    ak.set_defaults(fn=cmd_asks)
    ad = sub.add_parser("adapt");      ad.add_argument("project")
    ad.add_argument("--dry-run", action="store_true",
                    help="report what would change without touching anything")
    ad.set_defaults(fn=cmd_adapt)
    pb = sub.add_parser("publish", add_help=True,
                        help="commit docs/goals.html and push it, then check the "
                             "GitHub Pages URL (every run's bookkeeping does this)")
    pb.add_argument("project")
    pb.set_defaults(fn=cmd_publish)
    vi = sub.add_parser("vision");     vi.add_argument("project")
    vi.add_argument("text", nargs="?", help="the owner's long-term goal, one sentence")
    vi.add_argument("--why", help="why the goal changed (optional)")
    vi.set_defaults(fn=cmd_vision)
    st = sub.add_parser("setup");  st.add_argument("--project", required=True)
    st.add_argument("--schedule", choices=["manual", "auto"], default="manual")
    st.add_argument("--at", default=None,
                    help=f"HH:MM anchor for the first of {scheduler.RUNS_PER_DAY} "
                         "nightly runs (default: the interleaved plan)")
    st.add_argument("--strict", action="store_true"); st.set_defaults(fn=cmd_setup)
    sch = sub.add_parser("schedules", help="list or manage registered nightly schedules")
    sch.add_argument("--remove", help="remove a project's nightly schedule, keeping it in manual mode")
    sch.add_argument("--rebalance", action="store_true",
                     help=f"re-space every installed app into {scheduler.RUNS_PER_DAY} "
                          f"runs/night inside "
                          f"{scheduler.WINDOW_START_HOUR:02d}:00–{scheduler.WINDOW_END_HOUR:02d}:00")
    sch.set_defaults(fn=cmd_schedules)
    cp = sub.add_parser(
        "capabilities",
        help="what the team can do, and whether this project is wired for it "
             "(a capability that landed as a new command is invisible to adapt)")
    cp.add_argument("project")
    cp.add_argument("--json", action="store_true")
    cp.set_defaults(fn=cmd_capabilities)
    cv = sub.add_parser("coverage", help="unit and UI test coverage over the shipped tree "
                                         "(the numbers the planner gates on)")
    cv.add_argument("project")
    cv.add_argument("--json", action="store_true")
    cv.set_defaults(fn=cmd_coverage)
    up = sub.add_parser("ux-plan", help="which screens need a UX audit now "
                                        "(fingerprint-driven; unchanged screens cost nothing)")
    up.add_argument("project"); up.add_argument("--json", action="store_true")
    up.set_defaults(fn=cmd_ux_plan)
    ua = sub.add_parser("ux-audit", help="one neutral first-use reaction over the screens "
                                         "this run touched (renders from the app itself)")
    ua.add_argument("project")
    ua.add_argument("--scene", help="audit one rendered scene id (docs/storefront.json)")
    ua.add_argument("--all", action="store_true", help="audit every screen with a render")
    ua.add_argument("--avd", help="AVD to boot when none is running")
    ua.add_argument("--no-device", action="store_true",
                    help="opt OUT of device evidence (not supported for a real audit: a "
                         "render cannot show keyboard/inset/network behaviour)")
    ua.set_defaults(fn=cmd_ux_audit)
    uv = sub.add_parser("ux-video", help="record declared journeys (docs/ux-flows.json) on a device "
                                         "and react to how they FEEL in motion, not just one still")
    uv.add_argument("project")
    uv.add_argument("--flow", help="record/re-act one flow id")
    uv.add_argument("--all", action="store_true", help="re-act every flow, including current ones")
    uv.add_argument("--avd", help="AVD to boot when none is running (default: the first installed)")
    uv.add_argument("--keep-emulator", action="store_true",
                    help="leave the emulator this command started running")
    uv.add_argument("--record-only", action="store_true", help="record, skip the reaction")
    uv.add_argument("--re-record", action="store_true",
                    help="re-record every declared flow (default: only new/stale ones)")
    uv.add_argument("--via", choices=["gemini", "api"], default="gemini",
                    help="who judges the motion: browser Gemini (default, same maker as the "
                         "store assets) or the vision API")
    uv.set_defaults(fn=cmd_ux_video)
    uf = sub.add_parser("ux-flow", help="drive goal-declared journeys on a device with a VISION "
                                        "model choosing each action, then judge whether a real "
                                        "user can complete them (no script: docs/ux-flows.json "
                                        "entries with a `goal` and no `steps`)")
    uf.add_argument("project")
    uf.add_argument("--flow", help="audit one goal-declared flow id")
    uf.add_argument("--avd", help="AVD to boot when none is running (default: the first installed)")
    uf.add_argument("--max-steps", type=int, default=None,
                    help=f"step budget for each flow (default: the flow's max_steps, else "
                         f"{10})")
    uf.add_argument("--keep-emulator", action="store_true",
                    help="leave the emulator this command started running")
    uf.add_argument("--worktree", metavar="PATH",
                    help="audit THIS task worktree instead of origin/main — the point is "
                         "to walk a change before it is merged (and before CI)")
    uf.add_argument("--mock", action="store_true",
                    help="serve a local Supabase-shaped mock and point the DEBUG build at "
                         "it, so a journey can be walked past sign-in to the end")
    uf.add_argument("--mock-port", type=int, default=None,
                    help="port for the mock (default 54399, or the next free one)")
    uf.add_argument("--via", choices=["api"], default="api",
                    help="the vision model that navigates (the API; browser Gemini is "
                         "the asset maker, not a navigator)")
    uf.set_defaults(fn=cmd_ux_flow)
    ms = sub.add_parser("mock-server", help="serve a local Supabase-shaped mock backend "
                                            "(deterministic fixtures) for a manual or audited "
                                            "walk of a debug build")
    ms.add_argument("project")
    ms.add_argument("--port", type=int, default=None)
    ms.set_defaults(fn=cmd_mock_server)
    tt = sub.add_parser("tree"); tt.add_argument("project")
    tt.add_argument("--html", action="store_true", help="write the html view and print its path")
    tt.add_argument("--open", action="store_true", help="open the html view in the browser")
    tt.set_defaults(fn=cmd_tree)
    g = sub.add_parser("goal")
    g.add_argument("project")
    g.add_argument("action", choices=["add", "park", "set-status", "retire",
                                      "reparent", "revert", "review", "log", "verdict"])
    g.add_argument("--met", choices=["yes", "no"],
                   help="verdict: whether the milestone's measures are met NOW")
    g.add_argument("--evidence", help="verdict: the probe or artifact that decides it")
    g.add_argument("--id", help="the branch to change (see `goal log` / the tree page)")
    g.add_argument("--title", help="add: what this branch is for, in plain words")
    g.add_argument("--parent", help="add/reparent: vision or another branch id")
    g.add_argument("--kind", default=None,
                   choices=["goal", "subgoal", "milestone"])
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
    sbr = sub.add_parser("storefront-brand",
                         help="the listing's brand assets: the Play icon and the feature graphic, "
                              "painted by the image model from the app's own icon and hero screen")
    sbr.add_argument("project")
    sbr.add_argument("--generate", action="store_true",
                     help="paint icon + feature graphic in Gemini (browser) and record provenance")
    sbr.add_argument("--stitch-prompt", action="store_true",
                     help="print the icon prompt for Stitch and save it (the icon is a Stitch "
                          "design, never an image-model painting)")
    sbr.add_argument("--adopt-stitch", metavar="HTML",
                     help="adopt a Stitch HTML design as the Play icon: kept in "
                          "docs/storefront-icon.html, rendered at 512x512, provenance=stitch")
    sbr.add_argument("--stitch-source", default="", help="with --adopt-stitch: the Stitch screen "
                                                        "the design came from (provenance)")
    sbr.add_argument("--adopt", metavar="KIND=PATH",
                     help="record an OWNER-supplied file instead: icon=<png> | feature=<png> "
                          "(Play's exact dimensions required)")
    sbr.add_argument("--note", default="", help="why the owner supplied it (recorded)")
    sbr.add_argument("--launcher", action="store_true",
                     help="render the adopted icon artboard into the app's launcher "
                          "(raster mipmaps + adaptive foregrounds at every density) so the "
                          "icon a user taps IS the listing icon")
    sbr.add_argument("--launcher-background", default="",
                     help="with --launcher: the adaptive background colour (default: the "
                          "icon art's own corner pixel)")
    sbr.add_argument("--force", action="store_true", help="repaint even if an approved asset exists")
    sbr.set_defaults(fn=cmd_storefront_brand)
    br = sub.add_parser("brand", help="OWNER brand feedback → Stitch artboard (from code) → app "
                                     "launcher icon → Play icon → release → publish, in one "
                                     "deterministic chain that verify refuses to skip")
    br.add_argument("project")
    br.add_argument("--feedback", metavar="TEXT",
                    help="the owner's words, verbatim: starts the change and queues the task")
    br.add_argument("--design", action="store_true",
                    help="generate the artboard in Stitch and write the app + listing icons "
                         "(run with --worktree, never in the checkout)")
    br.add_argument("--from", dest="from_file", metavar="SVG_OR_HTML",
                    help="adopt an existing Stitch artboard instead of calling Stitch")
    br.add_argument("--stitch-source", default="",
                    help="with --from: the Stitch screen the artboard came from (provenance)")
    br.add_argument("--worktree", help="the dt/<task> tree that receives the icon change")
    br.add_argument("--publish", action="store_true", help="commit the listing edit to Play")
    br.add_argument("--yes", action="store_true", help="with --publish: write to Play (default is a dry run)")
    br.add_argument("--status", action="store_true", help="where the chain stands (the default)")
    br.add_argument("--json", action="store_true", help="with --status: machine-readable")
    br.add_argument("--timeout", type=int, default=900, help="Stitch generation budget, seconds")
    br.add_argument("--reset", action="store_true",
                    help="clear an abandoned brand run deliberately (kept as a .bak file)")
    br.set_defaults(fn=cmd_brand)
    rt = sub.add_parser("route", help="THE PROMPT HANDLER: turn one owner sentence into the "
                                      "ordered tool plan it implies, judge alignment with the "
                                      "current plan, and pin the task at the queue head")
    rt.add_argument("project")
    rt.add_argument("--prompt", required=True, help="the owner's words, verbatim")
    rt.add_argument("--apply", action="store_true",
                    help="admit the task (pinned head) and record the plan; default is a dry run")
    rt.add_argument("--json", action="store_true", help="machine-readable plan")
    rt.set_defaults(fn=cmd_route)
    sb = sub.add_parser("storefront-build",
                        help="THE storefront asset pass: render, compose the designed frame, composite, gate, publish — in one deterministic order")
    sb.add_argument("project")
    sb.add_argument("--frame", default="auto",
                    help="frame name to adopt for every scene (default: auto — one frame per scene)")
    sb.add_argument("--strict", action="store_true",
                    help="refuse an asset whose watermark heal cannot be proved clean "
                         "(default: heal best-effort, report it, and let the owner decide)")
    sb.add_argument("--poster", action="store_true",
                    help="THE rule's path, and the default: Gemini paints the WHOLE asset from the "
                         "rendered screen, one prompt per scene (browser), then removes its "
                         "watermark and upscales to Play size")
    sb.add_argument("--plate", help="FALLBACK (needs --why): backdrop plate PNG painted by Gemini or another image model")
    sb.add_argument("--gemini", action="store_true", help="FALLBACK (needs --why): paint the backdrop plate in Gemini through the owner's Chrome")
    sb.add_argument("--layout", help="layout JSON from a previous run (re-run deterministically instead of asking the model)")
    sb.add_argument("--frame-html", help="Stitch-authored frame HTML to adopt instead of composing with the vision model")
    sb.add_argument("--stitch-source", default="", help="with --frame-html: the Stitch screen it came from")
    sb.add_argument("--brief", default="", help="composition brief handed to the vision model / plate prompt")
    sb.add_argument("--builtin", action="store_true",
                    help="FALLBACK (needs --why): ship the plain builtin template deliberately")
    sb.add_argument("--owner-approved", action="store_true", dest="owner_approved",
                    help="FALLBACK consent: the OWNER said the listing may be composed by code "
                         "instead of painted. Required by every code path together with --why; "
                         "recorded in docs/storefront-frames.json and checked by store-publish")
    sb.add_argument("--why", default="",
                    help="FALLBACK reason, required by every code-composed path (--builtin, "
                         "--gemini, --plate, --frame-html, --layout, --frame): what must stay "
                         "pixel-crisp and why the poster cannot do it. Recorded and gated at publish.")
    sb.add_argument("--publish", action="store_true", help="upload + commit the listing after the gates pass")
    sb.add_argument("--force", action="store_true", help="replace an existing composition")
    sfs = sub.add_parser("storefront-status",
                         help="what the storefront pass last did (finished / interrupted / never ran) "
                              "and whether the listing may ship — read from files, not from a report")
    sfs.add_argument("project")
    sfs.add_argument("--json", action="store_true")
    sfs.set_defaults(fn=cmd_storefront_status)
    gma = sub.add_parser("gemini-auth",
                         help="authenticate or check persistent headless Playwright for Gemini asset generation")
    gma.add_argument("--status", action="store_true", help="check status of persistent profile and prerequisites")
    gma.add_argument("--force", action="store_true", help="force re-authentication even if profile already exists")
    gma.set_defaults(fn=cmd_gemini_auth)
    sb.add_argument("--plan", action="store_true",
                    help="print which composition path this run would take (poster vs a fallback "
                         "and its reason) and exit — nothing is rendered or painted")
    sb.set_defaults(fn=cmd_storefront_build)
    sd = sub.add_parser("storefront-direction",
                        help="the direction layer: what the listing is trying to say, decided before anything is drawn")
    sd.add_argument("project")
    sd.add_argument("--seed", action="store_true",
                    help="write a valid skeleton from the spec + brand tokens (judgements deliberately empty)")
    sd.add_argument("--apply", metavar="JSON", help="validate an authored direction and save it")
    sd.add_argument("--check", action="store_true", help="validate the current file (exit 1 when it cannot drive a run)")
    sd.add_argument("--from-research", action="store_true",
                    help="with --seed: carry evidence pointers from .pipeline/research.json")
    sd.add_argument("--print", action="store_true", help="with --seed: print the skeleton instead of writing it")
    sd.add_argument("--json", action="store_true")
    sd.set_defaults(fn=cmd_storefront_direction)
    tk = sub.add_parser("takeover",
                        help="where this project stands, from state alone — and the ONE next command")
    tk.add_argument("project")
    tk.add_argument("--json", action="store_true", help="machine-readable brief")
    tk.set_defaults(fn=cmd_takeover)
    da = sub.add_parser("docs-audit",
                        help="find the team's own docs stating the same requirement twice; newest wins, older copy removed")
    da.add_argument("--fix", action="store_true",
                    help="remove the older copy of every overlap (contradictions are reported, never removed)")
    da.add_argument("--check", action="store_true",
                    help="exit non-zero when any overlap exists (the pre-commit hook's mode)")
    da.add_argument("--file", action="append",
                    help="scope to a doc (repeatable); a duplicate only counts when both copies are in scope")
    da.add_argument("--json", action="store_true", help="machine-readable report")
    da.add_argument("--rules", action="store_true",
                    help="rule coverage: which rules are code, which are still only prose, and what would retire each")
    da.add_argument("--new-only", action="store_true",
                    help="only overlaps this commit introduces (what the pre-commit hook blocks on)")
    da.add_argument("--update-baseline", action="store_true",
                    help="record the current overlaps as known (after resolving one)")
    da.set_defaults(fn=cmd_docs_audit)
    sfp = sub.add_parser("storefront-scenes",
                         help="render REAL app composables to Play screenshots via Compose preview (no emulator)")
    sfp.add_argument("project")
    sfp.add_argument("--scaffold", action="store_true", help="create docs/storefront.json + Kotlin preview harness and wire the Gradle plugin")
    sfp.add_argument("--render", action="store_true", help="render the harness off-device into .pipeline/storefront_scenes")
    sfp.add_argument("--frame", action="store_true", help="composite marketing frames into distribution/listing/en-US/images/phoneScreenshots")
    sfp.add_argument("--frames", action="store_true", help="list adopted frame compositions and their scene assignments")
    sfp.add_argument("--design-frame", metavar="NAME",
                     help="compile the Stitch prompt for a new frame composition (writes .pipeline/storefront-frame-prompt-NAME.txt)")
    sfp.add_argument("--brief", metavar="TEXT", help="with --design-frame: the composition brief (layout idea, focal point, mood)")
    sfp.add_argument("--frame-scene", metavar="ID", help="with --design-frame: author the frame around this scene's copy (default: first scene)")
    sfp.add_argument("--vision-frame", metavar="NAME",
                     help="let the configured VISION LLM read the render and compose the layout, then verify + adopt it as a frame")
    sfp.add_argument("--backdrop", metavar="PNG",
                     help="with --vision-frame: a backdrop plate painted by Gemini (browser) or another image model, used behind the phone")
    sfp.add_argument("--gemini-brief", action="store_true",
                     help="print the paste-ready Gemini (browser) prompt for a backdrop plate, then stop")
    sfp.add_argument("--layout", metavar="PATH",
                     help="with --vision-frame: render from a hand-edited layout JSON instead of asking the model")
    sfp.add_argument("--adopt-frame", metavar="NAME",
                     help="verify a Stitch-authored HTML poster and commit it as docs/frames/NAME.html")
    sfp.add_argument("--frame-html", metavar="PATH", help="with --adopt-frame: the saved Stitch HTML file")
    sfp.add_argument("--stitch-source", metavar="URL", help="with --adopt-frame: the Stitch screen/file it came from (provenance)")
    sfp.add_argument("--assign", metavar="SCENE=FRAME", action="append",
                     help="with --frame: render SCENE inside FRAME (use builtin to revert)")
    sfp.add_argument("--review", action="store_true",
                     help="editorial pass: an LLM decides which scenes ship and in what order "
                          "(paywall/purchase surfaces are always cut); written to docs/storefront-review.json")
    sfp.add_argument("--llm", action="store_true", help="with --review: call the configured reviewer model")
    sfp.add_argument("--apply", metavar="JSON",
                     help="with --review: apply an orchestrator-authored review JSON instead of calling a model")
    sfp.add_argument("--all", action="store_true", help="scaffold, render and frame in one pass (default)")
    sfp.add_argument("--status", action="store_true", help="show scene bindings and provenance")
    sfp.add_argument("--force", action="store_true", help="overwrite the harness and existing framed screenshots")
    sfp.add_argument("--dry-run", action="store_true", help="print Gradle wiring changes without writing them")
    sfp.add_argument("--from-checkout", action="store_true",
                     help="render the local checkout instead of origin/main (screenshots then describe unshipped code)")
    sfp.set_defaults(fn=cmd_storefront_scenes)
    spb = sub.add_parser("store-publish",
                         help="upload the reviewed listing assets to Play in editorial order (dry run unless --yes)")
    spb.add_argument("project")
    spb.add_argument("--yes", action="store_true", help="actually upload and commit the listing edit")
    spb.set_defaults(fn=cmd_store_publish)
    sts = sub.add_parser("store-status",
                         help="record what Play says about the last delivery (policy state), "
                              "read from the console through the browser; the planner reads it")
    sts.add_argument("project")
    sts.add_argument("--sync", action="store_true",
                     help="read Play Console in the owner's browser and record what it says")
    sts.add_argument("--state", help="record a state directly: " +
                     ", ".join(__import__("team.store_status", fromlist=["STATES"]).STATES))
    sts.add_argument("--note", default="", help="one line of context (what Play said, in its words)")
    sts.add_argument("--location", default="", help="where Play found it (e.g. 'App icon (en-US)')")
    sts.add_argument("--decision", default="",
                     help="the judgement taken about it (kept across re-syncs): "
                          "e.g. 'do not resubmit the rejected build; ship the fixed one'")
    sts.add_argument("--json", action="store_true", help="print the recorded snapshot as JSON")
    sts.set_defaults(fn=cmd_store_status)
    prm = sub.add_parser("promote",
                         help="send a build that is already on an internal track to "
                              "production, on the owner's explicit word (sends for Play review)")
    prm.add_argument("project")
    prm.add_argument("--version", required=True,
                     help="the version name already live on the internal track (e.g. 1.0.12)")
    prm.add_argument("--notes-file", default=None,
                     help="release note to publish (default: the repo's whatsnew file)")
    prm.add_argument("--requested-by", required=True,
                     help="the owner's own words asking for this launch — recorded with the edit")
    prm.add_argument("--yes", action="store_true",
                     help="actually commit the production release (dry run without it)")
    prm.add_argument("--console", action="store_true",
                     help="record a promotion already sent from Play Console (the API "
                          "service account cannot write production); refuses unless the "
                          "production track reads back holding this build")
    prm.add_argument("--json", action="store_true", help="machine-readable result")
    prm.set_defaults(fn=cmd_promote)
    ads_p = sub.add_parser("ads",
                           help="paid acquisition: plan (docs/ads-plan.json) then create — "
                                "dry run unless --commit, PAUSED unless --enable")
    ads_p.add_argument("project")
    ads_p.add_argument("--seed", action="store_true",
                       help="write docs/ads-plan.json with the app's identity and blank judgements")
    ads_p.add_argument("--apply", metavar="FILE",
                       help="validate an authored plan and lock it into docs/ads-plan.json")
    ads_p.add_argument("--check", action="store_true",
                       help="validate the locked plan against Google's limits (no API call)")
    ads_p.add_argument("--create", action="store_true",
                       help="create the campaign from the plan (dry run unless --commit)")
    ads_p.add_argument("--commit", action="store_true", help="actually create it")
    ads_p.add_argument("--enable", action="store_true",
                       help="create it ENABLED (default: PAUSED, ready to start)")
    ads_p.add_argument("--status", action="store_true",
                       help="what the plan's campaign looks like live: status, budget, counts")
    ads_p.add_argument("--start", action="store_true",
                       help="enable the plan's existing campaign (spends its daily budget)")
    ads_p.add_argument("--pause", action="store_true",
                       help="pause the plan's existing campaign again")
    ads_p.set_defaults(fn=cmd_ads)
    cs = sub.add_parser("console", help="plan or drive Google Play Console setup")
    cs.add_argument("project")
    cs.add_argument("--script", action="store_true", help="output automated Playwright JS script")
    cs.set_defaults(fn=cmd_console)
    stp = sub.add_parser("stitch-prompt", help="compile specific, pre-locked, anti-generic screen prompt for Stitch MCP")
    stp.add_argument("project")
    stp.add_argument("--screen", default="main", help="screen type: main, scoreboard, summary, setup")
    stp.set_defaults(fn=cmd_stitch_prompt)
    sscr = sub.add_parser("stitch-screen", help="generate or compile human-grade anti-AI Stitch screen via Gemini 3.8 Flash")
    sscr.add_argument("project")
    sscr.add_argument("--screen", default="main", help="screen type: main, scoreboard, summary, setup")
    sscr.add_argument("--device", default="MOBILE", help="target device: MOBILE, DESKTOP, TABLET")
    sscr.add_argument("--model", default="GEMINI_3_8_FLASH", help="model ID (default: GEMINI_3_8_FLASH)")
    sscr.add_argument("--generate", action="store_true", help="call Stitch MCP to generate screen and download HTML")
    sscr.set_defaults(fn=cmd_stitch_screen)
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
