"""The asset prompt must be per app, and the painter order must be ChatGPT-first.

Pinned because both were team-wide constants: one hardcoded composition meant
every app's listing was the same poster with different words (owner, 2026-11-17),
and the provider list asked Gemini first even after the owner asked for ChatGPT
as the first priority. Neither is a matter of taste — one is a brand decision
that belongs to the project's direction file, the other is the owner's word.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import storefront_direction as sd
from team import storefront_poster as sp


def _project(title: str = "App One") -> Path:
    project = Path(tempfile.mkdtemp())
    listing = project / "distribution/listing/en-US"
    listing.mkdir(parents=True)
    (listing / "title.txt").write_text(title)
    return project


class ProviderOrderTests(unittest.TestCase):
    def test_chatgpt_is_asked_first(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DEVTEAM_IMAGE_PROVIDERS", None)
            self.assertEqual(sp._providers(), ["chatgpt", "gemini"])

    def test_env_override_is_honoured(self):
        with mock.patch.dict(os.environ, {"DEVTEAM_IMAGE_PROVIDERS": "gemini"}):
            self.assertEqual(sp._providers(), ["gemini"])

    def test_unknown_providers_are_dropped_not_guessed(self):
        with mock.patch.dict(os.environ, {"DEVTEAM_IMAGE_PROVIDERS": "midjourney,gemini"}):
            self.assertEqual(sp._providers(), ["gemini"])


class DeviceCaptureTests(unittest.TestCase):
    @staticmethod
    def _png(path: Path, width: int, height: int) -> None:
        import struct
        import zlib

        raw = b"\x00" * (width * 3 + 1)
        body = b"".join(raw for _ in range(height))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        path.write_bytes(b"\x89PNG\r\n\x1a\n"
                         + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                         + chunk(b"IDAT", zlib.compress(body))
                         + chunk(b"IEND", b""))

    def test_captures_become_the_scene_renders_with_their_own_provenance(self):
        from team import storefront_preview as sp

        project = Path(tempfile.mkdtemp())
        shots = project / "shots"
        shots.mkdir()
        self._png(shots / "01_hero.png", 1080, 2270)
        scenes = [{"id": "01_hero"}]
        made = sp.adopt_device_screens(project, scenes, shots)
        self.assertEqual(made["status"], "OK")
        self.assertEqual(made["provenance"], "device_capture")
        self.assertTrue((project / sp.SCENES_DIR_REL / "01_hero.png").is_file())

    def test_a_missing_capture_is_refused_not_substituted(self):
        from team import storefront_preview as sp

        project = Path(tempfile.mkdtemp())
        shots = project / "shots"
        shots.mkdir()
        made = sp.adopt_device_screens(project, [{"id": "01_hero"}], shots)
        self.assertEqual(made["status"], "BLOCKED")
        self.assertIn("01_hero", made["error"])


class CompositionTests(unittest.TestCase):
    scene = {"id": "01_hero", "badge": "Near You", "headline": "Books within reach",
             "sub": "Real copies on real shelves near you."}

    def test_the_apps_own_composition_is_quoted(self):
        direction = {"composition": "The device stands upright on the left over warm paper; "
                                   "the copy runs down the right margin as a single column."}
        prompt = sp.poster_prompt(_project(), self.scene, direction)
        self.assertIn("The device stands upright on the left", prompt)
        self.assertNotIn("anchored to the right edge", prompt)

    def test_two_apps_get_two_recipes(self):
        a = sp.poster_prompt(_project("App A"), self.scene,
                             {"composition": "Phone on the right, copy upper-left, cream field."})
        b = sp.poster_prompt(_project("App B"), self.scene,
                             {"composition": "Phone centred low, headline across the top, deep green."})
        self.assertIn("Phone on the right, copy upper-left, cream field.", a)
        self.assertIn("Phone centred low, headline across the top, deep green.", b)
        self.assertNotIn("Phone centred low", a)
        self.assertNotIn("Phone on the right", b)

    def test_corner_invariant_survives_any_recipe(self):
        prompt = sp.poster_prompt(_project(), self.scene,
                                  {"composition": "Full-bleed photograph, device centred."})
        self.assertIn("last 14% of the canvas width", prompt)

    def test_direction_without_a_recipe_is_refused(self):
        spec = {"scenes": [], "palette": {}}
        direction = {"version": sd.VERSION, "spec_sha": sd.spec_fingerprint(spec),
                     "locked_by": "owner", "concept": "one idea", "copy_angle": {},
                     "palette": {"emphasis": "the badge"},
                     "avoid": ["a paywall"],
                     "evidence": [{"source": "s", "note": "n", "date": "d"}],
                     "composition": ""}
        self.assertTrue(any("composition is empty" in issue
                            for issue in sd.validate(direction, spec)))
        direction["composition"] = "Phone low and rotated; headline above it; paper field."
        self.assertFalse(any("composition" in issue
                             for issue in sd.validate(direction, spec)))


if __name__ == "__main__":
    unittest.main()
