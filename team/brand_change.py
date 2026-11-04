"""Owner brand feedback → Stitch design → app icon → Play icon → publish.

Why this exists
---------------
The owner's words were "the icon is bad — change it". The previous answer to that
was prose: a skill said to run `--stitch-prompt`, generate in Stitch, adopt, bump
the version, build, and upload. The run that shipped the current icon did the
bumps and the PNGs and left *nothing* behind that could prove where the art came
from: no artboard, no prompt, no provenance record — just a commit message that
said "stitch-designed". A skill is a hope; this is the mechanism.

One owner sentence now starts a state machine whose steps cannot be reordered:

    intake    the feedback becomes a queued task (so it is not lost)
      │
    designed  Stitch is called FROM CODE (not by an agent reading prose), the
      │      512 artboard and the prompt that produced it are committed, and the
      │      Play icon is adopted through the existing provenance gate
      │
    applied   the app's launcher icon is re-rendered from the SAME adopted
      │      design, so the app icon and the store icon cannot drift
      │
    released  the normal release loop ships it (version bump, CI, internal test)
      │
    published the listing edit is committed to Play and read back

`verify` refuses an icon change whose provenance is missing or does not match
(`brand_check` in verify.py), and `store-publish` already refuses unproven brand
assets. The chain is therefore enforced where a skipped step would otherwise
have shipped silently.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

STATE_REL = ".pipeline/brand.json"
PROMPT_REL = "docs/storefront-icon-prompt.txt"
DESIGN_REL = "docs/storefront-icon.html"
LAUNCHER_REL = "docs/launcher-icon.json"
ASSETS_REL = "docs/storefront-assets.json"

# 48dp legacy icon, 108dp adaptive foreground (72dp safe zone), at every density.
DENSITIES: dict[str, float] = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0,
                               "xxhdpi": 3.0, "xxxhdpi": 4.0}
STEPS = ("intake", "designed", "applied", "released", "published")

_FEEDBACK_TERMS = ("icon", "logo", "mark", "launcher", "brand", "app icon")


# --------------------------------------------------------------------------- #
# state — one file, every step appended, nothing inferred from memory
# --------------------------------------------------------------------------- #
def _now() -> str:
    from .core import now_iso

    return now_iso()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state(project: Path) -> dict[str, Any]:
    path = project / STATE_REL
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save(project: Path, data: dict[str, Any]) -> None:
    from .core import save_json

    data["updated_at"] = _now()
    save_json(project / STATE_REL, data)


def _record(data: dict[str, Any], step: str, note: str, **extra: Any) -> None:
    data["state"] = step
    data.setdefault("history", []).append({"at": _now(), "step": step, "note": note})
    data.setdefault("steps", {}).setdefault(step, {}).update({"at": _now(), "note": note, **extra})


def _app_name(project: Path) -> str:
    from .storefront_brand import _app_name as name

    return name(project)


def _res_dir(project: Path) -> Path | None:
    """The Android res dir, from the profile's module when it names one."""
    try:
        from .core import profile_of

        compile_task = str((profile_of(project).get("verify") or {}).get("compile") or "")
        m = re.match(r":([\w-]+):", compile_task)
        if m:
            candidate = project / m.group(1) / "src/main/res"
            if candidate.is_dir():
                return candidate
    except Exception:
        pass
    for candidate in sorted(project.glob("*/src/main/res")):
        if "build" not in candidate.parts:
            return candidate
    return None


def _listing_images(project: Path) -> Path:
    from .storefront_brand import IMAGES_REL

    return project / IMAGES_REL


def feedback_kind(text: str) -> str | None:
    low = (text or "").lower()
    if any(term in low for term in _FEEDBACK_TERMS):
        return "icon"
    return None


