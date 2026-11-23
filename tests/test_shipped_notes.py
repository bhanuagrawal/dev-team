"""The store's current note is found by WHICH ROW COVERS THE NOTE, not by row order.

The ledger appends one row per touched path, and each row names the commit that
path's change landed on. For one release the rows therefore disagree: a code file
is recorded at the task commit, the note at the bump commit. `shipped_notes` read
the newest row and asked that commit for the note — so v1.0.91-rc.1 was compared
against v1.0.90 using the OLD note, and `store-readiness` reported the fresh,
correctly different release note as "shipped the same note as v1.0.90"
(2026-11-24).

Pinned here because the failure mode is invisible from the outside: the command
that raised it (`store-readiness`) has nothing to do with the ledger, so the bug
read as a release-note problem and the owner would have been asked to rewrite a
note that was already right.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import shipper

REL = "distribution/whatsnew/whatsnew-en-US"
OLD = "✓ Fixed: the old thing\n"
NEW = "✓ Fixed: the new thing\n"


def _git(repo: Path, *argv: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *argv],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _commit_note(repo: Path, note: str) -> str:
    path = repo / REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note)
    _git(repo, "add", REL)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "-m", note.strip())
    return _git(repo, "rev-parse", "HEAD")


def _project(rows: list[dict]) -> Path:
    project = Path(tempfile.mkdtemp())
    _git(project, "init", "-q")
    (project / ".pipeline").mkdir()
    (project / ".pipeline/state.json").write_text(json.dumps({"shipped": rows}))
    return project


class ShippedNoteIsReadFromTheRowThatCoversIt(unittest.TestCase):

    def test_a_code_row_recorded_last_does_not_hide_the_note_commit(self):
        project = _project([])
        old = _commit_note(project, OLD)
        task_commit = _git(project, "rev-parse", "HEAD")
        new = _commit_note(project, NEW)
        # Exactly the shape the ledger wrote: the code row names the TASK commit
        # (before the bump), the note row names the bump — and the code row is
        # appended last, so iterating backwards hit the outdated commit.
        (project / ".pipeline/state.json").write_text(json.dumps({"shipped": [
            {"version": "1.0.90", "commit": old, "target": "distribution/whatsnew/**"},
            {"version": "1.0.91-rc.1", "commit": new, "target": "distribution/whatsnew/**"},
            {"version": "1.0.91-rc.1", "commit": task_commit, "target": "composeApp/src/commonMain/**"},
        ]}))

        tail = shipper.shipped_notes(project, REL, limit=2)

        self.assertEqual(["1.0.91-rc.1", "1.0.90"], [v for v, _ in tail])
        self.assertEqual(NEW, tail[0][1],
                         "the shipped note is the one the bump commit carries")
        self.assertNotEqual(tail[0], tail[1],
                            "a fresh note must not read as a repeat of the previous one")

    def test_a_version_with_no_covering_row_still_reads_its_newest_row(self):
        project = _project([])
        old = _commit_note(project, OLD)
        new = _commit_note(project, NEW)
        # An older release shape: only code rows, no row that names the note.
        (project / ".pipeline/state.json").write_text(json.dumps({"shipped": [
            {"version": "1.0.90", "commit": old, "target": "composeApp/src/commonMain/**"},
            {"version": "1.0.91-rc.1", "commit": new, "target": "composeApp/src/commonMain/**"},
        ]}))

        tail = shipper.shipped_notes(project, REL, limit=2)

        self.assertEqual(["1.0.91-rc.1", "1.0.90"], [v for v, _ in tail])
        self.assertEqual(NEW, tail[0][1])

    def test_a_row_whose_commit_lacks_the_note_falls_through(self):
        project = _project([])
        (project / "README.md").write_text("no note yet\n")
        _git(project, "add", "README.md")
        _git(project, "-c", "user.name=t", "-c", "user.email=t@t",
             "commit", "-q", "-m", "before the note")
        note_commit = _commit_note(project, NEW)
        before_note = _git(project, "rev-list", "--max-parents=0", "HEAD")
        (project / ".pipeline/state.json").write_text(json.dumps({"shipped": [
            {"version": "1.0.91-rc.1", "commit": note_commit, "target": "distribution/whatsnew/**"},
            {"version": "1.0.91-rc.1", "commit": before_note, "target": "distribution/whatsnew/**"},
        ]}))

        tail = shipper.shipped_notes(project, REL, limit=1)

        self.assertEqual([("1.0.91-rc.1", NEW)], tail)


if __name__ == "__main__":
    unittest.main()
