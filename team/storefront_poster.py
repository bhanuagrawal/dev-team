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

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def _app_name(project: Path) -> str:
    """The app's name is a PROJECT fact — read it, never spell it here.

    Team code carries zero project names (the first team law, enforced by
    `devteam verify`): a prompt template that names one app ships a poster for
    that app in every other project's listing. The listing title wins over the
    repo name, because that is the name a store visitor reads.
    """
    listing = project / "distribution/listing/en-US/title.txt"
    try:
        title = listing.read_text(errors="ignore").strip()
    except Exception:
        title = ""
    if title:
        return title
    try:
        profile = json.loads((project / ".pipeline/profile.json").read_text())
    except Exception:
        return "my Android app"
    return profile.get("name") or "my Android app"


def poster_prompt(project: Path, scene: dict[str, Any], direction: dict[str, Any],
                  extra: str = "", index: int = 0, total: int = 0) -> str:
    """The prompt handed to Gemini WITH the real render attached.

    Deliberately says NOTHING about creative direction — no glow, no film grain, no
    phone styling. Composing the asset is the model's job. What the prompt pins is
    the part that must stay true: WHICH screen, WHICH words, WHICH numbers, and the
    app's own palette, because four frames painted in different chats must still
    read as one listing.

    Each instruction below exists because the run got it wrong once, with a real
    listing (2026-11-04):

    * the frame's position and the screen's own identity — the certificate frame
      came back painted with the DEFECT screen, copied from the frame before it;
    * numbers are transcribed, never computed — a progress chip was redrawn as
      "106%", a figure the app cannot produce;
    * the app's palette, quoted from the direction — a frame painted in a fresh
      chat landed on a blue-violet background while its three siblings were dark
      emerald.
    """
    parts = [
        "Create a vertical 9:16 Google Play Store marketing screenshot for my Android app "
        f"{_app_name(project)}.",
    ]
    if total:
        parts.append(f"This is frame {index + 1} of {total} of the listing.")
    parts.append(
        "The attached image is the ONLY app screen this frame may show. Treat it as the "
        "source of truth for the phone's display: the screen in your image must be that "
        "image, kept as it is. Do not redraw, restyle, merge or complete it — and do not "
        "reuse a screen from another image in this conversation.")
    screen = str(scene.get("screen") or "").strip()
    if screen:
        parts.append(f"The attached screen is: {screen}. If the image you are about to "
                     "draw does not show that, draw it again.")
    # The model's failure mode is ADDING: it put the dashboard's brand header, search
    # field and bottom navigation inside the inspection screens — an interface the app
    # does not have (observed 2026-11-04). Naming what the screen does NOT contain is
    # the only instruction that ever stopped it, because "reproduce it exactly" leaves
    # the model free to be helpful.
    absent = str(scene.get("screen_absent") or "").strip()
    if absent:
        parts.append(f"The attached screen does NOT contain {absent} — do not add any of "
                     "it to the phone.")
    parts.append(
        "Every element inside the phone must come from the attachment, in the same order "
        "and position. The frame around it (background, headline, caption) is yours to "
        "compose; the screen is not.")
    # Composition is a CONTRACT, not a suggestion: the first painted sets read as a
    # small phone floating in a mostly empty canvas (owner, 2026-11-07: "ratio lot
    # of empty space and phone not taking enough space"). Naming the proportions is
    # the only instruction that reliably sizes the device, and the bottom band is
    # bounded by the watermark corner the cleaner must keep plain.
    parts.append(
        "COMPOSITION — this decides whether the frame is usable: the phone is the SUBJECT, "
        "not a prop. Draw it 68-74% of the canvas height, anchored to the right edge, with "
        "its ENTIRE DISPLAY VISIBLE — the device body may bleed 0-2% past the edge but never "
        "the screen's own content (a cropped screen made the model invent what it could not "
        "see). Its top edge sits at 10-13% and its bottom edge at 82-85%. Put the copy in "
        "the upper-left, no wider than 44% of the canvas, so it never overlaps the device. "
        "Fill the space under the phone with its soft contact shadow — no wide blank band, "
        "no small floating device, no broad empty top margin.")
    parts += [
        "Every number and status on that screen (counts, percentages, dates, PASS/FAIL, "
        "severity) is part of the screenshot: copy each one exactly as it appears. Never "
        "calculate, increment or round one, and if a value is hard to read, keep it exactly "
        "as drawn instead of substituting a plausible one — a number the app cannot produce "
        "is worse than a soft one.",
        "Print exactly these words, and no other text:",
        f"label: {scene.get('badge') or ''}",
        f"headline: {scene.get('headline') or ''}",
        f"subtext: {scene.get('sub') or ''}",
    ]
    # The owner's "phate phate" (flat/washed, 2026-11-04) is a LOOK complaint, and a
    # look is a brand decision — so it lives in the direction file next to the
    # palette, not in a constant here. Project data, quoted verbatim.
    look = str(direction.get("look") or "").strip()
    if look:
        parts.append(f"Look: {look}")
    palette = direction.get("palette") or {}
    base = str(palette.get("base") or "").strip()
    accent = str(palette.get("accent") or "").strip()
    if base or accent:
        bits = []
        if base:
            bits.append(f"base {base}")
        if accent:
            bits.append(f"accent {accent}")
        parts.append(
            "Keep the marketing frame in the app's own palette (" + ", ".join(bits) +
            "), so every frame of this listing reads as one set. If you have already "
            "produced images in this conversation, match their background treatment, "
            "device framing and type scale.")
    parts += [
        "No watermark, no logos, no price, no rating badges.",
        # Not art direction: a production constraint. The vendor stamps its mark into
        # the bottom-right corner of every image, and the cleaner heals that corner. If
        # the copy or the phone is there, healing would smear the owner's own text — the
        # run refused exactly that. So the corner is asked to stay plain background.
        "Keep the bottom-right corner free of text and of the phone: leave it plain "
        "background.",
    ]
    angle = (direction.get("copy_angle") or {}).get(str(scene.get("id")), "")
    if angle:
        parts.append(f"What this screen is selling: {angle}")
    # A per-scene composition note, because where the copy sits DECIDES whether the
    # heal is safe: the model stamps its sparkle into the bottom-right, and when the
    # caption lives there the cleaner has to smear the owner's own words to remove it
    # (observed 2026-11-04: "Every audit, d▓▓▓ and certificate stored on the d▓▓▓").
    hint = str(scene.get("poster_hint") or "").strip()
    if hint:
        parts.append(f"Composition note: {hint}")
    if extra.strip():
        parts.append(extra.strip())
    return "\n".join(p for p in parts if p.strip())


