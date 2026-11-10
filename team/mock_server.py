"""A Supabase-shaped mock, so a journey can be audited end to end.

WHY THIS EXISTS
---------------
A flow audit that stops at "sign in" only ever proves the wall exists. The rest of
the journey — sign up, see the neighbourhood, request a book, reach the handoff —
needs a session and a backend that answers, and neither is available to an agent:
real sign-up is rate-limited, real listings are other people's data, and a request
would post a real transaction into a live project.

So the audit talks to a LOCAL mock instead. The app under test is unchanged: the
debug build is launched with a base-URL override that only a debuggable build
honours, so the same composables, view models and repositories run against
deterministic fixtures. Nothing is bypassed in production code, and nothing is
written to the real project.

The contract is the app's own: every path, status and JSON shape was read from the
client (`BookSwapApiClient`, `AuthRepositoryImpl`), so a mock that answers is a
statement about what the app EXPECTS. It is deliberately NOT a claim that the live
backend answers the same way — that is what an audit without `--mock` is for.

Deterministic by construction: fixed ids, fixed timestamps, no clock, no network.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_PORT = 54399
OWNER_ID = "11111111-1111-1111-1111-111111111111"
READER_ID = "22222222-2222-2222-2222-222222222222"
MOCK_TOKEN = "mock-session-token"
MOCK_TRANSACTION_ID = "11111111-2222-3333-4444-555555555555"

# The listing ids are stable so a request can be traced from the card to the RPC.
BOOKS = [
    {
        "listing_id": "a0000004-0000-0000-0000-000000000001",
        "isbn": "9780765377067",
        "title": "The Three-Body Problem",
        "author": "Cixin Liu",
        "cover_url": "https://covers.openlibrary.org/b/id/8231856-L.jpg",
        "vibe_tags": ["hard sci-fi", "first contact"],
        "listing_type": "TEMPORARY_LEND",
        "condition": "LIKE_NEW",
        "distance_meters": 420,
        "distance_label": "0.4 km away",
        "location_name": "Indiranagar",
        "owner_username": "sneha_kora",
        "owner_trust_score": 82.0,
    },
    {
        "listing_id": "a0000004-0000-0000-0000-000000000002",
        "isbn": "9780451524935",
        "title": "1984",
        "author": "George Orwell",
        "cover_url": "https://covers.openlibrary.org/b/id/8575708-L.jpg",
        "vibe_tags": ["dystopian"],
        "listing_type": "PERMANENT_SWAP",
        "condition": "GOOD",
        "distance_meters": 780,
        "distance_label": "0.8 km away",
        "location_name": "Domlur",
        "owner_username": "arjun_m",
        "owner_trust_score": 75.0,
    },
    {
        "listing_id": "a0000004-0000-0000-0000-000000000003",
        "isbn": "9780593135204",
        "title": "Project Hail Mary",
        "author": "Andy Weir",
        "cover_url": "https://covers.openlibrary.org/b/id/10574697-L.jpg",
        "vibe_tags": ["sci-fi", "humour"],
        "listing_type": "TEMPORARY_LEND",
        "condition": "BRAND_NEW",
        "distance_meters": 1150,
        "distance_label": "1.2 km away",
        "location_name": "Indiranagar",
        "owner_username": "priya_r",
        "owner_trust_score": 98.0,
    },
]

PROFILE = {
    "id": READER_ID,
    "username": "audit_reader",
    "full_name": "Audit Reader",
    "location_name": "Indiranagar",
    "city": "Bangalore",
    "trust_score": 75.0,
    "location": "SRID=4326;POINT(77.6408 12.9784)",
}


def _book_block_items() -> list[dict]:
    """The carousel shape the app's `BookCardUiModel` reads (camelCase contract)."""
    return [
        {
            "listingId": b["listing_id"],
            "title": b["title"],
            "author": b["author"],
            "coverUrl": b["cover_url"],
            "aiMatchBadge": b["distance_label"],
            "ownerUsername": b["owner_username"],
            "ownerTrustScore": b["owner_trust_score"],
            "action": {
                "label": "Request this book",
                "actionType": "REQUEST_BOOK",
                "payload": {"listing_id": b["listing_id"]},
            },
        }
        for b in BOOKS
    ]


