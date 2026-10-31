"""ADAPT — make an already-onboarded project match the CURRENT team.

The team changes; projects were onboarded under an older shape. "Run workflow
for <app>" must therefore start by adapting the project, not by assuming it is
current. This module is that step: deterministic, idempotent, non-destructive.

Rules it must never break:
  * It never invents DIRECTION. The long-term goal is the owner's sentence: when
    it is missing, adapt prints an OWNER ASK and keeps going — it does not draft
    a vision and it does not silently skip the question.
  * Structure only. Keys, references, mirror files and stale links are repaired;
    judgement (goals, priorities, scope) is not.
  * Idempotent and quiet: when the project is already current, it changes
    NOTHING — no rewrite, no diff noise, one line of output.
  * Reversible-ish: the previous profile is saved to `.pipeline/profile.bak`
    before the first rewrite of a run, and every change is recorded in
    state["adapted"] with what and why."""
from __future__ import annotations

import json
from pathlib import Path

from .core import (RunLog, load_json, now_iso, pipeline_dir, profile_of,
                   save_json, state_of)
from . import goals

SCHEMA = 2   # bump when a project needs new data/knowledge to work with the team


def ask(what: str, how: str = "") -> dict:
    """An owner ask: WHAT is missing, in plain words, and separately HOW to fix
    it. The two are split because the owner reads the page and the command is
    mechanical noise there — the page collapses it behind a click. Plain strings
    are still accepted (older state wrote them)."""
    return {"what": what.strip(), "how": (how or "").strip()}


def ask_text(a) -> tuple[str, str]:
    if isinstance(a, dict):
        return str(a.get("what") or ""), str(a.get("how") or "")
    return str(a or ""), ""

# Keys the current team reads. Missing non-critical ones get the default below;
# missing CRITICAL ones become an owner ask instead of an invented value.
DEFAULTS = {
    "main_branch": "main",
    "tag_prefix": "v",
    "ci_trigger": "bump",
    "src_root": None,
    "whatsnew_file": None,
    # How this project's Play release notes are produced:
    #   whatsnew_curated — the owner writes them by hand; the pipeline only READS
    #     the file (never rewrites it) and reports what will ship.
    #   whatsnew_max_chars — the store's allowed length for one release note
    #     (Play rejects longer ones); default 500.
    "whatsnew_curated": False,
    "whatsnew_max_chars": 500,
    "components_dir": None,
    "theme_dir": None,
    "reference_screen": None,
    "reference_picker": None,
    "motion_tokens": [],
    "conventions": [],
    "targets": [],
    "goals": {"vision": "", "milestones": []},
    "connections": {},
}
CRITICAL = ("repo_slug", "main_branch", "verify", "allowed_paths")


def _state_defaults(state: dict) -> list[str]:
    out = []
    for key, default in (("runs", []), ("shipped", []), ("queue", []),
                         ("goal_log", []), ("tree_changes", []),
                         ("alignment_log", []), ("release_candidates", []),
                         ("last_run", {})):
        if key not in state:
            state[key] = default
            out.append(f"state: added '{key}' (this team version records it)")
    return out


def needed(project: Path) -> list[str]:
    """Cheap read-only check: what would adapt DO? ('' list = already current).

    This is the FIXABLE set only — the structural drift `run()` repairs. An
    OWNER ASK is deliberately not an item here: `run()` never resolves one (it
    refuses to invent direction), so listing it would make the caller print the
    wrong remedy. `status` renders this list under "the workflow's first step
    fixes this: devteam adapt", and an ask is not fixed by adapt — it is fixed by
    the owner (`devteam vision`), which `status` already prints separately (the
    PLAN line) along with `adapt`/`doctor`'s OWNER ASK block. Name the remedy
    that actually works; an ask is a question, not a repair job."""
    try:
        profile, state = profile_of(project), state_of(project)
    except SystemExit:
        return ["not onboarded at all — run `devteam onboard <project>`"]
    out = []
    if int(profile.get("profile_schema") or 0) != SCHEMA:
        out.append(f"profile is schema v{profile.get('profile_schema') or 0}, "
                   f"team is v{SCHEMA}")
    out += [f"missing profile key '{k}'" for k in DEFAULTS if k not in profile]
    out += [f"missing profile key '{k}'" for k in CRITICAL if not profile.get(k)]
    out += [f"missing state key '{k}'" for k in
            ("queue", "goal_log", "tree_changes", "alignment_log")
            if k not in state]
    plan = goals.plan(profile)
    stale = [q.get("label") for q in state.get("queue", [])
             if str(q.get("advances") or "").strip() not in ("", "none")
             and not goals.find_node(profile, str(q.get("advances")))]
    if stale:
        out.append(f"{len(stale)} queued task(s) point at a goal that no longer "
                   f"exists: {', '.join(str(s) for s in stale[:3])}")
    deadc = [c for c in profile.get("conventions", []) if c and not Path(c).exists()]
    if deadc:
        fixable = sum(1 for c in deadc if _relocated(c))
        out.append(f"{len(deadc)} convention path(s) missing"
                   + (f" ({fixable} repairable automatically)" if fixable else ""))
    design_doc = project / "docs" / "design.md"
    if design_doc.exists() and not any(str(c).endswith("docs/design.md") for c in profile.get("conventions", [])):
        out.append("docs/design.md exists on disk but is not registered in conventions")
    return out


