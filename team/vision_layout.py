"""Vision-directed storefront layouts — the model composes, deterministic code paints.

Why this exists
---------------
Asking an image model for a finished poster repaints the app screenshot: a phone
showing a UI nobody shipped, in a listing that claims to show the app. Even with
"keep it pixel-identical" in the prompt, a diffusion model redraws type. That is
unauditable — you can only look at it and hope.

A *vision* model does not have that problem because it never paints. It reads the
screenshot we already render (so it knows the real accent, the density, whether
the screen is a list or a canvas) and answers with a LAYOUT: numbers. This module
turns that layout into the same contract the Stitch path adopts — a 1620x3200
document with a 1080x2400 `img#devteam-screen` and `data-slot` copy markers — so
the real screenshot is composited at 1:1 by us, never by a model.

The result is code, not a picture:
  screenshot -> vision model -> layout.json -> renderer -> HTML -> verifier -> PNG

Every layout is inspectable and editable before it ships (`--layout`), re-renders
byte-identically, and is refused by the same guards as any other frame: slot size,
nothing drawn over the screen, no letterboxing. The model cannot smuggle a
resampled screenshot or a fake UI past `storefront_frames.adopt`.

Model access comes from the VISION_LLM_* variables in the environment (provider,
base URL, model, key-variable name and extra headers), which is how this machine
already configures its vision model.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .storefront_frames import (
    CANVAS_H,
    CANVAS_W,
    SCREEN_H,
    SCREEN_W,
    SCREEN_ID,
    TEXT_SLOTS,
    capabilities,
    allowed_numbers,
    product_name,
)

MARKER = "devteam vision-layout"

# The bezel the renderer draws around the screen, in px on every side. The screen
# must stop this far from the canvas edge or the phone is cropped by the frame.
BEZEL_PAD = 22

# The only schema a model may answer with. Everything is a number or a colour, so
# the renderer never has to interpret prose, and a hand-edit is obviously wrong.
LAYOUT_KEYS = ("canvas", "phone", "badge", "headline", "sub", "chips", "palette")


# --------------------------------------------------------------------------- #
# model access (OpenAI-compatible; configured by VISION_LLM_*)
# --------------------------------------------------------------------------- #
def config() -> dict[str, Any]:
    """Resolve the vision model from the environment, falling back to the chat one."""
    key_var = os.environ.get("VISION_LLM_API_KEY_ENV") or os.environ.get("LLM_API_KEY_ENV") or ""
    key = os.environ.get(key_var, "") if key_var else ""
    headers = {"Content-Type": "application/json", "User-Agent": "devteam/1.0"}
    try:
        headers.update(json.loads(os.environ.get("VISION_LLM_HEADERS_JSON") or "{}"))
    except Exception:
        pass
    return {
        "base_url": (os.environ.get("VISION_LLM_BASE_URL")
                     or os.environ.get("LLM_BASE_URL") or "").rstrip("/"),
        "model": os.environ.get("VISION_LLM_MODEL") or os.environ.get("LLM_MODEL") or "",
        "key": key,
        "key_env": key_var,
        "headers": headers,
    }


def _data_uri(png: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()


def _call(cfg: dict[str, Any], prompt: str, png: Path, timeout: int = 240) -> str:
    if not cfg["base_url"] or not cfg["model"] or not cfg["key"]:
        raise SystemExit(
            "no vision model configured: set VISION_LLM_BASE_URL, VISION_LLM_MODEL and "
            "VISION_LLM_API_KEY_ENV in the environment")
    body = {
        "model": cfg["model"],
        "temperature": 0.2,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _data_uri(png)}},
            ],
        }],
    }
    headers = dict(cfg["headers"])
    headers["Authorization"] = f"Bearer {cfg['key']}"
    req = urllib.request.Request(f"{cfg['base_url']}/chat/completions",
                                 data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"vision model failed ({exc.code}): {detail}")
    except Exception as exc:
        raise SystemExit(f"vision model failed: {exc}")
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise SystemExit(f"unexpected vision response: {json.dumps(data)[:400]}")


def _json_block(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    raw = fenced.group(1) if fenced else text
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        raise SystemExit(f"vision model returned no JSON object: {text[:300]}")
    try:
        return json.loads(raw[start:end + 1])
    except Exception as exc:
        raise SystemExit(f"vision model returned invalid JSON: {exc}")


# --------------------------------------------------------------------------- #
# the prompt
# --------------------------------------------------------------------------- #
def build_prompt(project: Path, scene: dict[str, Any], brief: str = "") -> str:
    from .design import extract_design_tokens
    from .marketing_asset_generator import app_accent

    tokens = extract_design_tokens(project)
    accent = app_accent(project) or tokens.get("primary_accent")
    has, has_not = capabilities(project)
    numbers = allowed_numbers(project)

    return f"""You are art-directing ONE Google Play store screenshot for {product_name(project)}.

