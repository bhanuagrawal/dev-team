# dev-team — an autonomous app-delivery team that runs on any agentic tool

A small, standalone team that operates on Android / Kotlin-Multiplatform app
projects. **The team is project-independent:** every project fact lives in that
project's `.pipeline/profile.json`, and this repo holds only generic craft plus a
thin deterministic core (gates, worktrees, version truth, ledger, alarms). The
"team brain" is a set of skills; the muscle is Python. Zero LLM in the code, zero
project names in the code (enforced — see *Principles*).

Two modes, both driven by an agent:

- **MODE A — operate an existing project.** One run ships exactly one task to
  Play Console **internal testing**, or stops with a real question. Run it
  nightly, or by saying *"run workflow for &lt;Project&gt;"* to any agent with the
  `dev-team` skill loaded.
- **MODE B — create a new app from an idea.** `skills/new-project/SKILL.md`
  scaffolds, signs, wires CI and lands the first build on internal testing.

---

## What happens in every run

The orchestrator is an agent; everything below marked **code** is a command whose
output is not negotiable. The agent decides *what* to do, never *whether* a gate
passed.

### The order, end to end (one screen)

The orchestrator has two tools: **run a shell command**, and **edit files in the task worktree** (or spawn a subagent). The skill is the checklist that names the commands; the run is a fixed
sequence of them. Nothing below is invented at runtime — code decides *what is
allowed*, the agent decides *what to do*.

| # | What the orchestrator runs | Kind |
|---|---|---|
| — | *trigger* `scripts/nightly.sh <Project>` → `opencode run --auto "Run workflow for <Project>."` | shell |
| 0 | `cli.py adapt` | code |
| 1 | `cli.py status` | code (read-only) |
| 2 | `cli.py plan` | code + **one LLM call** |
| 3 | `cli.py pick` — then write `.pipeline/tasks/<id>.json` | code, then agent |
| 4 | `cli.py worktree-start` | code |
| 5 | `cli.py developer-prompt` | code |
| 6 | develop the task inside worktree | direct orchestrator execution (default) or subagent |
| 7 | `cli.py verify` | code (**gate**) |
| 8 | `cli.py release` | code (**gate**, only on green) |
| 9 | `cli.py record` | code |
| 10 | `cli.py alarm` — then post the outcome banner | code + agent |
| — | *tail* emulator teardown, then `alarm` again | shell |

The agent's judgement is steps 3 (compose the spec) and 6 (develop the task in worktree) and
its choice of words in 10; **`plan`, `verify`, `release` and `record` are gates
whose output it relays, never overrides.** It never edits project code — it runs
commands, writes task specs, and spawns the developer. Each step is expanded below.

### Step 0 — does the project exist, and does it match the team? (code)

Mode selection first: a project with `.pipeline/profile.json` is MODE A; anything
else is MODE B.

Then **adapt** — `cli.py adapt <project>`. The team changes over time (new data
shape, new goal model, new gates); a project was onboarded under an older team.
Adapt is deterministic and idempotent: it reports `up to date` in one line, or
lists what it repaired —

- missing profile/state keys, renamed fields, a moved path alias;
- convention files that no longer exist on disk (repaired when a single obvious
  new location can be traced; ambiguity is left alone, never guessed);
- queued tasks pointing at a goal that no longer exists (re-pointed at the active
  goal, or explicitly declared off-goal);
- the goal page and its mirrors regenerated.

It never invents direction. A missing long-term goal comes back as a **standing
OWNER ASK**, not as a vision the team drafted.

**doctor** — `cli.py doctor <project>` — checks that every agent's declared
requirements exist: `gh` authenticated, `origin` reachable, a developer model
configured, `java_home` present, and the analytics connection (a property id plus
a token that actually mints). A BLOCKED agent produces one precise owner ask —
never a silent guess. `plan` (and its engine `research`) runs doctor itself.

### Step 1 — where do we stand? (code, read-only)

`cli.py status <project>` prints the **plan** (the long-term goal, milestones in
order with `✓` done / `>` active / `·` later marks, the one being worked on, and
any plan changes the team made since you last looked), the queue grouped by the
goal each entry serves, the surfaces (shipped / animated / un-animated, with
risk labels), the last run, and — if the project lags the team — `ADAPT NEEDED`
(step 0 is the fix; nobody migrates by hand). It ends with the URLs below.

