"""The deployed backend must carry the change the client ships.

Observed 2026-11-22: `verify` was green and 1.0.89-rc.2 reached internal testing
while the live service had no feedback routes and `POST /api/v1/offers` died on a
dropped column. The walk saw every observable the task declared, because the deal
page is painted from fields the OLD server already sent — so the gate has to ask
the server, not only the screen.
"""

import json
import tempfile
import unittest
from pathlib import Path

from team import backend_probe


SPEC = {"backend": {
    "repo": "bookswap-backend",
    "base_url": "https://example.run.app",
    "requires": {
        "routes": ["POST /api/v1/offers/{offer_id}/feedback"],
        "calls": [{"path": "/api/v1/offers",
                   "item_keys": ["timeline", "feedback"]}],
    },
}}


class MissingRoutesTest(unittest.TestCase):
    def test_a_route_the_document_does_not_carry_is_named(self):
        paths = {"/api/v1/offers": {"get": {}, "post": {}}}
        self.assertEqual(
            ["POST /api/v1/offers/{offer_id}/feedback"],
            backend_probe.missing_routes(paths, ["POST /api/v1/offers/{offer_id}/feedback"]))

    def test_the_method_matters_not_only_the_path(self):
        paths = {"/api/v1/offers": {"get": {}}}
        self.assertEqual(["POST /api/v1/offers"],
                         backend_probe.missing_routes(paths, ["POST /api/v1/offers"]))
        self.assertEqual([], backend_probe.missing_routes(paths, ["GET /api/v1/offers"]))


class MissingKeysTest(unittest.TestCase):
    def test_a_row_without_the_key_is_the_old_shape(self):
        row = {"id": "1", "status": "COMPLETED"}
        self.assertEqual(["timeline", "feedback"],
                         backend_probe.missing_keys([row], ["timeline", "feedback"]))

    def test_the_first_item_decides_for_a_list(self):
        self.assertEqual([], backend_probe.missing_keys(
            [{"timeline": [], "feedback": []}], ["timeline"]))

    def test_an_empty_list_is_not_evidence_either_way(self):
        self.assertEqual([], backend_probe.missing_keys([], ["timeline"]))

    def test_an_object_answer_is_read_directly(self):
        self.assertEqual(["feedback"],
                         backend_probe.missing_keys({"timeline": []}, ["timeline", "feedback"]))

    def test_an_envelope_is_opened_to_find_its_rows(self):
        # BookSwap answers {"offers": [...], "next_cursor": null}; a bare-list
        # reader called that old-shape after a correct deploy.
        live = {"offers": [{"timeline": [], "feedback": {}, "agreed_at": None}],
                "next_cursor": None}
        self.assertEqual([], backend_probe.missing_keys(
            live, ["timeline", "feedback", "agreed_at"]))

    def test_an_empty_envelope_is_not_evidence_either_way(self):
        self.assertEqual([], backend_probe.missing_keys({"offers": [], "next_cursor": None},
                                                        ["timeline"]))


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".pipeline").mkdir()
        (self.root / ".pipeline" / "audit-account.json").write_text(
            json.dumps({"email": "audit@example.invalid", "password": "x"}))

    def tearDown(self):
        self.tmp.cleanup()

    def _fetch(self, paths, body=None, status_for_call=200):
        def fetch(method, url, payload, token):
            if url.endswith("/openapi.json"):
                return 200, {"paths": paths}
            return status_for_call, body
        return fetch

    def _login(self, *_a):
        return "token-1", ""

    def _contract(self, fetch):
        return backend_probe.contract(self.root, SPEC, self.root,
                                      fetch=fetch, sign_in=self._login)

    def test_no_backend_block_is_no_question(self):
        ok, msg = backend_probe.contract(self.root, {}, self.root)
        self.assertTrue(ok, msg)
        self.assertIn("no backend contract declared", msg)

    def test_the_service_that_has_it_passes(self):
        fetch = self._fetch(
            {"/api/v1/offers/{offer_id}/feedback": {"post": {}},
             "/api/v1/offers": {"get": {}}},
            body=[{"timeline": [], "feedback": []}])
        ok, msg = self._contract(fetch)
        self.assertTrue(ok, msg)
        self.assertIn("carries the declared contract", msg)

    def test_a_missing_route_refuses_and_says_how_to_fix_it(self):
        fetch = self._fetch({"/api/v1/offers": {"get": {}}},
                            body=[{"timeline": [], "feedback": []}])
        ok, msg = self._contract(fetch)
        self.assertFalse(ok, msg)
        self.assertIn("POST /api/v1/offers/{offer_id}/feedback", msg)
        self.assertIn("deploy.py", msg)

    def test_an_old_response_shape_refuses(self):
        fetch = self._fetch(
            {"/api/v1/offers/{offer_id}/feedback": {"post": {}},
             "/api/v1/offers": {"get": {}}},
            body=[{"id": "1", "status": "COMPLETED"}])
        ok, msg = self._contract(fetch)
        self.assertFalse(ok, msg)
        self.assertIn("timeline", msg)

    def test_an_unreachable_service_is_a_refusal_not_a_pass(self):
        def fetch(method, url, payload, token):
            return 0, "connection refused"
        ok, msg = self._contract(fetch)
        self.assertFalse(ok, msg)
        self.assertIn("cannot describe itself", msg)

    def test_a_call_that_answers_404_refuses(self):
        fetch = self._fetch({"/api/v1/offers/{offer_id}/feedback": {"post": {}},
                             "/api/v1/offers": {"get": {}}},
                            body={"detail": "Not Found"}, status_for_call=404)
        ok, msg = self._contract(fetch)
        self.assertFalse(ok, msg)
        self.assertIn("answered 404", msg)


if __name__ == "__main__":
    unittest.main()
