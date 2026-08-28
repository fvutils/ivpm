#****************************************************************************
#* test_install_merge.py
#*
#* Tests for the N-way manifest merge behind 'ivpm install': package collision
#* detection and its two escapes, root-scoped config merging, ordering, and
#* the ${IVPM_PROJECT} policy. Pure unit tests -- no subprocess, no network.
#****************************************************************************
import os
import sys
import unittest
from unittest import mock

_UNIT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(os.path.dirname(_UNIT_DIR))
sys.path.insert(0, os.path.join(_ROOT_DIR, "src"))

from ivpm.env_spec import EnvSpec
from ivpm.install_merge import (
    LoadedSource, MERGED_DEP_SET, apply_project_ref_policy, merge_packages,
    merge_root_config, merge_sources, package_identity)
from ivpm.install_spec import SourceSpec
from ivpm.package import Package
from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import NodeConfig, ProjInfo, PythonConfig, VenvMode
from ivpm.yamlsrc import SrcLoaderError


def _pkg(name, **kw):
    p = Package(name)
    p.src_type = kw.pop("src_type", "git")
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _source(alias, packages, **proj_kw):
    """Build a LoadedSource with *packages* and optional root-scoped config."""
    ds = PackagesInfo("default")
    for p in packages:
        ds.add_package(p)
    pi = ProjInfo(is_src=False)
    pi.name = alias
    for k, v in proj_kw.items():
        setattr(pi, k, v)
    pi.set_dep_set("default", ds)
    return LoadedSource(spec=SourceSpec(src="%s.yaml" % alias, alias=alias),
                        proj_info=pi, dep_set=ds, dep_set_names=["default"])


class TestPackageCollisions(unittest.TestCase):

    def test_identical_package_deduped(self):
        a = _source("edapack", [_pkg("verilator", url="git://v", version="5.020")])
        b = _source("corp", [_pkg("verilator", url="git://v", version="5.020")])
        with mock.patch("ivpm.install_merge.warning") as warn:
            merged, _ = merge_packages([a, b])
        self.assertEqual(list(merged), ["verilator"])
        self.assertFalse(warn.called, "an identical declaration is not a collision")

    def test_package_collision_errors(self):
        a = _source("edapack", [_pkg("verilator", url="git://v", version="5.020")])
        b = _source("corp", [_pkg("verilator", url="git://v", version="5.028")])
        with self.assertRaises(SrcLoaderError) as ctx:
            merge_packages([a, b])
        msg = str(ctx.exception)
        self.assertIn("verilator", msg)
        self.assertIn("edapack", msg)
        self.assertIn("corp", msg)

    def test_error_message_lists_both_escapes(self):
        """A collision the tool refuses to resolve costs as much as a silent one."""
        a = _source("edapack", [_pkg("verilator", version="5.020")])
        b = _source("corp", [_pkg("verilator", version="5.028")])
        with self.assertRaises(SrcLoaderError) as ctx:
            merge_packages([a, b])
        msg = str(ctx.exception)
        self.assertIn("--resolve verilator=corp", msg)
        self.assertIn("--on-collision=last-wins", msg)

    def test_resolve_selects_source(self):
        a = _source("edapack", [_pkg("verilator", version="5.020")])
        b = _source("corp", [_pkg("verilator", version="5.028")])
        merged, used = merge_packages([a, b], resolutions={"verilator": "corp"})
        self.assertEqual(merged["verilator"].version, "5.028")
        self.assertEqual(used, {"verilator": "corp"})

    def test_resolve_takes_definition_wholesale(self):
        """No field-level merging: a half-merged definition is untested by anyone."""
        a = _source("edapack", [_pkg("v", version="5.020", url="git://a", branch="main")])
        b = _source("corp", [_pkg("v", version="5.028", url="git://b")])
        merged, _ = merge_packages([a, b], resolutions={"v": "corp"})
        self.assertEqual(merged["v"].url, "git://b")
        self.assertFalse(getattr(merged["v"], "branch", None))

    def test_resolve_stale_warns(self):
        a = _source("edapack", [_pkg("verilator", version="5.020")])
        b = _source("corp", [_pkg("verilator", version="5.020")])
        with mock.patch("ivpm.install_merge.warning") as warn:
            merge_packages([a, b], resolutions={"verilator": "corp"})
        self.assertTrue(warn.called)
        self.assertIn("no effect", warn.call_args[0][0])

    def test_resolve_unknown_package_errors(self):
        a = _source("edapack", [_pkg("verilator")])
        with self.assertRaises(SrcLoaderError) as ctx:
            merge_packages([a], resolutions={"nosuch": "edapack"})
        self.assertIn("no source provides", str(ctx.exception))

    def test_resolve_unknown_alias_errors(self):
        a = _source("edapack", [_pkg("verilator")])
        with self.assertRaises(SrcLoaderError) as ctx:
            merge_packages([a], resolutions={"verilator": "nosuch"})
        self.assertIn("unknown source", str(ctx.exception))

    def test_on_collision_first_and_last_wins(self):
        a = _source("edapack", [_pkg("verilator", version="5.020")])
        b = _source("corp", [_pkg("verilator", version="5.028")])
        with mock.patch("ivpm.install_merge.warning"):
            first, _ = merge_packages([a, b], on_collision="first-wins")
            last, _ = merge_packages([a, b], on_collision="last-wins")
        self.assertEqual(first["verilator"].version, "5.020")
        self.assertEqual(last["verilator"].version, "5.028")

    def test_on_collision_still_warns(self):
        """The policy says how to break a tie, not that ties are unremarkable."""
        a = _source("edapack", [_pkg("verilator", version="5.020")])
        b = _source("corp", [_pkg("verilator", version="5.028")])
        with mock.patch("ivpm.install_merge.warning") as warn:
            merge_packages([a, b], on_collision="last-wins")
        self.assertTrue(warn.called)
        self.assertIn("verilator", warn.call_args[0][0])

    def test_package_order_follows_from_order(self):
        a = _source("edapack", [_pkg("alpha"), _pkg("beta")])
        b = _source("corp", [_pkg("gamma")])
        merged, _ = merge_packages([a, b])
        self.assertEqual(list(merged), ["alpha", "beta", "gamma"])


