"""GA4 read access: ONE machine credential, verified by READING the property.

Why this module exists (2026-11-05): the fallback credential used to be the
owner's Application Default Credentials, minted with
`gcloud auth application-default login --scopes=…analytics.readonly`. Google
stopped letting gcloud's default client ask for that scope ("this app is
blocked"), so every project that declared only a property id failed at runtime
with an instruction no longer possible to follow — and nothing noticed, because
the doctor check tested "can gcloud mint a token", not "can we read THIS
property".

The supported shape is a reader SERVICE ACCOUNT: one key on the machine, that SA
added as Viewer on every property it reads. A project then declares only its
property id (optionally pinning its own key_file); the machine supplies the
credential through $DEVTEAM_GA4_KEY. Credentials are machine facts — team code
holds no paths and no project names.

A declaration is accepted only when the property can actually be READ: `probe`
issues a real runReport, so "the key file exists" can never
read as ready again.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .core import run

ENV_KEY = "DEVTEAM_GA4_KEY"
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
REPORT_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport"


def key_path(declared: str | None) -> Path | None:
    """The reader key to use: the project's own declaration, else the machine's.

    `connections.ga4.key_file` pinning a key wins (a project may need its own
    credential); otherwise $DEVTEAM_GA4_KEY is the machine-wide reader key. None
    when neither is declared — the caller must then say what to do, never fall
    back to a login Google no longer allows.
    """
    for cand in (declared or "", os.environ.get(ENV_KEY) or ""):
        cand = str(cand).strip()
        if not cand:
            continue
        p = Path(cand).expanduser()
        if p.exists():
            return p
    return None


def _tail(r, limit: int = 200) -> str:
    return ((r.stdout or "") + (r.stderr or "")).strip()[-limit:]


def sa_email(key: Path) -> str:
    try:
        return json.loads(key.read_text()).get("client_email", "") or ""
    except Exception:
        return ""


def _gcloud(args: list[str], project: Path | None, timeout: int = 120):
    return run(["gcloud", *args], project, timeout=timeout)


def mint(project: Path | None, key: Path | None) -> tuple[str, str]:
    """(access token, why-not) exactly the way the collector will use it.

    A service-account key is activated to mint the token, then the machine's
    PREVIOUSLY ACTIVE gcloud account is restored — a collector must not silently
    repoint every other gcloud user on this box (the old code left the SA active).
    """
    if not shutil.which("gcloud"):
        return "", ("gcloud CLI not on PATH — the GA4 collector mints tokens "
                    "through it (install the SDK)")
    if key:
        if not key.exists():
            return "", f"reader key file missing: {key}"
        email = sa_email(key)
        if not email:
            return "", f"unreadable service-account key: {key}"
        before = (_gcloud(["config", "get-value", "account"], project, 60).stdout or "").strip()
        try:
            act = _gcloud(["auth", "activate-service-account", "--key-file", str(key)], project)
            if act.returncode != 0:
                return "", f"service-account activation failed: {_tail(act)}"
            tok = _gcloud(["auth", "print-access-token", "--account", email,
                           "--scopes", SCOPE], project)
            token = (tok.stdout or "").strip()
            if tok.returncode != 0 or not token:
                return "", f"service-account token failed: {_tail(tok)}"
            return token, ""
        finally:
            if before and before not in ("(unset)", ""):
                _gcloud(["config", "set", "account", before], project, 60)
    # No key declared: an ADC token is still tried (a machine that minted the
    # scope before Google's block keeps working), but it is NOT the recommended
    # path and the failure says why in terms of the key, not of a dead login.
    tok = _gcloud(["auth", "application-default", "print-access-token",
                   "--scopes", SCOPE], project, 60)
    token = (tok.stdout or "").strip()
    if tok.returncode != 0 or not token:
        tok = _gcloud(["auth", "application-default", "print-access-token"], project, 60)
        token = (tok.stdout or "").strip()
    if tok.returncode != 0 or not token:
        return "", ("no GA4 reader credential: declare a reader service-account "
                    f"key (connections.ga4.key_file, or ${ENV_KEY} machine-wide). "
                    "The owner's Application Default Credentials can no longer "
                    "mint analytics.readonly — Google blocked gcloud's default "
                    "client on 2026-11-05")
    return token, ""


def probe(project: Path | None, conf: dict) -> dict:
    """Read the declared property for real. {ok, status, reason, credential}."""
    prop = str((conf or {}).get("property") or "").strip()
    if not prop:
        return {"ok": False, "status": "undeclared",
                "reason": "connections.ga4.property is not declared"}
    key = key_path((conf or {}).get("key_file"))
    credential = f"service-account:{sa_email(key)}" if key else "adc"
    token, why_not = mint(project, key)
    if not token:
        return {"ok": False, "status": "no-credential", "reason": why_not,
                "credential": credential}
    body = {"dateRanges": [{"startDate": "7daysAgo", "endDate": "today"}],
            "metrics": [{"name": "activeUsers"}]}
    r = run(["curl", "-s", "-w", "\n__HTTP__%{http_code}", "-X", "POST",
             "-H", f"Authorization: Bearer {token}",
             "-H", "Content-Type: application/json",
             REPORT_URL.format(prop=prop), "-d", json.dumps(body)],
            project, timeout=120)
    if r.returncode != 0:
        return {"ok": False, "status": "unreachable",
                "reason": f"curl failed: {_tail(r)}", "credential": credential}
    payload, _, raw_code = (r.stdout or "").rpartition("__HTTP__")
    try:
        code = int((raw_code or "0").strip())
    except ValueError:
        code = 0
    if code == 200:
        return {"ok": True, "status": "ok", "reason": "", "credential": credential}
    message = ""
    try:
        message = (json.loads(payload).get("error") or {}).get("message", "")
    except Exception:
        message = "non-JSON response (network/proxy?)"
    if code in (401, 403):
        return {"ok": False, "status": "denied",
                "reason": f"HTTP {code}: {message or 'permission denied'}",
                "credential": credential}
    return {"ok": False, "status": "error", "reason": f"HTTP {code}: {message}",
            "credential": credential}


def fix_for(project_name: str, conf: dict, result: dict) -> str:
    """The owner/agent ask for a failed probe — precise, and not impossible.

    The old text said "run gcloud auth application-default login" — a command
    Google now blocks for this scope, so it sent the owner in a circle.
    """
    prop = str((conf or {}).get("property") or "<property id>")
    key = key_path((conf or {}).get("key_file"))
    status = (result or {}).get("status")
    if status == "denied":
        who = (result or {}).get("credential", "")
        email = who.split(":", 1)[1] if ":" in who else ""
        if email:
            return (f"give the reader service account {email} Viewer on GA4 property "
                    f"{prop} (analytics.google.com → Admin → Property access "
                    f"management → Add users), then re-run `devteam doctor "
                    f"{project_name}`; no login or key change is needed")
        return (f"the ADC credential in use has no access to GA4 property {prop}: "
                "declare a reader service-account key (connections.ga4.key_file or "
                f"${ENV_KEY}) whose SA is a Viewer on the property")
    if status == "no-credential":
        return ("declare a GA4 reader service-account key: set "
                f"${ENV_KEY}=<path to the reader SA json> machine-wide (or "
                f"connections.ga4.key_file in {project_name}'s profile) and add its "
                f"SA as Viewer on property {prop}. The owner's ADC login can no "
                "longer mint analytics.readonly — Google blocked gcloud's default "
                "client on 2026-11-05")
    return (f"GA4 property {prop} could not be read ({(result or {}).get('reason')}) — "
            f"check the key at {key or 'the declared source'} and the network, then "
            f"re-run `devteam doctor {project_name}`")