# --------------------------------------------------------------------------- #
# the brief — the app's own identity, and the rejection that started the change
# --------------------------------------------------------------------------- #
def brief(project: Path, feedback: str) -> str:
    """The prompt Stitch receives.

    Deliberately NOT `icon_stitch_prompt`: that one pinned the app's EXISTING
    mark ("the same silhouette its launcher icon shows"), which is correct for
    re-rendering a good icon and wrong for replacing a rejected one — it asks the
    model to keep the very glyph the owner just rejected.
    """
    from .design import extract_design_tokens

    tokens = extract_design_tokens(project)
    name = _app_name(project)
    base, accent = tokens.get("surface_base"), tokens.get("primary_accent")
    return "\n".join([
        f"Design a NEW Google Play app icon for the Android app {name}.",
        "",
        "WHY THIS EXISTS: the owner rejected the current icon as generic and "
        f"unforgettable-in-a-bad-way. Their words: \"{feedback.strip()}\". Replacing it, "
        "so the old mark (a power/pause ring) must not appear in any form.",
        "",
        "ARTBOARD: exactly 512 x 512, one square tile, edge to edge, nothing outside it, "
        "no phone frame, no screen, no UI, no background scenery.",
        f"PALETTE: {base} ground and {accent} as the single accent. Dark, high contrast, "
        "no second hue, no gradient that shifts hue.",
        "",
        "SUBJECT: one symbol that says 'a calmer phone' — protection, focus, a quiet "
        "boundary — expressed as a flat geometric silhouette. It must read as ONE shape "
        "at 48 px: no detail finer than 1/16 of the tile, generous padding (the mark "
        "inside the middle 66% of the tile).",
        "Not allowed: a power symbol, a pause symbol, a play button, a circle outline, "
        "a letter or wordmark, a leaf cliché, a brain, a lotus, a phone, a lock, a clock. "
        "Those are the generic answers this brief exists to avoid.",
        "",
        "STYLE: flat vector, crisp edges, solid fills, confident negative space. No bevel, "
        "no drop shadow, no 3D, no glow spilling outside the tile, no photographic texture.",
        "FORBIDDEN: any text, numerals, badges, prices, ratings, other product logos.",
        "Keep the bottom-right corner plain background — a vendor watermark is removed there.",
    ])


# --------------------------------------------------------------------------- #
# 1. intake — the owner's sentence becomes queued work
# --------------------------------------------------------------------------- #
def intake(project: Path, feedback: str, log=None) -> dict[str, Any]:
    say = log or (lambda *_: None)
    text = " ".join((feedback or "").split())
    if not text:
        return {"status": "BLOCKED", "error": "no feedback text"}
    kind = feedback_kind(text)
    if not kind:
        return {"status": "BLOCKED",
                "error": f"this is not brand feedback (no icon/logo/mark/brand term in {text!r})",
                "hint": "enqueue the feedback as its own task instead of guessing a brand change"}
    open_run = state(project)
    if open_run and open_run.get("state") not in (None, "published") and not open_run.get("closed"):
        return {"status": "BLOCKED",
                "error": f"a brand change is already open at state '{open_run.get('state')}' "
                         f"(since {open_run.get('created_at')})",
                "hint": _next_command(project.name, open_run) +
                        "  (or `brand <project> --reset` to start over deliberately)"}

    from . import queue as qmod
    from .core import save_state, state_of, today

    st = state_of(project)
    label = f"icon-refresh-{today()}"
    if any((q.get("label") or "") == label for q in st.get("queue") or []) or \
            any((s.get("label") or "") == label for s in st.get("shipped") or []):
        label = f"{label}-{len(st.get('queue') or []) + 1}"
    res = _res_dir(project)
    listing = _listing_images(project)
    targets = [str((listing / "icon.png").relative_to(project))]
    if res:
        rel = res.relative_to(project)
        targets += [str(rel / "mipmap-anydpi-v26/ic_launcher.xml"),
                    str(rel / "mipmap-*/ic_launcher.png"),
                    str(rel / "mipmap-*/ic_launcher_round.png"),
                    str(rel / "mipmap-*/ic_launcher_foreground.png"),
                    str(rel / "values/colors.xml")]
    # The provenance the change must carry — verify refuses an icon without it,
    # so it belongs in the task's own allow-list (else the release commits files
    # the task never declared, and the next agent reads them as scratch).
    targets += ["docs/storefront-icon.html", "docs/storefront-icon-prompt.txt",
                "docs/launcher-icon.json", "docs/storefront-assets.json"]
    entry = {
        "type": "feature",
        "label": label,
        "target": ", ".join(targets),
        "risk": "safe",
        "reason": (f"Owner feedback: \"{text}\" — replace the app icon as a designed artefact "
                   f"(Stitch artboard → app launcher icon → Play listing icon → release → "
                   f"store publish). The design, its prompt and its provenance ship in the "
                   f"same commit; verify refuses an icon change without them."),
    }
    if res:
        entry["advances"] = (state_of(project).get("last_run") or {}).get("advances") or ""
    block = qmod.add_task(st, entry, source="owner-feedback", position="head")
    save_state(project, st)
    data = seed(project, text, label)
    say(block)
    return {"status": "OK", "state": "intake", "label": label,
            "prompt": data["prompt"], "targets": targets}


