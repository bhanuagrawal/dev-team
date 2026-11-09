# dev-team — working guidelines

This repo IS the pipeline. Every app on this machine is driven from here, by
whichever agent you are (OpenCode, Antigravity, headless). This page is the front
door — the detail lives in `docs/*.md`, one page per job, and each page ends with
the ONE next command. `devteam tools` is the index of what the team can do.

## Start here

```bash
cd ~/dev-team
python3 cli.py tools                 # ONE PASS: every verb, what it writes, what it refuses
python3 cli.py status <project>      # delivery ref, plan, queue — where things stand
python3 cli.py admit <project> --query "<the owner's words>" \
        --kind <kind> --advances <milestone> --why "<one line>" --verdict aligned
#   OK    → pinned at the HEAD of the queue; execute it now, it is top priority
#   FLAG  → nothing queued; reply to whoever the block names and STOP
```

**The team is a toolbox; you are the resolver.** Read `tools` once, then decide
the sequence yourself — there is no route table to look for. `devteam admit` is
the only way a request becomes work; if it flags, the owner (or you, for a
missing kind/milestone) answers before anything executes.

## The five laws

1. **Never edit project code on `main`.** Every task runs in
   `.pipeline/worktrees/<task-id>` on branch `dt/<task-id>`, cut from
   `origin/main`; the branch is pushed to origin at creation, so the tree is only
   a cache. `release` is what puts work on `main`.
2. **Read project code from the delivery ref, not the checkout.** `origin/main` is
   what CI ships; the checkout drifts (68 commits behind, twice producing a
   confident wrong answer). `devteam status` prints `DELIVERY:` on every run.
3. **Production is a verb, not a side effect.** `devteam promote <project>
   --version <v> --requested-by "<the owner's words>" --yes` — only when the owner
   asked for that launch in this conversation. Never by `release`, `record`,
   `loop`, the nightly, or CI; the agent never raises it.
4. **Nothing remains uncommitted, ever.** Every change ends in a commit on a
   pushed ref before your turn ends. "Clean tree" is not the test — **"does this
   exist anywhere but this disk"** is. Work you did not write (the owner's WIP) is
   reported, never committed on their behalf.
5. **Commit every artefact, and say which ref holds it.** Code is not the only
   thing a run produces; the rent is paid per artefact, in the same turn:
   - a finished task on a worktree branch → commit **and push** `dt/<task-id>`
     even when it is not released. *Unreleased is not uncommitted.* The report
     names the ref (`main` or `dt/<task-id>`) and its commit: "done" on a branch
     nobody pushed is a rumour.
   - a generated design spec, screen export, store asset, listing copy or report
     page → commit it in the project (`docs/stitch/**`, `distribution/**`,
     `docs/**`). A generated artefact that exists only on disk is not a
     deliverable, and an ignored path is not an excuse.
   - work you are abandoning, or a tree you are about to prune → commit and push
     it to its branch first; never delete the only copy.
   - a rule, gate or pipeline change in this repo → commit and push **here** in
     the same turn.
   A command that writes into a project is not finished when it prints; it is
   finished when `git log` on the remote shows the commit. A dirty worktree, or a
   branch that is not at origin, is a failed run even when the code is "done".

## Pages — read the one the command names

| page | read it when | loader |
|---|---|---|
| `docs/loop.md` | running work: the commands, what lands where, cleanup, "done" | `devteam tools` / `status` |
| `docs/planning.md` | choosing work: the plan's shape and its gate | `devteam tree` / `doctor` |
| `docs/store.md` | anything the Play listing shows | `storefront-build` / `store-publish` refusal |
| `docs/guarantees.md` | a gate refused, or you are fixing the team itself | `devteam verify` refusal / `doctor` |
| `docs/new-app.md` | starting an app from an idea | `devteam onboard` / `skills/new-project` |

A page no command names is a page nobody reads: `devteam doctor` blocks an orphan
page or a dead link, so the map above cannot rot.

## Where things live

| what | where |
|---|---|
| team code | `~/dev-team/{cli.py,team/,skills/,prompts/,config/,docs/}` |
| canonical CI | `team/templates/ci-release.yml` (project copies are generated) |
| project state | `<project>/.pipeline/{profile,state}.json` — never hand-edited |
| task specs | `<project>/.pipeline/tasks/<id>.json` |
| run logs · handover copy | `<project>/.pipeline/runs/` · `<date>-handover-v<version>.md` |
| store scene spec | `<project>/docs/storefront.json` |
| rendered scenes | `<project>/.pipeline/storefront_scenes/` |
| report page (all apps) | `~/dev-team/site/` — `devteam site` builds, `--serve` serves |

## Done means

`verify` PASS → `release` delivered → `record` + `rc` + `alarm` printed →
worktrees pruned → the report quotes the handover and the page URLs. No
`uncommitted: WARN` from `doctor`, no `⏸ parked` attempt the report forgot.
