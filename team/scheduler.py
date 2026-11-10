"""Setup agent: installs everything the team needs to run daily and deliver to
the Play internal-testing track. Idempotent — safe to re-run.

What it sets up:
  1. environment checks (git, gh auth, python3, JDK/JBR, android sdk)
  2. project onboarding if missing (.pipeline/profile.json + state.json)
  3. launchd LaunchAgent running scripts/nightly.sh <project> `RUNS_PER_DAY`
     times inside the owner's 23:00–11:00 window
  4. global skill symlink so any agent CLI can operate the team (dev-team skill)
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
from pathlib import Path

from .core import TEAM_ROOT, agent_argv, pipeline_dir, role_cfg, run

LAUNCH_LABEL = "com.devteam.nightly"
DEFAULT_AT = "23:00"

# The owner's run window: 23:00 → 11:00 (12 hours). Every app fires
# RUNS_PER_DAY times inside it (owner request 2026-11-05) so one skipped run
# never costs an app its whole night. Lowered 3 → 2 on 2026-11-11 (owner
# request): two runs a night, ~6 h apart.
WINDOW_START_HOUR = 23
WINDOW_END_HOUR = 11
RUNS_PER_DAY = 2

# Best-effort spacing target between any two slots on the machine.
#
# `scripts/nightly.sh` holds ONE machine-wide lock (`.nightly.lock`), so an app
# that fires while another run is alive is SKIPPED, not deferred. The owner
# accepts an occasional overlap now that every app gets two chances a night;
# spacing is therefore a target we optimise, not a hard refusal. Apps are
# interleaved evenly across the window, which keeps consecutive runs as far
# apart as the number of apps allows (6 apps × 2 runs = 60 min apart).
MIN_GAP_MIN = 30


def _minutes_of(hour: int, minute: int) -> int:
    return (int(hour) * 60 + int(minute)) % 1440


def _hour_minute(total: int) -> tuple[int, int]:
    total %= 1440
    return total // 60, total % 60


def window_span_minutes() -> int:
    """Length of the owner's run window in minutes (23:00 → 11:00 = 720)."""
    return (_minutes_of(WINDOW_END_HOUR, 0) - _minutes_of(WINDOW_START_HOUR, 0)) % 1440


