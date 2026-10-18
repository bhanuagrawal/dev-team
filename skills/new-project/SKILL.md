---
name: new-project
description: >
  Delegated skill for the DEV-TEAM orchestrator's CREATE mode: go from a
  plain-language app idea to a repo, a signing key, wired secrets, a release
  workflow, and a first build on Play Console INTERNAL TESTING — then a
  nightly loop. Kotlin Multiplatform (Compose Multiplatform), Android-first;
  iOS is deliberately deferred. The agent does everything; the user is asked
  ONLY what genuinely requires a human.
---

# Create-Agent: idea → internal-testing build

You are the create-agent. The owner gave you an idea in plain language.
Your job ends with: **a commit on `main` that CI turns into a signed AAB
uploaded to the Play internal-testing track**, plus a nightly loop installed.
Default platform stack (owner decision, 2026-10): **KMP, Android-first**.

## STEP 0 — refresh official skills (mandatory, before ANY code)
Official Google Android skills are the primary craft source — update them
first, log versions:
```bash
android skills add --all --project <project-root>
git -C ~/.agents/kotlin-skills pull 2>/dev/null || \
  git clone https://github.com/Kotlin/kotlin-agent-skills ~/.agents/kotlin-skills
```
Record output into `~/.agents/kotlin-skills-version.txt` (date + HEAD).
Google reference: github.com/android/skills — includes project-creation,
edge-to-edge, R8, navigation for AGP9, and the `android` CLI (which itself
helps create projects, manage SDKs, search docs). Kotlin/JetBrains:
github.com/Kotlin/kotlin-agent-skills (+ JetBrains/skills catalog).
Offline / update-blocked → proceed but log it.

## STEP 1 — intake (ask the owner — these questions only)
Never assume; ask in ONE message:
1. App name + one-line what it does (you may propose a draft from their idea).
2. Package id proposal — confirm (permanent once published).
3. Repo visibility: private (default)/public.
4. Play developer account: exists / needs creation (human console + fee).
5. `PLAY_SERVICE_ACCOUNT_JSON`: if Play exists, walk them through creating the
   service account (Google Cloud console → Play Console → Users & permissions
   → grant "Release to testing tracks" → download JSON), then get a local path
   for the JSON from them. If not today, mark REQUIRED gate pending.
6. Firebase: default DEFERRED (Noop analytics) — repo learns this later.
Backup ask (one line): after you generate the keystore, they copy it + password
off-machine (you generate; they never generate).

## STEP 2 — project creation (official sources, not hand-rolled)
1. Scaffold from the official JetBrains KMP template:
   `git clone https://github.com/JetBrains/KotlinMultiplatformTemplate <name>`
   (or the KMP wizard output if the template moved). Keep ONE `composeApp`
   module; DELETE the iOS target config and iosMain — Android-first is a
   recorded owner decision; do not ship iOS until told.
2. Rename package/app per intake; keep `commonMain` as home for ALL product
   code; `androidMain` = MainActivity host only.
