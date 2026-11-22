# The plan — one goal, ordered lists, one gate

Loaded by: `devteam tree` · `devteam status` · `devteam doctor` (the `goals` check).

### Planning has four tiers — read as ORDERED LISTS, not a tree

`long-term goal` (owner, one sentence) → `sub-goals` (the **ends**: a great
experience, users stay, users pay — each with one number) → `features` (the
**means**: what gets built) → `tasks` (the queue). Each list is numbered and
**order is priority**; the links are ids (`serves=`), so one feature may serve
more than one sub-goal. The tree diagram is gone — `devteam status` prints
SUB-GOALS / FEATURES / TASKS with `TASKS #1` being the only entry a run builds.
A feature is never added directly under the long-term goal, and work filed under
a later sub-goal is refused while an earlier sub-goal's number fails — so "keep
users before you sell to them" is a gate, not a reminder. `devteam adapt`
inserts/re-tiers the plan deterministically on the next run of any app (no LLM,
no owner ask, every move recorded for review), `devteam doctor` blocks a flat
plan, and the planner may move any branch below the goal (`goal_changes`). A new
app gets the three sub-goals as soon as its long-term goal exists.


### Sub-goals are outcomes; phases are scope lanes

A **sub-goal** is an end (why): *A great experience*, *Users stay*, *Users pay* — each
with one number. A **phase** is a scope slice over features (when): Phase 1 must work
end to end at launch; Phase 2 waits. They are different axes, so **a phase is never a
sub-goal**.

A feature records `phase` and `serves[]` — the one or more sub-goals it advances. That
is how the plan page lists a feature under two sub-goals without duplicating it. A task
belongs to exactly one feature (`advances=<feature id>`). The page renders all four
tiers and groups features into phase lanes; it never shows a separate task list that
competes with the hierarchy.

### Design is consulted, not hardcoded

