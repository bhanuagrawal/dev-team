"""The brand assets the listing needs: the Play icon and the feature graphic.

Why this exists
---------------
Screenshots were the only generative deliverable. The icon and the feature
graphic were produced by whatever path happened to exist: a hand-written HTML
template, or — worse — an upscale of the app's 192px launcher bitmap, which
shipped a soft 512 icon and a banner carrying invented numbers ("-88% SAVINGS")
that the app's own code contradicts. Nothing refused either one, because nothing
measured WHERE a brand asset came from.

The owner's rule is the same as for screenshots: the image model paints the
listing's art from the app's real material, and code enforces that it did. So:

  * the icon is painted by Gemini WITH the app's real launcher icon attached as
    the reference, then the vendor watermark is healed and the result is set to
    Play's exact 512x512;
  * the feature graphic is painted WITH the app's real hero screen attached, and
    may print only the copy the scene spec already approves;
  * every generated file is recorded in `docs/storefront-assets.json` (committed
    beside the assets, unlike `.pipeline/`), with its source, chat and sha256;
  * `gate()` refuses a listing whose icon or banner has no approved provenance,
    and both the critic and the upload path call it.

An owner-supplied file is allowed — but only through `adopt()`, which records
`source: "owner"`. That is the difference between art the owner chose and an
accident that happened to look like art.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

BRAND_REL = "docs/storefront-assets.json"
IMAGES_REL = "distribution/listing/en-US/images"
SPECS: dict[str, tuple[int, int]] = {"icon": (512, 512), "feature": (1024, 500)}
APPROVED = ("gemini", "owner")
FILES = {"icon": "icon.png", "feature": "featureGraphic.png"}
VERSION = 1


def _now() -> str:
    from .core import now_iso

    return now_iso()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _app_name(project: Path) -> str:
    """The name a store visitor reads: the listing title, then the repo name."""
    from .storefront_poster import _app_name as poster_name

    return poster_name(project)


def load(project: Path) -> dict[str, Any]:
    path = project / BRAND_REL
    if not path.is_file():
        return {"version": VERSION, "assets": {}}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"version": VERSION, "assets": {}}
    if not isinstance(data, dict):
        return {"version": VERSION, "assets": {}}
    data.setdefault("version", VERSION)
    data.setdefault("assets", {})
    return data


def save(project: Path, data: dict[str, Any]) -> Path:
    from .core import save_json

    path = project / BRAND_REL
    save_json(path, data)
    return path


# --------------------------------------------------------------------------- #
# the gate: what an approved brand asset means
# --------------------------------------------------------------------------- #
def gate(project: Path) -> list[str]:
    """Every reason the listing may not ship, given the brand assets present."""
    issues: list[str] = []
    data = load(project)
    for kind, (w, h) in SPECS.items():
        target = project / IMAGES_REL / FILES[kind]
        record = (data.get("assets") or {}).get(kind) or {}
        if not target.is_file():
            issues.append(f"{FILES[kind]} is missing")
            continue
        dims = _png_size(target)
        if dims != [w, h]:
            issues.append(f"{FILES[kind]} is {dims}, Play needs {[w, h]}")
        source = str(record.get("source") or "")
        if source not in APPROVED:
            issues.append(
                f"{FILES[kind]} has no approved provenance (source: {source or 'none'}) — "
                f"the image model paints it from the app's own material "
                f"(`devteam storefront-brand <project>`), or the owner supplies the file "
                f"(`devteam storefront-brand <project> --adopt {kind}=<png>`)")
        elif record.get("sha256") and record["sha256"] != _sha(target):
            issues.append(
                f"{FILES[kind]} changed after it was recorded — the file on the listing is "
                f"not the file {source} produced; re-run the generation or re-adopt it")
    return issues


def status(project: Path) -> dict[str, Any]:
    data = load(project)
    out: dict[str, Any] = {}
    for kind in SPECS:
        record = (data.get("assets") or {}).get(kind) or {}
        target = project / IMAGES_REL / FILES[kind]
        out[kind] = {
            "file": str(target.relative_to(project)) if target.is_file() else None,
            "dims": _png_size(target) if target.is_file() else None,
            "source": record.get("source"),
            "generated": record.get("generated_at"),
            "chat": record.get("chat"),
        }
    return out


def format_status(project: Path) -> str:
    issues = gate(project)
    lines = ["STOREFRONT BRAND ASSETS", "=" * 60]
    for kind, info in status(project).items():
        dims = "x".join(str(d) for d in info["dims"]) if info.get("dims") else "-"
        lines.append(
            f"  {kind:<8} {info['source'] or '(none)':<8} {dims:<12} "
            f"{(info['file'] or 'missing')}")
    if issues:
        lines.append("\nGATE: refused")
        for issue in issues:
            lines.append(f"  ✗ {issue}")
    else:
        lines.append("\nGATE: approved")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# prompts — grounded in the app's own material, never in a template
# --------------------------------------------------------------------------- #
def _hero_scene(project: Path) -> dict[str, Any] | None:
    from . import storefront_preview as sp

    spec = sp.load_spec(project, create=False)
    scenes = spec.get("scenes") or []
    return scenes[0] if scenes else None


def icon_prompt(project: Path) -> str:
    """Paint the Play icon from the app's real launcher icon.

    Deliberately says nothing about style: the model's job is the art. What it
    pins is what must stay true — the mark is the app's existing one, the canvas
    is Play's, and the corner the watermark heals must stay plain.
    """
    return "\n".join([
        f"The attached image is the real launcher icon of my Android app {_app_name(project)}.",
        "Repaint it as the Google Play store icon: a square 1:1 composition of the SAME mark.",
        "Keep the subject, its silhouette and its colour relationships — this icon must be "
        "recognisable as the same app at 48 pixels.",
        "Fill the square edge to edge; the store shows it as a square tile and does not mask it "
        "for you. Keep the motif centred with generous padding.",
        "No text, no letters the mark does not already contain, no device frame, no badge, "
        "no price, no rating, no other product's logo.",
        "Keep the bottom-right corner free of detail: leave it plain background.",
    ])


def feature_prompt(project: Path) -> str:
    """Paint the 1024x500 banner, printing only approved copy.

    The banner is the one asset a store visitor sees beside the title, so it may
    carry the hero frame's words — and nothing else. Numbers, percentages and
    claims are the app's to make in its own UI, never the poster's.
    """
    scene = _hero_scene(project) or {}
    words = [
        f"label: {scene.get('badge') or ''}",
        f"headline: {scene.get('headline') or ''}",
        f"subtext: {scene.get('sub') or ''}",
    ]
    return "\n".join([
        f"Create a wide Google Play feature graphic (banner) for my Android app {_app_name(project)}.",
        "The attached image is the app's real screen: you may show it inside the banner, but do "
        "not invent or replace the app's interface, and do not invent any other UI.",
        "Print exactly these words, and no other text:",
        *words,
        "Put the phone in the left two thirds; the RIGHT third must be plain background with "
        "no phone, no text and no bright detail — the vendor mark is healed there.",
        "Keep every word inside the central 80% of the frame — the banner is cropped top and "
        "bottom when the store resizes it.",
        "No watermark, no logos, no price, no rating badges, no invented numbers or claims.",
        "Keep the bottom-right corner free of text and of the phone: leave it plain background.",
    ])


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #
def _reference(project: Path, kind: str, log=None) -> Path | None:
    """The real material the model is shown: the launcher icon, the hero screen."""
    if kind == "icon":
        candidates = sorted((project / "composeApp/src/androidMain/res").glob(
            "mipmap-*/ic_launcher.png"), key=lambda p: p.stat().st_size, reverse=True)
        candidates = [p for p in candidates if "_round" not in p.name]
        if candidates:
            return candidates[0]
        current = project / IMAGES_REL / FILES["icon"]
        return current if current.is_file() else None
    scene = _hero_scene(project)
    if scene:
        render = project / ".pipeline/storefront_scenes" / f"{scene.get('id')}.png"
        if render.is_file():
            return render
    if log:
        log("  brand: no hero render for the banner reference — run storefront-scenes --render first")
    return None


def generate(project: Path, kinds: tuple[str, ...] = ("icon", "feature"),
             force: bool = False, log=None) -> dict[str, Any]:
    """Paint the brand assets, or finish the ones already painted.

    Three states per asset, and they must not be confused:
      * nothing painted        -> ask Gemini, with the real launcher icon / hero screen;
      * painted, not recorded  -> CLEAN AND RECORD it (a model output that failed the
                                  watermark gate is not a reason to burn another
                                  generation — the cleaner is ours to fix);
      * painted and recorded   -> reuse, deterministic.
    """
    from . import storefront_poster as spt

    existing = load(project)
    raw_dir = project / ".pipeline/storefront_brand"
    raw_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[dict[str, str]] = []
    reused: list[str] = []

    for kind in kinds:
        raw = raw_dir / f"raw-{kind}.png"
        record = (existing.get("assets") or {}).get(kind) or {}
        current = project / IMAGES_REL / FILES[kind]
        approved = (record.get("source") in APPROVED and current.is_file()
                    and (not record.get("sha256") or record["sha256"] == _sha(current)))
        # A raw the record has not accounted for wins over a "recorded" asset: the
        # listing must describe the paint that is on disk, not an older one.
        stale_raw = raw.is_file() and record.get("raw_sha256") != _sha(raw)
        if not force and approved and not stale_raw:
            if log:
                log(f"  brand: reusing {kind} ({record.get('source')}, "
                    f"{record.get('generated_at') or 'unknown date'})")
            reused.append(kind)
            continue
        if not force and raw.is_file() and not stale_raw:
            if log:
                log(f"  brand: {kind} raw is recorded but the listing is not — re-deriving it")
        elif not force and raw.is_file():
            if log:
                log(f"  brand: {kind} is painted but not recorded — cleaning it")
        else:
            ref = _reference(project, kind, log=log)
            if not ref:
                return {"status": "BLOCKED", "error": f"no reference image for the {kind}",
                        "hint": "run `devteam storefront-scenes <project> --render` first"}
            prompt = icon_prompt(project) if kind == "icon" else feature_prompt(project)
            jobs.append({"kind": kind, "out": str(raw), "src": str(ref), "prompt": prompt})

    chat = None
    painted: dict[str, list[int] | None] = {}
    if jobs:
        if log:
            log(f"  brand: Gemini (browser) painting {len(jobs)} asset(s)")
        try:
            import subprocess

            res = subprocess.run(
                ["browser-control", "execute", "--json", spt._batch_script(jobs)],
                capture_output=True, text=True, timeout=900)
        except FileNotFoundError:
            return {"status": "BLOCKED", "error": "browser-control is not installed"}
        except Exception as exc:  # timeout and anything else the browser raises
            return {"status": "BLOCKED", "error": f"Gemini did not finish: {exc}"}
        if res.returncode != 0:
            return {"status": "BLOCKED",
                    "error": (res.stderr or "browser-control failed").strip()[:300]}
        try:
            payload = json.loads(res.stdout)
        except Exception:
            return {"status": "ERROR", "error": "browser-control returned no JSON"}
        value = payload.get("value") or {}
        chat = value.get("chat")
        results = value.get("results") or []
        for job, result in zip(jobs, results):
            if not result.get("ok"):
                return {"status": "BLOCKED",
                        "error": str(result.get("error") or "no image produced"), "chat": chat}
            painted[job["kind"]] = [result.get("w"), result.get("h")]
            (project / ".pipeline" / f"gemini-brand-{job['kind']}.txt").write_text(job["prompt"])

    if not jobs and len(reused) == len(kinds):
        # Every asset asked for is approved and its paint is accounted for.
        if log:
            log("  brand: nothing to do — everything asked for is already recorded")
        return {"status": "OK", "generated": [], "reused": reused}

    data = load(project)
    made: list[str] = []
    for kind in kinds:
        if kind in reused:
            continue
        raw = raw_dir / f"raw-{kind}.png"
        if not raw.is_file():
            continue
        record = (data.get("assets") or {}).get(kind) or {}
        # Brand assets are strict: a vendor mark on the icon or the banner is never
        # acceptable, so an unprovable heal refuses instead of shipping.
        cleaned = spt.clean_poster(raw, strict=True, half_ratio=0.075)
        if cleaned.get("status") != "OK":
            return {"status": cleaned.get("status", "ERROR"),
                    "error": cleaned.get("error", "the vendor watermark could not be removed"),
                    "hint": "a model mark must not ship on the listing"}
        width, height = SPECS[kind]
        target = project / IMAGES_REL / FILES[kind]
        sized = spt.to_play_size(Path(cleaned.get("file") or raw), target, width, height)
        if sized.get("status") != "OK":
            return {"status": "ERROR", "error": sized.get("error", "resize failed")}
        data.setdefault("assets", {})[kind] = {
            "source": "gemini",
            "generated_at": _now(),
            "chat": chat or record.get("chat"),
            "prompt_file": f".pipeline/gemini-brand-{kind}.txt",
            "raw": str(raw.relative_to(project)),
            "raw_sha256": _sha(raw),
            "raw_dimensions": painted.get(kind) or record.get("raw_dimensions"),
            "watermark": {"box": cleaned.get("box"), "residual": cleaned.get("residual"),
                          "verified": cleaned.get("verified", True)},
            "size": [width, height],
            "sha256": _sha(target),
        }
        made.append(kind)
        if log:
            log(f"  brand: ✓ {FILES[kind]} (-> {width}x{height}, mark healed"
                + (f" at {cleaned.get('box')}" if cleaned.get("box") else "") + ")")
    save(project, data)
    return {"status": "OK", "generated": made, "reused": reused, "chat": chat}


def adopt(project: Path, kind: str, path: Path, note: str = "") -> dict[str, Any]:
    """Record an OWNER-supplied brand asset: the one legitimate bypass.

    The file is copied to the listing and its provenance says `owner`, so the
    gate can still tell chosen art from an accident. Dimensions are enforced
    here, because Play refuses the upload anyway and a late refusal is worse.
    """
    if kind not in SPECS:
        return {"status": "ERROR", "error": f"unknown asset {kind!r} (use icon|feature)"}
    src = Path(path).expanduser()
    if not src.is_file():
        return {"status": "BLOCKED", "error": f"no file at {src}"}
    width, height = SPECS[kind]
    dims = _png_size(src)
    if dims != [width, height]:
        return {"status": "BLOCKED",
                "error": f"{src.name} is {dims}; the {kind} must be exactly {width}x{height}",
                "hint": "resize it first — Play refuses anything else"}
    target = project / IMAGES_REL / FILES[kind]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(src.read_bytes())
    data = load(project)
    data.setdefault("assets", {})[kind] = {
        "source": "owner",
        "generated_at": _now(),
        "supplied_from": str(src),
        "note": note,
        "size": [width, height],
        "sha256": _sha(target),
    }
    save(project, data)
    return {"status": "OK", "kind": kind, "file": str(target.relative_to(project))}


def ensure(project: Path, force: bool = False, log=None) -> dict[str, Any]:
    """The gate-driven step the build calls: generate whatever is not approved."""
    issues = gate(project)
    if not issues and not force:
        if log:
            log("  brand: icon and feature graphic are approved")
        return {"status": "OK", "generated": [], "reused": ["icon", "feature"]}
    needs = tuple(kind for kind in SPECS
                  if not (project / IMAGES_REL / FILES[kind]).is_file()
                  or ((load(project).get("assets") or {}).get(kind) or {}).get("source")
                  not in APPROVED or force)
    if not needs:
        return {"status": "OK", "generated": [], "reused": ["icon", "feature"]}
    return generate(project, kinds=needs, force=force, log=log)


def _png_size(path: Path) -> list[int] | None:
    import struct

    try:
        head = path.read_bytes()[:24]
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return list(struct.unpack(">II", head[16:24]))
    except Exception:
        return None
    return None
