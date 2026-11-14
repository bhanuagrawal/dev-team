import sys
import unittest
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TEAM_ROOT))

import cli  # noqa: E402


class RetiredIdsTest(unittest.TestCase):
    """A retired row spans a surface; only its Stitch id is dead.

    The run that found this: BookSwap's manifest retired a duplicate of the
    `library` surface years ago, and every row carries the SURFACE in `id`. Read
    as dead too, `library` stopped resolving, so `stitch-adopt --onto library`
    silently adopted onto the duplicate id and the ledger gate then saw that id
    as both retired and canonical — the run failed verify with a contradiction
    the ledger itself had created.
    """

    def test_the_surface_id_of_a_spanning_row_stays_alive(self):
        manifest = {
            "canonical_screens": [{"id": "library", "stitch_id": "live-screen"}],
            "retired_screens": [
                {"id": "library", "stitch_id": "dead-screen", "replaced_by": "live-screen"},
            ],
        }

        dead = cli._retired_ids(manifest)

        self.assertEqual({"dead-screen"}, dead)
        self.assertNotIn("library", dead)

    def test_a_single_id_row_still_retires_its_screen(self):
        manifest = {"retired_screens": [{"id": "old-screen", "reason": "dropped"}]}

        self.assertEqual({"old-screen"}, cli._retired_ids(manifest))


if __name__ == "__main__":
    unittest.main()