### Step 2 — the planner reviews the plan, and hands over the task (code + one LLM call)

`cli.py plan <project>` — **every run, not only when the queue is dry.** This is
the one place direction is decided, and the one writer of the plan and the queue.

Deterministic collectors (no LLM) gather: analytics (GA4 via a service account,
if the project declares one), a codebase scan **at `origin/main`**, repo/tag/
ledger facts, and the state of the project's own CI workflow — including the
failure log tail when delivery is red.

Then **exactly one** synthesis call returns strict JSON, and *code validates and
applies it*. The synthesis proposes:

- `insights` — the one-line headline plus a grounded analysis;
- a `queue` (≤ 6 entries), executable work first; every entry names
  `advances` (the goal it serves) and a `reason` citing a measure; investigations
  are capped and must each name the decision or code task they feed;
- `goal_changes` — **changes to the goal tree itself** (add a branch, park a dead
  one, set-status, retire, re-point) when the plan no longer matches reality,
  each with a plain-words `why`;
- `off_goal` — anything kept that serves no goal, with a reason (never silent);
- a `milestone_verdict` for the active goal;
- `dropped` — queue entries removed on purpose, with a reason;
- `issues_to_file` — decisions only a human can make.

Validation is code: output schema, tree-change schema (unknown branch, missing
`why`), then the tree changes applied on a **copy** so the whole plan is judged
against the tree it would produce (a plan may add a branch and queue work for it
in the same cycle), then the goal gate — unknown goal ids, a missing `advances`,
work aimed at a done **or parked** goal, a motion task with no listed surface, a
label that is not actually queued, work against a branch the same plan retired.
When CI is red, the first entry **must** be the delivery-restoring fix. Nothing is
trusted because an agent said it.

**Maintenance, not regeneration:** every entry already queued gets a verdict each
cycle — kept, superseded, or dropped with a reason — in the `QUEUE IMPACT` block.
A queued task can never vanish silently. And the handover is explicit: `queue[0]`
is the task the run builds (step 3 consumes it).

The review is then stamped in state (`plan_review`: when, how many waiting, which
task was handed over). `status` prints that line, and `pick` warns on **stderr**
when the plan was not reviewed today — a skipped step 2 is visible, never silent,
and never a hard block (a run with no LLM available must still be able to ship).

It prints the owner-facing `PLAN REVIEW` block (where this is going → what is left
→ the queue with `← NEXT` → this run builds … → what the plan moved and why),
regenerates the page, publishes the plan and prints both URLs.

No subprocess available in the host tool? `plan --no-llm` writes the exact
prompt (`.pipeline/research-prompt.txt`) for your own native agent, and
`plan --apply-result <file>` applies its JSON through the same validation.
`cli.py research <project>` is the same engine with a machine-readable JSON
result (no review block, no publish) for callers that want the queue dict.

### Step 3 — pick exactly one task (code)

`cli.py pick <project>`. The order is data, not judgement:

1. **delivery first** — if the last run failed, the queue's executable head
   (a red store upload outranks everything);
2. **the planner's head** — the queue entry the planner marked, in its own order:
   the queue is the plan and its head is the handover (this holds whether or not
   the project declares a goal tree, so a maintained queue is never dead weight);
3. the first un-animated surface (fallback for when nothing queued is buildable),
   skipping `supervised` ones unless a human is present.

It writes the task spec (`.pipeline/tasks/<id>.json`: id, type, surface label +
targets, risk, and for queue work the `advances` goal and the queue `reason`).
Exit code 2 = nothing actionable (→ step 2), 3 = only supervised tasks remain.
If the plan was not reviewed today it says so on stderr — run step 2 first.

### Step 4 — a sandbox, never your working tree (code)

`cli.py worktree-start <project> --task-id <id>` creates a fresh git worktree at
`origin/main` (`dt/<task>`), and records the base. All reading, scanning and
development happen in worktrees at `origin/main`; the local checkout — including
uncommitted WIP — is never touched.

### Step 5 — the exact developer prompt (code)

`cli.py developer-prompt <project> <spec>` renders the prompt verbatim: project
facts, targets, the risk addendum for gesture/drag-adjacent surfaces, the
analytics context, the reason the item was queued, and — for goal-linked work —

