"""Video pass for the UX audit — the screens in motion, and how they read.

WHY THIS EXISTS
---------------
`ux-audit` judges one STILL per screen. A still cannot show dead time, jank, an
abrupt transition, a spinner that flashes for 40 ms or a blank frame that sits
for three seconds — and it cannot show that a tap did nothing. Those are exactly
the failures a user calls "slow" or "broken" while the layout looks fine. The pass
therefore judges BOTH: every screen that appears (hierarchy, spacing, contrast,
affordance, copy, consistency) and the motion between them.

A FLOW is a short, declared journey (`docs/ux-flows.json`, project-owned), driven
on a booted emulator while `adb shell screenrecord` records it. Frames are
sampled on a fixed cadence and tiled into ONE contact sheet, and the same vision
model that judges stills reacts to the sequence. The gripes land in
`.pipeline/ux_audit.json` on the flow's declared screen, so the planner reads
motion and layout as one inventory rather than two.

Rules that keep it evidence, not entertainment:
  * the recording is built and installed from the DELIVERY ref (`origin/main`),
    never from the owner's checkout — a video describes the app CI ships;
  * a flow is skipped, never invented: no declared flow, no recording;
  * frames are sampled at SAMPLE_EVERY_S and the cadence is stored with the
    verdict, so a claim ("the spinner sits for four seconds") is reproducible;
  * a recording older than the screen's own sources is STALE and is refused —
    the same rule stills already follow;
  * a flow is REPLAYABLE: `"fresh": true` on a first-run journey clears the app's
    data before launch (`install -r` keeps it, so without this the flow is
    recordable once per device and fails on its first step thereafter); a flow
    that needs seeded state simply omits the key;
  * an emulator this command started is stopped before it returns.

Nothing here is committed: `.pipeline/` is machine-local, by contract.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from .core import now_iso, pipeline_dir, save_json

FLOWS_REL = "docs/ux-flows.json"
VIDEO_DIR = "ux_videos"
MANIFEST_NAME = "manifest.json"
MAX_SECONDS = 180
DEFAULT_SECONDS = 20
SAMPLE_EVERY_S = 2.0
DEAD_TAP_S = 1.1          # how long a tap gets to change the screen
GRID_COLS, GRID_ROWS = 3, 2
FRAME_W = 380
RUBRIC = "ux-video-v2"

PROMPT = """You are judging ONE interaction in a mobile app from {evidence}.
The interaction is {seconds} seconds long and was driven like this:
{actions}
{sampling}

Judge TWO things: how every VISIBLE SCREEN reads, and how the flow FEELS in motion.

Screen UI/UX — judge each screen that appears, not only what moves:
- hierarchy: is the most important thing the most prominent? can I tell what this
  screen wants me to do?
- layout: spacing, alignment, dead whitespace, content colliding or clipped, a
  row that wraps where its neighbours do not, content cut by an edge or a bar
- readability: contrast, type size, labels that truncate or run off
- affordance: a control that does not look tappable, or looks tappable but is a
  status; unexplained icons, dots or badges; an on/off state you cannot read
- content: marketing fluff or jargon where a plain sentence belongs; a label that
  does not say what happens; an option that looks missing
- consistency with the screens around it (chrome, padding, palette, icon style)

Motion — what changes between panels:
- dead time: a spinner, blank surface or unchanged screen that sits too long
- jank: a visible stutter, jump, or scroll that snaps
- abrupt transitions: a hard cut where the app clearly changes screen state, or
  content that appears before the chrome around it
- a tap that visibly produced NO change in the following panel (a dead control)
- loading/error states that flash, truncate, or cover the thing they describe

Screen: {surface} · flow: {flow}

Return ONLY this JSON:
{{
  "reactions": [
    {{"at": "00:02", "line": "what a first-time user would think out loud",
      "kind": "slow|jank|abrupt|blank|clear|premium"}}
  ],
  "defects": [
    {{"line": "exact description of the UI, UX or motion defect",
      "element": "the control or region", "kind": "ui|ux|motion",
      "severity": "P1_DEGRADED|P2_POLISH"}}
  ],
  "premium": ["what genuinely looks or feels fast, smooth or well-crafted"],
  "verdict": "one sentence on whether this flow and its screens feel production-grade"
}}

Rules:
- Name the screen or the panels a defect is on ("on the settings screen ...",
  "between panel 2 and 3 ..."). A screen that is only seen for a moment still counts.
- Never invent what you cannot see: if two panels are identical, say so or stay
  silent. Do not guess the app's name or brand.
- A control that was tapped with no visible response in the next panel is P1.
- A blank or unstyled frame that persists across panels is P1; a single frame is P2.
- A layout or readability problem that a user would hit on every visit is P1;
  cosmetic niceties are P2. Leave the lists empty when the flow is solid.
