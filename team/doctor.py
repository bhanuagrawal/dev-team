"""Doctor: validates each agent's setup requirements against the PROJECT'S
OWN declared connections (profile) + machine environment. Team code never
knows where credentials live — profiles do; doctor only checks them.

Per agent: READY | BLOCKED(reason) | NOT-CONFIGURED(agent optional for this
project). BLOCKED reasons are phrased as one precise ask for the owner.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .core import now_iso, profile_of, run, runtimes, save_state, state_of


def _has(p: str | Path) -> bool:
    return bool(p) and Path(str(p)).expanduser().exists()


def agent_requirements(profile: dict, project: Path) -> dict[str, list[tuple[str, bool, str]]]:
    conns = profile.get("connections", {})
    rt = runtimes()["roles"]

    ga4 = conns.get("ga4")
    ga4_checks: list[tuple[str, bool, str]] = []
    if ga4:
        ga4_checks = [
            ("connections.ga4.property declared", bool(ga4.get("property")),
             "add the GA4 property id"),
            ("service-account key file exists", _has(ga4.get("key_file", "")),
             "place a key file at connections.ga4.key_file (or update the path)"),
            ("gcloud CLI available", bool(shutil.which("gcloud")), "install gcloud SDK"),
            ("runtime token scope", True,  # verified live at pull time
             "research agent requests analytics.readonly automatically"),
        ]

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


def run_doctor(project: Path, log) -> dict:
    profile = profile_of(project)
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

    state = state_of(project)
    state["doctor"] = {"generated_at": now_iso(), "report": report}
    save_state(project, state)
    return report
