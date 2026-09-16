#****************************************************************************
#* test_python_floor.py
#*
#* Copyright 2024 Matthew Ballance and Contributors
#*
#* Licensed under the Apache License, Version 2.0 (the "License"); you may
#* not use this file except in compliance with the License.
#* You may obtain a copy of the License at:
#*
#*   http://www.apache.org/licenses/LICENSE-2.0
#*
#* Unless required by applicable law or agreed to in writing, software
#* distributed under the License is distributed on an "AS IS" BASIS,
#* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#* See the License for the specific language governing permissions and
#* limitations under the License.
#*
#****************************************************************************
"""Guards on the Python version floor IVPM promises.

ivpm 2.33.0 shipped `glob.glob(..., root_dir=)` and
`importlib.metadata.entry_points(group=)`, both 3.10-only, with no
`requires-python`. pip installed it happily under cp39 and it then died at
runtime, inside someone else's build. Neither call is wrong on a modern
interpreter, which is exactly why review did not catch either one.

These tests are cheap source-level guards: the authoritative check is the CI
version matrix in .github/workflows/ci.yml, which actually runs the suite on
every supported interpreter. These just fail in the developer's own run,
minutes rather than a release later.
"""
import os
import re
import unittest

import ivpm

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


SRC_DIR = os.path.dirname(os.path.abspath(ivpm.__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SRC_DIR))

#: The floor, spelled once. Update all three places together, or not at all:
#: this constant, `requires-python` in pyproject.toml, and the CI matrix.
FLOOR = (3, 9)


def _iter_sources():
    for dirpath, dirnames, filenames in os.walk(SRC_DIR):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__",)]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


class TestPythonFloor(unittest.TestCase):

    def _pyproject(self):
        path = os.path.join(REPO_ROOT, "pyproject.toml")
        if not os.path.isfile(path):
            self.skipTest("pyproject.toml not available (installed package)")
        with open(path, "rb") as fp:
            return tomllib.load(fp)

    def test_requires_python_is_declared(self):
        """Without this, pip installs into a too-old interpreter and says nothing."""
        requires = self._pyproject()["project"].get("requires-python")
        self.assertIsNotNone(
            requires, "pyproject.toml [project] must declare requires-python")
        self.assertEqual(requires, ">=%d.%d" % FLOOR)

    def test_glob_root_dir_is_not_used_directly(self):
        """`glob.glob(root_dir=)` is 3.10+. Use ivpm._compat.glob_rel."""
        offenders = [p for p in _iter_sources()
                     if os.path.basename(p) != "_compat.py"
                     and re.search(r"glob\([^)]*root_dir\s*=", open(p).read())]
        self.assertEqual(
            [], sorted(os.path.relpath(p, SRC_DIR) for p in offenders),
            "glob(root_dir=) requires Python 3.10; use ivpm._compat.glob_rel")

    def test_entry_points_group_is_not_used_directly(self):
        """`entry_points(group=)` is 3.10+. Use ivpm._compat.entry_points.

        On 3.9 the argument-less `entry_points()` returns a group -> list dict
        instead, so passing `group=` is a TypeError rather than a wrong answer.
        """
        # Only direct reaches into the stdlib are flagged; `entry_points(group=)`
        # on the symbol imported from ivpm._compat is the correct spelling and
        # is what every registry uses.
        pat = re.compile(r"importlib\.metadata\.entry_points\(\s*group\s*=")
        offenders = []
        for path in _iter_sources():
            if os.path.basename(path) == "_compat.py":
                continue
            lines = open(path).read().splitlines()
            for i, line in enumerate(lines):
                if not (pat.search(line)
                        or "from importlib.metadata import entry_points" in line):
                    continue
                # An inline `sys.version_info >= (3, 10)` branch is the one
                # legitimate spelling: the script pushed into a MANAGED VENV
                # cannot import ivpm._compat, so it carries its own guard.
                guard = "\n".join(lines[max(0, i - 3):i])
                if "version_info" in guard:
                    continue
                offenders.append(os.path.relpath(path, SRC_DIR))
        self.assertEqual(
            [], sorted(offenders),
            "entry_points(group=) requires Python 3.10; "
            "use ivpm._compat.entry_points")

    def test_ci_matrix_covers_the_floor(self):
        """The declared floor is only real if a CI row actually runs on it.

        Checked in BOTH workflows: either forge can hold release authority (the
        dvkit.org switch decides), and the one that publishes is the one whose
        matrix has to cover the floor.
        """
        for workflow_dir in (".github", ".forgejo"):
            path = os.path.join(REPO_ROOT, workflow_dir, "workflows", "ci.yml")
            if not os.path.isfile(path):
                self.skipTest("workflows not available (installed package)")
            with open(path) as fp:
                workflow = fp.read()
            # Read as text rather than parsed YAML: pyyaml is a runtime
            # dependency but the matrix is a single flat list, and a regex keeps
            # this test from caring about the rest of the workflow's shape.
            m = re.search(r"python-version:\s*\[([^\]]*)\]", workflow)
            self.assertIsNotNone(
                m, "no python-version matrix in %s/workflows/ci.yml" % workflow_dir)
            versions = [(int(a), int(b))
                        for a, b in re.findall(r"(\d+)\.(\d+)", m.group(1))]
            self.assertIn(
                FLOOR, versions,
                "%s matrix does not test the declared requires-python floor"
                % workflow_dir)

    def test_publish_waits_on_the_matrix(self):
        """A release must not be able to go out with a red matrix row.

        The gate is a `needs:` edge, which is the only construct in Actions that
        makes one job wait on another. Asserted as text for the same reason as
        above -- and because the failure this guards against is someone removing
        the edge, which no amount of YAML structure would make self-evident.
        """
        for workflow_dir in (".github", ".forgejo"):
            path = os.path.join(REPO_ROOT, workflow_dir, "workflows", "ci.yml")
            if not os.path.isfile(path):
                self.skipTest("workflows not available (installed package)")
            import yaml
            with open(path) as fp:
                jobs = yaml.safe_load(fp)["jobs"]
            self.assertIn("publish-pypi", jobs,
                          "%s: publishing must be its own job, so that `needs` "
                          "can gate it" % workflow_dir)
            needs = jobs["publish-pypi"].get("needs") or []
            if isinstance(needs, str):
                needs = [needs]
            self.assertIn("python-matrix", needs,
                          "%s: publish-pypi does not wait on the Python matrix"
                          % workflow_dir)


if __name__ == "__main__":
    unittest.main()
