"""Compare two PNGs without an image library — enough to prove that the icon a
user taps is the icon a store visitor sees.

Why this exists: Play rejected a shipped update on 2026-11-04 under the
Misleading Claims policy — "your app's installed icon or name differs from its
store listing" — while `store-readiness` passed, because it only checked that a
launcher icon and a 512 PNG *exist*. Existence is not agreement. This module is
the measurement that was missing: both files are decoded to a small greyscale
grid and compared with a difference hash (dHash), which tolerates the resampling
between a 512 store icon and a 192dp launcher bitmap but not a different mark.

Pure stdlib on purpose: the team runs on macOS and CI images with no Pillow, and
a gate that needs a package is a gate that gets skipped. PNG support is the
common subset Android's aapt and Chrome emit: bit depth 8/16, colour types
0/2/3/4/6, non-interlaced. Anything else returns None instead of guessing —
`store_readiness` reports "cannot compare", never a pass.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

_BIT_DEPTHS = {8: 1, 16: 2}
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _unfilter(raw: bytes, width: int, height: int, bpp: int, stride: int) -> list[bytes]:
    out: list[bytes] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        if pos >= len(raw):
            raise ValueError("truncated PNG data")
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if len(line) != stride:
            raise ValueError("truncated PNG row")
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        else:
            raise ValueError(f"unknown PNG filter {ftype}")
        out.append(bytes(line))
        prev = line
    return out


def decode_png(path: Path) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    """Decode to (width, height, RGBA rows flattened). Raises on unsupported input."""
    data = Path(path).read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")
    pos = 8
    width = height = depth = ctype = None
    idat = bytearray()
    palette: list[tuple[int, int, int]] = []
    trns: bytes = b""
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", body)
            if interlace:
                raise ValueError("interlaced PNG unsupported")
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]  # type: ignore[misc]
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat.extend(body)
        elif kind == b"IEND":
            break
    if width is None or ctype not in _CHANNELS or depth not in _BIT_DEPTHS:
        raise ValueError("unsupported PNG colour type/depth")
    bpp = _CHANNELS[ctype] * _BIT_DEPTHS[depth]
    stride = width * bpp
    rows = _unfilter(zlib.decompress(bytes(idat)), width, height, bpp, stride)
    out: list[tuple[int, int, int, int]] = []
    step = _BIT_DEPTHS[depth]
    for row in rows:
        vals = [row[i] for i in range(0, len(row), step)]  # high byte == value for 16-bit
        if ctype == 0:
            out.extend((v, v, v, 255) for v in vals)
        elif ctype == 2:
            out.extend((vals[i], vals[i + 1], vals[i + 2], 255)
                       for i in range(0, len(vals), 3))
        elif ctype == 3:
            for v in vals:
                r, g, b = palette[v] if v < len(palette) else (0, 0, 0)
                a = trns[v] if v < len(trns) else 255
                out.append((r, g, b, a))
        elif ctype == 4:
            out.extend((vals[i], vals[i], vals[i], vals[i + 1])
                       for i in range(0, len(vals), 2))
        else:
            out.extend((vals[i], vals[i + 1], vals[i + 2], vals[i + 3])
                       for i in range(0, len(vals), 4))
    return width, height, out


def _grid(path: Path, n: int = 9) -> list[list[int]]:
    """Downsample to an n x n grid of mean luminance, alpha composited on black."""
    w, h, px = decode_png(path)
    sums = [[0] * n for _ in range(n)]
    counts = [[0] * n for _ in range(n)]
    for y in range(h):
        gy = min(n - 1, y * n // h)
        row = y * w
        for x in range(w):
            r, g, b, a = px[row + x]
            lum = (r * 299 + g * 587 + b * 114) // 1000 * a // 255
            gx = min(n - 1, x * n // w)
            sums[gy][gx] += lum
            counts[gy][gx] += 1
    return [[sums[y][x] // counts[y][x] if counts[y][x] else 0 for x in range(n)]
            for y in range(n)]


def _otsu(lum: list[int]) -> int:
    hist = [0] * 256
    for v in lum:
        hist[v] += 1
    total = len(lum)
    sum_all = sum(i * hist[i] for i in range(256))
    sum_b = 0.0
    w_b = 0
    best = -1.0
    thr = 127
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best:
            best, thr = var, t
    return thr


def shape_mask(path: Path, n: int = 24) -> list[list[int]]:
    """The image's dominant shape: Otsu-split on luminance, majority per cell.

    A store icon and the launcher bitmap built from it are the same silhouette
    at different sizes and often different palettes (one app ships white-on-
    purple in the launcher and cyan-on-black in the listing). Comparing the
    SILHOUETTE — not the luminance — is what tolerates that, while a genuinely
    different mark (a power glyph against a shield) has nothing in common.
    """
    w, h, px = decode_png(path)
    lum = [((r * 299 + g * 587 + b * 114) // 1000) * a // 255 for r, g, b, a in px]
    thr = _otsu(lum)
    hits = [[0] * n for _ in range(n)]
    cells = [[0] * n for _ in range(n)]
    for y in range(h):
        gy = min(n - 1, y * n // h)
        row = y * w
        for x in range(w):
            gx = min(n - 1, x * n // w)
            cells[gy][gx] += 1
            if lum[row + x] > thr:
                hits[gy][gx] += 1
    return [[1 if (cells[y][x] and hits[y][x] * 2 >= cells[y][x]) else 0
             for x in range(n)] for y in range(n)]


def shape_similarity(a: Path, b: Path, n: int = 24) -> float | None:
    """Intersection-over-union of two silhouettes, 0..1 (1 = identical shape).

    Compared both as-is and inverted, because one file may draw the mark light on
    dark and the other dark on light; the better of the two is the honest answer.
    """
    try:
        ma, mb = shape_mask(a, n), shape_mask(b, n)
    except Exception:
        return None
    best = 0.0
    for invert in (False, True):
        inter = union = 0
        for y in range(n):
            for x in range(n):
                av = ma[y][x]
                bv = (1 - mb[y][x]) if invert else mb[y][x]
                if av and bv:
                    inter += 1
                if av or bv:
                    union += 1
        best = max(best, inter / union if union else 1.0)
    return best


def _dhash(path: Path, n: int = 9) -> int:
    g = _grid(path, n)
    bits = 0
    for y in range(n):
        for x in range(n - 1):
            bits = (bits << 1) | (1 if g[y][x] > g[y][x + 1] else 0)
    return bits


def distance(a: Path, b: Path, n: int = 9) -> int | None:
    """dHash Hamming distance between two images (0 = same structure).

    Returns None when either file cannot be decoded — callers must report that,
    not treat it as agreement. A 512 store icon and a 192dp launcher bitmap are
    the same art at different sizes, so a real pair lands at 0-6; a different
    mark (a power glyph against a green shield, measured live) lands far above
    the threshold `store_readiness` uses.
    """
    try:
        ha, hb = _dhash(a, n), _dhash(b, n)
    except Exception:
        return None
    return bin(ha ^ hb).count("1")
