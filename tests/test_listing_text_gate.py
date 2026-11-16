"""The listing's words must describe the product that ships.

Pinned because the pictures were gated and the copy was not: a rename or a
retirement could leave the title, the short description, the long description or
What's New naming a product the app no longer is, and Play shows that to every
reader who finds the app (owner request 2026-11-15).

The gate is deterministic on purpose — the app's own launcher name, and names the
project declared retired. It never guesses which words are "off-brand".
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import verify


MANIFEST = """<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <application android:label="BookSwap: Local Book Lending" />
</manifest>
"""


def _tree(root: Path, *, title="BookSwap: Local Book Lending",
          short="Zero-cash book swaps near you.", full="BookSwap swaps books within walking distance.",
          whatsnew="Small fixes.", manifest=True, retired=None) -> Path:
    listing = root / "distribution/listing/en-US"
    listing.mkdir(parents=True)
    (listing / "title.txt").write_text(title)
    (listing / "short_description.txt").write_text(short)
    (listing / "full_description.txt").write_text(full)
    (root / "distribution/whatsnew").mkdir(parents=True)
    (root / "distribution/whatsnew/whatsnew-en-US").write_text(whatsnew)
    if manifest:
        m = root / "composeApp/src/androidMain/AndroidManifest.xml"
        m.parent.mkdir(parents=True)
        m.write_text(MANIFEST)
    if retired is not None:
        (root / "docs").mkdir(parents=True, exist_ok=True)
        (root / "docs/storefront-direction.json").write_text(
            json.dumps({"retired_names": retired}))
    return root


class ListingTextGateTest(unittest.TestCase):
    def test_copy_that_names_the_app_passes(self):
        with tempfile.TemporaryDirectory() as d:
            ok, msg = verify.listing_text_check(_tree(Path(d)))
        self.assertTrue(ok, msg)

    def test_a_retired_name_in_any_text_fails(self):
        for field in ("title", "short", "full", "whatsnew"):
            with tempfile.TemporaryDirectory() as d:
                kwargs = {"retired": ["Folio & Shelf"]}
                if field == "title":
                    kwargs["title"] = "Folio & Shelf: Local Book Lending"
                elif field == "short":
                    kwargs["short"] = "Folio & Shelf — zero-cash swaps."
                elif field == "full":
                    kwargs["full"] = "BookSwap began life as Folio & Shelf."
                else:
                    kwargs["whatsnew"] = "Folio & Shelf is now BookSwap."
                ok, msg = verify.listing_text_check(_tree(Path(d), **kwargs))
            self.assertFalse(ok, field)
            self.assertIn("retired", msg)

    def test_a_title_that_never_names_the_app_fails(self):
        with tempfile.TemporaryDirectory() as d:
            ok, msg = verify.listing_text_check(
                _tree(Path(d), title="Local Book Lending"))
        self.assertFalse(ok)
        self.assertIn("never names the app", msg)

    def test_the_descriptions_need_not_repeat_the_name(self):
        # The real short description reads "Zero-cash, hyper-local book swaps and
        # lending near you." — good copy that never says BookSwap.
        with tempfile.TemporaryDirectory() as d:
            ok, msg = verify.listing_text_check(
                _tree(Path(d), short="Zero-cash, hyper-local book swaps and lending near you."))
        self.assertTrue(ok, msg)

    def test_whats_new_may_describe_a_fix_without_the_name(self):
        # A change note is not required to repeat the product's name; only a
        # retired name is a contradiction there.
        with tempfile.TemporaryDirectory() as d:
            ok, msg = verify.listing_text_check(_tree(Path(d), whatsnew="Offers have their own screens."))
        self.assertTrue(ok, msg)

    def test_a_project_with_no_listing_copy_passes(self):
        with tempfile.TemporaryDirectory() as d:
            ok, msg = verify.listing_text_check(Path(d))
        self.assertTrue(ok)
        self.assertIn("nothing to compare", msg)


if __name__ == "__main__":
    unittest.main()