A per-feature `ready/derive/needed` label is a snapshot of a design that can
change the next day. The plan records at most an **intent** ("reuse the detail
screen"); the authority is the **designer**, who reads the CURRENT Stitch
project and its exported screens for THIS task. There is **no `docs/design.md`**:
a markdown style law let agents claim design compliance while the running screen
matched nothing (owner directive 2026-11-09). The Stitch screen — exported
`index.html` + `screen.png` under `docs/stitch/screens/<id>/` — is the spec.

- The developer asks: `devteam design-consult <project> --task <label> --prompt`.
- The designer answers `existing_screen`, `tweak_screen`, `new_screen` or
  `not_applicable`, and the answer is recorded with a fingerprint of the design
  state (`--record`). Prefer `tweak_screen` (`devteam stitch-tweak`) over
  `new_screen` whenever an existing screen already covers the surface.
- If the Stitch project or manifest changes, the answer is **stale**
  and the developer must ask again. A `new_screen` or `tweak_screen` answer
  blocks UI code until the screen is generated/tweaked in Stitch and exported
  into `docs/stitch/screens/<id>/` before any UI code is written.
- Backend/data tasks are `not_applicable` from their targets; no designer call.

Stitch (`generate_screen_from_text` / `edit_screens` / `generate_variants`) always
returns a **new** screen id and Stitch has no delete-screen call, so "generate,
review, keep" would leave one dead screen per tweak. It does not:

- **The surface id is the only canonical id, and it never moves.** A generated or
  tweaked result is a *draft*: review it (drift audit + its `screen.png`), then
  write it onto the surface with `devteam stitch-adopt --draft <id> --onto
  <surface>` — `update_screen` overwrites the HTML/screenshot in place, so the
  export, the manifest entry and the Compose snapshot keep the same id.
- `devteam stitch-tweak` adopts by default; `--fork` is the legacy escape hatch
  (fork becomes the surface, the old id is retired).
- `devteam stitch-retire --screen <id> --replaced-by <id> --why "…" --yes` records
  a screen that is genuinely dead. `RETIRED.md` is RENDERED from
  `manifest.json → retired_screens`, never hand-edited, and a retired id is never
  canonical and is not resolvable by `stitch-tweak`/`stitch-adopt`.
- `devteam verify` refuses a manifest that disagrees with itself (a retired id
  still canonical, a `replaced_by` that is not canonical, an export directory for
  a dead id, a canonical surface with no export).

Drafts are disposable: generate them in a scratch Stitch project and
`delete_project` it when it piles up — the only true delete Stitch offers.

The exported screen set must be current: re-export the Stitch screens before a
UI task when the project changes. A UI task with no matching exported screen is
not ready — "derive it from tokens" is not an answer.

### Priority interrupts pause a phase; they never re-plan it

The queue has two lanes:

- **priority** — crashes, production bugs and owner asks. `devteam admit` pins
  one at the HEAD of the queue and marks it `pinned`.
- **plan** — the phase tasks, in build order. Phase 1 runs first; Phase 2 is
  frozen until Phase 1's tasks and measures are done.

The picker always takes a priority entry first. When that lane is empty it
resumes the phase at its saved pointer — the first unshipped plan task. A crash
therefore **pauses** the phase; it does not change the phase's task order, its
resume point or its scope. If the crash makes a phase task wrong, supersede that
one task with a recorded reason; do not silently drop it or re-plan the phase.
The plan page shows the interrupt, the resume task and the phase as paused.

### The plan heals itself; it never blocks on being stale

A queue entry is stale the moment its task ships — the ledger says delivered
while the plan still says waiting. That is a thing to FIX, not a thing to
refuse: a stale plan stops nothing, and every command that reads or writes it
already runs the same pass.

- **`save_state` reconciles before the write** (`queue.reconcile`): an entry
  whose label is in the shipped ledger, or an exact duplicate of an earlier
  entry, is dropped and recorded in `state['queue_events']`. Every verb inherits
  this — including a verb written next month — so nobody has to remember a step.
- **`pick` reconciles again on read**, because a state written by a bypass must
  not hand a delivered task to a run.
- **`devteam reconcile <project>`** (or `--all`, `--dry-run`) is the same pass as
  a verb: for a sweep from a schedule or a watcher, and the repair when a writer
  bypassed `save_state`. Deterministic, idempotent, and it never exits non-zero
  on staleness.
- **`doctor` reports it as a WARN**, never BLOCKED — a stale queue is healed on
  the next write; stopping the machine over it would be the one thing this check
  must never do.

Reconcile only removes what the ledgers already answer. Judgement — a scope a
newer change invalidated, two tasks that should merge — stays with the planner
(`plan` / `replan`) and its impact block, which keeps reporting every verdict.

## Done is earned, not exhausted (owner directive 2026-11-09)

When the last task of a feature ships, the feature is not done — it is **built**.
A branch is *complete* only when every measure it declared has evidence: a
machine-checkable number that signals satisfy, or a recorded verdict
(`goals.record_verdict`, printed by `devteam closure`) with a non-empty evidence
string. A built branch without evidence is a **GAP**, and the gap is the next
task — not the next feature. The same holds one level up: a sub-goal cannot read
done while a feature under it is unproven.

This is enforced, not remembered: `validate_advances` lets work be queued *at* a
built-but-unproven branch (and demands the task say what the gap is in
`problem`), but refuses a plan that steps **over** one; `status` prints ⚠
"built, NOT proven"; `record` prints the closure block the moment the last task
lands; `devteam closure <project> --require` exits non-zero for a step that must
not move on.

NEXT: `devteam tree <project>` to see where today's work sits, then `devteam
admit` — refused while an earlier sub-goal's number fails; `devteam closure
<project>` says which "done" is only a list that ran out.


### The product law is a file, and every task cites it

Priority is one axis; truth is the other. A task can advance the right milestone
and still contradict the product (2026-11-15: three docs, a prompt and a tool
catalogue each described a "match" the backend had already deleted).

So every managed project declares its law in **`PRODUCT.md`** — one clause per
line with a stable id (`P1`…`P12`), one sentence each. The sentence is the
quotable half; a clause may carry an **indented detail block** under it, which
says what the sentence means on a screen (owner decision 2026-11-22: *"this
needs more detailed writeup in product, not just your small point"*). The detail
is binding on whoever implements the clause, and `devteam law <project> --show
P16` prints it. Declaring it is not
optional (owner decision 2026-11-22: *"I want to have this for all the apps
managed by dev team and any new app"*); the only other legal state is the owner
waiving it in their own words, and that waiver is written into
`profile.json → law_waiver` where `doctor` and `status` can read it back:

```
devteam law <project> --waive --requested-by "<the owner's words>"
```

Everything else cites the law: a migration header (`-- Law: P1`), a test name, a
source file's header comment. Then:

- `devteam law <project>` prints every clause and the first file that cites it;
  it exits 1 while the law is undeclared and unwaived;
- `devteam admit … --law P1 --effect obeys` records **which clause** the request
  serves **and what it does to it** — required once the law is declared (not for
  `release`, which delivers work that already named its clause);
- `devteam pick` refuses (rc 5) to hand a run a task whose queue entry names no
  clause — the warn `doctor` used to print becomes the refusal at the moment
  code would be written;
- `devteam law <project> --cite <label> --law P4 --why "<why it serves it>"`
  classifies work that is ALREADY queued and predates the law (the planner used
  to queue clause-less entries). It neither adds nor changes work, needs a
  reason, and accepts only `obeys` — an amendment or a contradiction is a new
  request, so it goes through `admit`;
- `devteam doctor` **blocks** an app with no declared law and no waiver, and
  still refuses three things once it is declared: a clause nothing cites (*dead
  law*), a citation of a clause that does not exist (*fiction*), and a queued
  task with no clause (*work with no reason in the product*).

**The plan itself reads the law.** `PRODUCT.md` is a block in the planner's
prompt, every planned queue entry must carry `"law": ["P4", "P13"]`, and a new
milestone may carry the clauses it exists to render — entries queued under it
inherit them. `validate_plan` refuses a plan whose entry names no clause, cites
an unknown id, or claims `law_effect: "amends"`/`"contradicts"`: those are not a
planner's call.

**Goal and law are different kinds of thing, and that is why they collide.** A
goal is where the product goes; it moves. The law is what the product *is* while
it goes there; it does not move except by an amendment. So the order is
**owner intent → law constrains → goals → features → tasks**, and one request
gets exactly one of three answers:

| `--effect` | what happens |
|---|---|
| `obeys` | the clause is cited in the branch; the run proceeds |
| `amends` | allowed, recorded, and the clause's new sentence must land with the code that cites it |
| `contradicts` | **nothing is queued**: the owner is asked with the clause in front of them — amend, drop, or park |

The contradiction flag is the point: a request that would break a clause is not
an alignment question about the plan, it is a product decision, and the only
person who may make it is the owner.

**An amendment is the owner's act, never an agent's.** `devteam law <project>
--amend P8 --text "<new sentence>" --detail "<the written spec>" --requested-by
"<owner words>"` rewrites one clause in place (keeping its `binds:` marker
unless the new sentence declares its own) and logs the amendment in
`state.json → law_amendments`. `--detail` is optional: omitted, the block the
owner already wrote stays; `--detail ""` clears it deliberately. A NEW clause is
declared the same way with `--add P16 --text "…" --binds app,web,backend
--detail-file spec.md --requested-by "…"`, which refuses an id that already
exists. The sentence and its detail must land in the same branch as the code
that relied on the old one.

**A clause that spans platforms says so.** `- **P11** — … (binds: app, web,
backend)`. The project maps each name to the paths that own it, in
`profile.json → law_bindings`:

```json
"law_bindings": {
  "app":     ["composeApp/**"],
  "web":     ["web/**"],
  "backend": ["supabase/**", "python-backend/**"]
}
```

**A clause whose screen is not built yet binds nobody.** `--amend Pn --text "…
(binds: none)"` clears the marker: the sentence and its detail stay law, and no
surface is charged with a citation until the task that builds the screen names
its binding. The owner used it on BookSwap's P15 (delete your account in
Passport): the clause was declared while the task that builds the Passport
control was still queued, and charging it to every app diff refused work that
never opened that screen. Clearing is the owner's act like any amendment, and the
amendment log carries their words; a clause left at `none` for good is the same
dead law `doctor` reports — the deletion task re-binds it when it lands.

