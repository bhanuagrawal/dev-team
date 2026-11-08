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
# The icon comes from Stitch (a designed, adopted artefact); the banner and every
# screenshot are painted by Gemini. One owner-supplied file is the only other
# legitimate source. Anything else — an HTML template nobody adopted, an upscale of
# the launcher bitmap — is refused by `gate()`.
APPROVED = ("gemini", "stitch", "owner")
GEMINI_KINDS: tuple[str, ...] = ("feature",)
ICON_HTML_REL = "docs/storefront-icon.html"
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
            if kind == "icon":
                issues.append(
                    f"{FILES[kind]} has no approved provenance (source: {source or 'none'}) — "
                    f"the icon is a STITCH design, adopted from its saved HTML "
                    f"(`devteam storefront-brand <project> --stitch-prompt`, generate it, then "
                    f"`--adopt-stitch <file.html>`); the owner may supply the PNG instead "
                    f"(`--adopt icon=<png>`)")
            else:
                issues.append(
                    f"{FILES[kind]} has no approved provenance (source: {source or 'none'}) — "
                    f"the image model paints it from the app's own screen "
                    f"(`devteam storefront-brand <project> --generate`), or the owner supplies "
                    f"the file (`--adopt feature=<png>`)")
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


def icon_stitch_prompt(project: Path) -> str:
    """The prompt handed to STITCH for the Play icon.

    Stitch is the design surface, so this asks for a designed 512x512 tile rather
    than describing pixels: the mark, the palette and the prohibitions. The HTML it
    returns is adopted (`adopt_stitch_icon`) and rendered at 512 — code owns size and
    provenance, Stitch owns the craft.
    """
    try:
        from .design import extract_design_tokens

        tokens = extract_design_tokens(project)
    except Exception:
        tokens = {}
    accent = tokens.get("primary_accent") or "#E8442A"
    base = tokens.get("surface_base") or "#0D0D0D"
    name = _app_name(project)
    return "\n".join([
        f"Design the Google Play store icon for the Android app {name}.",
        "ARTBOARD: a single square 512x512 tile, edge to edge, nothing outside it.",
        f"PALETTE: surface base {base} as the ground, {accent} as the one accent. "
        f"High contrast, crisp geometric silhouette, no extra hues.",
        "SUBJECT: the app's own mark, the same silhouette its launcher icon shows, "
        "rendered flat and geometric. Keep it recognisable at 48 pixels: one "
        "silhouette, generous padding, no fine detail.",
        "STYLE: flat vector, crisp edges, no bevel, no drop shadow, no 3D depth, no glow "
        "that spills outside the tile, no photographic texture.",
        "FORBIDDEN: any word other than the mark itself, device frames, badges, prices, "
        "ratings, other product logos, gradients that change the hue.",
        "Keep the bottom-right corner empty background — a vendor watermark is removed there.",
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


def generate(project: Path, kinds: tuple[str, ...] = GEMINI_KINDS,
             force: bool = False, log=None) -> dict[str, Any]:
    """Paint the GEMINI-painted brand assets (the feature graphic), or finish one.

    The icon is deliberately NOT here: it is a Stitch design, adopted through
    `adopt_stitch_icon`. Asking Gemini for it would produce a second, competing
    rendition of the app's mark — the exact drift this split exists to prevent.

    Three states per asset, and they must not be confused:
      * nothing painted        -> ask Gemini, with the real launcher icon / hero screen;
      * painted, not recorded  -> CLEAN AND RECORD it (a model output that failed the
                                  watermark gate is not a reason to burn another
                                  generation — the cleaner is ours to fix);
      * painted and recorded   -> reuse, deterministic.
    """
    from . import storefront_poster as spt

    kinds = tuple(k for k in kinds if k in GEMINI_KINDS)
    if not kinds:
        return {"status": "BLOCKED",
                "error": "the icon is not painted by the image model",
                "hint": "run `devteam storefront-brand <project> --stitch-prompt`, generate the "
                        "icon in Stitch, then `--adopt-stitch <file.html>`"}
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
            # The shared browser script proves delivery by the SENT message's
            # attachment SHAPE (see storefront_poster._batch_script): the model
            # re-encodes the thumbnail, but its width/height ratio is the
            # reference's own and cannot be faked. A job without `ar` made that
            # proof impossible — every brand paint failed with "the render is not
            # in the sent message" while the upload had actually worked
            # (observed 2026-11-06). Carry the reference's measured shape.
            size = _png_size(ref)
            aspect = round(size[0] / size[1], 3) if size and size[1] else 0
            jobs.append({"kind": kind, "out": str(raw), "src": str(ref),
                         "prompt": prompt, "ar": aspect})

    chat = None
    painted: dict[str, list[int] | None] = {}
    if jobs:
        if log:
            log(f"  brand: painting {len(jobs)} asset(s)")
        outcome = spt.paint_batch(jobs, timeout=900, log=log)
        if not outcome.get("ok"):
            return {"status": "BLOCKED",
                    "error": (outcome.get("script_invalid") or outcome.get("error")
                              or "no image model produced the brand asset"),
                    "detail": "; ".join(outcome.get("attempts") or [])[:300]}
        value = outcome.get("value") or {}
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


def _render_html(html: Path, out: Path, width: int, height: int) -> dict[str, Any]:
    """Render an adopted HTML document at an exact pixel size."""
    import subprocess

    from .marketing_asset_generator import _find_chrome_executable

    chrome = _find_chrome_executable()
    if not chrome:
        return {"status": "ERROR", "error": "Google Chrome not found to render the icon"}
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
             "--virtual-time-budget=8000", f"--window-size={width},{height}",
             f"--screenshot={out.resolve()}", html.resolve().as_uri()],
            capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "error": "rendering the adopted HTML timed out"}
    if not out.is_file():
        return {"status": "ERROR", "error": "the adopted HTML produced no PNG"}
    dims = _png_size(out)
    if dims != [width, height]:
        return {"status": "ERROR",
                "error": f"rendered {dims} instead of {[width, height]} — the artboard must be "
                         f"exactly {width}x{height}"}
    return {"status": "OK", "file": out, "size": dims}


