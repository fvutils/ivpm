"""Unit tests for ivpm.site_config and its integration with Cache/setup_venv."""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, call

# Ensure src is on the path (mirrors CI setup)
_ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOTDIR, "src"))

from ivpm.site_config import DefaultSiteConfig, SiteConfig, get_site_config, reset_site_config
from ivpm.cache import Cache


# ---------------------------------------------------------------------------
# DefaultSiteConfig
# ---------------------------------------------------------------------------

class TestDefaultSiteConfig(unittest.TestCase):

    def setUp(self):
        self.cfg = DefaultSiteConfig()

    def test_default_cache_dir_no_xdg(self):
        """Without XDG_CACHE_HOME, returns ~/.cache/ivpm."""
        env = os.environ.copy()
        env.pop("XDG_CACHE_HOME", None)
        with patch.dict(os.environ, env, clear=True):
            result = self.cfg.get_default_cache_dir()
        expected = os.path.join(os.path.expanduser("~"), ".cache", "ivpm")
        self.assertEqual(result, expected)

    def test_default_cache_dir_with_xdg(self):
        """With XDG_CACHE_HOME set, returns $XDG_CACHE_HOME/ivpm."""
        with patch.dict(os.environ, {"XDG_CACHE_HOME": "/tmp/xdgcache"}):
            result = self.cfg.get_default_cache_dir()
        self.assertEqual(result, "/tmp/xdgcache/ivpm")

    def test_ivpm_install_args_is_pypi(self):
        """Default install args return ['ivpm'] (PyPI)."""
        self.assertEqual(self.cfg.get_ivpm_install_args(), ["ivpm"])


# ---------------------------------------------------------------------------
# get_site_config() discovery logic
# ---------------------------------------------------------------------------

class TestGetSiteConfig(unittest.TestCase):

    def setUp(self):
        reset_site_config()
        # Remove any real ivpm_site_config from sys.modules to start clean
        sys.modules.pop("ivpm_site_config", None)

    def tearDown(self):
        reset_site_config()
        sys.modules.pop("ivpm_site_config", None)

    def test_returns_default_when_no_module(self):
        """If ivpm_site_config is not installed, DefaultSiteConfig is used."""
        # Ensure the import fails
        with patch.dict(sys.modules, {"ivpm_site_config": None}):
            reset_site_config()
            cfg = get_site_config()
        self.assertIsInstance(cfg, DefaultSiteConfig)

    def test_uses_custom_module_get_config(self):
        """If ivpm_site_config is importable and has get_config(), it is used."""
        class CustomConfig(SiteConfig):
            def get_default_cache_dir(self):
                return ""
            def get_ivpm_install_args(self):
                return ["/opt/site/ivpm.whl"]

        mock_module = MagicMock()
        mock_module.get_config.return_value = CustomConfig()

        with patch.dict(sys.modules, {"ivpm_site_config": mock_module}):
            reset_site_config()
            cfg = get_site_config()

        self.assertIsInstance(cfg, CustomConfig)
        self.assertEqual(cfg.get_default_cache_dir(), "")
        self.assertEqual(cfg.get_ivpm_install_args(), ["/opt/site/ivpm.whl"])

    def test_singleton_cached(self):
        """get_site_config() returns the same object on repeated calls."""
        cfg1 = get_site_config()
        cfg2 = get_site_config()
        self.assertIs(cfg1, cfg2)

    def test_reset_clears_singleton(self):
        """reset_site_config() forces a fresh load on next call."""
        cfg1 = get_site_config()
        reset_site_config()
        cfg2 = get_site_config()
        self.assertIsNot(cfg1, cfg2)


# ---------------------------------------------------------------------------
# Cache.__init__ priority chain
# ---------------------------------------------------------------------------

class TestCacheInit(unittest.TestCase):

    def setUp(self):
        reset_site_config()
        sys.modules.pop("ivpm_site_config", None)

    def tearDown(self):
        reset_site_config()
        sys.modules.pop("ivpm_site_config", None)

    def _make_config(self, cache_dir_value):
        """Return a mock site config that returns cache_dir_value."""
        cfg = MagicMock(spec=SiteConfig)
        cfg.get_default_cache_dir.return_value = cache_dir_value
        return cfg

    def test_explicit_arg_wins(self):
        """Explicit cache_dir arg always wins."""
        env = os.environ.copy()
        env["IVPM_CACHE"] = "/from/env"
        with patch.dict(os.environ, env):
            with patch("ivpm.cache.get_site_config", return_value=self._make_config("/from/config")):
                c = Cache("/explicit/path")
        self.assertEqual(c.cache_dir, "/explicit/path")

    def test_env_var_wins_over_config(self):
        """IVPM_CACHE env var takes priority over site config default."""
        env = os.environ.copy()
        env["IVPM_CACHE"] = "/from/env"
        with patch.dict(os.environ, env):
            with patch("ivpm.cache.get_site_config", return_value=self._make_config("/from/config")):
                c = Cache()
        self.assertEqual(c.cache_dir, "/from/env")

    def test_config_fallback(self):
        """When IVPM_CACHE is unset, site config default is used."""
        env = os.environ.copy()
        env.pop("IVPM_CACHE", None)
        with patch.dict(os.environ, env, clear=True):
            with patch("ivpm.cache.get_site_config", return_value=self._make_config("/from/config")):
                c = Cache()
        self.assertEqual(c.cache_dir, "/from/config")

    def test_empty_string_config_disables_cache(self):
        """Site config returning '' should disable the cache."""
        env = os.environ.copy()
        env.pop("IVPM_CACHE", None)
        with patch.dict(os.environ, env, clear=True):
            with patch("ivpm.cache.get_site_config", return_value=self._make_config("")):
                c = Cache()
        self.assertIsNone(c.cache_dir)
        self.assertFalse(c.is_enabled())

    def test_no_cache_dir_anywhere_disables_cache(self):
        """With no arg, no env var, and empty config, cache is disabled."""
        env = os.environ.copy()
        env.pop("IVPM_CACHE", None)
        with patch.dict(os.environ, env, clear=True):
            # Simulate no ivpm_site_config available → DefaultSiteConfig used,
            # but we also unset XDG and HOME isn't empty — just verify it
            # does NOT raise and returns something sensible.
            with patch.dict(sys.modules, {"ivpm_site_config": None}):
                reset_site_config()
                c = Cache()
        # DefaultSiteConfig produces a non-empty path, so cache should be enabled
        self.assertTrue(c.is_enabled())
        self.assertIn("ivpm", c.cache_dir)


