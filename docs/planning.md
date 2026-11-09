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

