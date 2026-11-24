"""Promotion to production, on the owner's explicit word — or a recorded provision.

WHY THIS EXISTS (owner directive 2026-11-05)
-------------------------------------------
Until today production was human-only by construction: no command existed, CI
refused to promote, and the team could not even ask about it. The owner changed
that in one sentence — "jab main prompt dun promote to prod tab prod main ship
kardo" — because a launch is not a place where a UI detour helps him: every
promotion meant opening Play Console, finding the track, typing a note, and
pressing buttons he has already paid for someone else to press.

THE STANDING FORM (owner ask 2026-11-17)
----------------------------------------
The owner can also say "after CI completes, always" for one app. That is a
provision, not a looser rule: `team/autopromote.py` stores his words in the
project profile and then calls THIS function for the newest release candidate
once Play's internal track reads it back. Every guarantee below still holds —
the version must already be on internal, the note must exist, and the record
names the standing ask that caused it.

WHAT STAYS TRUE
---------------
Promotion is never a side effect of building. It is a verb, invoked on an
explicit owner request (or a provision recorded from one), and it refuses to run
on its own:
  * CI never calls it (the generated release workflow uploads to `internal` only);
  * `release`, `record` and `loop` never call it; the nightly's
    `autopromote --run` calls it only under a recorded owner ask — a per-app
    provision, or the team-wide weekly rule (production older than 7 days, read
    live from Play);
  * it requires `--yes` AND the owner's words (`--requested-by`), and both are
    recorded with the edit id, so "who asked for this launch" is never a guess;
  * it only promotes a version that is ALREADY on an internal track — an
    untested build cannot reach production through this door.

Google's review is still Google's: the commit sends the release for review and
the answer comes back days later. This command starts that clock; it cannot
shorten it.
"""
from __future__ import annotations

import json
import re
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import play_listing
from .core import profile_of, read_delivery_file, run, save_state, state_of

API = play_listing.API
SCOPE = play_listing.SCOPE
LOCALE = "en-US"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _edit(pkg: str, project: Path, token: str) -> str:
    got = play_listing._post(f"{API}/applications/{pkg}/edits", project, token)
    if got.get("_error") or not got.get("id"):
        raise RuntimeError(f"could not open an edit: {got.get('_error') or got}")
    return str(got["id"])


def _tracks(pkg: str, edit: str, project: Path, token: str) -> list[dict]:
    from .research import _play_get
    data = _play_get(f"{API}/applications/{pkg}/edits/{edit}/tracks", project, token)
    if data.get("_error"):
        raise RuntimeError(f"could not read tracks: {data['_error']}")
    return data.get("tracks") or []


def _track(tracks: list[dict], name: str) -> dict:
    for t in tracks:
        if t.get("track") == name:
            return t
    return {"track": name, "releases": []}


def _put_track(pkg: str, edit: str, project: Path, token: str,
               body: dict) -> dict:
    r = run(["curl", "-s", "-X", "PUT",
             "-H", f"Authorization: Bearer {token}",
             "-H", "Content-Type: application/json",
             "--data", json.dumps(body),
             f"{API}/applications/{pkg}/edits/{edit}/tracks/{body['track']}"],
            project, timeout=90)
    return play_listing._payload(r.stdout)


def _commit(pkg: str, edit: str, project: Path, token: str) -> dict:
    """Commit the production edit and let Play decide whether it may go to review.

    Play refuses an automatic review submission while the app has an open policy
    issue — "Changes cannot be sent for review automatically. Please set the
    query parameter changesNotSentForReview to true" — which is exactly the state
    the Nov 8 Misleading-Claims rejection leaves behind. Retrying with the flag
    commits the release as a draft that waits on Play Console → Send for review.
    That is a real launch step, so the result carries `_review_pending` and
    `promote` records it instead of reporting a review that never happened.
    """
    url = f"{API}/applications/{pkg}/edits/{edit}:commit"
    r = run(["curl", "-s", "-X", "POST",
             "-H", f"Authorization: Bearer {token}", url],
            project, timeout=120)
    out = play_listing._payload(r.stdout)
    err = str((out or {}).get("_error") or "").lower()
    if out.get("_error") and "cannot be sent for review" in err and "must not be set" not in err:
        r = run(["curl", "-s", "-X", "POST",
                 "-H", f"Authorization: Bearer {token}",
                 f"{url}?changesNotSentForReview=true"],
                project, timeout=120)
        out = play_listing._payload(r.stdout)
        if isinstance(out, dict) and not out.get("_error"):
            out["_review_pending"] = True
    return out


def _discard(pkg: str, edit: str, project: Path, token: str) -> None:
    run(["curl", "-s", "-X", "DELETE", "-H", f"Authorization: Bearer {token}",
         f"{API}/applications/{pkg}/edits/{edit}"], project, timeout=60)


def _note(project: Path, notes_file: str | None) -> str:
    if notes_file:
        p = Path(notes_file)
        if not p.is_absolute():
            p = project / p
        return p.read_text().strip()
    rel = profile_of(project).get("whatsnew_file")
    return (read_delivery_file(project, rel, fallback_to_checkout=True) or "").strip() if rel else ""


