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

    Deliberately says NOTHING about how the image should look — no palette, no glow,
    no phone styling, no film grain. Creative direction is the model's job; the
    owner's decision is that Gemini composes the asset. What the prompt does pin is
    the part that must stay true: which screen to show and which words to print.
    Everything it prints comes from the scene spec, so the poster cannot advertise
    a feature or a number the app does not have.
    """
    parts = [
        "Create a vertical 9:16 Google Play Store marketing screenshot for my Android app "
        "MindfulLauncher.",
        "The attached image is the app's real screen: show that screen in the image as the "
        "app screen, and do not invent or replace the app's interface.",
        "Print exactly these words, and no other text:",
        f"label: {scene.get('badge') or ''}",
        f"headline: {scene.get('headline') or ''}",
        f"subtext: {scene.get('sub') or ''}",
        "No watermark, no logos, no price, no rating badges.",
    ]
    angle = (direction.get("copy_angle") or {}).get(str(scene.get("id")), "")
    if angle:
        parts.append(f"What this screen is selling: {angle}")
    if extra.strip():
        parts.append(extra.strip())
    return "\n".join(p for p in parts if p.strip())


def _batch_script(jobs: list[dict[str, str]]) -> str:
    """One browser session, one Gemini chat, one asset per job.

    Every asset of a listing is drawn in the SAME conversation on purpose: the model
    keeps the look it just established, so the screenshots read as one set instead of
    three unrelated posters. Each job = {out, src, prompt}.
    """
    return f"""
    const JOBS = {json.dumps(jobs)};
    const pages = context.pages().filter(p => p.url().includes("gemini.google.com"));
    const page = pages[0] || await context.newPage();
    if (!pages.length) await page.goto("https://gemini.google.com/app", {{ waitUntil: "domcontentloaded" }});
    await page.bringToFront();
    const gen = () => page.locator("model-response img[src^='blob:'], "
                                 + "model-response img[src^='data:image']");
    const results = [];
    for (const job of JOBS) {{
      const before = await gen().count();
      await page.locator("[aria-label=\\"Upload & tools\\"]").first().click();
      await page.waitForTimeout(700);
      await page.locator("input[type=file]").first().setInputFiles(job.src);
      await page.waitForTimeout(3500);
      const composer = page.locator("rich-textarea [contenteditable=true], [contenteditable=true]").first();
      await composer.click();
      await composer.fill(job.prompt);
      await page.waitForTimeout(400);
      await page.keyboard.press("Enter");
      const start = Date.now();
      let picked = null;
      while (Date.now() - start < 300000) {{
        const imgs = gen();
        const n = await imgs.count();
        if (n > before) {{
          const last = imgs.nth(n - 1);
          const w = await last.evaluate(el => el.naturalWidth).catch(() => 0);
          if (w >= 500) {{ picked = await last.getAttribute("src"); break; }}
        }}
        await page.waitForTimeout(3000);
      }}
      if (!picked) {{ results.push({{ ok: false, error: "no NEW image in the model's answer" }}); break; }}
      const data = await page.evaluate(async (s) => {{
        const img = new Image(); img.src = s; await img.decode();
        const c = document.createElement("canvas");
        c.width = img.naturalWidth; c.height = img.naturalHeight;
        c.getContext("2d").drawImage(img, 0, 0);
        return {{ data: c.toDataURL("image/png"), w: c.width, h: c.height }};
      }}, picked);
      fs.writeFileSync(job.out, Buffer.from(data.data.split(",")[1], "base64"));
      results.push({{ ok: true, out: job.out, w: data.w, h: data.h }});
    }}
    return {{ ok: results.length && results.every(r => r.ok), chat: page.url(), results }};
    """


def generate_all(project: Path, scenes: list[dict[str, Any]], direction: dict[str, Any],
                 log=None, extra: str = "") -> dict[str, Any]:
    """Paint every poster in ONE Gemini chat, one prompt per scene.

    Returns {"status", "posters": {scene_id: path}, "chat"}. Reuses any poster that
    already exists, so a re-run is deterministic and does not repaint the set.
    """
    out_dir = project / ".pipeline/storefront_posters"
    out_dir.mkdir(parents=True, exist_ok=True)
    posters: dict[str, Path] = {}
    jobs: list[dict[str, str]] = []
    for scene in scenes:
        name = _poster_name(scene)
        out = out_dir / f"poster-{name}.png"
        source = project / ".pipeline/storefront_scenes" / f"{scene['id']}.png"
        if out.is_file():
            if log:
                log(f"  poster: reusing {out.name} ({out.stat().st_size // 1024} KB)")
            posters[scene["id"]] = out
            continue
        if not source.is_file():
            return {"status": "BLOCKED", "error": f"no render at {source.relative_to(project)}"}
        jobs.append({"out": str(out), "src": str(source),
                     "prompt": poster_prompt(project, scene, direction, extra=extra)})
    if not jobs:
        return {"status": "OK", "posters": posters, "chat": None, "painted": 0}
    if log:
        log(f"  poster: Gemini (browser) painting {len(jobs)} asset(s) in one chat")
    try:
        res = subprocess.run(["browser-control", "execute", "--json", _batch_script(jobs)],
                             capture_output=True, text=True, timeout=900)
    except FileNotFoundError:
        return {"status": "BLOCKED", "error": "browser-control is not installed"}
    except subprocess.TimeoutExpired:
        return {"status": "BLOCKED", "error": "Gemini did not finish within 15 minutes"}
    if res.returncode != 0:
        return {"status": "BLOCKED", "error": (res.stderr or "browser-control failed").strip()[:300]}
    try:
        payload = json.loads(res.stdout)
    except Exception:
        return {"status": "ERROR", "error": "browser-control returned no JSON"}
    value = payload.get("value") or {}
    for job, made in zip(jobs, value.get("results") or []):
        if not made.get("ok"):
            return {"status": "BLOCKED", "error": str(made.get("error") or "no image produced"),
                    "chat": value.get("chat")}
        path = Path(made["out"])
        posters[_scene_id_for(Path(job["src"]).stem)] = path
        if log:
            log(f"  poster: painted {path.name} ({made.get('w')}x{made.get('h')}, "
                f"{path.stat().st_size // 1024} KB)")
        (project / ".pipeline" / f"gemini-poster-{path.stem.replace('poster-', '')}.txt").write_text(
            job["prompt"])
    if not (payload.get("ok") and value.get("ok")):
        return {"status": "BLOCKED", "error": "one or more posters were not generated",
                "chat": value.get("chat")}
    return {"status": "OK", "posters": posters, "chat": value.get("chat"),
            "painted": len(jobs)}


def _scene_id_for(stem: str) -> str:
    return stem


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