The image attached is the REAL app screen that will appear in this poster. Read it:
note its background colour, its accent colour, whether it is a list, a canvas, a
form or a dashboard, and where the visual weight sits. Design the poster AROUND
this screen — you are not drawing it and must not describe changes to it.

Return ONLY a JSON object, no prose, with exactly this shape:

{{
  "canvas": {{"w": {CANVAS_W}, "h": {CANVAS_H}}},
  "phone":  {{"x": <int>, "y": <int>}},
  "badge":  {{"x": <int>, "y": <int>, "size": <int 20-34>, "align": "left"|"center", "color": "accent"|"muted"}},
  "headline": {{"x": <int>, "y": <int>, "size": <int 72-120>, "maxWidth": <int>, "align": "left"|"center", "lines": <int 1-3>}},
  "sub":    {{"x": <int>, "y": <int>, "size": <int 28-40>, "maxWidth": <int>, "align": "left"|"center"}},
  "chips":  [{{"x": <int>, "y": <int>, "side": "left"|"right"}}],
  "palette": {{"background": "<hex>", "accent": "{accent}", "text": "<hex>", "muted": "<hex>"}}
}}

Hard rules, checked by a program after you answer:
- The phone screen is EXACTLY {SCREEN_W}x{SCREEN_H} and it is placed at phone.x, phone.y.
  So {SCREEN_W} must fit in {CANVAS_W} and {SCREEN_H} in {CANVAS_H}: the phone can be at
  most {CANVAS_W - SCREEN_W}px from the left edge and {CANVAS_H - SCREEN_H}px from the top.
- NO text block and NO chip may overlap the phone rectangle. Everything is laid out
  beside or above it. Overlapping the screen hides the app you are advertising.
- The headline is the hero: size 72-120, and maxWidth must be wide enough for the
  words to break at spaces (roughly 8px of width per font-size point per word).
- Use the accent from the screenshot for accents; use the project's {tokens.get("headline_font")}
  for the headline and {tokens.get("body_font")} for body copy.

Store listing facts — do not invent beyond these:
{chr(10).join("- " + c for c in has) or "- (none)"}
Never imply: {", ".join(has_not) or "nothing"}. Never print a number outside: {", ".join(numbers) or "none"}.

This scene's copy:
  badge:    {scene.get("badge") or ""}
  headline: {scene.get("headline") or ""}
  sub:      {scene.get("sub") or ""}