class TestPackageIdentity(unittest.TestCase):

    def test_srcinfo_and_path_ignored(self):
        """Two catalogs declaring the same dep differ in file/line; that is
        not a difference in the package."""
        a = _pkg("v", version="1.0")
        b = _pkg("v", version="1.0")
        a.srcinfo, b.srcinfo = object(), object()
        a.path, b.path = "/x", "/y"
        self.assertEqual(package_identity(a), package_identity(b))

    def test_version_difference_detected(self):
        self.assertNotEqual(package_identity(_pkg("v", version="1.0")),
                            package_identity(_pkg("v", version="2.0")))


class TestRootConfigMerge(unittest.TestCase):

    def test_env_settings_concatenated_in_order(self):
        a = _source("edapack", [], env_settings=[
            EnvSpec("PATH", "/a", EnvSpec.Act.PathPrepend)])
        b = _source("corp", [], env_settings=[
            EnvSpec("PATH", "/b", EnvSpec.Act.PathPrepend)])
        root = merge_root_config([a, b])
        self.assertEqual([e.val for e in root["env_settings"]], ["/a", "/b"])

    def test_matching_configs_merge_silently(self):
        cfg = PythonConfig(venv=VenvMode.AUTO)
        a = _source("edapack", [], python_config=cfg)
        b = _source("corp", [], python_config=PythonConfig(venv=VenvMode.AUTO))
        root = merge_root_config([a, b])
        self.assertEqual(root["python_config"], cfg)

    def test_scalar_conflict_errors(self):
        a = _source("edapack", [], node_config=NodeConfig(manager="npm"))
        b = _source("corp", [], node_config=NodeConfig(manager="pnpm"))
        with self.assertRaises(SrcLoaderError) as ctx:
            merge_root_config([a, b])
        msg = str(ctx.exception)
        self.assertIn("manager", msg)
        self.assertIn("edapack", msg)
        self.assertIn("corp", msg)

    def test_scalar_conflict_says_resolve_does_not_apply(self):
        """--resolve is keyed by package name; it cannot address a config key."""
        a = _source("edapack", [], node_config=NodeConfig(manager="npm"))
        b = _source("corp", [], node_config=NodeConfig(manager="pnpm"))
        with self.assertRaises(SrcLoaderError) as ctx:
            merge_root_config([a, b])
        self.assertIn("--on-collision=last-wins", str(ctx.exception))
        self.assertIn("--resolve", str(ctx.exception))

    def test_scalar_conflict_policy_applies(self):
        a = _source("edapack", [], node_config=NodeConfig(manager="npm"))
        b = _source("corp", [], node_config=NodeConfig(manager="pnpm"))
        with mock.patch("ivpm.install_merge.warning"):
            root = merge_root_config([a, b], on_collision="last-wins")
        self.assertEqual(root["node_config"].manager, "pnpm")

    def test_handler_configs_recursive_merge(self):
        a = _source("edapack", [], handler_configs={"fusesoc": {"import": "all"}})
        b = _source("corp", [], handler_configs={"fusesoc": {"update-conf": True},
                                                 "direnv": {"x": 1}})
        root = merge_root_config([a, b])
        self.assertEqual(root["handler_configs"]["fusesoc"],
                         {"import": "all", "update-conf": True})
        self.assertEqual(root["handler_configs"]["direnv"], {"x": 1})

    def test_handler_config_leaf_conflict_errors(self):
        a = _source("edapack", [], handler_configs={"fusesoc": {"import": "all"}})
        b = _source("corp", [], handler_configs={"fusesoc": {"import": "none"}})
        with self.assertRaises(SrcLoaderError):
            merge_root_config([a, b])

    def test_deps_mode_defaults_to_flatten(self):
        """A tool directory is a flat tree of tools."""
        root = merge_root_config([_source("edapack", [])])
        self.assertEqual(root["deps_mode"], "flatten")

    def test_deps_mode_conflict_errors(self):
        a = _source("edapack", [], deps_mode="flatten")
        b = _source("corp", [], deps_mode="nested")
        with self.assertRaises(SrcLoaderError):
            merge_root_config([a, b])