def gap_minutes(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Clock distance between two slots, midnight-safe (23:50 vs 00:10 → 20)."""
    d = abs(_minutes_of(*a) - _minutes_of(*b))
    return min(d, 1440 - d)


def slot_order(hour: int, minute: int) -> int:
    """Position inside one run window, so 01:00 sorts after 23:00."""
    return (_minutes_of(hour, minute) - _minutes_of(WINDOW_START_HOUR, 0)) % 1440


def in_window(hour: int, minute: int) -> bool:
    return slot_order(hour, minute) < window_span_minutes()


def plan_gap_minutes(project_count: int) -> float:
    """Minutes between consecutive machine-wide runs for `project_count` apps.

    This is the best achievable spacing: the window is divided evenly into
    `apps × RUNS_PER_DAY` slots and adjacent slots belong to different apps.
    """
    if project_count <= 0:
        return 0.0
    return window_span_minutes() / (project_count * RUNS_PER_DAY)


def plan_schedule(projects: list[str] | set[str]) -> dict[str, list[tuple[int, int]]]:
    """Deterministic two-runs-a-night plan, evenly interleaved across apps.

    Slots are computed from the sorted project list, so the same set of apps
    always yields the same schedule (idempotent re-runs, no drift). Consecutive
    slots go to different apps, and one app's runs are ~6 h apart.
    """
    names = sorted({p for p in projects if p})
    if not names:
        return {}
    total = len(names) * RUNS_PER_DAY
    start = _minutes_of(WINDOW_START_HOUR, 0)
    step = window_span_minutes() / total
    plan: dict[str, list[tuple[int, int]]] = {n: [] for n in names}
    for i in range(total):
        slot = _hour_minute(round(start + i * step))
        plan[names[i % len(names)]].append(slot)
    return plan


def explicit_slots(anchor: str) -> list[tuple[int, int]]:
    """`--at HH:MM` anchors the first run; the other follows 6 h later."""
    try:
        hh, mm = (int(x) for x in anchor.split(":"))
    except Exception:
        raise SystemExit(f"invalid --at {anchor!r}; expected HH:MM")
    step = window_span_minutes() // RUNS_PER_DAY
    return [_hour_minute(hh * 60 + mm + k * step) for k in range(RUNS_PER_DAY)]


def _job_data(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            data = plistlib.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _project_from_args(data: dict) -> str:
    """The project a plist runs, read from its nightly.sh command line."""
    text = " ".join(str(a) for a in (data.get("ProgramArguments") or []))
    m = re.search(r'nightly\.sh"?\s+([^\s>&"]+)', text)
    return m.group(1) if m else ""


def list_installed_jobs() -> list[dict]:
    """Every devteam plist with its project and ALL calendar slots."""
    out = []
    for p in sorted((Path.home() / "Library/LaunchAgents").glob(f"{LAUNCH_LABEL}*.plist")):
        data = _job_data(p)
        raw = data.get("StartCalendarInterval")
        items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
        slots: list[tuple[int, int]] = []
        for it in items:
            if isinstance(it, dict) and it.get("Hour") is not None:
                slots.append((int(it["Hour"]), int(it.get("Minute", 0))))
        slots.sort(key=lambda s: slot_order(*s))
        out.append({
            "label": str(data.get("Label") or p.stem),
            "path": p,
            "project": _project_from_args(data),
            "slots": slots,
        })
    return out


def list_installed_schedules() -> list[dict]:
    """All devteam slots (one row per run) — the clash source."""
    out = []
    for job in list_installed_jobs():
        for h, m in job["slots"]:
            out.append({"label": job["label"], "project": job["project"],
                        "hour": h, "minute": m})
    return out


def clashes(hour: int, minute: int, installed: list[dict] | None = None,
            gap: int = MIN_GAP_MIN, exclude: str | None = None) -> list[dict]:
    """Installed slots closer than `gap` minutes to (hour, minute).

    Returns them with the distance so a report can name the neighbour instead of
    saying "collides" — the useful fact is which app is too close and by how much.
    `exclude` is a label and drops every slot belonging to that app.
    """
    out = []
    for s in (installed if installed is not None else list_installed_schedules()):
        if exclude and s["label"] == exclude:
            continue
        d = gap_minutes((hour, minute), (s["hour"], s["minute"]))
        if d < gap:
            out.append({**s, "gap": d})
    return out


def project_label(project_name: str) -> str:
    """/per-project label for NEW installs (legacy com.devteam.nightly is
    preserved for the first project that ever installed it)."""
    slug = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")
    return f"{LAUNCH_LABEL}.{slug}"


def plist_path(project_name: str, label: str | None = None) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label or project_label(project_name)}.plist"


def is_project_scheduled(project_name: str) -> bool:
    """Returns True if this project has an installed LaunchAgent plist."""
    label = project_label(project_name)
    installed = {s["label"] for s in list_installed_schedules()}
    if label in installed:
        return True
    if LAUNCH_LABEL in installed and _loaded_job_plist(LAUNCH_LABEL):
        legacy = Path(_loaded_job_plist(LAUNCH_LABEL) or "").read_text(errors="ignore")
        if project_name.lower() in legacy.lower():
            return True
    library = Path.home() / "Library" / "LaunchAgents"
    return (library / f"{label}.plist").exists()


def is_project_loaded(project_name: str) -> bool:
    """Returns True if this project's launchd job is currently loaded in launchctl."""
    label = project_label(project_name)
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return True  # If launchctl cannot be queried, do not block
    return (label in r.stdout) or (LAUNCH_LABEL in r.stdout and is_project_scheduled(project_name))


def propose_schedule(project_name: str, pref_at: str | None = None,
                     projects: list[str] | None = None) -> list[tuple[int, int]]:
    """The two slots this project would get.

    With no `--at`, the project joins the machine-wide interleaved plan, so it
    lands as far from every other app's runs as the window allows. An explicit
    `--at` anchors its first run (the other follows 6 h later) and leaves the
    rest of the plan untouched.
    """
    if pref_at:
        return explicit_slots(pref_at)
    names = set(projects or [j["project"] for j in list_installed_jobs() if j["project"]])
    names.add(project_name)
    return plan_schedule(names)[project_name]


def apply_plan(projects: list[str] | set[str], install_missing: bool = True) -> dict[str, list[tuple[int, int]]]:
    """Install the interleaved plan for every given project (idempotent)."""
    plan = plan_schedule(projects)
    jobs = {j["project"]: j for j in list_installed_jobs() if j["project"]}
    for name, slots in plan.items():
        job = jobs.get(name)
        if job:
            if job["slots"] != slots:
                install_launchd(name, slots, label=job["label"])
        elif install_missing:
            install_launchd(name, slots)
    return plan


def rebalance(projects: list[str] | None = None) -> dict[str, list[tuple[int, int]]]:
    """Re-space every installed app into the current two-a-night plan."""
    names = projects or [j["project"] for j in list_installed_jobs() if j["project"]]
    return apply_plan(names)


def env_checks() -> list[str]:
    problems = []
    def which(cmd: str) -> str | None:
        r = subprocess.run(["/usr/bin/which", cmd], capture_output=True, text=True,
                           timeout=30)
        return None if r.returncode != 0 else r.stdout.strip()
    for cmd in ("git", "python3", "gh"):
        if not which(cmd):
            problems.append(f"'{cmd}' not found on PATH")
    r = run(["gh", "auth", "status"], None, timeout=45)
    if r.returncode != 0:
        problems.append("gh is not authenticated (needed for CI polling + issue filing). Run: gh auth login")
    # headless spawn is optional (native subagents are the primary path); if the
    # environment declares one, its binary must exist. No vendor is assumed.
    argv = agent_argv(role_cfg("developer"), "x") or []
    if argv and not which(argv[0]):
        problems.append(f"headless agent command '{argv[0]}' not found on PATH "
                        "(unset it to use native subagents)")
    return problems


def install_skill_symlinks() -> list[str]:
    """Global, project-independent: one skill, visible to every agent CLI.
    The tool-neutral location is the single target — no per-vendor paths."""
    made, skipped = [], []
    home = Path.home()
    targets = [home / ".agents" / "skills" / "dev-team"]
    src = TEAM_ROOT / "skills" / "dev-team"
    for dst in targets:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            skipped.append(str(dst))
            continue
        if dst.is_symlink():
            dst.unlink()
        dst.symlink_to(src)
        made.append(f"{dst} → {src}")
    return made, skipped


def _loaded_job_plist(label: str) -> str | None:
    """Path of the plist a loaded launchd job was bootstrapped from, or None."""
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None
    m = re.search(r"^\s*path = (.+?)\s*$", r.stdout, re.M)
    return m.group(1) if m else None


def install_launchd(project_name: str, at, label: str | None = None) -> Path:
    """Write a plist that fires `RUNS_PER_DAY` times a night.

    `at` is either "HH:MM" (one legacy slot) or a list of (hour, minute) slots.
    """
    if isinstance(at, str):
        slots = [tuple(int(x) for x in at.split(":"))]
    else:
        slots = [(int(h), int(m)) for h, m in at]
    slots = sorted(slots, key=lambda s: slot_order(*s))
    library = Path.home() / "Library" / "LaunchAgents"
    library.mkdir(parents=True, exist_ok=True)
    # A launchd job's Label must be UNIQUE per schedule: two plists sharing one
    # Label (the legacy com.devteam.nightly is the classic case) can only ever
    # have ONE loaded, so a second project's nightly silently never fires.
    # The file name is already per-project — make the Label inside match it.
    label = label or project_label(project_name)
    plist = library / f"{label}.plist"
    nightly = TEAM_ROOT / "scripts" / "nightly.sh"
    logs = TEAM_ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    body = {
        "Label": label,
        "ProgramArguments": [
            "/bin/bash", "-c",
            f'exec "{nightly}" {project_name} >> "{logs}/launchd.log" 2>&1',
        ],
        "WorkingDirectory": str(TEAM_ROOT),
        # An array is exactly how launchd expresses "several times a day".
        "StartCalendarInterval": [{"Hour": h, "Minute": m} for h, m in slots],
    }
    plist.write_bytes(plistlib.dumps(body))
    # Boot out our own label, plus any job loaded from THIS exact plist path
    # under a different (legacy) label — a re-install then migrates cleanly and
    # never leaves a duplicate job pointed at the same file.
    to_boot = {label}
    legacy_path = _loaded_job_plist(LAUNCH_LABEL)
    if LAUNCH_LABEL != label and legacy_path and \
            Path(legacy_path).resolve() == plist.resolve():
        to_boot.add(LAUNCH_LABEL)
    for lbl in to_boot:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{lbl}"],
                       capture_output=True, timeout=60)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and "in_progress" not in (r.stdout + r.stderr) and "Operation now" not in (r.stdout + r.stderr):
        # fall back to legacy load
        subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True, timeout=60)
    return plist


def uninstall_launchd(project_name: str | None = None) -> None:
    """Removes this project's nightly label (or all devteam labels if unspecified)."""
    targets = []
    if project_name:
        targets = [project_label(project_name)]
    else:
        targets = [p.stem for p in
                   (Path.home() / "Library/LaunchAgents").glob(f"{LAUNCH_LABEL}*.plist")]
    for label in targets:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                       capture_output=True, timeout=60)
        (Path.home() / "Library/LaunchAgents" / f"{label}.plist").unlink(missing_ok=True)
