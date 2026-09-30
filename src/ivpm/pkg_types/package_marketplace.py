#****************************************************************************
#* package_marketplace.py
#*
#* Copyright 2026 Matthew Ballance and Contributors
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
"""
``src: marketplace`` -- plugins selected by name from a plugin marketplace.

::

    deps:
      - name: claude-official
        src: marketplace
        url: anthropics/claude-plugins-official
        plugins: [code-review, "lsp-*"]

A marketplace is a catalog (``marketplace.json``) that names plugins and says
where each one's source is.  This source reads the catalog, selects plugins
by name, and contributes one ordinary dependency per selected plugin -- git,
or http for an archive -- so every plugin is fetched through the usual cache
and pinned in ``package-lock.json`` like any other dependency.  The agents
handler then projects them into each tool.

How the marketplace itself is fetched depends on ``url``:

========================================  ====================================
``url``                                   the marketplace is
========================================  ====================================
git URL, or ``owner/repo`` (GitHub)       a git package (``PackageMarketplaceGit``)
local directory or catalog file           a directory package (``PackageMarketplaceDir``)
``https://.../marketplace.json``          a virtual node, like ``src: ivpm.yaml``
                                          (``PackageMarketplaceUrl``)
========================================  ====================================

A git or directory marketplace *is* a package: plugins listed with a relative
source live inside it, and are found there through its ``agents_config``.
A catalog fetched from a URL has no directory, so it may only list plugins
with remote sources.

Everything the agents handler needs -- which plugin paths to load, and the
marketplace entry to overlay on each manifest -- travels in ``agents_config``
on the packages, and so into the lock file, which keeps lock reproduction
independent of this module.
"""
import io
import json
import logging
import os
import re
import subprocess
import dataclasses as dc
from typing import Any, Dict, List, Optional

from .package_dir import PackageDir
from .package_git import PackageGit
from .package_ivpm_yaml import PackageIvpmYaml
from ..project_ops_info import ProjectUpdateInfo
from ..utils import fatal, getlocstr, note

_logger = logging.getLogger("ivpm.pkg_types.package_marketplace")

#: The synthetic dep-set a marketplace contributes its plugins through.
DEP_SET = "marketplace"

#: Options this source adds to those of the source it delegates to.
_OPTIONS = {"plugins", "marketplace-file"}

#: Entry fields that describe the listing, not the plugin. They are dropped
#: when an entry stands in for a plugin's manifest.
_LISTING_KEYS = ("source", "strict", "category", "tags", "relevance",
                 "headers", "headersHelper")

