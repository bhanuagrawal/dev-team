"""THE DEPLOYED BACKEND MUST CARRY THE CHANGE THE CLIENT SHIPS.

Why this exists (2026-11-22). `verify` was green, the walk reached its goal, and
1.0.89-rc.2 went to internal testing — while `POST /api/v1/offers` failed on the
live backend (`column "listing_type" … does not exist`) and the feedback routes
the new panel calls did not exist at all. The walk could not see it: the deal
page renders Waiting / Agreed / Done from fields the OLD server already sent, so
every observable the task declared appeared on screen without one new endpoint
being touched. The gate asked "did the change appear?" and the answer was yes,
for a screen painted from yesterday's data.

So a task may declare what its change needs from the deployed service:

    "backend": {
      "repo": "bookswap-backend",
      "base_url": "https://<service>.run.app",      # optional: read from the client
      "requires": {
        "routes": ["POST /api/v1/offers/{offer_id}/feedback"],
        "calls": [{"path": "/api/v1/offers",
                   "item_keys": ["timeline", "feedback", "agreed_at"]}]
      }
    }

`routes` is checked against the live service's own OpenAPI document (FastAPI
publishes it, so this is the server describing itself, not a guess). `calls` is
signed in with the audit account and reads one real response: a route that exists
but answers the old shape is refused too. The remedy is always the same and the
refusal says it: merge the backend to main, apply its migrations, deploy.

No backend block, no probe: a project whose change needs nothing from a server is
not asked a question about one. A probe that cannot reach the network is a
refusal, not a pass — the walk signs in to the same service, and an unreachable
backend is exactly the failure this gate exists to catch.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

UA = "devteam-backend-probe/1"
CLIENT_URL_RE = re.compile(r'"(https://[\w.-]+\.run\.app)"')

Fetch = Callable[[str, str, dict | None, str | None], tuple[int, Any]]


# --------------------------------------------------------------------------- #
# pure checks — the part a unit test can hold still
# --------------------------------------------------------------------------- #
def missing_routes(paths: dict, routes: list[str]) -> list[str]:
    """(method + path) that the service's own document does not carry."""
    gaps: list[str] = []
    for raw in routes:
        text = " ".join(str(raw).split())
        if not text:
            continue
        method, _, path = text.partition(" ")
        method, path = method.lower(), path.strip()
        entry = paths.get(path) if isinstance(paths, dict) else None
        if not isinstance(entry, dict) or method not in {str(k).lower() for k in entry}:
            gaps.append(text)
    return gaps


def missing_keys(payload: Any, item_keys: list[str]) -> list[str]:
    """Keys the response does not carry — on the row, or on its first item.

    The app's projection is a list for `/offers` and an object for one deal, and
    a declared key may sit on either. An empty list is not evidence either way
    (there may be no deal yet), so only a non-empty answer is judged.
    """
    if not item_keys:
        return []
    row: Any = payload
    if isinstance(payload, list):
        if not payload:
            return []
        row = payload[0]
    if not isinstance(row, dict):
        return [str(k) for k in item_keys]
    return [str(k) for k in item_keys if k not in row]


def remedy(backend: dict) -> str:
    repo = str(backend.get("repo") or "the backend")
    return (f"  merge {repo} to main, apply its migrations, then "
            "`python3 python-backend/deploy.py` (main only) and re-run verify — "
            "the walk proves the screen, this proves the server behind it.")


