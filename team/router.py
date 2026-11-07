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

# Ordered: the first kind whose terms match wins. Revamp is the broadest
# ground-up rebuild; brand/store/release are narrower; bugfix is specific.
KINDS: list[tuple[str, str, tuple[str, ...], str]] = [
    ("revamp", "feature", ("revamp", "rebuild from scratch", "redesign from scratch",
                           "ground up", "ground-up", "start from scratch", "rebuild",
                           "reimagining", "reimagine", "complete redesign", "delete the codebase",
                           "recreate the ui", "recreate ui", "scrap the existing design",
                           "scrap existing design", "scrap design", "delete the ui",
                           "delete ui", "new design from stitch", "stitch design",
                           "rebuild the ui", "rebuild ui", "redesign the ui", "redesign ui",
                           "ui revamp", "design revamp", "ui overhaul"),
     "a ground-up app rebuild or UI revamp: PRD/design re-imagination, design tokens, UI purge/rebuild, native build, fresh Play Store marketing assets"),
    ("brand", "feature", ("icon", "logo", "app icon", "launcher icon", "brand mark"),
     "a designed artefact: Stitch first, then the app, then the listing"),
    ("store", "feature", ("screenshot", "store listing", "feature graphic", "play store page",
                          "storefront", "listing copy", "store asset", "store assets",
                          "play store asset", "store image", "listing asset"),
     "the store page: render, compose, gate, then publish — no app code"),
    ("release", "feature", ("release", "ship it", "push to play", "cut a build", "publish the app"),
     "delivery of work already on the branch"),
    ("test", "feature", ("test", "testing", "ui test", "ui testing", "unit test", "coverage",
                         "instrumented test", "device test", "e2e test", "verification",
                         "verify"),
     "a test or verification task: writing or exercising unit/UI/device tests to prove behaviour"),
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
    "revamp": [
        ("competition", "devteam competition {p} --limit 5",
         "deep market and rival recon: >= 5 direct competitors, pricing models, weaknesses"),
        ("revamp-prep", "devteam revamp {p}",
         "re-imagine core loop via docs/PRD.md, synthesize docs/design.md tokens, invalidate stale assets"),
        ("worktree", "devteam worktree-start {p} --task-id revamp-core-loop",
         "the rebuild happens in an isolated branch dt/revamp-core-loop, auto-purging legacy code"),
        ("purge-codebase", "devteam revamp {p} --purge-codebase --worktree {wt}",
         "mechanically purge and archive all legacy application code and tests so no zombie architecture survives"),
        ("develop", "devteam developer-prompt {p} .pipeline/tasks/revamp-core-loop.json",
         "build native Jetpack Compose screens adhering 1:1 to docs/design.md tokens and Stitch designs"),
        ("verify", "devteam verify {p} --worktree {wt} --task-spec .pipeline/tasks/revamp-core-loop.json",
         "compile, unit tests, connected emulator tests, and design conformance gate"),
        ("release", "devteam release {p} --worktree {wt} --task-spec .pipeline/tasks/revamp-core-loop.json",
         "build signed AAB bundle, push to GitHub Actions release CI, upload to Play Internal Testing"),
        ("brand", "devteam brand {p} --design --publish --yes",
         "create new Stitch MCP app icon, adopt to mipmaps, and publish to Play Store"),
        ("storefront", "devteam storefront-build {p} --poster --force && devteam store-publish {p} --yes",
         "generate fresh marketing screenshot posters with Gemini heal and publish all listing assets"),
        ("record", "devteam record {p} --result shipped --task-spec .pipeline/tasks/revamp-core-loop.json "
                   "--version <v> --commit <sha> --note {q}",
         "the ledger, handover copy, and release-candidate verdict"),
    ],
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
_CHAIN["test"] = [
    ("plan", "devteam plan {p} --focus {q}", "the planner reconciles the queue; the task stays pinned"),
    ("pick", "devteam pick {p}", "the pinned head becomes the task spec"),
    ("worktree", "devteam worktree-start {p} --task-id {task}", "work happens on a branch"),
    ("develop", "devteam developer-prompt {p} .pipeline/tasks/{task}.json",
     "the developer agent writes and exercises tests in the worktree"),
    ("verify", "devteam verify {p} --worktree {wt} --task-spec .pipeline/tasks/{task}.json",
     "compile, tests, device, provenance"),
    ("release", "devteam release {p} --worktree {wt} --task-spec .pipeline/tasks/{task}.json",
     "version bump, CI, internal testing"),
    ("record", "devteam record {p} --result shipped --task-spec .pipeline/tasks/{task}.json "
               "--version <v> --commit <sha> --note {q}",
     "the ledger and the handover"),
]
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
    surfaces = _surfaces(project)
    for surface in surfaces:
        label = _norm(surface.get("label") or "")
        overlap = len(words & set(label.split()))
        if label and label in _norm(prompt):
            overlap += 2
        if overlap > score:
            best, score = surface, overlap
    if best is not None and score > 0:
        return best
    # Secondary match: scan surface source files for domain keywords in prompt
    for surface in surfaces:
        content_hits = 0
        targets = surface.get("targets") or surface.get("paths") or []
        for rel in targets:
            fpath = project / rel
            if fpath.is_file():
                try:
                    text = fpath.read_text(errors="ignore").lower()
                    for w in words:
                        if len(w) >= 4 and w in text:
                            content_hits += min(text.count(w), 10)
                except Exception:
                    pass
        if content_hits > score:
            best, score = surface, content_hits
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


def requires_storefront_assets(prompt: str, kind: str) -> bool:
    """Determine whether an owner prompt alters screens, design, or UI enough
    to require regenerating Play Store screenshot posters and marketing assets."""
    if kind in ("revamp", "store"):
        return True
    low = " " + (prompt or "").lower() + " "
    terms = (
        "recreate the ui", "recreate ui", "scrap design", "scrap the existing design",
        "scrap existing design", "new design", "stitch design", "redesign",
        "redesign the ui", "redesign ui", "rebuild the ui", "rebuild ui",
        "ui revamp", "design revamp", "ui overhaul", "theme change",
        "change the theme", "dark mode", "light mode", "warm mode", "palette",
        "brand refresh", "new look", "visual overhaul"
    )
    return any(t in low for t in terms)


def _targets(project: Path, kind: str, prompt: str = "") -> list[str]:
    if kind == "brand":
        return _brand_targets(project)
    try:
        from .core import profile_of

        profile = profile_of(project)
    except Exception:
        return []
    allowed = [str(p) for p in (profile.get("allowed_paths") or []) if p]
    base = list(allowed) if allowed else ([profile.get("src_root")] if profile.get("src_root") else [])
    if requires_storefront_assets(prompt, kind):
        extra = ["docs/storefront*.json", "docs/storefront-direction.json",
                 "distribution/listing/**/images/**"]
        for e in extra:
            if e not in base:
                base.append(e)
    if kind == "test":
        try:
            from . import code_tests
            unit_dirs, ui_dirs = code_tests.suggest_test_dirs(project)
            for d in unit_dirs + ui_dirs:
                glob_pat = f"{d}/**"
                if glob_pat not in base:
                    base.append(glob_pat)
        except Exception:
            pass
    return base


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
    needs_store = requires_storefront_assets(prompt, kind)
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
            "targets": _targets(project, kind, prompt),
            "pinned": True,
            "priority": "owner",
            "reason": f"Owner request: \"{prompt}\" — {align.get('why')}",
            "requires_storefront": needs_store,
        },
        "steps": steps,
        "blocked": align["verdict"] in ("BLOCKED", "OWNER"),
    }


