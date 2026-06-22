#****************************************************************************
#* test_git_progress.py
#****************************************************************************
"""Tests for git progress parsing/streaming used by the update TUI."""
import os
import subprocess
import tempfile

from ivpm.git_progress import parse_progress_line, run_git_with_progress


def test_parse_progress_line_phases():
    assert parse_progress_line(
        "Receiving objects:  42% (518/1234), 1.20 MiB | 2.40 MiB/s"
    ) == "Receiving objects 42%"
    assert parse_progress_line(
        "remote: Counting objects:  50% (617/1234)"
    ) == "Counting objects 50%"
    assert parse_progress_line(
        "Resolving deltas: 100% (789/789), done."
    ) == "Resolving deltas 100%"


def test_parse_progress_line_non_progress():
    assert parse_progress_line("Cloning into 'foo'...") is None
    assert parse_progress_line("remote: Enumerating objects: 32, done.") is None
    assert parse_progress_line("") is None


def _make_src_repo(root):
    src = os.path.join(root, "src")
    subprocess.run(["git", "init", "-q", src], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", src, "config", k, v], check=True)
    for i in range(20):
        with open(os.path.join(src, "f%d.txt" % i), "w") as fp:
            fp.write("data %d\n" % i)
    subprocess.run(["git", "-C", src, "add", "-A"], check=True)
    subprocess.run(["git", "-C", src, "commit", "-qm", "init"], check=True)
    return src


def test_run_git_with_progress_streams_and_dedups():
    with tempfile.TemporaryDirectory() as root:
        src = _make_src_repo(root)
        dst = os.path.join(root, "dst")
        msgs = []
        rc = run_git_with_progress(
            ["git", "clone", "file://" + src, dst, "--progress"],
            on_progress=msgs.append)
        assert rc == 0
        assert os.path.isdir(os.path.join(dst, ".git"))
        # We should have received some progress, and never two identical
        # messages back-to-back (percentage-change throttling).
        assert any(m.startswith("Receiving objects") for m in msgs)
        for a, b in zip(msgs, msgs[1:]):
            assert a != b


def test_run_git_with_progress_no_callback():
    with tempfile.TemporaryDirectory() as root:
        src = _make_src_repo(root)
        dst = os.path.join(root, "dst")
        # Should run cleanly even with no on_progress callback.
        rc = run_git_with_progress(
            ["git", "clone", "file://" + src, dst, "--progress"])
        assert rc == 0
