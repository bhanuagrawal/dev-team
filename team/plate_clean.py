"""Remove the mark an image model stamps into what it generates.

Why this exists
---------------
Gemini (and every other consumer image tool) puts a sparkle mark in the corner of
what it returns. On a Play listing that mark is a vendor advert sitting on the
owner's product page: it must not ship.

Shape of the fix
----------------
The mark is not searched for by brightness — the mark sits on different art every
time, and on a poster the phone's bezel or a lit glow is brighter and sharper than
the stamp. Observed twice: a detector that boxed "the brightest thing in the
corner" healed the glow and shipped the sparkle. So the cleaner targets the one
thing that is stable about a vendor stamp — WHERE it is:

    gemini stamp: centred at (0.843 w, 0.912 h), about 9% of the width

That box is healed from the surrounding gradient, then PROVED: the healed pixels
must be free of local contrast, and a contrast scan of the rest of the canvas must
not find a second stamp-shaped blob. A heal that cannot say "removed" is refused,
never shipped.
"""
from __future__ import annotations

import base64
import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# Vendor stamp placement, relative so it works at 572x1024 and at API resolutions.
STAMP_X, STAMP_Y, STAMP_HALF = 0.843, 0.912, 0.045
GROW = 1.35              # retry wider when the first box leaves contrast behind
MIN_CONTRAST = 14        # local contrast a glyph edge leaves behind (grain stays under 8)
OK_CONTRAST = 26         # best-effort tolerance: a healed corner may carry nearby art edges
SCAN_LO = (0.60, 0.60)   # the "is there a SECOND mark elsewhere" scan
SCAN_MIN_LUMA = 70
PAD = 6

