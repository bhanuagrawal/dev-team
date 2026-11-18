"""A painted listing's freshness is judged at Play's size, not the compose canvas.

Pinned because the two paths draw different objects on purpose: a code-composed
frame is a 1620x3200 canvas (and a clamped headless window is what the canvas
equality catches), while a painted poster is an image model's 9:16 answer
resampled to the 1080x1920 Play receives. Asking a painted frame for the canvas
made every poster read as stale the moment it existed — the listing was refused
as "not the 1620x3200 frame Play gets" with nothing wrong with it (2026-11-18).
"""

import hashlib
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import storefront_frames as sfr


def _png(path: Path, width: int, height: int) -> None:
    raw = b"\x00" * (width * 3 + 1)
    body = b"".join(raw for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(body))
                     + chunk(b"IEND", b""))


def _project(composition: str, width: int, height: int) -> Path:
    project = Path(tempfile.mkdtemp())
    listing = sfr.listing_dir(project)
    listing.mkdir(parents=True)
    shot = listing / "01_hero.png"
    _png(shot, width, height)
    registry = {
        "canvas": [sfr.CANVAS_W, sfr.CANVAS_H],
        "composition": {"path": composition},
        "listing": {
            "ref": "origin/main",
            "ref_sha": "0" * 40,
            "ui_path": "composeApp/src",
            "ui_sha": "",
            "app_version": "1.0.0",
            "spec_sha": "",
            "files": [{
                "name": shot.name,
                "sha256": hashlib.sha256(shot.read_bytes()).hexdigest(),
                "width": width,
                "height": height,
            }],
        },
    }
    registry_file = project / sfr.REGISTRY_REL
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(json.dumps(registry))
    return project


class PlaySizedTests(unittest.TestCase):
    def test_play_accepts_1080x1920(self):
        self.assertTrue(sfr._play_sized(1080, 1920))

    def test_a_short_side_under_1080_is_refused(self):
        self.assertFalse(sfr._play_sized(572, 1024))

    def test_an_aspect_beyond_2_to_1_is_refused(self):
        self.assertFalse(sfr._play_sized(1080, 2400))

    def test_the_compose_canvas_is_also_a_play_size(self):
        # Both are valid for Play; only the RECIPE decides which one a frame owes.
        self.assertTrue(sfr._play_sized(sfr.CANVAS_W, sfr.CANVAS_H))


class FreshnessTests(unittest.TestCase):
    def test_a_painted_frame_at_play_size_is_current(self):
        ok, why = sfr.listing_freshness(_project("poster", 1080, 1920))
        self.assertTrue(ok, why)

    def test_a_painted_frame_below_play_size_is_refused(self):
        ok, why = sfr.listing_freshness(_project("poster", 572, 1024))
        self.assertFalse(ok)
        self.assertIn("Play", why)

    def test_a_code_composed_frame_must_be_on_the_canvas(self):
        ok, why = sfr.listing_freshness(_project("code", 1080, 1920))
        self.assertFalse(ok)
        self.assertIn(f"{sfr.CANVAS_W}x{sfr.CANVAS_H}", why)


if __name__ == "__main__":
    unittest.main()