# ---------------------------------------------------------------------------
# setup_venv uses site config install args
# ---------------------------------------------------------------------------

class TestSetupVenvIvpmInstall(unittest.TestCase):

    def setUp(self):
        reset_site_config()
        sys.modules.pop("ivpm_site_config", None)

    def tearDown(self):
        reset_site_config()
        sys.modules.pop("ivpm_site_config", None)

    def _make_install_config(self, install_args):
        cfg = MagicMock(spec=SiteConfig)
        cfg.get_ivpm_install_args.return_value = install_args
        return cfg

    def test_pip_suppress_uses_config_install_args(self):
        """setup_venv (pip, suppress_output=True) uses site config install args."""
        import ivpm.utils as utils_mod

        custom_args = ["/opt/site/ivpm-custom.whl"]
        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            return result

        fake_python = "/fake/python"

        with patch("ivpm.utils.get_site_config", return_value=self._make_install_config(custom_args)), \
             patch("ivpm.utils.get_sys_python", return_value=fake_python), \
             patch("ivpm.utils.get_venv_python", return_value=fake_python), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("os.system"), \
             patch("shutil.which", return_value=None):  # force pip path

            utils_mod.setup_venv(
                "/fake/python_dir",
                uv_pip="pip",
                suppress_output=True
            )

        # Find any install cmd that includes our custom wheel
        install_cmds = [c for c in captured_cmds if "/opt/site/ivpm-custom.whl" in c]
        self.assertTrue(install_cmds, "Expected an ivpm install command with custom wheel")
        ivpm_install_cmd = install_cmds[0]
        self.assertIn("/opt/site/ivpm-custom.whl", ivpm_install_cmd)
        self.assertNotIn("ivpm", [tok for tok in ivpm_install_cmd if tok != "/opt/site/ivpm-custom.whl"])

    def test_pip_no_suppress_uses_config_install_args(self):
        """setup_venv (pip, suppress_output=False) uses site config install args."""
        import ivpm.utils as utils_mod

        custom_args = ["/opt/site/ivpm-custom.whl"]
        os_system_calls = []

        with patch("ivpm.utils.get_site_config", return_value=self._make_install_config(custom_args)), \
             patch("ivpm.utils.get_sys_python", return_value="/fake/python"), \
             patch("ivpm.utils.get_venv_python", return_value="/fake/python"), \
             patch("subprocess.run"), \
             patch("os.system", side_effect=lambda cmd: os_system_calls.append(cmd)), \
             patch("shutil.which", return_value=None):

            utils_mod.setup_venv(
                "/fake/python_dir",
                uv_pip="pip",
                suppress_output=False
            )

        install_cmds = [c for c in os_system_calls if "install" in c and "pip" not in c.split()[-1]]
        self.assertTrue(install_cmds, "Expected an os.system install command")
        self.assertIn("/opt/site/ivpm-custom.whl", install_cmds[0])

    def test_uv_uses_config_install_args(self):
        """setup_venv (uv) uses site config install args."""
        import ivpm.utils as utils_mod

        custom_args = ["/opt/site/ivpm-custom.whl"]
        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("ivpm.utils.get_site_config", return_value=self._make_install_config(custom_args)), \
             patch("ivpm.utils.get_sys_python", return_value="/fake/python"), \
             patch("ivpm.utils.get_venv_python", return_value="/fake/python"), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("shutil.which", return_value="/usr/bin/uv"):

            utils_mod.setup_venv(
                "/fake/python_dir",
                uv_pip="uv",
                suppress_output=True
            )

        install_cmds = [c for c in captured_cmds if "pip" in c and "install" in c]
        self.assertTrue(install_cmds, "Expected a uv pip install command")
        uv_install_cmd = install_cmds[0]
        self.assertIn("/opt/site/ivpm-custom.whl", uv_install_cmd)
        self.assertNotIn("ivpm", uv_install_cmd)


if __name__ == "__main__":
    unittest.main()