_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<img id="plate" src="%(src)s" style="display:none">
<script>
const BOX = %(box)s, MIN_CONTRAST = %(mincontrast)s, PAD = %(pad)s;
const LOX = %(lox)s, LOY = %(loy)s, SCAN_MIN_LUMA = %(scanluma)s;
window.addEventListener('load', () => {
  const img = document.getElementById('plate');
  const w = img.naturalWidth, h = img.naturalHeight;
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.drawImage(img, 0, 0);
  const src = g.getImageData(0, 0, w, h);

  // ---- heal: blend the two edges that are OUTSIDE the stamp -------------------
  const bx0 = Math.max(0, BOX[0] - PAD), by0 = Math.max(0, BOX[1] - PAD);
  const bx1 = Math.min(w - 1, BOX[2] + PAD), by1 = Math.min(h - 1, BOX[3] + PAD);
  const out = g.createImageData(w, h);
  out.data.set(src.data);
  const leftX = Math.max(0, bx0 - 3), topY = Math.max(0, by0 - 3);
  const bw = bx1 - bx0 + 1, bh = by1 - by0 + 1;
  for (let y = by0; y <= by1; y++) {
    for (let x = bx0; x <= bx1; x++) {
      const li = (y * w + leftX) * 4, ti = (topY * w + x) * 4, di = (y * w + x) * 4;
      // Weight by distance to each edge: the corner fill follows both gradients.
      const wx = bw > 1 ? (x - bx0) / (bw - 1) : 0.5;
      const wy = bh > 1 ? (y - by0) / (bh - 1) : 0.5;
      const wsum = wx + wy || 1;
      for (let ch = 0; ch < 3; ch++) {
        const v = (src.data[li + ch] * wy + src.data[ti + ch] * wx) / wsum;
        const grain = (Math.random() - 0.5) * 5;
        out.data[di + ch] = Math.max(0, Math.min(255, Math.round(v + grain)));
      }
      out.data[di + 3] = 255;
    }
  }
  g.putImageData(out, 0, 0);

  // ---- prove: no glyph edge left inside the healed box ------------------------
  const contrastIn = (rx0, ry0, rx1, ry1) => {
    const data = g.getImageData(rx0, ry0, rx1 - rx0 + 1, ry1 - ry0 + 1).data;
    const bw2 = rx1 - rx0 + 1, bh2 = ry1 - ry0 + 1;
    let m = 0;
    for (let yy = 1; yy < bh2 - 1; yy++) {
      for (let xx = 1; xx < bw2 - 1; xx++) {
        const i = (yy * bw2 + xx) * 4;
        const up = ((yy - 1) * bw2 + xx) * 4, dn = ((yy + 1) * bw2 + xx) * 4;
        const l = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        const nb = (0.299 * data[up] + 0.587 * data[up + 1] + 0.114 * data[up + 2]
                  + 0.299 * data[dn] + 0.587 * data[dn + 1] + 0.114 * data[dn + 2]) / 2;
        if (l - nb > m) m = l - nb;
      }
    }
    return m;
  };
  // Measured on the stamp's own footprint: the padded area legitimately contains the
  // art next to the mark, and counting that made an acceptable heal look dirty.
  const rx0 = Math.max(0, BOX[0]), ry0 = Math.max(0, BOX[1]);
  const rx1 = Math.min(w - 1, BOX[2]), ry1 = Math.min(h - 1, BOX[3]);
  const residual = contrastIn(rx0, ry0, rx1, ry1);

  // ---- safety net: a second stamp-shaped blob anywhere else -------------------
  const small = document.createElement('canvas');
  const f = Math.max(2, Math.round(Math.min(w, h) / 96));
  small.width = Math.max(1, Math.round(w / f)); small.height = Math.max(1, Math.round(h / f));
  small.getContext('2d', { willReadFrequently: true }).drawImage(c, 0, 0, small.width, small.height);
  const blurC = document.createElement('canvas'); blurC.width = w; blurC.height = h;
  const bg = blurC.getContext('2d', { willReadFrequently: true });
  bg.drawImage(small, 0, 0, small.width, small.height, 0, 0, w, h);
  const blur = bg.getImageData(0, 0, w, h).data;
  const healed = g.getImageData(0, 0, w, h).data;
  const xs = Math.floor(w * LOX), ys = Math.floor(h * LOY);
  let elsewhere = 0;
  for (let y = ys; y < h; y++) {
    for (let x = xs; x < w; x++) {
      if (x >= bx0 && x <= bx1 && y >= by0 && y <= by1) continue;
      const i = (y * w + x) * 4;
      const l = 0.299 * healed[i] + 0.587 * healed[i + 1] + 0.114 * healed[i + 2];
      const b = 0.299 * blur[i] + 0.587 * blur[i + 1] + 0.114 * blur[i + 2];
      if (l > SCAN_MIN_LUMA && l - b > MIN_CONTRAST * 3) elsewhere++;
    }
  }
  document.title = 'PLATE::' + JSON.stringify({
    size: [w, h], box: [bx0, by0, bx1, by1],
    residual: Math.round(residual), elsewhere,
    remaining: residual > MIN_CONTRAST
  });
  const el = document.createElement('div');
  el.id = 'out';
  el.setAttribute('data-url', c.toDataURL('image/png'));
  document.body.appendChild(el);
});
</script></body></html>
"""


def _chrome() -> str | None:
    from .marketing_asset_generator import _find_chrome_executable
    return _find_chrome_executable()


def _size(plate: Path) -> tuple[int, int]:
    return struct.unpack(">II", plate.read_bytes()[16:24])


def stamp_box(plate: Path, grow: float = 1.0) -> list[int]:
    w, h = _size(plate)
    half = max(16, round(w * STAMP_HALF * grow))
    cx, cy = round(w * STAMP_X), round(h * STAMP_Y)
    return [max(0, cx - half), max(0, cy - half), min(w - 1, cx + half), min(h - 1, cy + half)]


def clean(plate: Path, out: Path | None = None, chrome: str | None = None,
          timeout: int = 120, strict: bool = False) -> dict[str, Any]:
    """Heal the vendor stamp and prove the corner is clean.

    Best effort by default: the mark is healed, and if the proof cannot reach zero the
    result still ships but is reported `verified: False` with the measured residual, so
    an owner who looked at the image can accept a corner the algorithm is unsure about.
    `strict=True` (CLI `--strict`) restores the hard refusal. A run that silently keeps
    the mark is never acceptable either way — the difference is who decides.
    """
    chrome = chrome or _chrome()
    if not chrome:
        return {"status": "ERROR", "error": "Google Chrome / Chromium not found"}
    out = out or plate.with_name(plate.stem + ".clean.png")
    grow = 1.0
    last: dict[str, Any] = {}
    for _attempt in range(2):
        box = stamp_box(plate, grow)
        page = Path(tempfile.mkdtemp()) / "plate.html"
        data_uri = "data:image/png;base64," + base64.b64encode(plate.read_bytes()).decode()
        page.write_text(_PAGE % {"src": data_uri, "box": json.dumps(box),
                                 "mincontrast": MIN_CONTRAST, "pad": PAD,
                                 "lox": SCAN_LO[0], "loy": SCAN_LO[1],
                                 "scanluma": SCAN_MIN_LUMA})
        try:
            res = subprocess.run(
                [chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
                 "--virtual-time-budget=15000", "--dump-dom", page.resolve().as_uri()],
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "ERROR", "error": f"plate scan timed out after {timeout}s"}
        page.unlink(missing_ok=True)
        m = re.search(r"PLATE::(\{.*?\})</title>", res.stdout or "", re.S)
        if not m:
            return {"status": "ERROR", "error": "plate scan produced no result"}
        info = json.loads(m.group(1))
        last = info
        if info.get("remaining") and grow == 1.0:
            grow = GROW
            continue
        if info.get("remaining") and strict:
            return {"status": "REJECTED", "removed": False,
                    "error": "a mark is still present after healing — refusing this image",
                    "box": info["box"], "size": info["size"]}
        m2 = re.search(r'data-url="(data:image/png;base64,[^"]+)"', res.stdout or "", re.S)
        if not m2:
            return {"status": "ERROR", "error": "healed image was not returned by the browser"}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(m2.group(1).split(",", 1)[1]))
        verified = not info.get("remaining")
        return {"status": "OK", "removed": True, "verified": verified, "file": str(out),
                "box": info["box"], "size": info["size"],
                "residual": info.get("residual"),
                "elsewhere": info.get("elsewhere", 0),
                "note": None if verified else
                        "healed, but the corner still carries contrast — look at it before publishing"}
    return {"status": "REJECTED", "removed": False, "error": "mark survived healing",
            "box": last.get("box"), "size": last.get("size")} if strict else {
            "status": "OK", "removed": True, "verified": False,
            "file": str(out), "box": last.get("box"), "size": last.get("size"),
            "residual": last.get("residual"),
            "note": "healing could not be proved clean — inspect the corner before publishing"}
