"""The release workflow can run on a declared machine, and dev-team pulls it.

Why this is pinned: the account's Actions budget was a $0 stop on 2026-11-16, so
every GitHub-hosted job was refused in ~2s. GitHub does not meter self-hosted
runners, so the SAME workflow, secrets and upload step deliver normally from the
owner's Mac (proved on BookSwapKMP v1.0.71: versionCode 10113 reached `internal`
while the hosted jobs were still refused).

The design decision worth a test is that this is ONE job with a selector, not a
second job with a copy of the build steps: a duplicated fallback drifts, and a
drifting release workflow is the defect `team/ci.py` exists to prevent. So these
tests cover the rendered selector, the template's portability invariants (a
macOS runner rejects two GNU-isms), and the dispatch that `release` performs.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team import ci, shipper


LABELS = ["self-hosted", "macOS", "ARM64", "devteam"]


class RunsOnLineTest(unittest.TestCase):
    def test_a_project_with_no_machine_renders_the_cloud_label_verbatim(self):
        # Byte-identical to every workflow rendered before the selector existed.
        self.assertEqual(ci.runs_on_line({}), "ubuntu-latest")
        self.assertEqual(ci.runs_on_line({"self_hosted": []}), "ubuntu-latest")

    def test_a_declared_machine_renders_one_selector_not_a_second_job(self):
        line = ci.runs_on_line({"self_hosted": LABELS})
        self.assertIn("inputs.runner == 'local'", line)
        self.assertIn('fromJSON(\'["self-hosted","macOS","ARM64","devteam"]\')', line)
        self.assertTrue(line.endswith("|| 'ubuntu-latest' }}"), line)

    def test_labels_may_be_declared_as_a_comma_separated_string(self):
        self.assertIn('"ARM64"', ci.runs_on_line({"self_hosted": "self-hosted, ARM64"}))


class TemplateInvariantTest(unittest.TestCase):
    """What the template must never regress to."""

    def test_the_template_is_internally_sound(self):
        self.assertEqual(ci.self_check(), [])

    def test_the_selector_placeholder_is_used_by_both_jobs(self):
        text = ci.TEMPLATE.read_text()
        self.assertEqual(text.count("__RUNS_ON_LINE__"), 2)

    def test_no_gnu_only_step_survives(self):
        # Discovered on the real macOS runner, not hypothesised: BSD base64 has
        # no --decode (a hard failure at keystore restore) and BSD sed -i reads
        # the script as a backup suffix (silently skipped behind `|| true`).
        text = ci.TEMPLATE.read_text()
        self.assertNotIn("base64 --decode", text)
        self.assertNotIn("sed -i '", text)

    def test_the_dispatch_input_the_selector_reads_is_declared(self):
        # The template carries the placeholder; the expression is rendered. Both
        # halves must exist or `devteam` dispatch fails with "Unexpected input(s)".
        self.assertIn("      runner:", ci.TEMPLATE.read_text())
        self.assertIn("inputs.runner", ci.runs_on_line({"self_hosted": LABELS}))

    def test_a_branch_ref_cannot_become_an_artifact_name(self):
        # `debug-dt/runner-proof` failed a real run: a slash is illegal there.
        text = ci.TEMPLATE.read_text()
        self.assertNotIn("name: debug-${{ github.ref_name }}", text)
        self.assertIn('name: debug-${{ env.REF_SAFE }}', text)


class DispatchTest(unittest.TestCase):
    def _dispatch(self, declared, gh_rc=0, runs=None):
        runs = runs or [[{"databaseId": 1}], [{"databaseId": 2}, {"databaseId": 1}]]
        calls = []

        def fake_run(args, cwd=None, timeout=None):
            calls.append(args)
            return mock.Mock(returncode=gh_rc, stdout="", stderr="Unexpected input(s)")

        with mock.patch.object(shipper, "profile_of",
                               return_value={"repo_slug": "o/r", "ci_workflow": "release.yml",
                                             "ci": {"self_hosted": LABELS if declared else []}}), \
             mock.patch.object(shipper, "run", side_effect=fake_run), \
             mock.patch.object(shipper, "_ci_runs", side_effect=runs), \
             mock.patch.object(shipper.time, "sleep", return_value=None):
            rid = shipper.dispatch_local_run(Path("/project"), "v1.0.71")
        return rid, calls

    def test_no_declared_machine_means_no_gh_call_at_all(self):
        rid, calls = self._dispatch(declared=False)
        self.assertEqual(rid, "")
        self.assertEqual(calls, [])

    def test_it_dispatches_the_same_workflow_on_the_tag_with_runner_local(self):
        rid, calls = self._dispatch(declared=True)
        self.assertEqual(rid, "2")
        self.assertIn("workflow", calls[0])
        self.assertIn("release.yml", calls[0])
        self.assertIn("--ref", calls[0])
        self.assertIn("v1.0.71", calls[0])
        self.assertIn("runner=local", calls[0])

    def test_a_workflow_without_the_input_reports_instead_of_crashing(self):
        # A project copy rendered before TEMPLATE_VERSION 3 has no runner input;
        # the release must fall through to the tester channel, not die here.
        rid, calls = self._dispatch(declared=True, gh_rc=1)
        self.assertEqual(rid, "")
        self.assertTrue(calls)


class PrintedCopyTest(unittest.TestCase):
    """`devteam ci <project> > release.yml` IS what `--write` would write.

    Why this is pinned: `print(render())` appended a newline of its own, so a
    copy rendered through the shell was one byte longer than the renderer's.
    `doctor` compares the delivered file to the render and blocked the project as
    DRIFTED from the very template it matched (PdfKitKMP, 2026-11-19: 20376
    delivered vs 20375 rendered), and the only "fix" it could suggest was to
    rewrite a file nobody had hand-edited.
    """

    def test_stdout_is_byte_identical_to_the_render(self):
        import io
        import sys as _sys
        from contextlib import redirect_stdout
        from types import SimpleNamespace

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import cli
        from team import ci as ci_mod

        rendered = "on:\n  push:\n    tags: ['v*']\n"
        buf = io.StringIO()
        with mock.patch.object(cli, "resolve_project", return_value=Path("/tmp/none")), \
                mock.patch.object(ci_mod, "render", return_value=rendered), \
                redirect_stdout(buf):
            cli.cmd_ci(SimpleNamespace(project="X", write=False))
        self.assertEqual(buf.getvalue(), rendered)
        self.assertNotIn("\n\n", buf.getvalue())

    def test_a_render_without_its_own_newline_still_ends_in_exactly_one(self):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import cli
        from team import ci as ci_mod

        buf = io.StringIO()
        with mock.patch.object(cli, "resolve_project", return_value=Path("/tmp/none")), \
                mock.patch.object(ci_mod, "render", return_value="name: x"), \
                redirect_stdout(buf):
            cli.cmd_ci(SimpleNamespace(project="X", write=False))
        self.assertEqual(buf.getvalue(), "name: x\n")


if __name__ == "__main__":
    unittest.main()
