"""Stitch-authored storefront frames — the poster the real app sits inside.

Why this exists
---------------
`storefront_preview._frame_html` is ONE composition: centred pill, centred
headline, centred supporting line, centred phone, same aura underneath. Every
app that ships through this team therefore reads like every other app on the
shelf, and the thing a store listing is judged on — the first 2 seconds of a
thumb-scroll — stops being a deliverable anyone looks twice at.

The screenshot itself cannot become creative: it is a photograph of the real
composable, and a drawn impression of the UI is a lie (see
`storefront_preview`). The POSTER around it can. So art direction moves to
Stitch, which composes at the exact 1620x3200 frame and leaves one 1080x2400
hole; this module fills that hole with the real render.

The contract is inverted because Stitch cannot be *given* the render: its MCP
accepts a text prompt and a project title, every other create argument is
rejected ("Request contains an invalid argument") and no generation tool takes
an image. Stitch draws the poster with a named empty slot; the truth is
composited in afterwards, at 1:1, with no resampling.

Determinism is preserved the way the Kotlin harness is: the authored HTML is
ADOPTED into the project (`docs/frames/<name>.html`), committed, and rendered
offline. Stitch is an authoring step, never a release-time dependency — a
listing must be reproducible from a revision, not from a vendor session.

What is enforced at adoption
----------------------------
- exactly one `img#devteam-screen`, declaring 1080x2400, and measurably that size
- the slot sits fully inside the canvas (a poster that bleeds the phone off the
  edge is cropping the *app*, which is a false claim about the product)
- the document renders at exactly 1620x3200 with no letterboxing
- no other raster image anywhere: a fake UI image is how mock screenshots get
  back into a listing that claims to be real
- `data-slot` text markers for badge/headline/sub, so one composition serves
  every scene instead of freezing one scene's copy into the art

Zero project names — generic across all Android / KMP projects.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from html import escape, unescape
from pathlib import Path
from typing import Any

from .core import now_iso, profile_of, save_json

# Play refuses a screenshot whose long side exceeds twice the short side, so the
# frame canvas stays under 2:1 and the screen opening stays at exactly 1080 wide.
CANVAS_W, CANVAS_H = 1620, 3200
SCREEN_W, SCREEN_H = 1080, 2400

REGISTRY_REL = "docs/storefront-frames.json"
FRAMES_DIR_REL = "docs/frames"
SCREEN_ID = "devteam-screen"
# A screenshot drawn 2px off its own box is a rounding artefact; drawn 20px off it
# is a resample. Only the second is a lie, so only the second is refused.
SLOT_TOLERANCE = 3
TEXT_SLOTS = ("badge", "headline", "sub")
MARKER = "devteam storefront-frame"

# Raster sources that are not the slot are refused: an <img> next to a real
# screenshot is how a hand-drawn "mock" enters a listing that claims provenance.
_ALLOWED_RASTER_PREFIXES = ("data:image/svg+xml",)


# --------------------------------------------------------------------------- #
# registry — which composition each scene ships inside
# --------------------------------------------------------------------------- #
def registry_path(project: Path) -> Path:
    return project / REGISTRY_REL


def frame_file(project: Path, name: str) -> Path:
    return project / FRAMES_DIR_REL / f"{slug(name)}.html"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-") or "frame"


def load_registry(project: Path) -> dict[str, Any]:
    f = registry_path(project)
    if f.is_file():
        try:
            data = json.loads(f.read_text())
        except Exception:
            data = {}
        data.setdefault("frames", {})
        data.setdefault("assign", {})
        data.setdefault("canvas", [CANVAS_W, CANVAS_H])
        return data
    return {
        "_comment": "Storefront frame compositions. A DELIVERABLE: each frame is an "
                    "adopted HTML poster with an empty 1080x2400 slot for the real app "
                    "screenshot. Assign scenes to frames; unassigned scenes keep the "
                    "builtin layout.",
        "canvas": [CANVAS_W, CANVAS_H],
        "frames": {},
        "assign": {},
    }


def save_registry(project: Path, registry: dict[str, Any]) -> Path:
    path = registry_path(project)
    save_json(path, registry)
    return path


def assign(project: Path, scene_id: str, frame: str | None) -> dict[str, Any]:
    registry = load_registry(project)
    if frame in (None, "", "builtin", "classic"):
        registry["assign"].pop(str(scene_id), None)
    else:
        registry["assign"][str(scene_id)] = slug(frame)
    save_registry(project, registry)
    return registry


def visible_text(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _corpus(project: Path) -> str:
    parts: list[str] = []
    for f in sorted((project / "distribution/listing").glob("*/*.txt")):
        parts.append(f.read_text(errors="ignore"))
    spec_file = project / "docs/storefront.json"
    if spec_file.is_file():
        try:
            spec = json.loads(spec_file.read_text())
        except Exception:
            spec = {}
        for scene in spec.get("scenes") or []:
            parts += [str(scene.get(k) or "") for k in ("badge", "headline", "sub")]
    parts.append(str((profile_of(project).get("goals") or {}).get("vision") or ""))
    parts.append(str(profile_of(project).get("name") or ""))
    return " ".join(parts).lower()


def allowed_numbers(project: Path) -> list[str]:
    """Numbers the poster is allowed to print: the app's own copy and sample data.

    A poster that says "48.2 MB -> 4.6 MB" is making a measurement claim about a
    file that does not exist. The fixture data in the scene spec is real sample
    content the app renders, so those values are the ones a frame may show.
    """
    corpus = _corpus(project)
    spec_file = project / "docs/storefront.json"
    if spec_file.is_file():
        try:
            spec = json.loads(spec_file.read_text())
            corpus += " " + str(spec.get("fixtures") or "")
        except Exception:
            pass
    seen: list[str] = []
    for n in re.findall(r"(?<![\w.])(\d{1,4}(?:\.\d)?)(?![\w.])", corpus):
        if n not in seen:
            seen.append(n)
    return seen[:24]


def invented_claims(project: Path, html: str) -> list[str]:
    """Claims the poster makes that the app's own copy does not support.

    Prompt instructions are a request; this is the check. Two things can invent a
    feature: naming a capability the app lacks, and quoting a number it never
    quotes ("184 pages/sec", "-78% avg", "99.4% accuracy") — a poster is a claim,
    so both are reported before the composition is committed.
    """
    text = visible_text(html)
    lower = text.lower()
    _, absent = capabilities(project)
    problems = [f'claims "{c}", which the app does not have'
                for c in absent if c in lower]

    # The repo directory is not the product. Observed: a poster headed "PDFKITKMP"
    # and a "ENGINE: PURE LOCAL KMP" chip, because the model carried the folder
    # name out of its project context.
    product = product_name(project).lower()
    repo = project.name.lower()
    if repo != product and repo in lower.replace(" ", ""):
        problems.append(f'shows the repository name "{project.name}" as the product name')

    corpus = _corpus(project)
    corpora = corpus
    spec_file = project / "docs/storefront.json"
    if spec_file.is_file():
        try:
            corpora += " " + str(json.loads(spec_file.read_text()).get("fixtures") or "")
        except Exception:
            pass
    corpus_nums = set(re.findall(r"\d+(?:\.\d+)?", corpora))
    geometry = {str(SCREEN_W), str(SCREEN_H), str(CANVAS_W), str(CANVAS_H)}
    stray = sorted(n for n in set(re.findall(r"\d+(?:\.\d+)?", text))
                   if n not in corpus_nums and n not in geometry and set(n) - {"0", "."})
    if stray:
        problems.append("quotes numbers the app never quotes: " + ", ".join(stray[:10]))
    return problems


def frame_for_scene(project: Path, scene_id: str) -> str | None:
    return load_registry(project)["assign"].get(str(scene_id))


def product_name(project: Path) -> str:
    """What the app is CALLED on the shelf — never the repo directory.

    A prompt seeded with "PdfKitKMP" makes Stitch draw a build artefact: the
    store title is the product, so the store listing wins, then the profile name
    cleaned of repo suffixes, then the directory.
    """
    for listing in sorted((project / "distribution/listing").glob("*/title.txt")):
        line = listing.read_text(errors="ignore").strip().splitlines()
        if line and line[0].strip():
            return line[0].strip()
    raw = str(profile_of(project).get("name") or project.name)
    cleaned = re.sub(r"(KMP|Android|App)$", "", raw).strip() or raw
    cleaned = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cleaned)
    return cleaned.strip()


def capabilities(project: Path) -> tuple[list[str], list[str]]:
    """(what the app does, what it does NOT do) — read from the app's own words.

    Stitch will happily draw OCR, cloud sync, accounts and subscriptions for a
    local PDF tool, and a poster that advertises a feature the app does not have
    is a store-policy problem, not a design one. The allow-list is the project's
    own vocabulary; the deny-list is what the project's copy never claims.
    """
    corpus: list[str] = []
    for rel in ("distribution/listing/en-US/full_description.txt",
                "distribution/listing/en-US/short_description.txt"):
        f = project / rel
        if f.is_file():
            corpus.append(f.read_text(errors="ignore"))
    spec_file = project / "docs/storefront.json"
    if spec_file.is_file():
        try:
            spec = json.loads(spec_file.read_text())
        except Exception:
            spec = {}
        for scene in spec.get("scenes") or []:
            corpus += [str(scene.get(k) or "") for k in ("badge", "headline", "sub")]
            corpus += [str(c) for c in (scene.get("chips") or [])]
    vision = (profile_of(project).get("goals") or {}).get("vision") or ""
    corpus.append(str(vision))

    text = " ".join(corpus).lower()
    # Capabilities the app demonstrably has: the sections of its own listing copy.
    capabilities_out = [line.strip(" •-—").strip()
                        for line in " ".join(corpus).splitlines()
                        if line.strip().startswith("•") and len(line.strip()) > 8]
    # Claims it must never make: products in this category advertise them, the
    # project's copy never mentions them, and a poster cannot invent them.
    candidates = [
        "cloud sync", "cloud storage", "account", "sign in", "login", "subscription",
        "premium", "watermark", "ocr", "ai", "chatbot", "collaborate", "share link",
        "online", "upload", "server", "batch convert", "password", "signature",
    ]
    absent = [c for c in candidates if c not in text]
    return capabilities_out, absent


# --------------------------------------------------------------------------- #
# minimal, quote-aware HTML surgery (no parser dependency, no reformatting)
# --------------------------------------------------------------------------- #
def _tag_end(html: str, start: int) -> int:
    """Index just past the `>` closing the tag that starts at `start`."""
    i, quote = start, None
    while i < len(html):
        ch = html[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == ">":
            return i + 1
        i += 1
    return -1


def _screen_tag(html: str) -> tuple[int, int] | None:
    for m in re.finditer(r"<img\b", html, re.I):
        end = _tag_end(html, m.start())
        if end > 0 and re.search(rf"""\bid\s*=\s*["']{re.escape(SCREEN_ID)}["']""", html[m.start():end]):
            return m.start(), end
    return None


def _slot_spans(html: str, slot: str) -> list[dict[str, Any]]:
    """Every element carrying `data-slot="<slot>"`, as open/inner/close offsets."""
    spans: list[dict[str, Any]] = []
    open_re = re.compile(r"<([a-zA-Z][\w:-]*)\b[^>]*\bdata-slot\s*=\s*[\"']" + re.escape(slot) + r"[\"'][^>]*>")
    for m in open_re.finditer(html):
        tag = m.group(1)
        inner_start = m.end()
        # Walk forward counting same-name tags so a nested <span> cannot end the
        # slot early; slots are leaf text nodes, but the walk costs nothing and a
        # truncated replacement corrupts the poster rather than failing loudly.
        depth, inner_end = 1, None
        for mm in re.finditer(rf"<(/?){re.escape(tag)}\b[^>]*?(/?)>", html[inner_start:], re.I):
            if mm.group(1) == "" and mm.group(2) == "/":
                continue
            depth += -1 if mm.group(1) else 1
            if depth == 0:
                inner_end = inner_start + mm.start()
                break
        if inner_end is None:
            continue
        spans.append({"open": m.start(), "open_end": inner_start, "inner_end": inner_end, "tag": tag})
    return spans


def _fill_slot(html: str, slot: str, value: str) -> str:
    spans = _slot_spans(html, slot)
    if not spans:
        return html
    out = html
    for span in reversed(spans):
        out = out[:span["open_end"]] + escape(value) + out[span["inner_end"]:]
    return out


def _apply_chips(html: str, chips: list[str]) -> str:
    """Fill the frame's chip slots in order; hide the ones the scene does not use
    (an empty chip reads as lorem, and an invented stat is a false claim)."""
    spans = _slot_spans(html, "chip")
    if not spans:
        return html
    out = html
    for i, span in reversed(list(enumerate(spans))):
        text = chips[i] if i < len(chips) else None
        if text:
            out = out[:span["open_end"]] + escape(text) + out[span["inner_end"]:]
        else:
            hidden_open = out[span["open"]:span["open_end"]]
            if "style=" in hidden_open:
                hidden_open = re.sub(r"style\s*=\s*[\"']([^\"']*)[\"']",
                                     lambda m: f'style="{m.group(1)};display:none"', hidden_open, count=1)
            else:
                hidden_open = hidden_open[:-1].rstrip() + ' style="display:none">'
            # Replace the opening tag AND the inner text, but never the rest of the
            # document: a slice that stops at inner_end drops everything after it.
            out = out[:span["open"]] + hidden_open + out[span["inner_end"]:]
    return out


def _set_screen_src(html: str, uri: str) -> str:
    span = _screen_tag(html)
    if not span:
        return html
    start, end = span
    tag = html[start:end]
    if re.search(r"\bsrc\s*=\s*[\"'][^\"']*[\"']", tag):
        tag = re.sub(r"(\bsrc\s*=\s*)[\"'][^\"']*[\"']", lambda m: f'{m.group(1)}"{uri}"', tag, count=1)
    else:
        tag = tag[:-1].rstrip() + f' src="{uri}">'
    return html[:start] + tag + html[end:]


# Pinned at FILL time, not trusted from the author: whatever CSS Stitch wrote, the
# screenshot occupies exactly its own pixels. A frame that scales the slot 0.9x or
# stretches it to a parent's width is the failure this pipeline must never ship, and
# "the model promised" is not a guarantee. Layoutlib/vendor CSS is overridable; the
# composed file is ours.
_SLOT_CSS = (
    "<style id=\"devteam-slot-guard\">"
    f"#%s{{width:{SCREEN_W}px!important;height:{SCREEN_H}px!important;"
    "min-width:0!important;min-height:0!important;max-width:none!important;max-height:none!important;"
    "object-fit:none!important;object-position:top left!important;transform:none!important;"
    "border:0!important;padding:0!important;margin:0!important;box-sizing:content-box!important;"
    "display:block!important;}"
    "</style>"
) % SCREEN_ID


def guard_slot(html: str) -> str:
    if 'id="devteam-slot-guard"' in html or "id='devteam-slot-guard'" in html:
        return html
    if "</head>" in html:
        return html.replace("</head>", _SLOT_CSS + "\n</head>", 1)
    return _SLOT_CSS + html


# --------------------------------------------------------------------------- #
# validation — structural, then measured in a real layout
# --------------------------------------------------------------------------- #
def structural_issues(html: str) -> list[str]:
    issues: list[str] = []
    span = _screen_tag(html)
    if not span:
        issues.append(f'no <img id="{SCREEN_ID}"> — the poster has no slot for the real app screenshot')
    else:
        tag = html[span[0]:span[1]]
        for attr, want in (("width", SCREEN_W), ("height", SCREEN_H)):
            m = re.search(rf"\b{attr}\s*=\s*[\"']?(\d+)", tag, re.I)
            if not m:
                issues.append(f'the slot does not declare {attr}="{want}"')
            elif int(m.group(1)) != want:
                issues.append(f'the slot declares {attr}={m.group(1)}, expected {want}')

    missing = [s for s in TEXT_SLOTS if not _slot_spans(html, s)]
    if missing:
        issues.append(
            "missing text slot(s) " + ", ".join(f'data-slot="{s}"' for s in missing)
            + " — without them the composition freezes one scene's copy into the art"
        )

    others = []
    for m in re.finditer(r"<img\b", html, re.I):
        end = _tag_end(html, m.start())
        tag = html[m.start():end] if end > 0 else ""
        if re.search(rf"""\bid\s*=\s*["']{re.escape(SCREEN_ID)}["']""", tag):
            continue
        src = re.search(r"\bsrc\s*=\s*[\"']([^\"']*)", tag)
        if src and not src.group(1).startswith(_ALLOWED_RASTER_PREFIXES):
            others.append(src.group(1)[:60])
    if others:
        issues.append("non-slot raster image(s) — a poster must not contain a drawn "
                      "app screen: " + ", ".join(others))
    return issues


_PROBE = """
<script>
window.addEventListener('load', function () {
  try {
    var img = document.getElementById('%(id)s');
    var c = (img && img.closest('.poster-canvas')) || document.body.firstElementChild || document.body;
    var cr = c.getBoundingClientRect();
    var sr = img ? img.getBoundingClientRect() : null;
    // Every element is made hit-testable first: an overlay that only sets
    // pointer-events:none still RENDERS over the screenshot, and a probe that
    // respects pointer-events would call that poster clean.
    var s = document.createElement('style');
    s.textContent = '*{pointer-events:auto!important}';
    document.head.appendChild(s);
    var covered = [];
    if (sr && sr.width && sr.height) {
      var mark = function (px, py) {
        var el = document.elementFromPoint(px, py);
        if (!el || el === img || el.contains(img) || img.contains(el)) return;
        var name = el.tagName.toLowerCase() + (el.className && el.className.toString ?
                   '.' + el.className.toString().trim().split(/\\s+/).slice(0, 2).join('.') : '');
        if (covered.indexOf(name) === -1) covered.push(name);
      };
      // A card crossing the screen edge intrudes a narrow sliver: an interior-only
      // grid misses it and calls the poster clean (observed: a 160px sliver of a
      // 1080px screen survived a 5x9 grid). Sample the straight edges every 12px,
      // skipping 120px of each rounded corner, then fill the interior.
      var inset = 16, skirt = 120, step = 12;
      for (var px = sr.left + skirt; px <= sr.right - skirt; px += step) {
        mark(px, sr.top + inset);
        mark(px, sr.bottom - inset);
      }
      for (var py = sr.top + skirt; py <= sr.bottom - skirt; py += step) {
        mark(sr.left + inset, py);
        mark(sr.right - inset, py);
      }
      for (var i = 1; i <= 12; i++) {
        for (var j = 1; j <= 24; j++) {
          mark(sr.left + sr.width * i / 13, sr.top + sr.height * j / 25);
        }
      }
    }
    document.title = 'DEVTEAM_FRAME::' + JSON.stringify({
      doc: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
      canvas: [Math.round(cr.width), Math.round(cr.height), Math.round(cr.left), Math.round(cr.top)],
      slot: sr ? [Math.round(sr.left - cr.left), Math.round(sr.top - cr.top),
                  Math.round(sr.width), Math.round(sr.height)] : null,
      fit: img ? getComputedStyle(img).objectFit : null,
      nat: img ? [img.naturalWidth, img.naturalHeight] : null,
      covered: covered
    });
  } catch (e) { document.title = 'DEVTEAM_FRAME::ERR:' + e; }
});
</script>
"""


def measure(chrome: str, html_file: Path, timeout: int = 90) -> dict[str, Any]:
    """Render once and read the real geometry — never assume a CSS rule held."""
    html = html_file.read_text()
    probe = _PROBE % {"id": SCREEN_ID}
    tmp = html_file.with_name(html_file.stem + ".measure.html")
    tmp.write_text(html.replace("</body>", probe + "</body>") if "</body>" in html
                   else html + probe)
    try:
        res = subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
             "--force-device-scale-factor=1", f"--window-size={CANVAS_W},{CANVAS_H}",
             "--virtual-time-budget=5000", "--dump-dom", tmp.resolve().as_uri()],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"layout measurement timed out after {timeout}s"}
    finally:
        tmp.unlink(missing_ok=True)
    m = re.search(r"DEVTEAM_FRAME::(\{.*?\})</title>", res.stdout or "", re.S)
    if not m:
        return {"error": "the poster did not finish loading (no layout probe result)"}
    try:
        return json.loads(m.group(1))
    except Exception as exc:
        return {"error": f"unreadable layout probe: {exc}"}