> `GOAL THIS SERVES: <the goal> — done here means: <its outcome>`
> `LONG-TERM (owner): <the vision>`
> *Do not widen scope to "help" this goal: a smaller change that serves it is
> better than a bigger one that does not.*

Convention files that no longer exist are annotated `SKIP` rather than sent as a
broken path.

### Step 6 — develop (agent)

The orchestrator develops the task inside `WORKTREE_PATH` directly (universal default
across Claude Code, OpenCode, Antigravity, Cursor) adhering to the developer prompt,
conventions, and target allow-list. In multi-agent environments, the orchestrator may
optionally spawn a native subagent or run headless (`cli.py run-headless`).

### Step 7 — trust nothing: gates (code)

`cli.py verify <project> --worktree <path>` checks the *actual diff*, not the
agent's report:

- **scope** — nothing under the project's `frozen_paths`; an empty diff is a
  failure. Owner-recorded `constraint_lifts` are honoured; the orchestrator
  cannot invent one;
- **localization** — every newly referenced string resource exists in every
  locale the project ships;
- **compile**, then **tests** (a single retry, for the one known flaky suite);
- **emulator hygiene** (warn-only) — see *Safety rails*.

FAIL → The orchestrator fixes the failure in the worktree (or re-prompts the subagent with the failure output, max 2 attempts total), then the run records `failed` and alarms.

### Step 8 — release (code)

`cli.py release` bumps the version from the latest remote tag, writes the
release notes, commits, pushes to `origin/main` (fetch + rebase if the remote
moved meanwhile), and then **watches CI**, retrying up to 3 times with a fresh
bump if CI fails or times out. On tag-triggered projects it pushes the tag too.
CI delivers to Play Console internal testing. Production promotion is never part
of this — it stays a human action.

Release notes are a store artefact, so they get store rules: the generated copy
names the task's first clause (not the 200-char internal label) and is clamped to
`profile['whatsnew_max_chars']` (default 500 — Play rejects longer notes). A
project whose profile sets `whatsnew_curated: true` owns its notes by hand; the
pipeline then reads that file and **never rewrites it**, and the report says so.

### Step 9 — bookkeeping (code, every outcome)

`cli.py record <project> --result shipped|failed|blocked`. It appends to the run
ledger, writes the per-target delivered rows on a ship (attributing each to the
goal named in the task spec), refreshes the remaining-work list, regenerates the
plan views, and prints, for the run banner: the goal line
(`goal: [<goal>] <title> ← <task>`), the **AGENT FLAGS** block (what the planner
says the owner could connect next), the **OWNER ASKS** block (the questions only
the owner can answer — including "nobody has said what this app is for long term"
when no goal is set), on a ship the **RELEASE HANDOVER** block (below), the
**THE TEAM CHANGED THE PLAN** block if the plan moved, and the URLs. It also
publishes the plan (below).

#### The release handover (on every ship)

Handing over a build is not the same as reporting one. On a ship the same command
prints what the owner needs to USE the build — quoted verbatim by the banner:

- **what's new** — the exact committed note with its length against the limit
  (`231/500 chars`) and its source (pipeline-generated, or owner-curated);
- **manually test** — the areas the diff touched (derived from the task's
  targets) plus the claim the queue entry made. The gates prove it compiles,
  passes and installs; they cannot feel the app, and on 2026-10-23 a green device
  gate hid a success screen printing a MediaStore id that a hand walk caught at
  once;
