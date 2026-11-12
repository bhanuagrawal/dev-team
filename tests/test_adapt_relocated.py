"""adapt._relocated: a dead convention path is repaired only structurally.

A real project's coding-convention entry pointed at
`.../.gemini/antigravity/worktrees/dev-team/<branch>/skills/android-code/SKILL.md`
and adapt refused it as an untraceable ask, because only a plain `/dev-team/`
relocation was understood. A team WORKTREE is a throwaway copy of the team repo,
so the same relative path under the live team root is the answer — no guessing,
and the skills-root collision (two different `android-code` skills) is never
silently resolved.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TEAM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TEAM_ROOT))

from team import adapt  # noqa: E402
from team import core  # noqa: E402


class RelocatedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "team"
        self.target = self.root / "skills" / "android-code" / "SKILL.md"
        self.target.parent.mkdir(parents=True)
        self.target.write_text("# style law\n")
        self._patch = mock.patch.object(core, "TEAM_ROOT", self.root)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def test_worktree_path_maps_to_live_team_root(self):
        dead = ("/Users/someone/.gemini/antigravity/worktrees/dev-team/"
                "ship_some_app/skills/android-code/SKILL.md")
        self.assertEqual(adapt._relocated(dead), str(self.target))

    def test_plain_team_path_still_maps(self):
        dead = "/Users/someone/dev-team/skills/android-code/SKILL.md"
        self.assertEqual(adapt._relocated(dead), str(self.target))

    def test_missing_file_is_not_invented(self):
        dead = ("/Users/someone/.gemini/antigravity/worktrees/dev-team/"
                "ship_some_app/skills/android-code/GONE.md")
        self.assertIsNone(adapt._relocated(dead))


if __name__ == "__main__":
    unittest.main()