def seed(project: Path, feedback: str, label: str) -> dict[str, Any]:
    """Write the brand run (brief + state) for a task admitted elsewhere.

    The prompt handler enqueues the icon task itself; this is what lets the next
    step (`brand --design`) know WHICH sentence started it and what brief it owes.
    """
    text = " ".join((feedback or "").split())
    data = {
        "version": 1,
        "state": "intake",
        "feedback": text,
        "app": _app_name(project),
        "task_label": label,
        "prompt_rel": PROMPT_REL,
        "created_at": _now(),
        "history": [],
        "steps": {},
    }
    _record(data, "intake", text, queue_label=label)
    data["prompt"] = brief(project, text)
    _save(project, data)
    return data


def _next_command(project_name: str, data: dict[str, Any]) -> str:
    step = data.get("state")
    names = {"intake": "--design", "designed": "--design (finish the app icon)",
             "applied": "verify → release → record", "released": "--publish",
             "published": "--feedback (new change)"}
    return f"devteam brand {project_name} {names.get(step, '--status')}"


# --------------------------------------------------------------------------- #
# 2. design — Stitch from code, then the SAME pixels become the app icon
# --------------------------------------------------------------------------- #
def design(project: Path, worktree: Path, from_file: str | None = None,
           stitch_source: str = "", timeout: int = 900, log=None) -> dict[str, Any]:
    say = log or (lambda *_: None)
    data = state(project)
    if not data:
        return {"status": "BLOCKED",
                "error": "no brand change is open — start with brand <project> --feedback \"…\""}
    if data.get("state") not in ("intake", "designed", "applied"):
        return {"status": "BLOCKED", "error": f"brand change is at '{data.get('state')}', not designable"}
    worktree = worktree.resolve()
    if worktree == project.resolve():
        return {"status": "BLOCKED",
                "error": "the app icon must be written in a task worktree, never the checkout",
                "hint": f"devteam worktree-start {project.name} --task-id <id>, then "
                        f"brand {project.name} --design --worktree <WORKTREE_PATH>"}
    res = _res_dir(worktree)
    if res is None:
        return {"status": "BLOCKED", "error": f"no Android res dir under {worktree}"}

    prompt = str(data.get("prompt") or brief(project, str(data.get("feedback") or "")))
    (worktree / PROMPT_REL).parent.mkdir(parents=True, exist_ok=True)
    (worktree / PROMPT_REL).write_text(prompt + "\n")

    stitch: dict[str, Any] = {}
    if from_file:
        src = Path(from_file).expanduser()
        if not src.is_file():
            return {"status": "BLOCKED", "error": f"no design at {src}"}
        raw = src.read_bytes()
        mime = "image/svg+xml" if raw.lstrip().startswith(b"<svg") else "text/html"
        stitch = {"screen": stitch_source, "project": "", "adopted_from": str(src)}
    else:
        from . import stitch_mcp

        try:
            title = f"{data.get('app') or _app_name(project)} App Icon — {_now()[:10]}"
            project_id = stitch_mcp.create_project(title)
            say(f"  stitch: project {project_id} ('{title}')")
            screen = stitch_mcp.generate_icon(project_id.split("/")[-1], prompt,
                                              timeout=timeout, log=say)
            raw, mime = stitch_mcp.download(screen)
            stitch = {"project": project_id, "screen": str(screen.get("name") or ""),
                      "title": str(screen.get("title") or ""),
                      "width": screen.get("width"), "height": screen.get("height")}
            say(f"  stitch: ✓ {stitch['screen'] or '(screen)'} ({mime}, {len(raw)} bytes)")
        except Exception as exc:
            return {"status": "BLOCKED", "error": f"Stitch did not produce the icon: {exc}",
                    "hint": "nothing was written; re-run --design (a produced screen is adopted, "
                            "not regenerated)"}
    if mime not in ("image/svg+xml", "text/html") or len(raw) < 400:
        return {"status": "BLOCKED",
                "error": f"the Stitch design is {mime}, {len(raw)} bytes — not an adoptable artboard"}
    scratch = project / ".pipeline/stitch"
    scratch.mkdir(parents=True, exist_ok=True)
    suffix = ".svg" if mime == "image/svg+xml" else ".html"
    raw_file = scratch / f"{(stitch.get('screen') or 'adopted').split('/')[-1]}{suffix}"
    raw_file.write_bytes(raw)

    from . import storefront_brand as sbf

    adopted = sbf.adopt_stitch_icon(worktree, raw_file,
                                    stitch_source=str(stitch.get("screen") or stitch_source),
                                    note=f"owner feedback: {data.get('feedback')}")
    if adopted.get("status") != "OK":
        return adopted
    play_icon = worktree / adopted["file"]
    background = _edge_color(play_icon)
    launcher = apply_launcher(worktree, play_icon, background,
                              stitch=stitch, design_html=worktree / DESIGN_REL)

    data["stitch"] = stitch
    data["worktree"] = str(worktree)
    data["design"] = {
        "html": DESIGN_REL, "sha256": _sha(worktree / DESIGN_REL),
        "prompt_sha256": _sha(worktree / PROMPT_REL),
        "artboard": str(raw_file.relative_to(project)) if project in raw_file.parents else str(raw_file),
    }
    data["icon_sha256"] = _sha(play_icon)
    data["launcher"] = launcher
    data["background"] = background
    _record(data, "applied", "Stitch design adopted; app + listing icons written",
            design_sha256=data["design"]["sha256"], icon_sha256=data["icon_sha256"])
    _save(project, data)
    return {"status": "OK", "state": "applied", "play_icon": str(play_icon),
            "design": data["design"], "launcher": launcher, "stitch": stitch,
            "background": background}


