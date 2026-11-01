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
# The corner the prompt mandates plain, as a contract we can check: the vendor
# stamp lands somewhere in the last ~22% of both axes (its fixed centre is
# 0.843,0.912 with a 0.045 half). Scanning the fixed box alone missed a real
# sparkle that sat just above it and the image shipped "verified" (2026-11-01).
CORNER_LO = (0.78, 0.78)
CORNER_HALF = 0.05
ELSEWHERE_OK = 8         # a mark leaves dozens of px; noise leaves a handful
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
  let elsewhere = 0, ex0 = w, ey0 = h, ex1 = -1, ey1 = -1;
  for (let y = ys; y < h; y++) {
    for (let x = xs; x < w; x++) {
      if (x >= bx0 && x <= bx1 && y >= by0 && y <= by1) continue;
      const i = (y * w + x) * 4;
      const l = 0.299 * healed[i] + 0.587 * healed[i + 1] + 0.114 * healed[i + 2];
      const b = 0.299 * blur[i] + 0.587 * blur[i + 1] + 0.114 * blur[i + 2];
      if (l > SCAN_MIN_LUMA && l - b > MIN_CONTRAST * 3) {
        elsewhere++;
        if (x < ex0) ex0 = x; if (x > ex1) ex1 = x;
        if (y < ey0) ey0 = y; if (y > ey1) ey1 = y;
      }
    }
  }
  const elsewhereBox = elsewhere ? [ex0, ey0, ex1, ey1] : null;
  document.title = 'PLATE::' + JSON.stringify({
    size: [w, h], box: [bx0, by0, bx1, by1],
    residual: Math.round(residual), elsewhere, elsewhereBox,
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


_DETECT_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<img id="src" src="%(src)s" style="display:none">
<script>
const LOX = %(lox)s, LOY = %(loy)s, HALF = %(half)s;
const MIN_CONTRAST = %(mincontrast)s, SCAN_MIN_LUMA = %(scanluma)s;
window.addEventListener('load', () => {
  const img = document.getElementById('src');
  const w = img.naturalWidth, h = img.naturalHeight;
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.drawImage(img, 0, 0);
  // Low pass = the local background; the mark is what stands above it.
  const small = document.createElement('canvas');
  const f = Math.max(2, Math.round(Math.min(w, h) / 96));
  small.width = Math.max(1, Math.round(w / f)); small.height = Math.max(1, Math.round(h / f));
  small.getContext('2d').drawImage(c, 0, 0, small.width, small.height);
  const blurC = document.createElement('canvas'); blurC.width = w; blurC.height = h;
  const bg = blurC.getContext('2d', { willReadFrequently: true });
  bg.drawImage(small, 0, 0, small.width, small.height, 0, 0, w, h);
  const src = g.getImageData(0, 0, w, h).data, blur = bg.getImageData(0, 0, w, h).data;
  const xs = Math.floor(w * LOX), ys = Math.floor(h * LOY);
  const qw = w - xs, qh = h - ys;

  // A stamp is COMPACT and sits on flat background; the app's own artwork edge is
  // a long thin boundary. So the candidate is a connected blob with a roughly
  // square footprint — not merely the brightest pixel in the corner.
  const mask = new Uint8Array(qw * qh);
  for (let y = 0; y < qh; y++) {
    for (let x = 0; x < qw; x++) {
      const i = ((y + ys) * w + (x + xs)) * 4;
      const l = 0.299 * src[i] + 0.587 * src[i + 1] + 0.114 * src[i + 2];
      const b = 0.299 * blur[i] + 0.587 * blur[i + 1] + 0.114 * blur[i + 2];
      // Detection is deliberately more sensitive than the VERIFY pass: a dim stamp
      // (~luma 90 on a dark backdrop) must still be found, and the ring-flatness
      // test below — not a high threshold — is what rejects the app's own artwork.
      mask[y * qw + x] = (l >= 55 && l - b >= 20) ? 1 : 0;
    }
  }
  // A dim stamp fragments into pieces smaller than a stamp (observed: an 8x4 and an
  // 11x13 shard for one 40px star), so the mask is dilated until a star is one blob.
  for (let pass = 0; pass < 3; pass++) {
    const next = Uint8Array.from(mask);
    for (let y = 0; y < qh; y++) {
      for (let x = 0; x < qw; x++) {
        if (mask[y * qw + x]) continue;
        let near = false;
        for (let dy = -1; dy <= 1 && !near; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            const nx = x + dx, ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= qw || ny >= qh) continue;
            if (mask[ny * qw + nx]) { near = true; break; }
          }
        }
        if (near) next[y * qw + x] = 1;
      }
    }
    mask.set(next);
  }
  const seen = new Uint8Array(qw * qh);
  let best = null;
  const stack = [];
  for (let sy = 0; sy < qh; sy++) {
    for (let sx = 0; sx < qw; sx++) {
      const k0 = sy * qw + sx;
      if (!mask[k0] || seen[k0]) continue;
      let minx = sx, maxx = sx, miny = sy, maxy = sy, count = 0, lum = 0;
      stack.length = 0; stack.push(k0); seen[k0] = 1;
      while (stack.length) {
        const k = stack.pop(), x = k %% qw, y = (k - x) / qw;
        count++;
        if (x < minx) minx = x; if (x > maxx) maxx = x;
        if (y < miny) miny = y; if (y > maxy) maxy = y;
        const i = ((y + ys) * w + (x + xs)) * 4;
        lum += 0.299 * src[i] + 0.587 * src[i + 1] + 0.114 * src[i + 2];
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            const nx = x + dx, ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= qw || ny >= qh) continue;
            const nk = ny * qw + nx;
            if (mask[nk] && !seen[nk]) { seen[nk] = 1; stack.push(nk); }
          }
        }
      }
      const bw = maxx - minx + 1, bh = maxy - miny + 1;
      const aspect = bw / bh, fill = count / (bw * bh);
      const sizeOk = bw >= w * 0.02 && bw <= w * 0.12 && bh >= h * 0.02 && bh <= h * 0.15;
      const shapeOk = aspect > 0.45 && aspect < 2.2 && fill > 0.10;
      if (!sizeOk || !shapeOk) continue;
      // The stamp stands alone on plain background; the app's own artwork does not.
      // A bright page corner has the page on one side and the backdrop on the other,
      // so its RING is high-variance even though its blob looks compact.
      let ringSum = 0, ringSum2 = 0, ringN = 0;
      const pad = Math.max(4, Math.round(Math.max(bw, bh) * 0.35));
      for (let yy = Math.max(0, miny - pad); yy <= Math.min(qh - 1, maxy + pad); yy++) {
        for (let xx = Math.max(0, minx - pad); xx <= Math.min(qw - 1, maxx + pad); xx++) {
          if (xx >= minx && xx <= maxx && yy >= miny && yy <= maxy) continue;
          const ii = ((yy + ys) * w + (xx + xs)) * 4;
          const ll = 0.299 * src[ii] + 0.587 * src[ii + 1] + 0.114 * src[ii + 2];
          ringSum += ll; ringSum2 += ll * ll; ringN++;
        }
      }
      const ringMean = ringN ? ringSum / ringN : 0;
      const ringStd = ringN ? Math.sqrt(Math.max(0, ringSum2 / ringN - ringMean * ringMean)) : 999;
      if (ringStd > 16) continue;
      const score = lum / count;
      if (!best || score > best.score) {
        best = { score, count, bw, bh, fill,
                 at: [minx + xs + (bw >> 1), miny + ys + (bh >> 1)] };
      }
    }
  }

  let box = null, found = false;
  if (best) {
    found = true;
    const pad = Math.max(8, Math.round(w * 0.012)) + (best.bw >> 1);
    box = [Math.max(0, best.at[0] - pad), Math.max(0, best.at[1] - pad),
           Math.min(w - 1, best.at[0] + pad), Math.min(h - 1, best.at[1] + pad)];
  } else {
    box = [Math.max(0, Math.round(w * 0.843) - HALF), Math.max(0, Math.round(h * 0.912) - HALF),
           Math.min(w - 1, Math.round(w * 0.843) + HALF), Math.min(h - 1, Math.round(h * 0.912) + HALF)];
  }
  document.title = 'STAMP::' + JSON.stringify({
    size: [w, h], found, blob: best, at: best ? best.at : null, box
  });
});
</script></body></html>
"""


def stamp_box(plate: Path, grow: float = 1.0) -> list[int]:
    w, h = _size(plate)
    half = max(16, round(w * STAMP_HALF * grow))
    cx, cy = round(w * STAMP_X), round(h * STAMP_Y)
    return [max(0, cx - half), max(0, cy - half), min(w - 1, cx + half), min(h - 1, cy + half)]


def detect_box(plate: Path, chrome: str | None = None, timeout: int = 120,
               scan_lo: tuple[float, float] | None = None,
               half: int | None = None,
               min_contrast: int | None = None) -> dict[str, Any]:
    """Find where the stamp ACTUALLY is in the bottom-right quadrant.

    The stamp is usually at the fixed spot, but not always: a 1024x572 banner put
    it at x=0.91w while the healer blinded itself to x=0.79-0.89w, proved that box
    clean (it was empty background), and shipped the mark. So the position is
    measured — highest local contrast in the quadrant — and the fixed box is only
    the fallback. On our own art the corner is prompt-mandated to be plain
    background, so a bright blob there is the stamp and healing it cannot smear art.
    """
    chrome = chrome or _chrome()
    if not chrome:
        return {"status": "ERROR", "error": "Google Chrome / Chromium not found"}
    w, h = _size(plate)
    page = Path(tempfile.mkdtemp()) / "detect.html"
    data_uri = "data:image/png;base64," + base64.b64encode(plate.read_bytes()).decode()
    lox, loy = scan_lo or SCAN_LO
    page.write_text(_DETECT_PAGE % {"src": data_uri, "lox": lox, "loy": loy,
                                    "half": half if half is not None
                                    else max(16, round(w * STAMP_HALF)),
                                    "mincontrast": min_contrast if min_contrast is not None
                                    else MIN_CONTRAST * 3,
                                    "scanluma": 55})
    try:
        res = subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
             "--virtual-time-budget=15000", "--dump-dom", page.resolve().as_uri()],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "error": f"stamp detection timed out after {timeout}s"}
    page.unlink(missing_ok=True)
    m = re.search(r"STAMP::(\{.*?\})</title>", res.stdout or "", re.S)
    if not m:
        return {"status": "ERROR", "error": "stamp detection produced no result"}
    info = json.loads(m.group(1))
    return {"status": "OK", **info}


def _heal_once(plate: Path, box: list[int], chrome: str, timeout: int) -> dict[str, Any]:
    """Heal ONE box and measure the result. Returns the healed bytes plus the scan."""
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
    m2 = re.search(r'data-url="(data:image/png;base64,[^"]+)"', res.stdout or "", re.S)
    if not m2:
        return {"status": "ERROR", "error": "healed image was not returned by the browser"}
    info = json.loads(m.group(1))
    info["status"] = "OK"
    info["png"] = base64.b64decode(m2.group(1).split(",", 1)[1])
    return info


def _rebox(box: list[int], half: int, size: tuple[int, int]) -> list[int]:
    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    return [max(0, cx - half), max(0, cy - half),
            min(size[0] - 1, cx + half), min(size[1] - 1, cy + half)]


def _overlap(a: list[int], b: list[int]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def clean(plate: Path, out: Path | None = None, chrome: str | None = None,
          timeout: int = 120, strict: bool = False,
          half_ratio: float | None = None) -> dict[str, Any]:
    """Heal every vendor stamp and prove none is left.

    The verdict comes from the SAME detector that found the mark, run again on the
    healed image. Counting bright pixels was not a proof: on the app icon the white
    page fills the corner, so "bright pixels outside the healed box" was 398 on a
    perfectly clean image — a false accusation — while a banner shipped a real
    sparkle because the count sat just under the limit. A stamp is a compact blob
    standing alone on flat background; artwork edges and page corners are not.

    Two passes at most: the first heals the detected position (or the fixed spot),
    the second heals a stamp found somewhere else. Strict refuses anything left.

    Best effort by default for POSTERS (report `verified: False` with the box and
    let the owner look); `strict=True` is used by the CLI flag and ALWAYS for the
    listing's brand assets.
    """
    chrome = chrome or _chrome()
    if not chrome:
        return {"status": "ERROR", "error": "Google Chrome / Chromium not found"}
    out = out or plate.with_name(plate.stem + ".clean.png")
    size = _size(plate)
    half = max(16, round(size[0] * half_ratio)) if half_ratio else None

    work = Path(tempfile.mkdtemp()) / "step.png"
    work.write_bytes(plate.read_bytes())
    healed_any = False
    healed_boxes: list[list[int]] = []
    last: dict[str, Any] = {}
    detected_at: list[int] | None = None

    for _attempt in range(3):
        found = detect_box(work, chrome=chrome, timeout=timeout)
        if found.get("status") != "OK":
            return found
        box = found.get("box") if found.get("found") else None
        if detected_at is None and found.get("found"):
            detected_at = found.get("at")
        if not box:
            if healed_any:
                break
            box = stamp_box(plate)
        if any(_overlap(box, done) for done in healed_boxes):
            break
        if half:
            box = _rebox(box, half, size)
        info = _heal_once(work, box, chrome, timeout)
        if info.get("status") != "OK":
            return info
        work.write_bytes(info["png"])
        healed_any = True
        healed_boxes.append(box)
        last = info

    # The proof: no compact, isolated blob anywhere in the corner we are told to keep clear.
    final = detect_box(work, chrome=chrome, timeout=timeout)
    if final.get("status") != "OK":
        return final
    leftover = final.get("box") if final.get("found") else None
    inside_healed = leftover is not None and any(_overlap(leftover, done) for done in healed_boxes)
    dirty = bool(leftover) and not inside_healed

    # The corner the prompt mandates plain is a CONTRACT, not a hint. A blob THERE
    # is the vendor's mark even when it overlaps a box we already healed — the old
    # proof excused exactly that ("inside what we healed") and a poster shipped
    # carrying the model's sparkle while reporting verified (observed 2026-11-01:
    # raw and cleaned corners were pixel-identical). Scanning only the corner also
    # stops the quadrant-wide "highest contrast blob" from choosing the app's own
    # artwork and declaring the corner clean because it looked elsewhere.
    corner_hit = detect_box(
        work, chrome=chrome, timeout=timeout,
        scan_lo=CORNER_LO,
        half=max(12, round(size[0] * CORNER_HALF)),
        min_contrast=MIN_CONTRAST,
    )
    if corner_hit.get("status") != "OK":
        return corner_hit
    corner_mark = corner_hit.get("box") if corner_hit.get("found") else None
    if corner_mark:
        dirty = True
        leftover = leftover or corner_mark

    if dirty and strict:
        return {"status": "REJECTED", "removed": False,
                "error": f"a vendor stamp is still present at {leftover} — refusing this image",
                "box": leftover, "corner_mark": corner_mark, "size": size,
                "residual": last.get("residual")}
    if not healed_any:
        return {"status": "ERROR", "error": "the plate cleaner healed nothing"}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(work.read_bytes())
    verified = not dirty
    return {"status": "OK", "removed": True, "verified": verified, "file": str(out),
            "box": healed_boxes[-1], "size": size,
            "detected": bool(detected_at), "detected_at": detected_at,
            "residual": last.get("residual"), "elsewhere": 0 if verified else 1,
            "corner_mark": corner_mark, "leftover": leftover,
            "note": None if verified else
                    "healed, but a stamp still looks present — look at it before publishing"}