def _quote(text: str) -> str:
    return '"' + " ".join((text or "").split()).replace('"', "'") + '"'


def _label(project: Path, prompt: str, kind: str) -> str:
    from .core import today

    stop_words = {"the", "and", "for", "with", "this", "that", "from", "app", "you"}
    words = [w for w in _norm(prompt).split() if len(w) >= 3 and w not in stop_words][:4]
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
        "requires_storefront": result["task"].get("requires_storefront", False),
        "advances": (result.get("alignment") or {}).get("milestone") or "",
    }
    block = qmod.add_task(state, entry, source="prompt-handler", position="head")
    if result["kind"] == "brand":
        from . import brand_change

        if not brand_change.state(project):
            brand_change.seed(project, prompt, result["task"]["label"])

    # If the change alters the UI or is a revamp, ensure storefront regeneration is also queued
    if result["task"].get("requires_storefront") and result["kind"] != "store":
        from .core import today
        store_entry = {
            "type": "feature",
            "label": f"storefront-regenerate-assets-{today()}",
            "target": "distribution/listing/en-US/images/phoneScreenshots, docs/storefront*.json, docs/storefront-direction.json",
            "risk": "safe",
            "reason": f"Regenerate Play Store screenshots and marketing posters to reflect new UI/design for: {prompt}",
            "owner_request": prompt,
            "pinned": True,
            "priority": "owner",
            "kind": "store",
            "advances": (result.get("alignment") or {}).get("milestone") or "",
        }
        # In state["queue"], entry was placed at head (index 0). Place store_entry at index 1
        queue = state.get("queue") or []
        if queue and queue[0].get("label") == entry.get("label"):
            queue.insert(1, store_entry)
        else:
            queue.append(store_entry)
        state["queue"] = queue
        block += f"\n  linked storefront task: {store_entry['label']} queued immediately after {entry['label']}"

    save_state(project, state)
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
        if task.get("requires_storefront"):
            lines.append("  storefront: REQUIRED — Play Store screenshots and marketing posters must be regenerated")
    lines.append("")
    lines.append("EXECUTION PLAN (in order — each step's gate is the next step's precondition):")
    for step in result.get("steps") or []:
        lines.append(f"  {step['n']}. {step['step']:<10} {step['cmd']}")
        lines.append(f"       why: {step['why']}")
    return "\n".join(lines)
