# recon — the manual intel plane (competitor research, by hand)

name: recon
description: >
  Inspect a competitor's Android app and write a report the delivery team's
  planner reads. MANUAL and SEPARATE from the nightly run: you invoke it when
  you want it. Two passes — a deterministic static pass (no emulator) and an
  agentic dynamic pass (drive the app on an emulator). Use when the owner asks
  "what do our competitors do?", "check this app's features/paywalls", or wants
  grounded gaps instead of guesses.

## What this is (and is not)

- **A second workflow.** The delivery plane (`dev-team` skill, nightly run) does
  one task → internal testing. THIS plane produces competitor intel. They do not
  call each other: the only link is a directory of report files.
- **Manual, for now.** No schedule, no daemon, a human is present. Any question
  that needs a login, a secret or a judgement call comes to the human in the
  room — there is no unattended unblock path yet (see *Graduating*, below).
- **The boundary is files.** `devteam recon` writes
  `<project>/.pipeline/competitors/<package>.json`. The planner's collectors read
  that dir (`research._competitors_pull`) and **never wait** on it. `none` is a
  normal state, never an error.

## Where it sits

```
INTEL PLANE (this skill, manual)
  you → devteam recon static <apk|package>     → .pipeline/competitors/<pkg>.json
      → (optional) drive the app on an emulator → devteam recon findings …  ↺
                               │
                               │  files only, one-way, async
                               ▼
DELIVERY PLANE (dev-team, nightly)
  step 2 `plan`  →  research collectors read competitors/*.json as a SIGNAL
```

## The two passes

### 1. Static pass — deterministic, no emulator, cheap

```bash
devteam recon <project> static /path/to/app.apk
# or pull an installed package off a device/emulator:
devteam recon <project> static com.competitor.app --device <adb-serial>
```

What it extracts, with no LLM:

- manifest facts via `aapt2` / `apkanalyzer` when installed (package, version,
  min/target SDK, permissions, features, launcher activity);
- **SDK signatures** by byte-scanning the APK's dex for a generic catalogue
  (`team/recon.py` `SDK_MARKERS`): analytics, crash, ads, billing, push, auth,
  maps, health, … → grouped into **capabilities**.

Cached by `versionCode`: re-running on an unchanged version says *up to date*.
Use `--force` to refresh.

**Start here.** Static already answers "what features/integrations does it
have?" for most questions — no emulator, no tokens.

### 2. Dynamic pass — agentic, needs an emulator and you

Only for what static cannot answer (real flows, paywall gating, onboarding).

```bash
# hand the agent an exact prompt (mirrors developer-prompt):
devteam recon <project> prompt <package> --question "How is the paywall triggered?"
```

Follow the printed prompt. In short:

1. **Install & launch** the app on an emulator (Play-enabled image if it needs
   Play; otherwise side-load the APK). Start any emulator through
   `with-emulator <avd> -- <cmd...>` — teardown is guaranteed by an EXIT trap —
   and **never stop an emulator you did not start**.
2. **Read the UI tree first** (`android layout` / `uiautomator dump`) — it is
   structured and reliable. Use pixel-clicks (`computer-use` / `desktop` tools)
   **only** where the tree is empty (canvas, WebView).
3. **Ask the human** for anything needing a login/secret/payment. Never guess.
4. **Never touch any project's source code.** This plane reads behavior only.

Then return observations and merge them through code:

```bash
devteam recon <project> findings <package> --file .pipeline/competitors/<pkg>.findings.json
```

Findings file shape:

```json
{"findings": [{"question": "...", "answer": "...", "evidence": "screen/file",
               "confidence": "high|medium|low", "capability": "optional bucket"}],
 "dynamic": {"done": true, "screens": ["Home", "Paywall"], "notes": "..."},
 "questions": ["anything the human must answer"],
 "confidence": "high|medium|low"}
```

## Read side

```bash
devteam recon <project> list          # every report, one line each
devteam recon <project> show <package> # the full report JSON
```

The planner cites them next cycle via `signals.competitors`
(`capability_coverage` shows which apps have which capability — the grounded
"gap" evidence).

## Hard rules

- **Never edit project code** — this plane inspects competitors and writes only
  under `.pipeline/competitors/`.
- **Never write `plan`/`queue`/state** — that is the planner's job in the other
  plane. You produce evidence; the planner decides.
- **Never block the delivery run.** Reports are consumed *if present*; the run
  never waits for a recon.
- **Emulator hygiene** — the baseline rule from the dev-team skill applies here
  too: start through `with-emulator`, stop only what you started.
- **Observable behavior + static metadata only.** Do not patch, crack, or
  redistribute APKs; respect store terms.

## Graduating (only when an event fires)

- Manual runs feel like a chore (> ~once a week) → give the producer its own
  schedule.
- A mid-inspection question appears with no human around → that is the point to
  add a resident, addressable agent (A2A `input-required`) plus a client bridge.
- Another agent wants to call it directly → expose a typed façade (MCP tool).
- Until then, **do not** add a server or a protocol: the manual flow with a
  human in the room already covers stop-midway, answer-questions and unblock.