def adopt_stitch_icon(project: Path, html_file: Path, stitch_source: str = "",
                      note: str = "") -> dict[str, Any]:
    """Record a STITCH design as the Play icon: keep its source, render at 512.

    The design Stitch produced (HTML, or the standalone SVG it returns for an icon)
    is the deliverable, so it is saved inside the project
    (`docs/storefront-icon.html`) and committed with the PNG: the icon can be
    re-rendered from it, and the provenance names the screen it came from. Code owns
    the size, the provenance and the watermark check; Stitch owns the craft.
    """
    src = Path(html_file).expanduser()
    if not src.is_file():
        return {"status": "BLOCKED", "error": f"no HTML at {src}"}
    html = src.read_text(errors="ignore")
    if len(html) < 400:
        return {"status": "BLOCKED", "error": "the adopted design is empty or truncated"}
    # Stitch returns an icon design as a standalone SVG (an artboard, not a page).
    # Wrap it in a page that is exactly the tile, so Chrome's screenshot IS the icon.
    if html.lstrip().startswith("<svg"):
        html = ("<!DOCTYPE html><html><body style=\"margin:0;padding:0;background:#000;"
                "overflow:hidden\">" + html + "</body></html>")
    kept = project / ICON_HTML_REL
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(html)
    width, height = SPECS["icon"]
    target = project / IMAGES_REL / FILES["icon"]
    # Render and clean in the pipeline's own directory: the listing holds the final
    # PNG and nothing else (a stray icon.clean.png once shipped inside Play's images).
    scratch = project / ".pipeline/storefront_brand/icon-render.png"
    rendered = _render_html(kept, scratch, width, height)
    if rendered.get("status") != "OK":
        return rendered
    from . import storefront_poster as spt

    cleaned = spt.clean_poster(scratch, strict=True, half_ratio=0.06,
                               out=project / ".pipeline/storefront_brand/icon-clean.png")
    if cleaned.get("status") != "OK":
        return {"status": cleaned.get("status", "ERROR"),
                "error": cleaned.get("error", "the icon still carries a mark"),
                "hint": "re-generate in Stitch with the bottom-right corner empty"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(Path(cleaned.get("file") or scratch).read_bytes())
    data = load(project)
    data.setdefault("assets", {})["icon"] = {
        "source": "stitch",
        "generated_at": _now(),
        "stitch_source": stitch_source,
        "html": ICON_HTML_REL,
        "html_sha256": _sha(kept),
        "note": note,
        "size": [width, height],
        "sha256": _sha(target),
    }
    save(project, data)
    return {"status": "OK", "kind": "icon", "file": str(target.relative_to(project)),
            "html": ICON_HTML_REL}


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
    """The gate-driven step the build calls.

    Only the feature graphic can be generated here: the icon is a Stitch design, and
    a run that finds it missing must STOP with that instruction instead of quietly
    producing a second rendition of the app's mark.
    """
    data = load(project)
    issues = gate(project)
    icon_ok = not any("icon.png" in i for i in issues) and \
        ((data.get("assets") or {}).get("icon") or {}).get("source") in APPROVED
    if not icon_ok:
        hint = ("run `devteam storefront-brand <project> --stitch-prompt`, generate the icon in "
                "Stitch, then `--adopt-stitch <file.html>` (the owner may pass a finished PNG "
                "with `--adopt icon=<png>`)")
        return {"status": "BLOCKED", "error": "the Play icon has no approved Stitch design",
                "hint": hint}
    if not issues and not force:
        if log:
            log("  brand: icon and feature graphic are approved")
        return {"status": "OK", "generated": [], "reused": ["icon", "feature"]}
    needs = tuple(kind for kind in GEMINI_KINDS
                  if force
                  or not (project / IMAGES_REL / FILES[kind]).is_file()
                  or ((data.get("assets") or {}).get(kind) or {}).get("source") not in APPROVED)
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


# --------------------------------------------------------------------------- #
# the launcher: the icon a user actually taps must be the listing art
# --------------------------------------------------------------------------- #
# Play rejected a shipped update under the Misleading Claims policy because the
# installed icon differed from the listing icon. The audit could not see it: a
# project with only an adaptive VECTOR launcher has no bitmap to compare, so the
# check reported "no launcher bitmap" forever and passed — and a launcher left
# over from a scaffold can therefore ship for months under an adopted listing
# icon. So the brand pass renders the SAME artboard into the launcher at every
# density — one design, one set of pixels — and records what it wrote.
LAUNCHER_DENSITIES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96,
                      "xxhdpi": 144, "xxxhdpi": 192}
