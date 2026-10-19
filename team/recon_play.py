"""Play Store on an emulator — "install the app the way a user would".

The owner's flow for competitor recon: *search the app in the emulator's Play
Store, install it, use it.* This module does the parts that are deterministic —
find the emulator, launch Play, read the screen, recognise the states a human
must resolve, tap Install, wait for the install to land, then hand the APK to
the static pass.

The parts that are NOT deterministic come back as an **OWNER ASK** (exit code 4,
the same rc the rest of the team uses for "only a human can answer this"):

  * **not signed in** — a Play install needs the emulator signed into a Google
    account, and automated sign-in trips 2FA / device verification. So the human
    signs in on the emulator window, once, and re-runs. We detect the wall and
    ask; we never guess a password.
  * **a CAPTCHA / "verify it's you"** — same deal, same ask.
  * **the emulator has no Play Store** (an AOSP image, not a Play-enabled one) —
    ask for a Play-enabled AVD.

Why not just an agent with vision? Because everything up to the tap is knowable
from the accessibility tree and adb, and a report must not depend on a model
looking at pixels. So the tree does the work; the agent is only needed for what
the tree cannot say.

No vendor tool is required: plain `adb` (platform-tools) and the on-device
`uiautomator`. `android layout` is the agent's equivalent for its own
inspection, but nothing here depends on it.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from .core import run
from .emulator import ADB, running as _emulators

# rc=4 = "OWNER ASK" in this team (see skills/dev-team/SKILL.md step 3): the run
# did not fail, it is waiting on a human.
ASK_EXIT = 4

PLAY = "com.android.vending"
GMS = "com.google.android.gms"

# Buttons we will press without asking. Play labels the primary action one of
# these; a locale that is not English will not match, and the caller should fall
# back to `android layout` + a screenshot (the skill says so).
_BUTTONS = {
    "install": "install", "update": "update", "get": "get",
    "open": "open", "uninstall": "uninstall",
}
# Affirmative dialog buttons (first-run terms, "Accept & download", permission).
_CONFIRM = {"accept", "accept & download", "continue", "allow", "ok", "got it",
            "agree", "next", "install", "download", "yes", "no thanks"}
# Screen markers, matched case-insensitively against text + content-desc.
_SIGNED_OUT = ("sign in to find the latest", "sign in to your google account",
               "add your account", "add another account", "choose an account",
               "couldn't sign in", "use another account", "create account")
_VERIFY = ("not a robot", "captcha", "unusual traffic", "verify it's you",
           "confirm you're not a robot", "verify your identity")
_INCOMPATIBLE = ("isn't compatible", "is not compatible", "not compatible with",
                 "no eligible devices", "device not supported")
_NOT_FOUND = ("no results", "couldn't find", "no apps found", "not found",
              "try a different search")
_PROGRESS = ("installing", "downloading", "pending", "queued",
             "waiting to install", "%")


class Ask(Exception):
    """A question only a human can answer. The CLI prints it and exits 4."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def ask(message: str) -> "Ask":
    return Ask(message)


SIGNED_OUT_ASK = """\
The emulator ({serial}) is not signed into the Play Store, so it cannot install
anything yet.

Sign in ON THE EMULATOR WINDOW (it is visible on your screen): Google account
email, password, and your 2FA if asked. Then re-run the same command.

This is deliberately a human step — automated sign-in trips Google's device
verification, and a password is not something an agent should hold.
"""

NO_PLAY_ASK = """\
The device ({serial}) has no Play Store app (com.android.vending), so Play
installs are impossible on it.

Use a Play-enabled AVD — one created from a `google_apis_playstore` system
image. Your existing AVDs are listed by `android emulator list`; an AVD's image
is in ~/.android/avd/<name>.avd/config.ini (`image.sysdir.1` containing
`google_apis_playstore` means it has the Play Store).
"""

VERIFY_ASK = """\
Play is asking the emulator ({serial}) to verify the human — a CAPTCHA or an
identity check. That is yours to clear, and quickly, because it can time out.

Resolve it ON THE EMULATOR WINDOW, then re-run the same command.
"""


# --------------------------------------------------------------------------- #
# adb plumbing
# --------------------------------------------------------------------------- #

def shell(serial: str, *args: str, timeout: int = 60):
    return run([ADB, "-s", serial, "shell", *args], None, timeout=timeout)


def pick_device(device: str | None = None) -> str:
    """One emulator, chosen explicitly. Ambiguity is an ask, never a guess:
    installing the wrong app into the wrong device is not recoverable."""
    if device:
        return device
    live = _emulators()
    if not live:
        raise Ask(
            "No emulator is running.\n"
            "Start a Play-enabled one first, e.g.\n"
            "  android emulator start <avd>            # stays up across commands\n"
            "  # or, for one command:  with-emulator <avd> -- <command>\n"
            "then re-run. If you start it, stop it when you are done.")
    if len(live) > 1:
        names = "\n".join(f"  --device {e['serial']}   ({e.get('avd') or '?'})"
                          for e in live)
        raise Ask("More than one emulator is running — say which one:\n" + names)
    return live[0]["serial"]


