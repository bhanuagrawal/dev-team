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


def _assets_root(project: Path) -> tuple[Path | None, str]:
    """Where the assets come from: the checkout when it CLEANLY matches
    origin/main, otherwise a delivery tree at origin/main.

    A store upload is a claim about what users receive. Reading the checkout is
    only safe while it equals the delivery ref; a stale checkout published the
    old paywall frames once already — and a DIRTY checkout is the same defect
    one step earlier: `storefront-scenes` writes regenerated PNGs straight into
    `distribution/listing/` as uncommitted changes, so publishing from there put
    frames on Play that existed in no commit (observed 2026-10-31). A dirty
    asset tree is therefore refused, not quietly replaced: the fix is to commit
    the assets first, and the error says so.
    """
    from .core import git, git_ref_state
    state = git_ref_state(project)
    # Only ASSET paths matter: the owner's unrelated WIP (a plan edit, a scratch
    # note) must not block a store upload, and the plan mirror is written by the
    # pipeline itself.
    watched = ("distribution/listing", "fastlane/metadata", "src/main/play",
               "docs/storefront.json")
    st = git(["status", "--porcelain", "--", *watched], cwd=project, timeout=60)
    dirty = [l[3:] for l in (st.stdout or "").splitlines() if l.strip()]
    if state.get("behind") or state.get("stale") or state.get("diverged") or not state.get("head"):
        from .scan import origin_tree
        tree = origin_tree(project)
        note = f" (checkout also has {len(dirty)} uncommitted asset change(s), ignored)"
        return tree, "origin/main" + (note if dirty else "")
    if dirty:
        shown = ", ".join(dirty[:6]) + (" …" if len(dirty) > 6 else "")
        return None, (f"listing assets have {len(dirty)} uncommitted change(s) ({shown}) — "
                      f"a store upload must describe a COMMIT, not a working tree; "
                      f"commit them (and re-run) or discard them")
    return project, "checkout"


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
    return _payload(r.stdout)


def _discard(package: str, edit: str, token: str, project: Path) -> None:
    run(["curl", "-s", "-X", "DELETE", "-H", f"Authorization: Bearer {token}",
         f"{API}/applications/{package}/edits/{edit}"], project, timeout=60)


def _live_images(package: str, edit: str, token: str, project: Path,
                 locale: str, image_type: str) -> list[dict]:
    from .research import _play_get
    data = _play_get(f"{API}/applications/{package}/edits/{edit}/listings/{locale}/{image_type}",
                     project, token)
    return data.get("images") or []


def publish_icon(project: Path, dry_run: bool = True, log: Any = None) -> dict[str, Any]:
    """Upload ONLY the listing icon, commit it, and read it back.

    A brand change owns one asset. The full-listing upload also rewrites the
    screenshots and the feature graphic, which a brand run neither produced nor
    proved — so it refuses on their provenance and rightly uploads nothing. This
    path touches the icon alone: the same edit machinery, the same read-back, and
    a gate that is narrower by construction, not by a bypass flag.
    """
    say = log or (lambda *_: None)
    root, source = _assets_root(project)
    say(f"~ listing icon from {source}")
    if root is None:
        return {"status": "BLOCKED", "blockers": [source],
                "hint": "commit the asset changes so the upload describes a commit"}
    listing = _listing_dir(root)
    if not listing:
        return {"status": "ERROR", "error": "no listing directory (distribution/listing/en-US)"}
    img = listing / "images" if (listing / "images").is_dir() else listing
    icon = img / "icon.png"
    if not icon.is_file():
        return {"status": "ERROR", "error": "missing asset: icon.png"}
    from . import brand_change as bc
    from . import storefront_brand as sbf

    blockers = [i for i in sbf.gate(root)
                if "icon" in i.lower() and "feature" not in i.lower()]
    blockers += [i for i in bc.gate(root)]
    if blockers:
        return {"status": "BLOCKED", "blockers": sorted(set(blockers)),
                "hint": "the icon's provenance must match the commit before Play receives it"}

    package, token, why = _play(project)
    locale = (_assets(root) or {}).get("locale") or DEFAULT_LOCALE
    plan = {"status": "DRY-RUN" if dry_run else "UPLOADING", "package": package,
            "source": source, "locale": locale,
            "icon": icon.name, "sha256": _sha256(icon)}
    if not package or not token:
        return {"status": "ERROR", "error": f"Play auth unavailable: {why}"}
    if dry_run:
        say("  (dry run — nothing was written)")
        return plan
    edit = _post(f"{API}/applications/{package}/edits", project, token)
    edit_id = edit.get("id")
    if not edit_id:
        return {"status": "ERROR", "error": f"could not open a Play edit: {edit}"}
    try:
        gone = _delete(f"{API}/applications/{package}/edits/{edit_id}/listings/"
                       f"{plan['locale']}/icon", project, token)
        if gone.get("_error"):
            raise RuntimeError(f"deleteall icon: {gone['_error']}")
        up = _upload(icon, package, edit_id, token, plan["locale"], "icon", project)
        image = up.get("image") or up
        if up.get("_error") or not image.get("id"):
            raise RuntimeError(f"upload icon: {up.get('_error') or up}")
        want = image.get("sha256") or _sha256(icon)
        say(f"  ↑ icon/{icon.name}")
        live = _live_images(package, edit_id, token, project, plan["locale"], "icon")
        if not live or live[0].get("sha256") != want:
            raise RuntimeError("the uploaded icon did not verify in the edit")
        committed = _post(f"{API}/applications/{package}/edits/{edit_id}:commit", project, token)
        if committed.get("_error") or not committed.get("id"):
            raise RuntimeError(f"commit rejected: {committed.get('_error') or committed}")
        fresh = _post(f"{API}/applications/{package}/edits", project, token)
        fresh_id = fresh.get("id")
        if not fresh_id:
            raise RuntimeError(f"commit verification could not open a new edit: {fresh}")
        try:
            got = _live_images(package, fresh_id, token, project, plan["locale"], "icon")
        finally:
            _discard(package, fresh_id, token, project)
        if not got or got[0].get("sha256") != want:
            raise RuntimeError("commit did not take effect: the app still serves a different icon")
        plan.update(status="OK", committed=True, live_sha256=got[0].get("sha256"))
        say(f"  ✓ committed listing edit {edit_id} for {package} (icon verified in a fresh edit)")
        say("    note: if Managed publishing is ON, the change waits for the owner's "
            "Publish click in Play Console → Publishing overview")
        return plan
    except Exception as exc:
        _discard(package, edit_id, token, project)
        return {"status": "ERROR", "error": str(exc)[:400]}