class TestProjectRefPolicy(unittest.TestCase):

    def _env(self):
        return [EnvSpec("CFG", "${IVPM_PROJECT}/etc", EnvSpec.Act.Set)]

    def test_project_ref_errors_by_default(self):
        with self.assertRaises(SrcLoaderError) as ctx:
            apply_project_ref_policy(self._env(), "/opt/eda")
        self.assertIn("IVPM_PROJECT", str(ctx.exception))

    def test_project_ref_expand(self):
        with mock.patch("ivpm.install_merge.warning") as warn:
            out = apply_project_ref_policy(self._env(), "/opt/eda", "expand")
        self.assertEqual(out[0].val, "/opt/eda/etc")
        self.assertTrue(warn.called)

    def test_project_ref_drop(self):
        with mock.patch("ivpm.install_merge.warning") as warn:
            out = apply_project_ref_policy(self._env(), "/opt/eda", "drop")
        self.assertEqual(out, [])
        self.assertTrue(warn.called)

    def test_ivpm_packages_ref_allowed(self):
        """IVPM_PACKAGES stays valid in a tool directory -- it IS the outdir."""
        env = [EnvSpec("CFG", "${IVPM_PACKAGES}/etc", EnvSpec.Act.Set)]
        out = apply_project_ref_policy(env, "/opt/eda")
        self.assertEqual(out[0].val, "${IVPM_PACKAGES}/etc")

    def test_unadorned_reference_also_caught(self):
        env = [EnvSpec("CFG", "$IVPM_PROJECT/etc", EnvSpec.Act.Set)]
        with self.assertRaises(SrcLoaderError):
            apply_project_ref_policy(env, "/opt/eda")


class TestMergeSources(unittest.TestCase):

    def test_synthesized_root(self):
        a = _source("edapack", [_pkg("verilator")])
        b = _source("corp", [_pkg("yosys")])
        with mock.patch("ivpm.install_merge.note"):
            pi, used = merge_sources([a, b], outdir="/opt/eda")
        self.assertEqual(pi.deps_dir, ".")
        self.assertEqual(pi.default_dep_set, MERGED_DEP_SET)
        self.assertEqual(list(pi.get_dep_set(MERGED_DEP_SET).packages),
                         ["verilator", "yosys"])
        self.assertEqual(used, {})

    def test_no_sources_errors(self):
        with self.assertRaises(SrcLoaderError):
            merge_sources([], outdir="/opt/eda")


if __name__ == "__main__":
    unittest.main()
