"""Setup agent: installs everything the team needs to run daily and deliver to
the Play internal-testing track. Idempotent — safe to re-run.

What it sets up:
  1. environment checks (git, gh auth, python3, JDK/JBR, android sdk)
  2. project onboarding if missing (.pipeline/profile.json + state.json)
  3. launchd LaunchAgent running scripts/nightly.sh <project> daily at --at HH:MM
  4. global skill symlink so any agent CLI can operate the team (dev-team skill)
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .core import TEAM_ROOT, agent_argv, pipeline_dir, role_cfg, run

LAUNCH_LABEL = "com.devteam.nightly"
DEFAULT_AT = "23:10"
NIGHT_WINDOW_HOURS = [22, 23, 0, 1, 2, 3, 4, 5]  # owner's 10pm–6am IST window
GRID_MIN = [0, 10, 20, 30, 40, 50]

# Minimum distance between two nightly slots, in minutes.
#
# A slot is not a point in time. `scripts/nightly.sh` holds ONE machine-wide lock
# (`.nightly.lock`), so an app that fires while another run is alive is SKIPPED,
# not deferred — it loses that night entirely. The old rule only rejected two
# apps on the SAME minute, on a 10-minute grid, which put two of them 20 minutes
# apart: measured in logs/launchd.log, runs average 16–20 min and peak at 63,
# and the 22:20 app was skipped on 2026-11-03 AND 2026-11-04 while the 22:00 run
# was still going. An hour is the smallest gap that survives the observed tail
# (and still fits five apps inside the owner's 22:00–03:00 window).
MIN_GAP_MIN = 60


def _minutes_of(hour: int, minute: int) -> int:
    return (int(hour) * 60 + int(minute)) % 1440


def gap_minutes(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Clock distance between two slots, midnight-safe (23:50 vs 00:10 → 20)."""
    d = abs(_minutes_of(*a) - _minutes_of(*b))
    return min(d, 1440 - d)


def clashes(hour: int, minute: int, installed: list[dict] | None = None,
            gap: int = MIN_GAP_MIN, exclude: str | None = None) -> list[dict]:
    """Installed slots closer than `gap` minutes to (hour, minute).

    Returns them with the distance so a refusal can name the neighbour instead of
    saying "collides" — the useful fact is which app is too close and by how much.
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


def list_installed_schedules() -> list[dict]:
    """All devteam plists with their calendar times — the clash source."""
    import re as _re
    out = []
    for p in (Path.home() / "Library/LaunchAgents").glob(f"{LAUNCH_LABEL}*.plist"):
        text = p.read_text(errors="ignore")
        h = _re.search(r"<key>Hour</key><integer>(\d+)</integer>", text)
        m = _re.search(r"<key>Minute</key><integer>(\d+)</integer>", text)
        if h and m:
            out.append({"label": p.stem, "hour": int(h.group(1)),
                        "minute": int(m.group(1))})
    return out


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


def pick_free_slot(pref_at: str | None, exclude_project: str | None = None) -> tuple[int, int]:
    """Choose a slot with at least MIN_GAP_MIN minutes of clearance.

    An explicit pref_at wins when it clears every other app; otherwise the first
    free slot in the owner's night window on the 10-minute grid. Neither path may
    place an app close enough to lose its night to the machine-wide run lock — the
    gap is enforced, not advised (owner request 2026-11-05).
    """
    installed = list_installed_schedules()
    exclude = project_label(exclude_project) if exclude_project else None
    if pref_at:
        hh, mm = int(pref_at.split(":")[0]), int(pref_at.split(":")[1])
        close = clashes(hh, mm, installed, exclude=exclude)
        if close:
            near = ", ".join(
                f"{s['label']}@{s['hour']:02d}:{s['minute']:02d} ({s['gap']} min away)"
                for s in close)
            raise SystemExit(
                f"slot {hh:02d}:{mm:02d} is too close to {near}; nightly slots need "
                f">= {MIN_GAP_MIN} min between apps (one nightly holds a machine-wide "
                "lock, so an overlapping app is skipped, not deferred). Pick another --at.")
        return (hh, mm)
    for h in NIGHT_WINDOW_HOURS:
        for m in GRID_MIN:
            if not clashes(h, m, installed, exclude=exclude):
                return (h, m)
    raise SystemExit(f"no free night-window slot with >= {MIN_GAP_MIN} min spacing; "
                     "pick an explicit --at off-grid")


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


def install_launchd(project_name: str, at: str, label: str | None = None) -> Path:
    hh, mm = [int(x) for x in at.split(":")]
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
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>exec "{nightly}" {project_name} &gt;&gt; "{logs}/launchd.log" 2&gt;&amp;1</string>
  </array>
  <key>WorkingDirectory</key><string>{TEAM_ROOT}</string>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>{hh}</integer>
    <key>Minute</key><integer>{mm}</integer>
  </dict>
</dict></plist>"""
    plist.write_text(body)
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
    import re as _re
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