# --------------------------------------------------------------------------- #
# IO
# --------------------------------------------------------------------------- #
def request(method: str, url: str, payload: dict | None = None,
            token: str | None = None, timeout: int = 30) -> tuple[int, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", "replace")
            try:
                return res.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return res.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return exc.code, raw
    except Exception as exc:  # noqa: BLE001 - offline is a refusal, not a crash
        return 0, str(exc)


def base_url(project: Path, backend: dict, worktree: Path | None = None) -> str:
    declared = str(backend.get("base_url") or "").strip()
    if declared:
        return declared.rstrip("/")
    for root in (worktree, project):
        if root is None:
            continue
        for rel in ("composeApp/src/commonMain/kotlin/agrawal/bhanu/bookswap/"
                    "network/BookSwapApiClient.kt", "web/.env.example"):
            path = Path(root) / rel
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            match = CLIENT_URL_RE.search(text)
            if match:
                return match.group(1).rstrip("/")
    return ""


def _credentials(project: Path) -> tuple[str, str]:
    data: dict = {}
    path = project / ".pipeline" / "audit-account.json"
    try:
        if path.is_file():
            data = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        data = {}
    email = str(os.environ.get("DEVTEAM_AUDIT_EMAIL") or data.get("email") or "").strip()
    password = str(os.environ.get("DEVTEAM_AUDIT_PASSWORD") or data.get("password") or "")
    return email, password


def _sign_in(base: str, project: Path) -> tuple[str, str]:
    email, password = _credentials(project)
    if not email or not password:
        return "", "no audit account (set DEVTEAM_AUDIT_EMAIL / DEVTEAM_AUDIT_PASSWORD)"
    status, body = request("POST", f"{base}/api/v1/auth/login",
                           {"email": email, "password": password})
    if status not in (200, 201) or not isinstance(body, dict):
        return "", f"sign-in failed ({status})"
    return str(body.get("access_token") or ""), ""


def contract(project: Path, spec: dict | None, worktree: Path | None = None,
             *, fetch: Fetch | None = None,
             sign_in: Callable[[str, Path], tuple[str, str]] | None = None
             ) -> tuple[bool, str]:
    """(ok, message) — is the DEPLOYED service the one this change needs?

    `fetch`/`sign_in` are injectable so the tests never touch the network.
    """
    backend = (spec or {}).get("backend") or {}
    if not backend:
        return True, "no backend contract declared"
    requires = backend.get("requires") or {}
    routes = [str(r) for r in (requires.get("routes") or [])]
    calls = [c for c in (requires.get("calls") or []) if isinstance(c, dict)]
    if not routes and not calls:
        return True, "backend declared, nothing to probe"

    base = base_url(Path(project), backend, worktree)
    if not base:
        return False, ("BACKEND CONTRACT: the task declares backend requirements but "
                       "no base_url and the client names no service.\n"
                       + remedy(backend))
    get, login = fetch or request, sign_in or _sign_in

    gaps: list[str] = []
    if routes:
        status, doc = get("GET", f"{base}/openapi.json", None, None)
        if status != 200 or not isinstance(doc, dict):
            return False, (f"BACKEND CONTRACT: {base}/openapi.json answered {status} — "
                           "the deployed service cannot describe itself.\n"
                           + remedy(backend))
        for route in missing_routes(doc.get("paths") or {}, routes):
            gaps.append(f"  route {route} does not exist on {base}")

    token = ""
    for call in calls:
        path = str(call.get("path") or "")
        method = str(call.get("method") or "GET").upper()
        item_keys = [str(k) for k in (call.get("item_keys") or [])]
        if not path:
            continue
        if not token:
            token, why = login(base, Path(project))
            if not token:
                gaps.append(f"  cannot read {path}: {why}")
                continue
        status, body = get(method, base.rstrip("/") + path, None, token)
        if status != int(call.get("expect") or 200):
            gaps.append(f"  {method} {path} answered {status} (expected "
                        f"{int(call.get('expect') or 200)})")
            continue
        for key in missing_keys(body, item_keys):
            gaps.append(f"  {method} {path} carries no '{key}' — the deployed "
                        "server still sends yesterday's shape")

    if not gaps:
        return True, f"{base} carries the declared contract"
    return False, ("THE DEPLOYED BACKEND DOES NOT CARRY THIS CHANGE "
                   "(the walk proves the screen; this proves the server):\n"
                   + "\n".join(gaps) + "\n" + remedy(backend))
