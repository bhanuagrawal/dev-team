"""Doctor: validates each agent's setup requirements against the PROJECT'S
OWN declared connections (profile) + machine environment. Team code never
knows where credentials live — profiles do; doctor only checks them.

Per agent: READY | BLOCKED(reason) | NOT-CONFIGURED(agent optional for this
project). BLOCKED reasons are phrased as one precise ask for the owner.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .core import (
    PROJECTS_BASES, ROLE_ENV, TEAM_ROOT, agent_argv, now_iso, profile_of,
    resolve_model, role_env, run, runtimes, save_state, state_of,
)


def _has(p: str | Path) -> bool:
    return bool(p) and Path(str(p)).expanduser().exists()


def _model_fix(role: str, cfg: dict) -> str:
    """The right fix when a role's model check fails.

    A role model is usually a TOKEN ('llm' | 'thinker') that resolves from an
    environment variable (~/agentconfig/agentconfig.env). When the token is set
    but its env var is unset, advising "set roles.<role>.model in runtimes.json"
    sends the owner to edit the one file that is already correct — and the
    failure then reads as if the model being CONFIGURED were the problem. Name
    the real cause instead."""
    token = (cfg or {}).get("model", "") or ""
    env = ROLE_ENV.get(token)
    if env and not role_env(env):
        return (f"the '{role}' model is the token '{token}', which resolves from "
                f"${env} — that variable is not in this process's environment: "
                f"run `llmctl publish` to push the session, or export "
                f"{env}=<provider>/<model>. If it was switched recently this "
                f"process predates the change — restart it.")
    if not token:
        return f"set roles.{role}.model in runtimes.json"
    return (f"model token '{token}' does not resolve — check that ${env or 'the env'} "
            f"is exported") if env else f"set roles.{role}.model in runtimes.json"


def agent_requirements(profile: dict, project: Path) -> dict[str, list[tuple[str, bool, str]]]:
    conns = profile.get("connections", {})
    rt = runtimes()["roles"]

    ga4 = conns.get("ga4")
    ga4_checks: list[tuple[str, bool, str]] = []
    if ga4:
        from . import ga4 as ga4_mod
        prop = str(ga4.get("property") or "")
        key = ga4_mod.key_path(ga4.get("key_file"))
        ga4_checks.append(("connections.ga4.property declared", bool(prop),
                           "add the GA4 property id"))
        if key:
            ga4_checks.append((f"GA4 reader key readable ({key.name})", True, ""))
        # A declaration is READY only if the property can actually be read: the
        # old check proved "gcloud can mint a token", which stayed green while
        # every report returned 403 (or while the login Google now blocks was the
        # only advice). A real runReport is the only honest gate.
        probe = ga4_mod.probe(project, ga4)
        ga4_checks.append((f"GA4 property {prop or '?'} answers a report with the "
                           f"declared credential", bool(probe.get("ok")),
                           ga4_mod.fix_for(project.name, ga4, probe)))
        ga4_checks.append(("gcloud CLI available", bool(shutil.which("gcloud")),
                           "install the gcloud SDK (the GA4 collector mints service-account tokens through it)"))

    # Play Console connections (vitals + reviews): declared per project, and the
    # COLLECTOR is what silences the upgrade ask — so verify the key file and the
    # package id here, not just that the block exists (lesson 2026-10-22).
    play_checks: list[tuple[str, bool, str]] = []
    for key, label in (("play_vitals", "Play vitals"), ("play_reviews", "store reviews")):
        conf = conns.get(key)
        if not conf:
            continue
        play_checks += [
            (f"{label}: connections.{key}.key_file exists", _has(conf.get("key_file", "")),
             f"place the Play service-account JSON at connections.{key}.key_file"),
            (f"{label}: connections.{key}.package declared", bool(conf.get("package")),
             f"add the Play package id to connections.{key}.package"),
        ]
    if play_checks and not any(n == "gcloud CLI available" for n, _, _ in ga4_checks):
        play_checks.append(("gcloud CLI available", bool(shutil.which("gcloud")),
                            "install the gcloud SDK (both Play collectors mint tokens through it)"))

    dev = rt.get("developer", {})
    dev_argv = agent_argv(dev, "x") or []
    dev_bin = dev_argv[0] if dev_argv else ""
    dev_checks = [
        ("developer model configured", bool(resolve_model(dev)),
         _model_fix("developer", dev)),
        (f"headless agent command available ({dev_bin})" if dev_bin
         else "headless agent command not set — native subagents are the primary path",
         bool(shutil.which(dev_bin)) if dev_bin else True,
         f"install '{dev_bin}' on PATH, or drop the command and use native subagents"),
        ("java_home exists", _has(profile.get("java_home")), "set profile.java_home"),
    ]

    ship_checks = [
        ("gh authenticated", run(["gh", "auth", "status"], None, timeout=60).returncode == 0,
         "run: gh auth login"),
        ("origin reachable",
         run(["git", "-C", str(project), "ls-remote", "--heads", "origin"],
             project, timeout=60).returncode == 0,
         "check git remote / network"),
    ]

    syn = rt.get("product_synthesis", {})
    synthesis_checks = [
        ("product_synthesis model configured", bool(resolve_model(syn)),
         _model_fix("product_synthesis", syn)),
    ]

    from . import scheduler
    is_manual = profile.get("schedule") == "manual"
    sched_installed = scheduler.is_project_scheduled(project.name)
    sched_loaded = scheduler.is_project_loaded(project.name) if sched_installed else False
    if is_manual:
        sched_checks = [("nightly schedule: explicit manual mode declared", True, "")]
    else:
        sched_checks = [
            ("nightly schedule registered in launchd", sched_installed,
             f"run `devteam setup --project {project.name} --schedule auto` (or declare 'schedule': 'manual' in .pipeline/profile.json)"),
            ("nightly launchd job loaded and active", sched_loaded,
             f"launchd job for {project.name} is not loaded. Run: `devteam setup --project {project.name} --schedule auto`"),
        ]
        # Every app must hold RUNS_PER_DAY slots inside the owner's 23:00–11:00
        # window. Machine-wide spacing is best-effort: the run lock skips an
        # overlapping app rather than deferring it, but with three runs a night
        # a skip no longer costs the app its night (owner directive 2026-11-05).
        job = next((j for j in scheduler.list_installed_jobs()
                    if j["label"] == scheduler.project_label(project.name)
                    or j["project"] == project.name), None)
        slots = job["slots"] if job else []
        expected = f"{scheduler.RUNS_PER_DAY} runs/night ({scheduler.WINDOW_START_HOUR:02d}:00–{scheduler.WINDOW_END_HOUR:02d}:00)"
        ok = (len(slots) == scheduler.RUNS_PER_DAY
              and all(scheduler.in_window(h, m) for h, m in slots))
        detail = (", ".join(f"{h:02d}:{m:02d}" for h, m in slots) if slots
                  else "no slots registered")
        sched_checks.append((
            f"nightly runs {expected}", ok,
            "" if ok else
            (f"re-register: `devteam setup --project {project.name} --schedule auto` "
             f"(or `devteam schedules --rebalance`); found {detail}")))

    store_readiness_checks = [
        ("manifest detectable", bool(list(project.glob("**/AndroidManifest.xml"))),
         "ensure project contains AndroidManifest.xml"),
        ("gradle configuration detectable", bool(list(project.glob("*build.gradle*")) or list(project.glob("*/*build.gradle*"))),
         "ensure build.gradle or build.gradle.kts exists"),
    ]

    from . import competition, tavily
    has_recon = bool(competition.resolve_recon_bin()) or tavily.is_available()
    comp_checks = [
        ("competitor recon engine available (recon / tavily)", has_recon,
         "install recon-agent at ~/recon-agent or set TAVILY_API_KEY in environment"),
    ]

    return {
        "orchestrator": dev_checks + synthesis_checks + sched_checks,
        "developer": dev_checks,
        "research-agent": ga4_checks + play_checks + synthesis_checks + comp_checks,
        "release-agent": ship_checks + synthesis_checks,
        "alarm-agent": ship_checks[:1],
        "store-readiness-agent": store_readiness_checks,
    }


def _pipeline_json_check(project: Path) -> list[tuple[str, bool, str]]:
    """Every `.pipeline/*.json` is load-bearing (profile, state, tasks) and a
    malformed one silently breaks doctor/pick/verify — so parse them FIRST and
    report the exact file, instead of dying with a JSON traceback three frames
    deeper (lesson 2026-10-16 #46)."""
    checks: list[tuple[str, bool, str]] = []
    for p in sorted((project / ".pipeline").glob("*.json")):
        ok = True
        try:
            json.loads(p.read_text())
        except Exception:
            ok = False
        checks.append((f"{p.name} parses as JSON", ok,
                       f"repair malformed JSON in {p.name}"))
    return checks


# Words that are ordinary product/Android vocabulary. They cannot identify a project on
# their own ('a launcher-style rival' is generic prose), so they are not searched as
# tokens — the project's DISTINCTIVE words are, and the full name always matches.
GENERIC_TOKENS = {
    "launcher", "player", "coach", "tracker", "notes", "tasks", "list", "pdf", "kit",
    "app", "apps", "android", "studio", "focus", "pause", "calm", "daily", "habit",
    "routine", "budget", "wallet", "news", "music", "photos", "maps", "social", "video",
    "score", "court", "board", "media", "clean", "split", "merge", "convert", "share",
    "image", "images", "document", "documents", "file", "files", "text", "notes",
    "field", "cert", "certificate", "audit", "trade", "check", "inspection",
    "book", "books", "swap", "read", "reader",
}


def _name_tokens(name: str) -> set[str]:
    """The distinctive words of a project name, e.g. 'X Y Launcher' -> {'x','y'}."""
    words = re.findall(r"[A-Z]?[a-z]{2,}|[A-Z]{2,}(?![a-z])", name or "")
    return {w.lower() for w in words if len(w) >= 4}


def _distinctive_tokens(names: set[str]) -> set[str]:
    toks: set[str] = set()
    for n in names:
        toks |= _name_tokens(n)
    return toks - GENERIC_TOKENS


def _find_name_hits(text: str, names: set[str]) -> list[str]:
    """Project references present in `text` — full names AND distinctive name words.

    Matching only the full name let a project-keyed branch through untouched: code read
    a branch tested one lowercase word of the project's name while the registered name
    was the full CamelCase form, so a 300-line project-specific screenshot template sat
    inside the team's generic generator and every gate stayed green (2026-10-30). A
    distinctive word of the name identifies the project just as well as the full name,
    in any casing.
    """
    low = text.lower()
    hits = {n for n in names if n and n.lower() in low}
    for tok in _distinctive_tokens(names):
        if re.search(rf"\b{re.escape(tok)}\b", low):
            hits.add(tok)
    return sorted(hits)


def _registered_project_names() -> set[str]:
    """Every project this machine knows about — read from the projects' own
    profiles, never hard-coded (the team is project-independent)."""
    names: set[str] = set()
    for base in PROJECTS_BASES:
        try:
            children = base.iterdir()
        except Exception:
            continue
        for d in children:
            prof = d / ".pipeline" / "profile.json"
            if not prof.exists():
                continue
            try:
                n = json.loads(prof.read_text()).get("name")
            except Exception:
                n = None
            if n:
                names.add(str(n))
    return names


def _team_generic_check() -> list[tuple[str, bool, str]]:
    """INVARIANT: dev-team code/skills carry ZERO project references — a project's
    quirks live in that project's `.pipeline/profile.json`, never in team code.
    Greps the team's own code/skills for every registered project's name, with no
    exemption: there is no log file a project may be named in either."""
    names = _registered_project_names()
    if not names:
        return []
    files = [TEAM_ROOT / "cli.py", TEAM_ROOT / "README.md"]
    for pat in ("team/**/*.py", "prompts/**/*.md", "scripts/**/*.sh",
                "config/**/*.json", "skills/**/*.md"):
        files += [p for p in TEAM_ROOT.glob(pat)
                  if "__pycache__" not in p.parts]
    hits: list[str] = []
    for p in sorted(set(files)):
        try:
            found = _find_name_hits(p.read_text(errors="ignore"), names)
        except Exception:
            continue
        if found:
            hits.append(f"{p.relative_to(TEAM_ROOT)}: {', '.join(found)}")
    return [("team code/skills contain no project references", not hits,
             "move project-specific facts into that project's .pipeline/profile.json "
             "(never into team code). Offenders: " + "; ".join(hits))]


def run_doctor(project: Path, log) -> dict:
    # Load-bearing JSON first: never let a hand-edited profile/state blow up as
    # an opaque traceback. Report it as a first-class BLOCKED requirement.
    json_checks = _pipeline_json_check(project)
    bad_json = [(r, fix) for r, ok, fix in json_checks if not ok]
    for r, fix in bad_json:
        log(f"doctor pipeline-files: BLOCKED — {r} ({fix})")

    try:
        profile = profile_of(project)
    except Exception as e:  # profile itself unreadable → say so precisely
        log(f"doctor: FATAL — cannot load profile.json: {e}")
        blocked = ([{"requirement": r, "fix": fix} for r, fix in bad_json]
                   or [{"requirement": "profile.json loads", "fix": str(e)}])
        return {"pipeline-files": {"status": "blocked", "blocked": blocked,
                                   "checks_passed": 0}}
    reqs = agent_requirements(profile, project)
    report: dict[str, dict] = {}
    for agent, checks in reqs.items():
        if not checks:
            report[agent] = {"status": "not-configured", "blocked": [], "checks_passed": 0}
            log(f"doctor {agent}: not-configured (optional for this project)")
            continue
        missing = [(r, fix) for r, ok, fix in checks if not ok]
        report[agent] = {
            "status": "ready" if not missing else "blocked",
            "blocked": [{"requirement": r, "fix": fix} for r, fix in missing],
            "checks_passed": len(checks) - len(missing),
        }
        log(f"doctor {agent}: {report[agent]['status']}"
            + (" — " + "; ".join(r for r, _ in missing) if missing else ""))

    report["pipeline-files"] = {
        "status": "ready" if not bad_json else "blocked",
        "blocked": [{"requirement": r, "fix": fix} for r, fix in bad_json],
        "checks_passed": len(json_checks) - len(bad_json),
    }

    # Team-genericness invariant: dev-team code must never name a project.
    generic_checks = _team_generic_check()
    for r, ok, fix in generic_checks:
        if not ok:
            log(f"doctor team-generic: BLOCKED — {r} ({fix})")
    if generic_checks:
        report["team-generic"] = {
            "status": "ready" if all(ok for _, ok, _ in generic_checks) else "blocked",
            "blocked": [{"requirement": r, "fix": fix}
                        for r, ok, fix in generic_checks if not ok],
            "checks_passed": sum(1 for _, ok, _ in generic_checks if ok),
        }

    # The project's CI is GENERATED from the team template (AGENTS.md #4). A copy
    # that disagrees with its generator is a workflow nobody reviewed, and the next
    # `ci --write` silently rewrites it — so the drift is invisible without this.
    try:
        from . import ci as ci_mod
        target = project / ci_mod.WORKFLOW_REL
        template_problems = ci_mod.self_check()
        template_ok = not template_problems
        if not target.is_file():
            same = False
            checks = [("the canonical workflow exists", False,
                       f"run: devteam ci {project.name} --write")]
        else:
            same = target.read_text() == ci_mod.render(project)
            checks = [("the workflow matches the team template" if same else
                       "the workflow has DRIFTED from the team template", same,
                       f"run: devteam ci {project.name} --write (the project copy is generated, "
                       f"never hand-edited)")]
        # The generated copy can only be as good as its generator: a template that
        # computes the version with shell arithmetic (or names a placeholder nobody
        # fills) is a TEAM bug that every project would inherit. Reported once, here,
        # instead of as N mysterious project drifts.
        checks.insert(0, ("the CI template is internally sound" if template_ok
                          else "the CI template is internally sound: " + "; ".join(template_problems),
                          template_ok, "fix team/templates/ci-release.yml (see ci.self_check())"))
        bad = [(r, fix) for r, ok, fix in checks if not ok]
        report["ci"] = {
            "status": "ready" if not bad else "blocked",
            "blocked": [{"requirement": r, "fix": fix} for r, fix in bad],
            "checks_passed": len(checks) - len(bad),
        }
        for r, fix in bad:
            log(f"doctor ci: BLOCKED — {r} ({fix})")
        if not bad:
            log("doctor ci: ready (workflow is the rendered template)")
    except SystemExit as exc:  # profile cannot render CI at all
        report["ci"] = {"status": "blocked", "blocked": [{"requirement": "ci renders", "fix": str(exc)}],
                        "checks_passed": 0}
        log(f"doctor ci: BLOCKED — {exc}")

    # The plan's SHAPE is code-evaluated, not prose: vision → goals → milestones.
    # A flat tree means a FEATURE stands where a GOAL should (bedtime grayscale,
    # notification shielding) and no goal number can gate the next one. The fix
    # is one deterministic command, so this blocks rather than warns.
    try:
        from . import goals as goals_mod
        gaps = goals_mod.tree_gaps(profile)
        why = (gaps[0] + (f" (and {len(gaps) - 1} more)" if len(gaps) > 1 else "")
               ) if gaps else ""
        checks = [("the plan has a middle tier (vision → goals → milestones)"
                   if not gaps else "the plan has no middle tier: " + why,
                   not gaps,
                   f"run: devteam adapt {project.name}   (re-shapes the tree "
                   f"deterministically; it never invents the vision)")]
        bad = [(r, fix) for r, ok, fix in checks if not ok]
        report["goals"] = {
            "status": "ready" if not bad else "blocked",
            "blocked": [{"requirement": r, "fix": fix} for r, fix in bad],
            "checks_passed": len(checks) - len(bad),
        }
        for r, fix in bad:
            log(f"doctor goals: BLOCKED — {r} ({fix})")
        if not bad:
            log("doctor goals: ready (vision → goals → milestones)")
    except Exception as e:  # a check must never hide the report
        log(f"doctor goals: could not check ({e})")

    # Release signing keystore: check that an off-repo backup exists in Google Drive or local vault
    try:
        from . import keystore_backup
        has_backup, detail = keystore_backup.verify_backup(project)
        if has_backup:
            log("doctor signing-keystore: ready (off-repo backup verified)")
        else:
            log(f"doctor signing-keystore: WARN — {detail} (run: python3 cli.py keystore-backup {project.name})")
    except Exception as e:
        log(f"doctor signing-keystore: WARN — verification error: {e}")

    # Nothing may exist only on this disk (AGENTS.md). `status` reports the same
    # set: it is the detection half of the rule — the writer commits or parks
    # its own work, the team never commits the owner's WIP for them.
    try:
        from .core import checkout_dirty
        dirty = checkout_dirty(project)
        if dirty:
            shown = ", ".join(dirty[:6]) + (" …" if len(dirty) > 6 else "")
            log(f"doctor uncommitted: WARN — {len(dirty)} file(s) in the checkout "
                f"exist only on this disk: {shown}")
            # A WARN, not a block: the checkout is the owner's, and their WIP is
            # theirs to commit. What the pipeline must never do is ADD to it.
            report["uncommitted"] = {
                "status": "warn",
                "blocked": [],
                "checks_passed": 0,
                "note": ("commit it (or park it on a dt/* branch) — a change that "
                         "exists only here is lost with this machine/session"),
            }
    except Exception as e:  # a check must never hide the report
        log(f"doctor uncommitted: could not check ({e})")

    # An owner ask a machine can do is not an ask — it is work the pipeline owes.
    # Deterministic rule table (team/asks.py): the same text on the same machine
    # always yields the same verdict, and the evidence names the binary/config that
    # decided tool availability. WARN, never block: resolving it may need the
    # owner's 2FA tap, but the ask must never be relayed to them as a chore.
    try:
        from . import asks as asks_mod
        rows = asks_mod.review_project(project)
        agentish = [r for r in rows if r["verdict"] in ("agent", "mixed")]
        if agentish:
            log(f"doctor asks: WARN — {asks_mod.summary_line(rows)}")
            for r in agentish[:5]:
                log(f"  · {r['title'][:72]} → {r['tool']}"
                    f" ({'available' if r['available'] else 'tool not started'})")
            report["asks"] = {"status": "warn", "blocked": [], "checks_passed": 0,
                              "note": asks_mod.summary_line(rows)}
        elif rows:
            log(f"doctor asks: ready — {asks_mod.summary_line(rows)}")
            report["asks"] = {"status": "ready", "blocked": [], "checks_passed": 1}
    except Exception as e:  # a review must never hide the report
        log(f"doctor asks: could not review ({e})")

    # Capability parity: a feature the team ships but this project cannot use is
    # invisible without this. The polish audit landed as a command, `adapt` could
    # not see it (its test is data shape, and this added no key), and this check is
    # the one that would have named it. WARN, never block: the project still
    # ships, it just cannot tell polished work from new surface area.
    try:
        from . import capabilities as cap_mod
        rows = cap_mod.review(project, profile, state_of(project))
        cap_bad = [r for r in rows if r["status"] not in ("ok", "n/a")]
        for r in cap_bad:
            log(f"doctor capabilities: WARN — {r['id']}: {r['detail']} "
                f"({cap_mod.summary_line(rows)})")
        if cap_bad:
            report["capabilities"] = {
                "status": "warn", "blocked": [],
                "checks_passed": len(rows) - len(cap_bad),
                "note": cap_mod.summary_line(rows) + " — `devteam capabilities "
                        f"{project.name}` explains each one",
            }
        else:
            report["capabilities"] = {"status": "ready", "blocked": [],
                                      "checks_passed": len(rows)}
            log(f"doctor capabilities: ready — {cap_mod.summary_line(rows)}")
    except Exception as e:  # a check must never hide the report
        log(f"doctor capabilities: could not check ({e})")

    # Test posture: the verify gate refuses a production change that ships no
    # test, but a project with ZERO UI tests can still look healthy until that
    # change happens. WARN (not block) so the debt is visible BEFORE the next
    # task fails verify — the fix is agent work, never an owner ask.
    try:
        from . import code_tests
        from . import scan as scan_mod
        # Count on the DELIVERY tree, not the checkout: a checkout that lags
        # origin/main (the normal state after a release) reported 36 test files
        # while the shipped tree held 43, so the owner read a stale number right
        # after paying for the release that added them.
        try:
            posture_tree = scan_mod.origin_tree(project)
        except Exception:
            posture_tree = project
        posture = code_tests.posture(posture_tree if posture_tree.is_dir() else project)
        rows = code_tests.posture_checks(posture)
        bad = [(r, fix) for r, ok, fix in rows if not ok]
        if bad:
            log(f"doctor tests: WARN — {len(bad)} test-posture gap(s): "
                + "; ".join(r for r, _ in bad))
            report["tests"] = {
                "status": "warn", "blocked": [],
                "checks_passed": len(rows) - len(bad),
                "note": ("the verify gate refuses a production change with no "
                         "matching test in the diff — see plan for the fix"),
            }
        elif rows:
            report["tests"] = {"status": "ready", "blocked": [],
                               "checks_passed": len(rows)}
            log(f"doctor tests: ready — {posture['unit_tests']} unit / "
                f"{posture['ui_tests']} UI asserting test file(s)")
    except Exception as e:  # a check must never hide the report
        log(f"doctor tests: could not check ({e})")

    # Coverage posture: 100% unit and UI test coverage is enforced by code.
    # When below 100%, warn so the debt is visible and planner queue HEAD prioritizes it.
    try:
        from . import coverage as coverage_mod
        cov = coverage_mod.stored(project)
        if not cov:
            from . import scan as scan_mod
            stree = scan_mod.origin_tree(project)
            if stree.is_dir():
                cov = coverage_mod.collect(stree, profile_of(project))
        if cov and cov.get("status") == "ok":
            below = cov.get("below_floor") or []
            if below:
                log(f"doctor coverage: WARN — {coverage_mod.summary_line(cov).removeprefix('coverage: ')} (target 100%)")
                report["coverage"] = {
                    "status": "warn", "blocked": [],
                    "checks_passed": 0,
                    "note": f"coverage below 100% ({', '.join(below)}); planner queue HEAD must raise coverage",
                }
            else:
                log(f"doctor coverage: ready — 100% unit and UI coverage")
                report["coverage"] = {"status": "ready", "blocked": [], "checks_passed": 1}
    except Exception as e:  # a check must never hide the report
        log(f"doctor coverage: could not check ({e})")

    # The report page answers "what shipped, what do I test?" on the owner's phone.
    # Every ledger write rebuilds it (`core.save_state`), so a page older than the
    # ledger means a rebuild FAILED or a writer bypassed the ledger — and the page
    # keeps reading as current while it describes an older release. Checked BEFORE
    # this run's own state write (itself a rebuild), so the warn names exactly what
    # the last reader was shown; a successful run clears it.
    try:
        from . import report as report_mod
        st = report_mod.status()
        if not st["built"]:
            log("doctor report-page: WARN — the report page was never built")
            report["report-page"] = {
                "status": "warn", "blocked": [], "checks_passed": 0,
                "note": "run: python3 cli.py site",
            }
        elif st["stale"]:
            log(f"doctor report-page: WARN — built {st['built']} but the ledger moved "
                f"since: {', '.join(st['stale'])}")
            report["report-page"] = {
                "status": "warn", "blocked": [], "checks_passed": 0,
                "note": ("a ledger write rebuilds the page, so this is a rebuild that "
                         "failed — see that command's stderr; fix: python3 cli.py site"),
            }
        else:
            report["report-page"] = {"status": "ready", "blocked": [], "checks_passed": 1}
            log(f"doctor report-page: ready (built {st['built']})")
    except Exception as e:  # a check must never hide the report
        log(f"doctor report-page: could not check ({e})")

    try:
        state = state_of(project)
        state["doctor"] = {"generated_at": now_iso(), "report": report}
        save_state(project, state)
    except Exception as e:  # a broken state.json must not hide the report
        log(f"doctor: could not persist report (state.json unreadable: {e})")
    return report
