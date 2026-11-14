"""A Stitch call that answers with DOM operations must fail FAST, with the remedy.

The run that found this (2026-11-14, a design tweak): `edit_screens` applied the
change live and answered with DOM operations instead of a screen artifact. The
code polled 120s, raised its "there is nothing to export — write the HTML back
yourself" error from inside its own try-block, and that error was then caught by
the same block's `except StitchError`, which polled for the full 900s budget and
failed with the transport wording instead. The tweak command was killed at 15
minutes twice while the real next step (write the reviewed HTML back with
`update_screen_content`, then `stitch-export`) was already known.
"""
import sys
import time
import unittest
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TEAM_ROOT))

from team import stitch_mcp  # noqa: E402


DOM_ONLY = {
    "outputComponents": [
        {
            "sessionEvent": {
                "eventPayload": {
                    "dom_operations": [{"op": "update", "target": "#cta"}],
                }
            }
        }
    ]
}


class StitchDomOperationsFailFastTest(unittest.TestCase):

    def setUp(self):
        self._call = stitch_mcp.call
        self._list = stitch_mcp.list_screens
        self._poll = stitch_mcp._poll_changed_screens
        stitch_mcp.call = lambda *a, **k: DOM_ONLY
        stitch_mcp.list_screens = lambda *a, **k: []
        # No real waiting and no second 900s budget: the poll must be asked for a
        # SHORT window and must be the only one.
        self.polled = []

        def fake_poll(pid, before, timeout=900, interval=20):
            self.polled.append(timeout)
            return []

        stitch_mcp._poll_changed_screens = fake_poll

    def tearDown(self):
        stitch_mcp.call = self._call
        stitch_mcp.list_screens = self._list
        stitch_mcp._poll_changed_screens = self._poll

    def test_edit_screens_names_the_write_back_route_and_stops(self):
        started = time.time()
        with self.assertRaises(stitch_mcp.NoExportableArtifact) as caught:
            stitch_mcp.edit_screens("11111", ["aaa"], "change the CTA")
        elapsed = time.time() - started
        message = str(caught.exception)
        self.assertIn("update_screen_content", message)
        self.assertIn("stitch-export", message)
        self.assertLess(elapsed, 5, "a DOM-only answer must not be polled twice")
        self.assertEqual(
            len(self.polled), 1,
            f"expected ONE short poll, got {self.polled} — the second is the 900s budget",
        )
        self.assertLessEqual(self.polled[0], 120)

    def test_generate_variants_names_the_write_back_route_and_stops(self):
        with self.assertRaises(stitch_mcp.NoExportableArtifact) as caught:
            stitch_mcp.generate_variants("11111", ["aaa"], "change the CTA")
        self.assertIn("update_screen_content", str(caught.exception))
        self.assertEqual(len(self.polled), 1, f"polls: {self.polled}")

    def test_the_fast_error_is_still_a_stitch_error(self):
        # Callers catch StitchError to print "STITCH TWEAK FAILED: <remedy>".
        self.assertTrue(issubclass(stitch_mcp.NoExportableArtifact, stitch_mcp.StitchError))


if __name__ == "__main__":
    unittest.main()