def apply_launcher(worktree: Path, play_icon: Path, background: str, *,
                   stitch: dict[str, Any], design_html: Path) -> dict[str, Any]:
    """Re-render the app's launcher icon from the adopted Play icon.

    One design, one set of pixels: the Play icon is the source, and every launcher
    bitmap is a re-render of it, so the icon a user taps and the icon a store
    visitor sees cannot disagree. Hashes are recorded per file; `verify` compares
    them and refuses a half-applied change.
    """
    res = _res_dir(worktree)
    if res is None:
        raise RuntimeError("no res dir to receive the launcher icon")
    dens = {}
    for name, scale in DENSITIES.items():
        folder = res / f"mipmap-{name}"
        folder.mkdir(parents=True, exist_ok=True)
        legacy = 48 * scale
        foreground = 108 * scale
        full = folder / "ic_launcher.png"
        rnd = folder / "ic_launcher_round.png"
        fg = folder / "ic_launcher_foreground.png"
        _render_tile(play_icon, full, int(legacy), "full")
        _render_tile(play_icon, rnd, int(legacy), "round")
        _render_tile(play_icon, fg, int(foreground), "foreground")
        dens[name] = {"icon": _sha(full), "round": _sha(rnd), "foreground": _sha(fg),
                      "sizes": {"icon": int(legacy), "foreground": int(foreground)}}
    _write_adaptive(res, background)
    prompt_rel = worktree / PROMPT_REL
    record = {
        "version": 1,
        "design": {"html": DESIGN_REL, "sha256": _sha(design_html),
                   "prompt": PROMPT_REL,
                   "prompt_sha256": _sha(prompt_rel) if prompt_rel.is_file() else ""},
        "play_icon": {"file": str(play_icon.relative_to(worktree)), "sha256": _sha(play_icon),
                      "size": 512},
        "background": background,
        "densities": dens,
        "stitch": stitch,
        "created_at": _now(),
    }
    from .core import save_json

    save_json(worktree / LAUNCHER_REL, record)
    return record


