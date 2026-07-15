#****************************************************************************
#* test_git_diagnose.py
#****************************************************************************
"""Tests for the offline git-failure diagnosis (no network access)."""
import os
import subprocess
import tempfile

from ivpm.git_diagnose import diagnose_git_failure
from ivpm.git_progress import run_git_with_progress

_URL = "https://github.com/zupec/zuspec-be-sw.git"


def _one(stderr, url=_URL, ssh_pref=None):
    return "\n".join(diagnose_git_failure(url, stderr, ssh_pref))


def test_repo_not_found_breaks_down_github_url():
    hints = diagnose_git_failure(
        _URL, "remote: Repository not found.\nfatal: repository not found")
    assert any("does not exist" in h for h in hints)
    # The parsed owner/repo are echoed so a typo in a component stands out.
    assert any("owner='zupec'" in h and "repo='zuspec-be-sw'" in h for h in hints)


def test_suspended_account_is_not_a_url_problem():
    out = _one("remote: Your account is suspended.\n"
               "fatal: unable to access ...: The requested URL returned error: 403")
    assert "suspended" in out
    assert "provider-side" in out


def test_auth_failure_ssh_hint():
    out = _one("git@github.com: Permission denied (publickey).\n"
               "fatal: Could not read from remote repository.",
               url="git@github.com:zupec/zuspec-be-sw.git")
    assert "refused authentication" in out
    assert "ssh -T git@github.com" in out


def test_branch_not_found_wins_over_generic_not_found():
    out = _one("fatal: Remote branch nope not found in upstream origin")
    assert "branch/tag/commit was not found" in out
    assert "repository does not exist" not in out


def test_dns_failure():
    out = diagnose_git_failure(
        "https://githubb.com/o/r.git",
        "fatal: unable to access: Could not resolve host: githubb.com")
    assert any("could not resolve host" in h and "githubb.com" in h for h in out)


def test_unrecognized_stderr_yields_no_hints():
    assert diagnose_git_failure(_URL, "some totally novel git message") == []


def test_diagnose_does_no_network(monkeypatch):
    # Guard against regressions that reintroduce provider probing: any attempt
    # to open a socket during diagnosis must fail the test.
    import socket

    def _boom(*a, **k):
        raise AssertionError("diagnosis attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    diagnose_git_failure(_URL, "remote: Repository not found.\nfatal: not found")


def test_run_git_with_progress_captures_stderr_tail():
    # A failing clone surfaces git's own stderr via the capture sink.
    with tempfile.TemporaryDirectory() as root:
        dst = os.path.join(root, "dst")
        sink = []
        rc = run_git_with_progress(
            ["git", "clone", os.path.join(root, "does-not-exist"), dst],
            stderr_sink=sink)
        assert rc != 0
        assert any("does-not-exist" in ln or "repository" in ln.lower()
                   for ln in sink)
