"""The mock must answer in production's SHAPES, not in a convenient one.

Two releases in a row were "verified" by device walks that could not have failed:

* the profile fixture carried WKT (`SRID=4326;POINT(...)`) while PostGIS returns
  a geometry as hex EWKB, so every real profile's coordinates were null and the
  app searched a hard-coded Bangalore fix (2026-11-20);
* `GET /api/v1/profile` answered a bare object while the client decodes a list,
  so the profile read failed and the same fallback was used again.

Each time the mock was the only thing that knew the shape, and each time a green
walk vouched for a build whose search was broken. These assertions are cheap and
they make that class of lie a failing test.
"""
import json
import unittest
import urllib.request

from team import mock_server


class MockContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = mock_server.MockServer().start()
        cls.server.wait_ready()
        cls.base = f"http://127.0.0.1:{cls.server.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def _get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.loads(r.read().decode())

    def test_profile_is_a_list_of_one_row(self):
        rows = self._get("/api/v1/profile")
        self.assertIsInstance(rows, list)
        self.assertEqual(1, len(rows))
        self.assertEqual(mock_server.READER_ID, rows[0]["id"])

    def test_profile_location_is_postgis_hex_not_wkt(self):
        row = self._get("/api/v1/profile")[0]
        location = row["location"]
        self.assertNotIn("POINT", location)          # never the spelling the app writes
        self.assertTrue(location.startswith("0101000020E6100000"))  # EWKB, SRID 4326
        self.assertEqual("Noida", row["city"])

    def test_search_at_the_profiles_place_finds_copies(self):
        lat, lng = mock_server.PROFILE_PLACE
        page = self._get(f"/api/v1/search?sentence=added%20this%20week&lat={lat}&lng={lng}")
        self.assertEqual(len(mock_server.BOOKS), page["total"])
        self.assertIn(mock_server.PROFILE["location_name"], page["area"]["why"])

    def test_search_from_a_place_the_reader_does_not_live_in_finds_nothing(self):
        # Bangalore, the app's invented fallback: 1,700 km from the fixture's row.
        page = self._get("/api/v1/search?sentence=added%20this%20week&lat=12.9784&lng=77.6408")
        self.assertEqual(0, page["total"])
        self.assertIn("not searching", page["area"]["why"])

    def test_a_get_records_the_query_it_was_asked(self):
        self._get("/api/v1/search?sentence=dune&lat=1.5&lng=2.5")
        last = self.server.requests[-1]
        self.assertEqual("dune", last["query"]["sentence"])
        self.assertEqual("1.5", last["query"]["lat"])


if __name__ == "__main__":
    unittest.main()
