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

DECIDE_PROMPT = """You are using a mobile app like a real person, to check ONE journey works.

App: {app}
Journey: {flow}
Goal: {goal}
Step {step} of {max_steps}.
Already done: {history}
{screen}

The screenshot IS the current screen. The list below is what is tappable on it
(read from the app's accessibility tree — tap by INDEX, never invent a place).

Tappable:
{targets}
{dead}
Choose the SINGLE next action that moves a real user closest to the goal:
  tap     — an index from the list above
  back    — leave a screen that cannot progress this journey
  done    — the goal is reached on this screen
  stuck   — the app blocks a person here (say what is missing), or the journey
            cannot continue from this screen at all

Never repeat an action that already did nothing on this screen. Never tap a
destructive or payment control.

Reply with JSON only, no prose, no code fences:
  {{"action": "tap", "index": 2, "why": "one short line"}}
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


def declared(project: Path) -> list[dict]:
    """Flows this pass owns: a goal, and no scripted steps."""
    out = []
    for f in load_spec(project).get("flows") or []:
        if not isinstance(f, dict) or not str(f.get("id") or "").strip():
            continue
        if not str(f.get("goal") or "").strip():
            continue
        out.append(f)
    return out


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
        out.append({"label": label[:60], "x": (x1 + x2) // 2, "y": (y1 + y2) // 2,
                    "clickable": clickable})
    # A label that appears twice (a card and its text) is one decision.
    seen, uniq = set(), []
    for t in out:
        key = (t["label"].lower(), t["y"] // 40)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)
    return uniq[:14]


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


def _screenshot(serial: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = _adb(serial, ["exec-out", "screencap", "-p"], timeout=60, binary=True)
    data = r.stdout if isinstance(r.stdout, bytes) else (r.stdout or "").encode()
    if data.startswith(b"\x89PNG"):
        dest.write_bytes(data)
        return True
    return False


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


def _window_title(serial: str, pkg: str) -> str:
    r = _adb(serial, ["shell", "dumpsys", "window"], timeout=60)
    text = r.stdout or ""
    for line in text.splitlines():
        if "mCurrentFocus" in line and pkg in line:
            m = re.search(rf"{re.escape(pkg)}/[\w.]*\.?([\w]+)\b", line)
            return m.group(1) if m else "app"
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
               max_steps: int | None = None, via: str = "api") -> dict:
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
    _adb(serial, ["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"],
         timeout=45)
    settled, waited = _settle(serial, pkg)
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

    tried: dict[str, set[str]] = {}
    outcome = "budget"
    reason = ""
    crashes: list[str] = []
    dead: list[dict] = []
    _write_record(project, {"flow": fid, "step": 0, "verdict": "", "state": "running"})

    for step in range(1, cap + 1):
        if time.monotonic() > deadline:
            outcome, reason = "timeout", f"the {int(flow.get('seconds') or DEFAULT_SECONDS)}s cap was reached"
            break
        still = out_dir / f"step-{step:02d}.png"
        if not _screenshot(serial, still):
            outcome, reason = "error", "could not capture the screen"
            break
        stills.append(still)
        if step > 1:
            crash = ux_video._crash_line(serial, pkg)
            if crash:
                crashes.append(crash)
                outcome, reason = "crashed", crash
                break
        xml = ux_video._ui_xml(serial)
        targets = tappables(xml, pkg)
        screen = _window_title(serial, pkg)
        history = ("\n".join(f"  {s['n']}. {s['did']} → {s['then']}" for s in steps)
                   or "  (nothing yet)")
        dead_line = ""
        if tried.get(screen):
            dead_line = ("Already tried on this screen with no visible change (do NOT "
                         "repeat): " + ", ".join(sorted(tried[screen])) + "\n")
        prompt = DECIDE_PROMPT.format(
            app=app, flow=fid, goal=goal, step=step, max_steps=cap,
            history=history, screen=(f"Screen: {screen}" if screen else ""),
            targets=_label_list(targets), dead=dead_line)
        decision = _ask(cfg, prompt, still)
        action = str(decision.get("action") or "").strip().lower()
        _write_record(project, {"flow": fid, "step": step, "verdict": "",
                                "state": "deciding", "action": action})

        if action == "done":
            outcome, reason = "done", str(decision.get("why") or "")
            steps.append({"n": step, "did": "reached the goal", "then": reason,
                          "screen": screen, "action": "done"})
            break
        if action in ("stuck", "ask"):
            outcome, reason = "stuck", str(decision.get("why") or "")
            steps.append({"n": step, "did": "stopped", "then": reason,
                          "screen": screen, "action": "stuck"})
            break
        if action == "back":
            _adb(serial, ["shell", "input", "keyevent", "4"], timeout=30)
            time.sleep(1.0)
            steps.append({"n": step, "did": "pressed back", "then": "left the screen",
                          "screen": screen, "action": "back"})
            continue
        if action != "tap":
            outcome, reason = "error", f"the model answered {json.dumps(decision)[:120]}"
            break

        index = decision.get("index")
        if not isinstance(index, int) or not (0 <= index < len(targets)):
            outcome, reason = "error", f"tap index {index!r} is not a listed target"
            break
        target = targets[index]
        before = ux_video._screen_sig(serial)
        _adb(serial, ["shell", "input", "tap", str(target["x"]), str(target["y"])],
             timeout=30)
        time.sleep(1.4)
        after = ux_video._screen_sig(serial)
        is_dead = bool(before and after and before == after)
        entry = {"n": step, "did": f"tapped {target['label'] or '(unlabelled)'}",
                 "then": ("nothing changed — DEAD CONTROL" if is_dead else "the screen changed"),
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
    if sheet_path is not None:
        history = "\n".join(f"  {s['n']}. {s['did']} → {s['then']}" for s in steps)
        try:
            verdict = _ask(cfg, JUDGE_PROMPT.format(
                app=app, flow=fid, goal=goal, outcome=outcome, history=history),
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
        "reactions": verdict.get("reactions") or [],
        "premium": verdict.get("premium") or [],
        "defects": verdict.get("defects") or [],
        "model_error": verdict.get("error") or "",
        "stills": [str(p) for p in stills],
        "sheet": str(sheet_path) if sheet_path else "",
        "rubric": RUBRIC,
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
    if result["outcome"] == "stuck" and result.get("reason"):
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
        keep_emulator: bool = False, max_steps: int | None = None) -> dict:
    """Audit every goal-declared flow (or one), on a device this pass owns."""
    from . import emulator

    todo = declared(project)
    if only:
        todo = [f for f in todo if str(f.get("id")) == only]
        if not todo:
            raise SystemExit(
                f"ux-flow: no flow {only!r} with a `goal` in {FLOWS_REL} — a flow "
                "this pass can audit declares what should be true, not the taps "
                "(scripted flows belong to `devteam ux-video`)")
    if not todo:
        return {"planned": 0, "run": [], "skipped": [], "failed": []}

    serial, started = emulator.ensure_device(avd=avd, log=log)
    results, failed, skipped = [], [], []
    try:
        from . import ux_video
        build = ux_video.build_and_install(project, serial, log)
        for flow in todo:
            fid = str(flow["id"])
            try:
                res = audit_flow(project, flow, serial, log=log,
                                 max_steps=max_steps)
            except SystemExit as exc:
                failed.append({"id": fid, "reason": str(exc)[:300]})
                continue
            res["build"] = build
            results.append(res)
            store(project, res, log=log)
    finally:
        if started and not keep_emulator:
            stopped = emulator.stop(serial)
            if log:
                log(f"~ ux-flow: emulator {serial} "
                    f"{'stopped' if stopped else 'DID NOT STOP — stop it manually'}")
        elif started and log:
            log(f"~ ux-flow: leaving emulator {serial} running (--keep-emulator)")
    return {"planned": len(todo), "run": results, "skipped": skipped, "failed": failed}


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
