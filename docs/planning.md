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

NEXT: `devteam tree <project>` to see where today's work sits, then
`devteam admit` — refused while an earlier sub-goal's number fails.