def _script_syntax_error(script: str) -> str:
    """`node --check` the generated script; "" when it parses.

    The script runs as an async function body, so it is wrapped the same way before
    checking. Returns the first line of the syntax error, or "" when node is absent
    (a machine without node cannot paint at all; that is reported elsewhere).
    """
    node = shutil.which("node")
    if not node:
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "check.js"
        path.write_text("(async () => {\n" + script + "\n})();\n")
        res = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        if res.returncode == 0:
            return ""
        lines = [ln for ln in (res.stderr or "").splitlines() if ln.strip()]
        return lines[0][:200] if lines else "node --check failed"


def _batch_script(jobs: list[dict[str, str]], provider: str = "gemini") -> str:
    """Build the painter script for one provider.

    The transport is identical (the browser-control relay); only the site's
    selectors and the definition of "the model finished" differ. Gemini is the
    default because it keeps a chat-scoped look; ChatGPT is the fallback when
    Gemini's daily image limit is reached (owner directive 2026-11-07).
    """
    if provider == "chatgpt":
        return _chatgpt_batch_script(jobs)
    return _gemini_batch_script(jobs)


def _providers() -> list[str]:
    """Painter order. `DEVTEAM_IMAGE_PROVIDERS` overrides (e.g. 'chatgpt')."""
    raw = (os.environ.get("DEVTEAM_IMAGE_PROVIDERS") or "gemini,chatgpt").strip()
    order = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return [p for p in order if p in ("gemini", "chatgpt")] or ["gemini"]


