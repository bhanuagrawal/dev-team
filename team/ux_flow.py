"""Flows, driven by a vision model that decides where to go — not by a script.

WHY THIS EXISTS
---------------
`ux-audit` judges one still per screen and `ux-video` replays a DECLARED action
list. Both are only as good as their author: a still cannot show that the app
opens on the wrong screen, and a scripted flow cannot discover a path nobody
wrote down. The failures that matter most are exactly the ones nobody scripted —
"there is no sign-in", "the first run dead-ends", "this button does nothing".

So this pass gives the flow a GOAL in plain words and lets a vision model use the
app like a person: look at the screen, choose one action, see what happened,
repeat — then judge the journey. It is the same shape as the recon agent's
navigator, with one difference that matters: here the model is auditing OUR app,
so a wall is a finding, not an obstacle to route around.

What stays deterministic (the model judges, code executes and proves):
  * tappables come from the accessibility tree, so every tap is explainable and
    the model never invents a coordinate;
  * each action is bracketed by two screen fingerprints — a tap that leaves the
    screen identical is a DEAD CONTROL at P1, not a step that "worked";
  * the crash buffer is read after every step (a flow can finish and still have
    killed the app on its last frame);
  * the step budget and the wall-clock cap are enforced; a run that loops on one
    screen stops and says so instead of burning tokens;
  * a still per step is kept on disk, so a claim is reproducible after the fact.

A flow is declared in `docs/ux-flows.json` with a `goal` and NO `steps`:

    {"id": "new-user", "surface": "Discovery", "fresh": true,
     "goal": "install for the first time, get through whatever the app asks
              for, and reach the neighbourhood feed", "max_steps": 8}

`ux-video` keeps owning flows that DO declare steps (a recording is evidence of a
specific journey); this pass owns the ones that only declare what should be true.

Gripes land in `.pipeline/ux_audit.json` on the flow's surface — the same
inventory the planner already reads — with `source: flow` and an id derived from
the complaint, and a re-run that no longer reproduces one marks it `gone`.

Nothing here is committed: `.pipeline/` is machine-local, by contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .core import now_iso, pipeline_dir, save_json

FLOWS_REL = "docs/ux-flows.json"
RUN_DIR = "ux_flows"
DEFAULT_STEPS = 10
MAX_STEPS_CAP = 16
DEFAULT_SECONDS = 240
RUBRIC = "ux-flow-v1"

# Chrome that is never a decision: a flow that "advances" by tapping the system
# back button has not learned anything about the app.
IGNORE_LABELS = {"back", "navigate up", "close", "cancel", "ok", "done", "skip"}

# How many tappable controls the vision model is shown at once. High enough for a
# full phone screen of named controls (a feed measured 66); blank containers only
# take the room that is left.
_TAP_CAP = 80

DECIDE_PROMPT = """You are using a mobile app like a real person, to check ONE journey works.