def layout_issues(measured: dict[str, Any]) -> list[str]:
    if measured.get("error"):
        return [measured["error"]]
    issues: list[str] = []
    doc_w, doc_h = measured.get("doc") or [0, 0]
    if (doc_w, doc_h) != (CANVAS_W, CANVAS_H):
        issues.append(f"the document lays out at {doc_w}x{doc_h}, not {CANVAS_W}x{CANVAS_H} "
                      "— Play would ship a letterboxed frame")
    cw, ch, cl, ct = measured.get("canvas") or [0, 0, 0, 0]
    if (cw, ch) != (CANVAS_W, CANVAS_H):
        issues.append(f"the poster canvas measures {cw}x{ch}, not {CANVAS_W}x{CANVAS_H}")
    slot = measured.get("slot")
    if not slot:
        issues.append("the slot could not be measured")
        return issues
    x, y, w, h = slot
    if (w, h) != (SCREEN_W, SCREEN_H):
        issues.append(f"the slot renders {w}x{h}, not {SCREEN_W}x{SCREEN_H} — a resampled "
                      "screenshot is no longer a claim about the app")
    fit = measured.get("fit")
    if fit and fit not in ("none", "fill"):
        issues.append(f"the slot stretches its image (object-fit: {fit}) — the screenshot is "
                      "being scaled to fit a box that is not its own size")
    nat = measured.get("nat")
    if nat:
        # The scene render is not always exactly 1080x2400 (Robolectric rounds the
        # dp->px qualifier: 392dp @440dpi lays out at 1078x2398). The old frame
        # stretched that 2px away — a resample nobody can see and nobody can prove.
        # The rule is instead: the box must be the size of the image it holds, so
        # the image is drawn 1:1; a box that is meaningfully bigger or smaller than
        # the screenshot would scale it.
        if abs(w - nat[0]) > SLOT_TOLERANCE or abs(h - nat[1]) > SLOT_TOLERANCE:
            issues.append(f"the slot is {w}x{h} but the screenshot is {nat[0]}x{nat[1]} — "
                          "it would be scaled to fit instead of drawn 1:1")
    if x < 0 or y < 0 or x + w > CANVAS_W or y + h > CANVAS_H:
        issues.append(f"the slot sits at {x},{y} and overflows the canvas — the poster is "
                      "cropping the app, which misrepresents the product")
    covered = measured.get("covered") or []
    if covered:
        issues.append("element(s) drawn ON TOP of the screenshot: " + ", ".join(covered[:3])
                      + " — a card over the app hides the UI it claims to show")
    return issues


