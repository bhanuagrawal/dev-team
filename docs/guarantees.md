# Guarantees — what code refuses, and the self-improvement duty

Loaded by: `devteam verify` (its refusal names the failing gate) · `devteam doctor`
· `devteam asks`. Nothing here is a reminder: each line names the command that
refuses.

8. **No automatic guards beyond compile/tests — plus the one release audit.**
   Frozen paths, release-knob diffing and dependency declaration were retired by
   the owner (2026-10-31).
   **The UX-FLOW AUDIT IS MANDATORY FOR RELEASE; NO OTHER AUDIT IS** (owner
   policy 2026-11-10). `verify` and `release` both run it: every journey declared
   in the project's `docs/ux-flows.json` must have been walked on this diff and
   must have REACHED ITS GOAL (a walk that stopped at a wall is a finding, not a
   passing audit), with no crash on the walk. There is no excuse field —
   `"flow_impact": "none"` was the loophole that let a release ship with unwalked
   journeys, and it is gone. `devteam ux-flow` exits non-zero on a journey that
   did not reach its goal, and `doctor` reports the audit as BLOCKED until every
   walk passes. `ux-audit` stills, `ux-video` replays, storefront review and
   design drift are **advisory**: they inform the run, they never gate a release.
   **No UI code without a design** (owner directive 2026-11-10): `verify` also
   refuses a UI diff whose task has no recorded design answer. Find it or get it
   created — a surface already in `docs/stitch/manifest.json` with an exported
   snapshot is reused or tweaked; a surface with none is answered by
   `devteam design-consult … --record`, and a `new_screen` answer must have its
   snapshot exported in the same change. Building a screen from tokens alone is
   what the gate refuses.
   **EVERY BUILDABLE UNIT HAS A GATE** (owner directive 2026-11-18). The
   inventory is DERIVED from the tree (`team/platforms.py`), never hand-listed: a
   tracked directory carrying `settings.gradle*`, `package.json`, `Package.swift`,
   `*.xcodeproj`, `Cargo.toml`, `go.mod`, `pubspec.yaml` or `pyproject.toml` is a
   platform. `verify` REFUSES a diff that touches a platform no gate builds, and
   the refusal names the exact `verify.tasks` entry to add; `doctor` reports an
   ungated unit as a WARN, so the gap is visible before a run reaches it. A gate
   covers the unit it runs in and nothing below it — the root Gradle task never
   covers a nested client. A project with one Gradle task is unchanged:
   `verify.compile` is still that one gate. Why: BookSwap's browser client (`web/`,
   its own `package.json`, its own `tsc --noEmit && vite build`) merged with a
   hand-copy of a product rule while `verify` ran Gradle alone and CI had no node
   step — a shared rule could change, PASS, ship, and leave the second client on
   the old copy.
   **A revamp is counted in the stack it ships.** The revamp gate asks a
   ground-up rebuild to rewrite application source, and "application source"
   follows the project's own platforms: Kotlin/Java for the app, and `web/src`'s
   `.ts/.tsx/.js/.jsx` for a browser client. Test files (`*Test.kt`, `*.test.ts`,
   anything under `web/tests/`) and generated output do not count. Why:
   BookSwap's 2026-11-19 website redesign replaced the whole browser client and
   the gate refused it with "0 code file changes" because no `.kt` moved — a
   stack-blind gate refusing a real rewrite.
   **TESTS ARE RETIRED — ALL OF THEM** (owner policy 2026-11-10, restated as
   "get rid of them completely"). There is no unit-test gate, no UI-test gate, no
   test run in `verify` and no coverage floor: `config/coverage.json` carries
   0/0 and a raised floor would re-introduce a gate the owner removed. A change
   is proven by (a) the **compile gate**, (b) the **ux-flow audit** for a screen,
   and (c) the store/design gates for what a reader is shown. The old diff-scoped
   UI-test requirement is gone with the suites themselves; `team/code_tests.py`
   now only classifies a diff as UI or not, which the design gate needs.
   **One exception, and it is not a test:** a project may keep a scene RENDERER
   (the generated `StorefrontScreenshotsTest` that `devteam storefront-preview`
   writes) because a listing frame needs a composable rendered to a bitmap. It
   asserts nothing, is never required by a gate and is never run by `verify`.
   `verify` also runs: team-generic, localization, brand, release-note consumer copy
   (the note may not repeat the last shipped one, and Google Play What's New must
   describe consumer benefit without technical engineering vocabulary; detailed
   changelogs belong in tester notes for Firebase App Distribution), the design gate,
   and compile.
   **A store picture may not describe an older build** (owner ask 2026-11-11):
   `verify` runs `team/storefront_frames.listing_freshness` on every task, and
   `store-publish` / `store-readiness` / `storefront-status` read the same
   function. `docs/storefront-frames.json` records what the shipped frames were
   drawn from — the ref, the last commit that touched the module's sources, the
   versionName, the scene-spec fingerprint, and a sha256 + size per PNG — and the
   gate refuses when any of those moved without a re-render, naming the file and
   `storefront-scenes <project> --render --frame` as the remedy. The versionName
   is recorded too, but a version-only move is reported, not refused: `release`
   bumps it on every ship and a screenshot shows screens, not a number — the
   frames describe an older build when the module's sources, the scene copy or
   the recorded bytes moved. In `verify` it
   refuses the diff that draws a picture (a screen, the scene copy, a frame, the
   listing) without re-recording it; a diff that draws nothing is not charged for
   a re-render it never needed. `store-publish` and `store-readiness` read it
   unconditionally, so staleness is never silenced — it just cannot reach Play.
   A project with no `listing` block is grandfathered, so the gate never invents
   work for a listing nobody recorded.
   Version/signing/SDK changes and new libraries are the reviewer's diff to
   catch — say so in the handover when a task touches them.
9. **Ask the owner only what no agent can decide** (freeze-worthy incidents,
   discard-vs-keep on their WIP, direction). Never ask about promotion — the
   owner triggers it with their own words, or arms a per-app provision once
   (`devteam autopromote --on`) that the nightly executes; the agent never
   raises either. Whether an
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
