"""Emulator lifecycle — the deterministic half of "if you start it, stop it".

The rule itself is prose an agent reads (the android-cli skill). This module is
what makes the rule hold when the agent forgets: a run records which emulators
were ALREADY alive before it started, and the run end stops anything that
appeared since.

The baseline is not ceremony. A leaked emulator and an emulator the owner
opened on purpose are indistinguishable from `adb devices` alone — stopping by
"whatever is running" would kill the owner's session. Comparing against a
start-of-run snapshot is the only thing that tells them apart.

Behaviour on a leak is WARN-ONLY (owner decision): the orphan is stopped and
named in the run log, the run still succeeds. Teardown is hygiene, not a gate.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from .core import pipeline_dir

# adb is looked up on PATH first (Android Studio ships it into PATH on macOS via
# the SDK); the conventional SDK location is the fallback.
ADB = shutil.which("adb") or str(Path.home() / "Library/Android/sdk/platform-tools/adb")
_SERIAL_RE = re.compile(r"^(emulator-\d+)\s")
_TIMEOUT = 30


def adb_available() -> bool:
    return bool(shutil.which("adb")) or Path(ADB).exists()


def _adb(args: list[str], timeout: int = _TIMEOUT) -> subprocess.CompletedProcess | None:
    """Never raises: an absent or wedged adb is not a pipeline failure."""
    try:
        return subprocess.run([ADB, *args], capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def running() -> list[dict]:
    """Booted emulators as [{"serial": ..., "avd": ...}]. Empty list when adb
    is missing — callers must treat "no data" as "nothing to do", not as an
    error (an agent-only machine still runs the pipeline)."""
    r = _adb(["devices"])
    if r is None or r.returncode != 0:
        return []
    found: list[dict] = []
    for line in r.stdout.splitlines():
        m = _SERIAL_RE.match(line.strip())
        if not m:
            continue
        serial = m.group(1)
        avd = ""
        name = _adb(["-s", serial, "emu", "avd", "name"], timeout=15)
        if name is not None and name.stdout.strip():
            avd = name.stdout.strip().splitlines()[0].strip()
        found.append({"serial": serial, "avd": avd})
    return found


def _label(entry: dict) -> str:
    return f"{entry['avd']} ({entry['serial']})" if entry.get("avd") else entry["serial"]


def _emulator_process_alive() -> bool | None:
    """True/False from the PROCESS TABLE, or None when we cannot tell.

    Deliberately not adb: `running()` sees only BOOTED devices, so a crashed or
    half-exited emulator is invisible there while still holding the AVD lock
    (2026-10-25 — the next boot died with `FATAL | Running multiple emulators
    with the same AVD`). None means "no pgrep on this machine": callers must read
    that as "assume alive" and change nothing, because unlinking a live
    emulator's lock is worse than a failed boot."""
    pg = shutil.which("pgrep")
    if not pg:
        return None
    try:
        r = subprocess.run([pg, "-f", "qemu-system|/emulator/emulator"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode == 0:
        return True
    if r.returncode == 1:
        return False
    return None


def clear_stale_locks(avd: str, log=None) -> list[str]:
    """Remove leftover `*.lock` files for `avd`, but ONLY when no emulator
    process is alive.

    A boot that is about to start is exactly when this is safe and useful: the
    caller has already established nothing is booted, so a lock file can only be
    debris from a dead instance. Guarded on the process table (not adb) and
    skipped entirely when liveness cannot be determined."""
    if _emulator_process_alive() is not False:
        return []
    d = Path.home() / ".android" / "avd" / f"{avd}.avd"
    if not d.is_dir():
        return []
    cleared: list[str] = []
    for f in sorted(d.glob("*.lock")):
        try:
            f.unlink()
            cleared.append(f.name)
        except OSError:
            pass
    if cleared and log:
        log(f"~ emulator: cleared stale lock(s) left by a dead emulator: "
            f"{', '.join(cleared)}")
    return cleared


def emulator_bin() -> str:
    return (shutil.which("emulator")
            or str(Path.home() / "Library/Android/sdk/emulator/emulator"))


def avds() -> list[str]:
    """Installed AVD names, or [] when the emulator binary is absent."""
    try:
        r = subprocess.run([emulator_bin(), "-list-avds"], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]


def booted(serial: str, timeout: int = 240) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = _adb(["-s", serial, "shell", "getprop", "sys.boot_completed"], timeout=15)
        if r is not None and (r.stdout or "").strip() == "1":
            return True
        time.sleep(2.0)
    return False


def ensure_device(avd: str | None = None, log=None, timeout: int = 240) -> tuple[str, bool]:
    """A booted emulator — the existing one, or one this call starts.

    Returns `(serial, started_by_us)`. The caller MUST stop it when the second
    value is True: an emulator the agent started is the agent's to tear down, and
    a pre-existing one is never touched (the owner may have it open on purpose).
    """
    live = running()
    if live:
        if log:
            log(f"~ emulator: reusing {_label(live[0])}")
        return live[0]["serial"], False
    name = (avd or "").strip()
    if not name:
        installed = avds()
        if not installed:
            raise SystemExit("no AVD installed — create one (`android emulator create`) "
                             "or pass --avd <name>")
        name = installed[0]
    if not adb_available():
        raise SystemExit("adb is not available — install Android platform-tools")
    clear_stale_locks(name, log)
    if log:
        log(f"~ emulator: starting {name} (headless)")
    subprocess.Popen(
        [emulator_bin(), "-avd", name, "-no-window", "-no-audio", "-no-boot-anim",
         "-no-snapshot-save", "-gpu", "swiftshader_indirect", "-netdelay", "none",
         "-netspeed", "full"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    serial = ""
    while time.monotonic() < deadline:
        live = running()
        if live:
            serial = live[0]["serial"]
            if booted(serial, timeout=max(10, int(deadline - time.monotonic()))):
                if log:
                    log(f"~ emulator: {name} booted as {serial}")
                return serial, True
            break
        time.sleep(2.0)
    raise SystemExit(f"emulator {name} did not boot within {timeout}s")


def baseline_path(project: Path) -> Path:
    return pipeline_dir(project) / "emulators.baseline.json"


def record_baseline(project: Path, log=None, reset: bool = False) -> list[dict]:
    """Call at RUN START. First snapshot wins: a later call inside the same run
    must not adopt the run's own emulator as 'pre-existing'. Pass reset=True at
    a genuine run entry point (nightly, run-headless, run-orchestrator) so a
    stale baseline from an earlier run cannot shield an orphan forever."""
    p = baseline_path(project)
    if p.exists() and not reset:
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass  # corrupt baseline must never block a run — re-record instead
    live = running()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(live, indent=2) + "\n")
    if log:
        names = ", ".join(_label(e) for e in live) or "none"
        log(f"emulator baseline recorded: {names}")
    return live


def stop(serial: str, wait_s: int = 20) -> bool:
    """Stop one emulator by serial and WAIT for it to actually leave adb.

    `adb emu kill` is asynchronous: it returns 0 while the device is still
    listed for several seconds (observed live). Reporting success off the exit
    code alone would be an unverifiable claim, and a second check in the same
    run would re-flag the same orphan. `adb emu kill` is used over
    `android emulator stop` because the serial is already known.
    """
    r = _adb(["-s", serial, "emu", "kill"])
    if r is None or r.returncode != 0:
        return False
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not any(e["serial"] == serial for e in running()):
            return True
        time.sleep(1.0)
    return False


def leak_check(project: Path, log=None, stop_leaked: bool = True) -> tuple[bool, str]:
    """Call at RUN END. Returns (clean, message) and emits the message through
    `log` when one is given (every caller records the verdict, not just the
    human-facing CLI).

    Orphans = running now minus the start-of-run baseline. Each orphan is
    stopped (when stop_leaked) and named. The baseline file is left in place so
    a second check inside the same run still knows what was pre-existing —
    the next run's reset=True overwrites it.
    """
    clean, msg = _leak_verdict(project, stop_leaked)
    if log:
        log(msg)
    return clean, msg


def _leak_verdict(project: Path, stop_leaked: bool) -> tuple[bool, str]:
    p = baseline_path(project)
    if not p.exists():
        return True, "emulator: no baseline for this run — nothing to check"
    try:
        baseline = {e["serial"] for e in json.loads(p.read_text())}
    except (json.JSONDecodeError, KeyError, TypeError):
        return True, "emulator: baseline unreadable — nothing to check"

    orphans = [e for e in running() if e["serial"] not in baseline]
    if not orphans:
        return True, "emulator: none leaked"

    stopped, kept = [], []
    for e in orphans:
        if stop_leaked and stop(e["serial"]):
            stopped.append(_label(e))
        else:
            kept.append(_label(e))
    parts = []
    if stopped:
        parts.append("stopped leaked emulator(s): " + ", ".join(stopped))
    if kept:
        parts.append("STILL RUNNING (stop manually): " + ", ".join(kept))
    return False, "emulator: " + "; ".join(parts)


def require_device(avd: str | None = None, log=None, purpose: str = "this pass"
                   ) -> tuple[str, bool]:
    """A device, or a failure that says why — never a silent downgrade.

    UX evidence is only evidence if it came off a device: a render of a composable
    proves the code compiles a picture, not that the screen works with a keyboard,
    insets, a real network and a real navigation stack. So `ux-audit`, `ux-video`
    and `ux-flow` REQUIRE an emulator or a connected device, and this is the one
    place that decides it.

    Integration tests are the opposite: they must run OFF a device (JVM /
    Robolectric), because a gate that needs a boot is a gate that fails for
    infrastructure reasons (owner policy 2026-11-08).

    Returns (serial, started_by_us). The caller MUST stop it when the second value
    is True; a device this call did not start is never touched.
    """
    serial, started = ensure_device(avd=avd, log=log)
    if log:
        kind = "connected device" if not serial.startswith("emulator-") else "emulator"
        log(f"~ device: {purpose} runs on {kind} {serial}"
            + (" (started by this pass)" if started else " (already running)"))
    return serial, started