STORE_URL = "https://play.google.com/store/apps/details"
_STORE_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def release_version(name: str) -> str:
    """The app version inside a Play release name: '26014 (1.4.8)' → '1.4.8'."""
    s = str(name or "").strip()
    m = re.search(r"\(([^)]+)\)", s)
    return (m.group(1) if m else s).strip().lstrip("v")


def store_updated_on(package: str, project: Path) -> tuple[str, str]:
    """When the STORE last changed, read live from the public listing.

    Google's Developer API returns a production track's versionCodes but no
    release timestamp, so the only live answer to "when did prod last change" is
    the listing's own 'Updated on' line. It is fetched on every call: no ledger,
    no snapshot, no cache — the same page a user sees decides.
    """
    if not package:
        return "", "no package declared"
    r = run(["curl", "-s", "-L", "-A", _STORE_UA,
             f"{STORE_URL}?id={package}&hl=en&gl=US"], project, timeout=60)
    body = r.stdout or ""
    if r.returncode != 0 or not body:
        return "", f"store page unreachable (curl rc={r.returncode})"
    m = re.search(r"Updated on</div>\s*<div[^>]*>\s*"
                  r"([A-Z][a-z]{2,8} \d{1,2}, \d{4})", body)
    if not m:
        return "", "the listing shows no 'Updated on' date (app not public yet?)"
    try:
        when = datetime.strptime(m.group(1), "%b %d, %Y").date()
    except ValueError:
        return "", f"could not parse the listing's date '{m.group(1)}'"
    return when.isoformat(), ""


def production_now(project: Path) -> dict:
    """What production holds RIGHT NOW — read from Play, never from our ledger.

    Returns `version`, `versionCodes`, `date` (the store's own live update date),
    `days` (how long since that date) and `why` when Play could not answer. A
    caller must treat an empty `date` as "unknown" and never as "fresh": an
    unverifiable age cannot authorize a launch.
    """
    project = Path(project)
    out: dict[str, Any] = {"version": "", "versionCodes": [], "date": "",
                           "days": None, "package": "", "why": ""}
    pkg, token, why = play_listing._play(project)
    out["package"] = pkg
    if not token:
        out["why"] = f"Play auth unavailable: {(why or {}).get('why') or 'unknown'}"
        return out

    read_edit = ""
    try:
        read_edit = _edit(pkg, project, token)
        tracks = _tracks(pkg, read_edit, project, token)
    except RuntimeError as e:
        out["why"] = str(e)
        return out
    finally:
        if read_edit:
            _discard(pkg, read_edit, project, token)

    releases = _track(tracks, "production").get("releases") or []
    rel = next((r for r in releases if str(r.get("status") or "") == "completed"),
               releases[0] if releases else None)
    if rel:
        out["version"] = release_version(rel.get("name"))
        out["versionCodes"] = [str(c) for c in (rel.get("versionCodes") or [])]

    when, why = store_updated_on(pkg, project)
    if when:
        out["date"] = when
        out["days"] = (date_cls.today() - date_cls.fromisoformat(when)).days
    else:
        out["why"] = why
    return out


