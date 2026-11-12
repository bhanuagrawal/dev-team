"""The other side of a swap, driven through the real API.

Why this exists
---------------
The app audit (`ux-flow`) walks ONE reader on one device. A swap needs two
readers, and a second emulator/browser session is expensive, flaky and needs a
second sign-in. So the audit seats one reader in the app under test and lets this
peer be the counterpart through the same public endpoints the app uses:
accept, share a number for that offer, confirm the handover, confirm the return.

That keeps the important part real — RLS, the mandatory-number gate, the lock,
the two-sided confirmations — while only one UI has to be driven. Run the peer
once with the app as the sender, then again with the app as the receiver, and
both UI positions are covered.

A peer is a test actor, not a bot: it acts only on offers whose counterpart is
the app reader named by `--with-reader`, and it never touches an unrelated live
swap.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

CLIENT_REL = "composeApp/src/commonMain/kotlin/agrawal/bhanu/bookswap/network/BookSwapApiClient.kt"
UA = "devteam-flow-peer/1"

OPEN_STATUSES = {"REQUESTED", "MATCH_PROPOSED", "COUNTER_OFFERED"}
LIVE_STATUSES = {"HANDOFF_SCHEDULED", "IN_TRANSIT", "ACTIVE_BORROW", "OVERDUE"}


# --------------------------------------------------------------------------- #
# the decision — pure, so it can be tested without a server
# --------------------------------------------------------------------------- #
def next_action(offer: dict, me: str, phone: str) -> dict | None:
    """What this peer should do to the offer now, or None to wait.

    `offer` is one row of `GET /api/v1/offers` (the app's projection). The order
    matters: a number is shared before a handover, and a handover before a return.
    """
    status = str(offer.get("status") or "").upper()
    if status in ("COMPLETED", "CANCELLED"):
        return None
    if str(offer.get("last_offered_by") or "") == me and status in OPEN_STATUSES:
        # Our offer (or our counter) is on the table; only the other reader can
        # answer it.
        return None
    if status in OPEN_STATUSES:
        return {"action": "accept", "why": f"offer is {status} and it is our turn"}

    if status == "HANDOFF_SCHEDULED":
        if not offer.get("contact_shared_by_me"):
            return {"action": "share", "phone": phone,
                    "why": "a number for this offer is mandatory before handover"}
        if (offer.get("contact_shared_by_counterpart")
                and not offer.get("handover_confirmed_by_me")):
            return {"action": "handover", "why": "both numbers are shared"}
        return None

    if status in ("ACTIVE_BORROW", "OVERDUE"):
        if str(offer.get("exchange_mode") or "").upper() == "TEMPORARY" \
                and not offer.get("return_confirmed_by_me"):
            return {"action": "return", "why": "temporary loan is live"}
        return None
    return None


# --------------------------------------------------------------------------- #
# app constants + HTTP
# --------------------------------------------------------------------------- #
def app_constants(project: Path) -> tuple[str, str, str]:
    """API base, Supabase base and the production anon key, read from the app.

    Read, never retyped: a peer with its own copy of the URL keeps acting after
    the app has moved.
    """
    src = (project / CLIENT_REL).read_text()
    api = re.search(r'"(https://[\w.-]+\.run\.app)"', src)
    supabase = re.search(r'"(https://[\w.-]+\.supabase\.co)"', src)
    keys = re.findall(r'"(eyJ[\w-]+\.[\w-]+\.[\w-]+)"', src)
    if not (api and supabase and keys):
        raise SystemExit(f"flow-peer: cannot read the API/Supabase/anon constants from {CLIENT_REL}")
    return api.group(1), supabase.group(1), keys[-1]


def request(method: str, url: str, token: str | None = None,
            payload: dict | None = None,
            headers: dict | None = None) -> tuple[int, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read().decode(errors="replace")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            body: Any = json.loads(raw)
        except json.JSONDecodeError:
            body = {"detail": raw[:500]}
        return e.code, body
    except Exception as e:  # noqa: BLE001 — report, never crash the caller
        return 0, {"detail": str(e)}


def sign_in(supabase: str, anon: str, email: str, password: str) -> tuple[str, str]:
    status, body = request(
        "POST", f"{supabase}/auth/v1/token?grant_type=password",
        token=anon, payload={"email": email, "password": password},
        headers={"apikey": anon},
    )
    if status not in (200, 201) or not isinstance(body, dict):
        raise SystemExit(f"flow-peer: sign-in failed ({status}): {body}")
    token = str(body.get("access_token") or "")
    user = body.get("user") or {}
    uid = str((user or {}).get("id") or "")
    if not token or not uid:
        raise SystemExit("flow-peer: sign-in answered without a token or user id")
    return token, uid


# --------------------------------------------------------------------------- #
# the watcher
# --------------------------------------------------------------------------- #
class Peer:
    def __init__(self, api: str, token: str, me: str, phone: str,
                 log: Callable[[str], None], with_reader: str = ""):
        self.api = api.rstrip("/")
        self.token = token
        self.me = me
        self.phone = phone
        self.log = log
        self.with_reader = with_reader
        self.actions: list[dict] = []

    def offers(self) -> list[dict]:
        status, body = request("GET", f"{self.api}/api/v1/offers", token=self.token)
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"offers read failed ({status}): {body}")
        rows = body.get("offers") or []
        if not isinstance(rows, list):
            return []
        if not self.with_reader:
            return rows
        return [
            o for o in rows
            if str((o.get("counterpart") or {}).get("id") or "") == self.with_reader
        ]

    def act(self, offer: dict, action: dict) -> bool:
        tx = str(offer.get("transaction_id") or "")
        kind = action["action"]
        if kind == "accept":
            status, body = request("POST", f"{self.api}/api/v1/offers/{tx}/accept",
                                   token=self.token, payload={})
        elif kind == "share":
            status, body = request(
                "POST", f"{self.api}/api/v1/transactions/{tx}/share-contact",
                token=self.token, payload={"phone_number": action.get("phone")},
            )
        elif kind == "handover":
            status, body = request("POST", f"{self.api}/api/v1/offers/{tx}/handover",
                                   token=self.token, payload={})
        elif kind == "return":
            status, body = request("POST", f"{self.api}/api/v1/offers/{tx}/return",
                                   token=self.token, payload={})
        else:
            return False
        ok = status in (200, 201)
        self.log(f"peer {kind:<8} tx={tx} -> {status} {'' if ok else body}")
        self.actions.append({"action": kind, "transaction_id": tx, "status": status,
                             "ok": ok, "why": action.get("why", "")})
        return ok

    def create_offer(self, app_reader: str, mode: str, days: int,
                     note: str = "") -> str:
        """Create the opening offer for the run where the APP is the receiver.

        The peer must own an available copy and the app reader must have an
        available listing; both are read from the API, never guessed.
        """
        status, mine = request("GET", f"{self.api}/api/v1/users/{self.me}/available-copies",
                               token=self.token)
        if status != 200 or not isinstance(mine, list) or not mine:
            raise SystemExit("flow-peer: the peer has no available copy to offer")
        status, shelf = request("GET", f"{self.api}/api/v1/users/{app_reader}/shelf",
                                token=self.token)
        if status != 200 or not isinstance(shelf, dict):
            raise SystemExit(f"flow-peer: cannot read the app reader's shelf ({status})")
        listings = [b for b in (shelf.get("bookshelf") or [])
                    if b.get("listingId") and b.get("isAvailable", True)]
        if not listings:
            raise SystemExit("flow-peer: the app reader has no available listing to receive an offer")
        payload = {
            "primary_book_id": listings[0]["listingId"],
            "book_ids": [str(mine[0].get("id") or "")],
            "exchange_mode": mode,
            "duration_days": int(days),
            "offer_note": note,
            "handoff_mode": "IN_PERSON",
        }
        status, body = request("POST", f"{self.api}/api/v1/offers",
                               token=self.token, payload=payload)
        tx = str((body or {}).get("transaction_id") or "") if isinstance(body, dict) else ""
        if status not in (200, 201) or not tx:
            raise SystemExit(f"flow-peer: creating the opening offer failed ({status}): {body}")
        self.log(f"peer create   tx={tx} on {payload['primary_book_id']} -> {status}")
        self.actions.append({"action": "create", "transaction_id": tx,
                             "status": status, "ok": True, "why": "opening offer"})
        return tx


def run(project: Path, *, email: str = "", password: str = "", token: str = "",
        phone: str = "+91 90000 00002", with_reader: str = "",
        create_offer: bool = False, exchange_mode: str = "TEMPORARY",
        duration_days: int = 21, interval: float = 3.0, timeout: int = 300,
        max_actions: int = 10, expect_completed: bool = False, once: bool = False,
        api_url: str = "", supabase_url: str = "", anon_key: str = "",
        log: Callable[[str], None] = print) -> dict:
    api, supabase, anon = app_constants(project)
    api = api_url or api
    supabase = supabase_url or supabase
    anon = anon_key or anon

    if not token and not email:
        import os
        email = os.environ.get("BOOKSWAP_PEER_EMAIL", "")
        password = os.environ.get("BOOKSWAP_PEER_PASSWORD", "")
    if not token:
        if not email or not password:
            raise SystemExit(
                "flow-peer: give --peer-email/--peer-password (or --peer-token), or export "
                "BOOKSWAP_PEER_EMAIL and BOOKSWAP_PEER_PASSWORD"
            )
        token, me = sign_in(supabase, anon, email, password)
    else:
        status, body = request("GET", f"{supabase}/auth/v1/user", token=token,
                               headers={"apikey": anon})
        if status != 200 or not isinstance(body, dict) or not body.get("id"):
            raise SystemExit(f"flow-peer: the peer token is not usable ({status})")
        me = str(body["id"])

    if not with_reader and not create_offer:
        raise SystemExit(
            "flow-peer: pass --with-reader <app reader id> so the peer only touches this "
            "flow's offers, or --create-offer to open one"
        )
    if create_offer and not with_reader:
        raise SystemExit("flow-peer: --create-offer needs --with-reader for the receiving app reader")

    log(f"peer signed in as {me}; app reader {with_reader or '(open offer)'}")
    peer = Peer(api, token, me, phone, log, with_reader=with_reader)
    if create_offer:
        peer.create_offer(with_reader, exchange_mode, duration_days)

    deadline = time.monotonic() + max(1, timeout)
    completed = False
    while time.monotonic() < deadline and len(peer.actions) < max_actions:
        for offer in peer.offers():
            action = next_action(offer, me, phone)
            if action:
                peer.act(offer, action)
                if once:
                    return _result(peer, completed)
        rows = peer.offers()
        completed = any(str(o.get("status") or "").upper() == "COMPLETED" for o in rows)
        if completed and expect_completed:
            break
        if once:
            break
        time.sleep(max(0.5, interval))

    if expect_completed and not completed:
        raise SystemExit(f"flow-peer: no completed swap within {timeout}s")
    return _result(peer, completed)


def _result(peer: Peer, completed: bool) -> dict:
    return {
        "peer_id": peer.me,
        "completed": completed,
        "actions": peer.actions,
    }


def main(project: Path, args) -> int:
    from .core import RunLog
    from datetime import datetime

    run_id = "flow-peer-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    log = RunLog(project, run_id)
    result = run(
        project,
        email=getattr(args, "peer_email", "") or "",
        password=getattr(args, "peer_password", "") or "",
        token=getattr(args, "peer_token", "") or "",
        phone=getattr(args, "phone", "") or "+91 90000 00002",
        with_reader=getattr(args, "with_reader", "") or "",
        create_offer=getattr(args, "create_offer", False),
        exchange_mode=getattr(args, "exchange_mode", "TEMPORARY"),
        duration_days=getattr(args, "duration_days", 21),
        interval=getattr(args, "interval", 3.0),
        timeout=getattr(args, "timeout", 300),
        max_actions=getattr(args, "max_actions", 10),
        expect_completed=getattr(args, "expect_completed", False),
        once=getattr(args, "once", False),
        api_url=getattr(args, "api_url", "") or "",
        supabase_url=getattr(args, "supabase_url", "") or "",
        anon_key=getattr(args, "anon_key", "") or "",
        log=log,
    )
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
    else:
        log(f"peer result: {len(result['actions'])} action(s), "
            f"completed={result['completed']}")
    return 0 if (result["completed"] or not getattr(args, "expect_completed", False)) else 1
