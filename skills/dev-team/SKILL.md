# dev-team — skill file for ANY agentic tool acting as orchestrator

name: dev-team
description: >
  Operating an autonomous app-development team. TWO MODES:
  (A) run the nightly pipeline (status, ship a task, verify, release); or
  (B) CREATE a brand-new app project from an idea — the full setup workflow
  that ends with a build on Play Console internal testing and a nightly loop.
  The orchestrator (YOU) decides and drives; development is performed
  either directly by the orchestrator in the isolated worktree (universal
  default across Claude Code, OpenCode, and Antigravity) or delegated to a
  subagent you spawn; all quality gates and bookkeeping run via this CLI.

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

**An upgrade is a HYPOTHESIS about what is missing — verify it before you hand
it to the owner as work.** An upgrade's `how` describes the WHOLE path to a
connection (declare the block, place the key, enable the API, grant access), so
it reads as a list of owner chores even when only the last inch is left. On
2026-10-25 two of three flags on one project were fixed in minutes: the Play
service-account key already existed on disk and both Play APIs already answered
for it — the only missing thing was the `connections.play_vitals` /
`connections.play_reviews` declaration in the profile. Before relaying an upgrade,
run `doctor` and a live probe (`research`, or the collector directly):
- key file exists and the read answers → the missing piece is the DECLARATION.
  Add it to the project profile (it is config, not app code, and `doctor` verifies
  it) and re-run `research`; the flag clears itself, presence-driven.
