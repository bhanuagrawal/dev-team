# The owner's inbox — a decision reaches the owner, not a log

The nightly runs while nobody watches. When a gate or the planner finds that the
**product itself should change** — an analytics or feedback signal that a surface
does not work, a request that contradicts the declared law, a goal nobody has set,
a walk that stopped on a human — the run correctly stops and hands the call back.

The failure this page prevents is not a wrong call. It is that the question was
printed into a log the owner never opens. So every decision the pipeline hands
back has **one address** and is mailed **once**, with the exact command that
answers it.

## Set it up (once per machine)

```bash
devteam mail set --to you@example.com --provider gmail
printf '%s' '<app-password>' | devteam mail set --password-stdin
devteam mail test
```

- `--provider` presets the relay: `gmail`, `icloud`, `fastmail`, `outlook`,
  `custom` (then pass `--host`, `--port`, `--mode`).
- The password goes to the **macOS Keychain** under `dev-team-mail`. It is never
  written to a tracked file; if the Keychain is unavailable the setup says so and
  uses a `0600` local file instead.
- Gmail and iCloud need an **app password** (Google: Security → App passwords;
  Apple: appleid.apple.com). A normal account password will be refused.
- `mail test` is not decoration: it proves the relay before a real decision
  depends on it.

The setup lives in `config/mail.local.json` (gitignored). Environment variables
override it — useful under launchd, where the login session already carries
secrets:

| variable | meaning |
|---|---|
| `DEVTEAM_MAIL_TO` | the owner's address |
| `DEVTEAM_MAIL_FROM` | `From:` (defaults to the SMTP user) |
| `DEVTEAM_SMTP_HOST` / `DEVTEAM_SMTP_PORT` / `DEVTEAM_SMTP_MODE` | the relay |
| `DEVTEAM_SMTP_USER` | SMTP login |
| `DEVTEAM_SMTP_PASSWORD` | the secret (wins over the Keychain) |
| `DEVTEAM_MAIL_CONFIG` | a different config file (tests, a second machine) |

## What gets mailed

One digest per project, containing only questions whose verdict is **OWNER** or
**MIXED** (`team/asks.py` classifies them; an ask a browser or desktop tool can do
is agent work and is never mailed):

| source | example |
|---|---|
| **proposal** | an agent used `devteam mail ask` because data says the product itself should change |
| **goal** | nobody has said what the app is for, so nothing can be built |
| **admit** | a request the gate refused because the owner decides (e.g. contradicts a clause) |
| **ask** | an open flag/question where the owner supplies one named thing |

The mail names the change, the evidence, the clause, and the command that answers
it — plus the hosted plan page so the question has context.

### Proposing a product change from data

An agent that reads analytics, reviews or a walk and concludes **the product
itself should change** records the proposal:

```bash
devteam mail ask <project> \
  --title "The pause loses half the people who meet it" \
  --question "Should the first pause be shorter, or is the 4-7-8 shape the product?" \
  --why "28 users start the breath and 13 finish; the drop is at second 12" \
  --law P3 \
  --command 'devteam admit <project> --query "<the owner\'s words>" --kind feature …'
```

Recorded, queued, and mailed — never built on the agent's assumption. `--no-send`
records it without mailing (the next nightly delivers it).

## How "once" is guaranteed

Each live decision gets an id derived from its source, title and wording. The
project ledger (`state.mail.items`) stores `sent_at`; a nightly that finds the
same open question seven nights running mails **once**. A send that fails leaves
`sent_at` empty, so the next run retries — the pipeline never calls an undelivered
ask delivered. When the underlying ask is answered (goal set, admit cleared, flag
resolved), it leaves the inbox and the ledger row is marked resolved.

## Read it, or stop it

```bash
devteam mail inbox <project>      # what needs you, sent or waiting
devteam mail inbox --all          # every app, one line each
devteam mail set --off            # mute delivery; asks are still recorded and visible
devteam mail set --on             # resume
```

`devteam status` prints a `mail:` line whenever something waits or the channel is
configured, and `devteam doctor` warns while no address is set — so "you need my
input" cannot silently expire. The nightly's last step is `devteam mail send`, the
second net for anything recorded while the relay was down.

Explicit proposals are closed with `devteam mail resolve <project> --id <id>`.
Derived asks (goal, admit, flag) are answered at their source — the inbox prints
the command.

## Limits worth knowing

- This is **send-only**. Replies are not read: the owner's answer is a chat reply
  to the agent, or the printed command. Nothing in the pipeline infers consent
  from silence, and no gate is bypassed by a mail.
- Only the decision text is mailed — no source code, no logs, no credentials.
- SMTP credentials are the owner's; the agent never prints the password. If the
  relay rejects the login, the error names the host and the fix, not the secret.

---

**NEXT:** `devteam mail test` — prove the relay, then every `mail:` line in
`status` means the owner can actually be reached.