# An adaptive icon's foreground is 108dp with the art inside the middle 66%.
ADAPTIVE_SIZES = {"mdpi": 108, "hdpi": 162, "xhdpi": 216,
                  "xxhdpi": 324, "xxxhdpi": 432}
SAFE_ZONE = 0.66


def _ffmpeg() -> str:
    import shutil
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg is required to derive the launcher from the icon art")
    return exe


def _corner_hex(png: Path) -> str:
    """The icon art's own top-left colour, so an adaptive icon has no visible seam."""
    import subprocess
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(png),
                        "-vf", "crop=1:1:0:0,format=rgb24", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=60)
    data = r.stdout or b""
    if len(data) < 3:
        return "#14191F"
    return "#{:02X}{:02X}{:02X}".format(data[0], data[1], data[2])


def _pad_art(src: Path, size: int, inner: int, out: Path, colour: str) -> bool:
    """Scale the art into `inner` px and centre it on a `size` px canvas."""
    import subprocess
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = (f"scale={inner}:{inner},pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=0x{colour.lstrip('#')}")
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(src), "-vf", vf, "-frames:v", "1", str(out)],
                       capture_output=True, timeout=120)
    return r.returncode == 0 and out.is_file() and out.stat().st_size > 0


def _resize(src: Path, size: int, out: Path) -> bool:
    import subprocess
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(src), "-vf", f"scale={size}:{size}",
                        "-frames:v", "1", str(out)], capture_output=True, timeout=120)
    return r.returncode == 0 and out.is_file() and out.stat().st_size > 0