def _chatgpt_batch_script(jobs: list[dict[str, str]]) -> str:
    """The same batch, painted in ONE signed-in ChatGPT chat.

    Mirrors the Gemini path deliberately (owner directive 2026-11-07: "use the
    same session of chatgpt like we do in gemini, follow other things as well"):
    one fresh chat for the whole set so the frames match, the render attached to
    every prompt, the SENT message proved to carry that render (by its shape), a
    bounded wait, a per-frame sidecar, and a limit that is NAMED rather than
    waited out. A frame that fails does not abort the rest — losing three good
    frames to one bad turn is what made the first batch unusable.
    """
    return f"""
    const JOBS = {json.dumps(jobs)};
    await page.goto("https://chatgpt.com/", {{ waitUntil: "domcontentloaded" }});
    await page.waitForTimeout(4000);
    const composer = page.locator('[contenteditable="true"]').first();
    if (!(await composer.count())) {{
      return {{ ok: false, signin: true, error: "ChatGPT composer not found (signed out?)" }};
    }}
    const fileInput = page.locator('input[type=file][aria-label="Attach photos or videos"], '
                                 + 'input[type=file][aria-label="Attach photos"], '
                                 + 'input[type=file][accept="image/*"]').first();
    // Every image on the canvas, tagged with whether it is still the composer
    // thumbnail. ChatGPT's markup has moved twice; this survives both because it
    // asks the DOM a question (where is it, how big) instead of naming a test id.
    const imgs = () => page.evaluate(() => Array.from(document.querySelectorAll("main img"))
      .map(i => ({{ src: i.getAttribute("src") || "", w: i.naturalWidth, h: i.naturalHeight,
                   composer: !!i.closest("[data-composer-attachments]") }})));
    const results = [];
    for (const job of JOBS) {{
      try {{
        const beforeAttach = (await imgs()).filter(i => i.composer).length;
        await fileInput.setInputFiles(job.src);
        let attachSrc = null;
        for (let i = 0; i < 25; i++) {{
          const comp = (await imgs()).filter(x => x.composer);
          if (comp.length > beforeAttach) {{ attachSrc = comp[comp.length - 1].src; break; }}
          await page.waitForTimeout(1000);
        }}
        if (!attachSrc) {{
          results.push({{ ok: false, scene: job.out, error: "the scene render never attached to the prompt" }});
          continue;
        }}
        await composer.click();
        await composer.fill(job.prompt);
        await page.waitForTimeout(400);
        await page.keyboard.press("Enter");

        // Delivery proof, Gemini's rule and reason: the SENT message must carry
        // the render, and its SHAPE is what a thumbnail cannot fake. A prompt that
        // ships without its screen makes the model invent one.
        let delivered = false;
        for (let i = 0; i < 30; i++) {{
          const sent = (await imgs()).filter(x => !x.composer && x.h > 0 && x.w >= 96
                                                 && Math.abs(x.w / x.h - job.ar) <= 0.06);
          if (sent.length) {{ delivered = true; break; }}
          await page.waitForTimeout(1000);
        }}
        if (!delivered) {{
          results.push({{ ok: false, scene: job.out,
                         error: "the render is not in the sent message — the model was asked to "
                                + "draw a screen it never received" }});
          continue;
        }}

        const start = Date.now();
        let picked = null;
        let dims = [0, 0];
        while (Date.now() - start < 240000) {{
          const fresh = (await imgs()).filter(x => !x.composer && x.src !== attachSrc
                                                 && x.w >= 600 && x.h >= 600);
          if (fresh.length) {{
            const got = fresh[fresh.length - 1];
            picked = got.src; dims = [got.w, got.h];
            break;
          }}
          const tail = (await page.locator("body").innerText()).slice(-700);
          if (/(usage cap|limit reached|you've reached|reached your|try again later|quota)/i.test(tail)) {{
            return {{ ok: false, limit: true, error: tail.slice(-180) }};
          }}
          await page.waitForTimeout(3000);
        }}
        if (!picked) {{
          results.push({{ ok: false, scene: job.out, error: "no NEW image in the model's answer" }});
          continue;
        }}
        if (picked.startsWith("blob:")) {{
          const data = await page.evaluate(async (s) => {{
            const img = new Image(); img.src = s; await img.decode();
            const c = document.createElement("canvas");
            c.width = img.naturalWidth; c.height = img.naturalHeight;
            c.getContext("2d").drawImage(img, 0, 0);
            return c.toDataURL("image/png");
          }}, picked);
          fs.writeFileSync(job.out, Buffer.from(data.split(",")[1], "base64"));
        }} else {{
          const resp = await context.request.get(picked);
          fs.writeFileSync(job.out, await resp.body());
        }}
        if (job.sidecar) fs.writeFileSync(job.sidecar,
          JSON.stringify({{ prompt_sha: job.sha, source_sha: job.source_sha, chat: page.url() }}));
        results.push({{ ok: true, out: job.out, w: dims[0], h: dims[1] }});
      }} catch (err) {{
        results.push({{ ok: false, scene: job.out, error: String(err && err.message || err) }});
      }}
    }}
    return {{ ok: results.length && results.every(r => r.ok), chat: page.url(), results }};
    """