def play_present(serial: str) -> bool:
    r = shell(serial, "pm", "list", "packages", PLAY, timeout=30)
    return f"package:{PLAY}" in (r.stdout or "")


def google_accounts(serial: str) -> list[str]:
    r = shell(serial, "dumpsys", "account", timeout=30)
    return sorted(set(re.findall(r"name=([^,\s}]+),\s*type=com\.google\b",
                                 r.stdout or "")))


def top_activity(serial: str) -> str:
    """'pkg/Class' of the resumed activity. Play's signed-out screen is
    `…finsky.unauthenticated.activity.UnauthenticatedMainActivity` — the single
    most precise signal that an install cannot work, so we read it directly."""
    r = shell(serial, "dumpsys", "activity", "activities", timeout=30)
    m = re.search(r"ResumedActivity:.*?\s([\w.]+)/([\w.$]+)", r.stdout or "")
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def play_state(serial: str) -> str:
    """'no-play' | 'signed-out' | 'ready'."""
    if not play_present(serial):
        return "no-play"
    if not google_accounts(serial) or "unauthenticated" in top_activity(serial).lower():
        return "signed-out"
    return "ready"


def installed_packages(serial: str) -> set[str]:
    r = shell(serial, "pm", "list", "packages", timeout=60)
    return {ln.split("package:", 1)[1].strip()
            for ln in (r.stdout or "").splitlines() if ln.startswith("package:")}


def installed(serial: str, package: str) -> bool:
    return package in installed_packages(serial)


def package_version(serial: str, package: str) -> dict:
    r = shell(serial, "dumpsys", "package", package, timeout=60)
    out = r.stdout or ""
    vc = re.search(r"versionCode=(\d+)", out)
    vn = re.search(r"versionName=(\S+)", out)
    return {"version_code": vc.group(1) if vc else None,
            "version_name": vn.group(1) if vn else None}


# --------------------------------------------------------------------------- #
# Play navigation + the accessibility tree
# --------------------------------------------------------------------------- #

def launch(serial: str, uri: str) -> bool:
    r = shell(serial, "am", "start", "-a", "android.intent.action.VIEW", "-d", uri,
              timeout=30)
    return "Error" not in (r.stdout or "") and (r.returncode == 0)


def open_details(serial: str, package: str) -> bool:
    return launch(serial, f"market://details?id={package}")


def open_search(serial: str, term: str) -> bool:
    return launch(serial, f"market://search?q={quote(term)}")


def _parse_ui(xml_text: str) -> list[dict]:
    nodes: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return nodes
    for n in root.iter("node"):
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds") or "")
        cx = cy = None
        if m:
            x1, y1, x2, y2 = (int(x) for x in m.groups())
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        nodes.append({
            "text": (n.get("text") or "").strip(),
            "desc": (n.get("content-desc") or "").strip(),
            "res": (n.get("resource-id") or "").strip(),
            "cls": (n.get("class") or "").strip(),
            "clickable": n.get("clickable") == "true",
            "x": cx, "y": cy,
        })
    return nodes


def ui(serial: str, retries: int = 3) -> list[dict]:
    """The current screen as a flat node list. `uiautomator` occasionally cannot
    settle (an animation, a WebView) and returns nothing — retry rather than
    declaring the app empty."""
    for _ in range(retries):
        r = shell(serial, "uiautomator", "dump", "/sdcard/recon_ui.xml", timeout=60)
        if r.returncode == 0 and "dumped to" in (r.stdout or "").lower():
            c = shell(serial, "cat", "/sdcard/recon_ui.xml", timeout=60)
            nodes = _parse_ui(c.stdout or "")
            if nodes:
                return nodes
        time.sleep(1.5)
    return []


def _label(node: dict) -> str:
    return (node["text"] or node["desc"]).strip()


def _find_button(nodes: list[dict], wanted: set[str]) -> dict | None:
    """First node whose exact label is one of `wanted`.

    Play often renders a button's text as a NON-clickable `View`/`TextView` with
    the tap target on an anonymous ancestor — the label's bounds are still inside
    the button, and `input tap` hit-tests by coordinate, so a clickable flag is
    not required. Prefer a genuinely clickable node when there is one; verified
    live against the Play details page, where the `Install` node is not clickable
    and a centre tap installs the app anyway.
    """
    fallback = None
    for n in nodes:
        if n["x"] is None or _label(n).lower() not in wanted:
            continue
        if n["clickable"]:
            return n
        if fallback is None:
            fallback = n
    return fallback