# --------------------------------------------------------------------------- #
# adopt — Stitch's HTML becomes a committed, verified composition
# --------------------------------------------------------------------------- #
def adopt(project: Path, name: str, html: str, source: str | None = None,
          chrome: str | None = None, force: bool = False) -> dict[str, Any]:
    issues = structural_issues(html)
    if issues:
        return {"status": "REJECTED", "issues": issues}

    # A wordmark naming the repository is not a style preference — it puts a build
    # artefact on the store shelf. Blocked, not warned.
    product = product_name(project).lower()
    repo = project.name.lower()
    if repo != product and repo in visible_text(html).lower().replace(" ", ""):
        return {"status": "REJECTED",
                "issues": [f'names the repository "{project.name}" as the product — '
                           f'the app is called "{product_name(project)}"']}

    target = frame_file(project, name)
    if target.is_file() and not force:
        return {"status": "EXISTS", "file": str(target.relative_to(project)),
                "hint": "re-run with --force to replace this composition"}

    if chrome is None:
        from .marketing_asset_generator import _find_chrome_executable
        chrome = _find_chrome_executable()
    if not chrome:
        return {"status": "ERROR", "error": "Google Chrome / Chromium not found — cannot verify layout"}

    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_suffix(".staged.html")
    # Measure what will actually SHIP: the slot guard is applied at fill time, so a
    # frame whose authored CSS pads the slot by 3px is fine (the guard pins it) while
    # a frame that positions the phone off-canvas is not.
    staged.write_text(guard_slot(html))
    try:
        measured = measure(chrome, staged)
    finally:
        staged.unlink(missing_ok=True)
    issues = layout_issues(measured)
    if issues:
        return {"status": "REJECTED", "issues": issues, "measured": measured}

    target.write_text(html)
    registry = load_registry(project)
    registry["frames"][slug(name)] = {
        "html": str(target.relative_to(project)),
        "canvas": [CANVAS_W, CANVAS_H],
        "adopted_at": now_iso(),
        "sha256": hashlib.sha256(html.encode()).hexdigest()[:16],
        "authored_by": "stitch",
        "source": source or "",
        "slots": {
            "screen": [SCREEN_W, SCREEN_H],
            "text": [s for s in TEXT_SLOTS if _slot_spans(html, s)],
            "chips": len(_slot_spans(html, "chip")),
        },
    }
    save_registry(project, registry)
    return {"status": "OK", "file": str(target.relative_to(project)), "measured": measured,
            "invented": invented_claims(project, html)}


