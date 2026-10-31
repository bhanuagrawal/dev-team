"""Remove the watermark an image model stamps into a backdrop plate.

Why this exists
---------------
Gemini (and every other consumer image tool) puts a sparkle/favicon mark in a
corner of what it generates. On a poster that mark is a vendor advert sitting on
a store listing; Adobe, Google Play policy and basic taste all say the same thing:
it must not ship.

Crop-only is not enough: the plate is scaled to cover 1620x3200, so a corner crop
is visible as a shifted composition, and a mark that drifts inward survives the
crop. So the mark is found and healed:

    load plate -> find the bright blob in the bottom-right quadrant
    -> mirror the pixels above it over the box (a soft gradient heals invisibly)
    -> re-scan to PROVE nothing bright is left
    -> write <plate>.clean.png and use that for every render

The check is the point: a heal that cannot say "removed" is a guess. If the
re-scan still finds a bright blob the caller is told, and the frame is refused
rather than shipping a watermark.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# The mark is a bright glyph on a dark plate, in the lower-right area. Both bounds
# are relative so the same code works at 572x1024 (what the Gemini web UI returns)
# and at whatever a paid API returns.
QUADRANT = (0.70, 0.70)      # scan from 70% across / 70% down
# The mark is a translucent glyph, not white: measured at L~95 on a plate whose
# dark-red glow peaks near L~60. A fixed 150 threshold found nothing, so the cut is
# RELATIVE to the brightest pixel in the corner — which is the mark by definition.
MIN_LUMA = 60                # ignore corners that are uniformly dark
DELTA = 18                   # how far above the corner's own background the mark sits
MAX_AREA = 0.25              # a big bright region is content, not a watermark
PAD = 8                      # grow the found box before healing

_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<img id="plate" src="%(src)s" style="display:none">
<script>
const LO = %(lo)s, MIN_LUMA = %(minluma)s, DELTA = %(delta)s, PAD = %(pad)s, MAX_AREA = %(maxarea)s;
window.addEventListener('load', () => {
  const img = document.getElementById('plate');
  const w = img.naturalWidth, h = img.naturalHeight;
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.drawImage(img, 0, 0);

  function scan() {
    const d = g.getImageData(0, 0, w, h).data;
    const xs = Math.floor(w * LO), ys = Math.floor(h * LO);
    let peak = 0;
    for (let y = ys; y < h; y++) {
      for (let x = xs; x < w; x++) {
        const i = (y * w + x) * 4;
        const l = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
        if (l > peak) peak = l;
      }
    }
    if (peak < MIN_LUMA) return null;
    const cut = Math.max(MIN_LUMA, peak - DELTA);
    let x0 = w, y0 = h, x1 = -1, y1 = -1, n = 0;
    for (let y = ys; y < h; y++) {
      for (let x = xs; x < w; x++) {
        const i = (y * w + x) * 4;
        const l = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
        if (l > cut) {
          n++;
          if (x < x0) x0 = x; if (x > x1) x1 = x;
          if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
      }
    }
    if (!n) return null;
    const area = (x1 - x0 + 1) * (y1 - y0 + 1);
    if (area > (w - xs) * (h - ys) * MAX_AREA) return { tooBig: true, x0, y0, x1, y1, n, peak: Math.round(peak) };
    return { x0, y0, x1, y1, n, peak: Math.round(peak) };
  }

  const raw = scan();
  const found = raw && !raw.tooBig ? raw : null;
  if (raw && raw.tooBig) {
    document.title = 'PLATE::' + JSON.stringify({
      size: [w, h], found: false, tooBig: true, peak: raw.peak,
      note: 'bright region fills the corner - treated as content, not a mark'
    });
    return;
  }
  let healed = null;
  if (found) {
    const bx0 = Math.max(0, found.x0 - PAD), bx1 = Math.min(w - 1, found.x1 + PAD);
    const by0 = Math.max(0, found.y0 - PAD), by1 = Math.min(h - 1, found.y1 + PAD);
    const src = g.getImageData(0, 0, w, h);
    const out = g.createImageData(w, h);
    out.data.set(src.data);
    // Reflect the band above the box over its top edge: on a soft gradient this is
    // indistinguishable from the plate that was there before the mark.
    // Rebuild the patch from the plate's own gradient: for each column take the
    // colour a few px above the box and a few px below it, and interpolate between
    // them down the box. Copying one band instead leaves a flat smear that is
    // obvious against a glow — interpolation follows the light, plus a little grain
    // so the patch is not suspiciously smooth.
    const depth = by1 - by0 + 1;
    const upRow = Math.max(0, by0 - 3), downRow = Math.min(h - 1, by1 + 3);
    for (let x = bx0; x <= bx1; x++) {
      const ai = (upRow * w + x) * 4, bi = (downRow * w + x) * 4;
      for (let y = by0; y <= by1; y++) {
        const f = (y - by0) / Math.max(1, depth - 1);
        const di = (y * w + x) * 4;
        for (let ch = 0; ch < 3; ch++) {
          const v = src.data[ai + ch] * (1 - f) + src.data[bi + ch] * f;
          const grain = (Math.random() - 0.5) * 6;
          out.data[di + ch] = Math.max(0, Math.min(255, Math.round(v + grain)));
        }
        out.data[di + 3] = 255;
      }
    }
    g.putImageData(out, 0, 0);
    healed = { box: [bx0, by0, bx1, by1], boxSize: [bx1 - bx0 + 1, by1 - by0 + 1] };

    // The residual check must NOT reuse the adaptive scan: that threshold is defined
    // by the brightest pixel, so it always finds something and every plate would be
    // refused. The honest question is "does the healed box now look like its
    // neighbours?" — max inside the box against the 95th percentile beside it.
    const boxMax = () => {
      const d2 = g.getImageData(bx0, by0, bx1 - bx0 + 1, by1 - by0 + 1).data;
      let m = 0;
      for (let i = 0; i < d2.length; i += 4) {
        const l = 0.299 * d2[i] + 0.587 * d2[i + 1] + 0.114 * d2[i + 2];
        if (l > m) m = l;
      }
      return m;
    };
    const bandMax = (ry0, ry1) => {
      if (ry1 <= ry0) return 0;
      const d3 = g.getImageData(bx0, ry0, bx1 - bx0 + 1, ry1 - ry0).data;
      const vals = [];
      for (let i = 0; i < d3.length; i += 4)
        vals.push(0.299 * d3[i] + 0.587 * d3[i + 1] + 0.114 * d3[i + 2]);
      vals.sort((a, b) => a - b);
      return vals.length ? vals[Math.floor(vals.length * 0.95)] : 0;
    };
    const band = Math.max(12, Math.round((by1 - by0) * 0.6));
    const ref = Math.max(bandMax(Math.max(0, by0 - band), Math.max(1, by0 - 1)),
                         bandMax(Math.min(h - 2, by1 + 2), Math.min(h - 1, by1 + band)));
    const cxp = g.getImageData(Math.round((bx0+bx1)/2), Math.round((by0+by1)/2), 1, 1).data;
    const bm = boxMax();
    healed.stats = { boxMax: Math.round(bm), ref: Math.round(ref),
                     center: Math.round(0.299*cxp[0] + 0.587*cxp[1] + 0.114*cxp[2]) };
    if (bm > ref + DELTA) healed.remaining = true;
  }
  document.title = 'PLATE::' + JSON.stringify({
    size: [w, h], found: !!found, healed, remaining: !!(healed && healed.remaining)
  });
  if (found) {
    const el = document.createElement('div');
    el.id = 'out';
    el.setAttribute('data-url', c.toDataURL('image/png'));
    document.body.appendChild(el);
  }
});
</script></body></html>
"""