"""


# --------------------------------------------------------------------------- #
# the project-owned flow spec
# --------------------------------------------------------------------------- #
def spec_path(project: Path) -> Path:
    return project / FLOWS_REL


def load_spec(project: Path) -> dict:
    p = spec_path(project)
    if not p.is_file():
        return {"flows": []}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {"flows": []}
    return data if isinstance(data, dict) else {"flows": []}


def declared(project: Path) -> list[dict]:
    return [f for f in (load_spec(project).get("flows") or [])
            if isinstance(f, dict) and str(f.get("id") or "").strip()]


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", str(text)).strip("-") or "flow"


def video_dir(project: Path) -> Path:
    return pipeline_dir(project) / VIDEO_DIR


def mp4_path(project: Path, flow_id: str) -> Path:
    return video_dir(project) / f"{_slug(flow_id)}.mp4"


def sheet_path(project: Path, flow_id: str) -> Path:
    return video_dir(project) / f"{_slug(flow_id)}-sheet.png"


def manifest_path(project: Path) -> Path:
    return video_dir(project) / MANIFEST_NAME


def load_manifest(project: Path) -> dict:
    p = manifest_path(project)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def save_manifest(project: Path, data: dict) -> None:
    video_dir(project).mkdir(parents=True, exist_ok=True)
    save_json(manifest_path(project), data)


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# the plan — what a recording/reaction is owed, and what is still evidence
# --------------------------------------------------------------------------- #
def _surfaces(project: Path) -> dict[str, dict]:
    from . import ux_audit
    return {s["label"].strip().lower(): s for s in ux_audit.surfaces(project)}


def _newer_than_inputs(ms: float, inputs: list[str], spec: Path) -> list[str]:
    newer = []
    paths = [Path(p) for p in inputs] + [spec]
    for p in paths:
        try:
            if p.is_file() and p.stat().st_mtime > ms:
                newer.append(str(p))
        except OSError:
            continue
    return newer


def plan(project: Path) -> dict:
    """Which flows need a recording or a reaction — decided from files, no model."""
    surfaces = _surfaces(project)
    man = load_manifest(project)
    spec = spec_path(project)
    rows = []
    for flow in declared(project):
        fid = str(flow["id"])
        label = str(flow.get("surface") or "")
        surface = surfaces.get(label.strip().lower())
        mp4 = mp4_path(project, fid)
        prev = man.get(fid) or {}
        sha = _sha(mp4) if mp4.is_file() else ""
        newer = (_newer_than_inputs(mp4.stat().st_mtime, (surface or {}).get("inputs") or [], spec)
                 if sha else [])
        if not surface:
            status = "no-surface"
        elif not sha:
            status = "new"
        elif newer:
            status = "stale"
        elif prev.get("rubric") != RUBRIC:
            # The question changed shape: a verdict produced by an older rubric is
            # not comparable, so the flow is owed a fresh reaction (the recording
            # is reused — only the judgement is redone).
            status = "unreacted"
        elif prev.get("video_sha") != sha or not prev.get("reacted_at"):
            status = "unreacted"
        else:
            status = "unchanged"
        rows.append({
            "id": fid,
            "surface": label,
            "steps": len(flow.get("steps") or []),
            "seconds": int(flow.get("seconds") or DEFAULT_SECONDS),
            "status": status,
            "mp4": str(mp4) if sha else "",
            "sheet": str(sheet_path(project, fid)) if (video_dir(project) / f"{_slug(fid)}-sheet.png").exists() else "",
            "stale_inputs": newer[:5],
            "reacted_at": prev.get("reacted_at", ""),
            "verdict": prev.get("verdict", ""),
        })
    counts = {k: sum(1 for r in rows if r["status"] == k)
              for k in ("new", "stale", "unreacted", "unchanged", "no-surface")}
    return {"rows": rows, "counts": counts, "rubric": RUBRIC}


def report(p: dict) -> str:
    c = p["counts"]
    lines = [f"UX VIDEO — {c['new']} unrecorded · {c['stale']} stale · "
             f"{c['unreacted']} unreacted · {c['unchanged']} current"
             + (f" · {c['no-surface']} without a declared screen" if c.get("no-surface") else "")]
    for r in p["rows"]:
        if r["status"] == "unchanged":
            continue
        extra = ""
        if r["status"] == "no-surface":
            extra = " — declare it in profile['targets'] first"
        elif r["status"] == "stale":
            extra = f" — newer source: {Path(r['stale_inputs'][0]).name}" if r["stale_inputs"] else ""
        lines.append(f"  · {r['status']:10} {r['id'][:34]:34} surface={r['surface'] or '—'}"
                     f" · {r['steps']} step(s) · {r['seconds']}s{extra}")
    if not p["rows"]:
        lines.append("  no flows declared — add journeys to " + FLOWS_REL)
    lines.append("  record/re-act: `devteam ux-video <project>`")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the device: build the delivery ref, install, drive, record
# --------------------------------------------------------------------------- #
def _adb(serial: str, args: list[str], timeout: int = 60, binary: bool = False):
    from .emulator import ADB
    return subprocess.run([ADB, "-s", serial, *args],
                          capture_output=True, text=not binary, timeout=timeout)


def package_name(project: Path) -> str:
    """The applicationId, from the flow spec, the build file, then the manifest."""
    spec = load_spec(project)
    if str(spec.get("package") or "").strip():
        return str(spec["package"]).strip()
    from . import storefront_preview as sp
    try:
        module = sp.find_compose_module(project)
    except SystemExit:
        module = project / "app"
    for build in (module / "build.gradle.kts", module / "build.gradle"):
        if not build.is_file():
            continue
        text = build.read_text(errors="ignore")
        m = re.search(r'applicationId\s*=?\s*["\']([\w.]+)["\']', text)
        if m:
            return m.group(1)
        m = re.search(r'namespace\s*=?\s*["\']([\w.]+)["\']', text)
        if m:
            return m.group(1)
    raise SystemExit("cannot determine the app id — set \"package\" in " + FLOWS_REL)


def build_and_install(project: Path, serial: str, log=None) -> dict:
    """Build the debug APK from the DELIVERY tree and install it on the device."""
    from . import scan
    from . import storefront_preview as sp

    tree = scan.origin_tree(project, log)
    module = sp.find_compose_module(tree)
    gw = tree / "gradlew"
    if not gw.is_file():
        gw = Path(shutil.which("gradle") or "")
        if not str(gw) or not gw.is_file():
            raise SystemExit("no gradlew in the delivery tree and no gradle on PATH")
    cap = sp.variant_cap(project)
    variant = f"{cap}Debug" if cap else "Debug"
    task = f":{module.name}:assemble{variant}"
    if log:
        log(f"~ ux-video: building {task} from the delivery tree")
    try:
        r = subprocess.run([str(gw), task, "--no-configuration-cache", "--console=plain", "-q"],
                           cwd=tree, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"{task} timed out after 1800s")
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        raise SystemExit(f"{task} failed: " + (tail[-1] if tail else "unknown"))
    apks = sorted(module.glob(f"build/outputs/apk/*/{'*'}{variant.lower()}*.apk"))
    if not apks:
        apks = sorted(module.glob("build/outputs/apk/**/*.apk"))
    if not apks:
        raise SystemExit(f"no APK produced by {task}")
    apk = apks[-1]
    if log:
        log(f"~ ux-video: installing {apk.name}")
    res = _adb(serial, ["install", "-r", "-d", str(apk)], timeout=300)
    if res.returncode != 0:
        out = (res.stdout or "") + (res.stderr or "")
        raise SystemExit("adb install failed: " + out.strip()[-500:])
    return {"apk": str(apk), "tree": str(tree), "task": task}


def _ui_xml(serial: str) -> str:
    _adb(serial, ["shell", "uiautomator", "dump", "/sdcard/devteam-ux-ui.xml"], timeout=45)
    r = _adb(serial, ["exec-out", "cat", "/sdcard/devteam-ux-ui.xml"], timeout=45)
    return (r.stdout or "").decode("utf-8", "replace") if isinstance(r.stdout, bytes) else (r.stdout or "")


def _node_center(xml: str, needle: str) -> tuple[int, int] | None:
    """Center of the BEST node matching `needle` — exact beats prefix beats substring.

    Compose does not reliably mark semantics nodes `clickable` in a uiautomator
    dump (the FAB and its label both report false), so a plain "first node that
    contains the words" match taps the wrong thing: 'New' matched the empty-state
    sentence "Tap + NEW AUDIT to create…" and the flow silently did nothing.
    Exact match first, then prefix, then substring; within a tier a clickable
    node wins, then the smallest (most specific) label.
    """
    want = re.sub(r"\s+", " ", needle.strip().lower())
    if not want:
        return None
    best: tuple | None = None
    for m in re.finditer(r"<node\b[^>]*>", xml):
        attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', m.group(0)))
        b = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrs.get("bounds", ""))
        if not b:
            continue
        x1, y1, x2, y2 = (int(v) for v in b.groups())
        area = max(1, (x2 - x1) * (y2 - y1))
        clickable = attrs.get("clickable") == "true"
        for raw in (attrs.get("text", ""), attrs.get("content-desc", "")):
            hay = re.sub(r"\s+", " ", (raw or "").strip().lower())
            if not hay:
                continue
            if hay == want:
                score = 0
            elif hay.startswith(want):
                score = 1
            elif want in hay:
                score = 2
            else:
                continue
            key = (score, 0 if clickable else 1, area)
            if best is None or key < best[0]:
                best = (key, ((x1 + x2) // 2, (y1 + y2) // 2))
    return best[1] if best else None


def _screen_size(serial: str) -> tuple[int, int]:
    r = _adb(serial, ["shell", "wm", "size"], timeout=20)
    m = re.search(r"(\d+)x(\d+)", (r.stdout or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (1080, 2400)


def system_insets(serial: str) -> dict:
    """The live system-bar insets in px, from the window manager.

    Not guessed from dp: the device reports `InsetsSource ... type=statusBars
    frame=[0,0][1080,63]`, which is the only number that describes THIS build
    on THIS device (a hardcoded 24dp was wrong on every device with a cutout).
    """
    out = {"top": 0, "bottom": 0, "source": "none"}
    r = _adb(serial, ["shell", "dumpsys", "window"], timeout=90)
    text = r.stdout or ""
    for kind in ("statusBars", "navigationBars"):
        m = re.search(rf"type={kind} frame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]", text)
        if not m:
            continue
        _l, t, _r, b = (int(v) for v in m.groups())
        if kind == "statusBars":
            # A top-anchored bar's inset is its HEIGHT (frame [0,0][1080,63]).
            out["top"] = (b - t) if t == 0 else 0
        else:
            out["bottom"] = (b - t) if b > t else 0
        out["source"] = "dumpsys"
    return out


def status_bar_collisions(serial: str, pkg: str, insets: dict | None = None) -> dict:
    """App labels drawn INSIDE the status bar — the insets bug a still cannot show.

    The still pass renders the app's composables onto a bare canvas, so a screen
    that never applies `WindowInsets.statusBars` looks perfect in every
    screenshot and broken on every phone (seen 2026-11-07: the dashboard's brand
    header sat 37px behind the clock). This reads the live view hierarchy and
    compares the app's own labelled nodes against the device's real inset.
    Deterministic: no model, no judgement, just pixel coordinates.
    """
    insets = insets or system_insets(serial)
    top = int(insets.get("top") or 0)
    xml = _ui_xml(serial)
    hits = []
    if top > 0:
        for m in re.finditer(r"<node\b[^>]*>", xml):
            attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', m.group(0)))
            if attrs.get("package") != pkg:
                continue
            label = (attrs.get("text") or "").strip() or (attrs.get("content-desc") or "").strip()
            if not label:
                continue          # a background may sit under the bar; a LABEL may not
            b = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrs.get("bounds", ""))
            if not b:
                continue
            x1, y1, x2, y2 = (int(v) for v in b.groups())
            if y1 < top:
                hits.append({"label": label[:60], "bounds": [x1, y1, x2, y2],
                             "overlap_px": min(top, y2) - y1})
    hits.sort(key=lambda h: (-h["overlap_px"], h["bounds"][1]))
    return {"status_bar_px": top,
            "nav_bar_px": int(insets.get("bottom") or 0),
            "collisions": hits[:6],
            "checked_at": now_iso()}


def _wait_for_text(serial: str, needle: str, tries: int = 5, pause: float = 0.6):
    for _ in range(tries):
        pos = _node_center(_ui_xml(serial), needle)
        if pos:
            return pos
        time.sleep(pause)
    return None


def _screen_sig(serial: str) -> str:
    """Fingerprint of the live screen — used to prove a tap changed something."""
    r = _adb(serial, ["exec-out", "screencap", "-p"], timeout=60, binary=True)
    data = r.stdout if isinstance(r.stdout, bytes) else (r.stdout or "").encode()
    return hashlib.sha256(data).hexdigest()[:16] if data.startswith(b"\x89PNG") else ""


def _step(serial: str, step: dict) -> dict:
    """Perform one declared step. Never silently ignores an unknown action.

    Returns {"action": <what happened>, "dead": <tap changed nothing>, "needle": ...}.
    A labelled tap is bracketed by two screen fingerprints: a tap that leaves the
    screen byte-identical is a DEAD CONTROL (or a target nothing can hit), and it
    is reported at P1 instead of being passed to the judge as if it worked.
    """
    if "wait" in step:
        ms = max(0, int(step.get("wait") or 0))
        time.sleep(ms / 1000.0)
        return {"action": f"wait {ms}ms", "dead": False}
    if "tap" in step:
        needle = str(step["tap"])
        pos = _wait_for_text(serial, needle)
        if not pos:
            raise SystemExit(f"ux-video: no node matching {needle!r} on screen — the flow "
                             f"cannot be driven; fix {FLOWS_REL}")
        before = _screen_sig(serial)
        _adb(serial, ["shell", "input", "tap", str(pos[0]), str(pos[1])], timeout=30)
        time.sleep(DEAD_TAP_S)
        after = _screen_sig(serial)
        return {"action": f"tap {needle!r} at {pos[0]},{pos[1]}", "needle": needle,
                "dead": bool(before and after and before == after)}
    if "long_press" in step:
        needle = str(step["long_press"])
        pos = _wait_for_text(serial, needle)
        if not pos:
            raise SystemExit(f"ux-video: no node matching {needle!r} on screen — the flow "
                             f"cannot be driven; fix {FLOWS_REL}")
        # A long press is a zero-distance swipe held past the long-press timeout.
        _adb(serial, ["shell", "input", "swipe", str(pos[0]), str(pos[1]),
                      str(pos[0]), str(pos[1]), "900"], timeout=30)
        time.sleep(0.8)
        return {"action": f"long-press {needle!r} at {pos[0]},{pos[1]}", "needle": needle,
                "dead": False}
    if "tap_xy" in step:
        x, y = (int(v) for v in step["tap_xy"])
        _adb(serial, ["shell", "input", "tap", str(x), str(y)], timeout=30)
        return {"action": f"tap {x},{y}", "dead": False}
    if "swipe" in step:
        w, h = _screen_size(serial)
        direction = str(step["swipe"]).lower()
        ms = int(step.get("ms") or 300)
        if direction == "up":
            args = [w // 2, int(h * 0.75), w // 2, int(h * 0.3)]
        elif direction == "down":
            args = [w // 2, int(h * 0.3), w // 2, int(h * 0.75)]
        elif direction == "left":
            args = [int(w * 0.8), h // 2, int(w * 0.2), h // 2]
        else:
            args = [int(w * 0.2), h // 2, int(w * 0.8), h // 2]
        _adb(serial, ["shell", "input", "swipe", *[str(v) for v in args], str(ms)], timeout=30)
        return {"action": f"swipe {direction}", "dead": False}
    if "text" in step:
        value = str(step["text"]).replace(" ", "%s")
        _adb(serial, ["shell", "input", "text", value], timeout=30)
        return {"action": f"type {step['text']!r}", "dead": False}
    if "key" in step:
        _adb(serial, ["shell", "input", "keyevent", str(step["key"]).upper()], timeout=30)
        return {"action": f"key {step['key']}", "dead": False}
    raise SystemExit(f"ux-video: unknown step {json.dumps(step)[:120]} — allowed: "
                     "wait, tap, tap_xy, long_press, swipe, text, key")


def _crash_line(serial: str, pkg: str) -> str:
    """The FATAL exception the app just hit, or "" (crash buffer is per boot)."""
    r = _adb(serial, ["logcat", "-d", "-b", "crash"], timeout=45)
    text = r.stdout if isinstance(r.stdout, str) else (r.stdout or b"").decode("utf-8", "replace")
    if pkg not in text or "FATAL EXCEPTION" not in text:
        return ""
    block = text.rsplit("FATAL EXCEPTION", 1)[-1]
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("Process:") or not line:
            continue
        # "AndroidRuntime: java.lang.IllegalArgumentException: ..." -> the message
        if "Exception" in line or "Error" in line:
            return re.sub(r"^\d+-\d+ [\d:.]+\s+\d+\s+\d+ E AndroidRuntime:\s*", "", line)[:240]
    return block.strip().splitlines()[0][:240] if block.strip() else ""


def _wake(serial: str) -> None:
    _adb(serial, ["shell", "input", "keyevent", "224"], timeout=20)   # WAKEUP
    _adb(serial, ["shell", "wm", "dismiss-keyguard"], timeout=20)
    _adb(serial, ["shell", "settings", "put", "system", "accelerometer_rotation", "0"], timeout=20)


def record_flow(project: Path, flow: dict, serial: str, log=None) -> dict:
    """Relaunch the app fresh, drive the declared steps, record the screen."""
    from .emulator import ADB

    fid = str(flow["id"])
    secs = max(6, min(int(flow.get("seconds") or DEFAULT_SECONDS), MAX_SECONDS))
    pkg = package_name(project)
    _wake(serial)
    _adb(serial, ["logcat", "-b", "crash", "-c"], timeout=30)   # only THIS flow's crash
    _adb(serial, ["shell", "am", "force-stop", pkg], timeout=30)
    # A FIRST-RUN flow only exists on a device with no app data. `install -r -d`
    # keeps that data, so without a reset the flow is recordable exactly once per
    # device and every later run dies on the first step ("no node matching 'Maybe
    # later'") — seen 2026-11-07. The reset is DECLARED per flow, never implied: a
    # journey that needs seeded state must keep it.
    if flow.get("fresh"):
        cleared = _adb(serial, ["shell", "pm", "clear", pkg], timeout=60)
        out = ((cleared.stdout or "") if cleared else "").strip()
        if not out.lower().startswith("success"):
            raise SystemExit(f"ux-video: could not clear app data for '{pkg}' "
                             f"(declared fresh) — got {out or 'no answer'}")
    _adb(serial, ["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"],
         timeout=45)
    time.sleep(1.2)

    remote = "/sdcard/devteam-ux.mp4"
    _adb(serial, ["shell", "rm", "-f", remote], timeout=20)
    if log:
        log(f"~ ux-video: recording '{fid}' (cap {secs}s) on {serial}")
    proc = subprocess.Popen(
        [ADB, "-s", serial, "shell", "screenrecord", "--time-limit", str(secs),
         "--bit-rate", "4000000", remote],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    actions: list[str] = []
    dead: list[dict] = []
    t0 = time.monotonic()
    # Where the inset check observes: 'start' judges the screen the flow opens
    # on, 'end' (default) the screen it leaves the user on. A project says which
    # because only it knows which screen its flow is ABOUT.
    check_at = str(flow.get("check") or "end").strip().lower()
    inset_report = status_bar_collisions(serial, pkg) if check_at == "start" else None
    try:
        for step in flow.get("steps") or []:
            res = _step(serial, step)
            actions.append(res["action"])
            if res.get("dead"):
                dead.append({"action": res["action"], "needle": res.get("needle", "")})
        # The declared `seconds` is the screenrecord cap. Driving the taps is slow in
        # real time (each uiautomator dump is a device round-trip), so a cap that is
        # too short cuts the video BEFORE the steps finish — the recording then never
        # shows the screen the flow is named after, yet a contact sheet sampled from
        # it was still judged (seen 2026-11-07: a 24s cap on a 31s drive judged the
        # home screen and reported 'the paywall never appears'). A capped recording
        # must not become evidence: fail and make the author raise `seconds`.
        spent = time.monotonic() - t0
        if spent > secs - 1.5:
            raise SystemExit(
                f"ux-video: driving '{fid}' took {spent:.1f}s but the recording cap is "
                f"{secs}s — the video ends before the declared steps do, so it is not "
                f"evidence of the screen this flow is about. Raise \"seconds\" in "
                f"{FLOWS_REL} above {int(spent + 5)} and record again.")
        if inset_report is None:
            inset_report = status_bar_collisions(serial, pkg)
        # Hold the settled final state long enough for at least one sampled frame.
        time.sleep(min(3.0, max(1.0, secs - (time.monotonic() - t0) - 0.5)))
        # A flow can COMPLETE and still have killed the app (the crash lands on the
        # last frame): the recording is then evidence of a crash, not of a working
        # flow. Read the buffer before declaring success.
        crashed = _crash_line(serial, pkg)
    except SystemExit:
        # A flow that cannot be driven because the app DIED is not a spec error:
        # report the crash itself at P0, so the planner sees a blunder instead of
        # an unexplained "no node matching ..." (seen 2026-11-07: a drawer scroll
        # crashed on a duplicate LazyColumn key and read as a broken flow spec).
        crash = _crash_line(serial, pkg)
        if crash:
            raise SystemExit(f"ux-video: the app CRASHED during '{fid}' — {crash}") from None
        raise
    finally:
        _adb(serial, ["shell", "pkill", "-INT", "screenrecord"], timeout=20)
        try:
            proc.wait(timeout=secs + 15)
        except subprocess.TimeoutExpired:
            proc.kill()
    elapsed = round(time.monotonic() - t0, 1)

    dest = mp4_path(project, fid)
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(6):
        _adb(serial, ["pull", remote, str(dest)], timeout=180)
        if dest.is_file() and dest.stat().st_size > 12_000:
            break
        time.sleep(1.5)
    if not (dest.is_file() and dest.stat().st_size > 12_000):
        raise SystemExit(f"ux-video: screenrecord produced no usable video for '{fid}' "
                         f"({dest.stat().st_size if dest.exists() else 0} bytes)")
    if crashed:
        raise SystemExit(f"ux-video: the app CRASHED during '{fid}' — {crashed}")

    # `seconds` describes the FILE, not the driver: the contact sheet samples the
    # recording's own duration, and a manifest that claimed the driving time made a
    # truncated video look complete (24s file reported as 30.7s).
    duration = elapsed
    probe = shutil.which("ffprobe")
    if probe:
        try:
            pr = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=nw=1:nk=1", str(dest)],
                                capture_output=True, text=True, timeout=30)
            duration = round(float((pr.stdout or "").strip()), 1)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
    return {"flow": fid, "mp4": str(dest), "seconds": duration,
            "drive_seconds": elapsed, "bytes": dest.stat().st_size, "actions": actions,
            "dead_taps": dead, "insets": inset_report, "recorded_at": now_iso()}


# --------------------------------------------------------------------------- #
# frames -> one contact sheet -> one reaction
# --------------------------------------------------------------------------- #
def contact_sheet(mp4: Path, out_png: Path, every: float | None = None) -> dict:
    """Tile evenly sampled frames from ACROSS the whole recording into one PNG.

    `every` defaults to duration/panels: sampling the first N seconds at a fixed
    cadence showed only the opening of a 20-second flow — the screen the user
    ended on (the one the flow is named after) was never in the sheet.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found on PATH — it is required to sample the recording")
    probe = shutil.which("ffprobe")
    duration = 0.0
    if probe:
        pr = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=nw=1:nk=1", str(mp4)],
                            capture_output=True, text=True, timeout=60)
        try:
            duration = float((pr.stdout or "0").strip() or 0)
        except ValueError:
            duration = 0.0
    panels = GRID_COLS * GRID_ROWS
    step = float(every) if every else (duration / panels if duration > 0 else SAMPLE_EVERY_S)
    step = max(0.4, step)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    vf = (f"fps=1/{step:.4f},scale={FRAME_W}:-1,"
          # A short flow yields fewer frames than the grid; clone the last one so
          # `tile` still emits a complete sheet instead of nothing.
          f"tpad=stop_mode=clone:stop_duration={step * panels:g},"
          f"tile={GRID_COLS}x{GRID_ROWS}:padding=6:margin=6:color=0x101418")
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(mp4), "-vf", vf, "-frames:v", "1", str(out_png)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not out_png.is_file():
        raise SystemExit("ffmpeg could not build a contact sheet: " + (r.stderr or "")[-300:])
    return {"sheet": str(out_png), "panels": panels, "every": round(step, 2),
            "duration": round(duration, 1)}