# --------------------------------------------------------------------------- #
# fill + render handoff
# --------------------------------------------------------------------------- #
def fill(template: str, scene: dict[str, Any], screen_png: Path) -> str:
    """Put the real screenshot and this scene's copy into an adopted composition."""
    html = _set_screen_src(template, screen_png.resolve().as_uri())
    html = guard_slot(html)
    for slot in TEXT_SLOTS:
        html = _fill_slot(html, slot, str(scene.get(slot) or ""))
    return _apply_chips(html, [str(c) for c in (scene.get("chips") or [])])


def html_for_scene(project: Path, scene: dict[str, Any],
                   screen_png: Path) -> tuple[str | None, str]:
    """(html, source) for a scene — None means "use the builtin layout"."""
    name = frame_for_scene(project, str(scene.get("id") or ""))
    if not name:
        return None, "builtin"
    path = frame_file(project, name)
    if not path.is_file():
        return None, f"builtin (assigned frame '{name}' is missing: {path.relative_to(project)})"
    return fill(path.read_text(), scene, screen_png), f"frame:{name}"


# --------------------------------------------------------------------------- #
# prompt compiler — the brief Stitch answers
# --------------------------------------------------------------------------- #
def compile_prompt(project: Path, scene: dict[str, Any], brief: str = "",
                   name: str = "", scenes: list[dict[str, Any]] | None = None) -> str:
    """A prompt that produces a poster this module can actually adopt.

    The prompt pins three things Stitch would otherwise improvise: the canvas,
    the slot markup, and the text markers. Everything else — composition,
    hierarchy, decorative vectors — is deliberately left to it, because that is
    the part that makes a listing memorable.
    """
    from .design import extract_design_tokens
    profile = profile_of(project)
    app = product_name(project)
    tokens = extract_design_tokens(project)
    accent = tokens.get("primary_accent")
    try:
        from .marketing_asset_generator import app_accent
        accent = app_accent(project) or accent
    except Exception:
        pass

    has, has_not = capabilities(project)
    has_lines = "\n".join(f"- {c}" for c in has) or "- (the listing copy names no features)"
    hasnt_lines = ", ".join(has_not) or "(none)"
    numbers = allowed_numbers(project)
    numbers_line = ", ".join(numbers) if numbers else "(the app quotes no numbers)"
    total = len(scenes or [])
    index = next((i + 1 for i, s in enumerate(scenes or []) if s is scene), 0)

    badge = scene.get("badge") or ""
    headline = scene.get("headline") or ""
    sub = scene.get("sub") or ""
    chips = scene.get("chips") or []
    chip_line = (
        "- Optional proof chips, in this order, each as "
        '<span data-slot="chip">text</span>: ' + "; ".join(chips)
    ) if chips else (
        "- You may include up to 2 optional proof chips, each as "
        '<span data-slot="chip">text</span>.'
    )

    return f"""Design a Google Play store screenshot poster as a single static HTML document.
This is a MARKETING FRAME for {app} — a poster, not an app screen. The app screen itself is composited in later and must not be drawn by you.

=== EXACTLY ONE POSTER, FOR ONE SCREEN ===
Produce one poster, for {app}'s screen "{scene.get("id")}"{" (" + str(index) + " of " + str(total) + " in the store listing)" if total else ""}. Do not generate variants, alternates, a set, or a design system page. One document, one composition, complete.

=== WHAT {app.upper()} ACTUALLY DOES, AND NOTHING ELSE ===
These are its features, quoted from its own store listing:
{has_lines}

It does NOT have, and must never show, label, badge, hint at or imply: {hasnt_lines}.
Do not invent a feature, a metric, a statistic, a percentage, a file size, a duration or a user count. The only numbers you may print anywhere are these, which come from the app's own copy and its sample data: {numbers_line}. If none of them suits the layout, print no number at all. No lorem ipsum, no "Coming soon", no ratings or award badges, no fake user names or avatars.

The product is called exactly "{app}". Any wordmark, header or label that names it must read "{app}" and nothing else — never "{app}KMP", "{app} Pro", "{app} App", "{app} Studio" or any invented suffix, and never the name of a source directory.

=== TYPOGRAPHY THAT HAS TO SURVIVE ===
The headline below is real copy and must break at word boundaries, never inside a word, and never with a hyphen you added. Give the headline block at least 620px of width — or the full canvas width if it sits above the phone — and let it wrap to at most 3 lines. A 300px column turns "ALL-IN-ONE PDF WORKSTATION" into four broken lines, which reads as a defect, not a design.

=== CANVAS (hard requirement) ===
- The document body must be exactly {CANVAS_W}px wide and {CANVAS_H}px tall, with margin:0, padding:0, overflow:hidden.
- One element with class "poster-canvas" sized exactly {CANVAS_W}px x {CANVAS_H}px, position:relative, holding everything.
- All sizing in CSS pixels. No max-width media queries, no responsive scaling, no vh/vw units. This renders once, in a {CANVAS_W}x{CANVAS_H} window.
- Nothing may extend past the canvas edges, and the canvas must be fully covered by the composition (no white or transparent margins).

=== THE SLOT (hard requirement) ===
Reserve one empty region of exactly {SCREEN_W}x{SCREEN_H} px for the real screenshot. It must be this exact element, placed inside a phone mockup whose bezel is at least 14px thick:

  <img id="{SCREEN_ID}" width="{SCREEN_W}" height="{SCREEN_H}" src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='{SCREEN_W}' height='{SCREEN_H}'><rect width='{SCREEN_W}' height='{SCREEN_H}' fill='%23111'/></svg>" alt="app screenshot" style="display:block;width:{SCREEN_W}px;height:{SCREEN_H}px;object-fit:none">

Rules for the slot:
- Draw NOTHING inside it. No status bar, no notch content, no wallpaper, no fake cards, no caption. It stays black until the real screenshot lands on top.
- The whole slot must sit inside the canvas. Do not bleed it off an edge — a cropped app screen misrepresents the product.
- THE SLOT IS A RESERVED DEAD ZONE. No chip, card, label, badge, stat, glow, gradient overlay or text may intersect its rectangle, even partially. Decoration sits beside the phone or against its bezel margin (the phone's padding, at most 24px thick) and stops there. Everything in the composition must be laid out around this rectangle, not across it.
- Never apply transform: scale(), rotate(), skew() or translate() to the slot, to its parent, or to the phone mockup. The slot must measure exactly {SCREEN_W}x{SCREEN_H} CSS pixels on screen. A transformed slot resamples the screenshot, and a resampled screenshot is no longer a claim about the app. Tilt decoration instead, never the display.
- The phone mockup, bezel included, must be entirely on the canvas. Choose its position so that the slot plus 24px of bezel on every side is inside 0..{CANVAS_W} and 0..{CANVAS_H}.
- No full-canvas overlay, grain, scrim, vignette, noise layer or gradient may be layered ABOVE the phone. Texture the canvas background underneath it instead.
- The slot must be the only <img> in the document with a raster source. Everything decorative must be inline SVG or pure CSS.

=== TEXT SLOTS (hard requirement) ===
The copy below goes into these exact markers, verbatim, so one composition can serve every screenshot:

  <span data-slot="badge">{badge}</span>
  <h1 data-slot="headline">{headline}</h1>
  <p data-slot="sub">{sub}</p>

{chip_line}

Set them in the composition however you like — size, weight, alignment, colour, letter-spacing — but keep each marker as a single element with no nested elements of its own tag. Do not add lorem ipsum or invented metrics anywhere.

=== BRAND ===
- Canvas base: {tokens.get("surface_base")} — near-black, matte.
- Elevated surfaces: {tokens.get("surface_elevated")} / {tokens.get("surface_highlight")}.
- Primary accent: {accent}. Secondary: {tokens.get("secondary_accent")}. Warning: {tokens.get("warning_accent")}.
- Headline face: {tokens.get("headline_font")}. Body: {tokens.get("body_font")}. Labels/chips: {tokens.get("label_font")} (uppercase, letter-spaced). Use the Google Fonts CDN.
- Headline must be at least 80px and legible at thumb size. Supporting copy at least 30px, contrast at least 4.5:1.

=== COMPOSITION BRIEF ===
{brief.strip() or "Compose the poster around this screen. Vary the alignment and the hierarchy of the copy, and let the phone mockup sit off-centre with one decorative element breaking its silhouette."}

Deliberately avoid the default Play-store layout of: centred pill badge, centred all-caps headline, centred paragraph, one centred phone, centred everything. Take a position instead — asymmetric margins, an overlapping card, a diagonal crop, a metric callout straddling the bezel. Make it feel art-directed, not templated.

=== OUTPUT ===
Return one complete HTML document. Tailwind via CDN is allowed; no build step, no external images, no scripts that fetch anything. The document must render identically on every run.
"""


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def verify(html_file: Path, chrome: str | None = None) -> list[str]:
    """Re-measure a COMPOSED frame: the copy changed, so the layout did too.

    A composition that was clean with the placeholder can break when a headline
    wraps one line longer — so the check runs on the file that produced the PNG,
    every time, not only when the frame was adopted.
    """
    if chrome is None:
        from .marketing_asset_generator import _find_chrome_executable
        chrome = _find_chrome_executable()
    if not chrome:
        return ["Google Chrome not found — the composed frame could not be verified"]
    return layout_issues(measure(chrome, html_file))


