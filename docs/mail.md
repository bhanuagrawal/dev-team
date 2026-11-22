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

Google goes through the **official Gmail API with OAuth** — the same connection
Claude and Gemini use, not an app password. One consent screen, then the pipeline
mints its own short-lived access token on every run.

```bash
devteam mail set --to you@gmail.com --provider gmail \
  --oauth-client-id '<id>.apps.googleusercontent.com' \
  --oauth-client-secret '<secret>'
devteam mail connect      # prints a URL, catches the loopback code
devteam mail test
```

The one-time Google Cloud setup, needed only when the client id/secret are not
already in `config/mail.local.json` (a new machine):

1. Create a project and **enable the Gmail API**.
2. Google Auth Platform → **Get started** → External, app name, support email,
   developer contact.
3. **Data access** → add the scope `https://www.googleapis.com/auth/gmail.send`.
4. **Clients** → Create client → **Desktop app** → copy the id and the secret.
5. `devteam mail set … --oauth-client-id … --oauth-client-secret …`, then
   `devteam mail connect`.
6. **Audience → Publish app.** While the app sits in *Testing*, Google expires the
   refresh token after 7 days; in production it survives. An unverified app shows
   "Google hasn't verified this app" — expected for a personal client, and the
   consent still works for the addresses listed as test users.

Where the two secrets live:

- the **refresh token** is written to the macOS Keychain under
  `dev-team-mail-oauth`, keyed by the account address — never a file;
- the **client id/secret** are an installed-app identifier written to
  `config/mail.local.json` (`0600`, gitignored).

`gmail.send` can compose and send and read **nothing**: the pipeline cannot see
the inbox it posts to. `devteam mail connect` says so when the consent screen
silently withholds the scope — retry and tick every box.

### A non-Google address

```bash
devteam mail set --to you@example.com --provider icloud   # or fastmail/outlook/custom
printf '%s' '<app-password>' | devteam mail set --password-stdin
devteam mail test
```

- `--provider` presets the relay: `gmail` (the OAuth path above), `icloud`,
  `fastmail`, `outlook`, `custom` (then pass `--host`, `--port`, `--mode`).
- On every non-Google relay the password goes to the **macOS Keychain** under
  `dev-team-mail`. It is never written to a tracked file; if the Keychain is
  unavailable the setup says so and uses a `0600` local file instead.
- iCloud and Fastmail need an **app-specific password**; a normal account
  password will be refused.
- `mail test` is not decoration: it proves the transport before a real decision
  depends on it.

The setup lives in `config/mail.local.json` (gitignored). Environment variables
override it — useful under launchd, where the login session already carries
secrets. On the Gmail path only the first three apply:

| variable | meaning |
|---|---|
| `DEVTEAM_MAIL_TO` | the owner's address |
| `DEVTEAM_MAIL_FROM` | `From:` (defaults to the authorised account) |
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

- This is **send-only**. `gmail.send` cannot read the inbox, and replies are not
  read: the owner's answer is a chat reply to the agent, or the printed command.
  Nothing in the pipeline infers consent from silence, and no gate is bypassed by
  a mail.
- Only the decision text is mailed — no source code, no logs, no credentials.
- The credentials are the owner's: an OAuth refresh token in the Keychain on the
  Google path, an app password on an SMTP relay. The agent never prints either;
  a failure names the host and the fix, not the secret.
- If Google says `invalid_grant`, the authorisation was revoked or expired (a
  password change, a *Testing* app's 7-day token, or the owner removing access):
  `devteam mail connect` again. Nothing else needs redoing.

---

**NEXT:** `devteam mail test` — prove the channel, then every `mail:` line in
`status` means the owner can actually be reached.
