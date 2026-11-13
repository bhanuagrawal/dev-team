"""The last few commits of main are the plan's to-do list, not its wallpaper.

Owner directive 2026-11-13: *"verify / replan based on the last few commits in
main"*. A sha mismatch only says the plan is old; the commits say what it is old
about. These tests pin the three readers to ONE list:

  * `core.recent_commits` — what landed, and what landed AFTER a point (`since`);
  * `replan.detect` — the drift detail names the commits the plan has not seen;
  * `research._recent_commits_pull` — the planner receives the same list as
    `since_plan_base`, so a replan can reconcile it;
  * `verify.base_drift` — the gate knows when its worktree predates main.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TEAM_ROOT))

from team import core, replan, research, verify  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com",
                        *args], cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


class CommitsSinceBaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "sample_app"
        self.project.mkdir()
        self.origin = root / "origin.git"
        self.origin.mkdir()
        _git(self.origin, "init", "--bare", "-b", "main")

        _git(self.project, "init", "-b", "main")
        (self.project / "a.txt").write_text("one\n")
        _git(self.project, "add", "-A")
        _git(self.project, "commit", "-m", "feat: first thing")
        self.first = _git(self.project, "rev-parse", "HEAD")

        (self.project / "b.txt").write_text("two\n")
        _git(self.project, "add", "-A")
        _git(self.project, "commit", "-m", "fix(ux-flow): a walk can finish signup")
        self.second = _git(self.project, "rev-parse", "HEAD")

        _git(self.project, "remote", "add", "origin", str(self.origin))
        _git(self.project, "push", "-u", "origin", "main")

        (self.project / ".pipeline").mkdir()
        (self.project / ".pipeline" / "profile.json").write_text(json.dumps({
            "name": "sample_app", "main_branch": "main", "verify": {"compile": "true"},
        }))
        (self.project / ".pipeline" / "state.json").write_text(json.dumps({
            "plan_base_sha": self.first[:12], "queue": [],
        }))

    def tearDown(self):
        self.tmp.cleanup()

    # -- the primitive ----------------------------------------------------- #
    def test_recent_commits_newest_first_and_limited(self):
        rows = core.recent_commits(self.project, ref="main", limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["subject"], "fix(ux-flow): a walk can finish signup")
        self.assertTrue(rows[0]["sha"])

    def test_since_narrows_to_what_the_plan_has_not_seen(self):
        rows = core.recent_commits(self.project, ref="main", since=self.first, limit=8)
        self.assertEqual([r["subject"] for r in rows],
                         ["fix(ux-flow): a walk can finish signup"])
        self.assertEqual(core.recent_commits(self.project, ref="main",
                                             since=self.second), [])

    def test_commit_summary_names_and_counts_the_rest(self):
        rows = [{"sha": "aaa1111", "subject": "one"},
                {"sha": "bbb2222", "subject": "two"}]
        self.assertEqual(core.commit_summary(rows, limit=1),
                         "aaa1111 one (+1 more)")

    def test_unreadable_ref_is_empty_not_an_exception(self):
        self.assertEqual(core.recent_commits(self.project, ref="no-such-ref"), [])

    def test_plan_mirror_commits_are_not_drift(self):
        (self.project / "docs").mkdir()
        (self.project / "docs" / "goals.html").write_text("<html>plan</html>\n")
        _git(self.project, "add", "-A")
        _git(self.project, "commit", "-m", "chore(plan): 18 features, 5 queued, 82 shipped")
        rows = core.recent_commits(self.project, ref="HEAD", limit=3)
        self.assertEqual([r["subject"] for r in rows],
                         ["fix(ux-flow): a walk can finish signup",
                          "feat: first thing"])

    # -- replan: the drift detail IS the commit list ------------------------ #
    def test_replan_detect_names_the_commits_since_the_plan_base(self):
        found = replan.detect(self.project)
        drift = [f for f in found if f["what"] == "plan built from an older main"]
        self.assertEqual(len(drift), 1, found)
        self.assertIn("fix(ux-flow): a walk can finish signup", drift[0]["detail"])

    def test_replan_result_reports_the_commits_it_was_built_around(self):
        rows = core.recent_commits(self.project, ref="origin/main",
                                   since=json.loads(
                                       (self.project / ".pipeline" / "state.json")
                                       .read_text())["plan_base_sha"])
        self.assertEqual(len(rows), 1)
        self.assertIn("signup", rows[0]["subject"])

    # -- the planner receives the same list -------------------------------- #
    def test_research_pull_carries_since_plan_base(self):
        sig = research._recent_commits_pull(
            self.project, since_plan_base=self.first[:12])
        self.assertEqual([c["subject"] for c in sig["since_plan_base"]],
                         ["fix(ux-flow): a walk can finish signup"])
        self.assertEqual(sig["since_plan_base_ref"], "origin/main")
        # The general window still exists (it answers a different question).
        self.assertTrue(sig["recent_commits"])

    def test_research_pull_without_a_base_sends_no_since_list(self):
        sig = research._recent_commits_pull(self.project)
        self.assertEqual(sig["since_plan_base"], [])
        self.assertIsNone(sig["since_plan_base_ref"])

    # -- verify: a worktree older than main says so ------------------------- #
    def test_base_drift_names_what_the_gate_did_not_judge(self):
        drift = verify.base_drift(self.project, self.first)
        self.assertEqual([c["subject"] for c in drift],
                         ["fix(ux-flow): a walk can finish signup"])

    def test_base_drift_is_empty_on_the_delivery_ref(self):
        self.assertEqual(verify.base_drift(self.project, "origin/main"), [])


if __name__ == "__main__":
    unittest.main()
