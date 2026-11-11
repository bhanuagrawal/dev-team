# Reference — the nightly run, step by step

Loaded by: `dev-team` skill (Mode A), when a run actually starts.
This is the long form of the loop in `docs/loop.md` (team repo): the same
commands, in order, with the reasoning and the failure branches.

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
     workflow run"* — **and again 2026-11-05**: keep ONE entry per run, raise the
     entry's size instead):
     every feature/bugfix/investigation entry carries `effort_min` — minutes of
     developer work, target ~210, hard band 120-300, enforced in code (canonical
     values in `team/queue.py`; `team/research.py` renders the prompt from them). ONE RUN SHIPS
     ONE ENTRY, so a head smaller than the run costs the owner the release it paid
     for. The numbers are
     calibrated to what an AGENT session costs, not to human effort: measured here,
     a whole nightly (plan → developer → verify → release incl. the CI watch) runs
     ~12 min, and a developer session that shipped a 1305-line deletion took ~5 min
     (an `effort_min` : wall-clock ratio of ~6-10x, which is what bounds the 300
     ceiling — and `team/developer.py` caps one session at 80 min so an entry can
     never outlive the 90-min nightly watchdog).
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
    - **TEST COVERAGE IS A SIGNAL AND A GATE** (owner 2026-11-05). `signals.coverage`
      carries two numbers over the shipped tree: **unit** (share of non-UI main-source
      files a JVM test names; real JaCoCo LINE coverage replaces the proxy when a report
      exists) and **ui** (share of UI files a test mounts, counting Robolectric
      `src/test` and `src/androidTest` alike). Floors live in `config/coverage.json` and a
      project may override its own in `profile['coverage']`; `devteam coverage <project>`
      prints them and `status` shows the last measurement. The planner prompt states them,
      and **code enforces the priority**: while either leg is below its floor the queue
      HEAD must be the executable entry that raises it (its `target` names the test
      sources it will write) — a red CI or a store rejection is the only exemption. A
      goal milestone does not outrank an uncovered codebase.
    - **POLISH IS MEASURED, NOT CLAIMED** (owner 2026-11-05: *"don't dumbly keep adding
      features — polish a lot"*, and *"look and feel usually doesn't change in one run"*).
      So the unit is the SCREEN and the trigger is a fingerprint, never a per-release
      ritual: `devteam ux-plan <project>` compares each screen's **visual** hash (its
      composables + theme/tokens + Stitch manifest/tokens) and **behaviour** hash (the
      view-model/state/repository files that feed it) with the last audit.
      - neither changed → `unchanged`, cost zero — which is the normal case;
      - visual changed (new screen, new dialog/empty state) → audit **that screen**;
      - behaviour changed while pixels did not ("looks the same, feels different": a
        new wait, error state, disabled button) → re-walk that flow;
      - tokens/theme changed → every screen moved at once, so all are stale.
      `devteam ux-audit <project>` sends the app's OWN render (the scene rendered from
      its composables, no emulator) to one neutral evaluator that reacts in a user's
      words — one line per moment, then premium / annoying / verdict — and stores the
      gripes (`UX-<scene>-<n>`) in `.pipeline/ux_audit.json`. It is deliberately NOT a
      1-10 score. A polish entry should name the gripe it removes; re-auditing that
      screen marks gripes the new pass no longer mentions as `gone`. `verify` WARNS when
      a run's diff changes a screen nobody has reacted to since (gates stay
      compile/tests). A surface with no rendered scene reports `no render` — bind it in
      `docs/storefront.json` (`"surface": "<label>"`); it is never judged from a
      different screen's picture.
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
   - motion / bugfix / feature → targets from the queue entry; the task spec says
     what to build. There is no per-type template: one skill, one spec.
   - investigation → code-free task: read-only + docs, write findings to
     docs/progress/investigation-<slug>.md, then
     `record --result shipped --note "investigation: <finding>"`.
   - `risk: supervised` tasks require a human present — otherwise skip them.
4. Compose the task spec JSON (a mini-PRD, not a label):
   {"id": "dt-<label-slug>-<seq>", "type": "motion|bugfix|feature|investigation",
    "surface": {"label": "<label>", "targets": ["<repo-relative path(s)>"]},
    "risk": "safe", "effort_min": 210, "created": "<iso>",
    "problem": "<one line, the user's words: what is broken or missing>",
    "done_when": ["<acceptance line a test can assert>", "..."],
    "user_sees": "<optional>", "not_in_scope": "<optional>"}
   Write it to `.pipeline/tasks/<id>.json`. `effort_min` (from the picked queue
   entry) is the expected size of ONE developer session — the brief shows it, so
   the task is worked at its intended depth rather than gold-plated or rushed.
