#****************************************************************************
#* marketplace.py
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
"""Reading plugin marketplace catalogs (``marketplace.json``).

A marketplace names plugins and says where each one's source is.  The format
read here is Claude Code's, which Copilot CLI, VS Code and Codex also accept::

    {
      "name": "acme-tools",
      "owner": {"name": "Acme"},
      "metadata": {"pluginRoot": "./plugins"},
      "plugins": [
        {"name": "formatter", "source": "formatter"},
        {"name": "linter", "source": {"source": "github", "repo": "acme/linter"}}
      ]
    }

This module only parses and maps.  It never fetches: ``to_dep`` turns an entry
into the options of an ordinary IVPM dependency, and the ``marketplace``
package source (and ``ivpm show plugins --from``) do the fetching through the
existing git and http sources, cache and lock.

``command`` sources are rejected outright: they run an arbitrary program to
produce the plugin, which is not something a dependency resolver should do.
"""
import dataclasses as dc
import fnmatch
import json
import os
import posixpath
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .manifest import SPEC_VERSIONS, Diagnostic, within
from . import validate as _v

#: Where a marketplace keeps its catalog, relative to the marketplace root,
#: in lookup order.
CATALOG_LOCATIONS = (
    os.path.join(".claude-plugin", "marketplace.json"),
    os.path.join(".agents", "plugins", "marketplace.json"),
    "marketplace.json",
)

#: Source kinds, as normalised by this module.
RELATIVE = "relative"
GITHUB = "github"
GIT = "git"
GIT_SUBDIR = "git-subdir"
ARCHIVE = "archive"
NPM = "npm"
COMMAND = "command"
UNKNOWN = "unknown"

#: Source kinds IVPM can install.
SUPPORTED_KINDS = (RELATIVE, GITHUB, GIT, GIT_SUBDIR, ARCHIVE)

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dc.dataclass(frozen=True)
class MarketplaceEntry:
    """One plugin listed by a marketplace."""
    name: str
    #: One of the source-kind constants above
    source_kind: str
    #: Normalised source fields: 'path' (relative, git-subdir), 'url', 'ref',
    #: 'sha', 'sha256', 'package', 'version' -- whichever apply
    source: Mapping[str, Any]
    #: False: the entry *is* the plugin's manifest
    strict: bool = True
    description: str = ""
    version: Optional[str] = None
    category: str = ""
    #: Every field of the entry as written, for the manifest overlay
    fields: Mapping[str, Any] = dc.field(default_factory=dict)

    @property
    def supported(self) -> bool:
        return self.source_kind in SUPPORTED_KINDS


@dc.dataclass(frozen=True)
class Marketplace:
    name: str
    #: Directory relative sources resolve against; None for a catalog read
    #: from a URL, which has no directory
    root_dir: Optional[str]
    catalog_path: Optional[str]
    entries: Tuple[MarketplaceEntry, ...] = ()
    description: str = ""
    diagnostics: Tuple[Diagnostic, ...] = ()

    def entry(self, name: str) -> Optional[MarketplaceEntry]:
        for e in self.entries:
            if e.name == name:
                return e
        return None


# ---------------------------------------------------------------------------
# Locating and loading
# ---------------------------------------------------------------------------

def find_catalog(root_dir: str, override: Optional[str] = None) -> Optional[str]:
    """The catalog file of the marketplace rooted at ``root_dir``, or None.

    ``override`` is a path relative to ``root_dir`` naming the catalog
    explicitly (the ``marketplace-file:`` dep option).
    """
    if override:
        path = os.path.join(root_dir, override)
        return path if os.path.isfile(path) else None
    for rel in CATALOG_LOCATIONS:
        path = os.path.join(root_dir, rel)
        if os.path.isfile(path):
            return path
    return None


def root_for_catalog(catalog_path: str) -> str:
    """The marketplace root implied by a catalog file's own location."""
    parent = os.path.dirname(os.path.abspath(catalog_path))
    if os.path.basename(parent) == ".claude-plugin":
        return os.path.dirname(parent)
    if (os.path.basename(parent) == "plugins"
            and os.path.basename(os.path.dirname(parent)) == ".agents"):
        return os.path.dirname(os.path.dirname(parent))
    return parent