Composition direction:
{brief.strip() or "Take a position: an asymmetric composition where the headline owns the space the phone does not."}
"""


# --------------------------------------------------------------------------- #
# layout -> HTML (the renderer is the only thing that draws pixels)
# --------------------------------------------------------------------------- #
def _num(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(round(float(value)))
    except Exception:
        return default
    return max(lo, min(hi, n))


def normalize(layout: dict[str, Any], tokens: dict[str, str]) -> dict[str, Any]:
    """Clamp a model's numbers into the canvas, and snap the phone onto the slot.

    Called before rendering and before validation: the model is asked for a legal
    layout, but a layout that is merely *almost* legal is cheaper to clamp than to
    re-ask for, and the verifier still refuses anything that is wrong.
    """
    phone = layout.get("phone") or {}
    # The bezel is drawn 22px outside the screen, so the screen must stop 22px short
    # of the edge or the phone is cropped by the canvas.
    px = _num(phone.get("x"), 270, BEZEL_PAD, CANVAS_W - SCREEN_W - BEZEL_PAD)
    py = _num(phone.get("y"), 800, 0, CANVAS_H - SCREEN_H)

    out: dict[str, Any] = {"canvas": {"w": CANVAS_W, "h": CANVAS_H},
                           "phone": {"x": px, "y": py}}
    for key, default_size, lo, hi in (("badge", 26, 18, 40), ("headline", 92, 60, 140),
                                      ("sub", 34, 24, 44)):
        block = layout.get(key) or {}
        out[key] = {
            "x": _num(block.get("x"), 80, 0, CANVAS_W - 200),
            "y": _num(block.get("y"), 240, 0, CANVAS_H - 120),
            "size": _num(block.get("size"), default_size, lo, hi),
            "maxWidth": _num(block.get("maxWidth"), 620, 240, CANVAS_W - 40),
            "align": "center" if str(block.get("align")) == "center" else "left",
        }
    chips = []
    for chip in (layout.get("chips") or [])[:3]:
        chips.append({"x": _num(chip.get("x"), 80, 0, CANVAS_W - 220),
                      "y": _num(chip.get("y"), 2000, 0, CANVAS_H - 80)})
    out["chips"] = chips

    palette = layout.get("palette") or {}
    def colour(name: str, fallback: str) -> str:
        value = str(palette.get(name) or "")
        return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback
    out["palette"] = {
        "background": colour("background", tokens.get("surface_base", "#0D0D0D")),
        "accent": colour("accent", tokens.get("primary_accent", "#E8442A")),
        "text": colour("text", "#FFFFFF"),
        "muted": colour("muted", "#9A9A9A"),
    }
    return out


def overlaps_phone(layout: dict[str, Any]) -> list[str]:
    """Text blocks that sit on the screenshot — refused before rendering, so the
    reason names the block instead of a rendered rectangle."""
    px, py = layout["phone"]["x"], layout["phone"]["y"]
    bad = []
    for key in ("badge", "headline", "sub"):
        block = layout[key]
        if (block["x"] < px + SCREEN_W and block["x"] + block["maxWidth"] > px
                and block["y"] < py + SCREEN_H and block["y"] + block["size"] * 3 > py):
            bad.append(key)
    for i, chip in enumerate(layout["chips"]):
        if (chip["x"] < px + SCREEN_W and chip["x"] + 220 > px
                and chip["y"] < py + SCREEN_H and chip["y"] + 60 > py):
            bad.append(f"chip{i + 1}")
    return bad


def _stack_text(L: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any]:
    """Flow the copy: the model sets where the column starts and how big each
    element is; the renderer decides y, because only it knows how the text wrapped.

    Letting the model place all three y-values is what produced a sub-headline
    sitting exactly on the headline's last line — a class of bug no prompt fixes,
    because the model cannot measure the font it never renders.
    """
    import math

    def lines(text: str, size: int, width: int) -> int:
        chars = max(8, int(width / (size * 0.52)))
        return max(1, math.ceil(len(text or "") / chars))

    badge, head, sub = L["badge"], L["headline"], L["sub"]
    y = badge["y"]
    badge["y"] = y
    y += int(badge["size"] * 2.0)
    head["y"] = y
    y += int(lines(scene.get("headline") or "", head["size"], head["maxWidth"])
              * head["size"] * 1.08) + int(head["size"] * 0.34)
    sub["y"] = y
    return L


def backdrop_css(backdrop: Path | None) -> str:
    """A generated backdrop as a full-bleed plate.

    This is where an image model is allowed to contribute: it paints a texture
    nothing has to be true about. It is never allowed near the screenshot, because
    every image model repaints app UI (observed: a Gemini poster whose cards,
    spacing and supporting line all differed from the render it was given).
    """
    if backdrop is None or not backdrop.is_file():
        return ""
    import base64 as _b64
    data = _b64.b64encode(backdrop.read_bytes()).decode()
    return ("position:absolute;inset:0;background:"
            "url(data:image/png;base64," + data + ") center/cover no-repeat;")


def render_html(project: Path, scene: dict[str, Any], screen_png: Path,
                layout: dict[str, Any], backdrop: Path | None = None) -> str:
    from .design import extract_design_tokens
    tokens = extract_design_tokens(project)
    L = normalize(layout, tokens)
    L = _stack_text(L, scene)
    p = L["palette"]
    ph = L["phone"]
    chips = "".join(
        f'<span data-slot="chip" style="position:absolute;left:{c["x"]}px;top:{c["y"]}px;'
        f'font-family:{tokens.get("label_font", "monospace")};font-size:20px;'
        f'letter-spacing:.14em;text-transform:uppercase;color:{p["accent"]};'
        f'border:1px solid {p["accent"]}55;padding:8px 14px;border-radius:6px">'
        f'{c.get("text", "")}</span>'
        for c in L["chips"])

    def block(key: str, tag: str, weight: int, family: str, transform: str = "") -> str:
        b = L[key]
        return (f'<{tag} data-slot="{key}" style="position:absolute;left:{b["x"]}px;top:{b["y"]}px;'
                f'width:{b["maxWidth"]}px;font-size:{b["size"]}px;font-weight:{weight};'
                f'line-height:1.06;text-align:{b["align"]};'
                f'font-family:{family};{transform}">{scene.get(key) or ""}</{tag}>')

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<!-- {MARKER}: layout from the vision model, pixels from this renderer -->
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Barlow+Condensed:wght@400;600&family=Inter:wght@400;600&family=Space+Grotesk:wght@700&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ width: {CANVAS_W}px; height: {CANVAS_H}px; overflow: hidden; }}
  .poster-canvas {{ position: relative; width: {CANVAS_W}px; height: {CANVAS_H}px;
    background: {p["background"]}; color: {p["text"]};
    font-family: {tokens.get("body_font", "sans-serif")}; }}
  .aura {{ position: absolute; left: {ph["x"] - 260}px; top: {ph["y"] - 260}px;
    width: {SCREEN_W + 520}px; height: {SCREEN_H + 520}px; border-radius: 50%;
    background: radial-gradient(circle, {p["accent"]}22 0%, transparent 62%); }}
  .bezel {{ position: absolute; left: {ph["x"] - 22}px; top: {ph["y"] - 22}px;
    width: {SCREEN_W + 44}px; height: {SCREEN_H + 44}px; border-radius: 76px;
    background: linear-gradient(180deg, #232323, #111); padding: 22px;
    box-shadow: 0 40px 90px rgba(0,0,0,.85), 0 0 0 1px #ffffff14; }}
  #devteam-screen {{ display: block; width: {SCREEN_W}px; height: {SCREEN_H}px;
    object-fit: none; border-radius: 56px; }}
</style></head>
<body><div class="poster-canvas">
  {('<div class="plate" style="' + backdrop_css(backdrop) + '"></div>') if backdrop else '<div class="aura"></div>'}
  {block("badge", "span", 700, tokens.get("label_font", "monospace"), "letter-spacing:.16em;text-transform:uppercase;")}
  {block("headline", "h1", 700, tokens.get("headline_font", "sans-serif"))}
  {block("sub", "p", 400, tokens.get("body_font", "sans-serif"))}
  {chips}
  <div class="bezel"><img id="{SCREEN_ID}" width="{SCREEN_W}" height="{SCREEN_H}"
    src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='{SCREEN_W}' height='{SCREEN_H}'><rect width='{SCREEN_W}' height='{SCREEN_H}' fill='%23101010'/></svg>"
    alt="app screenshot"></div>
</div></body></html>"""