_GITHUB_SHORTHAND = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class _MarketplaceMixin(object):
    """Catalog handling shared by every way of fetching a marketplace."""

    #: Selection patterns, as authored
    mkt_plugins: List[str] = None
    #: Catalog path relative to the marketplace root, when not the default
    mkt_file: Optional[str] = None
    #: Marketplace name from the catalog, once read
    mkt_name: Optional[str] = None

    @classmethod
    def dep_keys(cls):
        return super().dep_keys() | _OPTIONS

    def _mkt_options(self, opts, si):
        plugins = opts.get("plugins")
        if isinstance(plugins, str):
            plugins = [plugins]
        if not isinstance(plugins, list) or not plugins \
                or not all(isinstance(p, str) and p for p in plugins):
            fatal("Package '%s' (src: marketplace) requires 'plugins:', a list of "
                  "plugin names or glob patterns @ %s" % (self.name, getlocstr(opts)))
        self.mkt_plugins = [str(p) for p in plugins]
        if "marketplace-file" in opts:
            self.mkt_file = str(opts["marketplace-file"])

    def _mkt_expand(self, update_info: ProjectUpdateInfo, root_dir: Optional[str],
                    catalog_path: Optional[str]):
        """Read the catalog and return the ProjInfo contributing its plugins."""
        from ..agent_plugins import marketplace as mk

        if catalog_path is None:
            where = self.mkt_file or " or ".join(mk.CATALOG_LOCATIONS)
            fatal("Package '%s' (src: marketplace): no catalog (%s) in %s @ %s"
                  % (self.name, where, root_dir, getlocstr(self)))

        mkt = mk.load(catalog_path, root_dir)
        self._mkt_report(mkt.diagnostics)
        self.mkt_name = mkt.name

        chosen, diags = mk.select(mkt, self.mkt_plugins)
        self._mkt_report(diags)

        relative = [e for e in chosen if e.source_kind == mk.RELATIVE]
        if relative and root_dir is None:
            fatal("Package '%s' (src: marketplace): plugin(s) %s are stored inside "
                  "the marketplace, which cannot be fetched from a catalog URL. "
                  "Point 'url' at the marketplace repository instead @ %s"
                  % (self.name, ", ".join(e.name for e in relative), getlocstr(self)))
        for entry in relative:
            if not mk.within_root(mkt, entry):
                fatal("Package '%s' (src: marketplace): plugin '%s' resolves outside "
                      "the marketplace @ %s" % (self.name, entry.name, getlocstr(self)))

        # The marketplace package offers exactly the selected relative plugins
        # and nothing else it happens to contain -- in particular not every
        # other plugin the auto-probe would find under plugins/*/.
        cfg = dict(self.agents_config or {})
        cfg["plugins"] = [mk.plugin_path(e) for e in relative]
        cfg["plugin_manifests"] = {mk.plugin_path(e): _overlay(e) for e in relative}
        cfg.setdefault("skills", [])
        self.agents_config = cfg

        deps = []
        classify = _RefClassifier()
        for entry in chosen:
            if entry.source_kind == mk.RELATIVE:
                continue
            opts, ddiags = mk.to_dep(entry, classify)
            self._mkt_report(ddiags)
            # A plugin is not an IVPM project; do not go looking for its deps.
            opts["deps"] = "skip"
            opts["agents"]["plugin_manifests"] = {mk.plugin_path(entry): _overlay(entry)}
            deps.append(opts)

        note("Marketplace %s: %d plugin(s) selected (%d inside it, %d fetched separately)"
             % (mkt.name, len(chosen), len(relative), len(deps)))
        return self._mkt_proj_info(deps)

    def _mkt_proj_info(self, deps: List[Dict[str, Any]]):
        """Build the contributed dep-set by reading it as a manifest.

        Going through the reader (rather than constructing packages by hand)
        gives the contributed deps exactly the treatment a hand-written
        dependency gets: option validation, 'agents:' capture, defaults.
        JSON is valid YAML, so the manifest is written as JSON.
        """
        from ..ivpm_yaml_reader import IvpmYamlReader

        doc = {"package": {
            "name": self.name,
            "dep-sets": [{"name": DEP_SET, "deps": deps}],
        }}
        label = "<marketplace %s>" % (self.mkt_name or self.name)
        proj = IvpmYamlReader().read(io.StringIO(json.dumps(doc, indent=2)), label)
        origin = "%s#%s" % (getattr(self, "url", None) or self.name, self.mkt_name)
        for leaf in proj.get_dep_set(DEP_SET).packages.values():
            leaf.from_marketplace = origin
        self.dep_set = DEP_SET
        return proj

    def _mkt_report(self, diags):
        for d in diags:
            if d.severity == "error":
                fatal("Package '%s' (src: marketplace): %s @ %s"
                      % (self.name, d.message, getlocstr(self)))
            elif d.severity == "warning":
                _logger.warning("marketplace %s: %s", self.name, d.message)
            else:
                _logger.debug("marketplace %s: %s", self.name, d.message)


def _overlay(entry) -> Dict[str, Any]:
    """What the handler overlays on the plugin's manifest (see discovery)."""
    fields = {k: v for k, v in entry.fields.items() if k not in _LISTING_KEYS}
    return {"strict": entry.strict, "fields": fields}


