"""The brand assets the listing needs: the Play icon and the feature graphic.

Why this exists
---------------
Screenshots were the only generative deliverable. The icon and the feature
graphic were produced by whatever path happened to exist: a hand-written HTML
template, or — worse — an upscale of the app's 192px launcher bitmap, which
shipped a soft 512 icon and a banner carrying invented numbers ("-88% SAVINGS")
that the app's own code contradicts. Nothing refused either one, because nothing
measured WHERE a brand asset came from.

Where the art comes from (owner, 2026-11-23: "sirf feature graphics banner nahi
banwana … ye dev team ke code se hi hatado"):

  * the team GENERATES neither brand asset. The icon is a Stitch design adopted
    from its artboard (`adopt_stitch_icon`), and the feature graphic is a file
    the owner supplies (`adopt`); nothing in this repo paints a banner any more;
  * code still owns what it can check: Play's exact dimensions, the vendor-mark
    heal, and the provenance record in `docs/storefront-assets.json` (committed
    beside the assets, unlike `.pipeline/`), with source and sha256;
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
# Who may have made a listing's brand asset: a Stitch design or the owner's own
# file — the team paints neither any more (owner, 2026-11-23). `chatgpt`/`gemini`
# stay approved only so listings painted before that directive keep the provenance
# they were honest about; nothing in the repo records those sources now.
APPROVED = ("chatgpt", "gemini", "stitch", "owner")
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
                    f"the owner supplies the feature graphic "
                    f"(`devteam storefront-brand <project> --adopt feature=<png>`, Play's exact "
                    f"1024x500); the team does not generate a banner")
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


# --------------------------------------------------------------------------- #
# adoption — the file exists; code records where it came from
# --------------------------------------------------------------------------- #
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


def ensure(project: Path, log=None) -> dict[str, Any]:
    """The gate-driven step the build calls.

    Nothing is generated here (owner, 2026-11-23): the icon is a Stitch design and
    the banner is the owner's file. A run that finds either missing must STOP with
    the instruction that produces it, never paint a substitute.
    """
    issues = gate(project)
    if not issues:
        if log:
            log("  brand: icon and feature graphic are approved")
        return {"status": "OK", "generated": [], "reused": ["icon", "feature"]}
    hints: list[str] = []
    if any(FILES["icon"] in i for i in issues):
        hints.append("`devteam storefront-brand <project> --stitch-prompt`, generate the icon in "
                     "Stitch, then `--adopt-stitch <file.html>` (or `--adopt icon=<png>`)")
    if any(FILES["feature"] in i for i in issues):
        hints.append("supply the 1024x500 feature graphic with "
                     "`devteam storefront-brand <project> --adopt feature=<png>`")
    return {"status": "BLOCKED",
            "error": "brand asset(s) not approved: " + "; ".join(issues[:2]),
            "hint": "; ".join(hints) or "re-adopt the asset the record names"}


def _png_size(path: Path) -> list[int] | None:
    import struct

    try:
        head = path.read_bytes()[:24]
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return list(struct.unpack(">II", head[16:24]))
    except Exception:
        return None
    return None
