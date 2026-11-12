"""The peer's state machine, asserted without a server.

The peer is the second reader in a one-UI audit. Its decisions must be boring:
accept when it is our turn, share before handover, hand over only when both
numbers are in, return a temporary loan, and never touch a closed swap.
"""
import unittest

from team.flow_peer import next_action

ME = "peer-1"
PHONE = "+91 90000 00002"


def offer(**overrides):
    row = {
        "status": "REQUESTED",
        "last_offered_by": "app-1",
        "exchange_mode": "TEMPORARY",
        "contact_shared_by_me": False,
        "contact_shared_by_counterpart": False,
        "handover_confirmed_by_me": False,
        "handover_confirmed_by_counterpart": False,
        "return_confirmed_by_me": False,
        "return_confirmed_by_counterpart": False,
    }
    row.update(overrides)
    return row


class FlowPeerTest(unittest.TestCase):
    def test_accepts_an_offer_when_it_is_our_turn(self):
        action = next_action(offer(last_offered_by="app-1"), ME, PHONE)
        self.assertEqual("accept", action["action"])

    def test_waits_on_our_own_open_offer(self):
        self.assertIsNone(next_action(offer(last_offered_by=ME), ME, PHONE))

    def test_shares_a_number_before_handover(self):
        action = next_action(offer(status="HANDOFF_SCHEDULED"), ME, PHONE)
        self.assertEqual("share", action["action"])
        self.assertEqual(PHONE, action["phone"])

    def test_waits_for_the_other_number_before_handover(self):
        self.assertIsNone(next_action(offer(
            status="HANDOFF_SCHEDULED",
            contact_shared_by_me=True,
            contact_shared_by_counterpart=False,
        ), ME, PHONE))

    def test_confirms_handover_only_when_both_numbers_are_shared(self):
        action = next_action(offer(
            status="HANDOFF_SCHEDULED",
            contact_shared_by_me=True,
            contact_shared_by_counterpart=True,
        ), ME, PHONE)
        self.assertEqual("handover", action["action"])

    def test_does_not_repeat_a_handover_confirmation(self):
        self.assertIsNone(next_action(offer(
            status="HANDOFF_SCHEDULED",
            contact_shared_by_me=True,
            contact_shared_by_counterpart=True,
            handover_confirmed_by_me=True,
        ), ME, PHONE))

    def test_returns_a_live_temporary_loan(self):
        action = next_action(offer(
            status="ACTIVE_BORROW",
            exchange_mode="TEMPORARY",
        ), ME, PHONE)
        self.assertEqual("return", action["action"])

    def test_never_returns_a_permanent_swap(self):
        self.assertIsNone(next_action(offer(
            status="ACTIVE_BORROW",
            exchange_mode="PERMANENT",
        ), ME, PHONE))

    def test_closed_swaps_are_untouched(self):
        for status in ("COMPLETED", "CANCELLED"):
            self.assertIsNone(next_action(offer(status=status), ME, PHONE))


if __name__ == "__main__":
    unittest.main()