- **is this build a release candidate?** — the LLM that MADE the change decides
  and the owner is told, never asked (owner directives 2026-10-24: "that agent
  has to tell, not human", then "let llm decide this, ask this to llm only which
  made the changes, that's it, no eval nothing"). Nothing in the code rules on
  it: no rule, no scoring, no file checks, no validation of the answer. The
  handover prints the question and the orchestrator asks it of the developer
  session that wrote the code (or answers itself if that session is gone), then
  records it with `cli.py rc <project> --version <v> --yes|--no --note "…"` — one
  line of why, stored per version in `state['release_candidates']`. The owner can
  replace any call with `--owner`. Nothing acts on the verdict: there is still no
  promotion path here, and it is never phrased as "should we promote it".

Every one of those blocks is printed by code on every outcome, so the banner
cannot drop one by forgetting: a run that ships while the app has no long-term
goal still ends with the ask for that one sentence (and the run keeps asking until
it is answered with `devteam vision`).

### Step 10 — silence must be loud (code)

`cli.py alarm <project>` is deterministic: no success for two days while work
remains, three consecutive failed runs, or a red CI on the last pushed release
commit each produce (or de-duplicate) a GitHub Issue on the project.

Then the orchestrator posts the outcome banner — see the contract below — and the
run is over.

---

## The plan: why every release has a direction ("goal mode")

Companies run on long-, mid- and short-term goals; so does this pipeline. They are
**data, not another agent**:

| Horizon | Where it lives | Who writes it |
|---|---|---|
| long term | `profile['goals']['vision']` — the app's thesis, one sentence | **the owner only** |
| mid term | `profile['goals']['milestones']` — an outcome + measures, in order | owner and team (see below) |
| short term | the queue; every entry names the goal it serves, and its **head is the next run's task** | the planner + code |

The **active** goal is simply the first one not done — deterministic, not a
judgement call. A milestone whose measure is a number (e.g. "uninstall events
≤ 2 per 30 days") is evaluated by code from the collected signals; code's verdict
always beats an agent's. Text-only measures are the agent's honest judgement.

**One agent owns the plan, and it runs on every run** (step 2, `devteam plan`).
It reads fresh data, then maintains both halves of the plan: the **tree**
(`goal_changes` — add a branch, park a dead one, re-point work) and the **queue**
(every entry judged, kept, superseded or dropped with a reason). Its head is the
task the run builds. It is the only writer of either, and it never asks for
permission — the owner reviews afterwards.

**The team may change the plan; the owner reviews afterwards and never waits.**
`devteam goal <project> add|park|set-status|retire|reparent` mutates the tree
directly, with the reason and a before/after snapshot — the owner's own request
path, and the repair path. The planner does the same inside one cycle via
`goal_changes` (a branch added there can be queued against in the same plan). Every
change is recorded, shown as a banner on the plan page and in `status`, and
`goal revert` restores the previous plan exactly. Only the long-term goal is
human-only. Every review also leaves a stamp (`plan_review`) that `status` prints
and `pick` warns about on stderr when it is missing for today — a skipped plan
review is visible, never silent, and never a hard block.

**Alignment is a judgement, so it is done by an agent, and never silently.**
`devteam align <project> "<task or request>"` exports the question with the plan
attached; the agent answers `aligned | neutral | against | gap` with why, cost and
options; `--apply` records it. `against` warns the owner in plain words; `gap`
means the plan has no place for the work — so the team changes the tree rather
than squeezing the work under an unrelated goal. Both are advisory: recorded,
always shown, never blocking.

## What the owner sees

`devteam tree <project>` prints the plan (goal → goals → the work each run picks)
and writes two machine-local mirrors: `.pipeline/goals.txt` and
`.pipeline/goals.html` — one self-contained page, no CDN, business English first
with every technical detail collapsed behind a click (`--open` opens it).

`.pipeline/` is excluded from git **repo-locally**, so that page would die with
the laptop. So the same render is also **published with the project**: the run's
bookkeeping commits `docs/plan.md` (markdown, rendered by GitHub, business view
first, technical detail in `<details>`) and `docs/plan.html` to `main`, and
pushes. `devteam publish <project>` does the same on demand. Publishing is
best-effort by design — another branch checked out, no network, a moved remote
each report *why* and retry next run; it never fails a run, and it stages nothing
but those two files.

Every command that regenerates the plan, and every run, prints where the plan is:

```
goal page:   file:///…/.pipeline/goals.html                       (this machine)
shared plan: https://github.com/…/blob/main/docs/plan.md          (the repo)
```

The second line is printed **only when it is true** — mirror committed, unchanged,
on `origin` — never a guessed URL. `pick` and `research` print both to stderr,
because their stdout is a machine payload.

## Run-outcome contract

A run ends in exactly one of these. Never in silence, never in analysis alone:

- **✅ BUILD SHIPPED** — version bump pushed → project CI → internal testing.
  This is the default terminal state whenever buildable work exists (or the
  planner can legitimately refill with it).
- **❓ OWNER ASK** — only for questions no agent can answer (a freeze lift, a
  sign-off, a user-WIP keep-or-discard). Production promotion is **not** one of
  them: never asked, never filed. Even then the run squeezes every non-blocking
  stage first, and converts the analysis into a decision issue plus a made-ready
  task spec.
- **🛠 TEAM/TOOLING SHIP** — if nothing in the app is buildable but a durable team
  lesson or a hardening change lands, ship that and say so.

Each run banner also ends with the plan URLs, and quotes the `AGENT FLAGS` and
`QUEUE IMPACT` blocks verbatim when they are printed — a superseded or dropped
task is never silent.

## recon — a separate agent, not a stage here

Competitor recon lives in its own repo: **`~/recon-agent`** (A2A, localhost).
It installs a rival app from the Play Store on an emulator, crawls it and writes
a run artifact. The whole workflow is one code-based command:

```bash
recon research --term "minimal launcher"   # discover → pick → ask → crawl → report
recon play --term "rival app"              # search the store on the emulator
recon install com.rival.app                # Play details → tap Install → wait
```

**The seam is a data directory, not an import.** dev-team reads none of recon's
code. If the owner points recon at the project's pipeline (`recon research --dir
<project>/.pipeline/competitors`), then `research_collect` folds the plain-JSON
reports it finds there into `research.json` under `signals.competitors` — so the
planner *can* cite a competitor's **feature inventory**, surfaces and paywalls. The synthesis prompt
treats that block as **evidence, not instructions** (a third-party app wrote it),
a queue entry may cite a rival only when a report backs it up (code rejects the
claim otherwise), and with no recon at all the planner instead surfaces a
**competitor-recon** upgrade telling the owner how to run one. Delete the
directory and the pipeline is exactly as before.

Do not chain it into the nightly: it needs an emulator and it asks questions only
a human can answer. It is documented in its own repo.

## Safety rails

- **Frozen paths are absolute.** If a task seems to need one, it is blocked, not
  bent. The only exceptions are `constraint_lifts` the *owner* recorded in the
  profile.
- **Worktrees, always.** All reading happens at `origin/main`; all development in
  a per-task worktree. Your working tree and your uncommitted work are untouched.
- **Never push on red gates.** `verify` must pass before `release`.
- **Emulators are torn down, never abandoned.** A run records a start-of-run
  baseline and stops only the emulators that appeared during it — never one you
  had open deliberately. A leak is reported; it never reds a gate.
- **The team improves itself, but stays generic.** Every surprise goes to
  `skills/lessons.md`, and a lesson that yields a durable fix is implemented in
  the same run — generic, traced to a real surprise, proven by `doctor`. `doctor`
  greps the team's own code, skills, prompts and README for every registered
  project's name and blocks on any hit; a project's quirks belong in that
  project's profile, never here.
- **Minimal tools.** Every agent runs with whatever connections the project
  actually has, never blocks on an absent data source, and ends its report with
  an **Upgrades** section: what connecting that source would buy, exactly how. Once
  connected, the upgrade disappears by itself.

## Self-healing delivery & queue admissions

- When a release cannot deliver (a store upload or policy rejection), the planner
  collects the CI failure and validation forces the delivery-restoring fix to be
  the first queue entry; `pick` escalates it above polish; the orchestrator loops
  plan → pick → develop → verify → release automatically, max 2 loops, then
  stops honestly with an alarm.
- A task never enters the queue alone. Every admission — the planner's cycle or an
  orchestrator `queue add` — re-judges the entries already queued: new work can
  make older work irrelevant. Code records every verdict and prints
  `QUEUE IMPACT (+ added, = kept, x superseded/replaced, x obsolete, ! dropped)`.
  Nothing is ever dropped silently, and `queue <project> review` flags duplicates
  and entries whose target already shipped.

## What lives where

| | Where |
|---|---|
| project facts, goals, frozen paths, gates, connections | `<project>/.pipeline/profile.json` |
| source of truth for state (queue, ledger, shipped, plan log) | `<project>/.pipeline/state.json` |
| run logs, task specs, research, plan mirrors | `<project>/.pipeline/…` (git-excluded, machine-local) |
| **the published plan** | `<project>/docs/plan.md` + `docs/plan.html` (committed, pushed) |
| generic craft: skills, prompts, gates, CLI | **this repo** |

## The deterministic core (`cli.py` + `team/`)

```bash
python3 cli.py status|pick|verify|release|record <project>
python3 cli.py adapt <project> [--dry-run]      # step 0: match the current team
python3 cli.py doctor <project>                 # agents' declared requirements
python3 cli.py plan <project> [--no-llm] [--apply-result <file>]
                                                # step 2: THE PLANNER — review +
                                                # maintain the tree/queue + hand
                                                # over the task (the queue head)
python3 cli.py research <project> [--no-llm] [--apply-result <file>]
                                                # same engine, raw JSON result
python3 cli.py queue <project> [add|review]     # admission + impact review
python3 cli.py worktree-start <project> --task-id ID
python3 cli.py developer-prompt <project> .pipeline/tasks/ID.json
python3 cli.py tree <project> [--html] [--open] # the plan, locally
python3 cli.py publish <project>                # put the plan on the repo now
python3 cli.py goal <project> add|park|set-status|retire|reparent|revert|review|log
python3 cli.py vision <project> "one sentence"  # the owner's goal
python3 cli.py align <project> "<request>" [--apply]
python3 cli.py emulators <project> [--record [--reset]]
python3 cli.py alarm <project> ; python3 cli.py poll <project> ; python3 cli.py schedules
python3 cli.py rc <project> --yes|--no [--version V] [--note "…"] [--owner]
                                                # record the release-candidate
                                                # call (the making LLM decides)
python3 cli.py onboard <path> ; python3 cli.py setup --project <path> [--at HH:MM]
```

Only here, only these: gates, worktrees, version truth, the ledger, the plan,
alarms. Zero LLM, zero project names.

## Division of labour

| Layer | Who | What |
|---|---|---|
| Orchestrator | any agentic tool with the `dev-team` skill | decide + drive: adapt, **plan**, pick, sandbox, develop in worktree (or spawn developer), gate, release, record, report |
| Developer | direct orchestrator execution in worktree (default) or **native subagent** | edit code inside the task worktree, per `prompts/developer.md` |
| Research / **planner** | code collectors + **one** synthesis call (`cli.py plan`) | review the plan against fresh data: maintain the tree and the queue, then hand over the task (the queue head) — judged and applied by code |
| Python (`cli.py`, `team/`) | deterministic only | gates, worktree plumbing, version bump, bookkeeping, plan rendering/publishing, alarms |
| Scheduler | macOS `launchd` (`com.devteam.nightly`) | boot the orchestrator each night at the configured slot |

## Adding a project, creating one, and scheduling

- **Existing repo:** `python3 cli.py onboard <path>` → review
  `.pipeline/profile.json` (prune dark/invisible targets, adjust `frozen_paths`,
  set the gates and connections) → optionally
  `python3 cli.py setup --project <path> --schedule auto`.
- **New app:** say it in plain language to any agent with the skill loaded →
  `skills/new-project/SKILL.md` runs the whole turnkey flow, including the
  owner-only questions (identity, positioning, the long-term goal) and the first
  internal-testing build.
- **Scheduling:** `setup` installs a launchd job that runs
  `scripts/nightly.sh <Project>`, which boots the orchestrator with
  *"Run workflow for &lt;Project&gt;"*, caps it with a 90-minute watchdog, keeps a
  single-instance lock, and records/takes down emulators around the run. Slots are
  clash-checked inside the owner's night window; `devteam schedules` lists them.

## Layout

```
cli.py                  deterministic CLI (above)
team/                   core, adapt, scan, goals, tree, publish, research (the
                        planner engine), queue, verify, developer, shipper,
                        scribe, doctor, alarm, emulator, scheduler, align
prompts/developer.md    the developer contract (motion + generic templates)
scripts/nightly.sh      launchd entry point
config/runtimes.json    role → model (the only thing pinned)
skills/dev-team/        THE contract any orchestrator agent must follow
skills/new-project/     mode-B create-a-new-app playbook + reference assets
skills/android-code/    coding-style law for developer subagents
skills/checks/          executable expertise (localization completeness, …)
skills/lessons.md       append-only surprises log (the team's memory)
```

The team repo can live anywhere; it is resolved by path, and projects are
resolved by name from `DEVTEAM_PROJECTS_HOME` (optional), the team's parent
folder, `~/AndroidStudioProjects`, then `~/KMPProjects` — absolute paths always
work. Two notes for a scheduled (launchd) setup: keep the team and its logs
outside macOS TCC-protected folders such as `~/Documents`, and never hard-code a
default project — the team is project-independent by construction.