def load(catalog_path: str, root_dir: Optional[str] = None) -> Marketplace:
    """Load a catalog file.

    ``root_dir`` is where relative sources resolve; None means "no
    directory" (a catalog fetched from a URL), in which case relative entries
    are still listed but cannot be installed.
    """
    try:
        with open(catalog_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return _invalid(catalog_path, root_dir, "cannot read %s: %s" % (catalog_path, exc))
    return parse(data, catalog_path, root_dir)


def parse(data: Any, catalog_path: Optional[str] = None,
          root_dir: Optional[str] = None) -> Marketplace:
    """Parse an already-decoded catalog document."""
    where = catalog_path or "marketplace.json"
    if not isinstance(data, dict):
        return _invalid(catalog_path, root_dir, "%s must contain a JSON object" % where)
    name = data.get("name")
    if not isinstance(name, str) or not name:
        return _invalid(catalog_path, root_dir, "%s has no 'name'" % where)
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        return _invalid(catalog_path, root_dir, "%s has no 'plugins' list" % where)

    diags: List[Diagnostic] = []
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    plugin_root = metadata.get("pluginRoot")
    if plugin_root is not None and not isinstance(plugin_root, str):
        diags.append(Diagnostic("warning", "marketplace.invalid-field",
                                "ignoring metadata.pluginRoot: expected a path"))
        plugin_root = None

    entries: List[MarketplaceEntry] = []
    seen = set()
    for i, raw in enumerate(plugins):
        entry, ediags = _parse_entry(raw, i, plugin_root)
        diags.extend(ediags)
        if entry is None:
            continue
        if entry.name in seen:
            diags.append(Diagnostic("warning", "marketplace.duplicate-entry",
                                    "plugin '%s' is listed more than once; the first "
                                    "entry is used" % entry.name, path="/plugins/%d" % i))
            continue
        seen.add(entry.name)
        entries.append(entry)

    description = data.get("description")
    if not isinstance(description, str):
        description = metadata.get("description") if isinstance(
            metadata.get("description"), str) else ""

    return Marketplace(name=name, root_dir=root_dir, catalog_path=catalog_path,
                       entries=tuple(entries), description=description,
                       diagnostics=tuple(diags))


def _invalid(catalog_path, root_dir, message) -> Marketplace:
    return Marketplace(name="", root_dir=root_dir, catalog_path=catalog_path,
                       diagnostics=(Diagnostic("error", "marketplace.invalid", message),))


def _parse_entry(raw: Any, index: int, plugin_root: Optional[str]
                 ) -> Tuple[Optional[MarketplaceEntry], List[Diagnostic]]:
    ptr = "/plugins/%d" % index
    if not isinstance(raw, dict):
        return (None, [Diagnostic("warning", "marketplace.invalid-entry",
                                  "plugin entry %d is not an object; skipped" % index,
                                  path=ptr)])
    name = raw.get("name")
    if not isinstance(name, str) or not _valid_name(name):
        return (None, [Diagnostic("warning", "marketplace.invalid-entry",
                                  "plugin entry %d has an invalid name %s; skipped"
                                  % (index, json.dumps(name)), path=ptr + "/name")])

    kind, source, diags = _parse_source(raw.get("source"), plugin_root, name, ptr)
    strict = raw.get("strict", True)
    version = raw.get("version")
    return (MarketplaceEntry(
        name=name,
        source_kind=kind,
        source=source,
        strict=strict is not False,
        description=raw.get("description") if isinstance(raw.get("description"), str) else "",
        version=version if isinstance(version, str) else None,
        category=raw.get("category") if isinstance(raw.get("category"), str) else "",
        fields=dict(raw),
    ), diags)


def _valid_name(name: str) -> bool:
    schema = _v.load_schema(SPEC_VERSIONS[-1], "plugin")
    return not _v.validate(name, schema["properties"]["name"], schema, "/name")


def _parse_source(raw: Any, plugin_root: Optional[str], name: str, ptr: str
                  ) -> Tuple[str, Dict[str, Any], List[Diagnostic]]:
    """Normalise an entry's ``source`` to ``(kind, fields, diagnostics)``."""
    diags: List[Diagnostic] = []

    if isinstance(raw, str):
        path = _join_plugin_root(plugin_root, raw)
        if path is None:
            return _bad(name, "relative source %s must stay inside the marketplace"
                        % json.dumps(raw), ptr, diags)
        return (RELATIVE, {"path": path}, diags)

    if not isinstance(raw, dict):
        return _bad(name, "no usable 'source'", ptr, diags)

    kind = raw.get("source")
    fields: Dict[str, Any] = {}
    for key in ("ref", "sha"):
        if isinstance(raw.get(key), str):
            fields[key] = raw[key]
    if "sha" in fields and not _SHA_RE.match(fields["sha"]):
        diags.append(Diagnostic("warning", "marketplace.invalid-field",
                                "plugin '%s': ignoring 'sha' %s; expected a full commit hash"
                                % (name, json.dumps(fields["sha"])), path=ptr + "/source/sha"))
        del fields["sha"]

    if kind == "github":
        repo = raw.get("repo")
        if not isinstance(repo, str) or repo.count("/") != 1:
            return _bad(name, "github source needs 'repo' as owner/name", ptr, diags)
        fields["url"] = github_url(repo)
        return (GITHUB, fields, diags)

    if kind in ("url", "git"):
        url = raw.get("url")
        if not isinstance(url, str) or not url:
            return _bad(name, "%s source needs 'url'" % kind, ptr, diags)
        fields["url"] = url
        return (GIT, fields, diags)

    if kind == "git-subdir":
        url, path = raw.get("url"), raw.get("path")
        if not isinstance(url, str) or not isinstance(path, str):
            return _bad(name, "git-subdir source needs 'url' and 'path'", ptr, diags)
        norm = _norm_rel(path)
        if norm is None:
            return _bad(name, "git-subdir path %s must be relative and stay inside "
                        "the repository" % json.dumps(path), ptr, diags)
        fields.update(url=url, path=norm)
        return (GIT_SUBDIR, fields, diags)

    if kind == "local":
        # Codex's spelling of a relative source
        path = raw.get("path")
        joined = _join_plugin_root(plugin_root, path) if isinstance(path, str) else None
        if joined is None:
            return _bad(name, "local source needs a relative 'path' inside the "
                        "marketplace", ptr, diags)
        return (RELATIVE, {"path": joined}, diags)

    if kind == "archive":
        url, sha256 = raw.get("url"), raw.get("sha256")
        if not isinstance(url, str) or not url.startswith("https://"):
            return _bad(name, "archive source needs an https 'url'", ptr, diags)
        out = {"url": url}
        if isinstance(sha256, str) and _SHA256_RE.match(sha256):
            out["sha256"] = sha256.lower()
        else:
            diags.append(Diagnostic("warning", "marketplace.no-checksum",
                                    "plugin '%s': archive source has no valid 'sha256'; "
                                    "its content cannot be verified" % name,
                                    path=ptr + "/source"))
        return (ARCHIVE, out, diags)

    if kind == "npm":
        out = {k: raw[k] for k in ("package", "version", "registry")
               if isinstance(raw.get(k), str)}
        return (NPM, out, diags)

    if kind == "command":
        return (COMMAND, {}, diags)

    return (UNKNOWN, {"source": kind}, diags)


def _bad(name, message, ptr, diags):
    diags.append(Diagnostic("warning", "marketplace.invalid-source",
                            "plugin '%s': %s" % (name, message), path=ptr + "/source"))
    return (UNKNOWN, {}, diags)


def _norm_rel(path: str) -> Optional[str]:
    """A relative POSIX path with no '..' escape, or None."""
    if not path or path.startswith("/") or "\\" in path:
        return None
    norm = posixpath.normpath(path)
    if norm == ".." or norm.startswith("../"):
        return None
    return norm


def _join_plugin_root(plugin_root: Optional[str], path: str) -> Optional[str]:
    """Resolve a relative source against ``metadata.pluginRoot``.

    A bare name (``"formatter"``) is prefixed with ``pluginRoot``; an explicit
    ``./`` path is taken relative to the marketplace root as written.
    """
    if not path.startswith("./") and plugin_root:
        path = posixpath.join(plugin_root, path)
    return _norm_rel(path)


def github_url(repo: str) -> str:
    return "https://github.com/%s.git" % repo


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def select(mkt: Marketplace, patterns: Sequence[str]
           ) -> Tuple[List[MarketplaceEntry], List[Diagnostic]]:
    """Entries matching any of ``patterns`` (fnmatch), in catalog order.

    A pattern matching nothing is an error, with a close match suggested.
    """
    import difflib
    diags: List[Diagnostic] = []
    names = [e.name for e in mkt.entries]
    chosen = set()
    for pat in patterns:
        hits = [n for n in names if fnmatch.fnmatchcase(n, pat)]
        if not hits:
            close = difflib.get_close_matches(pat, names, n=1, cutoff=0.6)
            hint = " (did you mean '%s'?)" % close[0] if close else ""
            diags.append(Diagnostic("error", "marketplace.no-match",
                                    "marketplace '%s' has no plugin matching '%s'%s"
                                    % (mkt.name, pat, hint)))
        chosen.update(hits)
    return ([e for e in mkt.entries if e.name in chosen], diags)


# ---------------------------------------------------------------------------
# Mapping to IVPM dependencies
# ---------------------------------------------------------------------------

def plugin_path(entry: MarketplaceEntry) -> str:
    """Where the plugin sits inside what ``to_dep`` fetches ('.' = its root)."""
    if entry.source_kind in (RELATIVE, GIT_SUBDIR):
        return entry.source["path"]
    return "."


def to_dep(entry: MarketplaceEntry,
           classify_ref: Optional[Callable[[str, str], str]] = None
           ) -> Tuple[Optional[Dict[str, Any]], List[Diagnostic]]:
    """The ``ivpm.yaml`` dependency options that fetch ``entry``.

    Returns ``(None, [])`` for a relative entry -- it lives inside the
    marketplace itself -- and ``(None, [error])`` for a source IVPM cannot
    install.  ``classify_ref(url, ref)`` returns 'branch', 'tag' or 'commit'
    for a git ``ref``; by default a ref is treated as a branch.

    The options carry ``agents.plugins`` naming exactly the selected plugin,
    so nothing else in a multi-plugin repository is picked up.
    """
    kind = entry.source_kind
    if kind == RELATIVE:
        return (None, [])
    if kind == COMMAND:
        return (None, [Diagnostic(
            "error", "marketplace.command-source",
            "plugin '%s' is produced by running a command; IVPM does not run "
            "marketplace commands" % entry.name)])
    if kind not in SUPPORTED_KINDS:
        return (None, [Diagnostic(
            "error", "marketplace.unsupported-source",
            "plugin '%s' has a '%s' source, which IVPM cannot install yet"
            % (entry.name, entry.source.get("source") or kind))])

    src = entry.source
    opts: Dict[str, Any] = {"name": entry.name, "url": src["url"]}
    if kind == ARCHIVE:
        opts["src"] = "http"
        if "sha256" in src:
            opts["sha256"] = src["sha256"]
    else:
        opts["src"] = "git"
        if "sha" in src:
            opts["commit"] = src["sha"]
        elif "ref" in src:
            how = classify_ref(src["url"], src["ref"]) if classify_ref else "branch"
            opts[how] = src["ref"]

    opts["agents"] = {"plugins": [plugin_path(entry)]}
    return (opts, [])


def within_root(mkt: Marketplace, entry: MarketplaceEntry) -> bool:
    """True when a relative entry resolves inside the marketplace on disk."""
    if mkt.root_dir is None:
        return False
    return within(mkt.root_dir, os.path.join(mkt.root_dir, entry.source["path"]))