App: {app}
Journey: {flow}
Goal: {goal}
Step {step} of {max_steps}.
Already done: {history}
Already filled: {filled}
{screen}
{credentials}
The screenshot IS the current screen. The list below is what is tappable on it
(read from the app's accessibility tree — tap by INDEX, never invent a place).

Tappable:
{targets}
{dead}
Choose the SINGLE next action that moves a real user closest to the goal:
  type    — index + field ("email" or "password"): put the journey's credential in
            that field. When PROFILE values are listed below, field may also be
            "name", "handle" or "phone" for a shelf-onboarding or swap form. ONLY
            when those values are listed below; if they are not, a text field is a
            wall for you, not a dead control — choose stuck.
  tap     — an index from the list above
  scroll  — "down" or "up": reach content below or above the fold. The screenshot
            shows only the visible part of a screen; a request or continue control
            is often further down, and a screen is not a dead end until it has been
            scrolled.
  back    — leave a screen that cannot progress this journey
  done    — the goal is reached on this screen
  stuck   — the app blocks a person here (say what is missing), or the journey
            cannot continue from this screen at all

Never repeat an action that already did nothing on this screen. Never tap a
destructive or payment control.

ALSO, while you are here: when THIS screen made you hesitate — a label you had to
guess, something that looked broken, a wait that felt wrong, a screen that felt
unfinished — add one short line in the first person as "reaction" (see the shape
below). Only when it is true; omit it when the step was obvious. This is the note
a person would say out loud as they go, and it is read at the exact step it
happened instead of being reconstructed later.

Reply with JSON only, no prose, no code fences:
  {{"action": "tap", "index": 2, "why": "one short line",
    "reaction": {{"line": "one short line, first person", "kind": "confusing|broken|slow|good"}}}}
  {{"action": "type", "index": 4, "field": "email", "why": "one short line"}}
  {{"action": "type", "index": 6, "field": "name", "why": "one short line"}}
  {{"action": "scroll", "direction": "down", "why": "one short line"}}
  {{"action": "back", "why": "one short line"}}
  {{"action": "done", "why": "what proves the goal is reached"}}
  {{"action": "stuck", "why": "what a person would be unable to do here"}}
"""

JUDGE_PROMPT = """You just watched a first-time user try ONE journey in a mobile app.

App: {app}
Journey: {flow}
Goal: {goal}
Outcome: {outcome}
What happened, step by step:
{history}

What the user said as they went (their own words, at that step):
{notes}

The attachment is a contact sheet of the screens that appeared, in order
(left to right, top to bottom). Judge the JOURNEY, not only the pixels:
- did a real person reach the goal? if not, exactly where did it break?
- a control that was tapped and visibly did nothing
- a screen that cannot be left, a dead end, a blank or unstyled state
- friction a person would complain about: unexplained asks, a step that exists
  for no reason, copy that does not say what happens
- a crash, an ANR, or the app leaving itself

Name the screen or step each defect is on. Never invent what you cannot see: if
the evidence does not show a problem, say nothing about it.

Return ONLY this JSON:
{{
  "reactions": [{{"at": "step 2", "line": "what the user would think out loud",
                  "kind": "clear|confusing|broken|slow|premium"}}],
  "defects": [{{"line": "exact description of the defect", "element": "the control or screen",
                "kind": "flow|ui|ux", "severity": "P0_BLUNDER|P1_DEGRADED|P2_POLISH"}}],
  "premium": ["what genuinely works well in this journey"],
  "goal_reached": true,
  "verdict": "one sentence: does this journey work for a real user"
}}
"""


# --------------------------------------------------------------------------- #
# the spec: goal-declared flows only
# --------------------------------------------------------------------------- #
def flows_path(project: Path) -> Path:
    return project / FLOWS_REL


def load_spec(project: Path) -> dict:
    p = flows_path(project)
    if not p.is_file():
        return {"flows": []}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {"flows": []}
    return data if isinstance(data, dict) else {"flows": []}


def declared(project: Path, root: Path | None = None) -> list[dict]:
    """Flows this pass owns: a goal, and no scripted steps.

    [root] is the tree being AUDITED. A run against a worktree must walk the
    journeys that branch declares, not the ones the checkout happens to hold —
    the checkout lags main, so a branch that fixes a goal's wording or raises its
    step budget had its own audit ignore the change (2026-11-10).
    """
    out = []
    spec_root = Path(root) if root else _delivery_root(project)
    for f in load_spec(spec_root).get("flows") or []:
        if not isinstance(f, dict) or not str(f.get("id") or "").strip():
            continue
        if not str(f.get("goal") or "").strip():
            continue
        out.append(f)
    return out


def _delivery_root(project: Path) -> Path:
    """The tree the journeys are declared in: the DELIVERY ref, not the checkout.

    The checkout lags main (it is the owner's, and stays where they left it), so
    reading the spec from it walked an older step budget and an older goal than the
    build under audit (2026-11-11). Falls back to the checkout only when the
    delivery tree cannot be read.
    """
    try:
        from . import scan as scan_mod
        tree = scan_mod.origin_tree(project)
        if tree.is_dir() and (Path(tree) / FLOWS_REL).is_file():
            return Path(tree)
    except Exception:
        pass
    return project


def run_dir(project: Path) -> Path:
    return pipeline_dir(project) / RUN_DIR


def record_path(project: Path) -> Path:
    return run_dir(project) / "run.json"


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", str(text)).strip("-") or "flow"


def _write_record(project: Path, rec: dict) -> None:
    """The run record: a long pass an agent abandons must read as interrupted,
    not vanish (AGENTS.md rule 5)."""
    run_dir(project).mkdir(parents=True, exist_ok=True)
    save_json(record_path(project), {**rec, "at": now_iso(), "pid": os.getpid()})


def read_record(project: Path) -> dict:
    p = record_path(project)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# screen -> tappables (deterministic, from the accessibility tree)
# --------------------------------------------------------------------------- #
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def tappables(xml: str, pkg: str = "") -> list[dict]:
    """Every node a person could plausibly touch, with the point to touch.

    Compose does not reliably set `clickable` in a uiautomator dump, so a node
    counts when it carries a label (text/content-desc) or is clickable; the tap
    point is the centre of its bounds.
    """
    out = []
    for m in re.finditer(r"<node\b[^>]*>", xml):
        attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', m.group(0)))
        if pkg and attrs.get("package") and attrs["package"] != pkg:
            continue
        label = (attrs.get("text") or "").strip() or (attrs.get("content-desc") or "").strip()
        clickable = attrs.get("clickable") == "true"
        if not label and not clickable:
            continue
        b = _BOUNDS.match(attrs.get("bounds", ""))
        if not b:
            continue
        x1, y1, x2, y2 = (int(v) for v in b.groups())
        if x2 - x1 < 8 or y2 - y1 < 8:
            continue
        if label.strip().lower() in IGNORE_LABELS:
            continue
        cls = str(attrs.get("class") or "")
        out.append({"label": label[:60], "x": (x1 + x2) // 2, "y": (y1 + y2) // 2,
                    "clickable": clickable,
                    # A field that takes focus without repainting the screen is not a
                    # dead control; only a person can fill it, so the journey stops there.
                    "editable": ("EditText" in cls) or attrs.get("focusable") == "true"
                                and attrs.get("clickable") != "true"})
    # A label that appears twice (a card and its text) is one decision.
    seen, uniq = set(), []
    for t in out:
        key = (t["label"].lower(), t["y"] // 40)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)
    # Reading order, top to bottom. The old cap of 14 was hit by the filter chips
    # alone on a feed, so a real button below the fold never reached the vision
    # model and the journey "stopped" at a control it could not see (2026-11-10:
    # Request Borrow on a book card). A list that hides a tappable control is a
    # false audit, so the cap is high enough to include a full mobile screen.
    uniq.sort(key=lambda t: (t["y"], t["x"]))
    # A blank clickable container must never push a NAMED control out of the list.
    # A feed screen exposed 66 named controls plus 8 blank ones; the cap of 40
    # cut the list at the book cards and the bottom dock (My Shelf, Passport)
    # never reached the model, which then guessed at unlabelled coordinates and
    # "closed" a sheet it had never opened (2026-11-12, shelf-photo-to-library).
    # Named controls come first; blanks only fill what is left.
    named = [t for t in uniq if t["label"].strip()]
    blank = [t for t in uniq if not t["label"].strip()]
    kept = named[:_TAP_CAP]
    kept += blank[:max(0, _TAP_CAP - len(kept))]
    kept.sort(key=lambda t: (t["y"], t["x"]))
    return kept


def _pick_field(targets: list[dict], role: str) -> int | None:
    """The index of the editable field that plays `role`, by label then by order.

    Compose does not always expose a text field's label, so the SECOND editable
    control on a sign-in screen is the password even when it is unlabelled —
    trusting the model's index typed the password into the email box (seen
    2026-08-11 style: the field showed "audit-pass-123" and submit silently did
    nothing because the email was never valid).
    """
    fields = [i for i, t in enumerate(targets) if t.get("editable")]
    def labelled(i: int) -> bool:
        lab = (targets[i].get("label") or "").lower()
        keys = ("email", "mail") if role == "email" else (
            "pass", "secret") if role == "password" else ()
        return bool(keys) and any(k in lab for k in keys)
    named = [i for i in fields if labelled(i)]
    if named:
        return named[0]
    if role == "email":
        return fields[0] if fields else None
    if role == "password":
        return fields[1] if len(fields) > 1 else (fields[0] if fields else None)
    if role == "name":
        return fields[0] if fields else None
    if role == "handle":
        return fields[1] if len(fields) > 1 else (fields[0] if fields else None)
    if role == "phone":
        return fields[0] if fields else None
    return fields[0] if fields else None


def _label_list(targets: list[dict]) -> str:
    if not targets:
        return "  (nothing tappable is exposed — this screen may be blank, a canvas,\n" \
               "   or a system dialog)"
    lines = []
    for i, t in enumerate(targets):
        mark = "" if t["clickable"] else "  (labelled, not marked clickable)"
        lines.append(f"  {i}: {t['label'] or '(unlabelled control)'}{mark}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
def _adb(serial: str, args: list[str], timeout: int = 60, binary: bool = False):
    from .ux_video import _adb as adb_call
    return adb_call(serial, args, timeout=timeout, binary=binary)


def _start_video(serial: str, seconds: int) -> "subprocess.Popen | None":
    """Record the journey. Evidence a human can review without re-running it."""
    from .emulator import ADB
    try:
        return subprocess.Popen(
            [ADB, "-s", serial, "shell", "screenrecord", "--time-limit",
             str(max(15, int(seconds) + 20)), "/sdcard/ux-flow.mp4"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None


def _stop_video(serial: str, dest: Path) -> str:
    """Stop the recording, pull it beside the stills, return its path or ""."""
    try:
        _adb(serial, ["shell", "pkill", "-INT", "screenrecord"], timeout=20)
        time.sleep(1.2)
        _adb(serial, ["pull", "/sdcard/ux-flow.mp4", str(dest)], timeout=120)
        _adb(serial, ["shell", "rm", "-f", "/sdcard/ux-flow.mp4"], timeout=20)
    except Exception:
        return ""
    return str(dest) if dest.is_file() and dest.stat().st_size > 0 else ""


def _screenshot(serial: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = _adb(serial, ["exec-out", "screencap", "-p"], timeout=60, binary=True)
    data = r.stdout if isinstance(r.stdout, bytes) else (r.stdout or "").encode()
    if data.startswith(b"\x89PNG"):
        dest.write_bytes(data)
        return True
    return False


def _mock_token() -> str:
    """The token the local mock issues, so the delivered callback is a real one."""
    from .mock_server import MOCK_TOKEN
    return MOCK_TOKEN


def _launcher_component(serial: str, pkg: str) -> str:
    r = _adb(serial, ["shell", "cmd", "package", "resolve-activity", "--brief", pkg], timeout=45)
    text = r.stdout or ""
    for line in text.splitlines():
        line = line.strip()
        if "/" in line and pkg in line:
            return line
    return ""


def _launch(serial: str, pkg: str, base_url: str | None = None) -> None:
    """Open the app; with a mock, say where the API lives.

    `monkey` cannot carry extras, and the base-URL handoff has to be an intent
    extra. The DEBUG build honours it (a release build ignores it), so this is the
    one seam that lets an audit reach a local backend without any production code
    path being different.
    """
    component = _launcher_component(serial, pkg) if base_url else ""
    if component:
        args = ["shell", "am", "start", "-n", component,
                "--es", "devteam_mock_base_url", str(base_url)]
        _adb(serial, args, timeout=45)
        return
    _adb(serial, ["shell", "monkey", "-p", pkg, "-c",
                  "android.intent.category.LAUNCHER", "1"], timeout=45)


def _settle(serial: str, pkg: str, timeout: float = 12.0) -> tuple[bool, float]:
    """Wait for the launch screen to become the app's own, tappable UI.

    A screenshot taken 1.6s after `am start` catches the splash on a slow boot and
    the model then judges a logo as the product ("nothing is loading or tappable")
    — a false P0 that hides the real one. "Settled" is: the focused window belongs
    to the app AND the accessibility tree exposes at least one label. A flow that
    never settles is a genuine finding, returned as settled=False with the wait.
    """
    from .ux_video import _ui_xml
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        time.sleep(0.7)
        if not _window_title(serial, pkg):
            continue
        if tappables(_ui_xml(serial), pkg):
            return True, time.monotonic() - t0
    return False, time.monotonic() - t0


_BUSY_WORDS = ("loading", "please wait", "searching", "just a moment",
               "sending your", "reading your library")

def _wait_past_busy(serial: str, timeout: float = 10.0) -> None:
    """A spinner is a STATE, not a verdict — wait for it before judging.

    The walk screenshots at the top of every step, so a tap that starts a fetch was
    photographed 0.5s later: the model saw "Loading book details from catalog…",
    found nothing to tap, and declared the screen a dead end while the detail
    actually renders in ~2s (confirmed by hand, 2026-11-10). Waiting for the app
    to stop saying it is busy is deterministic and costs nothing when it is not.
    """
    from .ux_video import _ui_xml
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        xml = (_ui_xml(serial) or "").lower()
        if not any(w in xml for w in _BUSY_WORDS):
            return
        time.sleep(0.8)


_SCREEN_SIZE: dict[str, tuple[int, int]] = {}

def _screen_size(serial: str) -> tuple[int, int]:
    """The device's pixel size, asked once — a swipe needs real coordinates."""
    kept = _SCREEN_SIZE.get(serial)
    if kept:
        return kept
    r = _adb(serial, ["shell", "wm", "size"], timeout=30)
    text = (r.stdout or "") if isinstance(r.stdout, str) else ""
    for token in text.replace("Physical size:", " ").replace("Override size:", " ").split():
        if "x" in token:
            try:
                w, h = token.split("x")[:2]
                size = (int(w), int(h))
            except Exception:
                continue
            _SCREEN_SIZE[serial] = size
            return size
    return (1080, 2400)


def _window_title(serial: str, pkg: str) -> str:
    """The focused activity's short name — never a fragment of it.

    The old pattern let a greedy class match eat the ident and captured one
    trailing LETTER, so a finding read "tapping 'x' on y does nothing" (seen
    2026-11-08). Take the component after the package, then its last segment.
    """
    r = _adb(serial, ["shell", "dumpsys", "window"], timeout=60)
    text = r.stdout or ""
    for line in text.splitlines():
        if "mCurrentFocus" not in line or pkg not in line:
            continue
        m = re.search(rf"{re.escape(pkg)}/([\w.$]+)", line)
        if not m:
            return "app"
        short = m.group(1).rsplit(".", 1)[-1]
        return short if len(short) >= 3 else "app"
    if "Application Not Responding" in text or " isn't responding" in text:
        return "ANR dialog"
    return ""


def _ask(cfg, prompt: str, png: Path) -> dict:
    """One vision decision. A model failure is loud, never a silent guess."""
    from . import vision_layout
    raw = vision_layout._call(cfg, prompt, png)
    return vision_layout._json_block(raw)


def _sheet(stills: list[Path], out: Path) -> Path | None:
    """Tile the step stills into one image the judge can read in order."""
    ffmpeg = __import__("shutil").which("ffmpeg")
    if not ffmpeg or not stills:
        return stills[-1] if stills else None
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = 3
    rows = max(1, (len(stills) + cols - 1) // cols)
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\nduration 1\n" for p in stills))
    vf = (f"scale=360:-1,tile={cols}x{rows}:padding=6:margin=6:color=0x101418")
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-vf", vf, "-frames:v", "1", str(out)],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not out.is_file():
        return stills[-1]
    return out


def audit_flow(project: Path, flow: dict, serial: str, log=None,
               max_steps: int | None = None, via: str = "api",
               base_url: str | None = None, signed_in=None) -> dict:
    """Drive one goal-declared journey and judge what a person would hit."""
    from . import ux_video, vision_layout

    fid = str(flow["id"])
    goal = str(flow.get("goal") or "").strip()
    cap = int(max_steps or flow.get("max_steps") or DEFAULT_STEPS)
    cap = max(2, min(cap, MAX_STEPS_CAP))
    deadline = time.monotonic() + int(flow.get("seconds") or DEFAULT_SECONDS)
    pkg = ux_video.package_name(project)
    app = flow.get("app") or project.name
    cfg = vision_layout.config()

    out_dir = run_dir(project) / _slug(fid)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("step-*.png"):
        old.unlink()

    ux_video._wake(serial)
    _adb(serial, ["logcat", "-b", "crash", "-c"], timeout=30)
    _adb(serial, ["shell", "am", "force-stop", pkg], timeout=30)
    if flow.get("fresh"):
        cleared = _adb(serial, ["shell", "pm", "clear", pkg], timeout=60)
        word = ((cleared.stdout or "") if isinstance(cleared.stdout, str) else "").strip()
        if not word.lower().startswith("success"):
            raise SystemExit(f"ux-flow: could not clear app data for '{pkg}' (flow is "
                             f"declared fresh) — got {word or 'no answer'}")
    _launch(serial, pkg, base_url=base_url)
    settled, waited = _settle(serial, pkg)
    if not settled:
        # ONE retry, with room. A cold start right after `pm clear` on a headless
        # emulator outlasts the 12s cap often enough that judging it produced a
        # false "the app never opened" (2026-11-10, the third flow of a run). The
        # second attempt is the same launch; only the patience changes.
        if log:
            log(f"~ ux-flow: '{fid}' did not settle in {waited:.1f}s — relaunching once")
        _launch(serial, pkg, base_url=base_url)
        settled, waited = _settle(serial, pkg, timeout=30.0)
    if log:
        log(f"~ ux-flow: '{fid}' launch screen settled after {waited:.1f}s"
            + ("" if settled else " — THE APP NEVER LEFT ITS LAUNCH SCREEN"))

    steps: list[dict] = []
    stills: list[Path] = []
    if not settled:
        still = out_dir / "step-01.png"
        _screenshot(serial, still)
        stills.append(still)
        result = {
            "flow": fid, "surface": str(flow.get("surface") or ""), "goal": goal,
            "outcome": "stuck",
            "reason": (f"the app never left its launch screen: no tappable UI from "
                       f"{pkg} after {waited:.0f}s"),
            "steps": [{"n": 1, "did": "waited for the app to open",
                       "then": f"still no app UI after {waited:.0f}s",
                       "screen": "", "action": "stuck"}],
            "dead_taps": [], "crashes": [], "goal_reached": False,
            "verdict": f"the journey never starts: the app does not open in {waited:.0f}s",
            "reactions": [], "premium": [], "defects": [], "model_error": "",
            "stills": [str(p) for p in stills], "sheet": str(still),
            "rubric": RUBRIC, "at": now_iso(),
        }
        (out_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        _write_record(project, {"flow": fid, "step": 1, "state": "done",
                                "outcome": "stuck", "goal_reached": False,
                                "verdict": result["verdict"]})
        return result

    video_path = out_dir / "video.mp4"
    _start_video(serial, int(flow.get("seconds") or DEFAULT_SECONDS))

    tried: dict[str, set[str]] = {}
    filled: set[str] = set()
    frame_digests: list[str] = []
    model_calls = 0
    live_reactions: list[dict] = []
    typed = 0
    outcome = "budget"
    reason = ""
    crashes: list[str] = []
    dead: list[dict] = []
    _write_record(project, {"flow": fid, "step": 0, "verdict": "", "state": "running"})

    for step in range(1, cap + 1):
        if time.monotonic() > deadline:
            outcome, reason = "timeout", f"the {int(flow.get('seconds') or DEFAULT_SECONDS)}s cap was reached"
            break
        if step > 1:
            # Never photograph a spinner and call it the product.
            _wait_past_busy(serial)
        still = out_dir / f"step-{step:02d}.png"
        if not _screenshot(serial, still):
            outcome, reason = "error", "could not capture the screen"
            break
        # EVIDENCE HYGIENE: an unchanged frame is the same evidence twice. Keep
        # one copy instead of storing a duplicate the judge must read again.
        digest = hashlib.sha1(still.read_bytes()).hexdigest()
        if stills and digest == frame_digests[-1]:
            still.unlink(missing_ok=True)
            still = stills[-1]
        else:
            stills.append(still)
            frame_digests.append(digest)
        if step > 1:
            crash = ux_video._crash_line(serial, pkg)
            if crash:
                crashes.append(crash)
                outcome, reason = "crashed", crash
                break
        xml = ux_video._ui_xml(serial)
        targets = tappables(xml, pkg)
        screen = _window_title(serial, pkg)
        if log and any(x.get("editable") for x in targets):
            log("~ ux-flow: fields on this screen: " + ", ".join(
                f"{i}:{t['label'] or '(unlabelled)'}" for i, t in enumerate(targets)
                if t.get("editable")))
        history = ("\n".join(f"  {s['n']}. {s['did']} → {s['then']}" for s in steps)
                   or "  (nothing yet)")
        dead_line = ""
        if tried.get(screen):
            dead_line = ("Already tried on this screen with no visible change (do NOT "
                         "repeat): " + ", ".join(sorted(tried[screen])) + "\n")
        creds = flow.get("credentials") or {}
        profile = flow.get("profile") or {}
        credentials = ("CREDENTIALS for this journey: email and password are available — "
                       "use action type with field=email/password.\n"
                       if creds.get("email") and creds.get("password") else
                       "CREDENTIALS: none. You cannot sign in or create an account: a "
                       "text field is where a human takes over.\n")
        if profile.get("name") or profile.get("handle"):
            credentials += (
                "PROFILE values for a shelf-onboarding form: "
                + ", ".join(f"{k}={v}" for k, v in profile.items() if v)
                + " — use action type with field=name/handle.\n"
            )
        prompt = DECIDE_PROMPT.format(
            app=app, flow=fid, goal=goal, step=step, max_steps=cap,
            history=history, filled=(", ".join(sorted(filled)) or "(none yet)"),
            screen=(f"Screen: {screen}" if screen else ""),
            credentials=credentials,
            targets=_label_list(targets), dead=dead_line)
        model_calls += 1
        decision = _ask(cfg, prompt, still)
        live = decision.get("reaction")
        if isinstance(live, dict) and str(live.get("line") or "").strip():
            if log:
                log(f"~ ux-flow: '{fid}' step {step} — "
                    f"\"{str(live['line']).strip()[:160]}\" "
                    f"({str(live.get('kind') or 'confusing')})")
            live_reactions.append({
                "at": f"step {step}",
                "line": str(live["line"]).strip()[:240],
                "kind": str(live.get("kind") or "confusing").strip().lower()[:16],
                "screen": _window_title(serial, pkg),
                "still": str(still),
            })
        action = str(decision.get("action") or "").strip().lower()
        _write_record(project, {"flow": fid, "step": step, "verdict": "",
                                "state": "deciding", "action": action})

        if action == "done":
            outcome, reason = "done", str(decision.get("why") or "")
            steps.append({"n": step, "did": "reached the goal", "then": reason,
                          "screen": screen, "action": "done"})
            break
        if action in ("stuck", "ask"):
            # A wall is not a defect: with no credentials the agent cannot sign in,
            # the same way it cannot pay or pass a CAPTCHA. Say so, and do not let a
            # judge turn "I have no password" into "the app is broken".
            needs_a_person = (
                not (flow.get("credentials") or {}).get("password")
                and any(x.get("editable") for x in targets))
            outcome = "needs_credentials" if needs_a_person else "stuck"
            reason = str(decision.get("why") or "")
            steps.append({"n": step, "did": "stopped", "then": reason,
                          "screen": screen, "action": "stuck"})
            break
        if action == "scroll":
            direction = str(decision.get("direction") or "down").strip().lower()
            w, hh = _screen_size(serial)
            x = w // 2
            if direction == "up":
                y0, y1 = int(hh * 0.35), int(hh * 0.80)
            else:
                y0, y1 = int(hh * 0.80), int(hh * 0.35)
            _adb(serial, ["shell", "input", "swipe", str(x), str(y0), str(x), str(y1),
                          "400"], timeout=45)
            time.sleep(0.9)
            steps.append({"n": step, "did": f"scrolled {direction}",
                          "then": "the screen moved", "screen": screen,
                          "action": "scroll"})
            continue

        if action == "back":
            _adb(serial, ["shell", "input", "keyevent", "4"], timeout=30)
            time.sleep(1.0)
            steps.append({"n": step, "did": "pressed back", "then": "left the screen",
                          "screen": screen, "action": "back"})
            continue
        if action == "type":
            typed += 1
            field = str(decision.get("field") or "").strip().lower()
            creds = flow.get("credentials") or {}
            profile = flow.get("profile") or {}
            value = creds.get(field) or profile.get(field) or ""
            idx = _pick_field(targets, field)
            if not isinstance(idx, int) or not (0 <= idx < len(targets)) or not value:
                outcome, reason = "error", (
                    f"type needs a listed index and an available credential "
                    f"(got index {idx!r}, field {field!r})")
                break
            target = targets[idx]
            label = (target["label"] or "").strip()
            # The field's own label can be empty in a Compose dump, so trust the
            # model's declared field when it names a credential we actually hold.
            role = (field if field in creds or field in profile else
                    "email" if any(k in label.lower() for k in ("email", "mail"))
                    else "password" if any(k in label.lower() for k in ("pass", "secret"))
                    else "field")
            if role in filled or typed > len(creds) + len(profile):
                # Typing again APPENDS: the field read "…testaudit-pass-123" and the
                # sign-in then failed for a reason the app never caused.
                outcome, reason = "error", (
                    f"the model tried to fill the {role} field again (already filled: "
                    f"{', '.join(sorted(filled)) or 'none'})")
                break
            _adb(serial, ["shell", "input", "tap", str(target["x"]), str(target["y"])],
                 timeout=30)
            time.sleep(0.4)
            # Clear whatever was there, so a retype replaces instead of appending.
            _adb(serial, ["shell", "input", "keyevent", *(["67"] * 60)], timeout=60)
            _adb(serial, ["shell", "input", "text", value.replace(" ", "%s")], timeout=30)
            time.sleep(0.6)
            typed = value[:4].lower() in (ux_video._ui_xml(serial) or "").lower()
            steps.append({"n": step, "did": f"typed the {field} into "
                                            f"{target['label'] or 'the field'}",
                          "then": ("the field accepted it" if typed else
                                   "the field did not show the text"),
                          "screen": screen, "action": "type"})
            if not typed:
                dead.append({"step": step, "label": target["label"],
                             "screen": screen})
            filled.add(role)
            # The keyboard covers the submit control on a 6.4" phone; close it so the
            # next decision sees the screen a person would see after typing.
            _adb(serial, ["shell", "input", "keyevent", "4"], timeout=30)
            time.sleep(0.4)
            continue

        index = decision.get("index")
        if action != "tap":
            outcome, reason = "error", f"the model answered {json.dumps(decision)[:120]}"
            break

        if not isinstance(index, int) or not (0 <= index < len(targets)):
            outcome, reason = "error", f"tap index {index!r} is not a listed target"
            break
        target = targets[index]
        before = ux_video._screen_sig(serial)
        _adb(serial, ["shell", "input", "tap", str(target["x"]), str(target["y"])],
             timeout=30)
        time.sleep(1.4)
        after = ux_video._screen_sig(serial)
        is_dead = bool(before and after and before == after) and not target.get("editable")
        if base_url and (target.get("label") or "").lower().startswith("continue with"):
            time.sleep(1.2)
            _adb(serial, ["shell", "am", "start", "-a", "android.intent.action.VIEW",
                          "-d", f"bookswap://auth-callback#access_token={_mock_token()}"
                                f"&refresh_token=mock-refresh&expires_in=3600&token_type=bearer"],
                 timeout=45)
            # Completing a session is not instant (user fetch → profile fetch →
            # navigation). A fixed sleep judged the previous screen; a screen-change
            # poll broke on the Custom Tab closing, 1s before the feed. Wait for the
            # ground truth instead: the mock has answered the feed's first call.
            time.sleep(0.8)
            if signed_in is not None:
                for _ in range(30):
                    time.sleep(0.4)
                    if signed_in():
                        break
            else:
                before_cb = ux_video._screen_sig(serial)
                for _ in range(16):
                    time.sleep(0.5)
                    if ux_video._screen_sig(serial) != before_cb:
                        break
            time.sleep(1.2)
            steps.append({"n": step, "did": f"tapped {target['label']}",
                          "then": "the provider returned to bookswap://auth-callback "
                                  "(delivered by the runner: a mock cannot drive a browser)",
                          "screen": screen, "action": "tap", "label": target["label"]})
            continue

        entry = {"n": step, "did": f"tapped {target['label'] or '(unlabelled)'}",
                 "then": ("nothing changed — DEAD CONTROL" if is_dead else
                          ("focus moved into a text field" if target.get("editable")
                           else "the screen changed")),
                 "screen": screen, "action": "tap", "label": target["label"],
                 "dead": is_dead}
        steps.append(entry)
        if is_dead:
            dead.append({"step": step, "label": target["label"], "screen": screen})
            tried.setdefault(screen, set()).add(target["label"] or "(unlabelled)")
        after_crash = ux_video._crash_line(serial, pkg)
        if after_crash:
            crashes.append(after_crash)
            outcome, reason = "crashed", after_crash
            break

    if outcome == "budget":
        reason = f"{cap} step(s) used without reaching the goal"
    _write_record(project, {"flow": fid, "step": len(steps), "state": "judging",
                            "verdict": ""})

    # The judge reads the journey, not one frame.
    sheet_path = _sheet(stills, out_dir / f"{_slug(fid)}-trajectory.png")
    verdict = {"goal_reached": outcome == "done", "verdict": "", "reactions": [],
               "defects": [], "premium": []}
    if outcome == "error":
        # The DRIVER failed (a malformed decision, a tap it could not place). Asking
        # the judge to describe a journey that never happened produced invented
        # P0s from a single screenshot, so the error is reported as itself.
        verdict["verdict"] = f"the audit could not drive this journey: {reason}"
    elif sheet_path is not None:
        history = "\n".join(f"  {s['n']}. {s['did']} → {s['then']}" for s in steps)
        try:
            model_calls += 1
            notes = "".join(
                f"  {r['at']} ({r.get('screen') or 'screen'}): {r['line']} [{r['kind']}]\n"
                for r in live_reactions) or "  (no note taken during the walk)\n"
            verdict = _ask(cfg, JUDGE_PROMPT.format(
                app=app, flow=fid, goal=goal, outcome=outcome, history=history,
                notes=notes),
                Path(sheet_path))
        except SystemExit as exc:      # a model failure is recorded, never faked
            verdict["error"] = str(exc)[:200]

    result = {
        "flow": fid,
        "surface": str(flow.get("surface") or ""),
        "goal": goal,
        "outcome": outcome,
        "reason": reason,
        "steps": steps,
        "dead_taps": dead,
        "crashes": crashes,
        "goal_reached": bool(verdict.get("goal_reached") or outcome == "done"),
        "verdict": str(verdict.get("verdict") or ""),
        "reactions": (verdict.get("reactions") or []) + [
            {k: r[k] for k in ("at", "line", "kind", "still") if r.get(k)}
            for r in live_reactions
            if str(r["line"]).lower() not in
            " ".join(str(x.get("line", "")).lower() for x in (verdict.get("reactions") or []))
        ],
        "premium": verdict.get("premium") or [],
        "defects": verdict.get("defects") or [],
        "model_error": verdict.get("error") or "",
        # A walk can be blocked by something that is NOT the app: a provider that
        # needs a human's password, a device that will not boot, a missing model
        # key. Recording the ask here is what lets `ux-flow`, `verify`, `doctor`
        # and `asks` all name the same owner action instead of filing a product
        # defect nobody can fix in code.
        # A crash is the APP's, never the owner's: only a walk with no crash on the
        # device may be handed back as "this needs a human".
        "unblock": ("" if crashes else human_block(reason, verdict.get("error")) or ""),
        "blocked_on": ("owner" if (not crashes and human_block(reason, verdict.get("error")))
                       else ""),
        "stills": [str(p) for p in stills],
        "sheet": str(sheet_path) if sheet_path else "",
        "rubric": RUBRIC,
        "video": _stop_video(serial, video_path),
        "model_calls": model_calls,
        "at": now_iso(),
    }
    (out_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    _write_record(project, {"flow": fid, "step": len(steps), "state": "done",
                            "outcome": outcome, "verdict": result["verdict"],
                            "goal_reached": result["goal_reached"]})
    return result


# --------------------------------------------------------------------------- #
# findings -> the ux inventory the planner already reads
# --------------------------------------------------------------------------- #
def _severity(defect: dict) -> str:
    sev = str(defect.get("severity") or "").upper()
    if "P0" in sev:
        return "P0_BLUNDER"
    if "P1" in sev:
        return "P1_DEGRADED"
    return "P2_POLISH"


def store(project: Path, result: dict, log=None) -> dict:
    """Write the flow's findings into ux_audit.json on its declared surface."""
    from . import ux_audit

    label = result["surface"]
    if not label:
        if log:
            log(f"~ ux-flow: '{result['flow']}' has no surface — findings not stored")
        return {"stored": 0, "retired": 0}
    data = ux_audit.load(project)
    key = label.strip().lower()
    row = (data.get("surfaces") or {}).get(key) or {
        "label": label, "scene": None, "render": "", "gripes": [],
    }

    # Deterministic findings come first: a dead control is never a judgement call.
    findings = []
    for d in result["dead_taps"]:
        findings.append((f"tapping '{d['label']}' on {d['screen'] or 'the flow screen'} "
                         f"does nothing — a dead control", d["label"], "P1_DEGRADED",
                         "flow", "dead-click"))
    for c in result["crashes"]:
        findings.append((f"the app crashed during the {result['flow']} journey: {c}",
                         "", "P0_BLUNDER", "flow", "crash"))
    if result["outcome"] in ("needs_credentials", "error"):
        # Nothing is written: the journey stopped because the AUDITOR has no
        # password. Recording that as a defect would bury the app's real findings
        # under a limitation of the tool.
        pass
    elif result["outcome"] == "stuck" and result.get("reason"):
        findings.append((f"the {result['flow']} journey cannot be completed: {result['reason']}",
                         "", "P1_DEGRADED", "flow", "blocked"))
    if result["outcome"] in ("budget", "timeout"):
        findings.append((f"the {result['flow']} journey does not finish: {result['reason']}",
                         "", "P1_DEGRADED", "flow", "loops"))
    for d in result["defects"]:
        line = str((d or {}).get("line") or "").strip() if isinstance(d, dict) else str(d)
        if not line:
            continue
        findings.append((line, str((d or {}).get("element") or ""),
                         _severity(d if isinstance(d, dict) else {}), "flow", ""))

    open_now = []
    for line, element, severity, kind, tag in findings:
        gid = ux_audit.gripe_id(result["flow"], line)
        open_now.append(gid)
        row.setdefault("gripes", [])
        existing = next((g for g in row["gripes"] if g.get("id") == gid), None)
        if existing:
            existing.update({"status": "open", "severity": severity, "line": line,
                             "element": element or existing.get("element", "")})
            continue
        row["gripes"].append({
            "id": gid, "line": line, "element": element, "kind": kind,
            "severity": severity, "status": "open", "source": "flow",
            "flow": result["flow"], "tag": tag, "at": now_iso(),
        })

    retired = 0
    for g in row.get("gripes") or []:
        # ONLY this flow may retire its own findings. Two journeys can share a
        # surface, and retiring a sibling's gripes because THIS one did not step on
        # them wiped a valid P0 the moment the next flow ran (seen 2026-11-08: the
        # first-run findings vanished when request-a-book was audited next).
        if g.get("flow") and g.get("flow") != result["flow"]:
            continue
        if g.get("status") == "open" and g.get("id") not in open_now:
            g["status"] = "gone"
            g["gone_at"] = now_iso()
            g["gone_reason"] = (f"not reproduced by the {result['flow']} journey on "
                                f"{now_iso()[:10]} (outcome: {result['outcome']})")
            retired += 1

    row.update({"flow": result["flow"], "flow_rubric": RUBRIC,
                "flow_checked_at": result["at"],
                "flow_verdict": result["verdict"],
                "flow_outcome": result["outcome"]})
    data.setdefault("surfaces", {})[key] = row
    ux_audit.save(project, data)
    if log:
        log(f"~ ux-flow: '{result['flow']}' → {len(open_now)} finding(s) on '{label}', "
            f"{retired} earlier finding(s) retired")
    return {"stored": len(open_now), "retired": retired}


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def run(project: Path, only: str | None = None, log=None, avd: str | None = None,
        keep_emulator: bool = False, max_steps: int | None = None,
        worktree: Path | None = None, mock: bool = False,
        mock_port: int | None = None) -> dict:
    """Audit every goal-declared flow (or one), on a device this pass owns."""
    from . import emulator

    todo = declared(project, root=(Path(worktree) if worktree else None))
    if only:
        todo = [f for f in todo if str(f.get("id")) == only]
        if not todo:
            raise SystemExit(
                f"ux-flow: no flow {only!r} with a `goal` in {FLOWS_REL} — a flow "
                "this pass can audit declares what should be true, not the taps "
                "(scripted flows belong to `devteam ux-video`)")
    if not todo:
        return {"planned": 0, "run": [], "skipped": [], "failed": []}

    server = None
    base_url = None
    if mock:
        from . import mock_server
        server = mock_server.MockServer(mock_port or mock_server.DEFAULT_PORT).start()
        if not server.wait_ready():
            raise SystemExit("ux-flow: the mock backend did not come up")
        base_url = server.url
        if log:
            log(f"~ ux-flow: mock backend on {base_url} (deterministic fixtures) — "
                f"the debug build is pointed at it, no production path changes")

    serial, started = emulator.require_device(avd=avd, log=log,
                                              purpose="ux-flow")
    results, failed, skipped = [], [], []
    try:
        from . import ux_video
        build = ux_video.build_and_install(project, serial, log,
                                           tree=Path(worktree) if worktree else None)
        build["source"] = ("worktree: " + str(worktree)) if worktree else "delivery (origin/main)"
        if log:
            log(f"~ ux-flow: auditing {build['source']}")
        for flow in todo:
            if mock and not (flow.get("credentials") or {}).get("password"):
                # The mock accepts these; a real backend would not, so they are
                # injected only for an audited mock run and never live in the spec.
                flow = {**flow, "credentials": {"email": "audit@bookswap.test",
                                                "password": "audit-pass-123"}}
            fid = str(flow["id"])
            try:
                res = audit_flow(
                    project, flow, serial, log=log, max_steps=max_steps,
                    base_url=base_url,
                    signed_in=(lambda: any("/functions/v1/ui-composer" in r["path"]
                                           for r in server.requests)) if server else None)
            except SystemExit as exc:
                failed.append({"id": fid, "reason": str(exc)[:300]})
                continue
            except Exception as exc:  # a tool/device fault, never a traceback in a walk
                msg = f"{type(exc).__name__}: {exc}"[:300]
                ask = human_block(msg)
                if log:
                    log(f"~ ux-flow: '{fid}' could not be walked — {msg}"
                        + (f" (needs the owner: {ask})" if ask else ""))
                results.append({
                    "flow": fid, "surface": str(flow.get("surface") or ""),
                    "goal": str(flow.get("goal") or ""), "outcome": "error",
                    "reason": msg, "steps": [], "dead_taps": [], "crashes": [],
                    "goal_reached": False, "verdict": "", "reactions": [],
                    "premium": [], "defects": [], "model_error": "",
                    "unblock": ask or "", "blocked_on": "owner" if ask else "",
                    "stills": [], "sheet": "", "rubric": RUBRIC, "at": now_iso(),
                })
                continue
            res["build"] = build
            res["mock"] = base_url or ""
            results.append(res)
            store(project, res, log=log)
    finally:
        if server is not None:
            res_requests = server.requests
            if log:
                log(f"~ ux-flow: mock backend answered {len(res_requests)} request(s)")
            for r in results:
                r["mock_requests"] = res_requests
            server.stop()
        if started and not keep_emulator:
            stopped = emulator.stop(serial)
            if log:
                log(f"~ ux-flow: emulator {serial} "
                    f"{'stopped' if stopped else 'DID NOT STOP — stop it manually'}")
        elif started and log:
            log(f"~ ux-flow: leaving emulator {serial} running (--keep-emulator)")
    return {"planned": len(todo), "run": results, "skipped": skipped, "failed": failed}


# WHAT A BLOCKED AUDIT NEEDS A HUMAN FOR. A walk can stop for reasons that are
# not the app at all — a provider that needs a real password and a 2FA tap, a
# device that will not boot, a vision model with no key. Those are the owner's to
# unblock, and the audit says so out loud instead of reporting them as product
# defects the developer chases for hours (owner, 2026-11-10: "if ux flow get
# blocked it should let me know, I can help unblock").
_HUMAN_BLOCKS: tuple[tuple[str, str], ...] = (
    (r"credential|password|two[ -]?factor|2fa|verification code|sign[ -]?in as",
     "sign in once on the device with your own account, then tell the agent "
     "\"signed in\" — the walk continues from that screen"),
    (r"unauthori[sz]ed|not authorized|adb .*offline|no (?:emulator|device)|"
     r"emulator .*(?:not boot|did not start|failed to start)|no avd",
     "get a device in place: start an emulator (`android emulator start <avd>`) or "
     "plug a phone in, accept the USB debugging prompt, then say \"device ready\""),
    (r"model|api key|judge could not|no vision",
     "provide the vision model credentials (`agentconfig`), then say \"model ready\" "
     "— the audit cannot judge a screenshot without one"),
    (r"install_failed|install failed|not installed|user rejected",
     "approve the app install on the device (a dialog is waiting), then say "
     "\"installed\""),
    (r"could not capture|never left its launch screen|home launcher|screencap|"
     r"ui (?:dump|hierarchy) (?:failed|empty)|device offline",
     "the device stopped answering mid-walk: restart it (or free memory) and say "
     "\"device restarted\" — the walk re-runs from the start"),
)


def human_block(*texts: str | None) -> str | None:
    """The owner action that would unblock this walk, or None if it is not theirs.

    Deterministic and offline: the same sentence on the same evidence always asks
    the same person for the same thing, so the ask can be printed by `ux-flow`,
    `verify`, `doctor` and `asks` without any of them guessing.
    """
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for pattern, ask in _HUMAN_BLOCKS:
        if re.search(pattern, blob):
            return ask
    return None


def active_block(project: Path) -> dict | None:
    """The first recorded walk that is waiting on the owner, if any."""
    rows = []
    for flow in declared(project):
        result = run_dir(project) / _slug(str(flow["id"])) / "result.json"
        try:
            data = json.loads(result.read_text())
        except Exception:
            continue
        if data.get("crashes"):
            continue  # a crash is the app's, not the owner's
        ask = data.get("unblock") or human_block(data.get("reason"), data.get("model_error"))
        if ask and not data.get("goal_reached"):
            rows.append({"flow": str(flow["id"]), "ask": ask,
                         "reason": str(data.get("reason") or "")[:200]})
    return rows[0] if rows else None


def status_report(project: Path) -> str:
    """One line per goal-declared flow, plus an interrupted run if one is on disk."""
    rec = read_record(project)
    rows = declared(project)
    lines = [f"UX FLOW — {len(rows)} goal-declared flow(s) in {FLOWS_REL}"]
    for f in rows:
        fid = str(f["id"])
        res = run_dir(project) / _slug(fid) / "result.json"
        if not res.is_file():
            lines.append(f"  · never run    {fid[:30]:30} · {f.get('surface') or '—'}")
            continue
        try:
            data = json.loads(res.read_text())
        except Exception:
            lines.append(f"  · unreadable   {fid[:30]}")
            continue
        mark = "✓" if data.get("goal_reached") else "✗"
        lines.append(f"  {mark} {data.get('outcome', '?'):9} {fid[:30]:30} "
                     f"· {len(data.get('steps') or [])} step(s) · "
                     f"{len(data.get('dead_taps') or [])} dead · "
                     f"{len(data.get('crashes') or [])} crash(es)")
    if rec.get("state") == "running":
        lines.append(f"  ⚠ interrupted at step {rec.get('step', 0)} of '{rec.get('flow')}' "
                     f"({rec.get('at')}) — re-run `devteam ux-flow {project.name}`")
    if not rows:
        lines.append("  no goal-declared flow — add one with a `goal` and no `steps`")
    return "\n".join(lines)


def walk_problem(project: Path, flow: dict) -> str | None:
    """What keeps this journey from passing the release audit, or None if it passes.

    One place decides what "the ux-flow audit passed" means, so `verify`,
    `release` and `doctor` cannot disagree: the last walk exists, it did not
    crash the app, and it reached the declared goal. A walk that stopped at a
    wall is a FINDING, not a passing audit — that is the whole point of walking
    the journey (owner policy 2026-11-10: this audit is mandatory for release).
    """
    result = run_dir(project) / _slug(str(flow["id"])) / "result.json"
    if not result.is_file():
        return "never walked"
    try:
        data = json.loads(result.read_text())
    except Exception as exc:  # an unreadable walk is not a passing walk
        return f"last walk unreadable ({exc})"
    crashes = data.get("crashes") or []
    if crashes:
        return f"last walk crashed the app ({len(crashes)} crash(es))"
    if not data.get("goal_reached"):
        reason = str(data.get("reason") or data.get("verdict") or "").strip()
        hint = ""
        # A journey that stops at sign-in was walked without the deterministic
        # backend: a real provider needs a human's credentials, so the walk can
        # never finish that way. The mock completes the same flow through the
        # app's own auth seam — say so instead of leaving the reader stuck on a
        # wall the audit built itself.
        if re.search(r"credential|sign[ -]?in|oauth|password|log[ -]?in", reason, re.I):
            hint = (" — walk it with `--mock`: the local backend completes sign-in "
                    "through the app's own auth seam, which a real provider cannot do "
                    "without a human's credentials")
        return (f"last walk did not reach the goal ({data.get('outcome') or '?'})"
                + (f": {reason[:160]}" if reason else "") + hint)
    return None


def audit_problems(project: Path) -> list[dict]:
    """Every declared journey that does not pass the release audit, with the why."""
    return [{"id": str(f["id"]), "surface": f.get("surface") or "",
             "why": problem}
            for f in declared(project)
            for problem in [walk_problem(project, f)] if problem]


def stale_for_files(project: Path, changed: list[str]) -> list[dict]:
    """Declared journeys whose screen this diff touches, or that never ran.

    Deterministic: a journey is stale when a file under its `surface` screen (as
    `ux_audit.surfaces` resolves it) is in the diff, or when it has no result at
    all. It never guesses about the code — it names what nobody has walked.
    """
    from . import ux_audit
    rows = declared(project)
    if not rows:
        return []
    surfaces = {s["label"].strip().lower(): s for s in ux_audit.surfaces(project)}
    out = []
    for flow in rows:
        fid = str(flow["id"])
        result = run_dir(project) / _slug(fid) / "result.json"
        if not result.is_file():
            out.append({"id": fid, "why": "never walked", "surface": flow.get("surface") or ""})
            continue
        surface = surfaces.get(str(flow.get("surface") or "").strip().lower())
        if not surface:
            continue
        touched = {Path(c).as_posix() for c in changed}
        stale = [p for p in (surface.get("paths") or [])
                 if Path(p).as_posix() in touched
                 or any(Path(c).name == Path(p).name for c in touched)]
        if not stale:
            continue
        # A journey walked AFTER the change is current: compare the walk against the
        # touched file's own mtime, so shipping the release that fixes a finding does
        # not immediately re-report every journey as stale.
        walked_at = 0.0
        try:
            walked_at = result.stat().st_mtime
        except OSError:
            pass
        touched_at = 0.0
        for p in stale:
            try:
                touched_at = max(touched_at, (project / p).stat().st_mtime)
            except OSError:
                touched_at = max(touched_at, Path(p).stat().st_mtime if Path(p).exists() else 0)
        if touched_at > walked_at:
            out.append({"id": fid, "why": f"{Path(stale[0]).name} changed since it was walked",
                        "surface": surface["label"]})
    return out
