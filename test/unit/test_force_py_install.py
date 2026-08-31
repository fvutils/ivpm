"""
Unit tests for --force-py-install and the IVPM self-install guard.

Covers:
  FP01  force=True reinstalls, scoped to the distributions the phase names
  FP02  force=False leaves the installer command untouched
  FP03  update_info.force_py_install reaches _install_requirements
  FP04  A workspace that resolves 'ivpm' itself does not also inject the
        site-config IVPM install spec
  FP05  Editable requirements resolve to their declared distribution name
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.handlers.package_handler_python import (
    PackageHandlerPython,
    _project_name_at,
)
from ivpm.project_ops_info import ProjectUpdateInfo

from .test_base import TestBase


def _make_handler():
    h = PackageHandlerPython()
    h.reset()
    return h


def _write_reqs(testdir, lines, name="reqs.txt"):
    path = os.path.join(testdir, name)
    with open(path, "w") as fp:
        fp.write("\n".join(lines) + "\n")
    return path


def _write_project(testdir, dirname, dist_name):
    """Create a minimal editable-installable project; return its path."""
    path = os.path.join(testdir, dirname)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "pyproject.toml"), "w") as fp:
        fp.write('[project]\nname = "%s"\nversion = "0.1"\n' % dist_name)
    return path


def _capture_cmd(handler, reqs_path, **kwargs):
    """Call _install_requirements with the subprocess layer stubbed out.

    Returns the list of argvs that would have been executed.
    """
    seen = []

    def fake_run(cmd, env, stdout_arg, stderr_arg, use_uv, task, cwd=None):
        seen.append(list(cmd))
        return 0, []

    with patch.object(handler, "_run_with_progress", side_effect=fake_run):
        with patch("ivpm.handlers.package_handler_python.shutil.which",
                   return_value="/usr/bin/uv"):
            with patch("ivpm.handlers.package_handler_python.get_venv_python",
                       return_value="/venv/bin/python"):
                handler._install_requirements(
                    "/venv", reqs_path, False, **kwargs)

    return seen


def _reinstall_pkgs(cmd):
    """Extract the values of every --reinstall-package flag in an argv."""
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "--reinstall-package"]


# ---------------------------------------------------------------------------
# FP01/FP02 — the reinstall reaches the installer, scoped to this phase
# ---------------------------------------------------------------------------

class TestFP01ReinstallFlag(TestBase):

    def test_FP01a_uv_force_marks_named_requirements(self):
        reqs = _write_reqs(self.testdir, ["Sphinx", "pyyaml>=6.0"])
        cmd = _capture_cmd(_make_handler(), reqs, use_uv=True, force=True)[0]
        self.assertEqual(["sphinx", "pyyaml"], _reinstall_pkgs(cmd))

    def test_FP01b_uv_force_never_uses_a_blanket_reinstall(self):
        """A blanket --reinstall drags in transitive workspace-only deps."""
        reqs = _write_reqs(self.testdir, ["Sphinx"])
        cmd = _capture_cmd(_make_handler(), reqs, use_uv=True, force=True)[0]
        self.assertNotIn("--reinstall", cmd)

    def test_FP01c_pip_force_adds_a_scoped_second_pass(self):
        reqs = _write_reqs(self.testdir, ["Sphinx"])
        cmds = _capture_cmd(_make_handler(), reqs, use_uv=False, force=True)
        self.assertEqual(2, len(cmds), "expected a resolve pass and a force pass")
        self.assertNotIn("--force-reinstall", cmds[0])
        self.assertIn("--force-reinstall", cmds[1])
        self.assertIn("--no-deps", cmds[1])

    def test_FP02a_uv_without_force_has_no_reinstall(self):
        reqs = _write_reqs(self.testdir, ["Sphinx"])
        cmd = _capture_cmd(_make_handler(), reqs, use_uv=True, force=False)[0]
        self.assertNotIn("--reinstall", cmd)
        self.assertEqual([], _reinstall_pkgs(cmd))

    def test_FP02b_pip_without_force_runs_a_single_pass(self):
        reqs = _write_reqs(self.testdir, ["Sphinx"])
        cmds = _capture_cmd(_make_handler(), reqs, use_uv=False, force=False)
        self.assertEqual(1, len(cmds))
        self.assertNotIn("--force-reinstall", cmds[0])

    def test_FP02c_force_defaults_to_off(self):
        """Omitting force entirely must behave as force=False."""
        reqs = _write_reqs(self.testdir, ["Sphinx"])
        cmd = _capture_cmd(_make_handler(), reqs, use_uv=True)[0]
        self.assertEqual([], _reinstall_pkgs(cmd))


# ---------------------------------------------------------------------------
# FP03 — the flag is threaded through from the update info
# ---------------------------------------------------------------------------

class TestFP03FlagPropagation(TestBase):

    def _run_post_load(self, force_py_install):
        """Drive on_root_post_load far enough to reach the install call."""
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)
        # Pre-create the marker so the "already installed" branch is taken;
        # that is the branch force_py_install is supposed to punch through.
        with open(os.path.join(deps_dir, "python_pkgs_1.txt"), "w") as fp:
            fp.write("somepkg\n")
        os.makedirs(os.path.join(deps_dir, "python"), exist_ok=True)

        args = MagicMock()
        args.suppress_output = True
        args.py_skip_install = False
        args.py_uv = True
        args.py_pip = False
        args.py_prerls_packages = False
        args.py_system_site_packages = False
        args.force_py_install = force_py_install

        ui = ProjectUpdateInfo(
            args=args,
            deps_dir=deps_dir,
            project_dir=self.testdir,
            suppress_output=True,
        )
        ui.force_py_install = force_py_install

        handler = _make_handler()
        handler.pypi_pkg_s.add("somepkg")
        handler.pkgs_info["somepkg"] = MagicMock(name="somepkg", src_type="pypi")

        calls = []

        def fake_install(*a, **kw):
            calls.append(kw)

        with patch("ivpm.handlers.package_handler_python.setup_venv"):
            with patch.object(handler, "_push_entrypoint_agent_dirs"):
                with patch.object(handler, "_install_requirements",
                                  side_effect=fake_install):
                    handler.on_root_post_load(ui)

        return calls

    def test_FP03a_force_true_is_passed_down(self):
        calls = self._run_post_load(True)
        self.assertTrue(calls, "expected _install_requirements to be called")
        for kw in calls:
            self.assertTrue(kw.get("force"),
                            "force=True must reach _install_requirements")

    def test_FP03b_force_false_short_circuits(self):
        """Without the flag the already-installed marker skips install entirely."""
        calls = self._run_post_load(False)
        self.assertEqual([], calls)


# ---------------------------------------------------------------------------
# FP04 — do not install a second IVPM over the workspace's own
# ---------------------------------------------------------------------------

class TestFP04IvpmSelfInstallGuard(TestBase):

    def _ivpm_args_for(self, pypi=(), src=()):
        """Return handler._ivpm_install_args after the guard has run."""
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)

        args = MagicMock()
        args.suppress_output = True
        args.py_skip_install = True
        args.py_uv = False
        args.py_pip = False
        args.force_py_install = False

        ui = ProjectUpdateInfo(
            args=args,
            deps_dir=deps_dir,
            project_dir=self.testdir,
            suppress_output=True,
            skip_venv=True,
        )

        handler = _make_handler()
        for p in pypi:
            handler.pypi_pkg_s.add(p)
        for p in src:
            handler.src_pkg_s.add(p)

        site_config = MagicMock()
        site_config.get_ivpm_install_args.return_value = ["ivpm"]

        with patch("ivpm.site_config.get_site_config", return_value=site_config):
            with patch("ivpm.handlers.package_handler_python.setup_venv"):
                with patch.object(handler, "_push_entrypoint_agent_dirs"):
                    with patch.object(handler, "_install_requirements"):
                        handler.on_root_post_load(ui)

        return getattr(handler, "_ivpm_install_args", None)

    def test_FP04a_src_ivpm_suppresses_site_config_spec(self):
        """packages/ivpm is installed editable later -- do not inject PyPI ivpm."""
        self.assertFalse(self._ivpm_args_for(src=["ivpm"]))

    def test_FP04b_pypi_ivpm_suppresses_site_config_spec(self):
        self.assertFalse(self._ivpm_args_for(pypi=["ivpm"]))

    def test_FP04c_no_ivpm_dep_injects_site_config_spec(self):
        self.assertEqual(["ivpm"], self._ivpm_args_for(src=["otherpkg"]))


# ---------------------------------------------------------------------------
# FP05 — editable requirements resolve to their declared distribution name
# ---------------------------------------------------------------------------

class TestFP05EditableTargets(TestBase):

    def test_FP05a_editable_resolves_via_pyproject(self):
        path = _write_project(self.testdir, "zuspec-dataclasses", "zuspec-dataclasses")
        reqs = _write_reqs(self.testdir, ["-e %s" % path])
        targets = _make_handler()._reinstall_targets(reqs)
        self.assertEqual(["zuspec-dataclasses"], targets)

    def test_FP05b_dist_name_may_differ_from_directory_name(self):
        """The install keys on distribution name, so the directory is not enough."""
        path = _write_project(self.testdir, "some-checkout-dir", "actual_dist_name")
        reqs = _write_reqs(self.testdir, ["-e %s" % path])
        targets = _make_handler()._reinstall_targets(reqs)
        self.assertEqual(["actual-dist-name"], targets)

    def test_FP05c_editable_resolves_via_setup_cfg(self):
        path = os.path.join(self.testdir, "cfg-pkg")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "setup.cfg"), "w") as fp:
            fp.write("[metadata]\nname = cfg_pkg\n")
        reqs = _write_reqs(self.testdir, ["-e %s" % path])
        self.assertEqual(["cfg-pkg"], _make_handler()._reinstall_targets(reqs))

    def test_FP05d_unnameable_editable_is_dropped_not_guessed(self):
        path = os.path.join(self.testdir, "opaque-pkg")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "setup.py"), "w") as fp:
            fp.write("from setuptools import setup\nsetup()\n")
        reqs = _write_reqs(self.testdir, ["-e %s" % path])
        self.assertEqual([], _make_handler()._reinstall_targets(reqs))
        self.assertIsNone(_project_name_at(path))

    def test_FP05e_comments_blanks_and_options_are_ignored(self):
        reqs = _write_reqs(self.testdir, [
            "# a comment",
            "",
            "--index-url https://example.invalid/simple",
            "Sphinx",
        ])
        self.assertEqual(["sphinx"], _make_handler()._reinstall_targets(reqs))

    def test_FP05f_duplicates_are_collapsed(self):
        reqs = _write_reqs(self.testdir, ["setuptools>=64", "setuptools>=77", "setuptools"])
        self.assertEqual(["setuptools"], _make_handler()._reinstall_targets(reqs))

    def test_FP05g_missing_file_is_not_fatal(self):
        missing = os.path.join(self.testdir, "does-not-exist.txt")
        self.assertEqual([], _make_handler()._reinstall_targets(missing))

    def test_FP05h_mixed_phase_resolves_every_editable(self):
        """The real phase-4 shape: many editables, all of which must be marked."""
        names = ["zuspec-ir-core", "zuspec-rt-core", "dv-flow-mgr"]
        lines = ["-e %s" % _write_project(self.testdir, n, n) for n in names]
        reqs = _write_reqs(self.testdir, lines)
        self.assertEqual(names, _make_handler()._reinstall_targets(reqs))


if __name__ == "__main__":
    unittest.main()