3. Install the dev-team MVI core (references/mvi-core/*.kt) under
   `composeApp/src/commonMain/kotlin/<pkg>/core/event/` + rename package.
   These become FROZEN files. Follow references release-proven.yml /
   ship-on-version-bump-proven.yml to write `.github/workflows/` with the
   package + `composeApp/build.gradle.kts` swapped in. Verify `android`
   skills guidance for project creation if the template needs adaptation.
4. Write `AGENTS.md` (baseline from skills/android-code + this repo's own
   release/froze law), `docs/PRD.md` (draft from the idea),
   `distribution/whatsnew/whatsnew-en-US` (first build line),
   profile.json + state.json (field list same shape as `devteam onboard`
   output; src_root = composeApp/src/commonMain/kotlin;
   version_file = composeApp/build.gradle.kts; ci_workflow =
   ship-on-version-bump.yml; frozen_paths = core/event/**).
   **Also write `profile.json['goals']`** — from the owner's own words in
   intake: `vision` (the long-term thesis, one sentence) and `milestones`
   (mid-term, in order: `{id, title, outcome, measures[]}` where a measure is
   `{"text": "..."}` or, when GA4 can decide it, `{"metric": "...", "op": ">=",
   "target": N}`). A brand-new app is worthless without a direction: the first
   milestone is normally "the core loop works end-to-end", the second "we can
   see what users do" (instrumentation), the third a retention/traction number.
   The pipeline then orders every release against these — see the dev-team
   skill's GOAL MODE section.
   **Also stamp `"profile_schema": 2`** (the team's current data shape) so the
   project is born current. From then on `devteam adapt <project>` — step 0 of
   every workflow run — keeps it that way as the team itself changes; a project
   created by hand (no stamp) is adapted automatically on its first run instead
   of silently missing whatever the team learned since.

## STEP 3 — signing + secrets + repo (agent does; user only backs up + passes tokens)
```bash
keytool -genkeypair -v -keystore config/keystore.jks -alias upload \
  -keyalg RSA -keysize 2048 -validity 10000 -storepass $PW -keypass $PW \
  -dname "CN=<name>"
OWNER="$(gh api user -q .login)"   # authenticated account — never hard-coded in the skill
gh repo create "$OWNER/<slug>" --private --source . --push
gh secret set RELEASE_KEYSTORE_BASE64 -R "$OWNER/<slug>" --body "$(base64 -i config/keystore.jks)"
gh secret set KEYSTORE_PASSWORD/KEY_ALIAS/KEY_PASSWORD -R ... (keystore PW/alias/pw)
gh secret set PLAY_SERVICE_ACCOUNT_JSON -R ... --body-file <userProvided.json>   # when they give it
```
Write `.pipeline/secrets.json` locally (keystore path+pws, chmod 600,
gitignored). gitignore: keystore/config, .pipeline/, build, local.properties.

## STEP 4 — first internal-testing build (the goal proves itself)
1. `gh run run -R ...` or plain: push a `v1.0.0` TAG → release.yml fires →
   build → AAB → Play internal testing (or skip to STEP 5 if no Play gate yet;
   then the first bump push still proves CI green).
2. `gh run watch` to conclusion; log it in .pipeline/runs via
   `python3 <team>/cli.py record <slug-project> --result shipped --note "greenfield: first build"`.

## STEP 5 — nightly loop (OPT-IN, manual is the default)
The pipeline triggers MANUALLY by default: the owner (or any agent session)
says "run the dev-team pipeline for <project>". Auto-scheduling is an
explicit opt-in — ask the owner, and if accepted:
```bash
python3 <team>/cli.py setup --project <path> --schedule auto --at HH:MM
# --at optional: the scheduler proposes the first free slot inside the
# owner's night window (22:00–06:00 IST) that does NOT clash with other
# registered schedules (`devteam schedules` lists them all).
```
Then close with the consolidated report: what's live, what's pending gates,
the next-build trigger (manual phrase / schedule time).
Status summary to owner: what's live, what's pending gates, next build time.

## STAGE MODEL (an app reaches steady-state in stages — never block on a future one)
- **Stage 1 (agent, now):** scaffold from official template → GitHub repo (agent-created, private default, package derived from app name) → keystore generated + 4 secrets → CI workflow committed → **first v1.0.0 tag push = GREEN CI build on GitHub Actions** even with zero Play access (upload auto-DEFERRED — see reference workflow's conditional upload step). Firebase deferred by default (Noop tracker).
- **Stage 2 (after owner gate):** owner provides Play service-account JSON → agent sets the secret → the conditional turns ON → next release lands on internal testing automatically. Agent verifies + records.
- **Stage 3 (later, owner-triggered):** Firebase project + google-services.json → agent integrates + swaps Noop. 
- **Later-phase agents (explicitly NOT now, owner roadmap):** Play-account-creation agent; designer agent. Do not fake them; list them as deferred in run reports.
- Reporting rule: every run/summary states `stages satisfied → pending → deferred`. A stage's absence never blocks an already-satisfied one.

## HARD RULES
- NEVER build iOS blocks or iOS code (owner decision: Android-first).
- NEVER invent CI/credentials ourselves; use gh/keytool commands shown.
- NEVER push a bump/tag with red CI. Never hold user passwords—generate yours.
- Show ONE consolidated gate-report message, not a drip of questions.
- If official-skill sources moved — trust android CLI first, then JetBrains
  wizard/templates — never re-create art by memory.
