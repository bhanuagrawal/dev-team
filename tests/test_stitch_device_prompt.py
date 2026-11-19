"""The Stitch prompt describes the device the caller actually asked for.

It used to say "Mobile (390px viewport width, portrait)" on every call, so
`stitch-screen --device DESKTOP` asked Stitch for a desktop screen in one
argument and described a phone in the prompt. The model follows the prompt:
BookSwap's 2026-11-19 web set came back as a stretched phone until this was
fixed.
"""

import sys
import unittest
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TEAM_ROOT))

from team.design import _device_line  # noqa: E402


class StitchDevicePromptTests(unittest.TestCase):
    def test_desktop_asks_for_a_desktop(self) -> None:
        line = _device_line("DESKTOP")
        self.assertIn("Desktop", line)
        self.assertIn("1440", line)
        self.assertNotIn("390", line)

    def test_tablet_asks_for_a_tablet(self) -> None:
        line = _device_line("TABLET")
        self.assertIn("Tablet", line)
        self.assertIn("1024", line)

    def test_mobile_and_unknown_default_to_the_phone(self) -> None:
        for device in ("MOBILE", "", None, "watch"):
            with self.subTest(device=device):
                self.assertIn("Mobile", _device_line(device))

    def test_case_and_padding_do_not_matter(self) -> None:
        self.assertEqual(_device_line(" desktop "), _device_line("DESKTOP"))


if __name__ == "__main__":
    unittest.main()