# --------------------------------------------------------------------------- #
# entry point used by the CLI
# --------------------------------------------------------------------------- #
def propose(project: Path, scene: dict[str, Any], screen_png: Path,
            brief: str = "", log: Any = None) -> dict[str, Any]:
    from .design import extract_design_tokens
    cfg = config()
    if log:
        log(f"  vision model: {cfg['model']} @ {cfg['base_url']}  (screenshot: {screen_png.name})")
    raw = _call(cfg, build_prompt(project, scene, brief), screen_png)
    layout = normalize(_json_block(raw), extract_design_tokens(project))
    bad = overlaps_phone(layout)
    if bad:
        raise SystemExit("the vision model placed " + ", ".join(bad)
                         + " over the screenshot — re-run, or edit the layout with --layout")
    if log:
        log(f"  layout: phone at {layout['phone']['x']},{layout['phone']['y']}"
            f" · headline {layout['headline']['size']}px at {layout['headline']['x']},{layout['headline']['y']}")
    return layout


def prepare_backdrop(backdrop: Path) -> dict[str, Any]:
    """Every image model stamps its mark somewhere. Strip it before it can ship.

    Not optional and not a warning: a vendor watermark on a store listing is an
    advert on the owner's product. `plate_clean` heals the corner and proves the
    mark is gone; a plate it cannot clean is refused rather than shipped.
    """
    from .plate_clean import clean
    info = clean(backdrop)
    if info.get("status") == "REJECTED":
        raise SystemExit(f"backdrop refused: {info.get('error')}")
    if info.get("status") != "OK":
        raise SystemExit(f"backdrop could not be checked: {info.get('error')}")
    info["source"] = str(backdrop)
    return info