def _blocks() -> list[dict]:
    return [
        {
            "type": "smart_insight_banner",
            "headline": "3 readers near you want books on your shelf",
            "subtext": "Circulate 2 books this weekend to earn Priority Swap Credits.",
            "accentColorHex": "#E06D44",
            "primaryAction": {"label": "View Matches", "actionType": "view_matches"},
        },
        {
            "type": "book_carousel",
            "sectionTitle": "Available Within 15 Min Walk",
            "sectionSubtitle": "Verified owners with trust scores above 90 percent",
            "items": _book_block_items(),
        },
        {
            "type": "vision_scanner_cta",
            "title": "Scan your shelf",
            "subtitle": "Snap a photo of your bookshelf to list books in seconds",
            "action": {"label": "Scan", "actionType": "LAUNCH_SHELF_CAMERA"},
        },
    ]


def _sse(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "devteam-mock/1"

    # every request is recorded, so the audit can show what the app asked for
    def log_message(self, fmt, *args):  # noqa: D401 - silence stdlib logging
        return

    # -- plumbing -----------------------------------------------------------
    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw or b"{}")
        except Exception:
            return {}

    def _json(self, payload, status: int = 200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse_response(self, events: list[tuple[str, dict]]):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for event, payload in events:
            self.wfile.write(_sse(event, payload))
            self.wfile.flush()
            time.sleep(0.05)

    def _record(self, method: str, path: str, body: dict):
        entry = {"method": method, "path": path, "at": round(time.time(), 3)}
        if body:
            entry["keys"] = sorted(body)
            for key in ("p_user_book_id", "p_transaction_id", "p_secret", "email"):
                if key in body:
                    entry["params"] = {**(entry.get("params") or {}), key: body[key]}
        self.server.requests.append(entry)

    # -- routes -------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        self._record("GET", path, {})
        if path == "/auth/v1/authorize":
            # What a real GoTrue does: bounce the browser back to the app's own
            # scheme with tokens in the FRAGMENT. The Custom Tab hands off to the
            # intent filter, so the OAuth-only journey is auditable with no provider.
            provider = (parsed.query.split("provider=")[-1].split("&")[0]
                        if "provider=" in parsed.query else "google")
            location = (f"bookswap://auth-callback#access_token={MOCK_TOKEN}"
                        f"&refresh_token=mock-refresh-token&expires_in=3600"
                        f"&token_type=bearer&provider={provider}")
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/auth/v1/user":
            self._json({"id": READER_ID, "email": "audit@bookswap.test"})
            return
        if path == "/auth/v1/user":
            # The app asks who the token belongs to; GoTrue answers on GET and POST.
            self._json({"id": READER_ID, "email": "audit@bookswap.test"})
            return

        # Layer 1 REST: the book-detail screen's own read. The live route is
        # /api/v1/books/{listing_id}; without it a card that is on screen cannot
        # be opened ("Failed to load book details: not found"), and the borrow
        # journey dead-ends on a listing the reader can see (2026-11-10).
        if path == "/api/v1/trades/live":
            # The transaction a cold start should reopen; empty until this audit
            # has actually asked for a book.
            self._json({"transactionId": getattr(self.server, "state", {}).get("trade", "")})
            return

        if path.startswith("/api/v1/trades/") and path.endswith("/meetup"):
            tx = path.split("/api/v1/trades/")[-1].rsplit("/meetup", 1)[0]
            self._json({
                "transaction_id": tx or MOCK_TRANSACTION_ID,
                "status": "REQUESTED",
                "viewer_role": "BORROWER",
                "book": {"listing_id": BOOKS[0]["listing_id"],
                         "isbn": BOOKS[0].get("isbn") or "",
                         "title": BOOKS[0]["title"], "author": BOOKS[0]["author"],
                         "cover_url": BOOKS[0].get("cover_url"),
                         "condition": BOOKS[0].get("condition") or "LIKE_NEW",
                         "listing_type": BOOKS[0].get("listing_type") or "TEMPORARY_LEND",
                         "max_borrow_days": 21},
                "other_reader": {"user_id": OWNER_ID,
                                 "username": BOOKS[0].get("owner_username") or "sneha_kora",
                                 "trust_score": 82.0,
                                 "location_name": BOOKS[0].get("location_name") or "Indiranagar"},
                "midpoint": {"label": "Between you and Indiranagar 100ft Rd",
                             "distance_meters": 600},
                "venue": {"venue_id": "venue-1", "name": "Third Wave Coffee",
                          "address": "100ft Road, Indiranagar", "venue_type": "CAFE",
                          "distance_meters": 600, "walk_minutes": 8,
                          "opening_hours": "08:00–23:00", "community_handshakes": 42},
                "alternates": [],
                "accepted_at": None,
                "alternate_suggested_at": None,
            })
            return

        if path.startswith("/api/v1/books/"):
            lid = path.rsplit("/", 1)[-1]
            book = next((b for b in BOOKS if b["listing_id"] == lid), None)
            if book is None:
                self._json({"message": "not found"}, 404)
                return
            self._json({
                "listingId": book["listing_id"],
                "userId": OWNER_ID,
                "title": book["title"],
                "author": book["author"],
                "coverUrl": book.get("cover_url"),
                "description": ("A well-kept copy, read once. Happy to lend it to a "
                                "neighbour and get it back when you are done."),
                "vibeTags": book.get("vibe_tags") or [],
                "condition": book.get("condition") or "LIKE_NEW",
                "listingType": book.get("listing_type") or "TEMPORARY_LEND",
                "isAvailable": True,
                "maxBorrowDays": 21,
                "hasAnnotations": False,
                "isSmokeFree": True,
                "publisher": "Tor Books",
                "format": "Paperback",
                "publishedYear": 2014,
                "pageCount": 416,
                "isPetFree": True,
                "successfulSwaps": 3,
                "typicalResponseMinutes": 42,
                "owner": {
                    "userId": OWNER_ID,
                    "username": book.get("owner_username") or "sneha_kora",
                    "trustScore": book.get("owner_trust_score") or 82.0,
                    "locationName": book.get("location_name") or "Indiranagar",
                    "trustTier": "PROVEN",
                    "trustTierLabel": "Proven reader",
                    "shieldBadge": "bronze",
                    "isFrozen": False,
                },
                "createdAt": "2026-11-01T09:00:00Z",
            })
            return

        if path.startswith("/api/v1/users/") and path.endswith("/shelf"):
            uid = path.split("/api/v1/users/")[-1].rsplit("/shelf", 1)[0]
            shelf = [{
                "listingId": b["listing_id"],
                "title": b["title"],
                "author": b["author"],
                "coverUrl": b.get("cover_url"),
                "listingType": b.get("listing_type") or "TEMPORARY_LEND",
                "condition": b.get("condition") or "LIKE_NEW",
                "isAvailable": True,
            } for b in BOOKS[:2]]
            self._json({
                "user": {"userId": uid or OWNER_ID,
                         "username": BOOKS[0].get("owner_username") or "sneha_kora",
                         "trustScore": 82.0, "locationName": "Indiranagar",
                         "trustTier": "PROVEN", "trustTierLabel": "Proven reader",
                         "shieldBadge": "bronze", "isFrozen": False},
                "bookshelfCount": len(shelf), "bookshelf": shelf,
                "wishlistCount": 0, "wishlist": [],
            })
            return

        if path == "/rest/v1/profiles":
            self._json([PROFILE])
            return
        if path == "/__health":
            self._json({"status": "ok", "requests": len(self.server.requests)})
            return
        self._json({"message": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        self._record("POST", path, body)

        if path == "/auth/v1/user":
            self._json({"id": READER_ID, "email": "audit@bookswap.test"})
            return

        if path == "/api/v1/trades/request":
            # Opening the loan: the client posts the listing id and expects the
            # transaction it just created. Without this, "Borrow 21d" 404s and the
            # borrow journey ends before the pickup step.
            tx = MOCK_TRANSACTION_ID
            try:
                self.server.state["trade"] = tx
            except Exception:
                pass
            self._json({"success": True, "transactionId": tx,
                        "message": "Request sent to the keeper."})
            return

        if path.startswith("/api/v1/trades/") and path.endswith("/meetup"):
            self._json({"ok": True})
            return

        if path in ("/auth/v1/signup", "/auth/v1/token"):
            email = str(body.get("email") or "reader@bookswap.test")
            self._json({
                "access_token": MOCK_TOKEN,
                "token_type": "bearer",
                "expires_in": 3600,
                "user": {"id": READER_ID, "email": email},
            })
            return

        if path == "/rest/v1/profiles":
            row = {**PROFILE, **(body or {})}
            row["id"] = str(body.get("id") or READER_ID)
            self._json([row], 201)
            return

        if path.startswith("/rest/v1/rpc/"):
            self._rpc(path.rsplit("/", 1)[-1], body)
            return

        if path == "/functions/v1/ui-composer":
            self._sse_response([
                ("agent_status", {"stage": "ANALYZING_SPATIAL_RADIUS",
                                  "message": "Analyzing local radius (3km) around you..."}),
                ("agent_status", {"stage": "FAST_BRAIN_COMPOSITION",
                                  "message": "Composing your neighbourhood layout..."}),
                ("init", {"screenTitle": "Book Discovery", "routingTier": "TIER_B_FAST_BRAIN"}),
                *[("block", {"block": b}) for b in _blocks()],
                ("complete", {"status": "DONE", "totalBlocks": len(_blocks())}),
            ])
            return

        if path == "/functions/v1/trade-negotiator":
            self._json({"screenTitle": "P2P Swap Negotiator", "cyclesCount": 0,
                        "cycles": [], "blocks": []})
            return

        if path == "/functions/v1/bounty-radar":
            self._json({
                "screenTitle": "Community Wanted Bounties", "matchesCount": 1,
                "bounties": [{"bounty_id": "bounty-1", "requested_title": "Dune",
                              "requester_username": "aarav_indira",
                              "location_name": "Indiranagar", "distance_meters": 600}],
                "blocks": [{
                    "type": "bounty_prompt_card",
                    "bookTitle": "Dune",
                    "nearbyReadersCount": 4,
                    "pitchText": "4 neighbours are looking for this title right now.",
                    "action": {"label": "I Have This Book", "actionType": "CLAIM_BOUNTY"},
                }],
            })
            return

        if path == "/functions/v1/vision-shelf-scanner":
            self._json({"titles": ["Dune — Frank Herbert",
                                   "The Psychology of Money — Morgan Housel",
                                   "1984 — George Orwell"]})
            return

        if path == "/functions/v1/dispute-arbiter":
            self._json({"status": "OK", "blocks": []})
            return

        self._json({"message": f"no mock route for {path}"}, 404)

    def _rpc(self, name: str, body: dict):
        if name == "rpc_search_local_books":
            self._json(BOOKS)
            return
        if name == "rpc_request_book":
            # The client expects a JSON *scalar*: a bare quoted uuid.
            self._json("7c0a8f2e-0000-4000-8000-000000000001")
            return
        if name == "rpc_get_handoff_qr":
            self._json([{
                "transaction_id": str(body.get("p_transaction_id") or
                                      "7c0a8f2e-0000-4000-8000-000000000001"),
                "handoff_secret": "mock-handoff-secret",
                "role": "LENDER",
            }])
            return
        if name == "rpc_verify_handoff":
            self._json({"success": True, "status": "ACTIVE_BORROW",
                        "message": "Handoff verified — happy reading",
                        "due_date": "2026-11-22"})
            return
        if name == "rpc_match_book_to_bounties":
            self._json([{
                "bounty_id": "bounty-1", "requester_username": "aarav_indira",
                "requested_title": "Dune", "location_name": "Indiranagar",
                "distance_meters": 600.0, "match_score": 0.92,
            }])
            return
        if name in ("rpc_fulfill_bounty", "rpc_accept_swap", "rpc_open_dispute"):
            self._json({"success": True})
            return
        self._json({"message": f"no mock for rpc {name}"}, 404)


class MockServer:
    """A threaded HTTP server on localhost, started/stopped around one audit."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = int(port)
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """The address the EMULATOR uses: 10.0.2.2 is the host's loopback."""
        return f"http://10.0.2.2:{self.port}"

    @property
    def requests(self) -> list[dict]:
        return list(getattr(self.httpd, "requests", []) if self.httpd else [])

    def start(self) -> "MockServer":
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", self.port), _Handler)
        except OSError:
            # A busy port is not a failed audit: take whatever the OS offers and
            # report it, so a stray local stack (or a previous run) cannot block it.
            httpd = ThreadingHTTPServer(("0.0.0.0", 0), _Handler)
            self.port = httpd.server_address[1]
        httpd.requests = []          # type: ignore[attr-defined]
        httpd.state = {"trade": ""}  # type: ignore[attr-defined]
        httpd.daemon_threads = True  # type: ignore[attr-defined]
        self.httpd = httpd
        self.thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def wait_ready(self, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return True
            except OSError:
                time.sleep(0.1)
        return False

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None


def serve(project_name: str = "project", port: int = DEFAULT_PORT,
          log=None) -> int:
    """Serve in the foreground until interrupted — for a manual walk."""
    server = MockServer(port).start()
    server.wait_ready()
    msg = (f"mock backend for {project_name} on {server.url} "
           f"(host: http://127.0.0.1:{port}) — Ctrl-C to stop")
    print(msg)
    if log:
        log(msg)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        print("mock backend stopped")
    return 0
