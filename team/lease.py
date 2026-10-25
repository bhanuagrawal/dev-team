"""Machine-global resource leases — the deterministic half of "the AVD is shared".

`emulator.py` answers "what did THIS run switch on", so the run end can switch
it off again. Leases answer the other question, which no per-project state can
answer because the device is shared across projects on one machine:
**who owns the resource right now** — another run, the owner's own hand
session, or nobody.

A run acquires the resources its task spec declares in `needs` at
`run-start` (owned by the task id), holds them across developer → verify →
review → release, and `record` — which runs on EVERY outcome — drops them.
The store is machine-global on purpose (`~/.dev-team/locks`, not a project's
`.pipeline`): the AVD is not project-scoped, so a lock inside one project
would let a run in another project walk straight into its debug session.

A lease that outlives its run (crashed process, killed terminal, owner walk-
away) is not a deadlock: one older than its TTL (default 6h — the longest
queued entry is 240 min, plus review rounds and slack) is taken over, and
EVERY acquisition, takeover and drop is appended to the audit log, so "who had
the device and since when" is a file, not a memory.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .core import now_iso

HOME = Path.home() / ".dev-team"
LOCKS = HOME / "locks"
AUDIT = HOME / "leases.log"

#: Longest entry is a 240-minute task; a lease must outlive it plus review
#: rounds and slack, or a healthy run gets stolen from under itself.
DEFAULT_TTL_MIN = 360

_RETRIES = 8
_GRAMMAR = r"^[a-z0-9_-]+:[A-Za-z0-9._-]+$"


def claim_path(resource: str) -> Path:
    """The lock file for a resource. Grammar: '<kind>:<name>' — kind is the
    resource class (today: `avd`), name identifies the instance."""
    if ":" not in resource:
        raise SystemExit(f"bad resource '{resource}' — expected '<kind>:<name>', "
                         f"e.g. avd:medium_phone (the instance name is what makes "
                         f"two different devices leasable in parallel)")
    kind, _, name = resource.partition(":")
    if not kind or not name:
        raise SystemExit(f"bad resource '{resource}' — both '<kind>' and '<name>' "
                         f"are required")
    safe = f"{kind}__{name}".replace("/", "_")
    return LOCKS / f"{safe}.lock"


def _audit(action: str, resource: str, owner: str, **kw) -> None:
    parts = " ".join(f"{k}={v}" for k, v in kw.items())
    line = f"{now_iso()} {action} {resource} owner={owner}"
    if parts:
        line += " " + parts
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open("a") as f:
            f.write(line + "\n")
    except OSError:
        # The audit log is evidence, never a gate: a full disk or permission
        # quirk must not break the run it is meant to describe.
        pass


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) and data.get("owner") else None
    except (OSError, ValueError):
        return None


def _held_message(resource: str, held: dict) -> str:
    since = held.get("since", "?")
    until = held.get("until", "?")
    return (f"cannot lease {resource}: it is HELD by '{held.get('owner')}' "
            f"(since {since}, until {until}). Another run — or the owner, on "
            f"purpose — owns it. Wait for it to be recorded/expired; "
            f"`devteam leases` shows the full picture. Do not --force a lease "
            f"you do not know is dead: that is how two runs share one emulator, "
            f"which is exactly the collision the lease exists to prevent.")


def acquire(resource: str, owner: str, project: str | None = None,
            ttl_min: int = DEFAULT_TTL_MIN, force: bool = False) -> dict:
    """Take the lease for `resource` as `owner`.

    Protocol (POSIX, no lock server): an O_EXCL create IS the claim — it
    succeeds for exactly one process, so two simultaneous run-starts cannot
    both win. An existing lease is honoured unless it is stale (past its
    TTL) or force is given; a stale lease is moved aside via a rename that
    also succeeds for exactly one taker, then the claim is retried. An
    unparseable claim file is treated as stale (a half-written file from a
    killed process must not wedge the machine)."""
    import re
    if not re.match(_GRAMMAR, resource):
        raise SystemExit(f"bad resource '{resource}' — expected '<kind>:<name>'")
    path = claim_path(resource)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(_RETRIES):
        now = time.time()
        payload = {"resource": resource, "owner": owner, "project": project,
                   "since": now_iso(), "until_ts": now + ttl_min * 60,
                   "ttl_min": ttl_min}
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            _audit("ACQUIRE", resource, owner, project=project or "-")
            return payload
        except FileExistsError:
            held = _load(path)
            if held is None:
                # Corrupt or half-removed — move it aside (one winner) and retry.
                _evict(path, held=None, why="unreadable claim file")
                continue
            if force:
                _evict(path, held, why="forced")
                continue
            if held.get("owner") == owner:
                # Same owner re-acquiring (a re-run after a crash): refresh in
                # place. Idempotent by design — run-start must be re-runnable.
                try:
                    path.write_text(json.dumps(payload))
                except OSError:
                    pass
                _audit("ACQUIRE", resource, owner, project=project or "-",
                       note="refresh")
                return payload
            if time.time() > held.get("until_ts", 0):
                _evict(path, held, why="stale (past TTL)")
                continue
            raise SystemExit(_held_message(resource, held))
    raise SystemExit(f"could not acquire {resource} — lost the race "
                     f"{_RETRIES} times in a row (two processes? check `devteam leases`)")


def _evict(path: Path, held: dict | None, why: str) -> None:
    """Move a claim file aside. os.rename succeeds for exactly one of several
    simultaneous evictors, so takeovers are as race-free as the claims."""
    stamp = int(time.time() * 1000)
    try:
        os.rename(str(path), str(path) + f".stale-{stamp}")
    except OSError:
        return  # someone else already evicted it — their retry will win
    if held:
        _audit("TAKEOVER", held.get("resource", "?"), held.get("owner", "?"),
               reason=why, taken_by=os.getpid())


def drop(resource: str, owner: str, project: str | None = None,
         force: bool = False) -> bool:
    """Release the lease. Only the owner may drop it; --force is for a lease
    whose owning run is known to be dead. Returns False when nothing was held."""
    path = claim_path(resource)
    held = _load(path)
    if held is None:
        return False
    if held.get("owner") != owner and not force:
        raise SystemExit(f"refusing to drop {resource}: held by "
                         f"'{held.get('owner')}', not '{owner}' — a foreign "
                         f"lease is another run's ownership; use --force only "
                         f"when you are sure that run is dead")
    try:
        os.remove(str(path))
    except OSError:
        return False
    _audit("DROP", resource, held.get("owner", owner),
           requested_by=owner, force="yes" if force else "no",
           project=project or held.get("project") or "-")
    return True


def list_leases() -> list[dict]:
    """Every lease on this machine, oldest first, with age and stale flag."""
    rows = []
    if not LOCKS.exists():
        return rows
    for p in sorted(LOCKS.glob("*.lock")):
        data = _load(p)
        if data is None:
            try:
                data = json.loads(p.read_text())
            except (OSError, ValueError):
                data = {}
        until_ts = data.get("until_ts") or 0
        since_ts = since_iso2ts(data.get("since"))
        rows.append({
            "resource": data.get("resource") or p.name[:-5],
            "owner": data.get("owner") or "?",
            "project": data.get("project") or "-",
            "since": data.get("since") or "?",
            "until": ts2iso(until_ts),
            "age_min": int((time.time() - since_ts) / 60) if since_ts else None,
            "stale": bool(until_ts and time.time() > until_ts),
        })
    rows.sort(key=lambda r: r["since"] or "")
    return rows


def _ts(s: str | None) -> float | None:
    from datetime import datetime, timezone
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def since_iso2ts(s: str | None) -> float | None:
    return _ts(s)


def ts2iso(t: float | None) -> str:
    from datetime import datetime, timezone
    if not t:
        return "?"
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")