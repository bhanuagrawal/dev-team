# Guarantees — what code refuses, and the self-improvement duty

Loaded by: `devteam verify` (its refusal names the failing gate) · `devteam doctor`
· `devteam asks`. Nothing here is a reminder: each line names the command that
refuses.

8. **No automatic guards beyond compile/tests — plus the one release audit.**
   Frozen paths, release-knob diffing and dependency declaration were retired by
   the owner (2026-10-31).
   **THE CHANGE YOU MADE MUST HAVE BEEN RUN — walking every journey on every
   build is not the rule** (owner rule 2026-11-20: "jo banaya hai usko emulator
   pe test karo, fir release"; narrowed 2026-11-21: "wo ux flow run wala rule
   hata do … current change jo kiya hai wo emulator pe run karne wala laga do").
   `verify` and `release` both run this gate, and it asks about the diff: the
   newest recorded walk must be newer than the newest code file the diff
   touched, and that walk must have REACHED ITS GOAL with no crash (a walk that
   stopped at a wall is a finding, not a passing audit). A journey whose screen
   this diff touches is NOT required to be re-walked on its own, and a journey
   last walked on an older build is no longer a refusal: the walk that watched
   the change is the evidence. The gate is a clock, not a commit id (owner,
   2026-11-21): a rebase or a merge rewrites every commit without changing a
   pixel anyone watched. **Paperwork is not a screen**: the declared
   `version_file` and `whatsnew_file` are excluded from "code" — `release` bumps
   and rewrites them itself after the walk it already has, so re-releasing is not
   refused for a version string nobody could have watched (2026-11-22: rc.3 was
   refused because its own `versionName` was newer than the walk). A walk is
   still recorded with the tree's `HEAD`, so the
   handover can name the build a person actually opened. The rule exists because
   BookSwap shipped a search that no device had ever run: the gate re-walked a
   journey only when the diff touched a file its surface declared,
   `CoarseLocation.kt` belonged to no surface, and search was not a declared
   journey, so four green walks from older builds vouched for a build none of
   them had opened. **The walk runs against the real backend the app ships
   with** (owner, 2026-11-21: *"drop the mock server and use the real server and
   the test accounts"*), signing in with the audit account in
   `.pipeline/audit-account.json` (gitignored; `DEVTEAM_AUDIT_EMAIL` /
   `DEVTEAM_AUDIT_PASSWORD` win when set) — never a frozen fixture. The fixture
   is gone because it lied twice: a WKT `location` where Postgres returns hex
   EWKB, and a bare object where `/api/v1/profile` returns a one-row list, so
   the app's profile read failed and its invented Bangalore fallback was what
   every earlier walk actually searched with (2026-11-20); and it answered a
   one-shot completed task with no plan while production streams
   `statusUpdate`s, which is why a walk "passed" the progress-card bug
   (2026-11-21). A fixture in a spelling or a shape production never sends is a
   fixture that hides the bug. There is no excuse field —
   `"flow_impact": "none"` was the loophole that let a release ship with unwalked
   journeys, and it is gone. **A walk only counts if it SAW the change** (owner,
   2026-11-21: *"it should verify the change somehow … that was broken last
   time"*): the task spec declares `walk.observables` — words that can only be on
   screen if this diff runs — and may pin the journey with `walk.flows`; the walk
   records what every step displayed, straight from the accessibility tree, and
   `verify` refuses when none of the declared words ever appeared. That is the
   hole the progress card fell through: a green `search` walk was accepted as
   evidence for a diff that only runs inside the feed's A2A turn, so a phone that
   showed nothing until the feed landed was "tested" by a journey which never
   draws the card. `devteam ux-flow` exits non-zero on a journey that
   did not reach its goal, and `doctor` warns about journeys whose last walk
   predates their screen — advisory, because the gate only needs the change
   walked. `ux-audit` stills, `ux-video` replays, storefront review and
   design drift are **advisory**: they inform the run, they never gate a release.
   **THE SCREEN IS NOT THE SERVER** (2026-11-22). A walk proves what the app drew,
   and an app draws what the deployed backend sent: 1.0.89-rc.2 shipped a deal
   panel whose feedback routes did not exist on the live service and whose offer
   POST died on a dropped column, because the deal page renders Waiting/Agreed/
   Done from fields the OLD server already sent — every declared observable
   appeared without one new endpoint being touched. So a task may declare
   `backend.requires` (`routes` and signed-in `calls` with required keys) in its
   spec, and `verify` probes the deployed service: its own OpenAPI document for a
   route, one real authenticated response for a key. A missing route, an old
   shape or an unreachable service refuses, and the refusal names the remedy
   (`merge the backend to main, apply its migrations, deploy`). The walk still
   proves the screen; the probe proves the server behind it. No backend block, no
   probe — a change that needs nothing from a server is not asked about one.
   **No UI code without a design** (owner directive 2026-11-10): `verify` also
   refuses a UI diff whose task has no recorded design answer. Find it or get it
   created — a surface already in `docs/stitch/manifest.json` with an exported
   snapshot is reused or tweaked; a surface with none is answered by
   `devteam design-consult … --record`, and a `new_screen` answer must have its
   snapshot exported in the same change. Building a screen from tokens alone is
   what the gate refuses. A **browser client's `web/src/**/*.tsx`** is UI in the
   same sense (`.ts` modules are logic, `web/tests/` is a test): the gate used to
   know only `.kt`/`.java` and answered "no UI file changed" to a diff that
   rewrote the whole website (BookSwap, 2026-11-19).
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
   **A PUBLISHED PAGE TREE IS A SURFACE THE LAW MUST KNOW** (owner ask 2026-11-20).
   The law gate is no longer only `doctor`'s: `verify` runs
   `law.diff_gate` on every diff, and it refuses two things. (a) A diff that
   writes a tracked `index.html` tree that no `profile.law_bindings` glob names —
   the pages a reader opens are the product's most public claim, so the change
   that publishes a surface is the change that must name it. (b) A diff that
   changes a named surface without a clause bound to that surface being cited
   *in a non-prose file under the same glob* — a citation in a `.md` describes the
   law, it does not obey it, and a citation in `web/src` cannot vouch for
   `docs/**`. Coverage is therefore checked per glob, and a generator emits the
   marker into the pages it writes (`<!-- Law: P3, P13 -->`). Why: BookSwap's site
   was generated from a table `P13` had retired two days earlier and shipped "a
   city and a radius" and "books already on shelves in Bangalore" while the app,
   the browser client **and** a doc in `docs/` all cited `P13` — three green
   citations of a clause the fourth surface contradicted in public. `devteam law`
   exits 1 on an unnamed page tree or a prose-only binding, and `doctor` warns
   about both before a run reaches them.
   **THE LAW IS DECLARED, AND A CONFLICT STOPS THE RUN** (owner decision
   2026-11-22: *"I want to have this for all the apps managed by dev team and any
   new app"*). Declaring `PRODUCT.md` is mandatory; the only other legal state is
   the owner's waiver, recorded in `profile.json → law_waiver` with their words
   (`devteam law <p> --waive --requested-by "…"`). `doctor` BLOCKS an app with
   neither. `admit --law Pn --effect obeys|amends|contradicts` requires the agent
   to say what the request does to the clause, not just name one: a contradiction
   queues **nothing** and asks the owner with the clause text in front of them,
   because breaking a clause is a product decision, not a plan-alignment call.
   The planner reads the law and may not queue an `amends`/`contradicts` entry —
   an amendment is the owner's act (`devteam law --amend Pn --text … --requested-by
   …`) and lands with the code that cites the new sentence. `pick` refuses (rc 5)
   a queue entry that names no clause, so the bypass that let planned work skip
   the law is closed at the last gate before code is written. Why: the whole
   apparatus above was optional and after-the-fact — only one of six managed apps
   declared a law, the plan prompt never mentioned it, and the queue could enter
   from a path (`plan`) that no law gate watched.
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
   owner triggers it with their own words, arms a per-app provision once
   (`devteam autopromote --on`) that the nightly executes, or relies on the
   team-wide weekly rule they recorded on 2026-11-24 (`config/promote.json`:
   production older than 7 days → newest release candidate goes out, read LIVE
   from Play and never from `state.json`); the agent never
   raises any of them. Whether an
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
11. **A number the plan runs on is DECLARED and then MEASURED** (`devteam
   analytics <project>`, 2026-11-22). The planner decides what the product
   becomes from the events the app records, so an app must declare
   `profile.analytics` — which event is its activation, its completion, the
   optional surfaces whose usage share argues for re-prioritising one over
   another, and (when the tree carries a billing path) the paywall-shown and
   purchase-completed halves of the money funnel. The gate checks the
   declaration against two independent facts: the **delivery ref** (does the
   shipped code name the event at all — and does anything actually pass that
   name to a send? a `const val WISH_CREATED = "wish_created"` nothing calls is
   a name, not an event, which is how BookSwap's two core acts read as zeros for
   30 days) and the **property's own 30-day event table** (did a real user record
   it?). A declared event the tree no longer sends, a declared event the tree
   names but never emits, a declared event with 0 users beside declared events
   that have them, and a billing path with no purchase funnel are each BLOCKED —
   because a 0 the app never recorded and a 0 that means "nobody does this" are
   different facts, and only the app can tell them apart. The declaration is also
   held against the app's **own law**: a project that ships a tracker under a
   clause saying "no telemetry … nothing leaves the device" is BLOCKED for the
   owner to settle (amend the clause, or drop the tracker) — a run may not add
   another event under a promise that forbids it. `mode: none` is a
   first-class answer: an app whose product law forbids analytics (FieldCert P2)
   declares the clause, and the gate then checks the ABSENCE — no SDK, no
   tracker, no connected property. `devteam analytics --draft` proposes a
   declaration from the events the property already records; `research` turns
   every blocker into one queue upgrade, so the gap is planned rather than
   remembered. Notes that are never blockers but always printed: the
   activation→completion drop-off with its numbers, declared events that are
   silent while their siblings land, the usage share across declared features,
   and a renamed event whose old spelling still carries more users than the
   declared one.

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