5. Prepare the sandbox (deterministic git plumbing is code, not you):
   `python3 <team>/cli.py worktree-start <project> --task-id <id>`
   → WORKTREE_PATH / WORKTREE_BRANCH / WORKTREE_BASE / DEVELOP_SKILL / TASK_SPEC.
   Read that output as the run's coordinates; nothing else tells you where to work.
   Then `python3 <team>/cli.py emulators <project> --record --reset` — record the
   start-of-run emulator baseline. `run-orchestrator` does this automatically, but
   a STEP-BY-STEP run (this numbered procedure) does not, and the run-end leak
   check trusts whatever baseline file is on disk: with none (or a stale one that
   predates the owner opening an emulator) `verify` classifies a device the owner
   started themselves as "leaked" and stops it, breaking the "never stop an
   emulator you did not start" rule. Recording the baseline here, before any
   device work, is what makes that check safe.
6. READ THE SKILL — THE ONLY INSTRUCTION (there is no prompt and no template):
   `DEVELOP_SKILL` from step 5 is `skills/dev-team/references/develop.md`. Read it
   in full, then read `TASK_SPEC`. The spec is data (`problem`, `done_when`,
   `user_sees`, `not_in_scope`, `surface.targets`, `context.goal`,
   `context.conventions`, `context.build`, `context.design`, `context.journeys`);
   the skill is procedure. Nothing else is written for the coding agent, because
   prose that restates a gate is what drifts.
7. DEVELOP THE TASK — YOU, IN THIS SESSION, IN `WORKTREE_PATH`:
    - Do not spawn a developer subagent. You are the one who ran `status`, `plan`
      and `pick`: the goal, the design answer and any previous `verify` failure
      are already in your context. A fresh subagent starts without them and the
      run pays twice for the handoff.
    - Work in the tree for everything — edits, `git`, greps, builds, relative
      paths. The owner's checkout is not yours.
    - Follow `references/develop.md` for the hard rules (no commit/push/version
      bump; no pin changes to work around a local environment; retire what you
      replace; a mockup's copy is not data) and for what "done" means.
    - If the task cannot be done, write `PIPELINE_BLOCKER.md` in the tree root and
      stop — a blocker is a valid outcome, an invented workaround is not.

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
   - FAIL → Diagnose the failure, fix it inside `WORKTREE_PATH` and re-run verify
     (up to 2 iterations, then `devteam record --result failed` and alarm).
   - PASS → `python3 <team>/cli.py release <project> --worktree <WORKTREE_PATH>
     --task-spec .pipeline/tasks/<id>.json`
     This bumps the version, writes release notes (within
     `profile['whatsnew_max_chars']`, never the boilerplate on an owner-curated
     project, and REFUSES a note that repeats the last shipped one — see "The
     release handover"), commits, pushes to origin main, and watches CI. CI
     delivers to Play internal testing.
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
   `record` also reclaims the run's tree: merged work is dropped, unfinished
   work is committed + pushed to `dt/<task-id>` and the tree is dropped. That is
   the record of the attempt — `status` lists it as `⏸ parked dt/<id> @ <sha>`,
   never as a stray directory. On `blocked`, put the actual question in
   `--note` and name the branch; `main` is for what passed, so a paused attempt
   is never merged forward to "save" it.
10. `python3 <team>/cli.py alarm <project>` — staleness/CI reconciliation;
    may file a GitHub Issue. Then report the OUTCOME BANNER to the user/feed,
    one of the three contract states — e.g. `✅ SHIPPED vX → internal testing`
    / `❓ OWNER ASK: <the decision>` / `🛠 TEAM SHIP: <what landed>` — plus what
    ran, and next step. An analysis-only report with no ask and no ship fails
    the run-outcome contract.
10b. **Hand the owner the page, every run.** The banner ENDS with the two URLs
    `status`/`record` printed (`goal page: file:///…/goals.html`, and
    `shared plan: https://<owner>.github.io/<repo>/goals.html` once served) — a run that
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
10c-bis. **Every ask/flag now carries `agent-tool:` — obey it, never relay it.**
    Both blocks are reviewed by CODE (`team/asks.py`, a deterministic rule table —
    no model, no network) against the tools this machine actually has, and each
    item ends with one of:
      - `agent-tool: browser-control CAN do this` / `desktop-use CAN do this` —
        **AGENT WORK, not an owner chore.** Do it (the block prints the ordered
        plan and, when present, the ONE thing the owner must still supply: a
        card, a legal answer, a 2FA tap). Relaying it as an owner ask is a
        run-report failure.
      - `agent-tool: none — <why>` — genuinely the owner's (a policy act, a
        payment instrument, or text naming no console/OS surface at all).
    A block that ends `(N of these are AGENT WORK…)` is not a report line, it is
    a work order. `devteam asks <project>` prints the same review on demand
    (`--json` for a machine payload, `--strict` exits 1 while any open item is
    agent work, `--selftest` re-runs the rule table's own cases). When the console
    trip is done, record the identifier in the profile and re-run the collector —
    a presence-driven flag clears itself when the read answers.
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

NEXT: `devteam tools` when a step refuses and you need another verb; `references/handover.md` at the end of the run.
