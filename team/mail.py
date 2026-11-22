"""OWNER MAIL — the pipeline asking the owner to intervene, on purpose.

Why this exists (owner ask 2026-11-22)
--------------------------------------
The nightly runs while nobody watches. When a gate or the planner finds that the
PRODUCT ITSELF should change — a contradiction with the law, data that says a
surface does not work, a goal nobody has set, a walk that stopped on a human —
the run correctly stops and hands the call back. The failure mode is not a wrong
decision; it is that the question is printed into a log the owner never opens.

So an owner decision gets one address, one message, and one exact command that
answers it. This module owns three things and nothing else:

  * TRANSPORT — the official Gmail API with OAuth (`devteam mail connect`), or
    any SMTP relay for a non-Google account. Nothing here asks the owner for an
    app password: a Google app password is a weaker credential than an OAuth
    refresh token and Google is retiring the flow. The consent is one browser
    round-trip; the refresh token goes to the macOS Keychain, never a file.
  * COLLECT — every open escalation the pipeline can derive from the project's
    own ledgers (goal missing, admit flag, owner/mixed ask, explicit proposal).
  * SEND — ONE digest per project, deduplicated, marked sent in the ledger, so a
    nightly that finds the same open question seven nights running does not mail
    seven times. A send that fails leaves the ask UNSENT: never mark it delivered.

Determinism: no model, no project names, no network except the one send. Every
function that can fail returns a reason instead of raising, because the owner
channel must never be the reason a run dies.

The one rule this module enforces on the other side: it only mails what
`team.asks` classifies as OWNER or MIXED. An ask a machine can do (the owner's
logged-in browser, a desktop tool) is agent work, and mailing it is how the owner
ends up doing an agent's job.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import smtplib
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .core import TEAM_ROOT, now_iso, profile_of, save_state, state_of

CONFIG_NAME = "mail.local.json"
KEYCHAIN_SERVICE = "dev-team-mail"
OAUTH_KEYCHAIN_SERVICE = "dev-team-mail-oauth"

# Google's official send path. `gmail.send` is the one scope that can compose and
# send a message and read NOTHING: the pipeline cannot see the owner's inbox.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/userinfo.email",
                "openid"]
GMAIL_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
DEFAULT_OAUTH_PORT = 8765

# provider presets — a name is a shortcut for three fields, never a lock-in
PROVIDERS: dict[str, dict[str, Any]] = {
    # Google is OAuth, not a password: `devteam mail connect` performs the
    # consent and the transport is the Gmail API.
    "gmail": {"host": "gmail.googleapis.com", "port": 443, "mode": "https",
              "transport": "gmail-api", "note": "authorised once with `devteam mail connect`"},
    "icloud": {"host": "smtp.mail.me.com", "port": 587, "mode": "starttls",
               "transport": "smtp",
               "note": "needs an app-specific password from appleid.apple.com"},
    "fastmail": {"host": "smtp.fastmail.com", "port": 465, "mode": "ssl",
                 "transport": "smtp",
                 "note": "needs an app password from Fastmail → Settings → Privacy"},
    "outlook": {"host": "smtp-mail.outlook.com", "port": 587, "mode": "starttls",
                "transport": "smtp", "note": "may require an app password"},
    "custom": {"host": "", "port": 587, "mode": "starttls", "transport": "smtp",
               "note": ""},
}

_ENV = {
    "DEVTEAM_MAIL_TO": "to",
    "DEVTEAM_MAIL_FROM": "from",
    "DEVTEAM_SMTP_HOST": "host",
    "DEVTEAM_SMTP_PORT": "port",
    "DEVTEAM_SMTP_MODE": "mode",
    "DEVTEAM_SMTP_USER": "user",
}
_SECRET_ENV = "DEVTEAM_SMTP_PASSWORD"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MailError(RuntimeError):
    """A send that did not happen, with the reason a person can act on."""


# --------------------------------------------------------------------------- #
# config — env wins over the file, so launchd can carry the whole setup
# --------------------------------------------------------------------------- #
def config_path() -> Path:
    """Where the local setup lives. `DEVTEAM_MAIL_CONFIG` lets a test (or a
    second machine) point somewhere else without touching the real one."""
    override = (os.environ.get("DEVTEAM_MAIL_CONFIG") or "").strip()
    return Path(override) if override else (TEAM_ROOT / "config" / CONFIG_NAME)


def load_config() -> dict:
    """The effective setup: file first, then any DEVTEAM_* variable on top."""
    cfg: dict[str, Any] = {}
    path = config_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg = raw
        except Exception:
            cfg = {}
    for env, key in _ENV.items():
        val = (os.environ.get(env) or "").strip()
        if val:
            cfg[key] = val
    if cfg.get("port"):
        try:
            cfg["port"] = int(cfg["port"])
        except (TypeError, ValueError):
            pass
    if str(cfg.get("enabled", "true")).strip().lower() in ("0", "false", "no", "off"):
        cfg["enabled"] = False
    else:
        cfg.setdefault("enabled", True)
    return cfg


def _save_config(cfg: dict) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------- #
# the password — Keychain first, file (0600) only when there is no Keychain
# --------------------------------------------------------------------------- #
def _security(*args: str, stdin: str | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(["security", *args], capture_output=True,
                              text=True, input=stdin, timeout=20)
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or "").strip() or (proc.stderr or "").strip()


def _keychain_get(service: str, account: str) -> str:
    if not account:
        return ""
    rc, out = _security("find-generic-password", "-s", service, "-a", account, "-w")
    return out if rc == 0 else ""


def _keychain_set(service: str, account: str, value: str) -> bool:
    if not account:
        return False
    rc, _ = _security("add-generic-password", "-U", "-s", service,
                      "-a", account, "-w", value)
    return rc == 0


def keychain_get(account: str) -> str:
    return _keychain_get(KEYCHAIN_SERVICE, account)


def keychain_set(account: str, password: str) -> bool:
    return _keychain_set(KEYCHAIN_SERVICE, account, password)


def password_for(cfg: dict) -> str:
    """env → Keychain → file. A secret is never printed by any caller."""
    env = (os.environ.get(_SECRET_ENV) or "").strip()
    if env:
        return env
    ref = cfg.get("password_ref") or {}
    if isinstance(ref, dict) and ref.get("kind") == "keychain":
        got = keychain_get(str(ref.get("account") or ""))
        if got:
            return got
    return str(cfg.get("password") or "")


# --------------------------------------------------------------------------- #
# the official Google path — OAuth consent once, refresh token in the Keychain
# --------------------------------------------------------------------------- #
def oauth_account(cfg: dict) -> str:
    """Whose Google account is authorised: the From/user address."""
    return str(cfg.get("user") or cfg.get("from") or cfg.get("to") or "").strip()


def gmail_credential(cfg: dict) -> dict:
    """The refresh token the one-time consent stored (Keychain, never a file)."""
    raw = _keychain_get(OAUTH_KEYCHAIN_SERVICE, oauth_account(cfg))
    if not raw:
        return {}
    # `security -w` hex-encodes a value with newlines in it, so a credential
    # written with indentation comes back as hex — accept both spellings.
    text = raw.strip()
    if text and all(ch in "0123456789abcdef" for ch in text.lower()) and len(text) % 2 == 0:
        try:
            text = bytes.fromhex(text).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            text = raw
    try:
        cred = json.loads(text)
    except Exception:
        return {}
    if not (cred.get("client_id") and cred.get("client_secret")
            and cred.get("refresh_token")):
        return {}
    return cred


def oauth_client(cfg: dict | None = None) -> tuple[str, str]:
    """(client_id, client_secret) for the consent flow, from the local config."""
    cfg = load_config() if cfg is None else cfg
    block = cfg.get("oauth") or {}
    if not isinstance(block, dict):
        return "", ""
    return str(block.get("client_id") or ""), str(block.get("client_secret") or "")


def transport_for(cfg: dict) -> str:
    """gmail-api when the setup says so, else SMTP. Never guessed from a host."""
    explicit = str(cfg.get("transport") or "").strip().lower()
    if explicit in ("gmail-api", "smtp"):
        return explicit
    preset = PROVIDERS.get(str(cfg.get("provider") or "").lower()) or {}
    if preset.get("transport") == "gmail-api" and gmail_credential(cfg):
        return "gmail-api"
    return "smtp"


def _post_form(url: str, fields: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def gmail_access_token(cred: dict) -> str:
    """Mint a short-lived access token from the refresh token; raises MailError."""
    try:
        tok = _post_form(GMAIL_TOKEN_URL, {
            "client_id": cred["client_id"], "client_secret": cred["client_secret"],
            "grant_type": "refresh_token", "refresh_token": cred["refresh_token"]})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        exc.close()
        if "invalid_grant" in detail:
            raise MailError("the Gmail authorisation was revoked or expired — "
                            "run `devteam mail connect` again") from exc
        raise MailError(f"Gmail token exchange failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise MailError(f"cannot reach Google to renew the Gmail token: {exc}") from exc
    token = str(tok.get("access_token") or "")
    if not token:
        raise MailError("Google returned no access token for the saved authorisation")
    return token


def _mime(to: str, sender: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(body)
    return msg


def send_gmail(cfg: dict, subject: str, body: str, to: str = "") -> dict:
    """Send with the Gmail API — the same official API Claude/Gemini connectors use."""
    cred = gmail_credential(cfg)
    if not cred:
        raise MailError("no Gmail authorisation is stored — run `devteam mail connect`")
    recipient = (to or str(cfg.get("to") or "")).strip()
    sender = str(cfg.get("from") or cred.get("account") or recipient).strip()
    token = gmail_access_token(cred)
    msg = _mime(recipient, sender, subject, body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    payload = json.dumps({"raw": raw}).encode()
    req = urllib.request.Request(GMAIL_SEND_URL, data=payload, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            got = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        exc.close()
        raise MailError(f"Gmail refused the message ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise MailError(f"cannot reach the Gmail API: {exc}") from exc
    return {"status": "sent", "to": recipient, "subject": subject,
            "message_id": str(got.get("id") or ""), "host": "gmail.googleapis.com",
            "port": 443, "transport": "gmail-api"}


def connect(*, port: int = DEFAULT_OAUTH_PORT, timeout: int = 600,
            out=print) -> dict:
    """One consent round-trip: print the URL, catch the loopback code, store it.

    This is the ONLY step that needs a person (a Google consent screen). Everything
    after it is unattended: the refresh token lives in the Keychain and every
    nightly mints its own access token. No app password, no SMTP login.
    """
    cfg = load_config()
    client_id, client_secret = oauth_client(cfg)
    if not client_id or not client_secret:
        return {"ok": False, "why": "no OAuth client is configured — run "
                                    "`devteam mail set --oauth-client-id … "
                                    "--oauth-client-secret …` (Google Cloud → "
                                    "Google Auth Platform → Clients → Desktop app)"}
    account = oauth_account(cfg)
    if not account:
        return {"ok": False, "why": "no account — run `devteam mail set --to …` first"}
    holder: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:      # noqa: N802 (http.server's own name)
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs:
                holder["code"] = qs["code"][0]
                body = b"dev-team: consent received - you can close this tab."
                self.send_response(200)
            elif "error" in qs:
                holder["error"] = (qs.get("error_description") or qs["error"])[0]
                body = b"dev-team: consent was not granted."
                self.send_response(400)
            else:
                body = b"dev-team: waiting"
                self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if "code" in qs or "error" in qs:
                done.set()

        def log_message(self, *a) -> None:
            pass

    redirect = f"http://127.0.0.1:{port}/"
    url = GMAIL_AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES), "access_type": "offline",
        "prompt": "consent", "login_hint": account})
    try:
        srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        return {"ok": False, "why": f"cannot listen on 127.0.0.1:{port} ({exc}) — "
                                    f"try `--port 8766`"}
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    out("Open this URL, sign in as %s, and allow dev-team to send mail:" % account)
    out("")
    out(url)
    out("")
    out("Waiting for the consent…")
    try:
        if not done.wait(timeout):
            return {"ok": False, "why": f"no consent within {timeout}s — run it again"}
    finally:
        srv.shutdown()
    if holder.get("error"):
        return {"ok": False, "why": f"consent was not granted: {holder['error']}"}
    try:
        tok = _post_form(GMAIL_TOKEN_URL, {
            "client_id": client_id, "client_secret": client_secret,
            "code": holder["code"], "grant_type": "authorization_code",
            "redirect_uri": redirect})
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "why": f"token exchange failed: {exc}"}
    refresh = str(tok.get("refresh_token") or "")
    if not refresh:
        return {"ok": False, "why": "Google returned no refresh token (revoke the "
                                    "app's access and retry the consent)"}
    granted = str(tok.get("scope") or "")
    missing = [s for s in GMAIL_SCOPES if s not in granted and s != "openid"]
    cred = {"client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh, "scope": granted, "account": account}
    if not _keychain_set(OAUTH_KEYCHAIN_SERVICE, account, json.dumps(cred)):
        return {"ok": False, "why": "the Keychain refused the credential"}
    cfg["transport"] = "gmail-api"
    cfg.setdefault("provider", "gmail")
    _save_config(cfg)
    if missing:                        # the consent screen can silently drop a scope
        return {"ok": False, "why": "consent did not include " + ", ".join(missing)
                                    + " — retry and tick every scope"}
    try:
        gmail_access_token(cred)
    except MailError as exc:
        return {"ok": False, "why": str(exc)}
    return {"ok": True, "why": "", "scopes": granted, "account": account}


def set_config(*, to: str = "", from_: str = "", provider: str = "",
               host: str = "", port: int | None = None, mode: str = "",
               user: str = "", password: str = "", enabled: bool | None = None,
               note: str = "", transport: str = "", oauth_client_id: str = "",
               oauth_client_secret: str = "") -> dict:
    """Write the local setup. Returns {ok, why, cfg, path, password_stored}.

    A password is stored in the Keychain under the relay account; when there is
    no usable Keychain it is written to the 0600 file and the result SAYS so, so
    "where is my password?" is answerable without guessing. The Google path
    stores an OAuth *client* here (an installed-app identifier, 0600) and the
    refresh token in the Keychain — see `connect()`.
    """
    cfg = load_config()
    prov = (provider or cfg.get("provider") or ("gmail" if not cfg.get("host") else "custom")).lower()
    preset = PROVIDERS.get(prov) or PROVIDERS["custom"]
    cfg["provider"] = prov
    if to:
        to = to.strip()
        if not _EMAIL_RE.match(to):
            return {"ok": False, "why": f"{to!r} is not an email address", "cfg": cfg}
        cfg["to"] = to
    if from_:
        cfg["from"] = from_.strip()
    if host:
        cfg["host"] = host.strip()
    elif not cfg.get("host"):
        cfg["host"] = preset["host"]
    if port:
        cfg["port"] = int(port)
    elif not cfg.get("port"):
        cfg["port"] = preset["port"]
    if mode:
        cfg["mode"] = mode.lower()
    elif not cfg.get("mode"):
        cfg["mode"] = preset["mode"]
    if user:
        cfg["user"] = user.strip()
    elif not cfg.get("user"):
        cfg["user"] = cfg.get("from") or cfg.get("to") or ""
    cfg.setdefault("from", cfg.get("user") or cfg.get("to") or "")
    if transport:
        cfg["transport"] = transport.strip().lower()
    elif not cfg.get("transport"):
        cfg["transport"] = preset.get("transport") or "smtp"
    if oauth_client_id or oauth_client_secret:
        block = cfg.get("oauth") if isinstance(cfg.get("oauth"), dict) else {}
        if oauth_client_id:
            block["client_id"] = oauth_client_id.strip()
        if oauth_client_secret:
            block["client_secret"] = oauth_client_secret.strip()
        cfg["oauth"] = block
    if note:
        cfg["note"] = note
    if enabled is not None:
        cfg["enabled"] = bool(enabled)

    stored = "unchanged"
    if password:
        account = str(cfg.get("user") or cfg.get("to") or "")
        if keychain_set(account, password):
            cfg["password_ref"] = {"kind": "keychain", "service": KEYCHAIN_SERVICE,
                                   "account": account}
            cfg.pop("password", None)
            stored = "keychain"
        else:
            cfg["password"] = password
            cfg.pop("password_ref", None)
            stored = "file"

    path = _save_config(cfg)
    return {"ok": True, "why": "", "cfg": cfg, "path": path, "password_stored": stored}


def masked(address: str) -> str:
    addr = (address or "").strip()
    if "@" not in addr:
        return addr or "(none)"
    user, domain = addr.split("@", 1)
    keep = user[0] if user else ""
    return f"{keep}{'*' * max(1, len(user) - 1)}@{domain}"


def status(cfg: dict | None = None) -> dict:
    """Ready or the one thing missing, never a secret."""
    cfg = load_config() if cfg is None else cfg
    transport = transport_for(cfg)
    why = ""
    if not cfg.get("enabled", True):
        why = "muted (owner mail is switched off)"
    elif not cfg.get("to"):
        why = "no recipient is set"
    elif transport == "gmail-api":
        cred = gmail_credential(cfg)
        if not cred:
            why = ("Google is not authorised yet — run `devteam mail connect` "
                   "(one consent screen, no app password)")
        elif not str(cfg.get("from") or "").strip():
            cfg["from"] = str(cred.get("account") or cfg.get("to") or "")
    elif not cfg.get("host"):
        why = "no SMTP relay is set"
    elif cfg.get("user") and not password_for(cfg):
        why = (f"no SMTP password is set ({_SECRET_ENV}, or --password-stdin)")
    return {
        "configured": not why,
        "why": why,
        "to": str(cfg.get("to") or ""),
        "to_masked": masked(str(cfg.get("to") or "")),
        "from": str(cfg.get("from") or ""),
        "host": str(cfg.get("host") or ""),
        "port": cfg.get("port") or 587,
        "mode": str(cfg.get("mode") or "starttls"),
        "provider": str(cfg.get("provider") or ""),
        "transport": transport,
        "enabled": bool(cfg.get("enabled", True)),
    }


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
def send(cfg: dict, subject: str, body: str, to: str = "",
         dry_run: bool = False) -> dict:
    """Send ONE message. Raises MailError with what to fix; never retries.

    Google goes through the Gmail API with a refreshed OAuth token; anything else
    is SMTP. `dry_run` returns before either, so a rehearsal never sends mail.
    """
    st = status(cfg)
    if not st["configured"]:
        raise MailError(st["why"])
    recipient = (to or cfg.get("to") or "").strip()
    if dry_run:
        return {"status": "dry-run", "to": recipient, "subject": subject}
    if st["transport"] == "gmail-api":
        return send_gmail(cfg, subject, body, to=recipient)

    sender = (cfg.get("from") or cfg.get("user") or recipient).strip()
    msg = _mime(recipient, sender, subject, body)
    host = str(cfg["host"])
    port = int(cfg.get("port") or 587)
    mode = str(cfg.get("mode") or "starttls").lower()
    context = ssl.create_default_context()
    try:
        if mode == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server:
            server.ehlo()
            if mode == "starttls":
                server.starttls(context=context)
                server.ehlo()
            if cfg.get("user"):
                server.login(str(cfg["user"]), password_for(cfg))
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"{host}:{port} — {exc}") from exc
    return {"status": "sent", "to": recipient, "subject": subject,
            "message_id": str(msg.get("Message-ID") or ""), "host": host,
            "port": port, "transport": "smtp"}


# --------------------------------------------------------------------------- #
# what the pipeline is asking the owner
# --------------------------------------------------------------------------- #
def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _item_id(project_name: str, source: str, title: str, question: str) -> str:
    raw = "\0".join([project_name, source, _normalise(title), _normalise(question)])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _item(project_name: str, source: str, title: str, question: str,
          why: str = "", command: str = "", law: str = "") -> dict:
    title = (title or "?").strip()
    question = (question or why or "").strip()
    return {
        "id": _item_id(project_name, source, title, question),
        "source": source,
        "title": title,
        "question": question,
        "why": (why or "").strip(),
        "command": (command or "").strip(),
        "law": (law or "").strip(),
        "at": now_iso(),
    }


def collect(project: Path) -> list[dict]:
    """Every open escalation only the owner can answer, in the owner's words.

    Sources, in the order a reader should meet them:
      proposal — an agent's data-driven "the product itself should change", said
                 explicitly with `devteam mail ask`;
      goal     — nobody has said what the app is for (nothing can be built);
      admit    — a request the gate refused because the owner decides;
      ask      — an open flag/question whose review verdict is OWNER or MIXED.
    """
    profile, state = profile_of(project), state_of(project)
    rows: list[dict] = []
    seen: set[str] = set()

    def add(item: dict) -> None:
        base = _item_id(project.name, item["source"], item["title"], item["question"])
        if base in seen:
            return
        seen.add(base)
        rows.append(item)

    box = state.get("mail") or {}
    for a in box.get("asks") or []:
        if a.get("resolved_at"):
            continue
        add(_item(project.name, "proposal", a.get("title") or "?", a.get("question") or "",
                  why=a.get("why") or "", command=a.get("command") or "",
                  law=a.get("law") or ""))

    try:
        from . import goals
        for a in goals.owner_asks(profile, state, project.name):
            add(_item(project.name, "goal", a.get("what") or "?",
                      a.get("what") or "", command=a.get("how") or ""))
    except (Exception, SystemExit):
        pass

    for e in state.get("admit_log") or []:
        if str(e.get("who") or "") != "owner":
            continue
        add(_item(project.name, "admit",
                  "A request needs your call: " + str(e.get("question") or "")[:80],
                  str(e.get("question") or ""),
                  why=(f"the gate refused it ({e.get('verdict') or 'not aligned'})"),
                  command=str(e.get("next") or ""),
                  law=str(e.get("law") or "")))

    try:
        from . import asks
        for row in asks.review_project(project):
            if row.get("verdict") not in ("owner", "mixed"):
                continue
            title = str(row.get("title") or "?")
            extra = ""
            if row.get("verdict") == "mixed" and row.get("owner_input"):
                extra = " (you supply: " + "; ".join(row["owner_input"]) + ")"
            add(_item(project.name, "ask", title,
                      title + extra,
                      why=str(row.get("why") or ""),
                      command=str(row.get("run") or "")))
    except (Exception, SystemExit):
        pass
    return rows


def _record(project: Path, items: list[dict], state: dict) -> list[dict]:
    """Upsert live items into the ledger, resolve the ones that no longer live.

    Returns the live records in the same order, each carrying its send history.
    """
    box = state.setdefault("mail", {})
    records = box.setdefault("items", {})
    live_ids = {it["id"] for it in items}
    out: list[dict] = []
    for it in items:
        rec = records.get(it["id"]) or {}
        for key in ("source", "title", "question", "why", "command", "law"):
            rec[key] = it.get(key) or ""
        rec["id"] = it["id"]
        rec.setdefault("first_seen", it.get("at") or now_iso())
        rec["last_seen"] = now_iso()
        rec.pop("resolved_at", None)
        records[it["id"]] = rec
        out.append(rec)
    for rid, rec in records.items():
        if rid not in live_ids and not rec.get("resolved_at"):
            rec["resolved_at"] = now_iso()
    return out


def _digest(project: Path, items: list[dict]) -> tuple[str, str]:
    name = project.name
    n = len(items)
    subject = (f"[dev-team] {name}: {n} decision{'s' if n != 1 else ''} need"
               f"{'' if n != 1 else 's'} you")
    lines = [
        f"{name} stopped at a decision only you can make.",
        "",
        "Nothing was queued or built around the question. Each item below names the",
        "change the pipeline thinks the product needs, and the command that answers it.",
        "",
    ]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it.get('title') or '?'}")
        if it.get("question") and _normalise(it["question"]) != _normalise(it["title"]):
            lines.append(f"   why: {it['question']}")
        elif it.get("why"):
            lines.append(f"   why: {it['why']}")
        if it.get("why") and it.get("question") and it.get("why") != it.get("question"):
            lines.append(f"   gate: {it['why']}")
        if it.get("law"):
            lines.append(f"   law: {it['law']}")
        if it.get("command"):
            lines.append(f"   answer: {it['command']}")
        lines.append("")
    try:
        from . import publish
        url = publish.pages_url(project, profile_of(project))
        if url:
            lines += [f"The plan this question sits inside: {url}", ""]
    except Exception:
        pass
    lines += [
        f"See it again anytime:  devteam mail inbox {name}",
        f"Reply to the agent that asked, or run the command above; the next run picks it up.",
        "",
        "— dev-team (automated). Mute these mails: devteam mail set --off",
    ]
    return subject, "\n".join(lines)


def send_pending(project: Path, *, force: bool = False, dry_run: bool = False,
                 quiet: bool = False) -> dict:
    """Mail every NEW owner decision for one project, once.

    Returns {status, waiting, sent, why, to, subject}. Status is one of
    `nothing-new`, `unconfigured`, `sent`, `dry-run`, `error`. A failure never
    raises and never marks the ask delivered, so the next run retries it.
    """
    cfg = load_config()
    st = status(cfg)
    state = state_of(project)
    items = collect(project)
    records = _record(project, items, state)
    pending = [r for r in records if force or not r.get("sent_at")]

    def finish(result: dict) -> dict:
        try:
            save_state(project, state)
        except Exception as exc:     # a ledger write must not hide the send result
            if not quiet:
                print(f"mail: ledger write failed ({exc})", file=sys.stderr)
        return result

    if not items:
        return finish({"status": "nothing-new", "waiting": 0, "sent": 0,
                       "to": st["to_masked"], "why": ""})
    if not st["configured"]:
        return finish({"status": "unconfigured", "waiting": len(pending), "sent": 0,
                       "to": "", "why": st["why"]})
    if not pending:
        return finish({"status": "nothing-new", "waiting": 0, "sent": 0,
                       "to": st["to_masked"], "why": ""})

    subject, body = _digest(project, pending)
    try:
        res = send(cfg, subject, body, dry_run=dry_run)
    except MailError as exc:
        for rec in pending:
            rec["last_error"] = str(exc)
            rec["last_attempt"] = now_iso()
        return finish({"status": "error", "waiting": len(pending), "sent": 0,
                       "to": st["to_masked"], "why": str(exc), "subject": subject})
    if res.get("status") == "dry-run":
        # A rehearsal must not mark a decision delivered — that would be the one
        # way a real question silently never reaches the owner.
        return finish({"status": "dry-run", "waiting": len(pending), "sent": 0,
                       "to": st["to_masked"], "why": "", "subject": subject})
    stamp = now_iso()
    for rec in pending:
        rec["sent_at"] = stamp
        rec["to"] = res.get("to") or st["to"]
        rec["subject"] = subject
        rec.pop("last_error", None)
    state.setdefault("mail", {})["last_sent_at"] = stamp
    return finish({"status": res.get("status") or "sent", "waiting": 0,
                   "sent": len(pending),
                   "to": st["to_masked"], "why": "", "subject": subject})


def inbox(project: Path) -> dict:
    """The live owner decisions for a project, joined with what was mailed."""
    state = state_of(project)
    records = (state.get("mail") or {}).get("items") or {}
    rows = []
    for it in collect(project):
        rec = records.get(it["id"]) or {}
        rows.append({**it, "sent_at": rec.get("sent_at") or "",
                     "to": rec.get("to") or "",
                     "error": rec.get("last_error") or ""})
    rows.sort(key=lambda r: (bool(r["sent_at"]), r.get("at") or ""), reverse=False)
    st = status()
    return {"project": project.name, "configured": st["configured"],
            "to": st["to_masked"], "why": st["why"], "items": rows}


def add_proposal(project: Path, *, title: str, question: str, why: str = "",
                 command: str = "", law: str = "") -> dict:
    """Record an agent's data-driven proposal that the PRODUCT ITSELF should change.

    This is the explicit verb the planner uses when analytics, reviews or a walk
    point at a change no clause already decides. It does NOT queue anything: the
    proposal is mailed, and only the owner's answer turns it into work.
    """
    title, question = title.strip(), question.strip()
    if not title or not question:
        return {"ok": False, "why": "both --title and --question are required"}
    state = state_of(project)
    box = state.setdefault("mail", {})
    asks = box.setdefault("asks", [])
    item = _item(project.name, "proposal", title, question, why=why,
                 command=command, law=law)
    if not any(a.get("id") == item["id"] for a in asks):
        asks.append(item)
        save_state(project, state)
    return {"ok": True, "item": item}


def resolve(project: Path, item_id: str) -> dict:
    """Close an explicit proposal. Derived asks are answered at their source."""
    state = state_of(project)
    box = state.setdefault("mail", {})
    asks = box.get("asks") or []
    for a in asks:
        if a.get("id") == item_id:
            asks.remove(a)
            rec = (box.setdefault("items", {}).get(item_id) or {})
            rec["resolved_at"] = now_iso()
            box["items"][item_id] = rec
            save_state(project, state)
            return {"ok": True, "title": a.get("title") or ""}
    return {"ok": False, "why": (
        f"no open proposal with id {item_id!r} — a goal/admit/ask item is answered "
        "by its own command (see `devteam mail inbox <project>`)")}


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def format_status(st: dict) -> str:
    lines = ["OWNER MAIL — where a decision that needs you is delivered", ""]
    if st["configured"]:
        lines.append(f"  status:   ready → {st['to_masked']}")
        if st["transport"] == "gmail-api":
            lines.append("  relay:    Gmail API — official OAuth, authorised in the "
                         "Keychain (no app password)")
        else:
            lines.append(f"  relay:    {st['host']}:{st['port']} ({st['mode']})")
        lines.append(f"  from:     {st['from'] or '(same as recipient)'}")
    else:
        lines.append("  status:   NOT SET — escalations are recorded, not delivered")
        lines.append(f"  missing:  {st['why']}")
        lines.append("  set it:   devteam mail set --to you@example.com --provider gmail"
                     " → devteam mail connect")
    lines.append("  env:      DEVTEAM_MAIL_TO / DEVTEAM_SMTP_HOST / DEVTEAM_SMTP_USER"
                 " / DEVTEAM_SMTP_PASSWORD")
    lines.append("  guide:    docs/mail.md — what gets mailed, and how to stop it")
    return "\n".join(lines)


def format_inbox(data: dict) -> str:
    name = data["project"]
    rows = data["items"]
    lines = [f"OWNER INBOX — {name}", "=" * 66]
    if data["configured"]:
        lines.append(f"  delivering to: {data['to']}")
    else:
        lines.append(f"  NOT delivered — {data['why']}")
    if not rows:
        lines.append("  nothing needs you")
        return "\n".join(lines)
    lines.append("")
    unsent = [r for r in rows if not r["sent_at"]]
    for r in rows:
        mark = "sent" if r["sent_at"] else "WAIT"
        lines.append(f"  [{mark}] {r['title']}")
        if r.get("question") and r["question"] != r["title"]:
            lines.append(f"         why: {r['question']}")
        if r.get("law"):
            lines.append(f"         law: {r['law']}")
        if r.get("command"):
            lines.append(f"         answer: {r['command']}")
        if r.get("error"):
            lines.append(f"         last send failed: {r['error']}")
        lines.append(f"         id: {r['id']}")
    if unsent:
        lines.append("")
        lines.append(f"  {len(unsent)} waiting — `devteam mail send {name}`")
    return "\n".join(lines)


def selftest() -> list[str]:
    """Cheap invariants for `doctor`/tests: no config is still a valid state."""
    out = []
    st = status({"to": "a@b.co", "host": "h", "port": 587, "mode": "starttls",
                 "user": "a@b.co", "password": "x", "enabled": True})
    if not st["configured"]:
        out.append("a complete config did not read as configured")
    if masked("a@b.co") != "a***@b.co":
        out.append("mask leaks the local part")
    if transport_for({"provider": "gmail", "to": "a@b.co", "user": "a@b.co"}) != "smtp":
        out.append("gmail transport chosen without an authorisation")
    return out
