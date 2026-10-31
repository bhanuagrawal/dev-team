# dev-team — working guidelines

This repo IS the pipeline. Every app on this machine is driven from here, by
whichever agent you are (OpenCode, Antigravity, headless). Read this before
touching anything; it is the contract, not a suggestion.

## What this repo is

`cli.py` + `team/` = the orchestrator: plan → pick → worktree → develop →
verify → release → record. Project state lives in each project's `.pipeline/`,
never here. **Team code carries zero project names** — a project's quirks live in
that project's `.pipeline/profile.json`. `devteam verify` enforces this on every
release, whichever agent runs it.

## The one loop (any agent, any app)

```bash
cd ~/dev-team
python3 cli.py adapt   <project>     # make the project match the current team
python3 cli.py status  <project>     # queue, delivery state, goal
python3 cli.py plan    <project>     # planner maintains the queue (runs every cycle)
python3 cli.py pick    <project>     # the queue head → task spec + worktree command
python3 cli.py worktree-start <project> --task-id <id>
python3 cli.py developer-prompt <project> .pipeline/tasks/<id>.json
#   … develop ONLY inside the printed WORKTREE_PATH, only under the task's allow-list
python3 cli.py verify  <project> --worktree <wt> --task-spec .pipeline/tasks/<id>.json
python3 cli.py release <project> --worktree <wt> --task-spec .pipeline/tasks/<id>.json
python3 cli.py record  <project> --result shipped --task-spec <spec> --version <v> --commit <sha> --note "<one line>"
python3 cli.py rc      <project> --version <v> --yes|--no --note "<one line>"
python3 cli.py alarm   <project>
```

Report the outcome banner with the `RELEASE HANDOVER` block (store note + owner
checklist + release-candidate call) and the two page URLs `record` prints.

## Rules that hold for every agent

1. **Never edit project code on `main`.** Every task runs in
   `.pipeline/worktrees/<task-id>` on branch `dt/<task-id>`, cut from
   `origin/main`. `release` pushes that branch onto `main`. The branch is pushed
   to origin **at creation** (`worktree-start`), not at release — from that
   moment the tree is a cache and any run may delete it without losing work.
2. **Read project code from the delivery ref, not the checkout.** `origin/main`
   is what CI ships; the checkout drifts (seen: 68 commits behind, twice producing
   a confident wrong answer). `devteam status` prints `DELIVERY:` on every run.
3. **Never hand-edit `.pipeline/state.json`** — write via `devteam record`.
4. **One canonical CI.** `.github/workflows/release.yml` is generated from
   `team/templates/ci-release.yml` by `devteam ci <project> --write`. Never
   hand-edit a project's copy; never add a production-promotion workflow.
   Production promotion is human-only, always.
5. **Store assets are deliverables.** Screenshots come from the app's own
   composables (`devteam storefront-scenes`), never from hand-drawn HTML. A
   generated artefact a user reads (store note, screenshots, release text) needs
   the consumer's constraints and an owner who can overwrite it.
6. **Cleanup is part of the run.** `devteam pick` and `record` prune
   automatically; `devteam worktree-prune <project>` is the manual form (dry run
   by default). A tree is removed when its work is safe **somewhere else**: its
   commits landed on `origin/main`, or (for an unfinished attempt) they were
   committed and pushed to `dt/<task-id>` first — the branch is kept, the tree
   goes. Uncommitted work is parked, never discarded; `--force` is the only way
   to drop it and is never implied. After a run, a repo should hold `main` +
   `.pipeline/scan` + `.pipeline/publish` worktrees only. A tree locked by a run
   (`worktree-start` locks it) is left alone while it looks live — locked and
   touched inside the settle window (`DEVTEAM_WORKTREE_SETTLE_MIN`, 30 min) — so
   a dead session is reclaimed by the next `pick`, not by the owner's memory.
7. **A worktree is never the record of anything.** Work is closed when `main`
   contains it or `origin` holds its branch. An unfinished attempt lives as a
   parked branch (`dt/<task-id>`, or `dt/parked/<task-id>-<sha>` when a re-run
   needs the name back), is listed by `devteam status` as `⏸ parked`, and is
   resumed by checking that branch out into a worktree — never by hoping a
   directory survived. `record --result blocked` before any owner ask: the ask
   names the branch.
