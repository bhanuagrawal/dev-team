# Reference — GOAL MODE (direction of every release)

Loaded by: `devteam status` / `devteam tree` (the plan block), and the
`dev-team` skill when a release needs its direction stated.

## GOAL MODE — direction of every release (owner decision 2026-10-18)
Companies run on long/mid/short-term goals; so does this pipeline, and the
goals are DATA, not a new agent:
- **long term** — `profile['goals']['vision']`: the app's thesis (owner-authored).
- **mid term** — `profile['goals']['milestones']`: an outcome + measures, in
  order. The ACTIVE milestone is the first not-done one (deterministic).
- **short term** — the existing research queue; every entry must name the
  milestone it advances in `advances` (`off_goal` declares the exceptions).
Division of labour: the synthesis only PROPOSES (tags tasks, judges a milestone
whose measures are text); CODE DECIDES everything derivable — active milestone,
metric-measure evaluation (`goals.check_measures`; a code FAIL always beats an
LLM "met"), goal-linked ordering in `pick`, and the GOALS/report lines. A run
banner therefore always states the direction: `goal: [m1-…] <milestone> ← <task>`.
When the active milestone is met, the next entry is the NEXT milestone (research
proposes it; an owner call becomes an issues_to_file ask) — never invented polish.
A project with no `goals` gets NO WORK PICKED — there is no goal-less mode
(owner directive 2026-10-18). `pick` returns rc=4 with the owner ask, the run's
terminal state is the OWNER ASK, and the orchestrator asks the owner for one
sentence (`devteam vision`) in the same message. Once the vision exists, the
planner proposes the first milestone under it — that is the team's job, not an
owner ask.

**THE PLANNER — one agent owns the plan, and it runs on EVERY run (step 2).**
`devteam plan <project>` is the single place where direction is decided: it reads
fresh data (deterministic collectors — GA4/repo/codebase/CI), makes ONE synthesis
call, and — through code-validated gates — **maintains the goal tree and the
queue**. Nothing else writes the queue, and only the owner writes the vision.
Its contract:
- **It CHANGES the plan when the plan no longer matches reality** (`goal_changes`:
  add a branch, park/retire a dead one, re-point, set-status). No approval gate:
  each change is recorded with a plain-words reason, shown to the owner
  afterwards ("THE TEAM CHANGED THE PLAN" + page banner + `goal log`), and undone
  exactly with `goal revert`. It never asks permission to plan.
- **Maintenance, not regeneration.** Every queued entry gets a verdict each cycle
  (kept / superseded / dropped-with-reason — the `QUEUE IMPACT` block). A plan
  cannot make a queued task vanish silently.
- **The handover:** `queue[0]` is the ONE task the next run builds. `pick`
  executes that head (deterministically, with the delivery-first and
  un-animated-surface fallbacks); it does not re-decide direction.
- **The stamp:** every review writes `state['plan_review']` (when, what it left
  waiting, which task it handed over). `status` prints it and `pick` warns on
  stderr when the plan was not reviewed today — a skipped step 2 is visible
  instead of silent (a nudge, never a hard gate: a run with no LLM must still be
  able to ship).
- **A verdict about code is checkable, so check it.** A `milestone_verdict` that
  marks a TEXT-measured milestone met RETIRES it and re-aims every future
  release, so before the plan is applied the orchestrator re-runs the cheap
  check the synthesis names (grep the artifact for the measure's evidence). If
  the check disagrees, do not apply the pivot: the plan's direction is the one
  thing worth five minutes of verification, and an LLM verdict flips it.
  Metric-measured milestones need no such check — code already evaluates those
  and a code FAIL beats an LLM "met".
Deterministic core, agent at the edges: collectors, schema, tree changes, queue
impact, and the goal gate are all code — the LLM cannot smuggle in an unknown
branch, a missing reason, work against a done or parked goal, or off-goal work
that is not declared.

**Seeing the tree:** `devteam tree <project>` prints vision → milestones/sub-goals
→ leaves (the work each run picks) and writes two machine-local mirrors into the
project: `.pipeline/goals.txt` and `.pipeline/goals.html` (self-contained, click
to collapse; `--open` opens it). Deterministic renderer, no LLM/network;
refreshed by every `record`/`research`. A project with no goal tree renders the
BLOCKING ask instead of an empty page (and gets no work picked).

**The plan is also PUBLISHED, so it travels with the repo.** `.pipeline/` is
excluded repo-locally, so a machine-local tree would die with the laptop. The
same render is written to **`docs/goals.html`** — one self-contained page, the
SAME file the owner opens locally — and the run's bookkeeping (`record`,
`research`, `adapt`) commits that one file to main and pushes it —
`devteam publish <project>` does the same on demand. GitHub Pages serves it from
the repo's `/docs` source, so the plan has a real URL instead of a markdown blob.
(Markdown/mirror duplicates — `docs/plan.md`, `docs/plan.html` — are RETIRED and
removed from main by the publish commit; the owner asked for one hosted page,
not a second markdown copy to drift.) Best-effort by design: a checkout on
another branch, no network or a moved remote reports why and retries next run; it
never fails a run. Nothing else in the working tree is staged, and a local
release push that lands mid-run is handled by fetch/rebase.

**The page URL is printed — after EVERY change and on EVERY run.** Anything that
regenerates the page ends with two lines: `goal page: file:///…/goals.html`
(clickable in a terminal) and, once the mirror is on the repo AND the hosted page
has been read back (HTTP 200),
`shared plan: https://<owner>.github.io/<repo>/goals.html` — printed ONLY when it
is true (mirror committed, unchanged, on origin, and this build verified served;
the verified URL is recorded in `.pipeline/published.json` so `status` can print
it offline), never a guessed URL. Printed by
`adapt`, `status`, `record`, `doctor`, `tree`, `publish`,
`goal add|park|set-status|retire|reparent|revert|review`, `align --apply`,
`vision`, and `queue add|--done`. `pick` and `research` print them on **stderr**
(their stdout is a machine payload). The owner is therefore never told a change
happened without being handed the URLs that show it, and the run banner ends with
them (run-outcome contract).

**The team CHANGES the tree; the owner reviews afterwards (never waits).**
`devteam goal <project> add|park|set-status|retire|reparent` mutates the plan
directly — no approval gate, and the planner does the same thing inside its own
run via `goal_changes` (applied BEFORE the queue is validated, so a plan can add
a branch and queue work for it in the same cycle; a branch added this way may be
named in a queue entry by its title, and code resolves it). Every change is
recorded with the reason and a before/after snapshot, printed by `status`/`record`
as "THE TEAM CHANGED THE PLAN", and shown as a banner on the goal page; `goal log`
lists them and `goal revert` puts the last one back exactly. Only the long-term
goal stays human-only. The `goal` command is for the owner's own requests and for
repair — the planner is the standing owner of the plan.

**Alignment:** `devteam align <project> "<task or owner request>"` exports the
question with the plan attached; the agent answers JSON (`aligned|neutral|
against|gap` + why/cost/options) and `--apply` records it. `against` exits
non-zero and prints an owner-facing warning; `gap` means the plan has no place
for the work — the agent changes the tree rather than squeezing the work under an
unrelated goal. Verdicts are advisory: recorded and always shown, never silent,
never silently blocking.

NEXT: `devteam tree <project>` for the live plan; `docs/planning.md` (team
repo) for the gate that refuses work filed past an unmet goal.