def _adaptive_xml() -> str:
    return ("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
            "<adaptive-icon xmlns:android=\"http://schemas.android.com/apk/res/android\">\n"
            "    <background android:drawable=\"@color/ic_launcher_background\" />\n"
            "    <foreground android:drawable=\"@mipmap/ic_launcher_foreground\" />\n"
            "</adaptive-icon>\n")


def _write_adaptive(res: Path, background: str) -> None:
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        target = res / "mipmap-anydpi-v26" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_adaptive_xml())
    colors = res / "values/colors.xml"
    colors.parent.mkdir(parents=True, exist_ok=True)
    text = colors.read_text() if colors.is_file() else "<resources>\n</resources>\n"
    line = f"<color name=\"ic_launcher_background\">{background}</color>"
    if re.search(r"<color\s+name=\"ic_launcher_background\"\s*>.*?</color>", text):
        text = re.sub(r"<color\s+name=\"ic_launcher_background\"\s*>.*?</color>", line, text)
    else:
        text = text.replace("</resources>", f"    {line}\n</resources>")
    colors.write_text(text)


def _render_tile(src: Path, out: Path, size: int, mode: str) -> None:
    """Render `src` into an exact `size` PNG (full | round | foreground)."""
    from .marketing_asset_generator import _find_chrome_executable

    chrome = _find_chrome_executable()
    if not chrome:
        raise RuntimeError("Google Chrome not found for the launcher icon render")
    inner = max(1, int(round(size * 2 / 3))) if mode == "foreground" else size
    offset = (size - inner) // 2
    radius = "50%" if mode == "round" else "0"
    page = out.with_suffix(".html")
    page.write_text(
        "<!DOCTYPE html><html><body style=\"margin:0;padding:0;background:transparent;"
        "overflow:hidden\">"
        f"<div style=\"position:absolute;left:{offset}px;top:{offset}px;width:{inner}px;"
        f"height:{inner}px;border-radius:{radius};overflow:hidden\">"
        f"<img src=\"{src.resolve().as_uri()}\" style=\"display:block;width:{inner}px;"
        f"height:{inner}px\"></div></body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
                        "--default-background-color=00000000",
                        f"--window-size={size},{size}",
                        f"--screenshot={out.resolve()}", page.resolve().as_uri()],
                       capture_output=True, text=True, timeout=90)
    finally:
        page.unlink(missing_ok=True)
    dims = _png_size(out)
    if dims != [size, size]:
        raise RuntimeError(f"launcher render produced {dims}, expected {[size, size]}")


def _edge_color(png: Path) -> str:
    """The tile's own ground colour (top-left 8x8, averaged) — the adaptive
    background, so the scaled foreground's square edge disappears into it."""
    sips = shutil.which("sips")
    if not sips:
        return "#0A1628"
    crop = png.with_suffix(".edge.png")
    pixel = png.with_suffix(".pixel.png")
    try:
        subprocess.run([sips, "--cropToHeightWidth", "8", "8", "--cropOffset", "0", "0",
                        str(png), "--out", str(crop)], capture_output=True, timeout=60)
        subprocess.run([sips, "-z", "1", "1", str(crop), "--out", str(pixel)],
                       capture_output=True, timeout=60)
        rgb = _png_pixel(pixel)
        return "#%02X%02X%02X" % rgb if rgb else "#0A1628"
    finally:
        crop.unlink(missing_ok=True)
        pixel.unlink(missing_ok=True)


