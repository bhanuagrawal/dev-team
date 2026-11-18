"""The ChatGPT painter must save the ANSWER it painted, never the attachment it sent.

Pinned because the failure is invisible downstream: the run reports "painted" for
every frame, the critic reads copy and order and passes, and the listing ships the
bare device capture at the capture's own aspect — the app's own screen stretched
into the poster slot (observed 2026-11-18: eight frames, all of them the
attachment, while the real 9:16 answer sat in each chat unpicked; the
prompt-only fix of 2026-11-17 addressed a different cause and left this live).

Two independent witnesses, because either alone is fragile:
  * scope — only an image inside ChatGPT's generated-image node can be the answer;
  * freshness — a src already on the canvas when delivery was proved is content.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import storefront_poster as sp

JOB = {
    "out": "/tmp/poster.png",
    "src": "/tmp/scene.png",
    "prompt": "paint this",
    "sha": "abc",
    "source_sha": "def",
    "sidecar": "/tmp/poster.json",
    "ar": 0.45,
}


class PicksTheAnswerTests(unittest.TestCase):
    def _script(self) -> str:
        return sp._chatgpt_batch_script([JOB])

    def test_candidates_are_scoped_to_generated_images(self):
        script = self._script()
        self.assertIn("const answerImgs", script)
        self.assertIn("data-testid=generated-image-gallery", script,
                      "the answer must be found by the marker that only generated "
                      "images carry — the user's attachment is a plain main img")
        self.assertIn("(await answerImgs()).filter(", script,
                      "the picker must read the scoped list, not every image on the page")

    def test_content_is_snapshotted_after_delivery_is_proved(self):
        script = self._script()
        delivered = script.index("delivered = true")
        known = script.index("const known = new Set(")
        self.assertLess(delivered, known,
                        "the known-content snapshot must be taken once the sent message "
                        "carries the render, or the attachment itself is not yet on screen")

    def test_a_known_src_can_never_be_saved_as_the_frame(self):
        script = self._script()
        self.assertIn("!known.has(x.src)", script,
                      "a src already on the canvas is the attachment, never the answer")

    def test_delivery_proof_still_reads_the_whole_canvas(self):
        # The sent message is NOT a generated image, so the scoped list cannot prove
        # delivery; the shape check must keep looking at every non-composer image.
        script = self._script()
        self.assertIn(".filter(x => !x.composer).map(x => x.src)", script)

    def test_the_gemini_path_reads_only_model_responses(self):
        script = sp._gemini_batch_script([JOB])
        self.assertIn("model-response img", script,
                      "Gemini's locator is scoped to the answer, so the attachment cannot "
                      "be mistaken for it; a project-agnostic change must not lose that")

    def test_generated_scripts_parse(self):
        for script in (self._script(), sp._gemini_batch_script([JOB])):
            self.assertEqual(sp._script_syntax_error(script), "")


if __name__ == "__main__":
    unittest.main()