def _severity(text: str, line: str) -> str:
    t = f"{text} {line}".upper()
    if "P0" in t or "BLUNDER" in t:
        return "P0_BLUNDER"
    if "P1" in t or "DEGRADED" in t:
        return "P1_DEGRADED"
    return "P2_POLISH"


# --------------------------------------------------------------------------- #
# the reaction: Gemini in the browser, the same maker that paints the assets
# --------------------------------------------------------------------------- #
QUOTA_HINT = re.compile(
    r"(reached your (daily )?(limit|quota)|usage limit|quota exceeded|"
    r"too many requests|try again (later|tomorrow)|you.?ve hit your limit)",
    re.I)


def _describe_steps(flow: dict) -> str:
    bits = []
    for step in flow.get("steps") or []:
        if "tap" in step:
            bits.append(f"tap {step['tap']!r}")
        elif "swipe" in step:
            bits.append(f"swipe {step['swipe']}")
        elif "wait" in step:
            bits.append(f"wait {int(step.get('wait') or 0)}ms")
        elif "text" in step:
            bits.append(f"type {step['text']!r}")
        elif "key" in step:
            bits.append(f"key {step['key']}")
        elif "long_press" in step:
            bits.append(f"long-press {step['long_press']!r}")
        elif "tap_xy" in step:
            bits.append("tap")
    return " → ".join(bits) or "no steps recorded"