def publish_listing_assets(project: Path, dry_run: bool = True, log: Any = None) -> dict[str, Any]:
    """Upload icon, feature graphic and ordered screenshots; commit the edit.

    Refuses when the editorial review is missing or the critic fails, so the
    store can never receive a frame the reviewer rejected.
    """
    say = log or (lambda *_: None)
    root, source = _assets_root(project)
    say(f"~ listing assets from {source}")
    if root is None:
        return {"status": "BLOCKED", "blockers": [source],
                "hint": "commit the asset changes so the upload describes a commit"}
    assets = _assets(root)
    if assets.get("error"):
        return {"status": "ERROR", "error": assets["error"]}
    if assets["missing"]:
        return {"status": "ERROR", "error": "missing assets: " + ", ".join(assets["missing"])}

    from .asset_critic import review_store_assets
    report = review_store_assets(root)
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
    try:
        from . import storefront_brand as sbf

        blockers.extend(sbf.gate(root))
    except Exception:
        blockers.append("brand asset provenance could not be read (docs/storefront-assets.json)")
    # The frames must say HOW they were made: the rule's path is the poster
    # (Gemini paints the asset from the real render), and a code-composed frame is
    # an exception that carries a reason. An upload that cannot say which one it
    # is — or takes the exception silently — does not go to Play. Without this the
    # two paths were indistinguishable once written, so a fallback shipped looking
    # exactly like the rule's path (observed 2026-11-01).
    try:
        from . import storefront_build as sfb

        run = sfb.status(root).get("run") or {}
        if run.get("status") == "interrupted":
            blockers.append(
                f"the last storefront run died at step '{run.get('step')}' — the listing on disk "
                "may be half-written; re-run `devteam storefront-build <project>`")
    except Exception:
        pass
    try:
        from . import storefront_frames as sfr

        comp = (sfr.load_registry(root).get("composition") or {})
        if not comp:
            blockers.append(
                "no recorded composition path for the listing frames — run "
                "`devteam storefront-build <project>` (its default is the --poster path)")
        elif comp.get("path") != "poster" and not str(comp.get("why") or "").strip():
            blockers.append(
                "frames were composed by code ("
                + ", ".join(comp.get("flags") or ["no flags given"])
                + ") with no recorded reason — the rule's path is --poster")
        elif comp.get("path") == "poster":
            # A frame whose vendor-mark heal could not be proved clean does not
            # reach the store: the mark is the model's advert on the owner's page.
            marks = comp.get("watermark") or {}
            unproven = [sid for sid, m in marks.items() if not (m or {}).get("verified")]
            if not marks:
                blockers.append(
                    "the poster record carries no watermark verdict — re-run "
                    "`devteam storefront-build <project>` so every frame's heal is proved")
            elif unproven:
                blockers.append(
                    "frames carry an unproven watermark heal: " + ", ".join(sorted(unproven))
                    + " — re-run the poster pass so they are painted and healed cleanly")
    except Exception:
        blockers.append("the frames' composition record could not be read "
                        "(docs/storefront-frames.json)")
    if blockers:
        return {"status": "BLOCKED", "blockers": blockers, "score": report.get("score")}

    shots = assets["screenshots"]
    plan = {
        "status": "DRY-RUN" if dry_run else "UPLOADING",
        "package": "",
        "source": source,
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
        # "Verified" here means the edit carries the uploaded bytes — the API has
        # no way to ask whether the app uses Managed publishing, and under it a
        # committed change still waits in Publishing overview for the owner's
        # Publish click. Saying "live" would over-claim; name the gate instead.
        say(f"  ✓ committed listing edit {edit_id} for {package} (verified in a fresh edit)")
        say("    note: if Managed publishing is ON, the change now waits for the "
            "owner's Publish click in Play Console → Publishing overview")
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
