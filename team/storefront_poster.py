"""Full-poster generation: Gemini draws the asset, the render is its input.

Why this exists
---------------
The plate path (`storefront_build --gemini`) gives an image model only the
BACKDROP: the phone, the copy and the screenshot are all drawn by code, so the
listing can never show a screen the app did not render. That is the safest shape,
and it is not the only one the owner wants.

This module is the other shape, on purpose: the real scene render is handed to
Gemini (browser, the owner's Chrome) as an INPUT IMAGE, and the model returns the
finished marketing image — phone, background, headline, all of it — which is what
gets uploaded to Play.

What it can and cannot promise
------------------------------
* Fidelity: an image model does not copy bytes. The screen it draws is a
  depiction of the render, not the render; the tool therefore MEASURES how far
  the drawn screen sits from the real one and reports it, instead of pretending
  the two are identical. A poster that drifts far is refused by `--strict`.
* Resolution: the Gemini web UI returns roughly 9:16 at ~572x1024. Play's phone
  screenshots must be at least 1080x1920, so the poster is upscaled here — the
  layout survives, the small type softens. Posters that must stay crisp belong on
  the plate path; posters that must look painted belong here.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


def _app_name(project: Path) -> str:
    """The app's name is a PROJECT fact — read it, never spell it here.

    Team code carries zero project names (the first team law, enforced by
    `devteam verify`): a prompt template that names one app ships a poster for
    that app in every other project's listing.
    """
    try:
        profile = json.loads((project / ".pipeline/profile.json").read_text())
    except Exception:
        return "my Android app"
    return profile.get("name") or "my Android app"


def poster_prompt(project: Path, scene: dict[str, Any], direction: dict[str, Any],
                  extra: str = "") -> str:
    """The prompt handed to Gemini WITH the real render attached.

    Every word the poster may print comes from the scene spec, and the direction
    supplies the concept and the avoid-list — the same inputs the code renderer
    gets, so the two paths argue the same thing.
    """
    angle = (direction.get("copy_angle") or {}).get(str(scene.get("id")), "")
    parts = [
        "Create a vertical 9:16 Google Play Store marketing screenshot for my Android app "
        f"{_app_name(project)}.",
        "Use the attached app screenshot EXACTLY as the phone screen: keep its layout, "
        "colours, clock, search bar and dock of five app icons. Do not redraw the app "
        "interface, do not change its text, do not crop it.",
        "Show that screen inside a slim black phone with rounded corners and a soft drop "
        "shadow, centred, occupying the lower two thirds of the image.",
        "Background: very dark navy #0A0E14 with a soft teal #00E5FF glow behind the phone, "
        "subtle film grain, nothing else. Keep the whole bottom-right corner plain and dark — "
        "no glow, no sparkle, no glyph there.",
        f"Top of the image, in this order: a small uppercase teal label \"{scene.get('badge') or ''}\", "
        f"a big bold white condensed headline \"{scene.get('headline') or ''}\", then one line of "
        f"grey subtext \"{scene.get('sub') or ''}\".",
        "Print no other text. No logos, no watermark, no sparkles, no price, no rating badges.",
    ]
    if angle:
        parts.append(f"This frame's argument: {angle}")
    avoid = [a for a in (direction.get("avoid") or []) if "screen" not in a.lower()]
    if avoid:
        parts.append("Avoid: " + "; ".join(avoid) + ".")
    if extra.strip():
        parts.append(extra.strip())
    return "\n\n".join(p for p in parts if p.strip())


def _script(out: Path, source_png: Path, prompt: str) -> str:
    return f"""
    const out = {json.dumps(str(out))};
    const src = {json.dumps(str(source_png))};
    const pages = context.pages().filter(p => p.url().includes("gemini.google.com"));
    const page = pages[0] || await context.newPage();
    if (!pages.length) await page.goto("https://gemini.google.com/app", {{ waitUntil: "domcontentloaded" }});
    await page.bringToFront();
    // Count only the MODEL's images: the uploaded screenshot shows up as a blob
    // image too (at 460px wide), so "a new blob appeared" was satisfied by the
    // attachment preview and the run saved it as if it were the poster.
    const gen = () => page.locator("model-response img[src^='blob:'], "
                                 + "model-response img[src^='data:image']");
    const before = await gen().count();
    await page.locator("[aria-label=\\"Upload & tools\\"]").first().click();
    await page.waitForTimeout(800);
    await page.locator("input[type=file]").first().setInputFiles(src);
    await page.waitForTimeout(4000);
    const composer = page.locator("rich-textarea [contenteditable=true], [contenteditable=true]").first();
    await composer.click();
    await composer.fill({json.dumps(prompt)});
    await page.waitForTimeout(500);
    await page.keyboard.press("Enter");
    const start = Date.now();
    let picked = null;
    while (Date.now() - start < 300000) {{
      const imgs = gen();
      const n = await imgs.count();
      if (n > before) {{
        const last = imgs.nth(n - 1);
        const w = await last.evaluate(el => el.naturalWidth).catch(() => 0);
        // A generated poster is at least 500px wide; anything smaller is an icon
        // or a preview rendered into the transcript.
        if (w >= 500) {{ picked = await last.getAttribute("src"); break; }}
      }}
      await page.waitForTimeout(3000);
    }}
    if (!picked) return {{ ok: false, error: "no NEW image appeared in the model's answer" }};
    const data = await page.evaluate(async (s) => {{
      const img = new Image(); img.src = s; await img.decode();
      const c = document.createElement("canvas");
      c.width = img.naturalWidth; c.height = img.naturalHeight;
      c.getContext("2d").drawImage(img, 0, 0);
      return {{ data: c.toDataURL("image/png"), w: c.width, h: c.height }};
    }}, picked);
    fs.writeFileSync(out, Buffer.from(data.data.split(",")[1], "base64"));
    return {{ ok: true, path: out, w: data.w, h: data.h, url: page.url() }};
    """


def generate(project: Path, scene: dict[str, Any], prompt: str, log=None) -> dict[str, Any]:
    """Paint one full poster in Gemini (browser) from the scene's real render."""
    name = _poster_name(scene)
    out = project / ".pipeline/storefront_posters" / f"poster-{name}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_file():
        if log:
            log(f"  poster: reusing {out.name} ({out.stat().st_size // 1024} KB)")
        return {"status": "OK", "file": out, "reused": True}
    source = project / ".pipeline/storefront_scenes" / f"{scene['id']}.png"
    if not source.is_file():
        return {"status": "BLOCKED", "error": f"no render at {source.relative_to(project)}"}
    if log:
        log(f"  poster: Gemini (browser) painting {name} from {source.name}")
    try:
        res = subprocess.run(["browser-control", "execute", "--json", _script(out, source, prompt)],
                             capture_output=True, text=True, timeout=480)
    except FileNotFoundError:
        return {"status": "BLOCKED", "error": "browser-control is not installed"}
    except subprocess.TimeoutExpired:
        return {"status": "BLOCKED", "error": "Gemini did not answer within 8 minutes"}
    if res.returncode != 0:
        return {"status": "BLOCKED", "error": (res.stderr or "browser-control failed").strip()[:300]}
    try:
        payload = json.loads(res.stdout)
    except Exception:
        return {"status": "ERROR", "error": "browser-control returned no JSON"}
    value = payload.get("value") or {}
    if not (payload.get("ok") and value.get("ok")) or not out.is_file():
        return {"status": "BLOCKED", "error": str(value.get("error") or "no image produced")}
    if log:
        log(f"  poster: painted {out.name} ({value.get('w')}x{value.get('h')}, "
            f"{out.stat().st_size // 1024} KB)")
    return {"status": "OK", "file": out, "chat": value.get("url"),
            "source": str(source.relative_to(project))}