def _gemini_script(prompt: str, src: Path, expect_video: bool) -> str:
    """One chat, one attachment, one answer. Generic — no project is named."""
    return f"""
    const PROMPT = {json.dumps(prompt)};
    const SRC = {json.dumps(str(src))};
    const IS_VIDEO = {str(bool(expect_video)).lower()};
    const pages = context.pages().filter(p => p.url().includes("gemini.google.com"));
    const page = pages[0] || await context.newPage();
    // A FRESH chat: the verdict must not inherit an earlier asset's look or an
    // earlier flow's conversation. An existing Gemini tab is reused, never
    // navigated away from somewhere the owner was working.
    await page.goto("https://gemini.google.com/app", {{ waitUntil: "domcontentloaded" }});
    await page.waitForTimeout(1500);
    await page.bringToFront();
    const attachedCount = () => page
      .locator("rich-textarea img[src^='blob:'], rich-textarea img[src^='data:image'], "
               + ".file-preview-container img, .attachment-container").count();
    const before = await attachedCount();
    await page.locator("[aria-label=\\"Upload & tools\\"]").first().click();
    await page.waitForTimeout(700);
    await page.locator("input[type=file]").first().setInputFiles(SRC);
    let attached = false;
    for (let i = 0; i < 40; i++) {{
      if (await attachedCount() > before) {{ attached = true; break; }}
      await page.waitForTimeout(1000);
    }}
    if (!attached) {{
      return {{ ok: false, error: "the " + (IS_VIDEO ? "recording" : "contact sheet")
                                   + " never attached to the prompt" }};
    }}
    await page.waitForTimeout(IS_VIDEO ? 3500 : 2000);
    const composer = page.locator("rich-textarea [contenteditable=true], [contenteditable=true]").first();
    await composer.click();
    await composer.fill(PROMPT);
    await page.waitForTimeout(400);
    const sentBefore = await page.locator("user-query").count();
    const responsesBefore = await page.locator("model-response").count();
    await page.keyboard.press("Enter");
    const start = Date.now();
    let text = "";
    let lastLen = -1;
    let stable = 0;
    const bodyText = async () => (await page.locator("body").innerText().catch(() => "")) || "";
    while (Date.now() - start < 360000) {{
      if (/reached your (daily )?(limit|quota)|usage limit|quota exceeded|too many requests/i
          .test(await bodyText())) {{
        return {{ ok: false, quota: true, error: "gemini limit reached" }};
      }}
      const n = await page.locator("model-response").count();
      if (n > responsesBefore) {{
        const t = (await page.locator("model-response").nth(n - 1)
          .innerText().catch(() => "")) || "";
        if (t.trim().length > 0 && t.trim().length === lastLen && t.includes("}}")) {{
          stable += 1;
        }} else {{
          stable = 0;
        }}
        lastLen = t.trim().length;
        text = t;
        if (stable >= 2) break;
      }}
      await page.waitForTimeout(2500);
    }}
    if (!text.trim()) return {{ ok: false, error: "gemini returned no answer" }};
    return {{ ok: true, text, chat: page.url() }};
    """