def launcher_files(project: Path) -> dict[str, str]:
    """The launcher assets the brand pass owns, repo-relative."""
    out = {}
    for dens in LAUNCHER_DENSITIES:
        out[f"legacy:{dens}"] = (
            f"composeApp/src/androidMain/res/mipmap-{dens}/ic_launcher.png")
        out[f"adaptive:{dens}"] = (
            f"composeApp/src/androidMain/res/mipmap-{dens}/ic_launcher_foreground.png")
    return out


def write_launcher(project: Path, log=None, android_res: str = "composeApp/src/androidMain/res",
                   background: str = "") -> dict[str, Any]:
    """Render the recorded icon artboard into the launcher, at every density.

    Refuses when there is no adopted icon: an upscaled bitmap or a template is not
    the listing art, and inventing one is exactly the mismatch this prevents.
    """
    data = load(project)
    icon = (data.get("assets") or {}).get("icon") or {}
    if icon.get("source") not in APPROVED or not icon.get("html"):
        return {"status": "BLOCKED",
                "error": "no adopted icon art is recorded — run "
                         "`devteam storefront-brand <project> --stitch-prompt` then "
                         "`--adopt-stitch <file.html>` first"}
    html = project / str(icon["html"])
    if not html.is_file():
        return {"status": "BLOCKED", "error": f"the recorded artboard is missing: {html}"}
    res = project / android_res
    if not res.is_dir():
        return {"status": "BLOCKED", "error": f"no Android res directory at {res}"}

    scratch = project / ".pipeline/storefront_brand/launcher-art.png"
    rendered = _render_html(html, scratch, *SPECS["icon"])
    if rendered.get("status") != "OK":
        return rendered
    colour = background or _corner_hex(scratch)

    written: list[str] = []
    for dens, px in LAUNCHER_DENSITIES.items():
        legacy = res / f"mipmap-{dens}/ic_launcher.png"
        round_png = res / f"mipmap-{dens}/ic_launcher_round.png"
        # The legacy icon fills its box; a launcher masks it into its own shape.
        if _resize(scratch, px, legacy):
            written.append(str(legacy.relative_to(project)))
        _resize(scratch, px, round_png)
        written.append(str(round_png.relative_to(project)))
    for dens, px in ADAPTIVE_SIZES.items():
        fg = res / f"mipmap-{dens}/ic_launcher_foreground.png"
        if _pad_art(scratch, px, max(1, int(px * SAFE_ZONE)), fg, colour):
            written.append(str(fg.relative_to(project)))
    # The adaptive icon must point at the rendered foreground, or API 26+ keeps
    # drawing whatever vector was there before (the mismatch this closes).
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        xml = res / "mipmap-anydpi-v26" / name
        if not xml.parent.is_dir():
            continue
        xml.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '    <background android:drawable="@mipmap/ic_launcher_background" />\n'
            '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
            '</adaptive-icon>\n')
        written.append(str(xml.relative_to(project)))
    # The adaptive background is one flat colour: the art's own corner, so the
    # masked tile has no seam where the foreground ends.
    bg_png = res / "mipmap-xxxhdpi/ic_launcher_background.png"
    if _pad_art(scratch, 432, 432, bg_png, colour):
        for dens, px in ADAPTIVE_SIZES.items():
            dst = res / f"mipmap-{dens}/ic_launcher_background.png"
            if dens != "xxxhdpi":
                _pad_art(scratch, px, px, dst, colour)
            written.append(str(dst.relative_to(project)))

    icon["launcher"] = {
        "generated_at": _now(),
        "from_html": str(icon["html"]),
        "html_sha256": icon.get("html_sha256") or _sha(html),
        "background": colour,
        "files": {k: v for k, v in launcher_files(project).items()
                  if (project / v).is_file()},
        "sizes": {"legacy": LAUNCHER_DENSITIES, "adaptive": ADAPTIVE_SIZES},
    }
    data.setdefault("assets", {})["icon"] = icon
    save(project, data)
    if log:
        log(f"  brand: launcher rendered from {icon['html']} at "
            f"{len(LAUNCHER_DENSITIES)} density(ies) + adaptive foregrounds "
            f"(background {colour})")
    return {"status": "OK", "files": written, "background": colour,
            "provenance": BRAND_REL}
