"""Unit tests for ``ivpm diagnose git`` (plan Phase 5).

The command is the on-demand surface for the same decision the DEBUG trace
emits during a normal run, plus the probe -- so a user can investigate
without triggering a failing update, and support can ask for one command's
output.
"""
import io
import json
import textwrap
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import ivpm.utils as utils
import ivpm.cmds.cmd_diagnose as cd
from ivpm.cmds.cmd_diagnose import CmdDiagnose

from .test_base import TestBase


_URL = "https://github.com/example-org/lib-example.git"


def _args(**kw):
    base = dict(diagnose_cmd="git", target=_URL, project_dir=None, ssh=False,
                anonymous=False, git_auth_order=None, ls_remote=False,
                no_probe=True, json=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


class TestDiagnoseGit(unittest.TestCase):

    def setUp(self):
        utils.gh_auth_available.cache_clear()
        self.addCleanup(utils.gh_auth_available.cache_clear)

    def _run(self, **kw):
        out = io.StringIO()
        with patch.object(utils, "gh_auth_available", lambda h: True), \
             redirect_stdout(out):
            CmdDiagnose()(_args(**kw))
        return out.getvalue()

    def test_prints_the_decision(self):
        text = self._run()
        self.assertIn("git auth decision:", text)
        self.assertIn(_URL, text)
        self.assertIn("chosen", text)
        self.assertIn("credentials", text)

    def test_no_probe_is_reported_not_silent(self):
        text = self._run()
        self.assertIn("probe skipped", text)

    def test_retry_order_is_shown_when_there_is_a_fallback(self):
        text = self._run(git_auth_order=["gh", "ssh"])
        self.assertIn("retry order", text)
        self.assertIn("git@github.com:example-org/lib-example.git", text)

    def test_ssh_flag_is_honored(self):
        text = self._run(ssh=True)
        self.assertIn("git@github.com:example-org/lib-example.git", text)
        self.assertIn("cli:--ssh", text)

    def test_json_output_shape(self):
        data = json.loads(self._run(json=True))
        self.assertEqual(data["target"], _URL)
        self.assertEqual(data["decision"]["declared_url"], _URL)
        self.assertTrue(data["candidates"])
        self.assertIn("method", data["candidates"][0])
        self.assertIsNone(data["probe"])
        self.assertIsNone(data["ls_remote"])

    def test_json_includes_the_probe_when_enabled(self):
        fake = types.SimpleNamespace(
            as_dict=lambda: {"verdict": "v", "checks": [], "remedies": [],
                             "transport": "https"})
        with patch("ivpm.git_probe.probe_git_auth", return_value=fake):
            data = json.loads(self._run(json=True, no_probe=False))
        self.assertEqual(data["probe"]["verdict"], "v")

    def test_verdict_and_remedies_are_printed(self):
        from ivpm.git_probe import ProbeResult
        fake = ProbeResult(checks=[("gh", "installed")],
                           verdict="the token is fine",
                           remedies=["do the thing"])
        with patch("ivpm.git_probe.probe_git_auth", return_value=fake):
            text = self._run(no_probe=False)
        self.assertIn("git auth probe:", text)
        self.assertIn("verdict: the token is fine", text)
        self.assertIn("remedy: do the thing", text)

    def test_ls_remote_per_candidate(self):
        def _fake_run(cmd, **kw):
            return types.SimpleNamespace(returncode=0, stdout="abc\trefs/heads/main\n",
                                         stderr="")
        with patch.object(cd.subprocess, "run", _fake_run):
            text = self._run(ls_remote=True, git_auth_order=["gh", "ssh"])
        self.assertIn("ls-remote per candidate:", text)
        self.assertEqual(text.count("ok (1 heads)"), 2)

    def test_ls_remote_failure_is_classified(self):
        def _fake_run(cmd, **kw):
            return types.SimpleNamespace(
                returncode=128, stdout="",
                stderr="remote: Repository not found.\nfatal: not found")
        with patch.object(cd.subprocess, "run", _fake_run):
            text = self._run(ls_remote=True, git_auth_order=["https"])
        self.assertIn("failed (not_found)", text)


class TestDiagnosePackageName(TestBase):
    """A package name resolves the way an update would.

    Retyping the URL by hand loses the url-map rule and the declared spelling
    -- which is exactly what is in question when a fetch fails."""

    def test_package_name_resolves_to_its_declared_url(self):
        self.mkFile("ivpm.yaml", textwrap.dedent("""\
            package:
              name: root
              dep-sets:
                - name: default-dev
                  deps:
                    - name: lib-example
                      url: %s
            """ % _URL))
        out = io.StringIO()
        with patch.object(utils, "gh_auth_available", lambda h: True), \
             redirect_stdout(out):
            CmdDiagnose()(_args(target="lib-example",
                                project_dir=self.testdir))
        text = out.getvalue()
        self.assertIn(_URL, text)

    def test_unknown_package_name_is_an_error(self):
        self.mkFile("ivpm.yaml", textwrap.dedent("""\
            package:
              name: root
              dep-sets:
                - name: default-dev
                  deps: []
            """))
        from ivpm.msg import SrcLoaderError
        out = io.StringIO()
        with self.assertRaises(SrcLoaderError):
            with redirect_stdout(out):
                CmdDiagnose()(_args(target="nope", project_dir=self.testdir))


if __name__ == "__main__":
    unittest.main()
