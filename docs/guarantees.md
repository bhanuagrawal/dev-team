# Guarantees — what code refuses, and the self-improvement duty

Loaded by: `devteam verify` (its refusal names the failing gate) · `devteam doctor`
· `devteam asks`. Nothing here is a reminder: each line names the command that
refuses.

8. **No automatic guards beyond compile/tests.** Frozen paths, release-knob
   diffing and dependency declaration were retired by the owner (2026-10-31).
   `verify` runs: team-generic, localization, brand, the diff-scoped **test
   gate** (every changed production file must be named by a test file in the
   same diff; a changed UI file must be named by a Compose/Espresso test with an
   assertion — a screenshot render does not count), release-note freshness (the
   note may not repeat the last shipped one), compile, unit tests, instrumented
   tests. `release` re-runs the test gate before the version bump, so skipping
   `verify` does not skip it. **100% unit and UI test coverage is enforced by
   code**: `config/coverage.json` and `team/coverage.py` set the floor at 100%;
   when coverage is below 100%, the planner (`team/research.py`) is code-gated
   to make raising coverage the **topmost priority** (`queue[0]`), rejecting any
   plan whose head does not target tests. Version/signing/SDK changes and new
   libraries are the reviewer's diff to catch — say so in the handover when a task
   touches them.
9. **Ask the owner only what no agent can decide** (freeze-worthy incidents,
   discard-vs-keep on their WIP, direction). Never ask about promotion — the
   owner triggers it with their own words; the agent never raises it. Whether an
   ask is even the owner's is **decided by code, not by the agent's judgement**:
   `team/asks.py` classifies every ask/flag against a rule table and the tools this
   machine actually has, and `record`, `doctor` and `alarm` print the verdict
   (`agent-tool: browser-control CAN do this …`). An ask a browser or desktop
   driver can complete is **agent work** — do it, don't relay it; only what the
   verdict names as owner-only (a policy act, a payment instrument, the owner's
   legal/identity answers, one 2FA tap) reaches them.
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

## Self-improvement & Code-Based Invariants (standing duty)

A surprise is closed by **code, not by a note.** When a run does something you did
not expect, fix the mechanism that allowed it, **in this repo**, in the same run —
generic, run-derived, provable (`devteam doctor <project>` still green). There is
no lessons log to append to and no prose file to remember: the record of a lesson
is the commit that made the failure impossible, and if it is not a commit it did
not happen. If the fix is too large or too risky for the run, write it into
`scripts/rules.json` as an `enforcible` rule (the backlog `docs-audit --rules`
prints) rather than into a narrative nobody re-reads. Team code names no project;
a project's quirks live in that project's `.pipeline/profile.json`.

**Avoid prose checklists for operational guarantees.** Markdown files (`AGENTS.md`)
are non-deterministic — agentic tools miss items when relying on prose reminders.
Never attempt to close a procedural gap by simply appending prose checklist items
here for an agent to "remember." Every operational requirement (nightly loop
installation, visual asset checks, SDK baselines, signing, verification) MUST be
**code-based**:
1. **Code-enforced execution:** The CLI or harness performs or automates the action.
2. **Code-evaluated verification:** Evaluated deterministically in `devteam doctor`,
   `devteam store-readiness`, and `devteam verify`. If the condition is not met,
   the command exits non-zero and blocks progress mechanically.

## Where working guidance lives — and where it does NOT

This file, and the files under `skills/`, are the ONLY place an agent learns how
to work on a project. Project repos carry **no dev-team block, marker or agent
file** added by the team: their own `AGENTS.md`/`CLAUDE.md` belong to the project
and are left exactly as the owner wrote them. If an agent needs the procedure, it
reads it here — one source, no copies to drift.

NEXT: a surprise is closed by a commit in this repo, not a note — or filed in
`scripts/rules.json` as `enforcible` (`devteam docs-audit --rules`).
