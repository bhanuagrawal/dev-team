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
yet) gets the same research pipeline as an established project — just with an
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
- **Self-improvement is a standing duty, not a leftover:** after every run,
  append what surprised you to `dev-team/skills/lessons.md` (append-only) and,
  whenever a lesson yields a durable fix, APPLY IT to the team's own
  code/skills — this is the one sanctioned way the orchestrator edits
  `dev-team/`. A valid team change is: (a) generic — zero project names, one
  project's quirk lives in that project's `.pipeline/profile.json`, never in
  team code (now ENFORCED, not just prose: `doctor` greps the team's code/skills
  for every registered project's name and BLOCKS on any hit — only
  `skills/lessons.md`, the append-only surprises log, may name a project);
  (b) run-derived — it traces to a concrete surprise/lesson, not
  speculation; (c) proven — `devteam doctor <project>` still passes after, and
  the changed command is exercised at least once if cheap. `config/runtimes.json`
  (model choices) and project `profile.json` `targets` stay owner-approved —
  do not change either for them; propose instead. When in doubt whether a fix
  is generic enough, keep it out of team code and put it in the skill prose.
- Never hand-edit `.pipeline/state.json` — write via `devteam record`.
- The ONLY delivery goal is CI → Play **internal testing** (the version-bump
  push triggers the project's CI). Production promotion is human-only: no path
  in this team, and never an owner ask or a filed issue — the owner promotes
  manually in Play Console whenever they choose.
- Never push with red gates. `devteam verify` must PASS before `devteam release`.
- Frozen paths from the project profile are absolute — if a task seems to
  require them, stop and mark the task blocked instead.
- **Emulators are torn down, never abandoned.** A run that needs an AVD records
  a start-of-run baseline (`cli.py emulators <project> --record --reset`;
  `run-headless`/`run-orchestrator` do this automatically) and checks it at run
  end (`cli.py verify` and `nightly.sh` call `cli.py emulators <project>`).
  The check stops ONLY emulators that appeared during this run and never one
  the owner had open deliberately. Tell the developer subagent to start an AVD
  through `with-emulator <avd> -- <cmd>` (teardown guaranteed by an EXIT trap).
  A leak is warn-only: it never reds a gate, but the run report must mention it.

## Where everything lives
- team code/scripts:  `<this skill's dir>/../..`  (the dev-team repo)
- project state:      `<project>/.pipeline/state.json`   (source of truth)
- project profile:    `<project>/.pipeline/profile.json` (project facts incl.
                      frozen paths, verify commands, reference screen/pickers)
- run logs:           `<project>/.pipeline/runs/<run_id>.log`
- task specs:         `<project>/.pipeline/tasks/<task_id>.json`

## Run-outcome contract (every run, both modes)
A run ends in EXACTLY ONE of these — never in silence:
- ✅ **BUILD SHIPPED** — version bump pushed → project CI → internal testing. This
  is the default terminal state whenever the queue holds (or research can
  legitimately refill with) buildable work.
- ❓ **OWNER ASK** — the ONLY questions you ever ask are ones no agent can
  answer: a freeze lift/sign-off, a kill-switch flip, discard-vs-keep on user
  WIP. Production promotion is NOT one of them (see the delivery rule above) —
  never ask it, never file it. Even then: ask AFTER squeezing every
  non-blocking stage, and if all you have is analysis, convert it — a decision
  issue + a made-ready task spec that becomes buildable on "yes".
- 🛠 **TOOLING/TEAM SHIP** — if genuinely nothing in the APP is buildable but a
  team skill/code lesson conversion or research-export hardening lands, ship
  that (team repo commit) and say so; analysis-only runs are failures.
If the queue is empty: refill via research (which must now default to
executable work — see its prompt rules). If research still yields nothing
executable, the run's deliverable is the OWNER ASK (alarm + question) — that
is "stopping honestly", not idling. Never ask what to do; never ask about
things an agent can decide. Question budget: zero unless a gate truly blocks.

## GOAL MODE — direction of every release (owner decision 2026-10-18)
Companies run on long/mid/short-term goals; so does this pipeline, and the
goals are DATA, not a new agent:
- **long term** — `profile['goals']['vision']`: the app's thesis (owner-authored).
- **mid term** — `profile['goals']['milestones']`: an outcome + measures, in
  order. The ACTIVE milestone is the first not-done one (deterministic).
- **short term** — the existing research queue; every entry must name the
  milestone it advances in `advances` (`off_goal` declares the exceptions).
Division of labour: the synthesis only PROPOSES (tags tasks, judges a milestone
whose measures are text); CODE DECIDES everything derivable — active milestone,
metric-measure evaluation (`goals.check_measures`; a code FAIL always beats an
LLM "met"), goal-linked ordering in `pick`, and the GOALS/report lines. A run
banner therefore always states the direction: `goal: [m1-…] <milestone> ← <task>`.
When the active milestone is met, the next entry is the NEXT milestone (research
proposes it; an owner call becomes an issues_to_file ask) — never invented polish.
A project with no `goals` runs exactly as before (goal-less mode).

**Seeing the tree:** `devteam tree <project>` prints vision → milestones/sub-goals
→ leaves (the work each run picks) and writes two mirrors into the project:
`.pipeline/goals.txt` and `.pipeline/goals.html` (self-contained, click to
collapse; `--open` opens it). Deterministic renderer, no LLM/network; refreshed
by every `record`/`research`. A goal-less project renders the standing ask
instead of an empty page.

## Nightly run procedure (also works interactively)

1. `python3 <team>/cli.py status <project>` — see queue, shipped, last run.
2. If nothing executable remains → REFILL the queue:
   run `python3 <team>/cli.py research <project>` (code agent: pulls GA4 +
   codebase + repo signals; ONE synthesis call; updates state.json queue —
   no-LLM fallback writes research.json for you to read). Research defaults to
   EXECUTABLE work (motion/bugfix/feature); investigations are capped at 2 and
   must each name the owner decision or code task they feed. Every refill runs
   an IMPACT REVIEW (below): the synthesis must judge each already-queued task
   and the CLI prints a `QUEUE IMPACT` block. If research adds nothing executable
   → `devteam alarm` + OWNER ASK, then stop (see the run-outcome contract). No
   inventing work to fill silence; no analysis-only endings.
   HOST TOOL HAS NO SUBPROCESS? `research <project> --no-llm` writes
   `.pipeline/research-prompt.txt` (the exact prompt, goal tree included) —
   hand it to your OWN native agent, then apply its JSON with
   `research <project> --apply-result <file>`; the same schema, impact-review
   and goal gates run in code (nothing is trusted just because an agent said it).
3. DETERMINISTIC PICK (runbook step 3 is code now):
   `python3 <team>/cli.py pick <project>` [--supervised]
   → returns the next task spec file + worktree/prompt command bundle:
     priority order: (a) delivery-restoring code task after a failed run;
     (b) GOAL MODE — when the project declares `profile['goals']`, the queue
     entry whose `advances` names a live milestone (goal-linked work outranks
     polish, so cosmetic motion can never starve product work);
     (c) first safe un-animated surface; (d) remaining queue head
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
7. SPAWN THE DEVELOPER AS A SUBAGENT — your tool's own native mechanism:
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
    may file a GitHub Issue. Then report the OUTCOME BANNER to the user/feed,
    one of the three contract states — e.g. `✅ SHIPPED vX → internal testing`
    / `❓ OWNER ASK: <the decision>` / `🛠 TEAM SHIP: <what landed>` — plus what
    ran, and next step. An analysis-only report with no ask and no ship fails
    the run-outcome contract.
10b. **Relay, don't remember**: `devteam record` prints an `AGENT FLAGS` block
    (the research agents' upgrade/capability asks) on EVERY outcome. The run
    banner MUST quote that block verbatim in its final report — dropping it is
    a run-report failure even when the build shipped. The same rule applies to
    any `QUEUE IMPACT` block printed by research or `queue add`: quote it, so a
    superseded or dropped task is never silent. If record printed no block, the
    banner says "no agent flags pending".

## Interactive (human-present) sessions
Same procedure; `supervised` tasks become available when a human is with you —
say so in your report, then run the task with the human watching.

## Alarms you may receive
- nightly launched you → follow the procedure; never skip verify/release/record.
- alarm Issue exists with [dev-team] title → start at `status`, diagnose from
  `.pipeline/runs/`, fix cause (model missing, gh auth, CI red), re-run.

## Self-healing delivery loop (owner decision 2026-10-16)
When a run's release could not deliver (red delivery workflow — a store
upload/policy rejection, e.g. Play target-API level increases), the pipeline
fixes itself instead of idling:
1. `research` always collects `signals.ci` (last delivery-workflow conclusion
   + failure log tail) and MUST queue the delivery-restoring bugfix/feature as
   the first queue entry (validator-enforced whenever `last_conclusion=failure`).
2. `pick` escalates that queue head ABOVE motion surfaces whenever the last
   recorded run result is `failed`.
3. Engine/hostile-file frustration is resolved through
   `profile['constraint_lifts']` — {"paths", "decided_by", "note"} recorded by
   the OWNER in the profile; `verify.scope_check` honors them and the freeze
   stays absolute for everything else. Orchestrator never invents a lift.
4. Orchestration reruns research → pick → developer → verify → release
   **automatically, max 2 loops**, without a new owner ask; after loop 2 stop
   honestly with the alarm + a run report (never a silent red main).
5. **CI retry on release** (owner decision 2026-10-17): `push_and_deliver` monitors
   CI after each push and retries up to **3 attempts** if CI fails or times out.
   Each retry creates a fresh version bump commit (incrementing from the latest
   remote tag) and re-pushes. If all 3 attempts fail, the run records `failed`
   and the alarm files a GitHub Issue.

## Queue admissions & impact review (owner requirement 2026-10-16)
A new task never enters the queue in isolation. **Every admission — a research
refill or an explicit orchestrator add — must judge the impact on the tasks
already queued**, because new work can make older entries irrelevant (already
covered, same target + type, or a better replacement). Rule: *propose in the
agent, judge and apply in code; never drop silently.*

- Research refill: the synthesis reviews `signals.repo.current_queue` and, for
  each entry, either carries it forward, lists its exact label in a new entry's
  `"supersedes"`, or lists it in top-level `"dropped"` with a reason.
  Validation rejects unknown labels. Any queued task that simply vanishes is
  recorded as an *unexplained drop* and flagged.
- Explicit add: `python3 <team>/cli.py queue <project> add --spec <spec.json>`
  (or `--type/--label/--target/--reason`, plus repeatable `--supersedes LABEL`
  and `--position head|tail`). Code folds same-type/same-target work into the
  new entry and reports the impact.
- Both paths print `QUEUE IMPACT (…)`: `+ added`, `x superseded/replaced`,
  `x obsolete` (target already shipped), `! dropped`, `= kept`. Quote it in the
  run banner.
- Every verdict is preserved in `state['queue_events']` (append-only) — nothing
  is ever deleted silently, so a dropped task can always be restored.
- Standalone check anytime: `python3 <team>/cli.py queue <project> review`
  (read-only) flags queued entries whose target already shipped and internal
  duplicates.

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

## Upkeep (every run — the team improves itself)
At the START of every run, scan `dev-team/skills/lessons.md` for unconverted
lessons and promote them (see Hard rules). At the END of every run, append two
lines per surprise to <team>/skills/lessons.md: what surprised us + the
rule/code-check that would have caught it — then, if the durable fix is small
and generic, implement it in team code/skills the SAME RUN instead of
suggesting it in the report; reserve "suggested only" for fixes that are
big, risky, or need an owner decision. Report which lessons you converted.

## MODE B pointer (do not inline — follow the dedicated skill)
For create-a-new-app: `skills/new-project/SKILL.md` (same team dir) is the
authoritative create-agent playbook: official-skill refresh → intake
questions for the owner (only-human gates) → official JetBrains KMP template
as the scaffold (Android-first; no iOS artifacts) → MVI core adoption →
keystore + gh repo + secrets → proven CI workflow adaptation → first
v1.0.0 tag → `gh run watch` → internal-testing build → record + setup.