def render_to(project: Path, scene: dict[str, Any], screen_png: Path,
              layout: dict[str, Any], out_html: Path,
              backdrop: Path | None = None) -> tuple[Path, dict[str, Any] | None]:
    from .storefront_frames import _set_screen_src, guard_slot
    plate = None
    if backdrop is not None:
        plate = prepare_backdrop(backdrop)
        backdrop = Path(plate["file"])
    html = guard_slot(_set_screen_src(
        render_html(project, scene, screen_png, layout, backdrop),
        screen_png.resolve().as_uri()))
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html)
    return out_html, plate


# --------------------------------------------------------------------------- #
# the browser step: Gemini paints a plate, never the screen
# --------------------------------------------------------------------------- #
def gemini_backdrop_prompt(project: Path, scene: dict[str, Any] | None = None,
                           brief: str = "", direction: str = "") -> str:
    """The exact text to paste into Gemini (browser) for a backdrop plate.

    Gemini in the browser is the only image model this machine can reach for free,
    and it is genuinely good at one job: an atmospheric plate. It is not allowed
    the screen — it repaints app UI, and its output is 572x1024, which is a
    background at 2.8x upscale, not a screenshot.

    The plate is soft light and glow, so upscaling costs nothing visible; the
    phone, the copy and the screenshot are all drawn by the renderer at full size.
    """
    from .design import extract_design_tokens
    tokens = extract_design_tokens(project)
    base = tokens.get("surface_base", "#0D0D0D")
    accent = tokens.get("primary_accent", "#E8442A")
    focus = brief.strip() or "lower right"
    # The glow is the BRAND's glow. It used to be the literal words "deep-red",
    # which was one project's palette baked into a generic prompt: every other app
    # got a red halo under its teal/purple/blue accent (observed on a teal app).
    return (
        f"Create a plain vertical BACKGROUND PLATE, 9:16 portrait, for a Google Play "
        f"store screenshot of {product_name(project)}. Fill the whole frame with a "
        f"near-black background ({base}), one subtle radial glow in the brand accent "
        f"({accent}) towards the {focus}, soft diagonal light streaks from the opposite "
        f"corner, very fine film grain and a faint vignette. The accent colour is {accent}.\n\n"
        "It must be completely empty: NO text, NO letters, NO phone, NO app interface, "
        "NO icons, NO logos, NO sparkles, NO products. Just an abstract dark backdrop. "
        "Keep the bottom-right corner plain and dark.\n\n"
        "The output is used as a plate behind a phone mockup drawn by code, so it only "
        "needs to read as light and texture — it never contains the app."
        + (f"\n\nDIRECTION (from docs/storefront-direction.json): {direction}" if direction.strip() else "")
    )