8. **No automatic guards beyond compile/tests.** Frozen paths, release-knob
   diffing and dependency declaration were retired by the owner (2026-10-31).
   `verify` runs: team-generic, localization, compile, unit tests, instrumented
   tests. Version/signing/SDK changes and new libraries are the reviewer's diff to
   catch — say so in the handover when a task touches them.
9. **Ask the owner only what no agent can decide** (freeze-worthy incidents,
   discard-vs-keep on their WIP, direction). Never ask about promotion.
10. **Nothing remains uncommitted, ever.** Every change an agent makes ends in a
   commit on a pushed ref before its turn ends: a project change on `main` (once
   the gates pass) or on its `dt/<task-id>` branch; a team change on this repo's
   `main`. "Clean tree" is not the test — **"does this exist anywhere but this
   disk"** is. `devteam doctor` / `status` report every checkout file whose work
   exists nowhere else (`core.checkout_dirty`, which excludes only the plan
   mirror when origin already serves the identical bytes). Never leave a dirty
   worktree, an unpushed commit, or "I'll commit later": the next session may be
   a different tool that never saw this one. Work you did not write — the
   owner's WIP — is reported, never committed on their behalf.

## What lands where — and what the owner is never asked to hold

The trigger for `main` is **the gates passed**, not "a human is about to look".

| state of the work | where it goes | human needed? |
|---|---|---|
| finished, `verify` PASS | `main` (project) / a commit on `main` (this repo) | no |
| stopped mid-flight (verify FAIL, blocker) | committed + pushed to `dt/<task-id>`, tree pruned, attempt listed as `⏸ parked` | no |
| stopped for an owner ask | same as above, **plus** `record --result blocked --note "<the question>"` | yes — the ask itself |

Nothing about a finished change needs a human in the loop; the human only enters
for promotion (never agent-run), a `risk: supervised` task, a missing direction,
or a blocker. Never leave a run paused with a dirty worktree: commit, push, then
ask. A differential that only exists on this disk is not work, it is a rumour.

**A command that writes inside a project must end with a commit on a pushed ref.**
That includes ad-hoc commands (asset regeneration, harness scaffolding, listing
edits), not just the release loop — they are the ones that used to leave tracked
files modified in the checkout with no commit behind them. A store upload must
describe a commit: `store-publish` refuses a dirty asset tree rather than putting
frames on Play that exist in no revision.

**Cross-repo changes land in dependency order.** A change that touches both this
repo and a project: (1) commit and push the team-general fix here, (2) regenerate
the project's CI from the template (`devteam ci <project> --write`), (3) release
the project. The project's `release.yml` is generated by the template, so landing
the project side first ships CI that no longer matches its generator — and the
next `ci --write` silently rewrites it.

## Self-improvement (standing duty)

After any surprising run, append to `skills/lessons.md` (append-only) and, when
the lesson yields a durable fix, apply it **in this repo** — generic, run-derived,
and provable (`devteam doctor <project>` still green). `skills/lessons.md` is the
only file allowed to name a project.

## Where working guidance lives — and where it does NOT

This file, and the files under `skills/`, are the ONLY place an agent learns how
to work on a project. Project repos carry **no dev-team block, marker or agent
file** added by the team: their own `AGENTS.md`/`CLAUDE.md` belong to the project
and are left exactly as the owner wrote them. If an agent needs the procedure, it
reads it here — one source, no copies to drift.

## Where things live

| what | where |
|---|---|
| team code | `~/dev-team/{cli.py,team/,skills/,prompts/,config/}` |
| canonical CI | `team/templates/ci-release.yml` |
| project state | `<project>/.pipeline/{profile,state}.json` |
| task specs | `<project>/.pipeline/tasks/<id>.json` |
| run logs | `<project>/.pipeline/runs/` |
| handover copy | `<project>/.pipeline/runs/<date>-handover-v<version>.md` |
| store scene spec | `<project>/docs/storefront.json` |
| rendered scenes | `<project>/.pipeline/storefront_scenes/` |

## Done means

`verify` PASS → `release` delivered (CI green, internal testing updated) →
`record` + `rc` + `alarm` printed → worktrees pruned → the report quotes the
handover, the flags and the page URLs. Nothing sits uncommitted anywhere:
`devteam doctor <project>` prints no `uncommitted: WARN` line, and `status`
shows no `⏸ parked` attempt the report failed to mention.