def _gemini_answer(project: Path, prompt: str, src: Path, video: bool, log=None) -> dict:
    """Ask browser Gemini; return {ok,text} or {ok:false,quota,error}. Never raises."""
    from . import gemini_browser
    script = _gemini_script(prompt, src, video)
    if log:
        log(f"~ ux-video: asking browser Gemini about {src.name} "
            f"({'video' if video else 'contact sheet'})")
    try:
        res = gemini_browser.execute(script, timeout=420, log=log)
    except Exception as exc:            # a browser that is down is not a failed run
        return {"ok": False, "error": f"gemini browser failed: {exc}"[:300]}
    out = (res.stdout or "").strip()
    try:
        payload = json.loads(out)
    except Exception:
        return {"ok": False, "error": (out or (res.stderr or "").strip())[:300] or "no output"}
    if not payload.get("ok"):
        return {"ok": False, "quota": bool(payload.get("quota")),
                "error": str(payload.get("error") or "unknown")[:300]}
    return {"ok": True, "value": payload.get("value") or {}}


def build_prompt(row: dict, flow: dict, sheet: dict, evidence: str) -> str:
    if evidence == "video":
        sampling = ("The attachment IS the recording — watch it play. Judge timing, "
                    "transitions and dead time as they actually happen.")
    else:
        sampling = (f"The attachment is one contact sheet: {sheet['panels']} frames sampled "
                    f"left-to-right, top-to-bottom every {sheet['every']:g} seconds from that "
                    "recording — panel 1 is the start, the last panel is the end.")
    return PROMPT.format(
        seconds=sheet["duration"] or row["seconds"],
        panels=sheet["panels"],
        every=sheet["every"],
        surface=row["surface"],
        flow=row["id"],
        actions=_describe_steps(flow),
        evidence=("the recording itself" if evidence == "video" else "an ordered contact sheet"),
        sampling=sampling,
    )