def _chrome() -> str | None:
    from .marketing_asset_generator import _find_chrome_executable
    return _find_chrome_executable()


def clean(plate: Path, out: Path | None = None, chrome: str | None = None,
          timeout: int = 120) -> dict[str, Any]:
    """Find and heal the corner mark. Returns {status, found, removed, box, file}."""
    chrome = chrome or _chrome()
    if not chrome:
        return {"status": "ERROR", "error": "Google Chrome / Chromium not found"}
    out = out or plate.with_name(plate.stem + ".clean.png")

    page = Path(tempfile.mkdtemp()) / "plate.html"
    # A file:// image TAINTS the canvas ("cross-origin data") so getImageData and
    # toDataURL both throw; a data: URI is same-origin and stays readable.
    data_uri = "data:image/png;base64," + base64.b64encode(plate.read_bytes()).decode()
    page.write_text(_PAGE % {"src": data_uri, "lo": QUADRANT[0], "minluma": MIN_LUMA,
                             "delta": DELTA, "pad": PAD, "maxarea": MAX_AREA})
    try:
        res = subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
             "--virtual-time-budget=15000", "--dump-dom", page.resolve().as_uri()],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "error": f"plate scan timed out after {timeout}s"}
    dom = res.stdout or ""

    m = re.search(r"PLATE::(\{.*?\})</title>", dom, re.S)
    if not m:
        return {"status": "ERROR", "error": "plate scan produced no result"}
    info = json.loads(m.group(1))

    if not info["found"]:
        return {"status": "OK", "found": False, "removed": False, "file": str(plate),
                "size": info["size"], "note": "no mark detected — plate used as generated"}
    if info.get("remaining"):
        return {"status": "REJECTED", "found": True, "removed": False,
                "error": "a bright mark is still present after healing — refusing this plate",
                "box": info["healed"]["box"], "size": info["size"]}

    m2 = re.search(r'data-url="(data:image/png;base64,[^"]+)"', dom, re.S)
    if not m2:
        return {"status": "ERROR", "error": "healed plate was not returned by the browser"}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(m2.group(1).split(",", 1)[1]))
    return {"status": "OK", "found": True, "removed": True, "file": str(out),
            "box": info["healed"]["box"], "boxSize": info["healed"]["boxSize"],
            "size": info["size"]}
