# dev-team — skill file for ANY agentic tool acting as orchestrator

name: dev-team
description: >
  Operating an autonomous app-development team. TWO MODES:
  (A) run the nightly pipeline (status, ship a task, verify, release); or
  (B) CREATE a brand-new app project from an idea — the full setup workflow
  that ends with a build on Play Console internal testing and a nightly loop.
  The orchestrator (YOU) decides and drives; development is performed by a
  SUBAGENT you spawn; all quality gates and bookkeeping run via this CLI.

## MINIMAL-TOOLS PRINCIPLE (all agents, both modes)
Every agent runs on whatever connections the project HAS and still produces
full output. It NEVER blocks on an absent data source, and it ALWAYS ends its
report with an **Upgrades** section: the data sources the owner could connect
to make future decisions better — title, benefit, exact how (usually a
profile.connections entry + a key file; doctor verifies). Each upgrade is
presence-driven: once connected, it disappears from reports automatically.
Examples: GA4/Firebase analytics → numbers instead of vibes for priorities;
store reviews → churn reasons from users who uninstall same-day; Play vitals
→ per-release regression checks. This is how a brand-new app (no analytics
yet) gets the same research pipeline as MindfulLauncher — just with an
explicit "here's what connecting this would buy you" line.

## MODE SELECTION (do this first, every invocation)
- Project exists and has .pipeline/profile.json → MODE A (nightly run
  procedure below).
- Owner starts a NEW app ("build me an app that…") → MODE B:
  `skills/new-project/SKILL.md` — authoritative.
- Owner asks for ONE agent only → "DIRECT AGENT INVOCATION" section.

## AGENT ROSTER (three representations — all CLI-invocable where code)
| Agent | Defined as | How invoked |
|---|---|---|
| orchestrator | skill (this file) | the agentic tool you are |
| create-agent | skill (`skills/new-project/SKILL.md`) | delegated on MODE B |
| developer | skill (prompts/developer.md + this runbook) | YOUR native subagent |
| research (product) | CODE + one synthesis call (`cli.py research`) | standalone or when queue drained |
| alarm | CODE (`cli.py alarm`) | nightly end + on demand |
| verifier/gates | CODE (`cli.py verify`) | before every release |
| release-agent | CODE (`cli.py release`) | after green gates |
| scribe | CODE (runs on `record`) | ledger + mirrors |

Every agent's setup requirements (connections, CLIs, keys) are DECLARED in the
project profile (`connections` section) and validated by `cli.py doctor` —
run doctor at the start of any session touching research/release. A BLOCKED
agent = one precise ask for the owner, never a silent path guess in team code.
- Project exists and has .pipeline/profile.json → MODE A, follow "Nightly run
  procedure" below.
- Owner asks to start a NEW app (anything like "build me an app that…") →
  MODE B: load and follow `skills/new-project/SKILL.md` (same team dir) — it
  is authoritative. Keep this file's HARD RULES regardless of mode.

## OFFICIAL-SKILLS REFRESH (before writing/editing ANY project code)
Development leans on official Google/JetBrains skills and they drift:
1. `android skills add --all --project <project>` — updates the official
   Google Android skills (`github.com/android/skills`) to latest.
2. `git -C ~/.agents/kotlin-skills pull` (or clone
   `github.com/Kotlin/kotlin-agent-skills`) — official Kotlin org skills
   (AGP9 migration, gradle-dsl-doctor, tooling).
Log date + versions in `~/.agents/android-skills-version.txt`. If offline,
proceed and log it. This is a hard step for both modes.

## Hard rules (never violate)
- You NEVER edit project code directly. You may write `.pipeline/tasks/*.json`
  and `.pipeline/` bookkeeping only. Code edits belong to your spawned developer.
- Never edit `dev-team/` code, `config/runtimes.json`, or skills. Lessons go to
  `dev-team/skills/lessons.md` (append-only).