def react(project: Path, row: dict, flow: dict, mp4: Path, sheet: dict,
          log=None, via: str = "gemini") -> dict:
    """One reaction over the motion, made by browser Gemini (or the vision API).

    The evidence ladder is deliberate: the VIDEO is the truth, the contact sheet
    is what the browser can still show when a video upload is refused, and a
    quota stop is recorded as SKIPPED — the recording stays on disk, so the pass
    resumes next run instead of pretending the flow was judged.
    """
    from . import ux_audit, vision_layout

    if via == "api":
        prompt = build_prompt(row, flow, sheet, "sheet")
        cfg = vision_layout.config()
        if log:
            log(f"~ ux-video: reacting to '{row['id']}' via {cfg.get('model', '?')}")
        raw = vision_layout._call(cfg, prompt, Path(sheet["sheet"]))
        result = vision_layout._json_block(raw)
        return _result(result, row, "vision-api:" + str(cfg.get("model", "")), "sheet")

    value = {}
    quota = False
    errors = []
    for evidence, src in (("video", mp4), ("sheet", Path(sheet["sheet"]))):
        if evidence == "sheet" and not src.is_file():
            continue
        prompt = build_prompt(row, flow, sheet, evidence)
        ans = _gemini_answer(project, prompt, src, evidence == "video", log=log)
        if ans.get("ok"):
            answer = ans.get("value") or {}
            text = str(answer.get("text") or "")
            try:
                value = vision_layout._json_block(text)
            except SystemExit as exc:
                errors.append(f"{evidence}: {exc}")
                continue
            return _result(value, row, "gemini-web", evidence)
        if ans.get("quota"):
            quota = True
            break
        errors.append(f"{evidence}: {ans.get('error')}")
    if quota:
        return {"ok": False, "skipped": "browser Gemini reported its limit — left unjudged "
                                        "this run (the recording is kept)",
                "model": "gemini-web", "rubric": RUBRIC}
    return {"ok": False, "skipped": "browser Gemini could not judge this flow: "
                                    + "; ".join(errors)[-300:],
            "model": "gemini-web", "rubric": RUBRIC}


