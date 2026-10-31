"""Publish the Play listing's visual assets through the Play Developer API.

The repo copy under `distribution/listing/<locale>/images` is what the pipeline
reviews; this module is the last inch that puts it in front of users. It exists
because "the assets are committed" is not a release: the previous run shipped
four screenshots, the owner opened Play and saw the paywall still third, and
nothing in the pipeline could tell him why.

Order is the point. Play displays phone screenshots in upload order, so the
editorial review's ranking (and only that ranking) decides the sequence:
deleteall, then upload one by one. The critic must pass, the review must exist,
and a monetization frame must not be among the files — otherwise nothing is
uploaded and the edit is discarded, so a rejected asset cannot leak to the store.

Everything project-shaped comes from profile.json (package, connection key) and
distribution/listing (assets, locale).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .core import profile_of, run

API = "https://androidpublisher.googleapis.com/androidpublisher/v3"
UPLOAD_API = "https://androidpublisher.googleapis.com/upload/androidpublisher/v3"
SCOPE = "https://www.googleapis.com/auth/androidpublisher"
DEFAULT_LOCALE = "en-US"
IMAGE_TYPES = ("phoneScreenshots", "icon", "featureGraphic")


def _listing_dir(project: Path) -> Path | None:
    for rel in ("distribution/listing/en-US", "distribution/listing",
                "fastlane/metadata/android/en-US", "src/main/play/listings/en-US"):
        p = project / rel
        if p.is_dir():
            return p
    return None


def _assets(project: Path) -> dict[str, Any]:
    listing = _listing_dir(project)
    if not listing:
        return {"error": "no listing directory (distribution/listing/en-US)"}
    img = listing / "images" if (listing / "images").is_dir() else listing
    shots_dir = img / "phoneScreenshots" if (img / "phoneScreenshots").is_dir() else img / "phone-screenshots"
    shots = sorted(shots_dir.glob("*.png")) if shots_dir.is_dir() else []
    icon = img / "icon.png"
    fg = img / "featureGraphic.png"
    if not fg.is_file():
        fg = img / "feature-graphic.png"
    missing = [name for name, p in (("icon.png", icon), ("featureGraphic.png", fg)) if not p.is_file()]
    if not shots:
        missing.append("phoneScreenshots/*.png")
    return {"locale": listing.name if listing.name != "images" else DEFAULT_LOCALE,
            "icon": icon, "feature_graphic": fg, "screenshots": shots, "missing": missing}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _play(project: Path) -> tuple[str, str, dict]:
    """(package, token, why-not). Uses the first declared Play connection."""
    profile = profile_of(project)
    conns = profile.get("connections") or {}
    for key in ("play_vitals", "play_reviews", "play_listing"):
        conf = conns.get(key) or {}
        pkg = conf.get("package")
        if not pkg:
            continue
        from .research import _play_token
        token, why = _play_token(conf, SCOPE, project)
        if token:
            return pkg, token, {}
        return pkg, "", {"key": key, "why": why}
    return "", "", {"why": "no Play connection declared (connections.play_vitals/package)"}


def _payload(text: str) -> dict:
    """Parse a Play API answer, turning an error body into `_error`.

    The API answers 403/400 with `{"error": {code, message, status}}` and HTTP
    200 for most successes; a caller that only looks for `_error` in the parsed
    dict would read a PERMISSION_DENIED commit as success (observed 2026-10-31:
    the listing edit was discarded and the owner saw the old paywall for another
    day). Every failure shape names itself here.
    """
    try:
        data = json.loads(text or "{}")
    except Exception:
        return {"_error": (text or "non-JSON response")[:400]}
    if isinstance(data, dict) and data.get("error"):
        err = data["error"] if isinstance(data["error"], dict) else {"message": str(data["error"])}
        return {"_error": f"{err.get('status') or err.get('code') or 'error'}: "
                          f"{err.get('message') or ''}".strip()}
    return data if isinstance(data, dict) else {"_error": "unexpected payload"}


def _post(url: str, project: Path, token: str) -> dict:
    r = run(["curl", "-s", "-X", "POST", "-H", f"Authorization: Bearer {token}", url],
            project, timeout=90)
    return _payload(r.stdout)


def _delete(url: str, project: Path, token: str) -> dict:
    """DELETE a collection (the API's `deleteall` is DELETE on the imageType
    collection, NOT a `/deleteall` suffix — the suffix answers 404)."""
    r = run(["curl", "-s", "-X", "DELETE", "-H", f"Authorization: Bearer {token}", url],
            project, timeout=90)
    return _payload(r.stdout)


def _upload(file: Path, package: str, edit: str, token: str, locale: str,
            image_type: str, project: Path) -> dict:
    url = (f"{UPLOAD_API}/applications/{package}/edits/{edit}/listings/{locale}/"
           f"{image_type}?uploadType=media")
    r = run(["curl", "-s", "-X", "POST",
             "-H", f"Authorization: Bearer {token}",
             "-H", "Content-Type: image/png",
             "--data-binary", f"@{file}",
             url], project, timeout=180)
    try:
        out = json.loads(r.stdout or "{}")
    except Exception:
        out = {"_error": (r.stdout or r.stderr or "non-JSON response")[:400]}
    return out if isinstance(out, dict) else {"_error": "unexpected upload payload"}


def _discard(package: str, edit: str, token: str, project: Path) -> None:
    run(["curl", "-s", "-X", "DELETE", "-H", f"Authorization: Bearer {token}",
         f"{API}/applications/{package}/edits/{edit}"], project, timeout=60)


def _live_images(package: str, edit: str, token: str, project: Path,
                 locale: str, image_type: str) -> list[dict]:
    from .research import _play_get
    data = _play_get(f"{API}/applications/{package}/edits/{edit}/listings/{locale}/{image_type}",
                     project, token)
    return data.get("images") or []


def publish_listing_assets(project: Path, dry_run: bool = True, log: Any = None) -> dict[str, Any]:
    """Upload icon, feature graphic and ordered screenshots; commit the edit.

    Refuses when the editorial review is missing or the critic fails, so the
    store can never receive a frame the reviewer rejected.
    """
    say = log or (lambda *_: None)
    assets = _assets(project)
    if assets.get("error"):
        return {"status": "ERROR", "error": assets["error"]}
    if assets["missing"]:
        return {"status": "ERROR", "error": "missing assets: " + ", ".join(assets["missing"])}

    from .asset_critic import review_store_assets
    report = review_store_assets(project)
    ed = report.get("editorial") or {}
    blockers = []
    if not ed.get("reviewed"):
        blockers.append("no editorial review for the current docs/storefront.json")
    if ed.get("hard_fail"):
        blockers.append("critic hard-fail: " + "; ".join(ed.get("issues") or []))
    if report.get("score", 0) < 80:
        blockers.append(f"critic score {report.get('score')} < 80")
    if not ed.get("order_ok", True):
        blockers.append("listing order does not match the editorial review")
    if blockers:
        return {"status": "BLOCKED", "blockers": blockers, "score": report.get("score")}

    shots = assets["screenshots"]
    plan = {
        "status": "DRY-RUN" if dry_run else "UPLOADING",
        "package": "",
        "locale": assets["locale"],
        "order": [p.name for p in shots],
        "score": report.get("score"),
    }
    package, token, why = _play(project)
    plan["package"] = package
    if not package or not token:
        return {"status": "ERROR", "error": f"Play auth unavailable: {why}"}
    if dry_run:
        say(f"~ would upload {len(shots)} screenshot(s) in order: {', '.join(plan['order'])}")
        say("  (dry run — nothing was written)")
        return plan

    edit = _post(f"{API}/applications/{package}/edits", project, token)
    edit_id = edit.get("id")
    if not edit_id:
        return {"status": "ERROR", "error": f"could not open a Play edit: {edit}"}
    try:
        # Replace, never append: a stale paywall frame must be gone, and the
        # display order is the editorial order.
        uploads: list[dict] = []
        for image_type, files in (
            ("phoneScreenshots", shots),
            ("icon", [assets["icon"]]),
            ("featureGraphic", [assets["feature_graphic"]]),
        ):
            gone = _delete(f"{API}/applications/{package}/edits/{edit_id}/listings/"
                           f"{assets['locale']}/{image_type}", project, token)
            if gone.get("_error"):
                raise RuntimeError(f"deleteall {image_type}: {gone['_error']}")
            for f in files:
                up = _upload(f, package, edit_id, token, assets["locale"], image_type, project)
                # The API answers {"image": {...}}, not the image itself.
                img = up.get("image") or up
                if up.get("_error") or not img.get("id"):
                    raise RuntimeError(f"upload {f.name}: {up.get('_error') or up}")
                uploads.append({"type": image_type, "file": f.name,
                                "id": img.get("id"),
                                "sha256": img.get("sha256") or _sha256(f)})
                say(f"  ↑ {image_type}/{f.name}")

        live = {t: _live_images(package, edit_id, token, project, assets["locale"], t)
                for t in IMAGE_TYPES}
        for t in ("icon", "featureGraphic"):
            want = next(a["sha256"] for a in uploads if a["type"] == t)
            if not live[t] or live[t][0].get("sha256") != want:
                raise RuntimeError(f"{t} upload did not verify against the uploaded image")
        live_shots = live["phoneScreenshots"]
        if len(live_shots) != len(shots):
            raise RuntimeError(f"Play holds {len(live_shots)} screenshot(s), expected {len(shots)}")
        if [i.get("sha256") for i in live_shots] != [a["sha256"] for a in uploads
                                                     if a["type"] == "phoneScreenshots"]:
            raise RuntimeError("screenshot ORDER after upload does not match the editorial order")

        committed = _post(f"{API}/applications/{package}/edits/{edit_id}:commit", project, token)
        # A commit must come back as the edit object; anything else (an error
        # payload, a permission denial, an empty body) means the listing did NOT
        # change. Treating "no _error key" as success is what shipped a stale
        # listing: the SA could upload images but not commit the edit.
        if committed.get("_error") or not committed.get("id"):
            raise RuntimeError(f"commit rejected: {committed.get('_error') or committed}")
        # COMMIT IS A CLAIM, NOT A RESULT. Read the app's current state back
        # through a fresh edit: if the images are not there, the commit did not
        # take effect (permissions, managed publishing, a discarded edit) and
        # the owner must not be told the store was updated.
        fresh = _post(f"{API}/applications/{package}/edits", project, token)
        fresh_id = fresh.get("id")
        if not fresh_id:
            raise RuntimeError(f"commit verification could not open a new edit: {fresh}")
        try:
            want = {t: [a["sha256"] for a in uploads if a["type"] == t] for t in IMAGE_TYPES}
            got = {t: [i.get("sha256") for i in _live_images(
                package, fresh_id, token, project, assets["locale"], t)] for t in IMAGE_TYPES}
        finally:
            _discard(package, fresh_id, token, project)
        if got != want:
            raise RuntimeError(
                "commit did not take effect: the app still serves a different asset set "
                f"(expected {sum(len(v) for v in want.values())} uploaded image(s), "
                f"found {sum(len(v) for v in got.values())})")
        plan.update(status="OK", committed=True,
                    uploaded=len(uploads),
                    live_order=got["phoneScreenshots"])
        say(f"  ✓ committed listing edit {edit_id} for {package} (verified live)")
        return plan
    except Exception as exc:
        _discard(package, edit_id, token, project)
        msg = str(exc)
        hint = None
        if "PERMISSION_DENIED" in msg or "does not have permission" in msg:
            hint = ("the Play service account can edit this listing but may not "
                    "commit/publish it. Grant it 'Manage store presence' (or "
                    "publish the staged edit in Play Console); nothing was changed.")
        elif "commit did not take effect" in msg:
            hint = ("the edit was accepted but Play still serves the old assets. "
                    "Managed publishing is ON for this app (or the change is in "
                    "review): finish it in Play Console → Publishing overview.")
        return {"status": "ERROR", "error": msg, "hint": hint, "discarded": True}
