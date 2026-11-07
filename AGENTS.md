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
python3 cli.py asks    <project>     # review every open ask/flag: can a machine do it?
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

### Planning has four tiers — and code owns the shape

`vision` (owner, one sentence) → `goals` (the **ends**: a great experience, users
stay, users pay — each with one number) → `milestones` (the **means**: a feature
or outcome) → `tasks` (the queue). A milestone is never added directly under the
vision, and work filed under a later goal is refused while an earlier goal's
number fails — so "keep users before you sell to them" is a gate, not a
reminder. `devteam adapt` inserts/re-tiers the tree deterministically on the
next run of any app (no LLM, no owner ask, every move recorded for review),
`devteam doctor` blocks a flat tree, and the planner may move any branch below
the vision (`goal_changes`). A new app gets the three goals as soon as its
vision exists, so its first plan files milestones under ends from the start.

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
   hand-edit a project's copy; CI never reaches production.
   **Production promotion is a verb, not a side effect (owner directive
   2026-11-05).** It is `devteam promote <project> --version <v> --requested-by
   "<the owner's words>" --yes` — run only when the owner has explicitly asked
   for that launch in this conversation, never by `release`, `record`, `loop`,
   the nightly, or CI. The command refuses a version that is not already on an
   internal track, records the requesting words, the note and the Play edit id in
   `.pipeline/state.json`, and sends the release for Google's review (which no
   agent can skip). Google's review is the remaining gate — nothing else is.
5. **Store assets are deliverables.** Screenshots come from the app's own
   composables (`devteam storefront-scenes`), never from hand-drawn HTML. A
   generated artefact a user reads (store note, screenshots, release text) needs
   the consumer's constraints and an owner who can overwrite it. The real render is
   the **input** to the marketing image: `storefront-build --poster` attaches the
   scene's rendered screen and has **Gemini paint the whole asset in the browser,
   one prompt per scene** (`--poster`), then strips its watermark and upscales to a
   size Play accepts. The heal is strict: the corner the prompt keeps plain is
   re-checked as a contract, a frame whose corner still shows the model's mark is
   refused (never shipped with a warning), and the verdict is recorded per frame
   and re-read at publish. The prompt also asks the model to match the frames it
   has already painted in that chat — a soft instruction, not a gate. `--poster` is the DEFAULT of `storefront-build`: a
   code-composed frame (`--builtin`, `--gemini`/`--plate`, `--frame-html`,
   `--layout`, `--frame`) is a fallback and must carry `--why "<what must stay
   pixel-crisp>"`; the reason is recorded in `docs/storefront-frames.json` and
   `store-publish` refuses a listing whose frames are code-composed without one,
   or that has no recorded composition path at all (`storefront-build --plan`
   prints the path a run would take, without painting anything). A long pass keeps
   a run record (`.pipeline/storefront_run.json`: pid, step, verdict), so a run an
   agent backgrounded and abandoned reads as `interrupted at step '<x>'` rather
   than vanishing; `devteam storefront-status <project>` is how anyone — owner or
   orchestrator — checks what actually happened, and `store-publish` refuses an
   interrupted run outright. Never take an agent's word that a long command
   finished: read that record. The two other brand assets follow the same rule with
   different makers: the **icon is a Stitch design**, adopted from the artboard it
   returns (`storefront-brand --stitch-prompt` → `--adopt-stitch`), and the feature
   graphic is painted by Gemini from the hero screen. A listing whose icon or banner
   has no recorded provenance is refused, not graded. The code-rendered poster stays for work whose type must be
   pixel-crisp: Stitch (`--design-frame`/`--adopt-frame`), the vision model
   (`--vision-frame`), or a Gemini backdrop plate (`--gemini`/`--backdrop`) with
   every letter drawn by us. An image model repaints what it is shown, so a Gemini
   poster is a **depiction** of the app, never proof of it — say so, and never let
   a frame assert a feature or number the app does not have.
   A listing ships **at least four** phone screenshots (`storefront-build` refuses
   fewer): two cannot show what the app is, how it works, that it works, and who is
   in control — and the control frame is the one that went missing when three shipped.
   What the listing SAYS is decided before anything is drawn, in
   `docs/storefront-direction.json` (concept, per-frame argument, palette emphasis, evidence).
   Publishing a listing is a **browser** step, not an API call: the Play API uploads and
   commits an edit, but permissions ("Manage store presence") and **Managed publishing** are
   console-only, so `store-publish` ends in Play Console through `browser-control` and nothing
   is called live from an API answer.
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

