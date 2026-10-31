"""One deterministic command for the whole storefront asset pass.

Why this exists
---------------
The pass used to be four prose steps an agent had to remember and chain by hand:
render → review → compose the frame (Stitch or the vision model, with a plate from
Gemini) → composite → publish. Every step was documented somewhere, and an agent
that skipped one still produced a plausible-looking result: the release shipped
the BUILTIN frame template while the contract required a composed one, because
nothing in the pipeline demanded it.

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


# Play allows 2-8 phone screenshots. The owner's rule is stricter: a listing needs at
# least four, because two frames cannot show what the app is, how it works, that it
# works, and who is in control — the last of those was the frame that was missing
# when the listing shipped three.
MIN_SCREENSHOTS = 4


def _say(log, text: str) -> None:
    if log:
        log(text)


def _fail(status: str, error: str, hint: str = "") -> dict[str, Any]:
    return {"status": status, "error": error, "hint": hint}


def rank_prefixed(name: str) -> bool:
    """True for a file this pipeline names (`01_hero.png`), never for a hand-added one."""
    import re

    return bool(re.match(r"^\d{2,}_[A-Za-z0-9_-]+\.(png|jpg|jpeg)$", name))


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
    // Only an image that was NOT on the page before the prompt counts as painted.
    // The old loop took the page's LAST blob image, which a resumed conversation
    // already had — so a chat with an earlier plate satisfied it instantly and the
    // run reported "Gemini painted" without anything being generated.
    const before = new Set(await page.locator("img[src^='blob:'], img[src^='data:image']")
      .evaluateAll(els => els.map(e => e.getAttribute("src"))));
    const composer = page.locator("rich-textarea [contenteditable='true'], [contenteditable='true']").first();
    await composer.click();
    await composer.fill({json.dumps(prompt)});
    await page.waitForTimeout(400);
    await page.keyboard.press("Enter");
    const start = Date.now();
    let src = null;
    while (Date.now() - start < 180000) {{
      const srcs = await page.locator("img[src^='blob:'], img[src^='data:image']")
        .evaluateAll(els => els.map(e => e.getAttribute("src")));
      const fresh = srcs.filter(s => s && !before.has(s));
      if (fresh.length) {{ src = fresh[fresh.length - 1]; break; }}
      await page.waitForTimeout(2000);
    }}
    if (!src) return {{ ok: false, error: "no NEW image appeared after the prompt" }};
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


def _frame_name(scene: dict[str, Any]) -> str:
    """The poster a scene gets when it authors its own: its id without the rank.

    One frame per scene is the default because each scene argues something
    different (direction `copy_angle`) and a plate is prompted per argument.
    Sharing one poster across a listing was observed: three screenshots, one
    Gemini prompt, the same backdrop behind a calm home screen, a pause and a
    dashboard.
    """
    from .storefront_frames import slug

    base = re.sub(r"^\d+[-_\s]+", "", str(scene.get("id") or ""))
    return slug(base or scene.get("id") or "frame")


def _compose_scene(project: Path, scene: dict[str, Any], name: str, *,
                   direction, sd, sfr, sp, vl, plate: str | None, gemini: bool,
                   layout: str | None, brief: str, force: bool, log) -> dict[str, Any]:
    """Plate → layout → adopt for ONE scene. Returns {} on success, a refusal otherwise."""
    png = project / ".pipeline/storefront_scenes" / f"{scene['id']}.png"
    if not png.is_file():
        return _fail("ERROR", f"no render at {png.relative_to(project)}")
    plate_path = Path(plate).expanduser() if plate else None
    if plate_path and not plate_path.is_file():
        return _fail("BLOCKED", f"no plate at {plate_path}")
    if not plate_path:
        saved = project / ".pipeline/storefront_frames" / f"plate-{name}.png"
        if saved.is_file():
            plate_path = saved
    if not plate_path and gemini:
        prompt = vl.gemini_backdrop_prompt(
            project, scene, brief=brief, direction=sd.plate_brief(direction, scene))
        (project / ".pipeline").mkdir(parents=True, exist_ok=True)
        (project / ".pipeline" / f"gemini-backdrop-{name}.txt").write_text(prompt)
        plate_path = gemini_plate(project, name, prompt, log=log)
    if not plate_path:
        return _fail(
            "BLOCKED", f"no backdrop plate for frame '{name}' ({scene.get('id')})",
            f"the poster is a designed deliverable, not the builtin default: pass --gemini "
            f"to paint one in the browser (Gemini) per scene, or "
            f"`devteam storefront-scenes {project.name} --gemini-brief --frame-scene "
            f"{scene.get('id')}` and save it to "
            f".pipeline/storefront_frames/plate-{name}.png, or --builtin to ship the plain "
            f"template deliberately")
    _say(log, f"  {scene.get('id')}: plate {plate_path.name} "
              f"({plate_path.stat().st_size // 1024} KB) -> frame:{name}")
    layout_path = Path(layout) if layout else _saved(project, name, "layout")
    if layout_path and layout_path.is_file():
        payload = json.loads(layout_path.read_text())
        _say(log, f"  {scene.get('id')}: layout reusing {layout_path.name} (deterministic re-run)")
    else:
        _say(log, f"  {scene.get('id')}: layout from the vision model")
        framed_brief = sd.layout_brief(direction, scene)
        if brief.strip():
            framed_brief += "\nExtra brief: " + brief.strip()
        payload = vl.propose(project, scene, png, brief=framed_brief, log=log)
        layout_path = _saved(project, name, "layout")
        layout_path.parent.mkdir(parents=True, exist_ok=True)
        layout_path.write_text(json.dumps(payload, indent=2) + "\n")
        _say(log, f"  layout saved -> {layout_path.relative_to(project)} (hand-editable)")
    staged = project / ".pipeline/storefront_frames" / f"vision-{name}.html"
    staged_path, cleaned = vl.render_to(project, scene, png, payload, staged, backdrop=plate_path)
    if cleaned and cleaned.get("removed"):
        box = cleaned.get("box") or [0, 0, 0, 0]
        _say(log, f"  plate watermark healed at {box[0]},{box[1]}")
    adopted = sfr.adopt(project, name, staged_path.read_text(),
                        source=f"vision:{vl.config()['model']}", force=force)
    if adopted.get("status") != "OK":
        return _fail(adopted.get("status", "ERROR"),
                     "; ".join(adopted.get("issues") or []) or "adopt refused")
    sfr.assign(project, str(scene["id"]), name)
    return {}


def build(project: Path, *, frame: str = "auto", plate: str | None = None,
          gemini: bool = False, layout: str | None = None,
          frame_html: str | None = None, stitch_source: str = "",
          brief: str = "", builtin: bool = False, force: bool = False,
          poster: bool = False, strict: bool = False, publish: bool = False,
          log=None) -> dict[str, Any]:
    from . import asset_critic, storefront_direction as sd, storefront_frames as sfr
    from . import storefront_preview as sp, storefront_review as sr, vision_layout as vl

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

    # 2a — how many frames ship. This is a gate, not advice: a run that would ship
    # fewer than MIN_SCREENSHOTS stops before it renders anything.
    shipping = [s for s in sr.ordered_scenes(spec, review)]
    if len(shipping) < MIN_SCREENSHOTS:
        cut = ", ".join(sorted(sr.rejected_ids(review))) or "none"
        return _fail(
            "BLOCKED",
            f"{len(shipping)} screenshot(s) ship, the listing needs at least {MIN_SCREENSHOTS}",
            f"add scene(s) to {sp.SPEC_REL} (id, badge, headline, sub, composable), re-run "
            f"`devteam storefront-scenes {project.name} --scaffold --render`, then record a review "
            f"that keeps them: `devteam storefront-scenes {project.name} --review --apply <json>` "
            f"(currently cut: {cut})")

    # 2b — the DIRECTION: what the listing is trying to say. Without it the poster
    # is undirected (a generic plate plus whatever the model proposes first), and
    # there is nothing to review the result against.
    direction = sd.load(project, spec)
    if direction is None:
        raw = sd.load_raw(project)
        if raw is None:
            return _fail("BLOCKED", "no listing direction",
                         f"devteam storefront-direction {project.name} --seed, fill the judgements "
                         f"(concept, audience, copy_angle, palette.emphasis, evidence), then "
                         f"--apply <file>")
        return _fail("BLOCKED", "the listing direction cannot drive a run",
                     "; ".join(sd.validate(raw, spec)[:3]))
    _say(log, f"~ direction: {direction.get('concept')}")

    # 3 — render the app's own composables (from origin/main, never a mock).
    _say(log, "~ render: the app's own composables (origin/main)")
    rendered = sp.render_previews(project, log=log)
    if rendered.get("status") != "OK":
        return _fail(rendered.get("status", "ERROR"), rendered.get("error", "render failed"),
                     rendered.get("hint", ""))

    # 4 — the POSTER path: Gemini paints the whole asset from the real render.
    # This is the owner's chosen shape: the preview screen goes IN as an attachment
    # and the marketing image comes OUT, per scene, one prompt each.
    if poster:
        from . import storefront_poster as spt
        from . import storefront_review as sr
        ordered = sr.ordered_scenes(spec, review)
        listing = project / "distribution/listing/en-US/images/phoneScreenshots"
        listing.mkdir(parents=True, exist_ok=True)
        made = spt.generate_all(project, ordered, direction, log=log, extra=brief)
        if made.get("status") != "OK":
            return _fail(made.get("status", "ERROR"), made.get("error", "posters not generated"),
                         "the poster is a Gemini deliverable: re-run, or drop a finished image at "
                         ".pipeline/storefront_posters/poster-<scene>.png")
        if log and made.get("chat"):
            log(f"  chat: {made['chat']}")
        written = []
        for rank, scene in enumerate(ordered, 1):
            poster_file = made["posters"].get(scene["id"])
            if not poster_file:
                return _fail("ERROR", f"no poster for {scene['id']}")
            cleaned = spt.clean_poster(Path(poster_file), strict=strict)
            if cleaned.get("status") != "OK":
                return _fail(cleaned.get("status", "ERROR"),
                             cleaned.get("error", "the model's watermark could not be removed"),
                             "a vendor mark must not ship on the listing")
            if log:
                marker = "✓" if cleaned.get("verified", True) else "⚠"
                log(f"  {scene['id']}: {marker} watermark healed at {cleaned.get('box')} "
                    f"(residual {cleaned.get('residual')})"
                    + ("" if cleaned.get("verified", True) else
                       " — could not be proved clean; look at the corner, "
                       "or re-run with --strict to refuse outright"))
            name = f"{rank:02d}_{sr._slug(str(scene['id']))}.png"
            sized = spt.to_play_size(Path(cleaned.get("file") or poster_file), listing / name)
            if sized.get("status") != "OK":
                return _fail("ERROR", sized.get("error", "upscale failed"))
            if log:
                log(f"  ✓ {name} (Gemini {sized.get('upscaled_from')} -> Play {sized.get('size')})")
            written.append(name)
        # A frame the review no longer ships must LEAVE the listing. The code-composed
        # path does this; the poster path used to append, so a re-ordered review left
        # the old rank+slug files behind and they were uploaded as extra screenshots
        # (observed: six frames on Play for a four-frame review, and the critic still
        # said 95/100 because it only checks order, not extras).
        stale = [p for p in listing.glob("*.png")
                 if p.name not in written and rank_prefixed(p.name)]
        for p in stale:
            p.unlink()
        if stale and log:
            log("  − removed superseded frame(s): " + ", ".join(sorted(p.name for p in stale)))
        report = asset_critic.review_store_assets(project)
        score = report.get("score", 0)
        _say(log, f"  critic: {score}/100")
        if report.get("hard_fail") or score < 80:
            return _fail("BLOCKED", f"critic {score}/100", "; ".join(report.get("issues") or [])[:400])
        out = {"status": "OK", "score": score, "frame": "gemini-poster",
               "concept": direction.get("concept"),
               "order": [s["id"] for s in ordered],
               "screenshots": written, "chat": made.get("chat")}
        if publish:
            from .play_listing import publish_listing_assets
            out["publish"] = publish_listing_assets(project, dry_run=False, log=log)
        return out

    # 4b — the PLATE path: Gemini paints the backdrop, code draws phone and copy.
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
            prompt = vl.gemini_backdrop_prompt(
                project, hero, brief=brief, direction=sd.plate_brief(direction, hero))
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
            framed_brief = sd.layout_brief(direction, hero)
            if brief.strip():
                framed_brief += "\nExtra brief: " + brief.strip()
            payload = vl.propose(project, hero, png, brief=framed_brief, log=log)
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
           "concept": direction.get("concept"),
           "order": [s["id"] for s in sr.ordered_scenes(spec, review)]}
    if publish:
        from .play_listing import publish_listing_assets
        out["publish"] = publish_listing_assets(project, dry_run=False, log=log)
    return out