- the read answers with an honest zero (no reviews below a public listing, no
  vitals rows below Play's small-sample threshold) → that is `status=ok` and the
  flag is CLEARED, not stuck; say so rather than leaving the owner chasing it.
- the precondition genuinely is absent (no key, no access, or it needs an emulator
  and a human, as `competitor-recon` does) → THEN it is an owner ask, and it is
  worth stating the single missing step rather than the whole path.

## MODE SELECTION (do this first, every invocation)
- Project exists and has .pipeline/profile.json → MODE A (nightly run
  procedure below). STEP 0 of MODE A is `adapt` — a project onboarded under an
  older team is brought up to date by code before any work is picked; never edit
  `.pipeline/profile.json` by hand to catch up.
- Owner starts a NEW app ("build me an app that…") → MODE B:
  `skills/new-project/SKILL.md` — authoritative.
- Owner asks for ONE agent only → "DIRECT AGENT INVOCATION" section.

## AGENT ROSTER (three representations — all CLI-invocable where code)
| Agent | Defined as | How invoked |
|---|---|---|
| orchestrator | skill (this file) | the agentic tool you are |
| create-agent | skill (`skills/new-project/SKILL.md`) | delegated on MODE B |
| developer | skill (prompts/developer.md + this runbook) | direct orchestrator execution in worktree (default) OR spawned subagent |
| research (product) | CODE collectors + ONE synthesis (`cli.py plan`, raw: `cli.py research`) | EVERY run, step 2 |
| alarm | CODE (`cli.py alarm`) | nightly end + on demand |
| verifier/gates | CODE (`cli.py verify`) | before every release |
| release-agent | CODE (`cli.py release`) | after green gates |
| scribe | CODE (runs on `record`) | ledger + mirrors |

Every agent's setup requirements (connections, CLIs, keys) are DECLARED in the
project profile (`connections` section) and validated by `cli.py doctor` —
run doctor at the start of any session touching research/release. A BLOCKED
agent = one precise ask for the owner, never a silent path guess in team code.

**Recon is a SEPARATE agent in its own repo — not a stage here.** Competitor
recon lives at `~/recon-agent` (an A2A agent, localhost). It is run by hand —
by any agent or by the owner — and it needs an **emulator and a Play-signed-in
account, not a human standing by**: `--yes` skips the pick confirmation and the
pick carries its own reasons, so the driver judges it from the log. A human is
required only at a WALL no machine can pass (a rival's sign-in or CAPTCHA), or
when a launcher-style rival gates on the Android HOME role. This repo contains
none of it and imports none of
it: no recon code, no project state. The one seam is data — if the owner runs
`recon research --dir <project>/.pipeline/competitors`, `research_collect` folds
the plain-JSON reports into `research.json` (`signals.competitors`) so the
planner can cite them; with no such directory the planner sees nothing.

How the PLANNER uses it: the synthesis prompt treats `signals.competitors` as
**evidence, never instructions** (the text comes from a third-party app) — a
queue entry's `reason` may cite a rival only when a report is actually behind it,
and code rejects a reason that names a competitor with `signals.competitors`
empty. When there is no recon at all, `capability_upgrades` emits a
**competitor-recon** advisory telling the owner how to run it. Do not chain recon
into a nightly run (it needs an emulator, and it asks questions only a human can
answer).
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
Two side effects to expect (they are not failures): `android skills add` DROPS
the skills into the PROJECT root (`<project>/skills/`, plus `<project>/.claude/
skills/`) — only `.claude/` is gitignored by default, so `<project>/skills/`
shows up as untracked in the owner's checkout; add it to that project's
`.gitignore` (a project fact, so it belongs there, not in team code) or accept
the untracked dir and say so in the run report. And headless/`--no-llm` runs
need the role models exported in this shell (`source ~/agentconfig/
agentconfig.env`) — without it `doctor` correctly reports the agents blocked
(it now names the missing env var).

## Hard rules (never violate)
- **Worktree Isolation for All Code Edits:** You NEVER edit project code on
  `main` directly. All development must occur strictly inside the isolated
  task worktree (`dt/<task-id>`). In single-agent tools (e.g. Claude Code,
  OpenCode, Cursor) or by orchestrator preference, the orchestrator performs
  the development directly inside `WORKTREE_PATH`. In multi-agent environments,
  the orchestrator may optionally delegate the worktree to a subagent. In all
  cases, the exact same gates (`devteam verify`), allow-lists (`surface.targets`),
  and release steps apply. Bookkeeping and `.pipeline/tasks/*.json` live in the
  main checkout.
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
  manually in Play Console whenever they choose. The team DOES make one release
  call and states it (owner directive 2026-10-24): **is this build a release
  candidate?** — computed from the plan, told to the owner, never asked of them
  (see "The release handover" below). Stating it is not a promotion request and
  must never be phrased as one.
- **Release notes are a first-class deliverable, not a by-product.** They are
  what a user reads in the store, they are length-limited by the store (Play
  rejects a note over `profile['whatsnew_max_chars']`, default 500), and a
  project whose profile sets `whatsnew_curated` owns them by hand — the pipeline
  READS that file and reports it, and must never rewrite it (a curated note was
  silently replaced by generated boilerplate on 2026-10-23).
- Never push with red gates. `devteam verify` must PASS before `devteam release`.
- **Automatic scope/knob/dependency guards are RETIRED (owner directive
  2026-10-31).** There is no `frozen_paths` check, no release-knob diff and no
  dependency-declaration gate. What remains is the task allow-list in the
  developer prompt plus compile/test/device gates; a bad change is reverted, and
  CI only reaches internal testing. Reintroduce a NARROW rule only when a real
  incident names what it prevents.
- **Emulators are torn down, never abandoned.** A run that needs an AVD records
  a start-of-run baseline (`cli.py emulators <project> --record --reset`;
  `run-headless`/`run-orchestrator` do this automatically) and checks it at run
  end (`cli.py verify` and `nightly.sh` call `cli.py emulators <project>`).
  The check stops ONLY emulators that appeared during this run and never one
  the owner had open deliberately. Tell the developer subagent to start an AVD
  through `with-emulator <avd> -- <cmd>` (teardown guaranteed by an EXIT trap).
  A leak is warn-only: it never reds a gate, but the run report must mention it.
  **When the device gate fails on the emulator itself, diagnose before retrying.**
  A failed `:app:connectedDebugAndroidTest` is not always the code. Three
  emulator-only failure modes seen in the wild, in the order they cascade, all of
  which look like unrelated build errors in the gate's output:
  1. `Failed to load snapshot 'default_boot'` + `Emulator process has exited
     early` — the AVD saved a corrupt snapshot on a previous exit. `android
     emulator start` (which is what `with-emulator` calls) LOADS a snapshot by
     default, so ONE bad save poisons every later run. A test AVD should not
     snapshot: set `fastboot.forceColdBoot=yes` / `fastboot.forceFastBoot=no` in
     the AVD's `config.ini` and delete `snapshots/`, or boot with
     `android emulator start --cold`.
  2. `FATAL | Running multiple emulators with the same AVD` — a zombie
     `qemu-system-aarch64` from the failed attempt is still alive (`adb devices`
     shows nothing, so it looks idle) and holds the AVD lock. Kill it and remove
     `~/.android/avd/<avd>.avd/*.lock`.
  3. The boot never comes online within 300 s although the process is alive:
     check the tail of `~/.android/<avd>/emulator.log` for `Showing crashdialog
     to get consent` — a crashed emulator blocks startup on a GUI consent dialog
     that no headless agent can click. Clear `/tmp/android-*/emu-crash-*.db` (the
     pending crash report is what it wants consent to send) and start again.
  Verify the emulator is the problem by checking whether the CODE gates
  (scope/localization/compile/test) already passed: if they did, the failure is
  device infrastructure, and repairing the AVD is part of the run, not a reason
  to record `failed`.

## Where everything lives
- team code/scripts:  `<this skill's dir>/../..`  (the dev-team repo)
- project state:      `<project>/.pipeline/state.json`   (source of truth)
- project profile:    `<project>/.pipeline/profile.json` (project facts incl.
                      verify commands, reference screen/pickers)
- run logs:           `<project>/.pipeline/runs/<run_id>.log`
- task specs:         `<project>/.pipeline/tasks/<task_id>.json`

## Run-outcome contract (every run, both modes)
A run ends in EXACTLY ONE of these — never in silence:
- ✅ **BUILD SHIPPED** — version bump pushed → project CI → internal testing. This
  is the default terminal state whenever the queue holds (or research can
  legitimately refill with) buildable work. **A shipped run hands the owner the
  build, not just the news:** its banner MUST carry the `RELEASE HANDOVER` block
  (what's new, what to manually test, and the release-candidate call the LLM that
  made the change recorded) — see "The release handover" below. A shipped run
  without it is a run-report failure.
- ❓ **OWNER ASK** — the ONLY questions you ever ask are ones no agent can
  answer: a freeze lift/sign-off, a kill-switch flip, discard-vs-keep on user
  WIP. Production promotion is NOT one of them (see the delivery rule above) —
  never ask it, never file it. **The release-candidate call is NOT an owner ask:
  the LLM that made the change makes it and the owner is told** (owner directive
  2026-10-24) — it is part of the RELEASE HANDOVER, recorded with `devteam rc`
  (the owner can replace it with `--owner`). Even then: ask AFTER squeezing every
  non-blocking stage, and if all you have is analysis, convert it — a decision
  issue + a made-ready task spec that becomes buildable on "yes".
  **A STANDING ASK IS NOT OPTIONAL TO RELAY.** `adapt`/`doctor` print the open
  questions at the start and `record` prints an `OWNER ASKS` block on EVERY
  outcome; the banner MUST quote that block (and put the question to the owner
  in the same message) even when the run SHIPPED. "Nobody has said what this app
  is for long term — one sentence" is the standing one and it is **BLOCKING**: a
  project nobody has aimed has no long-term goal, so no work is picked for it —
  `pick` returns rc=4 with the ask, the run's terminal state is the OWNER ASK,
  and the orchestrator puts the question to the owner in plain words. It stays
  that way until the owner answers with
  `devteam vision <project> "<sentence>"`. Owner directive 2026-10-18: *"I should
  have been prompted to provide long term goals. There should not be no goal-less
  mode."* Shipping work aimed at nothing is a run-report failure AND a
  run-design failure. The vision is the owner's forever; the planner proposes the
  milestones UNDER it, never the owner.
- 🛠 **TOOLING/TEAM SHIP** — if genuinely nothing in the APP is buildable but a
  team skill/code lesson conversion or research-export hardening lands, ship
  that (team repo commit) and say so; analysis-only runs are failures.
If the queue is empty: the planner (step 2 — `devteam plan`, which runs every
run) must refill it with executable work. If it still yields nothing
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
A project with no `goals` gets NO WORK PICKED — there is no goal-less mode
(owner directive 2026-10-18). `pick` returns rc=4 with the owner ask, the run's
terminal state is the OWNER ASK, and the orchestrator asks the owner for one
sentence (`devteam vision`) in the same message. Once the vision exists, the
planner proposes the first milestone under it — that is the team's job, not an
owner ask.

**THE PLANNER — one agent owns the plan, and it runs on EVERY run (step 2).**
`devteam plan <project>` is the single place where direction is decided: it reads
fresh data (deterministic collectors — GA4/repo/codebase/CI), makes ONE synthesis
call, and — through code-validated gates — **maintains the goal tree and the
queue**. Nothing else writes the queue, and only the owner writes the vision.
Its contract:
- **It CHANGES the plan when the plan no longer matches reality** (`goal_changes`:
  add a branch, park/retire a dead one, re-point, set-status). No approval gate:
  each change is recorded with a plain-words reason, shown to the owner
  afterwards ("THE TEAM CHANGED THE PLAN" + page banner + `goal log`), and undone
  exactly with `goal revert`. It never asks permission to plan.
- **Maintenance, not regeneration.** Every queued entry gets a verdict each cycle
  (kept / superseded / dropped-with-reason — the `QUEUE IMPACT` block). A plan
  cannot make a queued task vanish silently.
- **The handover:** `queue[0]` is the ONE task the next run builds. `pick`
  executes that head (deterministically, with the delivery-first and
  un-animated-surface fallbacks); it does not re-decide direction.
- **The stamp:** every review writes `state['plan_review']` (when, what it left
  waiting, which task it handed over). `status` prints it and `pick` warns on
  stderr when the plan was not reviewed today — a skipped step 2 is visible
  instead of silent (a nudge, never a hard gate: a run with no LLM must still be
  able to ship).
- **A verdict about code is checkable, so check it.** A `milestone_verdict` that
  marks a TEXT-measured milestone met RETIRES it and re-aims every future
  release, so before the plan is applied the orchestrator re-runs the cheap
  check the synthesis names (grep the artifact for the measure's evidence). If
  the check disagrees, do not apply the pivot: the plan's direction is the one
  thing worth five minutes of verification, and an LLM verdict flips it.
  Metric-measured milestones need no such check — code already evaluates those
  and a code FAIL beats an LLM "met".
Deterministic core, agent at the edges: collectors, schema, tree changes, queue
impact, and the goal gate are all code — the LLM cannot smuggle in an unknown
branch, a missing reason, work against a done or parked goal, or off-goal work
that is not declared.

**Seeing the tree:** `devteam tree <project>` prints vision → milestones/sub-goals
→ leaves (the work each run picks) and writes two machine-local mirrors into the
project: `.pipeline/goals.txt` and `.pipeline/goals.html` (self-contained, click
to collapse; `--open` opens it). Deterministic renderer, no LLM/network;
refreshed by every `record`/`research`. A project with no goal tree renders the
BLOCKING ask instead of an empty page (and gets no work picked).

**The plan is also PUBLISHED, so it travels with the repo.** `.pipeline/` is
excluded repo-locally, so a machine-local tree would die with the laptop. The
same render is written to `docs/plan.md` (GitHub-rendered, business English
first, technical detail in `<details>`) and `docs/plan.html`, and the run's
bookkeeping (`record`, `research`, `adapt`) commits those two files to main and
pushes them — `devteam publish <project>` does the same on demand. Best-effort by
design: a checkout on another branch, no network or a moved remote reports why
and retries next run; it never fails a run. Nothing else in the working tree is
staged, and a local release push that lands mid-run is handled by fetch/rebase.

**The page URL is printed — after EVERY change and on EVERY run.** Anything that
regenerates the page ends with two lines: `goal page: file:///…/goals.html`
(clickable in a terminal) and, once the mirror is on the repo,
`shared plan: https://github.com/…/docs/plan.md` — printed ONLY when it is true
(mirror committed, unchanged, on origin), never a guessed URL. Printed by
`adapt`, `status`, `record`, `doctor`, `tree`, `publish`,
`goal add|park|set-status|retire|reparent|revert|review`, `align --apply`,
`vision`, and `queue add|--done`. `pick` and `research` print them on **stderr**
(their stdout is a machine payload). The owner is therefore never told a change
happened without being handed the URLs that show it, and the run banner ends with
them (run-outcome contract).

**The team CHANGES the tree; the owner reviews afterwards (never waits).**
`devteam goal <project> add|park|set-status|retire|reparent` mutates the plan
directly — no approval gate, and the planner does the same thing inside its own
run via `goal_changes` (applied BEFORE the queue is validated, so a plan can add
a branch and queue work for it in the same cycle; a branch added this way may be
named in a queue entry by its title, and code resolves it). Every change is
recorded with the reason and a before/after snapshot, printed by `status`/`record`
as "THE TEAM CHANGED THE PLAN", and shown as a banner on the goal page; `goal log`
lists them and `goal revert` puts the last one back exactly. Only the long-term
goal stays human-only. The `goal` command is for the owner's own requests and for
repair — the planner is the standing owner of the plan.

**Alignment:** `devteam align <project> "<task or owner request>"` exports the
question with the plan attached; the agent answers JSON (`aligned|neutral|
against|gap` + why/cost/options) and `--apply` records it. `against` exits
non-zero and prints an owner-facing warning; `gap` means the plan has no place
for the work — the agent changes the tree rather than squeezing the work under an
unrelated goal. Verdicts are advisory: recorded and always shown, never silent,
never silently blocking.

## Nightly run procedure (also works interactively)

0. **ADAPT FIRST — make the project match the team you are running.** The team
   changes (new fields, new goal model, new gates); a project was onboarded under
   an older one. Before anything else:
   `python3 <team>/cli.py adapt <project>` (add `--dry-run` to only report).
   Deterministic and idempotent: it reports `up to date` with one line, or lists
   what it repaired — missing profile/state keys, the `components_dir` alias,
   queued tasks whose goal no longer exists (re-pointed at the active goal, or
   declared off-goal), the goal page + mirrors regenerated — plus any STANDING
   OWNER ASKs (a missing long-term goal, dead convention paths, a missing gate
   field). **A missing long-term goal is BLOCKING, not a footnote:** put the ask
   to the owner in the SAME message that reports the run, here at step 0, and do
   not proceed to step 2/3 — `pick` will refuse (rc=4) anyway. You never invent
   direction: a missing goal comes back as an ask to
   pass to the owner, never as a vision the team drafted. Run `status` after it;
   the workflow does not start on an un-adapted project.
1. `python3 <team>/cli.py status <project>` — see queue, shipped, last run.
   `status` is read-only and prints `ADAPT NEEDED` when the project lags the
   team (step 0 is the fix — never migrate by hand). It ends with the
   `goal page: file://…` URL: keep it, the run banner ends with it (10b).
2. **RUN THE PLANNER — every run, not just when the queue is empty.**
   `python3 <team>/cli.py plan <project>`
   ONE agent reviews where the work is heading against fresh data: it refreshes
   the research signals (GA4 + codebase + repo + CI), makes one synthesis call,
   MAINTAINS the tree and the queue (code-validated: schema, tree-change schema,
   goal gate, impact review), prints the owner-facing `PLAN REVIEW` block
   (heading → what is left → the queue → THIS RUN BUILDS … → what the plan moved
   and why), then publishes the plan and prints both URLs. Its output is the
   handover: the queue head is what step 3 will build.
   - Research/planner defaults to EXECUTABLE work (motion/bugfix/feature);
     investigations are capped at 2 and must each name the owner decision or code
     task they feed.
   - SIZE IS PART OF THE PLAN (owner decision 2026-10-18: *"plan lead task not too
     small, [the developer] agent might take 20-25 min to complete it"* — **revised
     by the owner 2026-10-23 (a)**: *"agents take less time to deliver a task … we are
     considering more time … fix this in the code where we are doing the estimate"* —
     **revised again 2026-10-23 (b)**: *"Replan, we are shipping too less code in one
     workflow run"*):
     every feature/bugfix/investigation entry carries `effort_min` — minutes of
     developer work, target ~150, hard band 90-240, enforced in code. ONE RUN SHIPS
     ONE ENTRY, so a head smaller than the run costs the owner the release it paid
     for. The numbers are
     calibrated to what an AGENT session costs, not to human effort: measured here,
     a whole nightly (plan → developer → verify → release incl. the CI watch) runs
     ~12 min, and a developer session that shipped a 1305-line deletion took ~5 min
     (an `effort_min` : wall-clock ratio of ~6-10x, which is what bounds the 240
     ceiling against the developer role's 3600 s timeout).
     The fixed cost of a RUN (verify + version bump + CI + internal-testing build)
     dominates the developer's minutes, so the target moved UP twice — fewer, richer
     entries per release. A plan of 2-4 entries is the healthy shape; the queue is
     never padded to a count. A too-small entry must be folded into the related one
     via `supersedes` or bundled with its siblings (several screens sharing one
     theme = one entry); a too-big one must be split. The review block prints a
     `SIZING` line, and the estimate travels into the developer prompt. The
     delivery-restoring head (red CI) is exempt — the fire outranks sizing.
    - **CODE-FIRST HEAD** (owner directive 2026-10-23: *"we are shipping too less code
      in one workflow run"*): because a run builds only `queue[0]`, the head must be
      executable work (motion/bugfix/feature) whenever any executable entry is queued —
      an investigation is never the handover while code sits behind it, or the release
      ships analysis a user cannot see. Enforced in code (the plan is rejected); an
      investigation may head the queue only when nothing executable exists, and then its
      `reason` says so.
   - Every cycle runs an IMPACT REVIEW: the synthesis must judge each
     already-queued task; the CLI prints a `QUEUE IMPACT` block (added / kept /
     replaced / obsolete / dropped). A queued task can never vanish silently.
   - If it leaves nothing executable → `devteam alarm` + OWNER ASK, then stop
     (see the run-outcome contract). No invented work to fill silence.
   - `devteam research <project>` is the same engine with a machine-readable
     JSON result (used when a caller wants the queue dict); `plan` adds the
     review block + the page publish.
   - HOST TOOL HAS NO SUBPROCESS? `plan <project> --no-llm` writes
     `.pipeline/research-prompt.txt` (the exact prompt, goal tree included) —
     hand it to your OWN native agent, then apply its JSON with
     `plan <project> --apply-result <file>`; the same schema, impact-review
     and goal gates run in code (nothing is trusted just because an agent said it).
   - A SCOPED OWNER REQUEST ("review the existing UX", "look at onboarding") is
     a flag, not a hand-edited prompt: `plan <project> --focus "<the owner's
     words>"` injects it into the prompt as a labelled block (works on the
     `--no-llm` export and the in-process synthesis alike). It only aims the
     review — the same gates still judge the result — so pass the owner's request
     through verbatim and let the planner decide whether the tree moves.
3. DETERMINISTIC PICK (runbook step 3 is code now):
   `python3 <team>/cli.py pick <project>` [--supervised]
   → returns the next task spec file + worktree/prompt command bundle:
     priority order: (a) delivery-restoring code task after a failed run;
     (b) THE PLANNER'S HEAD — the queue entry the planner marked, in its order
     (the queue is the plan; the head is the handover, goal tree or not);
     (c) first safe un-animated surface (fallback: nothing queued is buildable).
   rc=2 nothing actionable (→ step 2 re-review + idle rules)
   rc=4 NO GOAL — the project has no long-term goal: nothing is picked. Put the
     ask to the owner in plain words, in the same message (the run's terminal
     state is the OWNER ASK; `devteam vision <project> "<sentence>"` sets it).
   rc=3 only supervised remain (ask human). The pick is code-verifiable; you
   review and proceed — you do not re-derive selection logic. If `pick` prints
   `NOTE: plan review … NOT reviewed today` on stderr, step 2 was skipped — run
   it before building. Type handling:
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
    "risk": "safe", "effort_min": 150, "created": "<iso>"}
   Write it to `.pipeline/tasks/<id>.json`. `effort_min` (from the picked queue
   entry) is the expected size of ONE developer session — the developer prompt
   shows it, so the task is worked at its intended depth rather than gold-plated
   or rushed.
5. Prepare the sandbox (deterministic git plumbing is code, not you):
   `python3 <team>/cli.py worktree-start <project> --task-id <id>`
   → WORKTREE_PATH / WORKTREE_BRANCH / WORKTREE_BASE.
   Then `python3 <team>/cli.py emulators <project> --record --reset` — record the
   start-of-run emulator baseline. `run-headless`/`run-orchestrator` do this
   automatically, but a STEP-BY-STEP run (this numbered procedure) does not, and
   the run-end leak check trusts whatever baseline file is on disk: with none (or
   a stale one that predates the owner opening an emulator) `verify` classifies a
   device the owner started themselves as "leaked" and stops it, breaking the
   "never stop an emulator you did not start" rule. Recording the baseline here,
   before any device work, is what makes that check safe.
6. Get the exact developer prompt (keep it verbatim):
   `python3 <team>/cli.py developer-prompt <project> .pipeline/tasks/<id>.json`
7. DEVELOP THE TASK — Two execution paths:
   (A) DIRECT ORCHESTRATOR DEVELOPMENT (Universal Default — works in Claude Code, OpenCode, Antigravity, Cursor):
       - You (the orchestrator) perform the implementation directly inside `WORKTREE_PATH`.
       - Follow the exact instructions, conventions, and constraints from the developer prompt generated in step 6.
       - Read mandatory style/design docs (`docs/design.md`, `profile.conventions`, official Google skills).
       - Edit ONLY files under the task's allow-list (`surface.targets`). NEVER edit on `main` directly.
       - UI/UX Excellence & Hierarchy Assertions: design for the real-world physical context of use (glanceability from 15ft, outdoor glare, one-handed reachability). Never ship raw wireframe/canvas prototypes or flat unstyled low-contrast text. Use cohesive design tokens (high-contrast palette, tabular display typography, modern squircles 16-24dp, tactile press feedback) and complete lifecycle states (empty guidance, active interaction, frictionless undo recovery, repeat/rematch loops). Whenever adding or modifying UI screens, components, or interactive flows, write automated UI tests (in the project's test set, e.g. `commonTest` or `androidTest`) verifying layout math, interaction states, and user flows. Run automated UI hierarchy assertions to ensure no sibling view bounds overlap and all text container dimensions fully enclose their measured text bounds.
       - Do not change release knobs (versionName, versionCode, signing, targetSdk) unless the task says so — nothing checks this automatically now.
       - Test incrementally inside the worktree using the project's verify commands (e.g. `gradle compileDebugKotlinAndroid testDebugUnitTest`). Failure to run an on-device runner (no AVD/emulator) is non-blocking (warn-only), but any failure of the test assertions themselves is a hard blocker.
       - If you encounter a build or test error, diagnose and resolve it directly in the worktree.
   (B) SUBAGENT DELEGATION (Optional fallback when supported and conserving orchestrator context):
       - Budget the session at the task's `effort_min` (~150 is the norm for a healthy entry, band 90-240).
       - Spawn subagent with `cwd = WORKTREE_PATH` using your tool's native mechanism (e.g. `invoke_subagent`).
       - Pass the exact prompt from step 6.
       - Model: read `roles.developer.model` from `<team>/config/runtimes.json` if your tool supports per-subagent models.
       - If your tool cannot spawn subagents and you choose not to develop directly, run headless:
         `python3 <team>/cli.py run-headless <project> --task-spec .pipeline/tasks/<id>.json`

8. VERIFY THE WORKTREE — TRUST NOTHING:
   `python3 <team>/cli.py verify <project> --worktree <WORKTREE_PATH> --task-spec .pipeline/tasks/<id>.json`
   - Pass `--task-spec`: the dependency gate reads the task's
     `dependencies_added` declaration from it. Without the spec the gate can
     still REPORT a new third-party dependency but cannot judge it, so the one
     check that forces "what does this library buy us?" goes quiet exactly when
     it matters. Declare dependencies when you write the spec (step 4): a task
     that adds a library names it there — alias, catalogue key or coordinate —
     together with why, because a dependency is a third party, a supply chain, an
     account, a licence and permanent maintenance, and the plan once adopted one
     from a spec's stack line without anybody asking.
   - FAIL → Diagnose the failure:
     - If developing directly: fix the issue inside `WORKTREE_PATH` and re-run verify (up to 2 iterations).
     - If using a subagent: append the failure output to the subagent's context (spawn a FRESH subagent with same prompt + "previous attempt failed as follows: ..." — max 2 attempts total, then `devteam record --result failed` and alarm).
   - PASS → `python3 <team>/cli.py release <project> --worktree <WORKTREE_PATH>
     --task-spec .pipeline/tasks/<id>.json`
     This bumps the version, writes release notes (within
     `profile['whatsnew_max_chars']`, and NOT AT ALL when the project's notes are
     owner-curated — see "The release handover"), commits, pushes to origin
     main, and watches CI. CI delivers to Play internal testing.
     (Note: Audit Google Play launch readiness anytime via `python3 <team>/cli.py store-readiness <project>`.)
   - READ "NOTICED BUT NOT FIXED" NOTES — THEY ARE THE
     PIPELINE'S ONLY FEED-FORWARD. Note unrelated defects rather than fixing
     them out-of-scope. Before recording the run, take each note and either
     QUEUE it (folding it into an entry that already touches that file when it is
     too small to justify its own release) or drop it with a stated reason —
     never leave one unread. A bugfix that removed an
     invented-winner defect from one screen left the identical defect on another
     screen, and only the developer's closing note surfaced it.
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
10b. **Hand the owner the page, every run.** The banner ENDS with the two URLs
    `status`/`record` printed (`goal page: file:///…/goals.html`, and
    `shared plan: https://github.com/…/docs/plan.md` once published) — a run that
    changed the plan, the queue, or shipped anything without pasting them is
    incomplete. They are the owner's one-click "where we are now", and they are
    printed even when a run changes nothing: paste them from the command output,
    never invent or guess a URL.
10c. **Relay, don't remember**: `devteam record` prints an `AGENT FLAGS` block
    (the research agents' upgrade/capability asks) on EVERY outcome. The run
    banner MUST quote that block verbatim in its final report — dropping it is
    a run-report failure even when the build shipped. The same rule applies to
    any `QUEUE IMPACT` block printed by the planner or `queue add`: quote it, so a
    superseded or dropped task is never silent. If record printed no block, the
    banner says "no agent flags pending". The `OWNER ASKS` block printed by the
    same command follows the same rule: it is the questions only the owner can
    answer (no long-term goal yet, a blocked gate), it is printed on every
    outcome, and the banner MUST carry it verbatim — plus put the question to the
    owner in plain words. A shipped run with an unanswered standing ask still ends
    with that ask.
10d. **A shipped run also hands over the BUILD** — `record --result shipped`
    prints a `RELEASE HANDOVER` block (release note + length, what to test by
    hand, and the release-candidate call). Two duties here, in order:
    (1) ASK THE LLM THAT MADE THE CHANGE — the developer session you spawned
    (continue that conversation if your tool can): "you made these changes — does
    this build stand on its own in front of users, yes or no?" — then record the
    answer with `devteam rc <project> --version <v> --yes|--no --note "<its one
    line>"`. No rule, no scoring, nothing to validate: the judgment is the
    agent's (owner directive 2026-10-24). If the session is closed, answer it
    yourself from the diff and the developer's report.
    (2) STATE the call in the banner as a statement, not a question: the owner is
    told whether the build is a release candidate, never asked to guess it.
    Details below.

## The release handover (owner requirement 2026-10-23)
A build on internal testing is only useful if the owner knows what it says and
what to do with it. So every shipped run's report carries three things, printed
by code (`record`) and quoted verbatim by the orchestrator:

1. **What's new — the note Play will show.** The exact committed text, with its
   length against the allowed limit: `what's new (231/500 chars, …)`. The store
   turns away an over-long note, so the length is part of the report, not a
   detail (the note is trimmed to `profile['whatsnew_max_chars']`, default
   500; the generated copy names the task's first clause, never the 200-char
   internal label).
   - **Where the words come from is a project fact.** Default: the pipeline
     generates them from the task. `profile['whatsnew_curated'] = true` means the
     OWNER writes them: the pipeline reads the file, prints it and never rewrites
     it (and the report says which of the two it was — an owner must never be
     shown boilerplate as if it were their own words, nor their words replaced
     silently).
   - **THE GENERATED LINE IS ONLY AS GOOD AS THE LABEL, so write one when the
     label is pipeline-shaped.** The fallback note interpolates the task's label
     verbatim (`✓ New in this update: <label>`), and `_short_label` only cuts a
     long mechanism sentence at its first clause — it does NOT humanize a slug, by
     design (de-hyphenating would mangle a real product name like `PDF-Kit`, the
     exact hazard its docstring names). So a queue label such as
     `day-one-pause-runthrough` would ship to the store as
     `✓ New in this update: day-one-pause-runthrough` — internal words in a user's
     listing. The seam is already there: the task spec (`.pipeline/tasks/<id>.json`,
     which the ORCHESTRATOR owns) may carry an explicit `"whatsnew"` string and
     `_whatsnew_text` uses it verbatim (clamped to `whatsnew_max_chars`). When the
     picked entry's label is a slug or otherwise reads as pipeline vocabulary, the
     orchestrator writes the user-facing line into the spec before spawning the
     developer, and the handover then quotes that copy. Never "fix" this with a
     copy heuristic in team code — a judgment about storefront wording is exactly
     the kind of approximation the owner has told the team to stop computing
     (lessons 2026-10-24, pass 3).
2. **What to manually test.** The pipeline's gates prove the build compiles,
   passes its tests and installs — they cannot feel the app. So the handover
   lists the areas the diff touched (derived from the task's targets, never
   invented) plus the concrete claim the queue entry made, and asks the owner to
   walk it. This is not ceremony: on 2026-10-23 a device gate went green while a
   success screen printed a MediaStore id, and a hand walk found seven defects an
   agent crawl's "0 blocked taps" had hidden.
3. **Is this build a release candidate? — the LLM that MADE the change decides;
   nothing in the code rules on it.** Owner directive 2026-10-24: first *"if a
   build is a release candidate that agent has to tell, not human"*, then *"let
   llm decide this, ask this to llm only which made the changes, that's it, no
   eval nothing"*. So there is **no rule, no scoring, no file-overlap check and no
   validation of the answer** — the handover prints the question and the single
   command that records the answer, and the judgment belongs to the agent:
   - **Ask the developer session** (the agent that wrote the code — continue its
     conversation if your tool can): *"you made these changes — does this build
     stand on its own in front of users, yes or no?"* One line of why.
   - If that session is gone, the orchestrating LLM answers it, having seen the
     diff and the developer's report.
   - Record it: `devteam rc <project> --version <v> --yes|--no --note "<the one
     line>"`. The owner can replace the call later with `--owner`, and both stay
     in `state['release_candidates']` (newest wins, so it cannot drift).
   **State the call in the run banner** — as a statement, not a question: the
   owner is told whether the build is a release candidate, never asked to guess.
   **Nothing acts on the verdict**: there is still no promotion path in this
   team, and the line is never phrased as "should we promote it" — promoting
   beyond internal testing stays the owner's manual step in Play Console.

## When the owner hands over a SPEC (do this before planning it)
A spec arrives as a document, not as a queue entry, and it is usually part-wrong
about the app. Before it reaches the planner, DIFF it against the shipped tree
and answer three questions per item: is it ALREADY BUILT, is it PARTIAL, or does
it CONTRADICT what is shipped? Then check the spec against the store's own rules.
Two incidents, one session apart, both caught before any code was written: the
spec asked for `targetSdk 34` when Play rejects anything below its current floor
(shipping it would have failed the release), and it asked for Compose Material3
UI on an app the same owner had already ordered to be fully canvas-drawn with no
stock widget anywhere. A spec is also the cheapest place to find work that DOES
NOT need doing — its "core" was already implemented and unit-tested, and
building it again would have been pure waste. Report the diff to the owner as
ALIGNED / PARTIAL / CONFLICT with evidence (file + line where possible), let
them resolve the conflicts, and only then let the planner turn it into queue
entries. Record the adopted spec as the project's source of truth (its PRD) so
the two cannot drift.
**QUESTION THE STACK, NOT JUST THE FEATURES.** A spec's stack line ("use X",
"via Y") reads like a requirement and is actually a *suggestion about how* — and
it can contradict the spec's own principles. A spec that demands "fully local, no
backend" while naming a hosted billing service is the common case: the service is
a LAYER on top of the store's own billing, not an alternative to it, so adopting
it adds a third party and an account (plus a revenue-share fee) in exchange for
benefits the app may not be able to use yet. Check what a named service actually
DOES before it becomes the plan, say plainly what it would and would not buy, and
record the deviation if the app's constraints make the leaner path the right one.

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
1. The planner (`devteam plan`, step 2 of every run) always collects `signals.ci`
   (last delivery-workflow conclusion + failure log tail) and MUST queue the
   delivery-restoring bugfix/feature as the first queue entry
   (validator-enforced whenever `last_conclusion=failure`).
2. `pick` escalates that queue head ABOVE motion surfaces whenever the last
   recorded run result is `failed`.
3. Engine/hostile-file frustration is resolved through
   `profile['constraint_lifts']` — {"paths", "decided_by", "note"} recorded by
   the OWNER in the profile; `verify.scope_check` honors them and the freeze
   stays absolute for everything else. Orchestrator never invents a lift.
4. Orchestration reruns plan → pick → developer → verify → release
   **automatically, max 2 loops**, without a new owner ask; after loop 2 stop
   honestly with the alarm + a run report (never a silent red main).
5. **CI retry on release** (owner decision 2026-10-17): `push_and_deliver` monitors
   CI after each push and retries up to **3 attempts** if CI fails or times out.
   Each retry creates a fresh version bump commit (incrementing from the latest
   remote tag) and re-pushes. If all 3 attempts fail, the run records `failed`
   and the alarm files a GitHub Issue.

## Queue admissions & impact review (owner requirement 2026-10-16)
A new task never enters the queue in isolation. **Every admission — the planner's
cycle or an explicit orchestrator add — must judge the impact on the tasks
already queued**, because new work can make older entries irrelevant (already
covered, same target + type, or a better replacement). Rule: *propose in the
agent, judge and apply in code; never drop silently.*

- Planner cycle: the synthesis reviews `signals.repo.current_queue` and, for
  each entry, either carries it forward, lists its exact label in a new entry's
  `"supersedes"`, or lists it in top-level `"dropped"` with a reason.
  Validation rejects unknown labels. Any queued task that simply vanishes is
  recorded as an *unexplained drop* and flagged.
- Explicit add: `python3 <team>/cli.py queue <project> add --spec <spec.json>`
  (or `--type/--label/--target/--reason`, plus repeatable `--supersedes LABEL`
  and `--position head|tail`). Code folds same-type/same-target work into the
  new entry and reports the impact.
- Both paths print `QUEUE IMPACT (…)`: `+ added`, `= kept`,
  `x superseded/replaced`, `x obsolete` (target already shipped), `! dropped`.
  Quote it in the run banner.
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
- "run competitor recon" / "do competition research" / "check a competitor
  app" → that is a **separate agent** at `~/recon-agent`, not a stage of this
  repo. Run it there: `recon research --yes` (discover → pick → ask → crawl →
  report; it needs an EMULATOR and a Play-signed-in account — not a human, since
  `--yes` delegates the pick confirmation to whoever is driving — and a human is
  required only at a sign-in/CAPTCHA wall), or `recon play` / `recon install`
  for one step. Do NOT chain it into the pipeline and do not look for recon code
  here.
- "use researcher to find a suitable design.md so that is enforced" / "run market research" →
  The research agent has live web research capabilities via the **Tavily MCP Server**
  (configured in `~/.gemini/config/mcp_config.json`, powered by `tavily-mcp` and `TAVILY_API_KEY`).
  Tools available: `tavily_search`, `tavily_research`, `tavily_extract`, `tavily_crawl`, `tavily_map`
  (or via code: `python3 <team>/cli.py market <project> "<topic>"`).
  When asked to research and build a design system or market benchmark:
  1. Researcher queries Tavily for modern Android/Compose design standards, typography scales,
     spacing tokens, and competitor UX patterns.
  2. The research synthesis and product thesis are handed to **Stitch MCP** (`create_project`,
     `create_design_system_from_design_md`, `generate_screen_from_text`) to create the initial
     screen mockups and authoritative design tokens. If a worker lacks Stitch MCP tools, it raises
     an `orchestrator_requests` block for the orchestrator.
  3. Writes the design specification to `<project>/docs/design.md`.
  4. Once the workflow understands the design, refinements loop back to Stitch (`generate_variants`,
     `edit_screens`, `apply_design_system`) to keep code and mockups synchronized.
  5. Registers `docs/design.md` in `<project>/.pipeline/profile.json` under `conventions`.
  6. Runs `python3 <team>/cli.py adapt <project>` to validate.
  7. The developer agent's prompt automatically injects `docs/design.md` under
     **CODING CONVENTIONS (MANDATORY READS — style law; must read before writing code)**
     on all future runs, strictly enforcing the design system across the entire team.
- "store readiness" / "check store compliance" / "researcher store readiness" →
  The researcher automatically ingests full Play Store compliance data under `signals.store_readiness` in `.pipeline/research.json` and `.pipeline/research-prompt.txt`. If blockers exist, the researcher prioritizes compliance tasks before discretionary features, and capability upgrades flag outstanding policy items. Standalone audit: `python3 <team>/cli.py store-readiness <project>`.
- "replace the store screenshots" / "store assets" / "the listing shows X" →
  Four steps, in this order, and none of them is optional:
  1. **Render** from the app's own composables: `python3 <team>/cli.py storefront-scenes <project> --render`
     (never hand-drawn HTML; the scene spec is the project's `docs/storefront.json`).
  2. **Editorial review — an LLM decides, not the filenames.**
     `devteam storefront-scenes <project> --review` writes the prompt for the orchestrating LLM,
     `--review --llm` calls the configured reviewer model, `--review --apply <json>` records the
     orchestrator's answer. The review names the shipping order, the cuts and the reason for each;
     it is committed as `docs/storefront-review.json`. Two policy rules override any model:
     **a monetization surface (paywall/purchase/subscription/upgrade/price) never ships**, and every
     kept frame must be ranked (an unranked scene is cut, never silently shipped).
  3. **Frame in ranked order**: `--frame` writes `01_<slug>.png` … and removes pipeline-owned
     frames the review cut. The critic (`store-assets`) fails a listing that still carries a
     monetization frame, `icon = null`/flat placeholder tiles, or an order that disagrees with the review.
  4. **Publish, then verify**: `devteam store-publish <project>` (dry run) / `--yes` uploads
     icon + feature graphic + screenshots in order through the Play API and verifies the committed
     state. `PERMISSION_DENIED` on commit means the service account lacks publishing rights — finish
     in Play Console; if the app has **Managed publishing** on, the approved change still needs the
     owner's Publish click in Publishing overview. Never report "republished" from a successful
     upload alone: a 403 commit used to read as success and left the paywall frame live.
Rule: honor the narrower scope exactly — no pipeline chaining — except the
non-negotiables (never commit on red gates; never
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

