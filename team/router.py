"""The PROMPT HANDLER — one owner sentence in, one ordered execution plan out.

Not the planner. The PLANNER (`devteam plan`, team/research.py) maintains the
queue and judges the goal; it re-reads reality every cycle. The HANDLER answers
a narrower, different question: the owner just said something — which tools does
that imply, in what order, does it belong in the current plan at all, and which
task does it become?

Why it exists
-------------
Every owner sentence used to start a fresh agent's interpretation: which of the
~40 commands to run, whether the planner had to see it, whether it was a plan
change or a defect. Two agents answered the same sentence differently. The
ordering is not a matter of taste — a plan change that skips the planner loses
the queue's verdict, a release that skips verify ships unproven work, and a
brand change that skips Stitch ships an icon nobody designed — so the order is
code here, once.

Contract:
  * the handler never invents work: an unknown prompt is a BLOCKED ask;
  * alignment is judged against the plan that exists (goal + queue), and a prompt
    that would change DIRECTION is an owner decision, never an agent rewrite;
  * an admitted task is `pinned`: the planner can reorder everything else, but a
    live owner request stays at the head until it ships or the owner drops it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

STATE_REL = ".pipeline/route.json"

# Ordered: the first kind whose terms match wins. Brand/store/release are
# narrower than "feature", bugfix is more specific than a visual tweak.
KINDS: list[tuple[str, str, tuple[str, ...], str]] = [
    ("brand", "feature", ("icon", "logo", "app icon", "launcher icon", "brand mark"),
     "a designed artefact: Stitch first, then the app, then the listing"),
    ("store", "feature", ("screenshot", "store listing", "feature graphic", "play store page",
                          "storefront", "listing copy"),
     "the store page: render, compose, gate, then publish — no app code"),
    ("release", "feature", ("release", "ship it", "push to play", "cut a build", "publish the app"),
     "delivery of work already on the branch"),
    ("bugfix", "bugfix", ("crash", "broken", "doesn't work", "does not work", "not working",
                          "fails", "failure", "stuck", "bug", "error", "wrong", "won't"),
     "a defect: reproduce, fix, prove it"),
    ("visual", "feature", ("button", "looks", "ugly", "colour", "color", "font", "spacing",
                           "padding", "too big", "too small", "aligned", "alignment", "layout",
                           "screen", " ui", "ux", "contrast", "overlap", "cluttered", "plain",
                           "don't like", "do not like", "hate"),
     "a UI change on a screen the app already has"),
    ("feature", "feature", ("add", "support", "new", "allow", "enable", "make it", "should"),
     "new capability"),
    ("direction", "feature", ("focus on", "strategy", "goal", "roadmap", "prioritise",
                              "prioritize", "direction", "instead of", "we should"),
     "a direction change: the owner's call, and the planner's to encode"),
]

_CHAIN: dict[str, list[tuple[str, str, str]]] = {
    # (step label, command template, what it proves / why it is there)
    "brand": [
        ("plan", "devteam plan {p} --focus {q}",
         "the planner reconciles the queue around this request; the pinned task survives"),
        ("worktree", "devteam worktree-start {p} --task-id {task}",
         "the icon change happens on a branch, never in the checkout"),
        ("design", "devteam brand {p} --design --worktree {wt}",
         "Stitch is called from code; the artboard, prompt and provenance are written"),
        ("verify", "devteam verify {p} --worktree {wt} --task-spec .pipeline/tasks/{task}.json",
         "compile, tests, device, and the icon-provenance gate"),
        ("release", "devteam release {p} --worktree {wt} --task-spec .pipeline/tasks/{task}.json",
         "version bump, CI, Play internal testing"),
        ("record", "devteam record {p} --result shipped --task-spec .pipeline/tasks/{task}.json "
                   "--version <v> --commit <sha> --note {q}",
         "the ledger, the handover and the release-candidate call"),
        ("publish", "devteam brand {p} --publish --yes",
         "the listing edit is committed to Play and read back"),
    ],
    "visual": [
        ("plan", "devteam plan {p} --focus {q}", "the planner reconciles the queue; the task stays pinned"),
        ("pick", "devteam pick {p}", "the pinned head becomes the task spec"),
        ("worktree", "devteam worktree-start {p} --task-id {task}", "work happens on a branch"),
        ("develop", "devteam developer-prompt {p} .pipeline/tasks/{task}.json",
         "the developer agent edits only the allow-list, in the worktree"),
        ("verify", "devteam verify {p} --worktree {wt} --task-spec .pipeline/tasks/{task}.json",
         "compile, tests, device, provenance"),
        ("release", "devteam release {p} --worktree {wt} --task-spec .pipeline/tasks/{task}.json",
         "version bump, CI, internal testing"),
        ("record", "devteam record {p} --result shipped --task-spec .pipeline/tasks/{task}.json "
                   "--version <v> --commit <sha> --note {q}",
         "the ledger and the handover"),
    ],
    "bugfix": [],       # filled from visual below
    "feature": [],
    "store": [
        ("direction", "devteam storefront-direction {p}",
         "what the listing says is decided before anything is drawn"),
        ("build", "devteam storefront-build {p}", "render, compose, heal, gate — one deterministic order"),
        ("publish", "devteam store-publish {p} --yes", "upload, commit, and read the live edit back"),
    ],
    "release": [
        ("status", "devteam status {p}", "what is actually on main and on Play"),
        ("record", "devteam record {p} --result shipped --task-spec .pipeline/tasks/{task}.json "
                   "--version <v> --commit <sha> --note {q}", "close the ledger for the delivery"),
    ],
    "direction": [
        ("ask", "devteam goal {p} set --vision {q}   (owner only)",
         "a direction change is the owner's sentence to make, not an agent's inference"),
        ("plan", "devteam plan {p} --focus {q}", "the planner re-derives the queue from the new goal"),
    ],
}
_CHAIN["bugfix"] = _CHAIN["visual"]
_CHAIN["feature"] = _CHAIN["visual"]


def _now() -> str:
    from .core import now_iso

    return now_iso()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def kind_of(prompt: str) -> tuple[str, str]:
    """(kind, evidence). Evidence is the literal term that decided it, so the
    plan can be argued with instead of believed."""
    low = " " + (prompt or "").lower() + " "
    for kind, _type, terms, _why in KINDS:
        for term in terms:
            if term in low:
                return kind, term
    return "unknown", ""


def _type_of(kind: str) -> str:
    for k, t, _terms, _why in KINDS:
        if k == kind:
            return t
    return "feature"


def _why_of(kind: str) -> str:
    for k, _t, _terms, why in KINDS:
        if k == kind:
            return why
    return ""


def _surfaces(project: Path) -> list[dict[str, Any]]:
    try:
        from .scan import scan_surfaces

        return scan_surfaces(project)
    except Exception:
        return []


def _match_surface(project: Path, prompt: str) -> dict[str, Any] | None:
    """The screen/component the owner named, by token overlap with real surfaces."""
    words = {w for w in _norm(prompt).split() if len(w) >= 3}
    best, score = None, 0
    for surface in _surfaces(project):
        label = _norm(surface.get("label") or "")
        overlap = len(words & set(label.split()))
        if label and label in _norm(prompt):
            overlap += 2
        if overlap > score:
            best, score = surface, overlap
    return best


def _brand_targets(project: Path) -> list[str]:
    from . import brand_change

    listing = brand_change._listing_images(project) / "icon.png"
    out = [str(listing.relative_to(project))]
    res = brand_change._res_dir(project)
    if res:
        rel = res.relative_to(project)
        out += [str(rel / "mipmap-anydpi-v26/ic_launcher.xml"),
                str(rel / "mipmap-*/ic_launcher.png"),
                str(rel / "mipmap-*/ic_launcher_round.png"),
                str(rel / "mipmap-*/ic_launcher_foreground.png"),
                str(rel / "values/colors.xml")]
        # the provenance files the icon change must carry (verify refuses without them)
        out += ["docs/storefront-icon.html", "docs/storefront-icon-prompt.txt",
                "docs/launcher-icon.json", "docs/storefront-assets.json"]
    return out


def _targets(project: Path, kind: str) -> list[str]:
    if kind == "brand":
        return _brand_targets(project)
    try:
        from .core import profile_of

        profile = profile_of(project)
    except Exception:
        return []
    allowed = [str(p) for p in (profile.get("allowed_paths") or []) if p]
    return allowed or ([profile.get("src_root")] if profile.get("src_root") else [])


def alignment(project: Path, prompt: str, kind: str) -> dict[str, Any]:
    """Does this belong in the plan that exists? Deterministic, quoted reasons."""
    from .core import profile_of, state_of
    from . import goals

    profile = profile_of(project)
    state = state_of(project)
    if kind == "unknown":
        return {"verdict": "BLOCKED",
                "why": "the sentence matches no known kind of work (brand, store, release, "
                       "bugfix, visual, feature, direction) — an agent must not guess",
                "question": "is this a UI change, a defect, a feature, a store change, or a "
                            "direction change?"}
    vision = goals.vision_required(profile)
    if vision and not goals.vision(profile):
        return {"verdict": "BLOCKED",
                "why": "the project has no long-term goal, so nothing can be aligned to one",
                "question": "what is the one-sentence goal for this app?"}
    active = goals.active_milestone(profile, state) or {}
    if kind == "direction":
        return {"verdict": "OWNER",
                "why": "a direction change is the owner's call; the planner encodes it after",
                "milestone": active.get("id"), "question": prompt}
    surface = _match_surface(project, prompt)
    queue = state.get("queue") or []
    queued = next((q for q in queue
                   if surface and _norm(q.get("label") or "") == _norm(surface.get("label") or "")),
                  None)
    advances = str((queued or {}).get("advances") or active.get("id") or "")
    if kind == "brand":
        from . import brand_change

        open_brand = brand_change.state(project)
        if open_brand and open_brand.get("state") not in (None, "published"):
            return {"verdict": "BLOCKED",
                    "why": f"a brand change is already open at '{open_brand.get('state')}'",
                    "next": f"devteam brand {project.name} --status"}
    return {
        "verdict": "OK" if surface or kind in ("store", "release") else "PROVISIONAL",
        "why": (f"names the known surface '{surface.get('label')}'" if surface else
                "no surface is named; the task's allow-list lets the developer find it"),
        "surface": (surface or {}).get("label"),
        "milestone": advances,
    }


def plan(project: Path, prompt: str) -> dict[str, Any]:
    kind, evidence = kind_of(prompt)
    align = alignment(project, prompt, kind)
    label = _label(project, prompt, kind)
    task_id = _task_id(label)
    steps = [{"n": i + 1, "step": s, "cmd": c.format(
                  p=project.name, q=_quote(prompt), task=task_id)
              if "{wt}" not in c else c.format(p=project.name, q=_quote(prompt), task=task_id,
                                               wt="<WORKTREE_PATH>"),
              "why": w}
             for i, (s, c, w) in enumerate(_CHAIN.get(kind, []))]
    for step in steps:
        if step["cmd"].endswith("--worktree <WORKTREE_PATH>"):
            step["note"] = "the exact path is printed by worktree-start"
    return {
        "version": 1,
        "at": _now(),
        "project": project.name,
        "prompt": prompt,
        "kind": kind,
        "evidence": evidence,
        "meaning": _why_of(kind),
        "alignment": align,
        "task": {
            "label": label,
            "id": task_id,
            "type": _type_of(kind),
            "targets": _targets(project, kind),
            "pinned": True,
            "priority": "owner",
            "reason": f"Owner request: \"{prompt}\" — {align.get('why')}",
        },
        "steps": steps,
        "blocked": align["verdict"] in ("BLOCKED", "OWNER"),
    }


def _quote(text: str) -> str:
    return '"' + " ".join((text or "").split()).replace('"', "'") + '"'


def _label(project: Path, prompt: str, kind: str) -> str:
    from .core import today

    words = [w for w in _norm(prompt).split() if len(w) >= 3][:4]
    slug = "-".join(words) or kind
    return f"{kind}-{slug}-{today()}"[:60].strip("-")


def _task_id(label: str) -> str:
    # pick derives the real id (label slug + run seq); the plan names the shape
    # and pick prints the exact spec, so a copy-paste cannot invent one.
    return "TASK_ID"


def apply(project: Path, prompt: str) -> dict[str, Any]:
    """Admit the task (pinned head) and record the plan. Refuses a blocked one."""
    result = plan(project, prompt)
    from .core import save_json, save_state, state_of
    from . import queue as qmod

    path = project / STATE_REL
    save_json(path, result)
    if result["blocked"]:
        return result
    state = state_of(project)
    entry = {
        "type": result["task"]["type"],
        "label": result["task"]["label"],
        "target": ", ".join(result["task"]["targets"]),
        "risk": "safe",
        "reason": result["task"]["reason"],
        "owner_request": prompt,
        "pinned": True,
        "priority": "owner",
        "kind": result["kind"],
        "advances": (result.get("alignment") or {}).get("milestone") or "",
    }
    block = qmod.add_task(state, entry, source="prompt-handler", position="head")
    save_state(project, state)
    if result["kind"] == "brand":
        from . import brand_change

        if not brand_change.state(project):
            brand_change.seed(project, prompt, result["task"]["label"])
    result["queue_impact"] = block
    result["admitted"] = True
    return result


def format_plan(result: dict[str, Any]) -> str:
    align = result.get("alignment") or {}
    lines = [
        "PROMPT HANDLER",
        "=" * 66,
        f"  prompt:    {result.get('prompt')}",
        f"  kind:      {result.get('kind')}  (decided by {result.get('evidence')!r})",
        f"  meaning:   {result.get('meaning')}",
        f"  alignment: {align.get('verdict')} — {align.get('why')}",
    ]
    if align.get("surface"):
        lines.append(f"             surface: {align['surface']}")
    if align.get("question"):
        lines.append(f"  question:  {align['question']}")
    if result.get("blocked"):
        lines.append("  NOTHING WAS QUEUED (the handler refuses to guess)")
    else:
        task = result.get("task") or {}
        lines.append(f"  task:      {task.get('label')}  [{task.get('type')}, pinned head]")
    lines.append("")
    lines.append("EXECUTION PLAN (in order — each step's gate is the next step's precondition):")
    for step in result.get("steps") or []:
        lines.append(f"  {step['n']}. {step['step']:<10} {step['cmd']}")
        lines.append(f"       why: {step['why']}")
    return "\n".join(lines)
