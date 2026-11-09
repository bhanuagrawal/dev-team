# Reference — operating the team (skills, asks, direct runs, upkeep)

Loaded by: the `dev-team` skill when the owner asks for one agent only, or
when an ask/flag needs triage.

## OFFICIAL-SKILLS REFRESH (before writing/editing ANY project code)
Development leans on official Google/JetBrains skills and they drift:
1. `android skills add --all --project <absolute project path>` — updates the
   official Google Android skills (`github.com/android/skills`) to latest. Pass the
   project's ROOT PATH, never its bare name: `--project` takes a path, so a name
   silently creates a nested `<name>/` directory inside whatever checkout you ran it
   from (seen 2026-11-01 — a junk tree in the owner's project root that no gate
   noticed, because an untracked directory is not a change to any tracked file).
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

## Interactive (human-present) sessions
Same procedure; `supervised` tasks become available when a human is with you —
say so in your report, then run the task with the human watching.

## Alarms you may receive
- nightly launched you → follow the procedure; never skip verify/release/record.
- alarm Issue exists with [dev-team] title → start at `status`, diagnose from
  `.pipeline/runs/`, fix cause (model missing, gh auth, CI red), re-run.

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
  Scope-limited still means ADMITTED: if the request would CREATE the work, run
  `devteam admit` first (a task that was never admitted has no milestone, no
  reason and no place in the plan — the run would be invisible).
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
  **First decide what the listing says** (`devteam storefront-direction <project> --seed` →
  fill concept / audience / copy_angle / palette.emphasis / evidence → `--apply`): one locked
  idea plus the argument each frame makes, committed as `docs/storefront-direction.json` and read
  by the plate prompt and the layout brief. A direction can waste a poster; it can never touch the
  screen, which stays the real render.
  **Then run `devteam storefront-build <project> --poster` — the whole pass, in the only order
  that works.** It renders the app's own composables from origin/main, refuses to continue
  without the editorial review (and without **at least four shipping frames** — fewer is a
  BLOCKED, not a warning), sends each scene's render to Gemini (browser, ONE chat, one prompt per
  scene, each after the first told to match the set already in the conversation) which paints the marketing image, strips the model's watermark, upscales the result to
  a size Play accepts, gates on the critic, and prints the upload command. Run the steps
  individually only to debug one of them:
  1. **Render** from the app's own composables: `python3 <team>/cli.py storefront-scenes <project> --render`
     (never hand-drawn HTML; the scene spec is the project's `docs/storefront.json`).
  2. **Editorial review — an LLM decides, not the filenames.**
     `devteam storefront-scenes <project> --review` writes the prompt for the orchestrating LLM,
     `--review --llm` calls the configured reviewer model, `--review --apply <json>` records the
     orchestrator's answer. The review names the shipping order, the cuts and the reason for each;
     it is committed as `docs/storefront-review.json`. Two policy rules override any model:
     **a monetization surface (paywall/purchase/subscription/upgrade/price) never ships**, and every
     kept frame must be ranked (an unranked scene is cut, never silently shipped).
  3. **Compose, then frame in ranked order**: the poster is a DESIGNED deliverable. Two shapes,
     both one prompt per scene. **`--poster`** (the owner's default): the scene render is attached
     to the Gemini prompt and the model returns the finished marketing image — it is a depiction,
     so it is watermarked-cleaned and upscaled to Play size, never passed off as the render.
     **`--gemini`** (pixel-crisp type): Gemini paints only the backdrop plate, and the phone,
     layout and every letter are drawn by code — the composition comes from the vision model
     (`--vision-frame`/`--layout`) or Stitch (`--design-frame` → `--adopt-frame`). `--frame` then
     writes `01_<slug>.png` … and removes pipeline-owned frames the review cut. `--builtin` ships
     the plain template and must be passed DELIBERATELY — it is never the fallback, and
     `storefront-build` refuses rather than default to it. The critic (`store-assets`) fails a
     listing that still carries a monetization frame, `icon = null`/flat placeholder tiles, or an
     order that disagrees with the review.
  3b. **The icon is a STITCH design; the banner is Gemini's.** `devteam storefront-brand <project>
     --stitch-prompt` compiles the icon brief (artboard, palette, flat vector, mark only), you
     generate it in Stitch, and `--adopt-stitch <file>` keeps the artboard at
     `docs/storefront-icon.html` and renders it at exactly 512×512. The feature graphic is the
     Gemini one: `--generate` hands the model the app's hero render and records the result.
     Both land in `docs/storefront-assets.json`, and `storefront-build` runs the banner step
     automatically while REFUSING to invent an icon — an upscaled mipmap or a template scores
     zero and the upload stops. The owner may still supply either file with
     `--adopt kind=<png>`, recorded as `owner`.
  4. **Publish — API for the upload, BROWSER for the publish.** `devteam store-publish <project>`
     (dry run) / `--yes` uploads icon + feature graphic + screenshots in the editorial order,
     commits the edit, and reads the bytes back through a fresh edit. That is where the API
     stops: it cannot grant permissions and it cannot release a listing that **Managed
     publishing** is holding. So this step is finished in the **browser** — `browser-control`,
     driving the owner's own logged-in Play Console session — and it is never called done from
     an API answer:
     - `PERMISSION_DENIED` on commit = the service account lacks **Manage store presence** for
       this app (a testing-track-only grant happily uploads every image and then has the edit
       discarded, so nothing changes). Grant it in Play Console → Users and permissions → the
       account → App permissions → Manage permissions. Console-only, one browser step, once
       per app — then re-run `--yes`, which re-uploads nothing if the bytes already match.
     - Managed publishing ON → the committed change lands in **Changes in review**, then in
       **Publishing overview → Changes ready to publish**. Press Publish (an owner call when
       the gate is theirs), then verify what customers actually see — the public listing page,
       not an API read-back.
     Never report "republished", "live" or "pushed to Play" from a successful upload or a
     committed edit alone: an upload is not a publish, and a commit under managed publishing is
     a staged change. A 403 commit used to read as success and left the paywall frame live.
Rule: honor the narrower scope exactly — no pipeline chaining — except the
non-negotiables (never commit on red gates; never
hand-edit state.json; MODE-B gates still asked). State a one-line report of
what you did and did not chain.

## Upkeep (every run — the team improves itself)
There is nothing to read back and nothing to append. When a run surprises you,
the surprise is a defect in the team's code or in this contract: fix the mechanism
in the SAME RUN — generic (no project names), traced to what actually happened,
and proven (`devteam doctor <project>` still green, and the changed command
exercised once if cheap). A note describing the fix is not the fix; only a commit
counts, so "suggested only" is reserved for fixes that are big, risky, or need an
owner decision — and those go into `scripts/rules.json` as `enforcible`, where
`devteam docs-audit --rules` keeps them visible. Report the commit, not the
lesson.

NEXT: `devteam asks <project>` — who can do each open ask, decided by code.
