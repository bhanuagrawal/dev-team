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

