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
        --kind <kind> --advances <milestone> --why "<one line>" --verdict aligned \
        --law <Pn> --effect obeys      # or amends | contradicts
#   OK    → pinned at the HEAD of the queue; execute it now, it is top priority
#   FLAG  → nothing queued; reply to whoever the block names and STOP
#   `--effect contradicts` is an owner decision, never a run: nothing queues.
```

**The product law is declared, not remembered.** Every managed app has a
`PRODUCT.md` (mandatory unless the owner waives it in their words); `devteam law
<project>` shows it, `doctor` blocks without it, and `pick` refuses a queued task
that names no clause. A clause is one sentence plus, where the sentence needs it,
an indented **detail block** that spells out what it means on a screen — the
sentence is what a report quotes, the detail is what an implementation follows
(`devteam law <project> --show P16`). Never build work you cannot tie to a
clause — and never change a clause yourself: `devteam law <project> --amend Pn
--text "…" --detail "…" --requested-by "<owner words>"` is the owner's act, and
`--add Pn --text "…" --detail-file spec.md --requested-by "…"` declares a new one.

**The team is a toolbox; you are the resolver.** Read `tools` once, then decide
the sequence yourself — there is no route table to look for. `devteam admit` is
the only way a request becomes work; if it flags, the owner (or you, for a
missing kind/milestone) answers before anything executes.

## The six laws

1. **Never edit project code on `main`.** Every task runs in
   `.pipeline/worktrees/<task-id>` on branch `dt/<task-id>`, cut from
   `origin/main`; the branch is pushed to origin at creation, so the tree is only
   a cache. `release` is what puts work on `main`.
2. **Read project code from the delivery ref, not the checkout.** `origin/main` is
   what CI ships; the checkout drifts (68 commits behind, twice producing a
   confident wrong answer). `devteam status` prints `DELIVERY:` on every run.
3. **Production is a verb — and only the owner's word makes it one.**
   `devteam promote <project> --version <v> --requested-by "<the owner's words>"
   --yes` launches one build. When the owner asks the team to do it every time,
   `devteam autopromote <project> --on --requested-by "<their words>"` records a
   per-app provision: the newest release candidate is promoted once CI has put
   it on the internal track, and an explicit `rc --no` on that version vetoes it.
   Never by `release`, `record`, `loop` or CI, and never raised by an agent
   without one of those two asks.
4. **Nothing remains uncommitted, ever.** Every change ends in a commit on a
   pushed ref before your turn ends. "Clean tree" is not the test — **"does this
   exist anywhere but this disk"** is. Work you did not write (the owner's WIP) is
   reported, never committed on their behalf.
5. **Nothing ships unwalked — the change you made runs on a device first.**
   Owner rule 2026-11-20: *"jo banaya hai usko emulator pe test karo, fir
   release"*, narrowed on 2026-11-21: *"wo ux flow run wala rule hata do … current
   change jo kiya hai wo emulator pe run karne wala laga do"*. `verify` and
   `release` refuse a code change with no walk newer than the file you changed,
   and a walk that stopped at a wall. They do **not** require every journey in
   `docs/ux-flows.json` to be re-walked on every build: the walk that watched the
   change is the evidence, and a journey last walked on an older build is
   information, not a refusal. A walk is not tied to a commit id (a rebase
   rewrites every sha without changing a pixel), but it is recorded with the
   tree's `HEAD` so the handover names the build a person opened. **The walk
   runs against the real backend the app ships with** (owner, 2026-11-21: *"drop
   the mock server and use the real server and the test accounts"*), signing in
   with the audit account from `.pipeline/audit-account.json` (gitignored;
   `DEVTEAM_AUDIT_EMAIL` / `DEVTEAM_AUDIT_PASSWORD` win) — never a frozen
   fixture. A fixture in a spelling or a shape production never sends is a
   fixture that hides the bug, and the progress card was "verified" against one
   that answered a completed task with no plan.
   A diff that changes how the app searches, locates a reader, or reads its own
   profile is a diff that gets opened on a phone before it gets a version code.
   **And the walk has to show the change**: the task names, in `walk.observables`,
   words that can only be on screen if this diff runs (and may pin the journey in
   `walk.flows`). The walk records every step's on-screen text from the
   accessibility tree, so `verify` matches the claim against what the walk saw —
   a green walk that never reached the change is refused, which is how "no
   message visible on app until feed arrived" (2026-11-21) gets caught next time:
   a `search` walk passed while the progress card was never on screen, because
   search submits over REST and only the feed's turn goes through A2A.
   Full text: `docs/guarantees.md` item 8.
6. **Commit every artefact, and say which ref holds it.** Code is not the only
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
| `docs/planning.md` | choosing work: the plan's shape, its gate, and the product law | `devteam tree` / `doctor` / `law` |
| `docs/store.md` | anything the Play listing shows | `storefront-build` / `store-publish` refusal |
| `docs/guarantees.md` | a gate refused, or you are fixing the team itself | `devteam verify` refusal / `doctor` |
| `docs/new-app.md` | starting an app from an idea | `devteam onboard` / `skills/new-project` |
| `docs/mail.md` | a decision needs the owner, or setting up their email | `devteam mail` / `doctor mail: WARN` |

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
| owner email (recipient + OAuth client) | `config/mail.local.json` (0600, gitignored; the OAuth refresh token in the Keychain) |
| report page (all apps) | `~/dev-team/site/` — `devteam site` builds, `--serve` serves |

## Done means

`verify` PASS → `release` delivered → `record` + `rc` + `alarm` printed →
worktrees pruned → the report quotes the handover and the page URLs. No
`uncommitted: WARN` from `doctor`, no `⏸ parked` attempt the report forgot.