def _gemini_batch_script(jobs: list[dict[str, str]]) -> str:
    """One browser session, one Gemini chat, one asset per job.

    Every asset of a listing is drawn in the SAME conversation on purpose: the model
    keeps the look it just established, so the screenshots read as one set instead of
    three unrelated posters. Each job = {out, src, prompt}.
    """
    return f"""
    const JOBS = {json.dumps(jobs)};
    // A run paints its frames in ONE conversation so they read as a set — but it
    // must be a conversation this run STARTED. Reusing whichever Gemini tab is
    // already open continued the previous listing's chat, and the prompt's own
    // "match the images already in this conversation" then carried the OLD look
    // into every repaint (observed 2026-11-07: a --force repaint of a warm-stone
    // listing came back dark, matching the chat's previous dark frames).
    // Navigating the session page to /app opens a NEW chat; it is the one route
    // both the headless runner and the browser-control relay accept (a fresh
    // `context.newPage()` is rejected by the relay).
    await page.goto("https://gemini.google.com/app", {{ waitUntil: "domcontentloaded" }});
    await page.bringToFront();
    const gen = () => page.locator("model-response img[src^='blob:'], "
                                 + "model-response img[src^='data:image']");
    const results = [];
    // The composer keeps the attached render as a blob thumbnail. Counting them is
    // how the run proves the screen was actually handed to the model: when an
    // upload silently fails the prompt still sends, and the model — with no
    // attached screen — paints the PREVIOUS frame's screen again, which is a
    // wrong-screen listing no downstream gate can see (observed 2026-11-04: the
    // certificate frame came back showing the defect checklist).
    const attachedCount = () => page
      .locator("rich-textarea img[src^='blob:'], rich-textarea img[src^='data:image'], "
               + ".file-preview-container img").count();
    for (const job of JOBS) {{
      const before = await gen().count();
      const attachedBefore = await attachedCount();
      await page.locator("[aria-label=\\"Upload & tools\\"]").first().click();
      await page.waitForTimeout(700);
      await page.locator("input[type=file]").first().setInputFiles(job.src);
      let attached = false;
      for (let i = 0; i < 20; i++) {{
        if (await attachedCount() > attachedBefore) {{ attached = true; break; }}
        await page.waitForTimeout(1000);
      }}
      if (!attached) {{
        results.push({{ ok: false, scene: job.out, error: "the scene render never attached to the prompt" }});
        break;
      }}
      await page.waitForTimeout(2500);
      const composer = page.locator("rich-textarea [contenteditable=true], [contenteditable=true]").first();
      await composer.click();
      await composer.fill(job.prompt);
      await page.waitForTimeout(400);
      const sentBefore = await page.locator("user-query").count();
      await page.keyboard.press("Enter");
      // The proof that matters: the SENT message carries the render. The composer
      // thumbnail can belong to an earlier upload (or to the previous frame's
      // output), so the sent bubble is the only honest witness — and it is what the
      // model actually sees. Gemini echoes the attachment back as a RE-ENCODED
      // thumbnail whose pixel width is a property of the browser tab (measured
      // 2026-11-05: 460px in a relay tab, 230px in a saved chat), so a width
      // threshold is not evidence — it read every real delivery as missing and
      // blocked four fresh renders for a screen the model had already received.
      // What the thumbnail CANNOT fake is the render's SHAPE: its width/height
      // ratio is the scene's own, which no icon and no earlier frame matches.
      let delivered = false;
      for (let i = 0; i < 30; i++) {{
        const queries = page.locator("user-query");
        const nq = await queries.count();
        if (nq > sentBefore) {{
          const pic = queries.nth(nq - 1).locator("img").first();
          const dims = await pic.evaluate(
            el => [el.naturalWidth, el.naturalHeight]).catch(() => [0, 0]);
          const w = dims[0], h = dims[1];
          const ratioOk = job.ar > 0 && h > 0 && w >= 96
            && Math.abs(w / h - job.ar) <= 0.06;
          if (ratioOk) {{ delivered = true; break; }}
        }}
        await page.waitForTimeout(1000);
      }}
      if (!delivered) {{
        results.push({{ ok: false, scene: job.out,
                       error: "the render is not in the sent message — the model was asked to "
                              + "draw a screen it never received" }});
        break;
      }}
      const start = Date.now();
      let picked = null;
      while (Date.now() - start < 300000) {{
        const imgs = gen();
        const n = await imgs.count();
        if (n > before) {{
          const last = imgs.nth(n - 1);
          // Same re-encoding as the upload thumbnail: the answer's blob is
          // ~572px wide in one tab and smaller in another, so "bigger than an
          // icon" is the honest floor, not a fixed 500px.
          const w = await last.evaluate(el => el.naturalWidth).catch(() => 0);
          if (w >= 300) {{ picked = await last.getAttribute("src"); break; }}
        }}
        // Gemini's cap must be NAMED, not waited out: without this the run sits
        // here until it times out and reports "no image", and the ChatGPT fallback
        // never fires (owner directive 2026-11-07).
        const tail = (await page.locator("body").innerText()).slice(-700);
        if (/(reached your limit|limit reached|daily limit|image limit|quota|too many requests|try again (tomorrow|later))/i.test(tail)) {{
          return {{ ok: false, limit: true, error: tail.slice(-180) }};
        }}
        await page.waitForTimeout(3000);
      }}
      if (!picked) {{ results.push({{ ok: false, scene: job.out, error: "no NEW image in the model's answer" }}); break; }}
      const data = await page.evaluate(async (s) => {{
        const img = new Image(); img.src = s; await img.decode();
        const c = document.createElement("canvas");
        c.width = img.naturalWidth; c.height = img.naturalHeight;
        c.getContext("2d").drawImage(img, 0, 0);
        return {{ data: c.toDataURL("image/png"), w: c.width, h: c.height }};
      }}, picked);
      fs.writeFileSync(job.out, Buffer.from(data.data.split(",")[1], "base64"));
      // The cache key travels with the image: which prompt painted it.
      if (job.sidecar) fs.writeFileSync(job.sidecar,
        JSON.stringify({{ prompt_sha: job.sha, source_sha: job.source_sha, chat: page.url() }}));
      results.push({{ ok: true, out: job.out, w: data.w, h: data.h }});
    }}
    return {{ ok: results.length && results.every(r => r.ok), chat: page.url(), results }};
    """


