"""
Tests for PEP 517 build-requirement collection.

Python packages are installed with ``--no-build-isolation`` so each builds
against the workspace's editable packages instead of released copies from
PyPI.  The installer therefore does not provision ``[build-system] requires``,
and a package whose build backend is absent fails with ModuleNotFoundError.
``_collect_build_requires`` gathers those declarations so they can be
installed ahead of anything that needs building.
"""
import os
import sys
import tempfile
import unittest

from ivpm.content_attrib import OriginMap
from ivpm.handlers.package_handler_python import PackageHandlerPython


def _handler(workspace_pkgs=()):
    """A handler with just enough state for _collect_build_requires."""
    h = PackageHandlerPython.__new__(PackageHandlerPython)
    h.pkgs_info = {name: None for name in workspace_pkgs}
    # _collect_build_requires also records which package declared each
    # requirement, so that a backend that fails to install can be traced back
    # to the package that needs it.
    h._build_requires_src = {}
    return h


class TestCollectBuildRequires(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _pkg(self, name, toml=None):
        d = os.path.join(self.dir, name)
        os.makedirs(d, exist_ok=True)
        if toml is not None:
            with open(os.path.join(d, "pyproject.toml"), "w") as fp:
                fp.write(toml)
        return name

    def test_backend_requirement_is_collected(self):
        """The motivating case: a package using a non-setuptools backend."""
        self._pkg("dv-solve", """
[build-system]
requires = ["setuptools>=64", "wheel", "ivpm-build>=0.2.0"]
build-backend = "ivpm_build.backend"
""")
        got = _handler()._collect_build_requires(self.dir, ["dv-solve"])
        self.assertIn("ivpm-build>=0.2.0", got)

    def test_workspace_packages_are_skipped(self):
        """A build requirement that names a workspace package must not be
        resolved from PyPI -- the editable install supplies it, and a released
        copy would shadow the local source."""
        self._pkg("consumer", """
[build-system]
requires = ["setuptools", "zuspec-ir-core"]
""")
        got = _handler(["zuspec-ir-core"])._collect_build_requires(
            self.dir, ["consumer"])
        self.assertNotIn("zuspec-ir-core", got)
        self.assertIn("setuptools", got)

    def test_workspace_match_is_name_normalised(self):
        """PEP 503 folding: zuspec_ir_core and zuspec-ir-core are one name."""
        self._pkg("consumer", """
[build-system]
requires = ["Zuspec_IR.Core"]
""")
        got = _handler(["zuspec-ir-core"])._collect_build_requires(
            self.dir, ["consumer"])
        self.assertEqual([], got)

    def test_distinct_constraints_on_one_dist_are_all_kept(self):
        """Two floors for the same backend must both constrain the resolve,
        rather than one silently winning here."""
        self._pkg("a", '[build-system]\nrequires = ["setuptools>=64"]\n')
        self._pkg("b", '[build-system]\nrequires = ["setuptools>=70"]\n')
        got = _handler()._collect_build_requires(self.dir, ["a", "b"])
        self.assertIn("setuptools>=64", got)
        self.assertIn("setuptools>=70", got)

    def test_identical_specifiers_are_deduped(self):
        self._pkg("a", '[build-system]\nrequires = ["wheel"]\n')
        self._pkg("b", '[build-system]\nrequires = ["wheel"]\n')
        got = _handler()._collect_build_requires(self.dir, ["a", "b"])
        self.assertEqual(["wheel"], got)

    def test_missing_or_unparsable_sources_are_tolerated(self):
        """A package with no pyproject.toml, no build-system table, or an
        unparsable file must not abort the update -- the install itself
        reports real problems far more precisely."""
        self._pkg("no-toml")
        self._pkg("no-build-system", 'x = 1\n')
        self._pkg("broken", 'this is ]] not toml\n')
        self._pkg("good", '[build-system]\nrequires = ["ivpm-build"]\n')
        got = _handler()._collect_build_requires(
            self.dir,
            ["no-toml", "no-build-system", "broken", "good", "never-cloned"])
        self.assertEqual(["ivpm-build"], got)

    def test_non_string_and_blank_entries_ignored(self):
        self._pkg("odd", '[build-system]\nrequires = ["", "wheel", 42]\n')
        got = _handler()._collect_build_requires(self.dir, ["odd"])
        self.assertEqual(["wheel"], got)

    def test_build_system_not_a_table_is_ignored(self):
        self._pkg("odd", 'build-system = "nonsense"\n')
        self.assertEqual([], _handler()._collect_build_requires(self.dir, ["odd"]))

    def test_no_source_packages_yields_nothing(self):
        self.assertEqual([], _handler()._collect_build_requires(self.dir, []))



class TestDynamicBuildRequires(unittest.TestCase):
    """PEP 517 lets a backend ask for more at build time than it declares in
    ``[build-system] requires``. hatchling does this for editable builds
    ('editables'), and under --no-build-isolation nobody installs it unless
    IVPM asks the backend and installs the answer itself."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # A stand-in venv whose interpreter is the one running the tests.
        self.venv = os.path.join(self.dir, "venv")
        os.makedirs(os.path.join(self.venv, "bin"))
        os.symlink(sys.executable, os.path.join(self.venv, "bin", "python"))

    def _pkg(self, name, toml, backend_src=None):
        d = os.path.join(self.dir, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pyproject.toml"), "w") as fp:
            fp.write(toml)
        if backend_src is not None:
            with open(os.path.join(d, "fake_backend.py"), "w") as fp:
                fp.write(backend_src)
        return name

    def _intree(self, name, backend_src):
        return self._pkg(name, """
[build-system]
requires = ["setuptools"]
build-backend = "fake_backend"
backend-path = ["."]
""", backend_src)

    def _write(self, h, names, static=()):
        h._origins = OriginMap()
        path = os.path.join(self.dir, "reqs.txt")
        n = h._write_dynamic_build_requires(
            self.venv, self.dir, names, list(static), path)
        with open(path) as fp:
            lines = [l.strip() for l in fp
                     if l.strip() and not l.startswith("#")]
        self.assertEqual(n, len(lines))
        return lines

    def test_setuptools_and_default_backends_are_not_asked(self):
        """Their hook runs setup.py, which may import siblings not yet
        installed; and no build-backend means setuptools' legacy backend."""
        self._pkg("st", '[build-system]\nrequires = ["setuptools"]\n'
                        'build-backend = "setuptools.build_meta"\n')
        self._pkg("legacy", '[build-system]\nrequires = ["setuptools"]\n'
                            'build-backend = "setuptools.build_meta:__legacy__"\n')
        self._pkg("none", '[build-system]\nrequires = ["setuptools"]\n')
        self._pkg("hatch", '[build-system]\nrequires = ["hatchling"]\n'
                           'build-backend = "hatchling.build"\n')
        got = _handler()._dynamic_build_targets(
            self.dir, ["st", "legacy", "none", "hatch", "never-cloned"])
        self.assertEqual(["hatch"], [t[0] for t in got])
        self.assertEqual("hatchling.build", got[0][2])
        self.assertTrue(got[0][4])  # editable by default

    def test_backend_answer_is_collected(self):
        """The motivating case: hatchling asking for 'editables'."""
        self._intree("hatchlike", """
def get_requires_for_build_editable(config_settings=None):
    return ["editables~=0.3"]
""")
        h = _handler()
        self.assertEqual(["editables~=0.3"], self._write(h, ["hatchlike"]))
        self.assertEqual("hatchlike", h._build_requires_src["editables~=0.3"])

    def test_workspace_and_static_requirements_are_filtered(self):
        self._intree("p", """
def get_requires_for_build_editable(config_settings=None):
    return ["setuptools", "Zuspec_IR.Core", "editables"]
""")
        got = self._write(_handler(["zuspec-ir-core"]), ["p"],
                          static=["setuptools"])
        self.assertEqual(["editables"], got)

    def test_backend_output_is_not_mistaken_for_the_answer(self):
        self._intree("noisy", """
print("backend chatter")
def get_requires_for_build_editable(config_settings=None):
    print("more chatter")
    return ["editables"]
""")
        self.assertEqual(["editables"], self._write(_handler(), ["noisy"]))

    def test_backend_without_the_hook_needs_nothing(self):
        self._intree("bare", "x = 1\n")
        self.assertEqual([], self._write(_handler(), ["bare"]))

    def test_backend_that_cannot_load_is_tolerated(self):
        """Typically a backend provided by a workspace package that is not
        installed yet; the build reports that better than we can here."""
        self._pkg("broken", '[build-system]\nrequires = ["nope"]\n'
                            'build-backend = "no_such_backend_mod"\n')
        self._intree("ok", """
def get_requires_for_build_editable(config_settings=None):
    return ["editables"]
""")
        self.assertEqual(["editables"],
                         self._write(_handler(), ["broken", "ok"]))


if __name__ == "__main__":
    unittest.main()