`devteam law` then refuses a declared binding that nothing under its own globs
cites, and `doctor` reports it. One citation from one client is not coverage:
without `binds:`, "cited" meant only "somebody mentioned it" — BookSwap's
location clause passed on a Kotlin citation while the browser client shipped a
hand-copy of the old rule on the same disk. A binding the project never mapped
is a finding too, so the marker can never silently pass.

The map is written by the verb that uses it, never by hand:
`devteam law <project> --bind app 'composeApp/src/**'` names a surface, and
`--unbind app` (or `--unbind app 'docs/**'` for one glob) is its undo. Prefer one
name per surface — `app`, `shared`, `design` — over one name wearing two globs:
coverage is per glob, so a clause cited only under the first glob leaves an
honest finding under the second, and the fix is a distinct name, not a second
citation. Why the undo exists: declaring Paddle's law bound `app` to both
`composeApp/src/**` and `design/src/**`, and the only way out of the false
finding was editing `profile.json`, the file the team is told never to touch.

**A citation in prose covers nothing but prose, and each glob is its own
surface.** Two refinements from 2026-11-20, when BookSwap's public site shipped
copy that contradicted two clauses for two days with every gate green:

- a `.md` page saying "P13 says a place is never named by a list of cities" is a
  document *describing* the law, not a surface *obeying* it — so only a non-prose
  citation (`*.kt`, `*.ts`, `*.py`, `*.html`…) covers a binding, and the generator
  of a page tree carries the header itself;
- coverage is checked **per glob**, not per name: a citation in `web/src` cannot
  vouch for `docs/**`, because those are different things a reader opens. Map the
  published tree as its own glob under the name it belongs to, and let the
  generator emit the marker into the pages it writes (`<!-- Law: P3, P13 -->`).

**A published page tree must be a named surface.** Any tracked `index.html` tree
is a surface, and if a project declares law while leaving it out of
`law_bindings`, `devteam law` exits 1 and `doctor` warns: the pages a reader opens
are the product's most public claim, and a tree no clause knows is a claim nothing
checks. `verify` refuses a diff that writes such a tree — the moment to name it is
the change that publishes it — and refuses a diff that changes a named surface
without the clauses bound to it being cited there.

A project that has neither a declared law nor a recorded waiver is **blocked**,
not merely warned: `doctor` exits 1 and `pick` refuses to start a run. An
amendment must touch the clause, the code that cites it, and a test in one
commit — `devteam law --amend` writes the clause half of that, the branch owes
the rest.
