#****************************************************************************
#* test_install_cmd.py
#*
#* End-to-end tests for 'ivpm install': the shared tool-directory layout (the
#* outdir IS the deps-dir), multi-source merging, the lock's install record,
#* and replay. Uses local 'src: dir' packages and --py-skip-install, so no
#* network access is required.
#****************************************************************************
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

_UNIT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(os.path.dirname(_UNIT_DIR))
_SRC_DIR = os.path.join(_ROOT_DIR, "src")
_PYTHON = os.path.join(_ROOT_DIR, "packages", "python", "bin", "python3")
if not os.path.exists(_PYTHON):
    _PYTHON = sys.executable
_ENV = {**os.environ, "PYTHONPATH": _SRC_DIR}


def _run(*args, cwd, check=True):
    cmd = [_PYTHON, "-m", "ivpm"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, env=_ENV, cwd=cwd)
    if check and r.returncode != 0:
        raise AssertionError(
            f"{cmd} failed (rc={r.returncode})\nstdout:{r.stdout}\nstderr:{r.stderr}")
    return r.stdout, r.returncode, r.stderr


class TestInstallCmd(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.pkgs = os.path.join(self.tmp, "pkgsrc")
        os.makedirs(self.pkgs)
        for name in ("verilator", "yosys", "gtkwave"):
            d = os.path.join(self.pkgs, name)
            os.makedirs(d)
            with open(os.path.join(d, "README"), "w") as f:
                f.write(name)
        self.outdir = os.path.join(self.tmp, "eda")

    def tearDown(self):
        self._tmp.cleanup()

    def _catalog(self, fname, pkg_name, dep_sets):
        """Write a catalog manifest. *dep_sets* is {set-name: [pkg-names]}."""
        lines = ["package:", "  name: %s" % pkg_name, "  dep-sets:"]
        for ds, names in dep_sets.items():
            lines.append("    - name: %s" % ds)
            lines.append("      deps:")
            for n in names:
                lines.append("        - name: %s" % n)
                lines.append("          src: dir")
                lines.append("          url: file://%s" % os.path.join(self.pkgs, n))
        path = os.path.join(self.tmp, fname)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def _lock(self):
        with open(os.path.join(self.outdir, "package-lock.json")) as f:
            return json.load(f)

    #-----------------------------------------------------------------------
    # Layout
    #-----------------------------------------------------------------------

    def test_layout_is_deps_dir(self):
        cat = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", cat, cwd=self.tmp)
        for entry in ("package-lock.json", "ivpm.json", "verilator"):
            self.assertTrue(os.path.exists(os.path.join(self.outdir, entry)),
                            "%s should be directly in the outdir" % entry)
        # No intervening 'packages' level.
        self.assertFalse(os.path.isdir(os.path.join(self.outdir, "packages")))

    def test_nothing_written_above_outdir(self):
        cat = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        before = set(os.listdir(self.tmp))
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", cat, cwd=self.tmp)
        self.assertEqual(set(os.listdir(self.tmp)) - before, {"eda"})

    def test_no_project_scoped_dirs(self):
        """The agents handler is project-scoped, so it must not run here."""
        cat = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", cat, cwd=self.tmp)
        for d in (".agents", ".claude", ".cursor", "fusesoc.conf"):
            self.assertFalse(os.path.exists(os.path.join(self.outdir, d)),
                             "%s is project-scoped and must not be created" % d)

    #-----------------------------------------------------------------------
    # Sources
    #-----------------------------------------------------------------------

    def test_two_sources_merge(self):
        a = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        b = self._catalog("b.yaml", "corp", {"common": ["yosys"]})
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", a, "--from", b, "-d", "common", cwd=self.tmp)
        self.assertTrue(os.path.isdir(os.path.join(self.outdir, "verilator")))
        self.assertTrue(os.path.isdir(os.path.join(self.outdir, "yosys")))

    def test_per_source_dep_set_selection(self):
        """The motivating case: take two sets from one source, not a third."""
        a = self._catalog("a.yaml", "edapack", {
            "digital-sim": ["verilator"],
            "digital-formal": ["yosys"],
            "openroad": ["gtkwave"]})
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", a, "-d", "digital-sim", "-d", "digital-formal",
             cwd=self.tmp)
        self.assertTrue(os.path.isdir(os.path.join(self.outdir, "verilator")))
        self.assertTrue(os.path.isdir(os.path.join(self.outdir, "yosys")))
        self.assertFalse(os.path.isdir(os.path.join(self.outdir, "gtkwave")))

    def test_outdir_required(self):
        cat = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        out, rc, err = _run("install", "--from", cat, cwd=self.tmp, check=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("outdir", out + err)

    def test_mistyped_per_source_option_errors(self):
        cat = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        out, rc, err = _run("install", "-o", self.outdir, "--from", cat,
                            "--depset", "default", cwd=self.tmp, check=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("unknown option", out + err)

    #-----------------------------------------------------------------------
    # Collisions
    #-----------------------------------------------------------------------

    def test_collision_errors_with_both_escapes(self):
        a = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        # Same package name, different url -> a genuine disagreement.
        b = os.path.join(self.tmp, "b.yaml")
        with open(b, "w") as f:
            f.write(textwrap.dedent("""
                package:
                  name: corp
                  dep-sets:
                    - name: default
                      deps:
                        - name: verilator
                          src: dir
                          url: file://%s
            """ % os.path.join(self.pkgs, "yosys")))
        out, rc, err = _run("install", "-o", self.outdir, "--py-skip-install",
                            "--from", a, "--from", b, cwd=self.tmp, check=False)
        self.assertNotEqual(rc, 0)
        msg = out + err
        self.assertIn("--resolve verilator=corp", msg)
        self.assertIn("--on-collision", msg)

    def test_resolve_escape_works(self):
        a = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        b = os.path.join(self.tmp, "b.yaml")
        with open(b, "w") as f:
            f.write(textwrap.dedent("""
                package:
                  name: corp
                  dep-sets:
                    - name: default
                      deps:
                        - name: verilator
                          src: dir
                          url: file://%s
            """ % os.path.join(self.pkgs, "yosys")))
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--resolve", "verilator=corp", "--from", a, "--from", b,
             cwd=self.tmp)
        # corp's definition points at the yosys tree.
        with open(os.path.join(self.outdir, "verilator", "README")) as f:
            self.assertEqual(f.read(), "yosys")

    #-----------------------------------------------------------------------
    # Lock record and replay
    #-----------------------------------------------------------------------

    def test_lock_records_sources_ordered(self):
        a = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        b = self._catalog("b.yaml", "corp", {"common": ["yosys"]})
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", a, "--from", b, "-d", "common", cwd=self.tmp)
        lock = self._lock()
        self.assertEqual(lock["install_mode"], "toolchain")
        self.assertEqual([s["from"] for s in lock["sources"]], [a, b])
        self.assertEqual([s["as"] for s in lock["sources"]], ["edapack", "corp"])
        self.assertEqual(lock["sources"][1]["dep_sets"], ["common"])

    def test_bare_replay(self):
        a = self._catalog("a.yaml", "edapack", {
            "digital-sim": ["verilator"], "openroad": ["gtkwave"]})
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", a, "-d", "digital-sim", cwd=self.tmp)
        before = self._lock()["sources"]
        _run("install", "-o", self.outdir, "--py-skip-install", cwd=self.tmp)
        self.assertEqual(self._lock()["sources"], before)
        self.assertFalse(os.path.isdir(os.path.join(self.outdir, "gtkwave")))

    def test_bare_install_without_record_errors(self):
        out, rc, err = _run("install", "-o", self.outdir, cwd=self.tmp,
                            check=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("--from", out + err)

    def test_replay_reports_diff_on_change(self):
        a = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        b = self._catalog("b.yaml", "corp", {"common": ["yosys"]})
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", a, cwd=self.tmp)
        out, _, err = _run("install", "-o", self.outdir, "--py-skip-install",
                           "--from", a, "--from", b, "-d", "common",
                           cwd=self.tmp)
        msg = out + err
        self.assertIn("Replacing the recorded install spec", msg)
        self.assertIn(b, msg)

    def test_status_works_in_installed_tree(self):
        """Phase 1 integration: the outdir is recognized as a deps-dir."""
        a = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", a, cwd=self.tmp)
        _run("status", cwd=self.outdir)

    def test_destroy_deps_only_keeps_the_outdir(self):
        """The deps-dir is the root here, so --deps-only must remove the tool
        directory's *contents*, never the directory the user pointed at."""
        a = self._catalog("a.yaml", "edapack", {"default": ["verilator"]})
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", a, cwd=self.tmp)
        _run("destroy", "--deps-only", "-y", "-p", self.outdir, cwd=self.tmp)
        self.assertTrue(os.path.isdir(self.outdir))
        self.assertFalse(os.path.isdir(os.path.join(self.outdir, "verilator")))

    #-----------------------------------------------------------------------
    # --root-var
    #-----------------------------------------------------------------------

    def _envrc(self):
        with open(os.path.join(self.outdir, "packages.envrc")) as f:
            return f.read()

    def _envrc_catalog(self, fname="a.yaml"):
        """A catalog whose package publishes an export.envrc, so the direnv
        handler actually writes packages.envrc."""
        with open(os.path.join(self.pkgs, "verilator", "export.envrc"), "w") as f:
            f.write("# nothing\n")
        return self._catalog(fname, "edapack", {"default": ["verilator"]})

    def test_default_exports_ivpm_packages(self):
        cat = self._envrc_catalog()
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", cat, cwd=self.tmp)
        self.assertIn("export IVPM_PACKAGES=%s\n" % self.outdir, self._envrc())

    def test_root_var_names_the_directory(self):
        cat = self._envrc_catalog()
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--root-var", "TOOLS_ROOT", "--from", cat, cwd=self.tmp)
        content = self._envrc()
        self.assertIn("export TOOLS_ROOT=%s\n" % self.outdir, content)
        # IVPM_PACKAGES stays defined, as an alias, so manifests referencing it
        # keep resolving.
        self.assertIn("export IVPM_PACKAGES=${TOOLS_ROOT}\n", content)
        self.assertEqual(self._lock()["root_var"], "TOOLS_ROOT")

    def test_root_var_replayed(self):
        cat = self._envrc_catalog()
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--root-var", "TOOLS_ROOT", "--from", cat, cwd=self.tmp)
        _run("install", "-o", self.outdir, "--py-skip-install", cwd=self.tmp)
        self.assertIn("export TOOLS_ROOT=", self._envrc())
        self.assertEqual(self._lock()["root_var"], "TOOLS_ROOT")

    def test_root_var_sticks_across_a_respecified_install(self):
        """Re-running with --from but no --root-var keeps the recorded name:
        consumers reference it, so losing it silently would break them."""
        cat = self._envrc_catalog()
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--root-var", "TOOLS_ROOT", "--from", cat, cwd=self.tmp)
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--from", cat, cwd=self.tmp)
        self.assertIn("export TOOLS_ROOT=", self._envrc())

    def test_root_var_ivpm_packages_restores_the_default(self):
        cat = self._envrc_catalog()
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--root-var", "TOOLS_ROOT", "--from", cat, cwd=self.tmp)
        _run("install", "-o", self.outdir, "--py-skip-install",
             "--root-var", "IVPM_PACKAGES", "--from", cat, cwd=self.tmp)
        content = self._envrc()
        self.assertNotIn("TOOLS_ROOT", content)
        self.assertIn("export IVPM_PACKAGES=%s\n" % self.outdir, content)
        self.assertNotIn("root_var", self._lock())

    def test_root_var_rejects_non_identifier(self):
        cat = self._envrc_catalog()
        out, rc, err = _run("install", "-o", self.outdir, "--py-skip-install",
                            "--root-var", "tools-root", "--from", cat,
                            cwd=self.tmp, check=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("--root-var", out + err)

    def test_root_var_rejects_ivpm_project(self):
        cat = self._envrc_catalog()
        out, rc, err = _run("install", "-o", self.outdir, "--py-skip-install",
                            "--root-var", "IVPM_PROJECT", "--from", cat,
                            cwd=self.tmp, check=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("IVPM_PROJECT", out + err)

    def test_cli_help_lists_install(self):
        out, _, _ = _run("--help", cwd=self.tmp)
        self.assertIn("install", out)


if __name__ == "__main__":
    unittest.main()
