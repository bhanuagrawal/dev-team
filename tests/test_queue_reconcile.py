"""The plan heals itself: a delivered queue entry is dropped, never a blocker.

Owner directive 2026-11-16: "never get blocked just because plan is stale, re plan".
So reconciliation lives in the WRITE (`save_state`), is re-checked at the read
that matters (`pick`), has a verb for a sweep (`devteam reconcile`), and is a WARN
in doctor — never BLOCKED.

These tests pin the three things that make "it can't be forgotten" true:
1. the pass is deterministic and idempotent;
2. the write funnel runs it, so every verb inherits it;
3. only `team/queue.py` writes `state['queue']`, so no command can bypass it.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import core, queue

TEAM_ROOT = Path(__file__).resolve().parents[1]


def _entry(label: str, advances: str = "m1", **extra) -> dict:
    return {"label": label, "type": "bugfix", "target": "composeApp/**",
            "advances": advances, **extra}


def _state(*labels: str, shipped=("already-done",)) -> dict:
    return {
        "queue": [_entry(label) for label in labels],
        "shipped": [{"target": "composeApp/src/**", "label": label, "version": "1.0"}
                    for label in shipped],
        "runs": [],
    }


class ReconcileTest(unittest.TestCase):
    def test_a_delivered_entry_is_dropped_and_recorded(self):
        state = _state("already-done", "still-open")

        result = queue.reconcile(state)

        self.assertTrue(result["changed"])
        self.assertEqual(["still-open"], [q["label"] for q in state["queue"]])
        self.assertEqual(["already-done"], [d["label"] for d in result["dropped"]])
        events = [e for e in state["queue_events"] if e["source"] == "reconcile"]
        self.assertEqual("dropped", events[0]["action"])
        self.assertIn("already shipped", events[0]["reason"])

    def test_it_is_idempotent(self):
        state = _state("already-done", "still-open")
        queue.reconcile(state)

        again = queue.reconcile(state)

        self.assertFalse(again["changed"])
        self.assertEqual(["still-open"], [q["label"] for q in state["queue"]])
        self.assertEqual(1, len([e for e in state["queue_events"]
                                 if e["source"] == "reconcile"]))

    def test_open_work_is_never_touched(self):
        state = _state("still-open", "also-open", shipped=())

        result = queue.reconcile(state)

        self.assertFalse(result["changed"])
        self.assertEqual(["still-open", "also-open"],
                         [q["label"] for q in state["queue"]])

    def test_an_exact_duplicate_collapses_to_the_first_copy(self):
        state = _state("same-task", "same-task", shipped=())

        result = queue.reconcile(state)

        self.assertEqual(["same-task"], [q["label"] for q in state["queue"]])
        self.assertEqual("superseded", result["dropped"][0]["action"])

    def test_the_pass_stamps_what_it_read(self):
        state = _state("still-open", shipped=())

        queue.reconcile(state)

        self.assertTrue(state["queue_reconciled_at"])
        self.assertEqual(queue.queue_fingerprint(state["queue"]),
                         state["queue_fingerprint"])

    def test_the_read_only_form_reports_without_mutating(self):
        state = _state("already-done", "still-open")
        before = [q["label"] for q in state["queue"]]

        stale = queue.stale(state)

        self.assertEqual(["already-done"], [d["label"] for d in stale])
        self.assertEqual(before, [q["label"] for q in state["queue"]])
        self.assertNotIn("queue_reconciled_at", state)

    def test_drop_task_records_the_pop_and_refuses_a_missing_label(self):
        state = _state("one", "two", shipped=())

        self.assertTrue(queue.drop_task(state, "one"))
        self.assertEqual(["two"], [q["label"] for q in state["queue"]])
        self.assertEqual("dropped", state["queue_events"][0]["action"])
        self.assertFalse(queue.drop_task(state, "not-there"))


class SaveStateHealsTest(unittest.TestCase):
    """The WRITE is the funnel: every verb that saves a state reconciles it."""

    def test_save_state_drops_delivered_work_before_writing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            state = _state("already-done", "still-open")

            with patch.object(core, "_refresh_report", lambda *_: None):
                core.save_state(project, state)

            written = core.state_of(project)
            self.assertEqual(["still-open"], [q["label"] for q in written["queue"]])
            self.assertTrue(written["queue_reconciled_at"])
            self.assertFalse([q for q in written["queue"]
                              if q["label"] == "already-done"])

    def test_a_reconcile_failure_still_lands_the_write(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            state = _state("already-done")

            def boom(*_a, **_k):
                raise RuntimeError("queue pass is broken")

            with patch.object(core, "_refresh_report", lambda *_: None), \
                    patch("team.queue.reconcile", boom):
                core.save_state(project, state)   # must not raise

            written = core.state_of(project)
            # The ledger is the record: the entry survives a broken pass, and the
            # next save or `devteam reconcile` retries it.
            self.assertEqual(["already-done"], [q["label"] for q in written["queue"]])


class OneQueueWriterTest(unittest.TestCase):
    """`state['queue']` is written in ONE place, so the pass cannot be bypassed."""

    def test_only_team_queue_writes_the_queue(self):
        offenders = []
        files = [TEAM_ROOT / "cli.py", *(TEAM_ROOT / "team").glob("*.py")]
        for path in files:
            if path.name == "queue.py":
                continue
            text = path.read_text(errors="ignore")
            for shape in ('state["queue"] =', "state['queue'] ="):
                if shape in text:
                    offenders.append(path.name)
                    break
        self.assertEqual([], offenders,
                         "write state['queue'] through team/queue.py — a direct "
                         "assignment bypasses reconcile and the audit row")


if __name__ == "__main__":
    unittest.main()