def status(project: Path) -> dict[str, Any]:
    registry = load_registry(project)
    rows = []
    for name, meta in sorted(registry["frames"].items()):
        path = project / meta.get("html", "")
        rows.append({
            "name": name,
            "file": meta.get("html"),
            "present": path.is_file(),
            "adopted_at": meta.get("adopted_at"),
            "slots": meta.get("slots") or {},
            "scenes": sorted(s for s, f in registry["assign"].items() if f == name),
        })
    return {"registry": REGISTRY_REL, "canvas": registry.get("canvas"),
            "frames": rows, "assign": registry["assign"]}


def format_status(data: dict[str, Any]) -> str:
    lines = [f"STOREFRONT FRAMES  ({data['registry']}, canvas {data['canvas'][0]}x{data['canvas'][1]})"]
    if not data["frames"]:
        lines.append("  (none adopted — every scene renders the builtin layout)")
    for f in data["frames"]:
        mark = "✓" if f["present"] else "!"
        slots = (f.get("slots") or {}).get("text") or []
        lines.append(f"  {mark} {f['name']:<18} {f.get('file')}"
                     + (f"  ← {', '.join(f['scenes'])}" if f["scenes"] else "  (unassigned)"))
        if not f["present"]:
            lines.append("      file missing — re-adopt or reassign")
        elif not slots:
            lines.append("      no text slots")
    return "\n".join(lines)