def scan(nodes: list[dict]) -> dict:
    """Classify a Play screen into {state, button}.

    Order is the whole point: a sign-out wall or a CAPTCHA outranks any button
    we might otherwise tap, because tapping 'Install' through them is how a
    recon silently records nothing.
    """
    blob = " \u241f ".join(
        f"{n['text']} {n['desc']}".lower() for n in nodes)
    if any(m in blob for m in _SIGNED_OUT):
        return {"state": "signed-out", "button": None}
    if any(m in blob for m in _VERIFY):
        return {"state": "verify", "button": None}
    if any(m in blob for m in _INCOMPATIBLE):
        return {"state": "incompatible", "button": None}
    button = _find_button(nodes, set(_BUTTONS))
    if button:
        return {"state": _BUTTONS[_label(button).lower()], "button": button}
    confirm = _find_button(nodes, _CONFIRM)
    if confirm:
        return {"state": "confirm", "button": confirm}
    if any(m in blob for m in _NOT_FOUND):
        return {"state": "not-found", "button": None}
    if any(m in blob for m in _PROGRESS):
        return {"state": "progress", "button": None}
    return {"state": "unknown", "button": None}


def tap(serial: str, node: dict) -> None:
    if node.get("x") is not None:
        shell(serial, "input", "tap", str(node["x"]), str(node["y"]), timeout=30)


def screen_rows(serial: str, limit: int = 12) -> list[str]:
    """Visible text rows, for a human choosing manually (search results)."""
    rows = [f"{_label(n)}   @[{n['x']},{n['y']}]" if n["x"] is not None else _label(n)
            for n in ui(serial) if _label(n)]
    return rows[:limit]


# --------------------------------------------------------------------------- #
# install → pull → static pass
# --------------------------------------------------------------------------- #

def play_install(project: Path, package: str, device: str | None = None,
                 force: bool = False, timeout: int = 900, log=None) -> dict:
    """Install `package` from the Play Store on the emulator, then run the
    static pass on what landed. Raises Ask when a human must act first."""
    from . import recon

    say = log or (lambda *_a, **_k: None)
    serial = pick_device(device)

    state = play_state(serial)
    if state == "no-play":
        raise Ask(NO_PLAY_ASK.format(serial=serial))
    if state == "signed-out":
        raise Ask(SIGNED_OUT_ASK.format(serial=serial))

    already = installed(serial, package)
    if not open_details(serial, package):
        raise SystemExit(f"could not open Play details for {package} "
                         f"on {serial} (is Play enabled on this image?)")
    say(f"play: opened {package} on {serial}"
        f"{' (already installed — checking for an update)' if already else ''}")

    deadline = time.monotonic() + timeout
    last, prev_sig, stuck = "", None, 0
    while time.monotonic() < deadline:
        nodes = ui(serial)
        verdict = scan(nodes)
        st, btn = verdict["state"], verdict["button"]
        if st != last:
            say(f"play: screen → {st}")
            last = st
        # Nothing moved for several scans and we still do not understand the
        # screen: stop and ask rather than tap blindly or spin until timeout.
        sig = tuple(_label(n) for n in nodes if _label(n))[:14]
        stuck = stuck + 1 if sig == prev_sig else 0
        prev_sig = sig
        if st == "unknown" and stuck >= 5:
            rows = "\n".join(f"    {r}" for r in sig) or "    (the UI tree was empty)"
            raise Ask(
                f"Play is on a screen recon does not recognise, so it will not "
                f"guess a tap. On {serial} right now:\n{rows}\n"
                f"Clear it on the emulator window, then re-run.")
        if st == "signed-out":
            raise Ask(SIGNED_OUT_ASK.format(serial=serial))
        if st == "verify":
            raise Ask(VERIFY_ASK.format(serial=serial))
        if st == "incompatible":
            raise SystemExit(
                f"Play says {package} is not compatible with {serial}'s image "
                f"— pick another device/ABI, or use `recon static <apk>`.")
        if st == "not-found":
            raise SystemExit(f"Play has no app for {package} on {serial}.")
        if st in ("install", "update") and btn:
            tap(serial, btn)
            say(f"play: tapped {_label(btn)}")
            time.sleep(4)
            continue
        if st == "confirm" and btn:
            tap(serial, btn)
            time.sleep(3)
            continue
        if st in ("open", "uninstall"):
            break  # installed and up to date
        if st in ("progress", "unknown"):
            time.sleep(3)
            continue
        time.sleep(2)

    if not installed(serial, package):
        raise Ask(
            f"Play did not finish installing {package} on {serial} within "
            f"{timeout}s.\nLook at the emulator: it is usually a sign-in, a "
            f"CAPTCHA, a Play terms dialog, or a slow network. Resolve it, then "
            f"re-run.")
    ver = package_version(serial, package)
    say(f"play: installed {package} "
        f"v{ver.get('version_name') or '?'} ({ver.get('version_code') or '?'})")

    apk = recon.pull_from_device(package, device=serial)
    report = recon.static_pass(project, apk, source="play", force=force, log=say)
    report["installed_from"] = {"via": "play", "device": serial, **ver}
    from .core import save_json
    save_json(recon.report_path(project, report["package"]), report)
    return report
