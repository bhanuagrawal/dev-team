# Develop — the only instruction a coding agent gets

Loaded by: `worktree-start` (its `DEVELOP_SKILL=` line) · `pick` (`develop_skill`) ·
`references/nightly-run.md` step 6.

There is no prompt and no template. Two things define this task: the spec file
(`TASK_SPEC`) and this page. Everything else is a gate, and the gates are code.

## Where you are

You are in a throwaway tree at `WORKTREE_PATH`, cut from `origin/main` on branch
`dt/<task-id>`. Nothing you do here is the record: `devteam release` is what puts
work on `main`, and `origin/dt/<task-id>` is where an unfinished attempt is parked.

Work **in that tree** for everything — edits, `git`, greps, builds, relative paths.
The project checkout is the owner's and is not yours, even when it is the same app.

## What you are building

Read `TASK_SPEC` first. It is data, not advice:

- `problem`, `done_when`, `user_sees`, `not_in_scope` — what this task is for and
  where it stops. `done_when` is the acceptance bar; nothing else widens scope.
- `surface.targets` — where to look first.
- `context.goal` — the milestone and its outcome. Serve it with a smaller change
  rather than a bigger one that also "helps".
- `context.conventions` — files to read before writing code (style law, project
  idioms, architecture). A path marked `SKIP: not found` does not exist; do not
  hunt for it.
- `context.build.compile` — the command that must be green when you finish.
- `context.design` — whether this task needs a design answer, which Stitch screen
  already covers the surface, and how to get one if it does not. UI written with
  no recorded answer is refused by the gate.
- `context.journeys` — the user flows that must still work afterwards.

Match the nearest existing feature: state holder, DI, navigation, error handling,
design tokens. Copy the project's idiom; do not introduce a second one.

## Hard rules

1. **Do not commit, do not push, do not bump a version.** Leave the work
   uncommitted in the tree; the pipeline commits after its own gates pass.
2. **Never change a pin, wrapper, SDK level or plugin to work around a problem
   that only exists in your local environment.** A missing build cache is not a
   reason to move the project. Report it instead.
3. **Retire what you replace.** A screen or route this task replaces is removed in
   the same change; leaving the old surface alive beside the new one is an
   incomplete task, not a cautious one.
4. **A mockup's copy is not data.** Numbers, ranks, badges and names in a design
   are placeholders: render values the app actually holds, or drop the element.
   An invented statistic shipped as if it were recorded is a truth defect.
5. **Never stop an emulator you did not start.** If you need one, start it through
   `with-emulator <avd> -- <command...>` (teardown is guaranteed by an EXIT trap);
   if you start one by hand, stop it before you finish.
6. **Do not widen the diff.** No drive-by reformatting, no unrelated renames, no
   "while I was here" refactors. The reviewer reads the diff, not your intent.

## Done

- `context.build.compile` runs green, in this tree.
- Every journey in `context.journeys` can still be walked on this change.
- For a UI task: the change matches the exported screen in
  `docs/stitch/screens/<id>/` (layout, type, spacing, imagery), not a token
  description of it.
- Then stop. `devteam verify` decides — run it, read the refusal, fix it, up to
  two iterations.

## Stuck

If the task cannot be done — a missing design answer, a credential, a device that
will not boot, a contradiction in the spec — write `PIPELINE_BLOCKER.md` in the
tree root with the single question a human must answer, and stop without guessing.
A blocker is a valid outcome; an invented workaround is not.

## Report

When you finish, say what changed and what you could not do. Files touched, the
verify result, anything the owner has to know. A few lines, no essay.

NEXT: `devteam verify <project> --worktree <WORKTREE_PATH> --task-spec <TASK_SPEC>`