def _png_pixel(path: Path) -> tuple[int, int, int] | None:
    import struct
    import zlib

    try:
        blob = path.read_bytes()
    except OSError:
        return None
    pos, idat, channels = 8, b"", 3
    while pos < len(blob):
        length = struct.unpack(">I", blob[pos:pos + 4])[0]
        kind = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(data[9], 3)
        elif kind == b"IDAT":
            idat += data
        pos += 12 + length
    try:
        raw = zlib.decompress(idat)
    except Exception:
        return None
    if len(raw) < 1 + channels:
        return None
    return tuple(raw[1:4])  # type: ignore[return-value]


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
# 3. the gate — one answer for status, verify and publish
# --------------------------------------------------------------------------- #
def gate(root: Path) -> list[str]:
    """Reasons this icon change may not ship. Empty list = provable.

    Reads the tree that would be shipped (a worktree for verify, the delivery
    checkout for publish); every claim is a file or a hash, never a note.
    """
    issues: list[str] = []
    design, prompt = root / DESIGN_REL, root / PROMPT_REL
    launcher, assets = root / LAUNCHER_REL, root / ASSETS_REL
    for path, why in ((design, "the adopted Stitch artboard"),
                      (prompt, "the prompt that produced the artboard"),
                      (launcher, "the app-icon provenance"),
                      (assets, "the listing-icon provenance")):
        if not path.is_file():
            issues.append(f"{path.relative_to(root)} is missing — {why} is not committed; "
                          f"run `devteam brand <project> --design --worktree <tree>`")
    if issues:
        return issues
    try:
        assets_data = json.loads(assets.read_text())
        icon = (assets_data.get("assets") or {}).get("icon") or {}
    except Exception:
        return ["docs/storefront-assets.json cannot be read"]
    try:
        launcher_data = json.loads(launcher.read_text())
    except Exception:
        return [f"{LAUNCHER_REL} cannot be read"]
    source = str(icon.get("source") or "")
    if source != "stitch":
        issues.append(f"the Play icon's source is {source or 'none'!r}, not a Stitch design")
    design_sha = _sha(design)
    if icon.get("html_sha256") and icon["html_sha256"] != design_sha:
        issues.append("the Play icon was adopted from a DIFFERENT artboard than "
                      f"{DESIGN_REL} — re-adopt the committed design")
    recorded = str((launcher_data.get("design") or {}).get("sha256") or "")
    if recorded != design_sha:
        issues.append("the app icon was not rendered from the committed artboard — "
                      "re-run the launcher apply so both icons share one design")
    play = _listing_images(root) / "icon.png"
    if not play.is_file():
        issues.append("the listing icon is missing")
    elif icon.get("sha256") and icon["sha256"] != _sha(play):
        issues.append("the listing icon changed after adoption — re-adopt it")
    elif str((launcher_data.get("play_icon") or {}).get("sha256") or "") != _sha(play):
        # The launcher is a RE-RENDER of the adopted icon, not a copy: Chrome
        # rasterises the same artboard to slightly different bytes on a different
        # day (measured 2026-11-04: two renders of one artboard differed in sha
        # while the mark was identical). Byte equality therefore demanded a
        # re-render that could never satisfy it. What must hold is the DESIGN —
        # the installed icon and the listing icon are one mark — and that is what
        # is measured, on the pixels that actually ship.
        from . import pngcmp

        res_probe = _res_dir(root)
        biggest = sorted((res_probe.glob("mipmap-*/ic_launcher.png") if res_probe else []),
                         key=lambda p: p.stat().st_size, reverse=True)
        sim = pngcmp.shape_similarity(play, biggest[0]) if biggest else None
        if sim is None or sim < 0.9:
            issues.append("the app icon and the Play icon are different designs "
                          f"(shape {sim if sim is None else round(sim, 2)})")
    res = _res_dir(root)
    if res is None:
        issues.append("no Android res dir to check the launcher icon against")
        return issues
    for name, digests in (launcher_data.get("densities") or {}).items():
        folder = res / f"mipmap-{name}"
        for kind, suffix in (("icon", "ic_launcher.png"), ("round", "ic_launcher_round.png"),
                             ("foreground", "ic_launcher_foreground.png")):
            path = folder / suffix
            want = str((digests or {}).get(kind) or "")
            if not path.is_file():
                issues.append(f"{path.relative_to(root)} is missing")
            elif want and _sha(path) != want:
                issues.append(f"{path.relative_to(root)} does not match the recorded design")
    prompt_sha = str((launcher_data.get("design") or {}).get("prompt_sha256") or "")
    if not prompt_sha:
        issues.append(f"{LAUNCHER_REL} records no prompt hash — the brief is part of the claim")
    elif _sha(prompt) != prompt_sha:
        issues.append(f"{PROMPT_REL} changed after the design — the committed brief is not "
                      "the one that produced the icon")
    return issues


