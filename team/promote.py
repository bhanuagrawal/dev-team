"""Promotion to production, on the owner's explicit word — never on its own.

WHY THIS EXISTS (owner directive 2026-11-05)
-------------------------------------------
Until today production was human-only by construction: no command existed, CI
refused to promote, and the team could not even ask about it. The owner changed
that in one sentence — "jab main prompt dun promote to prod tab prod main ship
kardo" — because a launch is not a place where a UI detour helps him: every
promotion meant opening Play Console, finding the track, typing a note, and
pressing buttons he has already paid for someone else to press.

WHAT STAYS TRUE
---------------
Promotion is never a side effect. It is a verb, it is invoked on an explicit
owner request, and it refuses to run on its own:
  * CI never calls it (the generated release workflow uploads to `internal` only);
  * `release`, `record`, `loop` and the nightly never call it;
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
    """Commit WITHOUT `changesNotSentForReview`: the release must reach Google."""
    r = run(["curl", "-s", "-X", "POST",
             "-H", f"Authorization: Bearer {token}",
             f"{API}/applications/{pkg}/edits/{edit}:commit"],
            project, timeout=120)
    return play_listing._payload(r.stdout)


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


def promote(project: Path, version: str, *, requested_by: str,
            notes_file: str | None = None, dry_run: bool = False,
            log=None) -> dict:
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
    }
    state.setdefault("promotions", []).append(record)
    save_state(project, state)
    return {"status": "promoted", **record}
