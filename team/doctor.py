"""Doctor: validates each agent's setup requirements against the PROJECT'S
OWN declared connections (profile) + machine environment. Team code never
knows where credentials live — profiles do; doctor only checks them.

Per agent: READY | BLOCKED(reason) | NOT-CONFIGURED(agent optional for this
project). BLOCKED reasons are phrased as one precise ask for the owner.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .core import (
    PROJECTS_BASES, TEAM_ROOT, now_iso, profile_of, run, runtimes,
    save_state, state_of,
)


def _has(p: str | Path) -> bool:
    return bool(p) and Path(str(p)).expanduser().exists()


def agent_requirements(profile: dict, project: Path) -> dict[str, list[tuple[str, bool, str]]]:
    conns = profile.get("connections", {})
    rt = runtimes()["roles"]

    ga4 = conns.get("ga4")
    ga4_checks: list[tuple[str, bool, str]] = []
    if ga4:
        key_needed = bool(ga4.get("key_file"))
        if key_needed:
            ga4_checks.append(("service-account key file exists", _has(ga4.get("key_file", "")),
                               "place a key file at connections.ga4.key_file (or update the path)"))
        else:
            # simple default: owner's shared Google login (one-time gcloud ADC),
            # no per-project key file — verify the token mints now
            adc_ok = run(["gcloud", "auth", "application-default", "print-access-token",
                          "--scopes", "https://www.googleapis.com/auth/analytics.readonly"],
                         None, timeout=60).returncode == 0
            ga4_checks += [
                ("connections.ga4.property declared", bool(ga4.get("property")),
                 "add the GA4 property id"),
                ("owner gcloud login can mint analytics.readonly token", adc_ok,
                 "one-time: gcloud auth application-default login --scopes=https://www.googleapis.com/auth/analytics.readonly"
                 " (shared across ALL projects)"),
            ]
        ga4_checks.append(("gcloud CLI available", bool(shutil.which("gcloud")), "install gcloud SDK"))

    dev = rt.get("developer", {})
    dev_rt_name = dev.get("runtime") or ""
    dev_checks = [
        (f"developer runtime CLI present ({dev_rt_name})",
         bool(shutil.which(dev_rt_name)), f"install the {dev_rt_name} CLI"),
        ("java_home exists", _has(profile.get("java_home")), "set profile.java_home"),
        ("developer model configured", bool(dev.get("model")),
         "set roles.developer.model in runtimes.json"),
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
        ("product_synthesis model configured", bool(syn.get("model")),
         "set roles.product_synthesis.model in runtimes.json"),
    ]

    return {
        "orchestrator": dev_checks + synthesis_checks,
        "developer": dev_checks,
        "research-agent": ga4_checks + synthesis_checks,
        "release-agent": ship_checks + synthesis_checks,
        "alarm-agent": ship_checks[:1],
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


def _find_name_hits(text: str, names: set[str]) -> list[str]:
    """Project names present in `text` (case-insensitive). Pure + testable."""
    low = text.lower()
    return sorted({n for n in names if n and n.lower() in low})


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
    Greps the team's own code/skills for every registered project's name.
    `skills/lessons.md` is deliberately EXEMPT: it is the append-only surprises
    log and the one place a project may legitimately be named."""
    names = _registered_project_names()
    if not names:
        return []
    files = [TEAM_ROOT / "cli.py", TEAM_ROOT / "README.md"]
    for pat in ("team/**/*.py", "prompts/**/*.md", "scripts/**/*.sh",
                "config/**/*.json", "skills/**/*.md"):
        files += [p for p in TEAM_ROOT.glob(pat)
                  if p.name != "lessons.md" and "__pycache__" not in p.parts]
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

    try:
        state = state_of(project)
        state["doctor"] = {"generated_at": now_iso(), "report": report}
        save_state(project, state)
    except Exception as e:  # a broken state.json must not hide the report
        log(f"doctor: could not persist report (state.json unreadable: {e})")
    return report