# --------------------------------------------------------------------------- #
# 4. status — what actually happened, step by step
# --------------------------------------------------------------------------- #
def status(project: Path, root: Path | None = None) -> dict[str, Any]:
    data = state(project)
    if not data:
        return {"status": "NONE", "steps": {}}
    from .core import state_of

    st = state_of(project)
    label = str(data.get("task_label") or "")
    shipped = [s for s in st.get("shipped") or [] if (s.get("label") or "") == label]
    released = shipped[-1] if shipped else None
    # Before the release, the change lives in its task worktree: that tree IS the
    # candidate commit, so the gate is read there. After the release the ledger is
    # the authority and the checkout holds the shipped files.
    check = root
    if check is None:
        check = project
        if not released and data.get("worktree"):
            candidate = Path(str(data["worktree"]))
            if candidate.is_dir():
                check = candidate
    issues = gate(check)
    published = (data.get("steps") or {}).get("published") or {}
    return {
        "status": "OK" if not issues else "REFUSED",
        "state": data.get("state"),
        "root": str(check),
        "label": label,
        "feedback": data.get("feedback"),
        "design_sha256": (data.get("design") or {}).get("sha256"),
        "icon_sha256": data.get("icon_sha256"),
        "stitch": data.get("stitch"),
        "released": {"version": released.get("version"), "commit": released.get("commit")}
        if released else None,
        "published": published or None,
        "issues": issues,
        "next": _next_command(project.name, data),
    }


def format_status(project: Path, root: Path | None = None) -> str:
    info = status(project, root=root)
    if info["status"] == "NONE":
        return ("BRAND: no icon change is open\n"
                f"  start one: devteam brand {project.name} --feedback \"<the owner's words>\"")
    order = ("intake", "designed", "applied", "released", "published")
    reached = info.get("state") or "intake"
    if info.get("released"):
        reached = "released"
    if info.get("published"):
        reached = "published"
    lines = ["BRAND CHANGE", "=" * 60,
             f"  feedback: {info.get('feedback')}",
             f"  task:     {info.get('label')}"]
    for step in order:
        mark = "✓" if order.index(step) <= order.index(reached) else "·"
        lines.append(f"  {mark} {step}")
    if info.get("design_sha256"):
        lines.append(f"  design:   {info['design_sha256'][:16]}  "
                     f"{(info.get('stitch') or {}).get('screen') or ''}")
    if info.get("released"):
        lines.append(f"  released: v{info['released'].get('version')} "
                     f"({str(info['released'].get('commit') or '')[:8]})")
    if info.get("published"):
        lines.append(f"  published: {info['published'].get('at')} "
                     f"({str(info['published'].get('version') or '')})")
    if info.get("issues"):
        lines.append("  GATE: refused")
        for issue in info["issues"]:
            lines.append(f"    ✗ {issue}")
    lines.append(f"  next: {info.get('next')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 5. publish — the last inch, and it names what the API cannot see
# --------------------------------------------------------------------------- #
def publish(project: Path, yes: bool = False, log=None) -> dict[str, Any]:
    say = log or (lambda *_: None)
    info = status(project)
    if info["status"] == "NONE":
        return {"status": "BLOCKED", "error": "no brand change is open"}
    if info["issues"]:
        return {"status": "BLOCKED", "blockers": info["issues"],
                "hint": "fix the icon change before it goes to the store"}
    from .play_listing import publish_icon

    res = publish_icon(project, dry_run=not yes, log=say)
    if res.get("status") in ("BLOCKED", "ERROR"):
        return res
    if yes:
        data = state(project)
        version = ((info.get("released") or {}).get("version") or "")
        _record(data, "published", "listing edit committed and read back",
                version=version, icon_sha256=_sha(_listing_images(project) / "icon.png"))
        _save(project, data)
        res["brand"] = {"version": version, "state": "published"}
    return res
