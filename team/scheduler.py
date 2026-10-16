"""Setup agent: installs everything the team needs to run daily and deliver to
the Play internal-testing track. Idempotent — safe to re-run.

What it sets up:
  1. environment checks (git, gh auth, python3, opencode, JDK/JBR, android sdk)
  2. project onboarding if missing (.pipeline/profile.json + state.json)
  3. launchd LaunchAgent running scripts/nightly.sh <project> daily at --at HH:MM
  4. global skill symlink so any agent CLI can operate the team (dev-team skill)
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .core import TEAM_ROOT, pipeline_dir, run

LAUNCH_LABEL = "com.devteam.nightly"
DEFAULT_AT = "23:10"
NIGHT_WINDOW_HOURS = [22, 23, 0, 1, 2, 3, 4, 5]  # owner's 10pm–6am IST window
GRID_MIN = [0, 10, 20, 30, 40, 50]


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


def pick_free_slot(pref_at: str | None, exclude_project: str | None = None) -> tuple[int, int]:
    """Choose a slot not colliding with any installed devteam schedule.
    Explicit pref_at wins if free; otherwise first free slot in the owner's
    night window on a 10-minute grid."""
    want = (int(pref_at.split(":")[0]), int(pref_at.split(":")[1])) if pref_at else None
    installed = {(s["hour"], s["minute"]) for s in list_installed_schedules()}
    if want and want not in installed:
        return want
    for h in NIGHT_WINDOW_HOURS:
        for m in GRID_MIN:
            if (h, m) not in installed:
                return (h, m)
    raise SystemExit("no free night-window slot; pick an explicit --at off-grid")


def env_checks() -> list[str]:
    problems = []
    def which(cmd: str) -> str | None:
        r = run(["/usr/bin/which", cmd], None, timeout=30) if False else subprocess.run(
            ["/usr/bin/which", cmd], capture_output=True, text=True, timeout=30)
        return None if r.returncode != 0 else r.stdout.strip()
    for cmd in ("git", "python3", "gh", "opencode"):
        if not which(cmd):
            problems.append(f"'{cmd}' not found on PATH")
    r = run(["gh", "auth", "status"], None, timeout=45)
    if r.returncode != 0:
        problems.append("gh is not authenticated (needed for CI polling + issue filing). Run: gh auth login")
    if not which("opencode"):
        problems.append("opencode CLI not found — developer runtime default will not launch")
    r = run(["gh", "auth", "status"], None, timeout=45)
    return problems


def install_skill_symlinks() -> list[str]:
    """Global, project-independent: one skill, visible to every agent CLI."""
    made, skipped = [], []
    home = Path.home()
    targets = [home / ".agents" / "skills" / "dev-team",
               home / ".claude" / "skills" / "dev-team",
               home / ".opencode" / "skill" / "dev-team"]
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


def install_launchd(project_name: str, at: str, label: str | None = None) -> Path:
    hh, mm = [int(x) for x in at.split(":")]
    library = Path.home() / "Library" / "LaunchAgents"
    library.mkdir(parents=True, exist_ok=True)
    plist = library / f"{(label or project_label(project_name))}.plist"
    nightly = TEAM_ROOT / "scripts" / "nightly.sh"
    logs = TEAM_ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LAUNCH_LABEL}</string>
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
    label = plist.stem
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
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