def _relocated(path: str) -> str | None:
    """A path the TEAM moved, repaired structurally — never by guessing.

    Two cases, in order, and nothing else:
      1. The path was inside the team repo (`.../dev-team/...`) and the team repo
         has moved: the SAME file at the same relative place is the answer.
      2. The path was a skills path: `skills/<name>/...` found under a known
         skills root. Exactly one hit, or we refuse — a name collision (two
         different skills called android-code) is an ambiguity only the owner may
         resolve, and re-pointing to the wrong one silently swaps the style law
         the developer is told to read."""
    p = Path(path)
    # 1. team repo relocation (structural: same tail, new root)
    for marker in ("/dev-team/", "/Documents/AI/dev-team/"):
        if marker in path:
            tail = path.split(marker, 1)[1]
            from .core import TEAM_ROOT
            cand = TEAM_ROOT / tail
            if cand.exists():
                return str(cand)
    # 2. skills root search (unique hit only)
    if "/skills/" in path:
        tail = path.split("/skills/", 1)[1]
        from .core import TEAM_ROOT
        roots = [TEAM_ROOT / "skills", Path.home() / ".agents" / "skills",
                 Path.home() / ".Codex" / "skills"]
        hits = [str(r / tail) for r in roots if (r / tail).exists()]
        if len(hits) == 1:
            return hits[0]
    return None