def promote(project: Path, version: str, *, requested_by: str,
            notes_file: str | None = None, dry_run: bool = False,
            console: bool = False, log=None) -> dict:
    """Send `version` — already live on an internal track — to production.

    Returns a dict whose `status` is one of:
      promoted | already-promoted | refused | ERROR
    Every refusal is explicit: a promotion that silently did nothing is worse
    than one that failed loudly, because the owner has told real users a date.
    """
    project = Path(project)
    say = log or (lambda *_: None)
    pkg, token, why = play_listing._play(project)
    if not token:
        return {"status": "ERROR", "error": f"Play auth unavailable: {why.get('why') if why else 'unknown'}"}

    state = state_of(project)
    version = str(version or "").strip()
    if not version:
        return {"status": "refused", "error": "no version named — promotion is never guessed"}

    read_edit = ""
    try:
        read_edit = _edit(pkg, project, token)
        tracks = _tracks(pkg, read_edit, project, token)
    except RuntimeError as e:
        return {"status": "ERROR", "error": str(e)}
    finally:
        if read_edit:
            _discard(pkg, read_edit, project, token)

    internal = _track(tracks, "internal").get("releases") or []
    production = _track(tracks, "production").get("releases") or []
    build = next((r for r in internal if str(r.get("name") or "") == version), None)
    if not build:
        return {"status": "refused",
                "error": f"v{version} is not on the internal track — this door only "
                         "promotes a build a tester has already run"}
    codes = [str(c) for c in (build.get("versionCodes") or [])]

    # Console-driven promotion. The API service account can read tracks but may
    # lack the permission that writes a production release (seen 2026-11-07:
    # commit refused PERMISSION_DENIED), and Play's own console is then the only
    # door. This branch records that launch AFTER the fact — but only when the
    # production track is read back holding this build, so a claim of promotion
    # is never a guess. It runs before the already-promoted check so the record
    # says "console", not the API path that never committed.
    if console:
        note = _note(project, notes_file)
        ready = next((r for r in production
                      if str(r.get("name") or "") == version
                      or set(map(str, r.get("versionCodes") or [])) & set(codes)), None)
        if dry_run:
            return {"status": "DRY-RUN", "version": version, "versionCodes": codes,
                    "note": note, "would_record": "console-promotion",
                    "production_read_back": (ready or {}).get("status") or "not-listed-yet"}
        if not ready:
            return {"status": "refused",
                    "error": f"v{version} is not on the production track yet — finish the "
                             "launch in Play Console (Internal testing → Promote release → "
                             "Production → Send for review), then record it with --console"}
        if not note:
            return {"status": "refused",
                    "error": "this release has no store note — Play would show the previous "
                             "release's words; fix the note first"}
        record = {
            "at": _now(),
            "version": version,
            "versionCodes": codes,
            "edit": "console",
            "requested_by": requested_by,
            "note": note,
            "read_back": ready.get("status") or "not-listed-yet",
            "managed_publishing": bool(ready.get("status") == "draft"),
        }
        state.setdefault("promotions", []).append(record)
        save_state(project, state)
        say(f"promote: v{version} ({','.join(codes)}) recorded from Play Console — "
            f"production reads back '{record['read_back']}'")
        return {"status": "promoted-console", **record}

    if any(code in {str(c) for r in production for c in (r.get("versionCodes") or [])}
           for code in codes):
        prod_rel = next((r for r in production if any(str(c) in codes for c in (r.get("versionCodes") or []))), {})
        if not any(p.get("version") == version for p in state.get("promotions", [])):
            record = {
                "at": _now(),
                "version": version,
                "versionCodes": codes,
                "edit": "existing",
                "requested_by": requested_by,
                "note": _note(project, notes_file),
                "read_back": prod_rel.get("status") or "completed",
                "managed_publishing": bool(prod_rel.get("status") == "draft"),
            }
            state.setdefault("promotions", []).append(record)
            save_state(project, state)
        return {"status": "already-promoted", "version": version, "versionCodes": codes}

    if build.get("status") not in ("completed", "inProgress"):
        return {"status": "refused",
                "error": f"internal release for v{version} is '{build.get('status')}', "
                         "not a completed test release"}

    note = _note(project, notes_file)
    if not note:
        return {"status": "refused",
                "error": "this release has no store note — Play would show the previous "
                         "release's words; fix the note first"}

    if dry_run:
        return {"status": "DRY-RUN", "version": version, "versionCodes": codes,
                "note": note, "would_promote": True}

    edit = _edit(pkg, project, token)
    body = {
        "track": "production",
        "releases": [{
            "name": version,
            "versionCodes": codes,
            "status": "completed",           # full rollout: this is a launch, not a staged test
            "releaseNotes": [{"language": LOCALE, "text": note}],
        }],
    }
    put = _put_track(pkg, edit, project, token, body)
    if put.get("_error"):
        _discard(pkg, edit, project, token)
        return {"status": "ERROR", "error": f"production track write refused: {put['_error']}"}

    committed = _commit(pkg, edit, project, token)
    if committed.get("_error") and "draft app" in str(committed["_error"]).lower():
        body["releases"][0]["status"] = "draft"
        _put_track(pkg, edit, project, token, body)
        committed = _commit(pkg, edit, project, token)
    if committed.get("_error"):
        _discard(pkg, edit, project, token)
        return {"status": "ERROR", "error": f"commit refused: {committed['_error']}"}

    review_pending = bool(committed.get("_review_pending"))
    if review_pending:
        say(f"promote: v{version} ({','.join(codes)}) committed as edit {edit} — "
            "NOT sent for review (Play policy hold); send it from Play Console → "
            "Publishing overview")
    else:
        say(f"promote: v{version} ({','.join(codes)}) committed as edit {edit} — sent for review")

    # Read back through a FRESH edit: the commit's own payload says nothing about
    # what Play kept. `draft` here is the managed-publishing signature — the
    # change is committed but still waits on the console's Publish button.
    try:
        check_edit = _edit(pkg, project, token)
        after = _track(_tracks(pkg, check_edit, project, token), "production")
        _discard(pkg, check_edit, project, token)
    except RuntimeError:
        after = {"releases": []}
    kept = next((r for r in (after.get("releases") or [])
                 if set(map(str, r.get("versionCodes") or [])) & set(codes)), None)

    record = {
        "at": _now(),
        "version": version,
        "versionCodes": codes,
        "edit": edit,
        "requested_by": requested_by,
        "note": note,
        "read_back": (kept or {}).get("status") or "not-listed-yet",
        "managed_publishing": bool(kept and kept.get("status") == "draft"),
        "review_pending": review_pending,
    }
    state.setdefault("promotions", []).append(record)
    save_state(project, state)
    return {"status": "promoted", **record}