def clean_poster(poster: Path) -> dict[str, Any]:
    """Strip the vendor sparkle an image model leaves in a corner.

    A Play listing is the owner's product page; a model's watermark on it is an
    advert for the model. Reuses the plate cleaner (it finds the mark by local
    contrast), and refuses the poster if the heal cannot prove the mark is gone.
    """
    from . import plate_clean

    return plate_clean.clean(poster)


def to_play_size(poster: Path, out: Path, width: int = 1080, height: int = 1920) -> dict[str, Any]:
    """Upscale the painted poster to a size Play accepts, without inventing pixels.

    Play's phone screenshots need >= 1080x1920 and an aspect ratio of at most 2:1.
    The web UI returns ~572x1024, so the poster is resampled in Chrome. That is an
    upscale: the composition survives, small type softens. It is reported as such
    rather than passed off as a native render.
    """
    from .marketing_asset_generator import _find_chrome_executable

    chrome = _find_chrome_executable()
    if not chrome:
        return {"status": "ERROR", "error": "Google Chrome not found for the upscale"}
    out.parent.mkdir(parents=True, exist_ok=True)
    page = out.with_suffix(".html")
    page.write_text(
        "<!DOCTYPE html><html><body style='margin:0;background:#000'>"
        f"<img src='{poster.resolve().as_uri()}' "
        f"style='display:block;width:{width}px;height:{height}px;object-fit:cover;"
        "image-rendering:auto'>"
        "</body></html>")
    try:
        subprocess.run([chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
                        "--allow-file-access-from-files", f"--window-size={width},{height}",
                        f"--screenshot={out.resolve()}", page.resolve().as_uri()],
                       capture_output=True, text=True, timeout=90)
    finally:
        page.unlink(missing_ok=True)
    if not out.is_file():
        return {"status": "ERROR", "error": "the upscale produced no PNG"}
    return {"status": "OK", "file": out, "size": [width, height],
            "upscaled_from": _png_size(poster)}


def _poster_name(scene: dict[str, Any]) -> str:
    from .storefront_frames import slug

    base = re.sub(r"^\d+[-_\s]+", "", str(scene.get("id") or ""))
    return slug(base or scene.get("id") or "poster")


def _png_size(path: Path) -> list[int] | None:
    import struct

    try:
        head = path.read_bytes()[:24]
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return list(struct.unpack(">II", head[16:24]))
    except Exception:
        pass
    return None
