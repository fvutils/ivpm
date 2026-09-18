#****************************************************************************
#* test_git_diagnose.py
#****************************************************************************
"""Tests for the offline git-failure diagnosis (no network access)."""
import os
import subprocess
import tempfile

from ivpm.git_diagnose import (
    classify_git_failure, diagnose_git_failure, should_retry_other_transport,
    sso_authorization_url,
)
from ivpm.git_progress import run_git_with_progress

_URL = "https://github.com/zupec/zuspec-be-sw.git"

#: The verbatim shape of the stderr from the incident that motivated the SSO
#: classification: git reports a bare 403, and the actionable part (the
#: authorization URL) is buried in the remote's own message.
_SSO_STDERR = """\
remote: The 'example-org' organization has enabled or enforced SAML SSO. To access
remote: this repository, visit https://github.com/orgs/example-org/sso?authorization_request=ABC123xyz
remote: and try your request again.
fatal: unable to access 'https://github.com/example-org/lib-example.git/': The requested URL returned error: 403
"""

#: What a genuinely credential-less https clone reports instead.  Nearly
#: identical to the eye, opposite fix.
_NO_CRED_STDERR = """\
remote: Invalid username or token. Password authentication is not supported for Git operations.
fatal: Authentication failed for 'https://github.com/example-org/lib-example.git/'
"""


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


def test_sso_hint_carries_the_authorization_url():
    # One line resolves the whole failure: where to go to authorize.
    out = _one(_SSO_STDERR)
    assert "SAML SSO" in out
    assert ("https://github.com/orgs/example-org/sso?"
            "authorization_request=ABC123xyz") in out


def test_sso_url_extraction():
    assert sso_authorization_url(_SSO_STDERR).endswith("ABC123xyz")
    assert sso_authorization_url("no url here") is None


def test_sso_without_a_url_still_explains_the_authorization_step():
    out = _one("remote: the organization has enabled or enforced SAML SSO\n"
               "fatal: The requested URL returned error: 403")
    assert "SAML SSO" in out
    assert "authorization" in out


def test_invalid_username_or_token_is_not_the_sso_hint():
    # The valuable split: this one means "nothing reached git", and the SSO
    # remedy would send the user to authorize a token they never presented.
    out = _one(_NO_CRED_STDERR)
    assert "no usable credential" in out
    assert "SAML" not in out
    assert "get-urlmatch" in out


def test_scope_hint_names_gh_auth_refresh():
    out = _one("remote: Resource not accessible by personal access token\n"
               "fatal: unable to access ...: 403")
    assert "scope" in out
    assert "gh auth refresh" in out


def test_classify_labels():
    cases = [
        (_SSO_STDERR, "sso"),
        (_NO_CRED_STDERR, "no_credential"),
        ("remote: Resource not accessible by personal access token", "scope"),
        ("git@github.com: Permission denied (publickey).", "auth"),
        ("remote: Repository not found.", "not_found"),
        ("fatal: Remote branch nope not found in upstream origin", "ref_not_found"),
        ("fatal: Could not resolve host: githubb.com", "dns"),
        ("remote: Your account is suspended.", "suspended"),
        ("some totally novel git message", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ]
    for stderr, expected in cases:
        assert classify_git_failure(stderr) == expected, stderr


def test_retry_only_for_auth_shaped_failures():
    for stderr in (_SSO_STDERR, _NO_CRED_STDERR,
                   "git@github.com: Permission denied (publickey).",
                   "remote: Resource not accessible by personal access token"):
        assert should_retry_other_transport(stderr) is True, stderr
    for stderr in ("remote: Repository not found.",
                   "fatal: Remote branch nope not found in upstream origin",
                   "fatal: Could not resolve host: githubb.com",
                   "remote: Your account is suspended.",
                   "some totally novel git message", ""):
        assert should_retry_other_transport(stderr) is False, stderr


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