**Cross-repo changes land in dependency order.** A change that touches both this
repo and a project: (1) commit and push the team-general fix here, (2) regenerate
the project's CI from the template (`devteam ci <project> --write`), (3) release
the project. The project's `release.yml` is generated by the template, so landing
the project side first ships CI that no longer matches its generator — and the
next `ci --write` silently rewrites it.

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
| report page (all apps, what changed / what to test) | `~/dev-team/site/` — `devteam site` builds it, `--serve` serves it, `--install` keeps it up at login |
| rendered scenes | `<project>/.pipeline/storefront_scenes/` |

## New App "In One Go" — Non-Negotiable Order & Hard Checklist

When building and shipping a brand-new app MVP in one go, the agent MUST execute these steps in strict sequence before returning to the owner that work is done:

1. **Discovery & Architecture:**
   - Draft `docs/PRD.md` with explicit, domain-specific features (no generic placeholders).
   - Perform competitive recon and write `docs/COMPETITION_RESEARCH.md` (minimum 5 competitors with pricing, weaknesses, and differentiation).
2. **Initial UI Design & Stitch MCP:**
   - Initial UI is designed via **Stitch MCP** (`generate_screen_from_text` or `create_design_system_from_design_md`), NEVER invented blindly.
   - Design tokens, typography, and color palette written into `docs/design.md`.
3. **Single Source of Truth Plan (`docs/plan.md`):**
   - `docs/plan.md` MUST be populated with a multi-milestone roadmap (Milestone 1 Core Loop, Milestones 2–5) with outcomes, measures, and the initial execution queue.
   - Run `devteam adapt <project>` to sync `docs/plan.md` into `profile.json` and `state.json`. `docs/plan.md` must never be left as an empty skeleton.
4. **Code Implementation:**
   - 100% native platform code (Compose / Kotlin).
   - Real offline persistence, real domain logic (no mocks, no fake static data).
   - All screens adhere to `docs/design.md` tokens.
5. **Storefront Brand & Marketing Posters (Rule 5):**
   - App Icon (512x512): Created by Stitch MCP (`storefront-brand --stitch-prompt` → adopt).
   - Feature Graphic (1024x500): Painted by Gemini from the hero render (`storefront-brand --generate`).
   - Phone Screenshots (minimum 4): Rendered from real app composables via `storefront-scenes` → framed by Gemini posters (`storefront-build`).
   - Zero procedural HTML mocks or cross-app template leakage. Critic score ≥ 90/100 [Grade A].
6. **Services & Telemetry:**
   - Firebase: Project created, SHA-1 and SHA-256 registered, `google-services.json` committed.
   - GA4: Property and Data Stream created, linked to app package.
   - Privacy Policy: `docs/privacy.html` committed and verified live on public HTTPS URL (HTTP 200).
7. **Store Distribution & Review:**
   - Build & sign AAB bundle via canonical CI (`release.yml`).
   - Upload AAB to Play Console internal testing track.
   - Fill Play Store listing copy and upload all 6 authentic visual assets (Icon, Feature Graphic, 4 Posters).
   - Save changes and submit for Google Play review ("Changes in review").
   - Obtain internal testing join link (`https://play.google.com/apps/internaltest/...`).
8. **Automated Sanity Gates (Code-Evaluated):**
   - `python3 cli.py store-readiness <project>` → PASS (evaluates Play compliance, SDK targets, assets, and active `launchd` nightly schedule).
   - `python3 cli.py doctor <project>` → PASS (evaluates orchestrator readiness, launchd registration, CI parity, and git tree clean).
   - Both git repos (`dev-team` and `<project>`) are 100% clean with all commits pushed to origin.

## Done means

`verify` PASS → `release` delivered (CI green, internal testing updated) →
`record` + `rc` + `alarm` printed → worktrees pruned → the report quotes the
handover, the flags and the page URLs. Nothing sits uncommitted anywhere:
`devteam doctor <project>` prints no `uncommitted: WARN` line, and `status`
shows no `⏸ parked` attempt the report failed to mention.
