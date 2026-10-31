"""One deterministic command for the whole storefront asset pass.

Why this exists
---------------
The pass used to be four prose steps an agent had to remember and chain by hand:
render → review → compose the frame (Stitch or the vision model, with a plate from
Gemini) → composite → publish. Every step was documented somewhere, and an agent
that skipped one still produced a plausible-looking result: the release shipped
the BUILTIN frame template while the contract and the lessons both required a
composed one, because nothing in the pipeline demanded it.

This module is that demand. It runs the steps in the only order that works, and it
REFUSES to continue when an input the workflow requires is missing — a missing
plate or a missing editorial review is a stop, never a silent default.

Determinism
-----------
Two inputs really are nondeterministic — the vision model's layout and Chrome's
font/antialiasing timing — so "byte-identical every run" is not on offer. What is
on offer, and what this command enforces:

  * one entry point: there is no way to run the steps out of order or partially;
  * the layout is SAVED and reused (`--layout` or the saved copy), so a re-run
    re-adopts the same composition instead of asking a model again;
  * the plate is SAVED and reused, so a re-run does not repaint the art;
  * the builtin template requires an explicit `--builtin` (it is never a fallback),
    and the command says so in its summary when it was chosen;
  * every refusal names the exact command that unblocks it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _say(log, text: str) -> None:
    if log:
        log(text)


def _fail(status: str, error: str, hint: str = "") -> dict[str, Any]:
    return {"status": status, "error": error, "hint": hint}


def _saved(project: Path, name: str, suffix: str) -> Path:
    return project / ".pipeline/storefront_frames" / f"{suffix}-{name}.json"


def gemini_plate(project: Path, name: str, prompt: str, log=None) -> Path | None:
    """Ask Gemini (browser) for the backdrop plate and save the PNG.

    The browser is the owner's own logged-in Chrome through browser-control: no
    API key, no image model that repaints the app. The generated image is a PLATE
    only — soft light behind the phone — which is why its 572x1024 output is fine.

    Best-effort by design: Gemini's web UI is not a stable API, so a failure here
    is a clear message with the manual path (`--plate <saved.png>`), never a
    half-composed frame.
    """
    out = project / ".pipeline/storefront_frames" / f"plate-{name}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_file():
        _say(log, f"  plate: reusing {out.name} ({out.stat().st_size // 1024} KB)")
        return out
    script = f"""
    const out = {json.dumps(str(out))};
    const pages = context.pages().filter(p => p.url().includes("gemini.google.com"));
    const page = pages[0] || await context.newPage();
    if (!pages.length) await page.goto("https://gemini.google.com/app", {{ waitUntil: "domcontentloaded" }});
    await page.bringToFront();
    const composer = page.locator("rich-textarea [contenteditable='true'], [contenteditable='true']").first();
    await composer.click();
    await composer.fill({json.dumps(prompt)});
    await page.waitForTimeout(400);
    await page.keyboard.press("Enter");
    const start = Date.now();
    let src = null;
    while (Date.now() - start < 180000) {{
      const imgs = page.locator("img[src^='blob:'], img[src^='data:image']");
      const n = await imgs.count();
      if (n) {{ src = await imgs.nth(n - 1).getAttribute("src"); if (src) break; }}
      await page.waitForTimeout(2000);
    }}
    if (!src) return {{ ok: false, error: "no generated image appeared" }};
    const data = await page.evaluate(async (src) => {{
      const img = new Image();
      img.src = src;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.naturalWidth; c.height = img.naturalHeight;
      c.getContext("2d").drawImage(img, 0, 0);
      return {{ data: c.toDataURL("image/png"), w: c.width, h: c.height }};
    }}, src);
    const b64 = data.data.split(",")[1];
    fs.writeFileSync(out, Buffer.from(b64, "base64"));
    return {{ ok: true, path: out, w: data.w, h: data.h }};
    """
    try:
        res = subprocess.run(["browser-control", "execute", "--json", script],
                             capture_output=True, text=True, timeout=420)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
    if res.returncode != 0:
        return None
    try:
        payload = json.loads(res.stdout)
    except Exception:
        return None
    if not (payload.get("ok") and payload.get("value", {}).get("ok")):
        return None
    if out.is_file() and out.stat().st_size > 10_000:
        _say(log, f"  plate: Gemini painted {out.name} ({out.stat().st_size // 1024} KB)")
        return out
    return None


def build(project: Path, *, frame: str = "hero", plate: str | None = None,
          gemini: bool = False, layout: str | None = None,
          frame_html: str | None = None, stitch_source: str = "",
          brief: str = "", builtin: bool = False, force: bool = False,
          publish: bool = False, log=None) -> dict[str, Any]:
    from . import asset_critic, storefront_frames as sfr, storefront_preview as sp
    from . import storefront_review as sr, vision_layout as vl

    # 1 — the spec is the project's, and it must exist before anything renders.
    spec_file = project / sp.SPEC_REL
    if not spec_file.is_file():
        return _fail("BLOCKED", f"no {sp.SPEC_REL}",
                     f"devteam storefront-scenes {project.name} --scaffold")
    spec = sp.load_spec(project, create=False)
    scenes = spec.get("scenes") or []

    # 2 — an editorial review decides order and cuts. No review, no listing.
    review = sr.load_review(project, spec)
    if not review:
        return _fail("BLOCKED", "no editorial review",
                     f"devteam storefront-scenes {project.name} --review --apply <review.json>")

    # 3 — render the app's own composables (from origin/main, never a mock).
    _say(log, "~ render: the app's own composables (origin/main)")
    rendered = sp.render_previews(project, log=log)
    if rendered.get("status") != "OK":
        return _fail(rendered.get("status", "ERROR"), rendered.get("error", "render failed"),
                     rendered.get("hint", ""))

    # 4 — compose the frame. This is the step that used to be skippable.
    if frame_html:
        _say(log, f"~ composition: adopting Stitch HTML ({Path(frame_html).name})")
        adopted = sfr.adopt(project, frame, Path(frame_html).read_text(), source=stitch_source, force=force)
        if adopted.get("status") != "OK":
            return _fail(adopted.get("status", "ERROR"), "; ".join(adopted.get("issues") or []) or "adopt refused")
    elif builtin:
        _say(log, "~ composition: BUILTIN template (explicit --builtin; not a designed frame)")
        for scene in scenes:
            sfr.assign(project, scene["id"], None)
    else:
        hero = scenes[0] if scenes else None
        if not hero:
            return _fail("BLOCKED", "no scenes in the spec")
        png = project / ".pipeline/storefront_scenes" / f"{hero['id']}.png"
        if not png.is_file():
            return _fail("ERROR", f"no render at {png.relative_to(project)}")
        plate_path = Path(plate).expanduser() if plate else None
        if plate_path and not plate_path.is_file():
            return _fail("BLOCKED", f"no plate at {plate_path}")
        if not plate_path and gemini:
            prompt = vl.gemini_backdrop_prompt(project, hero, brief=brief)
            plate_path = gemini_plate(project, frame, prompt, log=log)
            if not plate_path:
                return _fail("BLOCKED", "Gemini (browser) produced no usable plate",
                             f"generate the plate manually from `devteam storefront-scenes "
                             f"{project.name} --gemini-brief`, save it, then re-run with --plate <png>")
        if not plate_path:
            saved_plate = project / ".pipeline/storefront_frames" / f"plate-{frame}.png"
            if saved_plate.is_file():
                plate_path = saved_plate
            else:
                return _fail(
                    "BLOCKED", "no backdrop plate for the frame",
                    "the poster is a designed deliverable, not the builtin default: pass "
                    f"--plate <png>, or --gemini to paint one in the browser (Gemini), or "
                    "--builtin to ship the plain template deliberately")
        _say(log, f"~ composition: plate {plate_path.name} ({plate_path.stat().st_size // 1024} KB)")
        layout_path = Path(layout) if layout else _saved(project, frame, "layout")
        if layout_path and layout_path.is_file():
            payload = json.loads(layout_path.read_text())
            _say(log, f"~ layout: reusing {layout_path.name} (deterministic re-run)")
        else:
            _say(log, "~ layout: asking the vision model to compose (numbers, not pixels)")
            payload = vl.propose(project, hero, png, brief=brief, log=log)
            layout_path.parent.mkdir(parents=True, exist_ok=True)
            layout_path.write_text(json.dumps(payload, indent=2) + "\n")
            _say(log, f"  layout saved -> {layout_path.relative_to(project)} (hand-editable)")
        staged = project / ".pipeline/storefront_frames" / f"vision-{frame}.html"
        staged_path, cleaned = vl.render_to(project, hero, png, payload, staged, backdrop=plate_path)
        if cleaned and cleaned.get("removed"):
            box = cleaned.get("box") or [0, 0, 0, 0]
            _say(log, f"  plate watermark healed at {box[0]},{box[1]}")
        adopted = sfr.adopt(project, frame, staged_path.read_text(),
                            source=f"vision:{vl.config()['model']}", force=force)
        if adopted.get("status") != "OK":
            return _fail(adopted.get("status", "ERROR"),
                         "; ".join(adopted.get("issues") or []) or "adopt refused")
        for scene in scenes:
            sfr.assign(project, scene["id"], frame)

    # 5 — composite the reviewed order into Play-ready PNGs.
    _say(log, "~ composite: the reviewed order into phoneScreenshots")
    framed = sp.compose_frames(project, log=log, force=force)
    if framed.get("status") != "OK":
        return _fail(framed.get("status", "ERROR"), framed.get("error", "composite failed"))

    # 6 — the critic is a gate, not a score to admire.
    report = asset_critic.review_store_assets(project)
    score = report.get("score", 0)
    _say(log, f"  critic: {score}/100")
    if report.get("hard_fail") or score < 80:
        return _fail("BLOCKED", f"critic {score}/100" + (" (hard fail)" if report.get("hard_fail") else ""),
                     "; ".join(report.get("issues") or [])[:400])

    out = {"status": "OK", "score": score, "frame": "builtin" if builtin else frame,
           "order": [s["id"] for s in sr.ordered_scenes(spec, review)]}
    if publish:
        from .play_listing import publish_listing_assets
        out["publish"] = publish_listing_assets(project, dry_run=False, log=log)
    return out