- Never hand-edit `.pipeline/state.json` — write via `devteam record`.
- The ONLY delivery goal is CI → Play **internal testing** (the version-bump
  push triggers the project's CI). Production promotion is human-only and has
  no path in this team.
- Never push with red gates. `devteam verify` must PASS before `devteam release`.
- Frozen paths from the project profile are absolute — if a task seems to
  require them, stop and mark the task blocked instead.

## Where everything lives
- team code/scripts:  `<this skill's dir>/../..`  (the dev-team repo)
- project state:      `<project>/.pipeline/state.json`   (source of truth)
- project profile:    `<project>/.pipeline/profile.json` (project facts incl.
                      frozen paths, verify commands, reference screen/pickers)
- run logs:           `<project>/.pipeline/runs/<run_id>.log`
- task specs:         `<project>/.pipeline/tasks/<task_id>.json`

## Nightly run procedure (also works interactively)
1. `python3 <team>/cli.py status <project>` — see queue, shipped, last run.
2. If nothing unanimated remains (all shipped/animated) → REFILL the queue:
   run `python3 <team>/cli.py research <project>` (code agent: pulls GA4 +
   codebase + repo signals; ONE synthesis call; updates state.json queue —
   no-LLM fallback writes research.json for you to read). If research adds
   nothing actionable → `devteam alarm` then report and stop. No inventing
   work to fill silence.
3. DETERMINISTIC PICK (runbook step 3 is code now):
   `python3 <team>/cli.py pick <project>` [--supervised]
   → returns the next task spec file + worktree/prompt command bundle:
     priority order: first safe un-animated surface → research queue head
     (bugfix/feature only; investigation stays yours to perform per step 3).
   rc=2 nothing actionable (→ research/step-2 refill + idle rules)
   rc=3 only supervised remain (ask human). The pick is code-verifiable; you
   review and proceed — you do not re-derive selection logic. Type handling:
   - motion → targets from surfaces; use the MOTION developer prompt template.
   - bugfix/feature → targets from the queue entry; use the GENERIC developer
     prompt template (`devteam developer-prompt <project> <spec> --template generic`).
   - investigation → code-free task: fresh analysis subagent, read-only +
     docs, write findings to docs/progress/investigation-<slug>.md, then
     `record --result shipped --note "investigation: <finding>"`.
   - `risk: supervised` tasks require a human present — otherwise skip them.
4. Compose the task spec JSON:
   {"id": "dt-<label-slug>-<seq>", "type": "motion|bugfix|feature|investigation",
    "surface": {"label": "<label>", "targets": ["<repo-relative path(s)>"]},
    "risk": "safe", "created": "<iso>"}
   Write it to `.pipeline/tasks/<id>.json`.
5. Prepare the sandbox (deterministic git plumbing is code, not you):
   `python3 <team>/cli.py worktree-start <project> --task-id <id>`
   → WORKTREE_PATH / WORKTREE_BRANCH / WORKTREE_BASE.
6. Get the exact developer prompt (keep it verbatim):
   `python3 <team>/cli.py developer-prompt <project> .pipeline/tasks/<id>.json`
7. SPAWN THE DEVELOPER AS A SUBAGENT — your tool's own mechanism, e.g.
   the Task/subagents tool (Claude Code) or the agents mechanism (opencode):
   - agent prompt = the exact prompt from step 6
   - run with cwd = WORKTREE_PATH (pass this in the subagent's initial
     instruction: "work ONLY inside directory <WORKTREE_PATH>")
   - MODEL: read roles.developer.model from <team>/config/runtimes.json and
     set it for the subagent if your tool supports per-subagent models; if it
     does not, proceed with your default model and RECORD which model you
     used in the run log. Never silently skip the model note.
   - if your tool CANNOT spawn subagents at all, use the documented fallback:
     tell the user you are doing so, then use the headless path:
     `python3 <team>/cli.py run-headless <project> --task-spec .pipeline/tasks/<id>.json`
     which spawns the runtime subprocess from runtimes.json and applies the
     same gates.
8. When the developer subagent returns, TRUST NOTHING:
   `python3 <team>/cli.py verify <project> --worktree <WORKTREE_PATH>`
   - FAIL → append the failure output to the subagent's context (spawn a FRESH
     subagent with same prompt + "previous attempt failed as follows: ..." —
     max 2 attempts total, then `devteam record --result failed` and alarm).
   - PASS → `python3 <team>/cli.py release <project> --worktree <WORKTREE_PATH>
     --task-spec .pipeline/tasks/<id>.json`
     This bumps the version, writes release notes, commits, pushes to origin
     main, and watches CI. CI delivers to Play internal testing.
9. Bookkeeping (one command, every outcome):
   `python3 <team>/cli.py record <project> --result shipped|failed|blocked
     [--task-spec .pipeline/tasks/<id>.json] [--version <ver>] [--commit <sha>]
     --note "<one line>"`
10. `python3 <team>/cli.py alarm <project>` — staleness/CI reconciliation;
    may file a GitHub Issue. Then report outcome to the user/feed.

## Interactive (human-present) sessions
Same procedure; `supervised` tasks become available when a human is with you —
say so in your report, then run the task with the human watching.

## Alarms you may receive
- nightly launched you → follow the procedure; never skip verify/release/record.
- alarm Issue exists with [dev-team] title → start at `status`, diagnose from
  `.pipeline/runs/`, fix cause (model missing, gh auth, CI red), re-run.

## DIRECT AGENT INVOCATION (scope-limited runs)
The owner may ask for ONE agent only, and that must work:
- "run only the developer agent" → spawn the developer subagent on the given
  task/spec and STOP — no pipeline bookkeeping beyond that agent's own duty.
- "run only release / verify / alarm / create / setup" → run just that stage
  via the matching commands/skill section, then report.
Rule: honor the narrower scope exactly — no pipeline chaining — except the
non-negotiables (never commit on red gates; never touch frozen paths; never
hand-edit state.json; MODE-B gates still asked). State a one-line report of
what you did and did not chain.

## Upkeep
When something surprising burned time (bad model behavior, missing tool,
flaky gate), append two lines to <team>/skills/lessons.md: what surprised us +
the rule/code-check that would have caught it. Suggest the matching durable
fix (check-script vs skill line) in your report.

## MODE B pointer (do not inline — follow the dedicated skill)
For create-a-new-app: `skills/new-project/SKILL.md` (same team dir) is the
authoritative create-agent playbook: official-skill refresh → intake
questions for the owner (only-human gates) → official JetBrains KMP template
as the scaffold (Android-first; no iOS artifacts) → MVI core adoption →
keystore + gh repo + secrets → proven CI workflow adaptation → first
v1.0.0 tag → `gh run watch` → internal-testing build → record + setup.

