# The loop — how a run goes

Loaded by: `devteam tools` · `devteam status` · `devteam takeover`.
Read this when you are about to run work, not when you are choosing it.

**Changed something outside the pipeline?** (a file in the checkout, a commit
pushed by hand, a design doc, a screen) — run `devteam replan <project>`. It
detects what moved, **adopts it (never reverts the owner's work)**, re-shapes the
project and re-derives the queue, then stamps the delivery sha the new plan is
built from. `devteam status` prints `plan base:`; when main has moved past it,
that line names `replan` — a plan is never followed against a main it was not
built from. Nothing is deleted: every adoption is logged in `state.adoptions` and
every queue verdict in `state.queue_events`.

**A team change to the plan format is the same problem** (2026-11-09: a task is a
mini-PRD — `problem`, `done_when`, `user_sees`, `not_in_scope`). Each app picks it
up on its next run — `adapt` at step 0 is deterministic, and old queue entries are
grandfathered so the rule cannot stall a run it did not plan. To do it now instead
of waiting for three nightly slots per app: `devteam replan --all --dry-run` shows
every app, what it would adopt and whether its tasks are still on the older shape;
drop `--dry-run` to sweep for real (`--no-llm` = shape only, the planner finishes
next run).

## The one loop (any agent, any app)

```bash
cd ~/dev-team
python3 cli.py tools                  # ONE PASS: every verb, what it writes, what it refuses
python3 cli.py status  <project>      # queue, delivery state, goal
python3 cli.py adapt   <project>     # make the project match the current team
python3 cli.py admit   <project> --query "<the owner's words>" \
        --kind <kind> --advances <milestone> --why "<one line>" --verdict aligned
#   → OK: pinned at the HEAD of the queue (execute it now — it is top priority).
#   → FLAG: nothing queued — reply to whoever the block names and STOP.
python3 cli.py asks    <project>     # review every open ask/flag: can a machine do it?
python3 cli.py plan    <project>     # planner maintains the queue (runs every cycle)
python3 cli.py pick    <project>     # the queue head → task spec + worktree command
python3 cli.py worktree-start <project> --task-id <id>
python3 cli.py developer-prompt <project> .pipeline/tasks/<id>.json --worktree <wt>
#   → prints a POINTER (WORKTREE_PATH + TASK_SPEC). Hand it to the coding tool
#     as-is: the spec file carries the goal, conventions, build/test commands and
#     the test contract. The tool reads the project and implements in its own
#     context. There is no file allow-list — the agent owns the whole project.
python3 cli.py verify  <project> --worktree <wt> --task-spec .pipeline/tasks/<id>.json
python3 cli.py release <project> --worktree <wt> --task-spec .pipeline/tasks/<id>.json
python3 cli.py record  <project> --result shipped --task-spec <spec> --version <v> --commit <sha> --note "<one line>"
python3 cli.py rc      <project> --version <v> --yes|--no --note "<one line>"
python3 cli.py alarm   <project>
```

**The team is a toolbox; you are the resolver.** `devteam tools` lists every verb
(what it writes, what it refuses) — read it once, then decide the sequence. There
is no route table to look for. `devteam admit` is the only way a request becomes
work: **OK** → pinned at the head of the queue (top priority, execute now);
**FLAG** → nothing queued; reply to whoever the block names and stop.

Report the outcome banner with the `RELEASE HANDOVER` block (store note + owner
checklist + release-candidate call) and the two page URLs `record` prints.

## What lands where — and what the owner is never asked to hold

The trigger for `main` is **the gates passed**, not "a human is about to look".

| state of the work | where it goes | human needed? |
|---|---|---|
| finished, `verify` PASS | `main` (project) / a commit on `main` (this repo) | no |
| stopped mid-flight (verify FAIL, blocker) | committed + pushed to `dt/<task-id>`, tree pruned, attempt listed as `⏸ parked` | no |
| stopped for an owner ask | same as above, **plus** `record --result blocked --note "<the question>"` | yes — the ask itself |

Nothing about a finished change needs a human in the loop; the human only enters
for a launch they choose to trigger (`devteam promote`, never raised by an agent),
a `risk: supervised` task, a missing direction, or a blocker. Never leave a run
paused with a dirty worktree: commit, push, then ask. A differential that only
exists on this disk is not work, it is a rumour.

**A command that writes inside a project must end with a commit on a pushed ref.**
That includes ad-hoc commands (asset regeneration, harness scaffolding, listing
edits), not just the release loop — they are the ones that used to leave tracked
files modified in the checkout with no commit behind them. A store upload must
describe a commit: `store-publish` refuses a dirty asset tree rather than putting
frames on Play that exist in no revision.

**Generated artefacts are deliverables, and a batch of work is not one release.**
A Stitch screen export (`docs/stitch/**`), a regenerated store asset, or a report
page is committed in the same turn it is produced — an ignored path in the commit
command is a failed commit, not a finished one; read back `git log` on the remote.
When a run builds a set of screens on one branch and deliberately does **not**
release it yet, every finished screen is still committed and pushed to
`dt/<task-id>`, and the report names that ref and its commit. "It is on the
branch, not on `main`" is a complete answer only when the branch is at origin and
the report says so; an unreleased branch is not an uncommitted one.

**Cross-repo changes land in dependency order.** A change that touches both this
repo and a project: (1) commit and push the team-general fix here, (2) regenerate
the project's CI from the template (`devteam ci <project> --write`), (3) release
the project. The project's `release.yml` is generated by the template, so landing
the project side first ships CI that no longer matches its generator — and the
next `ci --write` silently rewrites it.

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

## Done means

`verify` PASS (which includes the mandatory **ux-flow audit**: every declared
journey walked on this diff and reaching its goal — no other audit gates a
release, owner policy 2026-11-10) → `release` delivered (CI green, internal
testing updated) → `record` + `rc` + `alarm` printed → worktrees pruned → the report quotes the
handover, the flags and the page URLs. Nothing sits uncommitted anywhere:
`devteam doctor <project>` prints no `uncommitted: WARN` line, and `status`
shows no `⏸ parked` attempt the report failed to mention.

NEXT: `devteam admit <project> --query "<the owner's words>" ...` — a request becomes
work only there. Refused? `docs/planning.md`.
