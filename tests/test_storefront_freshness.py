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


class SourceProvenanceTests(unittest.TestCase):
    """A test file is not a screen: moving it must not stale the listing.

    Android and KMP keep `androidUnitTest`/`commonTest` inside the same `src/`
    root as the screens, so a fingerprint over `composeApp/src` moved on a commit
    that drew nothing — and `store-readiness` then refused the listing as showing
    "screens an older commit drew" (observed on Paddle, 2026-11-21).
    """

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.env = {
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        }
        self._git("init", "-b", "main")
        (self.repo / "composeApp/src/main/kotlin").mkdir(parents=True)
        (self.repo / "composeApp/src/androidUnitTest/kotlin").mkdir(parents=True)
        (self.repo / "composeApp/build.gradle.kts").write_text(
            'android {\n    defaultConfig {\n        versionName = "1.0.0"\n    }\n}\n')
        (self.repo / "composeApp/src/main/kotlin/Home.kt").write_text(
            "@Composable fun Home() { Text(\"hi\") }\n")
        (self.repo / "composeApp/src/androidUnitTest/kotlin/HomeTest.kt").write_text(
            "class HomeTest { @Test fun x() {} }\n")
        shot = sfr.listing_dir(self.repo)
        shot.mkdir(parents=True)
        _png(shot / "01_hero.png", sfr.CANVAS_W, sfr.CANVAS_H)
        self._commit()
        self._git("update-ref", "refs/remotes/origin/main", "HEAD")
        sfr.record_listing(self.repo)

    def _git(self, *args: str) -> str:
        import os
        import subprocess

        out = subprocess.run(["git", "-C", str(self.repo), *args],
                             capture_output=True, text=True,
                             env={**os.environ, **self.env})
        if out.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {out.stderr}")
        return out.stdout.strip()

    def _commit(self) -> None:
        self._git("add", "-A")
        self._git("commit", "-m", "snapshot", "-q")

    def test_a_test_only_commit_does_not_stale_the_listing(self):
        (self.repo / "composeApp/src/androidUnitTest/kotlin/HomeTest.kt").write_text(
            "class HomeTest { @Test fun y() {} }\n")
        self._commit()
        self._git("update-ref", "refs/remotes/origin/main", "HEAD")
        ok, why = sfr.listing_freshness(self.repo)
        self.assertTrue(ok, why)

    def test_a_shipped_source_commit_still_stales_the_listing(self):
        (self.repo / "composeApp/src/main/kotlin/Home.kt").write_text(
            "@Composable fun Home() { Text(\"bye\") }\n")
        self._commit()
        self._git("update-ref", "refs/remotes/origin/main", "HEAD")
        ok, why = sfr.listing_freshness(self.repo)
        self.assertFalse(ok)
        self.assertIn("moved since the frames were drawn", why)


if __name__ == "__main__":
    unittest.main()
