"""TOOLBOX — the whole set of verbs, in one pass.

Why this exists (2026-11-09, owner directive)
---------------------------------------------
The team's public surface is ~60 commands, and the only way to learn it was to
read `cli.py --help` (grouped by nothing) or to be told a sequence by a file that
had to be kept in sync with the code. Both drift: `capabilities.py` exists
because a whole command landed and no project ever heard of it.

So the inventory is DATA, and it is complete by construction: `doctor` compares
it against the parser (`sub.add_parser("…")` in cli.py) and BLOCKS on any verb
present in one and missing from the other. Adding a command without describing it
is therefore impossible, and deleting one without its row is too.

The agent's rule (see skills/dev-team/SKILL.md, "FIRST ACTION"): read this list
once — `devteam tools` — then decide the sequence itself. The team does not route;
it reports what it can do, what each verb writes, and what each verb refuses.
`refuses` is the important column: it is why a sequence cannot go wrong quietly.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

CLI_PY = Path(__file__).resolve().parent.parent / "cli.py"

# phase → the order an agent usually needs them in. Not a pipeline: a set.
PHASES = [
    ("orient", "where everything stands (read-only unless noted)"),
    ("plan", "the plan: the goal tree, the queue, market signal"),
    ("build", "one task: spec, worktree, code, UX"),
    ("deliver", "gates and delivery to internal testing"),
    ("assets", "the Play listing: icon, frames, screenshots, copy"),
    ("ops", "the machine around the project (CI, schedules, console, reports)"),
    ("internal", "host plumbing — an agent does not call these directly"),
]

# (verb, phase, what it is for, what it writes, what makes it refuse, agent-facing)
_RAW: list[tuple[str, str, str, str, str, bool]] = [
    # ---- orient ------------------------------------------------------------ #
    ("status", "orient", "delivery ref, plan, queue, store, last run — one block",
     "nothing", "nothing (read-only)", True),
    ("tools", "orient", "THIS inventory: every verb, what it writes, what it refuses",
     "nothing", "nothing (read-only)", True),
    ("takeover", "orient", "the same facts joined, ending in ONE next command",
     "nothing", "nothing (read-only)", True),
    ("scan", "orient", "surface inventory of the delivery ref",
     ".pipeline/scan worktree", "nothing", True),
    ("tree", "orient", "the plan as a tree (vision → goals → milestones → queue)",
     "nothing; --open writes .pipeline/goals.html", "no vision", True),
    ("doctor", "orient", "every gate the team can check, as READY/BLOCKED",
     ".pipeline/runs/ log", "never — it reports", True),
    ("capabilities", "orient", "each team capability, checked against this project",
     ".pipeline/targets-draft.json (owner approves)", "nothing", True),
    ("adapt", "orient", "bring the project up to the current team (shape, tree, CI)",
     "profile/state shape, .pipeline/*", "a project it cannot read", True),
    ("coverage", "orient", "unit + UI coverage against the 100% floor",
     "nothing", "nothing", True),
    ("asks", "orient", "review every open ask/flag and say who can do it (rule table)",
     "nothing; --strict exits 1 while an ask is agent work",
     "nothing — it reports", True),
    ("ux-plan", "orient", "which screens need a UX audit now",
     "nothing", "no declared targets", True),
    ("storefront-status", "orient", "what the last storefront run actually did",
     "nothing", "nothing", True),
    # ---- plan -------------------------------------------------------------- #
    ("plan", "plan", "THE PLANNER: reconcile the queue against signal and the tree",
     "state.queue, goal log, .pipeline/research.json", "no vision; flat tree", True),
    ("replan", "plan", "an OUT-OF-BAND change (the owner edited or pushed by hand) or a "
                       "team change to the plan FORMAT: detect → adopt (never revert) → "
                       "adapt → re-plan; `--all` sweeps every app",
     "state.adoptions, state.plan_base_sha, queue, profile shape",
     "a broken app is reported and the sweep continues; a planner refusal is reported", True),
    ("research", "plan", "the planner's engine, raw: collectors + one synthesis",
     ".pipeline/research.json", "no synthesis path (still collects)", True),
    ("market", "plan", "Tavily market/competitor research for a thesis or feature",
     ".pipeline/market/*", "no Tavily key", True),
    ("competition", "plan", "deep rival recon: ≥5 competitors, pricing, weaknesses",
     ".pipeline/competitors/*", "no Tavily key", True),
    ("refill", "plan", "refill an empty queue from the milestone horizon",
     "state.queue", "no active milestone", True),
    ("queue", "plan", "queue add/drop/focus, one entry at a time",
     "state.queue", "no goal tree (needs --advances)", True),
    ("vision", "plan", "set THE one-sentence goal (owner's words)",
     "profile.goals.vision", "never — the owner is the authority", True),
    ("goal", "plan", "change the goal tree below the vision, with a logged why",
     "profile.goals, goal log", "removing the vision", True),
    ("align", "plan", "ask whether a request serves a goal; apply the answer",
     "state goal log (on --apply)", "nothing", True),
    ("admit", "plan", "THE GATE: judge an owner request against the plan, pin it "
                      "at the head — or flag it and queue nothing",
     "state.queue only when the verdict is 'aligned'; admit log otherwise",
     "unknown kind, unknown/done/parked milestone, unmet earlier goal, no --why, "
     "no vision, no --verdict", True),
    ("closure", "plan", "is a 'done' real? A branch whose tasks are all delivered but "
                        "whose measures have no evidence is a GAP — the next task, not "
                        "the next feature",
     "nothing; --require exits 1 instead of reporting",
     "nothing — it reports", True),
    ("loop", "plan", "N cycles of plan review + refill, printing each next task "
                     "(inspection — the agent executes)",
     ".pipeline/runs/ log", "nothing", True),
    # ---- build ------------------------------------------------------------- #
    ("pick", "build", "the queue head → task spec + worktree command",
     ".pipeline/tasks/<id>.json", "empty queue; queue not planned since admission", True),
    ("worktree-start", "build", "cut dt/<task-id> from origin/main, lock it, and "
                                "print the tree + task spec + the develop skill",
     ".pipeline/worktrees/<id>", "dirty or unknown task id", True),
    ("verify", "build", "the gate: team-generic, localization, brand, test gate, "
                        "coverage, compile, tests, device",
     ".pipeline/runs/ log, verify id", "any gate red", True),
    ("worktree-prune", "build", "remove trees whose work is safe elsewhere",
     "worktree registry", "uncommitted work (parks it instead)", True),
    ("stitch-export", "build", "download every Stitch screen (HTML + screenshot) into docs/stitch — the design spec",
     "docs/stitch/**, .pipeline/stitch_manifest.json", "no stitch_project_id; Stitch unavailable", True),
    ("design-consult", "build", "ask the designer for ONE task — MANDATORY before UI: "
                                "no UI code without a design, so find it (reuse/tweak a "
                                "project screen) or get it created (consult → record → "
                                "export); the answer is cached against the current "
                                "Stitch/manifest fingerprint",
     ".pipeline/design_consult.json", "unknown task; bad answer JSON; a UI diff with no "
                                      "recorded answer (verify refuses)", True),
    ("revamp", "build", "PRD re-imagination and legacy purge (destructive; Stitch holds the visual spec)",
     "docs/PRD.md, code (with --purge-codebase)",
     "a purge outside a worktree; no PRD source", True),
    ("ux-audit", "build", "one neutral first-use reaction over the real screens",
     ".pipeline/ux_audit.json", "no emulator", True),
    ("ux-video", "build", "record declared journeys on a device",
     ".pipeline/ux_video/*", "no declared flows; no emulator", True),
    ("ux-flow", "build", "THE release audit: drive goal-declared journeys with a "
                         "vision model; every walk must reach its goal",
     ".pipeline/ux_flows/*", "a journey not walked on this diff; a walk that did not "
                             "reach its goal; no emulator", True),
    ("flow-peer", "build", "drive the OTHER reader through the real API so one app "
                           "can be walked through accept → number → handover → return",
     ".pipeline/runs/flow-peer-*.log", "no peer credential; no --with-reader; no "
                                       "available app listing for --create-offer", True),
    # ---- deliver ----------------------------------------------------------- #
    ("release", "deliver", "version bump → signed AAB → CI → internal testing",
     "version file, tag, CI run, state", "verify not run for this worktree", True),
    ("record", "deliver", "close the ledger: result, note, handover copy",
     "state, .pipeline/runs/*-handover-*.md", "no task spec", True),
    ("rc", "deliver", "record the release-candidate call (yes/no + note)",
     "state", "no shipped release", True),
    ("alarm", "deliver", "print what is still open (asks, parked, dirty refs)",
     "nothing", "nothing", True),
    ("poll", "deliver", "tail the last run log",
     "nothing", "nothing", True),
    ("promote", "deliver", "send an internal-track build to production (OWNER ONLY)",
     "Play review, state", "version not on an internal track; no --requested-by", True),
    ("ci", "deliver", "print or write the canonical release workflow",
     ".github/workflows/release.yml (with --write)", "no profile", True),
    # ---- assets ------------------------------------------------------------ #
    ("storefront-direction", "assets", "decide what the listing SAYS, before drawing",
     "docs/storefront-direction.json", "no locked concept", True),
    ("storefront-scenes", "assets", "render REAL composables to screenshots",
     ".pipeline/storefront_scenes/*", "no scenes in the app", True),
    ("storefront-build", "assets", "render → compose → heal → gate, one order",
     ".pipeline/storefront_posters/*, docs/storefront-frames.json",
     "no locked direction; fewer than four scenes; an interrupted earlier run", True),
    ("store-publish", "assets", "upload the reviewed listing and read it back",
     "Play listing, state", "dirty asset tree; interrupted run; no provenance", True),
    ("store-status", "assets", "record what Play says about the last delivery",
     "state", "nothing", True),
    ("store-readiness", "assets", "Play launch compliance: SDK, assets, schedule",
     "nothing (audits)", "never — it reports", True),
    ("store-assets", "assets", "audit/generate/score icon, banner, screenshots",
     "distribution/listing/**", "no base assets", True),
    ("storefront-brand", "assets", "feature graphic from the hero render (Gemini)",
     "distribution/listing/**/featureGraphic*", "no hero render", True),
    ("brand", "assets", "owner feedback → Stitch icon → app → listing, one chain",
     "mipmaps, docs/storefront-icon*, docs/launcher-icon.json",
     "an earlier brand change is still unpublished", True),
    ("gemini-auth", "assets", "sign the browser in to Gemini for the paint step",
     "browser session", "no browser-control", True),
    ("stitch-prompt", "assets", "compile a project-generic, anti-generic screen prompt for Stitch MCP",
     "nothing (prints prompt)", "unknown project", True),
    ("stitch-screen", "assets", "generate a new Stitch screen and export to docs/stitch (refuses if screen exists unless --force)",
     "docs/stitch/**, .pipeline/stitch_manifest.json (with --generate)", "existing screen matches without --force; no MCP connection", True),
    ("stitch-tweak", "assets", "apply a surgical in-place tweak to an existing Stitch screen (edit_screens / REFINE variants)",
     "docs/stitch/**, .pipeline/stitch_manifest.json", "no stitch_project_id; missing screen/change; tweak overstepped", True),
    ("stitch-adopt", "assets", "adopt a reviewed draft Stitch screen onto a surface's own id (no fork, nothing to retire)",
     "docs/stitch/**, .pipeline/stitch_manifest.json", "unknown surface; draft carries no HTML; no stitch_project_id", True),
    ("stitch-retire", "assets", "record a dead Stitch screen: ledger row, no canonical row, no export (dry run without --yes)",
     "docs/stitch/manifest.json, RETIRED.md, .pipeline/stitch_manifest.json",
     "unknown screen; --replaced-by is not canonical", True),
    ("stitch-curate", "assets", "curate Stitch canvases into a canonical manifest vs stale candidates",
     "docs/stitch/manifest.json, .pipeline/stitch_manifest.json", "unknown project", True),
    ("validate-design", "assets", "audit Stitch screens or tweaks for drift, overstepping, and misses",
     "nothing", "no exported screen HTML found; drift/overstepping FAIL", True),
    # ---- ops --------------------------------------------------------------- #
    ("site", "ops", "build/serve the report page for every app",
     "site/", "nothing", True),
    ("schedules", "ops", "register or inspect the nightly launchd jobs",
     "launchd plists", "macOS only", True),
    ("console", "ops", "plan or drive Google Play Console setup",
     "Play console, state", "no browser session", True),
    ("ads", "ops", "paid acquisition: plan then create (paused)",
     "docs/ads-plan.json, Play", "no account; no --commit", True),
    ("publish", "ops", "publish the goal page to GitHub Pages",
     "docs/goals.html, gh-pages", "no vision", True),
    ("docs-audit", "ops", "find the team's docs stating one rule twice",
     "docs (with --fix)", "nothing", True),
    ("onboard", "ops", "create a project's .pipeline profile from its layout",
     ".pipeline/profile.json", "unknown layout", True),
    ("keystore-backup", "ops", "back up the release keystore off-repo and verify it",
     "Google Drive + ~/.config/devteam/keystores", "no backup destination", True),
    # ---- internal ---------------------------------------------------------- #
    ("run-orchestrator", "internal", "spawn a host agent with a prompt",
     "—", "—", False),
    ("setup", "internal", "install the team's own prerequisites",
     "shell profile", "—", False),
    ("mock-server", "internal", "serve a Supabase-shaped mock backend",
     "localhost port", "—", False),
    ("emulators", "internal", "record/reset the emulator baseline",
     ".pipeline/emulators.baseline.json", "—", False),
]

INVENTORY: dict[str, dict[str, Any]] = {}


def _row(verb: str, phase: str, what: str, writes: str, refuses: str,
         agent: bool = True) -> None:
    INVENTORY[verb] = {"verb": verb, "phase": phase, "what": what,
                       "writes": writes or "—", "refuses": refuses or "—",
                       "agent": bool(agent)}


for _item in _RAW:
    _row(*_item)


# The team's own docs, and what each page is for. This is the POINTER CHAIN: the
# front page (AGENTS.md) links a page, a command prints it when it matters, and
# `doctor` refuses an orphan page (a page no command names) or a dead row (a row
# whose file is gone). Prose that nobody is pointed at is prose that rots.
PAGES: list[tuple[str, str]] = [
    ("docs/loop.md", "running work: the commands, what lands where, cleanup, done"),
    ("docs/planning.md", "choosing work: the plan's shape and its gate"),
    ("docs/store.md", "anything the Play listing shows"),
    ("docs/guarantees.md", "a gate refused, or you are fixing the team itself"),
    ("docs/new-app.md", "starting an app from an idea"),
]

TEAM_ROOT = CLI_PY.parent


def page_drift() -> list[str]:
    """Orphan pages and dead rows, in both directions.

    The same rule for the skill's references: a reference file nobody points at
    is a page an agent will never read (2026-11-09, the pointer chain)."""
    declared = {p for p, _ in PAGES}
    on_disk = {str(p.relative_to(TEAM_ROOT)) for p in (TEAM_ROOT / "docs").glob("*.md")}
    out = []
    for page in sorted(on_disk - declared):
        out.append(f"'{page}' exists but no command names it — add a PAGES row "
                   f"(team/toolbox.py) and print it where it matters")
    for page in sorted(declared - on_disk):
        out.append(f"'{page}' is named in PAGES but the file is gone — fix the pointer")
    skill = TEAM_ROOT / "skills/dev-team"
    if skill.is_dir():
        front = (skill / "SKILL.md")
        text = front.read_text() if front.is_file() else ""
        for ref in sorted((skill / "references").glob("*.md")) if (skill / "references").is_dir() else []:
            if f"references/{ref.name}" not in text:
                out.append(f"'references/{ref.name}' is not named in SKILL.md — "
                           f"add it to the Pages table or delete the file")
    return out


def format_pages() -> list[str]:
    lines = ["", "PAGES — each ends with the ONE next command:", ""]
    for page, when in PAGES:
        lines.append(f"  {page:<22} {when}")
    return lines


def commands_in_cli() -> list[str]:
    """Every verb the parser registers. The source is the authority, not this file.

    Multi-line registrations exist (`sub.add_parser(` then the name on the next
    line — keystore-backup, asks), so the whitespace between the call and the
    literal is part of the pattern. A single-line-only regex silently missed two
    live verbs the first time this check was written (2026-11-09)."""
    try:
        src = CLI_PY.read_text()
    except OSError:
        return []
    return re.findall(r'sub\.add_parser\(\s*["\']([a-z0-9-]+)["\']', src)


def drift() -> list[str]:
    """Verbs present in one place and missing from the other — in both directions."""
    declared = set(INVENTORY)
    parsed = set(commands_in_cli())
    out = []
    for verb in sorted(parsed - declared):
        out.append(f"'{verb}' exists in cli.py but has no toolbox row — describe it "
                   f"in team/toolbox.py (what it writes, what it refuses)")
    for verb in sorted(declared - parsed):
        out.append(f"'{verb}' has a toolbox row but no cli.py command — delete the row")
    return out


def for_agents() -> list[dict[str, Any]]:
    return [INVENTORY[v] for v in INVENTORY if INVENTORY[v]["agent"]]


def format_inventory(agent_only: bool = True) -> str:
    rows = for_agents() if agent_only else list(INVENTORY.values())
    lines = [
        "THE TOOLBOX — every verb the team can run",
        "=" * 78,
        "Read this once, then decide the sequence yourself. The team does not route.",
        "`refuses` is the column that matters: a step cannot fail quietly.",
        "",
    ]
    for phase, meaning in PHASES:
        group = [r for r in rows if r["phase"] == phase]
        if not group:
            continue
        lines.append(f"{phase.upper()} — {meaning}")
        for r in group:
            lines.append(f"  {r['verb']:<20} {r['what']}")
            lines.append(f"  {'':<20} writes:  {r['writes']}")
            lines.append(f"  {'':<20} refuses: {r['refuses']}")
        lines.append("")
    lines.append(f"{len(rows)} verbs. Full inventory (incl. host plumbing): devteam tools --all")
    lines.append("A request is not work until `devteam admit` pins it at the queue head.")
    lines.extend(format_pages())
    return "\n".join(lines)