def run(project: Path, log: RunLog | None = None, apply: bool = True) -> dict:
    """Bring the project up to the team's current shape. Returns
    {changes, asks, already, schema}. Prints nothing itself — the caller decides."""
    profile, state = profile_of(project), state_of(project)
    pdir = pipeline_dir(project)
    changes: list[str] = []
    asks: list[str] = []

    def note(msg: str) -> None:
        changes.append(msg)
        if log:
            log("adapt: " + msg)

    # 1. schema stamp ---------------------------------------------------------
    have = int(profile.get("profile_schema") or 0)
    if have != SCHEMA:
        note(f"schema v{have or 'none'} → v{SCHEMA}")

    # 2. structural keys ------------------------------------------------------
    for key, default in DEFAULTS.items():
        if key not in profile:
            profile[key] = default
            note(f"added missing profile key '{key}'")
    if profile.get("components_dir") is None and profile.get("shared_components_dir"):
        profile["components_dir"] = profile["shared_components_dir"]
        note("components_dir ← shared_components_dir (alias the prompts read)")
    missing_critical = [k for k in CRITICAL if not profile.get(k)]
    for k in missing_critical:
        asks.append(ask(
            f"the team cannot gate or ship this project: its setup is missing "
            f"'{k}'",
            f"python3 cli.py onboard {project}   (or add '{k}' to "
            f".pipeline/profile.json by hand)"))

    # 3. state keys ----------------------------------------------------------
    changes += _state_defaults(state)

    # 4. direction: ask, never invent ----------------------------------------
    a = goals.direction_ask(profile, state, project)
    if a:
        asks.append(ask(a["what"], a["how"]))

    # 5. stale goal links in the queue ---------------------------------------
    # A queue entry pointing at a branch that no longer exists would be invisible
    # drift: the tree shows it as unlinked work and the goal gate rejects new
    # plans mentioning the ghost. Re-point it at what the project is actually
    # working on, and say so.
    act = goals.active_milestone(profile, state)
    fixed = 0
    for q in state.get("queue", []):
        mid = str(q.get("advances") or "").strip()
        if mid in ("", "none") or goals.find_node(profile, mid):
            continue
        if act:
            q["advances"] = act["id"]
            fixed += 1
        else:
            q["advances"] = "none"
            fixed += 1
            asks.append(ask(
                f"'{q.get('label')}' served a goal that no longer exists and there "
                f"is no goal to re-point it at — it is now visible as off-goal",
                f"python3 cli.py queue {project}   (review the queue)"))
    if fixed:
        note(f"re-pointed {fixed} queued task(s) whose goal no longer exists"
             + (f" → '{act['title']}'" if act else " → declared off-goal"))

    # 6. capability the current team needs for this project's own measures ---
    p = goals.plan(profile)
    if p:
        needs_metrics = any(
            isinstance(m, dict) and m.get("metric")
            for ms in p["milestones"] for m in (ms.get("measures") or []))
        if needs_metrics and not (pipeline_dir(project) / "research.json").exists():
            asks.append(ask(
                "one of the goals is measured by a number (weekly active users) "
                "but nothing collects that number yet — until then it can only be "
                "guessed at",
                "declare the analytics connection in "
                ".pipeline/profile.json['connections']"))

    # 7. conventions that point at files that no longer exist ---------------
    conventions = list(profile.get("conventions", []))
    repaired, dead = [], []
    for c in conventions:
        if not c or Path(c).exists():
            continue
        fixed = _relocated(c)
        if fixed:
            repaired.append((c, fixed))
            conventions[conventions.index(c)] = fixed
        else:
            dead.append(c)
    if repaired:
        profile["conventions"] = conventions
        for old, new in repaired:
            note(f"convention path moved → {new} (was {old})")
    if dead:
        asks.append(ask(
            f"the coding-convention file the team tells its developer to read is "
            f"gone ({Path(dead[0]).name}), so runs proceed without the style law",
            "fix .pipeline/profile.json['conventions'] — the team auto-repairs "
            "paths it can trace, and leaves the ambiguous ones to you"))

    # 7b. design system convention (docs/design.md) ------------------------
    design_doc = project / "docs" / "design.md"
    if design_doc.exists():
        resolved_str = str(design_doc.resolve())
        has_design = any(str(c).endswith("docs/design.md") or c == resolved_str for c in conventions)
        if not has_design:
            conventions.append(resolved_str)
            profile["conventions"] = conventions
            note("registered docs/design.md in conventions (enforces design system style law)")
    else:
        asks.append(ask(
            "no docs/design.md found — the developer agent builds screens without an enforced design system",
            f"run `devteam design {project.name}` to research category standards and synthesize docs/design.md"))

    # 8. write (only if something changed) + snapshots + mirrors -------------
    # A standing owner ask is NOT "not current": the shape is fine, the question
    # is just unanswered. Re-running must change nothing and stay quiet, but the
    # asks stay on screen (they are the reason the project is still goal-less).
    already = not changes and (state.get("adapted") or {}).get("asks") == asks
    if apply and not already:
        bak = pdir / "profile.bak"
        if not bak.exists():
            bak.write_text(json.dumps(profile_of(project), indent=2) + "\n")
        profile["profile_schema"] = SCHEMA
        profile["adapted_at"] = now_iso()
        save_json(pdir / "profile.json", profile)
        state["adapted"] = {"at": now_iso(), "schema": SCHEMA,
                            "changes": changes, "asks": asks}
        save_json(pdir / "state.json", state)
        try:
            from . import tree as tree_mod
            tree_mod.write(project)
        except Exception:
            pass
        try:
            from . import scribe
            scribe.update(project)
        except Exception:
            pass
    return {"changes": changes, "asks": asks, "already": already, "schema": SCHEMA}


def report(project: Path, res: dict, project_name: str | None = None,
           dry: bool = False) -> str:
    """Plain, owner-readable. Silence when there is nothing to say would be
    wrong — 'already current' is information too (it tells the owner the project
    is not silently drifting)."""
    name = project_name or project.name
    if res["already"]:
        lines = [f"ADAPT {name}: up to date with the team (schema v{res['schema']}) "
                 "— nothing to change"]
        for a in res["asks"]:
            what, how = ask_text(a)
            lines.append(f"  ? OWNER ASK (standing): {what}"
                         + (f"\n      how: {how}" if how else ""))
        return "\n".join(lines)
    head = (f"ADAPT {name} — WOULD do this (dry run, nothing written):"
            if dry else f"ADAPT {name} — brought it up to date with the team:")
    lines = [head]
    lines += [f"  + {c}" for c in res["changes"]]
    for a in res["asks"]:
        what, how = ask_text(a)
        lines.append(f"  ? OWNER ASK: {what}")
        if how:
            lines.append(f"      how: {how}")
    if res["changes"]:
        lines.append(f"  (previous profile kept at .pipeline/profile.bak; page "
                     f"regenerated)")
    return "\n".join(lines)