def _result(value: dict, row: dict, model: str, evidence: str) -> dict:
    from . import ux_audit
    gripes = []
    # `defects` covers the visible screens as well as the motion; `motion_defects`
    # is the older key and is still read so a stored/older answer is not lost.
    for item in (value.get("defects") or value.get("motion_defects") or []):
        if not isinstance(item, dict):
            continue
        line = str(item.get("line") or "").strip()
        if not line:
            continue
        kind = str(item.get("kind") or "motion").strip().lower()
        gripes.append({
            "id": ux_audit.gripe_id(_slug(row["id"])[:12], line),
            "line": line,
            "element": str(item.get("element") or ""),
            "kind": kind if kind in ("ui", "ux", "motion") else "motion",
            "severity": _severity(str(item.get("severity") or ""), line),
            "status": "open",
            "source": "video",
            "flow": row["id"],
            "at": now_iso(),
        })
    return {
        "ok": True,
        "evidence": evidence,
        "brief": [{"at": str(r.get("at") or ""), "line": str(r.get("line") or ""),
                   "kind": str(r.get("kind") or "")}
                  for r in (value.get("reactions") or []) if isinstance(r, dict)],
        "premium": [str(x) for x in (value.get("premium") or [])][:6],
        "verdict": str(value.get("verdict") or ""),
        "gripes": gripes,
        "model": model,
        "rubric": RUBRIC,
    }


def store_dead_taps(project: Path, row: dict, dead: list[dict]) -> None:
    """Record taps that changed nothing as P1 gripes on the flow's surface.

    The model can only see a dead control if the sampled panels happen to show it;
    the driver KNOWS, because the screen was byte-identical before and after.
    """
    from . import ux_audit
    if not dead:
        return
    data = ux_audit.load(project)
    surf = (data.get("surfaces") or {}).get(row["surface"])
    if surf is None:
        return
    gripes = [g for g in (surf.get("gripes") or [])
              if not (g.get("source") == "video" and g.get("flow") == row["id"]
                      and g.get("kind") == "dead-click")]
    seen = set()
    for d in dead:
        needle = str(d.get("needle") or "")
        line = (f"tapping {needle!r} changed nothing on screen — the control looks "
                f"tappable but produced no visible response")
        key = ux_audit.gripe_id(_slug(row["id"])[:12], line)
        if key in seen:
            continue
        seen.add(key)
        gripes.append({
            "id": key,
            "line": line,
            "element": needle,
            "kind": "dead-click",
            "severity": "P1_DEGRADED",
            "status": "open",
            "source": "video",
            "flow": row["id"],
            "at": now_iso(),
        })
    surf["gripes"] = gripes
    ux_audit.save(project, data)


def store_crash(project: Path, row: dict, reason: str) -> None:
    """Record an app crash observed while driving a flow as a P0 blunder.

    The recording is the evidence; without this, a flow that kills the app only
    appears in the run's `failed` list and never reaches the planner's gripes.
    """
    from . import ux_audit
    line = reason.split("CRASHED", 1)[-1].strip(" —-:.")
    if not line:
        return
    data = ux_audit.load(project)
    surf = (data.get("surfaces") or {}).get(row["surface"])
    if surf is None:
        return
    fresh = [g for g in (surf.get("gripes") or [])
             if not (g.get("source") == "video" and g.get("flow") == row["id"]
                     and g.get("kind") == "crash")]
    fresh.append({
        "id": ux_audit.gripe_id(_slug(row["id"])[:12], line),
        "line": line,
        "element": "",
        "kind": "crash",
        "severity": "P0_BLUNDER",
        "status": "open",
        "source": "video",
        "flow": row["id"],
        "at": now_iso(),
    })
    surf["gripes"] = fresh
    ux_audit.save(project, data)


def store_reaction(project: Path, row: dict, sheet: dict, reaction: dict) -> dict:
    """Attach the flow's result to its declared screen in the UX audit store.

    A SKIPPED reaction (a quota stop, a browser that would not answer) is stored
    too — with no gripes touched. Otherwise a flow nobody managed to judge would
    read exactly like one that was judged clean.
    """
    from . import ux_audit
    data = ux_audit.load(project)
    label = row["surface"]
    surf = (data.get("surfaces") or {}).get(label)
    if surf is None:
        raise SystemExit(f"ux-video: no audited screen labelled {label!r} — declare it in "
                         f".pipeline/profile.json['targets'] first")
    entry = {
        "flow": row["id"],
        "mp4": row["mp4"],
        "sheet": sheet.get("sheet", ""),
        "panels": sheet["panels"],
        "sample_every_s": sheet["every"],
        "duration_s": sheet["duration"],
        "rubric": RUBRIC,
        "model": reaction.get("model", ""),
        "evidence": reaction.get("evidence", ""),
        "at": now_iso(),
    }
    if not reaction.get("ok"):
        entry["skipped"] = reaction.get("skipped", "not judged")
        surf.setdefault("videos", {})[row["id"]] = entry
        ux_audit.save(project, data)
        return surf["videos"][row["id"]]
    # A reaction replaces the JUDGEMENT of this flow, but never the mechanical
    # observations (a crash, a dead tap) that the driver proved on the device.
    keep = [g for g in (surf.get("gripes") or [])
            if not (g.get("source") == "video" and g.get("flow") == row["id"]
                    and g.get("kind") not in ("crash", "dead-click"))]
    surf["gripes"] = keep + reaction["gripes"]
    entry.update({
        "reacted_at": now_iso(),
        "reactions": reaction["brief"],
        "premium": reaction["premium"],
        "verdict": reaction["verdict"],
        "gripes": reaction["gripes"],
    })
    surf.setdefault("videos", {})[row["id"]] = entry
    ux_audit.save(project, data)
    return surf["videos"][row["id"]]


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def store_insets(project: Path, surface: str, insets: dict | None) -> dict | None:
    """Attach the device's own inset measurement to the declared screen.

    One gripe per screen, not one per label: the user sees one broken header, so
    the planner gets one fix. The previous inset gripes for the surface are
    dropped first — a screen that has been fixed must stop carrying the finding.
    """
    from . import ux_audit
    if not insets:
        return None
    data = ux_audit.load(project)
    surf = (data.get("surfaces") or {}).get(surface)
    if surf is None:
        return None                      # not declared/audited: nothing to attach to
    keep = [g for g in (surf.get("gripes") or [])
            if not (g.get("source") == "video" and g.get("kind") == "inset")]
    collisions = insets.get("collisions") or []
    gripes = []
    if collisions:
        worst = collisions[0]
        others = ", ".join(c["label"] for c in collisions[1:3])
        line = (f"'{worst['label']}' is drawn {worst['overlap_px']}px inside the "
                f"{insets['status_bar_px']}px status bar"
                + (f" (also: {others})" if others else "")
                + " — this screen never applies status-bar window insets, so the system "
                  "clock and battery sit on top of it")
        gripes.append({
            "id": ux_audit.gripe_id(_slug(surface)[:12], line),
            "line": line,
            "element": "top header / status bar",
            "kind": "inset",
            "severity": "P0_BLUNDER",
            "status": "open",
            "source": "video",
            "flow": "",
            "at": now_iso(),
        })
    surf["gripes"] = keep + gripes
    surf["insets"] = {**insets, "gripes": gripes}
    ux_audit.save(project, data)
    return surf["insets"]


