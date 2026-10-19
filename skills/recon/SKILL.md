# recon — install a competitor app on the emulator (manual, by hand)

name: recon
description: >
  Get a competitor's Android app onto an emulator through the Play Store, the way
  a user would — search the store, install it, then use it. MANUAL and separate
  from the nightly run. Use when the owner says "check this app", "look at what a
  rival does", or hands over a package id to install.

## What this is (and is not)

- **A second workflow.** The delivery plane (`dev-team` skill, nightly run) ships
  one task to internal testing. This plane gets a rival app installed so a human
  or an agent can actually use it. They share nothing: this writes no reports and
  never touches project code.
- **Manual.** No schedule, no daemon — a human is present.
- **It installs; it does not judge.** Nothing here reads the app's store metadata,
  scans its APK, or records findings. Using the app and deciding what it means is
  the agent's (or the owner's) job.

## Flow

```bash
android emulator start <avd>                         # start once, keep it for the session
devteam recon <project> play --term "forest focus"   # search the Store; lists the visible rows
devteam recon <project> install com.rival.app        # Play details → tap Install → wait
# then USE it: launch the app, read the UI tree, ask the human for logins
android emulator stop <avd>                          # you started it; you stop it
```

`install` is deterministic: it finds the emulator, opens the app's Play page,
reads the accessibility tree, taps Install (or Update), clears Play's own dialogs
and waits for the package to land. It prints the version it installed and writes
nothing.

Prefer `install <package>`. When you only know the app's *name*, `play --term`
lists the visible rows so you can pick the exact package id — installing the
wrong id installs the wrong app.

## The one thing it will never do: sign in

A Play install needs the emulator signed into a Google account, and automated
sign-in trips 2FA / device verification. So when the emulator is not signed in —
or Play throws a CAPTCHA, or the image has no Play Store at all — the command
stops with an **OWNER ASK** (rc=4) saying exactly what to do. You sign in **on the
emulator window**, once, and it sticks in the AVD.

**Never type a Google password into a prompt.** The ask is the feature, not a
limitation: an agent holding an account password is a worse failure than a failed
install.

## Driving the app afterwards (an agent's job, not a command)

1. Launch the app. **Read the UI tree first** (`android layout`) — structured and
   reliable. Use pixel-clicks (`computer-use` / `desktop` tools) **only** where
   the tree is empty (canvas, WebView), and take a screenshot (`android screen
   capture`) before clicking blind.
2. **Ask the human** for anything needing a login, a secret or a payment.
3. **Never touch any project's source code.** Read behavior only.

## Hard rules

- **Never edit project code**, and never write planner state (`plan`/`queue`).
- **Emulator hygiene** — start it, and stop only what you started.
  `with-emulator <avd> -- <cmd>` does both for a single command; if you keep one
  up across the session, `android emulator stop <avd>` before you finish.
- **Never type a Google password.**
- **Observable behavior only.** Do not patch, crack or redistribute APKs; respect
  store terms.

## Graduating (only when an event fires)

- You want a durable record of what a rival does, rather than a working install →
  that is a different design, and nothing here does it today.
- Unattended installs (nobody in the room to clear a sign-in) → that is the
  moment to think about a resident agent, and not before.
