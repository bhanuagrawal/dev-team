# dev-team — a project-independent agent team for app development

A small standalone team (this repo) that operates on app projects on this
machine. **Two modes, both agent-driven:**

- **MODE A — operate an existing project** (a real project today): nightly
  pipeline — status → task → worktree → developer subagent → gates → release
  → version-bump push → CI → Play internal testing.
- **MODE B — create a new app from an idea** (`skills/new-project/SKILL.md`):
  the create-agent sets EVERYTHING up — official-skill refresh, JetBrains KMP
  template (Android-first, iOS deferred), MVI core adoption, signing key,
  repo + secrets, proven CI workflow, gates — ending in the first build on
  Play internal testing + the nightly loop installed.

Project facts live **in the project** (`<project>/.pipeline/`); generic craft
lives here. Team code is deliberately thin: only the irreducible deterministic
core (gates, worktree plumbing, version-truth release, ledger, alarms). The
agents define the rest in skills, and development leans on **official
Google/JetBrains skills refreshed before every coding session**.

## Run-outcome contract

Every run ends in **a build shipped to internal testing, an owner ask, or a team/tooling ship — never in silent analysis-only**. The orchestrator asks questions only when a genuinely owner-gated decision blocks the path (production promotion, freeze lifts, kill-switch flips), then continues to a build. See `skills/dev-team/SKILL.md` → Run-outcome contract.

## Self-improvement loop (standing)

The team gets better every run, and stays **extremely generic**: every run logs
surprises in `skills/lessons.md`, and lessons that yield durable fixes get
implemented into team code/skills **in the same run** — fixes must be
project-agnostic (project quirks live in `<project>/.pipeline/profile.json`),
run-derived, and proven with `devteam doctor`. See
`skills/dev-team/SKILL.md` → Hard rules + Upkeep for the contract.

## The skills (the actual "team brain")
- `skills/dev-team/` — orchestrator contract: mode selection, nightly runbook,
  fallback spawning, alarms.
- `skills/new-project/` — create-agent playbook + reference assets (proven
  CI ymls, MVI event-core files).
- `skills/android-code/` — coding-style law for dev subagents.
- `skills/checks/` — executable expertise (localization completeness…).
- `skills/lessons.md` — appended after every surprise; the orchestrator's
  standing duty (see `skills/dev-team/SKILL.md` → Upkeep / Hard rules) is to
  promote lessons into durable, generic fixes in team code/skills the same run
  — never project-specific, always proven by `devteam doctor`.

## Deterministic core (cli.py + team/)
```
python3 cli.py status|onboard|scan <project>
python3 cli.py worktree-start <project> --task-id ID
python3 cli.py developer-prompt <project> <spec.json>
python3 cli.py queue <project> [add|review]   # admission + impact review
python3 cli.py verify <project> --worktree <path>
python3 cli.py release <project> --worktree <path> --task-spec <spec.json>
python3 cli.py record <project> --result shipped|failed|blocked …
python3 cli.py poll / alarm / setup
```
Only here, only these: gates (frozen-paths scope, 15-locale check, build+test),
worktrees, tag-truth version bump + push, state ledger, alarms. Zero LLM, zero
project names.

### Queue impact review
Any task entering the queue — a research refill or an orchestrator
`queue add` — is judged against the tasks already queued: new work can make an
older entry irrelevant (same target + type, already covered, or superseded).
Code records every verdict in `state['queue_events']` and prints a
`QUEUE IMPACT` block (`+ added`, `x superseded/replaced`, `x obsolete`,
`! dropped`, `= kept`) for the run banner; nothing is ever dropped silently.

## Delivery goal (both modes)
Version-bump push → project's own GitHub Actions → **Play Console internal
testing**. Production promotion stays human-only; the team has no production
path.

## The local-checkout lesson
Machine-local checkouts can be stale/diverged. ALL code reads/scans/dev happen
in worktrees at `origin/main` (`.pipeline/scan`, `.pipeline/worktrees/`).
The user's local working tree — including its uncommitted WIP — is never
touched.

## Layout
```
cli.py  team/  prompts/developer.md  scripts/nightly.sh
config/runtimes.json            # role → runtime → model
skills/                          # the real team brain (above)
```
The team repo can live anywhere — it currently sits in `~/Documents/AI/dev-team`
alongside the other agent systems. Projects resolve by name from
`DEVTEAM_PROJECTS_HOME` (optional env override), the team's parent folder, and
`~/AndroidStudioProjects` (in that order); absolute paths always work.

## Adding a project (existing repo)
`python3 cli.py onboard <path>` → review `.pipeline/profile.json` →
run manually anytime (just ask an agent session), or opt into a
clash-checked nightly: `python3 cli.py setup --project <path> --schedule auto [--at HH:MM]`.

## Creating an app from scratch
Say it in plain language to any agent with this skill loaded →
`skills/new-project/SKILL.md` runs the whole turnkey.

## Division of labor

| Layer | Who | What |
|---|---|---|
| Orchestrator | any agentic tool (opencode, Claude Code, …) running **with the dev-team skill** | decide + drive: pick task, prepare sandbox, spawn developer, gate, release, record |
| Developer | the orchestrator's **native subagent** (model from `config/runtimes.json`) | edit code in the task worktree, per `prompts/developer.md` |
| Python | `cli.py` + `team/*` — deterministic only | gates (scope/localization/build/test), worktree plumbing, version bump, state bookkeeping, alarms. Zero LLM, zero project names. |
| Scheduler | macOS launchd (`com.devteam.nightly`) | boots the orchestrator daily at the configured time |

## Delivery goal

Version-bump push → project's own GitHub Actions → **Play Console internal
testing**. Production promotion stays human-only; this team has no production
path by construction.

## Key routes

```
python3 cli.py status <project>          # queue / shipped / last run
python3 cli.py onboard <project>         # detect + write profile draft (idempotent)
python3 cli.py worktree-start <project> --task-id ID
python3 cli.py developer-prompt <project> <spec.json>
python3 cli.py verify <project> --worktree <path>
python3 cli.py release <project> --worktree <path> --task-spec <spec.json>
python3 cli.py record <project> --result shipped|failed|blocked …
python3 cli.py alarm <project>           # staleness + CI reconciliation → GitHub Issue
python3 cli.py setup --project <path> --at 23:10   # launchd + global skill links
```

## The local-checkout lesson

This app's machine-local git checkout can be stale/diverged (it currently is:
v3.13.17+ work exists only on origin). Therefore **all reading of project code
(scans, discovery, references) happens in a worktree at `origin/main`**
(`.pipeline/scan`), and all development happens in per-task worktrees.
The user's local working tree — including its uncommitted WIP — is never
touched by the team.

## Layout

```
cli.py                  deterministic CLI (above)
team/                   core, scan, verify, developer (headless fallback),
                        shipper, alarm, scribe, scheduler (launchd)
prompts/developer.md    the developer contract (prompt template)
scripts/nightly.sh      launchd entry: boots the orchestrator agentic tool
config/runtimes.json    role → runtime → model (per-tool headless invocation)
skills/dev-team/        THE skill: what any agentic orchestrator must do
skills/checks/          executable expertise (localization completeness …)
```

## Adding a project

1. `python3 cli.py onboard <path>` — detects stack/src roots/components/
   reference screens, discovers unanimated surfaces, writes
   `<project>/.pipeline/profile.json` + starter state.
2. Review the profile: prune dark/invisible features from `targets`, adjust
   `frozen_paths` + risk labels. The profile is the human-reviewed contract.
3. `python3 cli.py setup --project <path> --at <HH:MM>` if it should run nightly.