def run(project: Path, only: str | None = None, everything: bool = False,
        log=None, avd: str | None = None, keep_emulator: bool = False,
        record_only: bool = False, via: str = "gemini",
        re_record: bool = False) -> dict:
    """Record what is missing/stale, then react to every recording owed one."""
    from . import emulator

    p = plan(project)
    rows = p["rows"]
    if only:
        rows = [r for r in rows if r["id"] == only]
        if not rows:
            raise SystemExit(f"ux-video: no declared flow {only!r} in {FLOWS_REL}")
    elif not everything and not re_record:
        rows = [r for r in rows if r["status"] != "unchanged"]
    no_surface = [r for r in rows if r["status"] == "no-surface"]
    for r in no_surface:
        if log:
            log(f"~ ux-video: skipping '{r['id']}' — surface {r['surface']!r} is not declared")

    to_record = [r for r in rows
                 if r["status"] in ("new", "stale")
                 or (r["status"] != "no-surface" and (only or re_record))]
    skipped: list[dict] = []
    failed: list[dict] = []
    recorded: list[dict] = []

    if to_record:
        serial, started = emulator.ensure_device(avd=avd, log=log)
        try:
            build_info = build_and_install(project, serial, log)
            for r in to_record:
                flow = next((f for f in declared(project) if str(f["id"]) == r["id"]), None)
                if not flow:
                    continue
                try:
                    rec = record_flow(project, flow, serial, log=log)
                except SystemExit as exc:
                    reason = str(exc)[:300]
                    failed.append({"id": r["id"], "reason": reason})
                    if "CRASHED" in reason:
                        store_crash(project, r, reason)
                    continue
                if log:
                    log(f"~ ux-video: {rec['bytes'] // 1024} KB, {rec['seconds']}s, "
                        f"{len(rec['actions'])} action(s)")
                man = load_manifest(project)
                man[r["id"]] = {
                    "video_sha": _sha(Path(rec["mp4"])),
                    "rubric": RUBRIC,
                    "recorded_at": rec["recorded_at"],
                    "seconds": rec["seconds"],
                    "actions": rec["actions"],
                    "build": build_info,
                    "insets": rec.get("insets") or {},
                    "reacted_at": "",
                }
                save_manifest(project, man)
                insets = store_insets(project, r["surface"], rec.get("insets"))
                if insets and insets.get("collisions") and log:
                    log(f"~ ux-video: '{r['surface']}' draws "
                        f"{len(insets['collisions'])} label(s) inside the "
                        f"{insets['status_bar_px']}px status bar")
                recorded.append(rec)
                if rec.get("dead_taps"):
                    store_dead_taps(project, r, rec["dead_taps"])
        finally:
            if started and not keep_emulator:
                stopped = emulator.stop(serial)
                if log:
                    log(f"~ ux-video: emulator {serial} "
                        f"{'stopped' if stopped else 'DID NOT STOP — stop it manually'}")
            elif started and log:
                log(f"~ ux-video: leaving emulator {serial} running (--keep-emulator)")

    reacted: list[dict] = []
    if not record_only:
        fresh = {r["flow"] for r in recorded}
        for r in plan(project)["rows"]:
            if only and r["id"] != only:
                continue
            if not only and not everything and not re_record and r["status"] == "unchanged":
                continue
            if r["id"] in fresh:
                r["status"] = "unreacted"
            if r["status"] in ("new", "stale"):
                if r["id"] not in fresh:
                    skipped.append({"id": r["id"], "reason": "no fresh recording this run"})
                continue
            if not r["mp4"] or not r["surface"]:
                continue
            flow = next((f for f in declared(project) if str(f["id"]) == r["id"]), None)
            if not flow:
                continue
            try:
                sheet = contact_sheet(Path(r["mp4"]), sheet_path(project, r["id"]))
            except SystemExit as exc:
                failed.append({"id": r["id"], "reason": str(exc)[:300]})
                continue
            reaction = react(project, r, flow, Path(r["mp4"]), sheet, log=log, via=via)
            try:
                entry = store_reaction(project, r, sheet, reaction)
            except SystemExit as exc:
                failed.append({"id": r["id"], "reason": str(exc)[:300]})
                continue
            man = load_manifest(project)
            man.setdefault(r["id"], {})["sheet"] = sheet["sheet"]
            man[r["id"]]["rubric"] = RUBRIC
            man[r["id"]]["panels"] = sheet["panels"]
            man[r["id"]]["evidence"] = reaction.get("evidence", "")
            if reaction.get("ok"):
                man[r["id"]]["reacted_at"] = entry["reacted_at"]
                man[r["id"]].pop("skipped_reason", None)
                reacted.append({**entry, "surface": r["surface"], "id": r["id"]})
            else:
                # Quota is not a failure: the recording is kept, and the next run
                # picks the judgement up where this one stopped.
                man[r["id"]]["skipped_at"] = now_iso()
                man[r["id"]]["skipped_reason"] = reaction.get("skipped", "")
                skipped.append({"id": r["id"], "reason": reaction.get("skipped", "")})
                if log:
                    log(f"~ ux-video: '{r['id']}' left unjudged — "
                        f"{reaction.get('skipped', 'no answer')}")
            save_manifest(project, man)
    return {"recorded": recorded, "reacted": reacted, "skipped": skipped,
            "failed": failed, "planned": len(rows)}


def stale_for_files(project: Path, changed: list[str]) -> list[dict]:
    """Flows whose recording predates a change in the files this diff touched."""
    changed_set = {str(c).replace("\\", "/") for c in changed}
    out = []
    for r in plan(project)["rows"]:
        if r["status"] != "stale":
            continue
        touched = [p for p in r["stale_inputs"]
                   if any(p.replace("\\", "/").endswith(c) or c.endswith(p) for c in changed_set)]
        if touched:
            out.append({"id": r["id"], "surface": r["surface"], "inputs": touched})
    return out
