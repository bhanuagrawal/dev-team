# Reference — the handover (and a SPEC handed to you)

Loaded by: `devteam record` (it prints the handover copy) and the skill at
the end of every run.

## The release handover (owner requirement 2026-10-23)
A build on internal testing is only useful if the owner knows what it says and
what to do with it. So every shipped run's report carries three things, printed
by code (`record`) and quoted verbatim by the orchestrator:

1. **What's new — the note Play will show.** The exact committed text, with its
   length against the allowed limit: `what's new (231/500 chars, …)`. The store
   turns away an over-long note, so the length is part of the report, not a
   detail (the note is trimmed to `profile['whatsnew_max_chars']`, default
   500; the generated copy names the task's first clause, never the 200-char
   internal label).
   - **Where the words come from is a project fact.** Default: the pipeline
     generates them from the task. `profile['whatsnew_curated'] = true` means the
     OWNER's wording wins over the boilerplate: the pipeline reads the file and
     never invents the type-template for it. It DOES write an explicit
     `"whatsnew"` from the task spec — that is this release's authored copy, and
     refusing it is what froze one project's note for 28 releases.
   - **A REPEATED NOTE IS REFUSED, NOT WARNED.** `verify` and `release` compare
     the note about to ship with the last SHIPPED note (the ledger, not the
     newest tag: a tag lands before CI, so a failed upload must not count) and
     fail on a byte-identical repeat. `store-readiness` reports the last two
     shipped notes being identical as a BLOCKER. Remedy is always the same one
     line: put this release's user-facing copy in the spec's `"whatsnew"`. Curation
     is the owner's last word, never a freeze.
   - **THE GENERATED LINE IS ONLY AS GOOD AS THE LABEL, so write one when the
     label is pipeline-shaped.** The fallback note interpolates the task's label
     verbatim (`✓ New in this update: <label>`), and `_short_label` only cuts a
     long mechanism sentence at its first clause — it does NOT humanize a slug, by
     design (de-hyphenating would mangle a real product name like `PDF-Kit`, the
     exact hazard its docstring names). So a queue label such as
     `day-one-pause-runthrough` would ship to the store as
     `✓ New in this update: day-one-pause-runthrough` — internal words in a user's
     listing. The seam is already there: the task spec (`.pipeline/tasks/<id>.json`,
     which the ORCHESTRATOR owns) may carry an explicit `"whatsnew"` string and
     `_whatsnew_text` uses it verbatim (clamped to `whatsnew_max_chars`). When the
     picked entry's label is a slug or otherwise reads as pipeline vocabulary, the
     orchestrator writes the user-facing line into the spec before spawning the
     developer, and the handover then quotes that copy. Never "fix" this with a
     copy heuristic in team code — a judgment about storefront wording is exactly
     the kind of approximation the owner has told the team to stop computing.
2. **What to manually test.** The pipeline's gates prove the build compiles,
   passes its tests and installs — they cannot feel the app. So the handover
   lists the areas the diff touched (derived from the task's targets, never
   invented) plus the concrete claim the queue entry made, and asks the owner to
   walk it. This is not ceremony: on 2026-10-23 a device gate went green while a
   success screen printed a MediaStore id, and a hand walk found seven defects an
   agent crawl's "0 blocked taps" had hidden.
3. **Is this build a release candidate? — the LLM that MADE the change decides;
   nothing in the code rules on it.** Owner directive 2026-10-24: first *"if a
   build is a release candidate that agent has to tell, not human"*, then *"let
   llm decide this, ask this to llm only which made the changes, that's it, no
   eval nothing"*. So there is **no rule, no scoring, no file-overlap check and no
   validation of the answer** — the handover prints the question and the single
   command that records the answer, and the judgment belongs to the agent:
   - **Ask the developer session** (the agent that wrote the code — continue its
     conversation if your tool can): *"you made these changes — does this build
     stand on its own in front of users, yes or no?"* One line of why.
   - If that session is gone, the orchestrating LLM answers it, having seen the
     diff and the developer's report.
   - Record it: `devteam rc <project> --version <v> --yes|--no --note "<the one
     line>"`. The owner can replace the call later with `--owner`, and both stay
     in `state['release_candidates']` (newest wins, so it cannot drift).
   **State the call in the run banner** — as a statement, not a question: the
   owner is told whether the build is a release candidate, never asked to guess.
   **Nothing acts on the verdict**: there is still no promotion path in this
   team, and the line is never phrased as "should we promote it" — promoting
   beyond internal testing stays the owner's manual step in Play Console.

## When the owner hands over a SPEC (do this before planning it)
A spec arrives as a document, not as a queue entry, and it is usually part-wrong
about the app. Before it reaches the planner, DIFF it against the shipped tree
and answer three questions per item: is it ALREADY BUILT, is it PARTIAL, or does
it CONTRADICT what is shipped? Then check the spec against the store's own rules.
Two incidents, one session apart, both caught before any code was written: the
spec asked for `targetSdk 34` when Play rejects anything below its current floor
(shipping it would have failed the release), and it asked for Compose Material3
UI on an app the same owner had already ordered to be fully canvas-drawn with no
stock widget anywhere. A spec is also the cheapest place to find work that DOES
NOT need doing — its "core" was already implemented and unit-tested, and
building it again would have been pure waste. Report the diff to the owner as
ALIGNED / PARTIAL / CONFLICT with evidence (file + line where possible), let
them resolve the conflicts, and only then let the planner turn it into queue
entries. Record the adopted spec as the project's source of truth (its PRD) so
the two cannot drift.
**QUESTION THE STACK, NOT JUST THE FEATURES.** A spec's stack line ("use X",
"via Y") reads like a requirement and is actually a *suggestion about how* — and
it can contradict the spec's own principles. A spec that demands "fully local, no
backend" while naming a hosted billing service is the common case: the service is
a LAYER on top of the store's own billing, not an alternative to it, so adopting
it adds a third party and an account (plus a revenue-share fee) in exchange for
benefits the app may not be able to use yet. Check what a named service actually
DOES before it becomes the plan, say plainly what it would and would not buy, and
record the deviation if the app's constraints make the leaner path the right one.

NEXT: `devteam rc <project> --version <v> --yes|--no --note "…"` — the
release-candidate call is yours to record, never the owner's to make up.
