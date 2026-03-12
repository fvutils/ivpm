import os
import subprocess
import unittest


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PYTHON = os.path.join(_ROOT, "packages", "python", "bin", "python3")
_SRC = os.path.join(_ROOT, "src")
_ENV = {**os.environ, "PYTHONPATH": _SRC}


def _run(*args):
    result = subprocess.run(
        [_PYTHON, "-m", "ivpm"] + list(args),
        capture_output=True,
        text=True,
        env=_ENV,
        check=False)
    return result


def _usage_commands(stdout):
    usage_lines = stdout.splitlines()
    start = next(i for i, line in enumerate(usage_lines) if line.startswith("usage:"))

    commands = []
    for line in usage_lines[start + 1:]:
        if not line.startswith(" "):
            break
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        chunk = stripped
        if chunk.endswith("..."):
            chunk = chunk[:-3].rstrip()
        commands.extend(chunk[1:-1].split(","))

    return commands


class TestCliHelp(unittest.TestCase):

    def test_deprecated_commands_hidden_from_top_level_help(self):
        result = _run("--help")

        self.assertEqual(result.returncode, 0)
        self.assertNotIn("git-status", result.stdout)
        self.assertNotIn("git-update", result.stdout)
        self.assertIn("status", result.stdout)
        self.assertIn("sync", result.stdout)

    def test_visible_commands_are_alphabetized_in_top_level_help(self):
        result = _run("--help")

        self.assertEqual(result.returncode, 0)
        commands = _usage_commands(result.stdout)

        self.assertEqual(commands, sorted(commands))

    def test_cache_subcommands_are_alphabetized_in_help(self):
        result = _run("cache", "--help")

        self.assertEqual(result.returncode, 0)
        commands = _usage_commands(result.stdout)

        self.assertEqual(commands, sorted(commands))

    def test_show_subcommands_are_alphabetized_in_help(self):
        result = _run("show", "--help")

        self.assertEqual(result.returncode, 0)
        commands = _usage_commands(result.stdout)

        self.assertEqual(commands, sorted(commands))