class _RefClassifier(object):
    """Tell a branch from a tag for a marketplace git ``ref``.

    IVPM takes ``branch:`` and ``tag:`` separately; a marketplace says only
    ``ref``.  ``git ls-remote`` answers which it is.  If it cannot (offline, no
    such ref), the ref is treated as a branch and git reports the problem when
    the plugin is fetched.
    """

    def __call__(self, url: str, ref: str) -> str:
        try:
            out = subprocess.run(
                ["git", "ls-remote", "--heads", "--tags", url,
                 "refs/heads/" + ref, "refs/tags/" + ref],
                capture_output=True, text=True, timeout=60,
                env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        except (OSError, subprocess.SubprocessError):
            return "branch"
        refs = [line.split("\t", 1)[-1] for line in out.stdout.splitlines()]
        if "refs/heads/" + ref not in refs and any(
                r.startswith("refs/tags/" + ref) for r in refs):
            return "tag"
        return "branch"


# ---------------------------------------------------------------------------
# The three ways of fetching a marketplace
# ---------------------------------------------------------------------------

@dc.dataclass
class PackageMarketplaceGit(_MarketplaceMixin, PackageGit):
    """A marketplace kept in a git repository."""

    def process_options(self, opts, si):
        super().process_options(opts, si)
        self._mkt_options(opts, si)

    def update(self, update_info: ProjectUpdateInfo):
        # The repository's own ivpm.yaml, if any, is not consulted: this
        # dependency asked for plugins, not for the repository's deps.
        super().update(update_info)
        from ..agent_plugins import marketplace as mk
        return self._mkt_expand(update_info, self.path,
                                mk.find_catalog(self.path, self.mkt_file))


@dc.dataclass
class PackageMarketplaceDir(_MarketplaceMixin, PackageDir):
    """A marketplace in a local directory, linked like ``src: dir``."""

    def process_options(self, opts, si):
        super().process_options(opts, si)
        self._mkt_options(opts, si)

    def update(self, update_info: ProjectUpdateInfo):
        super().update(update_info)
        from ..agent_plugins import marketplace as mk
        root = os.path.join(update_info.deps_dir, self.name)
        self.path = root.replace("\\", "/")
        return self._mkt_expand(update_info, root, mk.find_catalog(root, self.mkt_file))


@dc.dataclass
class PackageMarketplaceUrl(_MarketplaceMixin, PackageIvpmYaml):
    """A catalog fetched by URL. Virtual: it occupies no packages-dir slot."""

    def process_options(self, opts, si):
        super().process_options(opts, si)
        self.src_type = "marketplace"
        self._mkt_options(opts, si)

    def update(self, update_info: ProjectUpdateInfo):
        update_info.report_package(cacheable=True)
        local = self._fetch_yaml(update_info)
        return self._mkt_expand(update_info, None, local)

    def get_lock_entry(self):
        return {
            "src": "marketplace",
            "url": self.url,
            "plugins": self.mkt_plugins,
            "fingerprint": self.resolved_fingerprint,
            "reproducible": True,
            "virtual": True,
        }

    def spec_matches_lock(self, lock_entry):
        return (self.url == lock_entry.get("url")
                and self.mkt_plugins == lock_entry.get("plugins"))


# ---------------------------------------------------------------------------
# The registered source
# ---------------------------------------------------------------------------

class PackageMarketplace(object):
    """Factory for ``src: marketplace``: picks the fetcher from ``url``."""

    @staticmethod
    def create(name, opts, si):
        url = opts.get("url") if hasattr(opts, "get") else None
        if not isinstance(url, str) or not url:
            fatal("Package '%s' (src: marketplace) requires 'url:' @ %s"
                  % (name, getlocstr(opts)))

        opts = dict(opts)
        kind, url, catalog = _classify_url(url, si)
        opts["url"] = url
        if catalog is not None and "marketplace-file" not in opts:
            opts["marketplace-file"] = catalog

        cls = {"git": PackageMarketplaceGit, "dir": PackageMarketplaceDir,
               "url": PackageMarketplaceUrl}[kind]
        pkg = cls(name)
        pkg.process_options(opts, si)
        return pkg

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="marketplace",
            description="Plugins selected by name from a plugin marketplace "
                        "(marketplace.json). Each plugin becomes a dependency of "
                        "its own, fetched and locked like any other.",
            params=[
                ParamInfo("url", "Marketplace: git URL, GitHub owner/repo, local "
                          "directory or catalog file, or https URL of a "
                          "marketplace.json", required=True, type_hint="url"),
                ParamInfo("plugins", "Plugin names or glob patterns to install",
                          required=True, type_hint="list"),
                ParamInfo("marketplace-file", "Catalog path within the marketplace "
                          "(default: .claude-plugin/marketplace.json, "
                          ".agents/plugins/marketplace.json, marketplace.json)"),
                ParamInfo("branch", "Git marketplaces: branch to check out"),
                ParamInfo("tag", "Git marketplaces: tag to check out"),
                ParamInfo("commit", "Git marketplaces: commit to check out"),
            ],
        )


def _classify_url(url: str, si) -> tuple:
    """``(kind, url, catalog)`` for a marketplace ``url``.

    ``kind`` is 'git', 'dir' or 'url'; ``catalog`` is a catalog path relative
    to the marketplace root when ``url`` named a catalog file, else None.
    """
    from ..agent_plugins import marketplace as mk

    if url.startswith(("http://", "https://")):
        if url.split("?", 1)[0].endswith(".json"):
            if url.startswith("http://"):
                fatal("A marketplace catalog must be fetched over https: %s" % url)
            return ("url", url, None)
        return ("git", url, None)
    if url.startswith(("ssh://", "git://", "git@")) or url.endswith(".git"):
        return ("git", url, None)

    path = url[len("file://"):] if url.startswith("file://") else url
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path):
        base = None
        if si is not None and getattr(si, "filename", None):
            base = os.path.dirname(si.filename)
        path = os.path.join(base or os.getcwd(), path)

    if os.path.isfile(path):
        root = mk.root_for_catalog(path)
        return ("dir", "file://" + root, os.path.relpath(path, root))
    if os.path.isdir(path):
        return ("dir", "file://" + os.path.normpath(path), None)
    if not url.startswith("file://") and _GITHUB_SHORTHAND.match(url):
        return ("git", mk.github_url(url), None)
    fatal("Marketplace '%s' is neither a git URL, an owner/repo, nor an "
          "existing local path" % url)
