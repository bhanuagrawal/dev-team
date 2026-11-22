"""The release gate asks about the change, not about a journey schedule.

Owner rule 2026-11-20 ("jo banaya hai usko emulator pe test karo, fir release"),
narrowed on 2026-11-21: *"wo ux flow run wala rule hata do … current change jo
kiya hai wo emulator pe run karne wala laga do"*.

So `flow_gate` passes when the code this diff touched has a walk recorded after
it that reached its goal — and it does NOT fail because another declared journey
was last walked on an older build, or because a journey whose screen the diff
also touches was not separately re-walked. What it still refuses is a change
nobody opened, and a walk that stopped at a wall standing in as evidence.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from team import ux_flow, verify

CODE = "composeApp/src/commonMain/kotlin/Place.kt"


class FlowGateChangeOnlyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        spec = self.root / "docs" / "ux-flows.json"
        spec.parent.mkdir(parents=True)
        spec.write_text(json.dumps({"flows": [
            {"id": "search", "surface": "Discovery", "goal": "find a book"},
            {"id": "library", "surface": "Library", "goal": "see my shelf"},
        ]}))
        self.code = self.root / CODE
        self.code.parent.mkdir(parents=True)
        self.code.write_text("class Place\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _walk(self, flow_id: str, age: float, *, reached: bool = True) -> Path:
        out = ux_flow.run_dir(self.root) / ux_flow._slug(flow_id)
        out.mkdir(parents=True, exist_ok=True)
        result = out / "result.json"
        result.write_text(json.dumps({
            "goal_reached": reached,
            "outcome": "done" if reached else "stuck",
            "reason": "" if reached else "the offer sheet would not open",
        }))
        stamp = time.time() - age
        os.utime(result, (stamp, stamp))
        return result

    def _touch(self, age: float) -> None:
        stamp = time.time() - age
        os.utime(self.code, (stamp, stamp))

    def _gate(self, files=None):
        return verify.flow_gate(self.root, self.root, {}, files or [CODE])

    def test_one_walk_of_the_change_passes_however_old_the_other_journeys_are(self):
        # `library` was never walked on this tree at all. That is not a refusal
        # under the narrowed rule: the change was opened on a device.
        self._touch(age=600)
        self._walk("search", age=60)
        ok, msg = self._gate()
        self.assertTrue(ok, msg)
        self.assertIn("was run on a device", msg)
        self.assertIn("search", msg)

    def test_a_change_nobody_opened_is_refused(self):
        ok, msg = self._gate()
        self.assertFalse(ok, msg)
        self.assertIn("has not been run on a device", msg)
        self.assertIn(CODE, msg)

    def test_a_walk_older_than_the_change_is_refused(self):
        self._walk("search", age=600)
        self._touch(age=60)
        ok, msg = self._gate()
        self.assertFalse(ok, msg)
        self.assertIn("has not been run on a device", msg)

    def test_a_walk_that_stopped_at_a_wall_is_not_evidence(self):
        self._touch(age=600)
        self._walk("search", age=60, reached=False)
        ok, msg = self._gate()
        self.assertFalse(ok, msg)
        self.assertIn("does not show the change working", msg)
        self.assertIn("did not reach the goal", msg)

    def test_a_docs_only_diff_needs_no_walk(self):
        ok, msg = self._gate(["docs/ux-flows.json", "distribution/whatsnew/en-US"])
        self.assertTrue(ok, msg)
        self.assertIn("no code a device executes", msg)

    def _declare_paperwork(self) -> None:
        (self.root / ".pipeline").mkdir(exist_ok=True)
        (self.root / ".pipeline" / "profile.json").write_text(json.dumps({
            "version_file": "composeApp/build.gradle.kts",
            "whatsnew_file": "distribution/whatsnew/whatsnew-en-US",
        }))

    def test_the_release_version_bump_is_not_a_change_to_walk(self):
        """`release` bumps its own version file; that is paperwork, not a screen.

        Observed 2026-11-22: after 1.0.89-rc.2 shipped, re-releasing rc.3 was
        refused with "the change in composeApp/build.gradle.kts has not been run
        on a device" — the entire change was `versionName = "1.0.89-rc.3"`, written
        by `release` itself after the walk it already had.
        """
        self._declare_paperwork()
        version_file = self.root / "composeApp" / "build.gradle.kts"
        version_file.write_text('versionName = "1.0.89-rc.3"\n')
        note = self.root / "distribution" / "whatsnew" / "whatsnew-en-US"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("a deal reads Waiting, Agreed, Done or Ended\n")
        self._touch(age=600)
        self._walk("search", age=60)
        ok, msg = self._gate(["composeApp/build.gradle.kts",
                              "distribution/whatsnew/whatsnew-en-US", CODE])
        self.assertTrue(ok, msg)
        self.assertIn("search", msg)

    def test_a_real_build_script_change_still_needs_a_walk(self):
        self._declare_paperwork()
        settings = self.root / "settings.gradle.kts"
        settings.write_text("include(\":composeApp\")\n")
        self._touch(age=600)
        self._walk("search", age=60)
        ok, msg = self._gate(["settings.gradle.kts"])
        self.assertFalse(ok, msg)
        self.assertIn("settings.gradle.kts", msg)


if __name__ == "__main__":
    unittest.main()