def _prompt_sha(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def paint_batch(jobs: list[dict[str, str]], timeout: int = 900,
                log=None) -> dict[str, Any]:
    """Paint a batch of assets, walking the signed-in image models in order.

    Gemini first (it keeps a chat-scoped look). When it reports its image limit,
    the SAME jobs go to ChatGPT in the owner's signed-in Chrome (owner directive
    2026-11-07: "use logged-in chatgpt session like gemini if limit of gemini
    reaches"). Only a LIMIT moves to the next model — a composition failure is a
    prompt problem and would burn the second model's quota for nothing.
    """
    from . import gemini_browser

    attempts: list[str] = []
    for provider in _providers():
        script = _batch_script(jobs, provider=provider)
        bad = _script_syntax_error(script)
        if bad:
            return {"ok": False, "value": {}, "provider": provider, "attempts": attempts,
                    "script_invalid": f"the generated {provider} browser script is invalid: {bad}"}
        if log:
            log(f"  {provider}: painting {len(jobs)} asset(s) in one chat")
        try:
            # A whole listing in one chat can outlast a single frame: ChatGPT's
            # turns are slower than Gemini's, so its batch gets a longer budget.
            res = gemini_browser.execute(
                script, timeout=(1800 if provider == "chatgpt" else timeout), log=log)
        except FileNotFoundError:
            return {"ok": False, "value": {}, "provider": None, "attempts": attempts,
                    "error": "Playwright or browser-control is not installed"}
        except subprocess.TimeoutExpired:
            attempts.append(f"{provider}: timed out")
            continue
        if res.returncode != 0:
            text = ((res.stdout or "") + (res.stderr or "")).strip()[:300]
            attempts.append(f"{provider}: {text or 'browser execution failed'}")
            if log:
                log(f"  {provider}: failed — trying the next signed-in image model")
            continue
        try:
            payload = json.loads(res.stdout)
        except Exception:
            attempts.append(f"{provider}: returned no JSON")
            continue
        value = payload.get("value") or {}
        if payload.get("ok") and value.get("ok"):
            return {"ok": True, "value": value, "provider": provider, "attempts": attempts}
        if value.get("limit") or re.search(r"\blimit\b|quota|usage cap",
                                           json.dumps(value), re.I):
            attempts.append(f"{provider}: image limit reached")
            if log:
                log(f"  {provider}: image limit reached — falling back")
            continue
        return {"ok": False, "value": value, "provider": provider, "attempts": attempts,
                "error": "one or more assets were not generated"}
    return {"ok": False, "value": {}, "provider": None, "attempts": attempts,
            "error": "no image model produced the assets"}


def generate_all(project: Path, scenes: list[dict[str, Any]], direction: dict[str, Any],
                 log=None, extra: str = "", force: bool = False,
                 only: set[str] | None = None) -> dict[str, Any]:
    """Paint every poster in ONE Gemini chat, one prompt per scene.

    Returns {"status", "posters": {scene_id: path}, "chat", "painted"}.

    The cache is keyed by the PROMPT, not by the scene name. Reusing on the file
    name alone meant an edit to poster_prompt never reached a listing: the run
    reported "reusing poster-*.png" and shipped images painted by the old prompt
    (observed 2026-11-01 — a prompt change was silently a no-op). Each poster now
    carries a sidecar with the hash of the prompt that painted it; a different
    hash repaints, and `force` repaints regardless.
    """
    out_dir = project / ".pipeline/storefront_posters"
    out_dir.mkdir(parents=True, exist_ok=True)
    posters: dict[str, Path] = {}
    jobs: list[dict[str, Any]] = []
    for order, scene in enumerate(scenes):
        name = _poster_name(scene)
        out = out_dir / f"poster-{name}.png"
        source = project / ".pipeline/storefront_scenes" / f"{scene['id']}.png"
        if not source.is_file():
            return {"status": "BLOCKED", "error": f"no render at {source.relative_to(project)}"}
        prompt = poster_prompt(project, scene, direction, extra=extra,
                               index=order, total=len(scenes))
        sha = _prompt_sha(prompt)
        # The cache key is the prompt AND the render it was painted from. A poster
        # is a depiction of one specific screen; when the app changed and only the
        # copy stayed the same, reusing on the prompt alone shipped a poster of the
        # old build (observed 2026-11-04: four fresh renders, four old posters, and
        # a green critic because the copy matched).
        try:
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        except Exception:
            source_sha = ""
        sidecar = out.with_suffix(".json")
        # Delivery proof for the upload is the render's SHAPE (see _batch_script):
        # a job without a measured ratio could only be checked by the width
        # heuristic that just read every real delivery as missing. Refuse to paint
        # rather than paint without a witness.
        size = _png_size(source)
        if not size or not size[0] or not size[1]:
            return {"status": "BLOCKED",
                    "error": f"cannot measure the render at {source.relative_to(project)} — "
                             "the upload cannot be proved delivered without its shape"}
        aspect = round(size[0] / size[1], 3)
        painted_by = ""
        painted_source = ""
        try:
            _side = json.loads(sidecar.read_text()) or {}
            painted_by = _side.get("prompt_sha") or ""
            painted_source = _side.get("source_sha") or ""
        except Exception:
            painted_by = ""
        if out.is_file() and not force and painted_by == sha and painted_source == source_sha:
            if log:
                log(f"  poster: reusing {out.name} ({out.stat().st_size // 1024} KB, same prompt)")
            posters[scene["id"]] = out
            continue
        if out.is_file() and log:
            log(f"  poster: repainting {out.name} — "
                + ("--force" if force else "the prompt changed since it was painted"))
        # Repaint ONE frame without spending the others: the prompt still says
        # "frame N of M" for the WHOLE listing (so its hash matches the batch run
        # and the cache stays valid), but only the named scenes become jobs.
        if only and scene["id"] not in only:
            if out.is_file():
                posters[scene["id"]] = out
            continue
        jobs.append({"out": str(out), "src": str(source), "prompt": prompt,
                     "sha": sha, "source_sha": source_sha, "sidecar": str(sidecar),
                     "ar": aspect})
    if not jobs:
        return {"status": "OK", "posters": posters, "chat": None, "painted": 0}
    outcome = paint_batch(jobs, timeout=900, log=log)
    value = outcome.get("value") or {}
    if outcome.get("script_invalid"):
        return {"status": "ERROR", "error": outcome["script_invalid"],
                "hint": "this is a team-code bug, not a model one — nothing was painted"}
    if outcome.get("provider") is None and not outcome.get("ok"):
        return {"status": "BLOCKED",
                "error": outcome.get("error") or "no image model produced the frames",
                "detail": "; ".join(outcome.get("attempts") or [])[:400]}
    if log:
        log(f"  poster: painted by {outcome.get('provider')}")
    for job, made in zip(jobs, value.get("results") or []):
        if not made.get("ok"):
            return {"status": "BLOCKED", "error": str(made.get("error") or "no image produced"),
                    "chat": value.get("chat"),
                    "scene": _poster_scene_hint(job.get("out") or "")}
        path = Path(made["out"])
        posters[_scene_id_for(Path(job["src"]).stem)] = path
        if log:
            log(f"  poster: painted {path.name} ({made.get('w')}x{made.get('h')}, "
                f"{path.stat().st_size // 1024} KB)")
        (project / ".pipeline" / f"gemini-poster-{path.stem.replace('poster-', '')}.txt").write_text(
            job["prompt"])
    if not outcome.get("ok"):
        return {"status": "BLOCKED", "error": "one or more posters were not generated",
                "chat": value.get("chat")}
    return {"status": "OK", "posters": posters, "chat": value.get("chat"),
            "painted": len(jobs)}


def _scene_id_for(stem: str) -> str:
    return stem


def _poster_scene_hint(path: str) -> str:
    """The cache name (`poster-<scene>.png`) of the frame that failed, for the hint."""
    name = Path(path).name
    return name[len("poster-"):-len(".png")] if name.startswith("poster-") else ""


def clean_poster(poster: Path, strict: bool = False,
                 half_ratio: float | None = None,
                 out: Path | None = None) -> dict[str, Any]:
    """Strip the vendor sparkle an image model leaves in a corner.

    A Play listing is the owner's product page; a model's watermark on it is an
    advert for the model. Reuses the plate cleaner (it finds the mark by local
    contrast), and refuses the poster if the heal cannot prove the mark is gone.
    """
    from . import plate_clean

    return plate_clean.clean(poster, strict=strict, half_ratio=half_ratio, out=out)


# A painted listing comes back FLAT: the model renders a mid-grey-friendly image
# and the web UI's 572x1024 output is then resampled up, which softens edges twice.
# The owner's words for it were "phate phate" (washed out). Grading is the last step
# of OUR pipeline, so it costs no image and is reproducible: one tone curve plus a
# light unsharp mask, both applied in the same Chrome pass that does the upscale.
# "Phate phate" (the owner's words, 2026-11-04). Measured in this pipeline, not
# assumed: a heavy contrast lift plus a vignette turned the source's faint texture
# into visible blocks, and a 3x3 convolution haloed every glyph. What survives a
# 572 -> 1080 upscale is a gentle curve and a blurred-copy unsharp; everything
# stronger is visibly worse than flat.
DEFAULT_GRADE = {"contrast": 1.06, "saturate": 1.12, "brightness": 1.0,
                 "sharpen": 0.20, "sharpen_radius": 1.2,
                 "vignette": 0.0, "vignette_inner": 55}


def grade_of(spec: dict[str, Any] | None) -> dict[str, float]:
    """The tone mapping for this listing: spec `grade` wins, else the default."""
    base = dict(DEFAULT_GRADE)
    raw = (spec or {}).get("grade") or {}
    for k, v in raw.items():
        if k in base:
            try:
                base[k] = float(v)
            except (TypeError, ValueError):
                pass
    return base


def to_play_size(poster: Path, out: Path, width: int = 1080, height: int = 1920,
                 grade: dict[str, float] | None = None) -> dict[str, Any]:
    """Upscale the painted poster to a size Play accepts, and grade it.

    Play's phone screenshots need >= 1080x1920 and an aspect ratio of at most 2:1.
    The web UI returns ~572x1024, so the poster is resampled in Chrome. That is an
    upscale: the composition survives, small type softens — and it is reported as
    such rather than passed off as a native render.

    The grade is a MEASUREMENT-FREE tone map: contrast, saturation, a touch of black
    point and a 3x3 unsharp mask. It changes how the pixels look, never what they
    say, so a graded frame still carries the app's exact text.
    """
    from .marketing_asset_generator import _find_chrome_executable

    grade = grade or dict(DEFAULT_GRADE)
    chrome = _find_chrome_executable()
    if not chrome:
        return {"status": "ERROR", "error": "Google Chrome not found for the upscale"}
    out.parent.mkdir(parents=True, exist_ok=True)
    page = out.with_suffix(".html")
    k = float(grade.get("sharpen") or 0)
    # Unsharp mask as original*(1+k) - blur*k. A 3x3 convolution kernel was tried
    # first and it amplified the source's compression blocks into visible noise on
    # a flat dark background; a blurred copy as the "low pass" is smooth and only
    # lifts edges.
    sharpen_svg = (
        f'<feGaussianBlur in="SourceGraphic" stdDeviation="{float(grade.get("sharpen_radius") or 1.2)}" result="lo"/>'
        f'<feComposite in="SourceGraphic" in2="lo" operator="arithmetic" '
        f'k1="0" k2="{1 + k:.3f}" k3="{-k:.3f}" k4="0"/>'
        if k > 0 else ""
    )
    page.write_text(
        "<!DOCTYPE html><html><head><style>"
        "html,body{margin:0;background:#000;width:" + str(width) + "px;height:" + str(height) + "px}"
        f"#vig{{position:fixed;inset:0;pointer-events:none;"
        f"background:radial-gradient(ellipse at 50% 40%,rgba(0,0,0,0) {int(grade.get('vignette_inner', 42))}%,"
        f"rgba(0,0,0,{grade.get('vignette', 0.30)}) 100%)}}"
        f"#shot{{display:block;width:{width}px;height:{height}px;object-fit:cover;"
        f"filter:contrast({grade.get('contrast')}) saturate({grade.get('saturate')}) "
        f"brightness({grade.get('brightness')}) url(#grade)}}"
        "</style></head><body>"
        '<svg width="0" height="0" style="position:absolute">'
        '<filter id="grade" x="0" y="0" width="100%" height="100%">'
        f"{sharpen_svg}"
        "</filter></svg>"
        f"<img id='shot' src='{poster.resolve().as_uri()}'>"
        # A vignette is depth without noise: it darkens the corners the model leaves
        # flat and lifts the phone out of the page. Amplifying contrast on a dark
        # background instead surfaced the source's compression blocks.
        f'<div id="vig"></div>'
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
            "upscaled_from": _png_size(poster), "grade": grade}


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
