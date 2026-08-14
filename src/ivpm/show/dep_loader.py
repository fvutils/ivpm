#****************************************************************************
#* dep_loader.py
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
"""Loader for 'ivpm show deps'.

Reads the root ivpm.yaml, packages/package-lock.json, and each sub-package's
ivpm.yaml to build a DepGraph that captures the full resolved dependency picture.
"""
import json
import os
import sys
import warnings
from typing import Dict, List, Optional, Set

from .dep_info import DepGraph, DepNode


def _load_lock(deps_dir: str) -> Optional[dict]:
    """Return the parsed packages dict from package-lock.json, or None if absent."""
    lock_path = os.path.join(deps_dir, "package-lock.json")
    if not os.path.isfile(lock_path):
        return None
    with open(lock_path) as f:
        data = json.load(f)
    # The lock file has a top-level "packages" dict; older/test formats may not.
    return data.get("packages", data)


def _live_info(src: str, name: str, deps_dir: str) -> dict:
    """Delegate to the appropriate package class's get_live_info() method.

    Returns a dict that may contain 'version_resolved' and/or 'commit_resolved'.
    Only imports the relevant class to avoid pulling in heavy dependencies.
    """
    if src == "pypi":
        from ..pkg_types.package_pypi import PackagePyPi
        return PackagePyPi.get_live_info(name, deps_dir)
    if src == "git":
        from ..pkg_types.package_git import PackageGit
        return PackageGit.get_live_info(name, deps_dir)
    return {}


def _declared_deps(proj_dir: str, dep_set: str) -> List[str]:
    """Return the list of dep names declared in ivpm.yaml for the given dep-set.

    Reads through IvpmYamlReader so that ``include:`` directives are resolved
    (a raw yaml parse would miss dep-sets that live in included files).

    Returns an empty list when ivpm.yaml is absent or the dep-set is not found.
    This function is called while scanning sub-package dirs, so it must stay
    lenient: any read/parse/validation error (IvpmYamlReader raises
    SrcLoaderError on malformed manifests or unknown source types) is swallowed
    and treated as "no declared deps" rather than aborting the whole graph.
    """
    yaml_path = os.path.join(proj_dir, "ivpm.yaml")
    if not os.path.isfile(yaml_path):
        return []
    try:
        from ..ivpm_yaml_reader import IvpmYamlReader
        with open(yaml_path) as f:
            pi = IvpmYamlReader().read(f, yaml_path, allow_include=True)
        if dep_set in pi.dep_set_m:
            return list(pi.dep_set_m[dep_set].packages.keys())
    except Exception:
        pass
    return []


def _build_node(name: str,
                specifier: str,
                lock: Optional[dict],
                shadowed: bool,
                also_requested_by: List[str],
                scope: str = "") -> DepNode:
    """Construct a DepNode from lock-file data (or minimal data if lock absent).

    *scope* is the key prefix for the dependency scope this package was
    resolved into ("" at the root, else e.g. "toolB/packages/"). Lock keys are
    scope paths, so it is what makes the lookup find the right one of two
    same-named packages.
    """
    entry = (lock or {}).get(scope + name) or {}
    src = entry.get("src", "")
    dep_set = entry.get("dep_set") or "default"

    node = DepNode(
        name=name,
        src=src,
        specifier=specifier,
        shadowed=shadowed,
        also_requested_by=list(also_requested_by),
        dep_set=dep_set,
        scope=scope,
        nested=entry.get("deps_mode") == "nested",
        cycle_elided=entry.get("cycle_elided"),
    )

    # Populate resolved identity fields from the lock entry
    if entry:
        node.url = entry.get("url")
        node.branch = entry.get("branch")
        node.tag = entry.get("tag")
        node.commit = entry.get("commit_resolved") or entry.get("commit_requested")
        node.version = entry.get("version_requested")
        node.version_resolved = entry.get("version_resolved")
        cache_val = entry.get("cache")
        if cache_val is not None:
            node.cache = bool(cache_val)

    return node


class DepLoader:
    """Loads the dependency graph for a project workspace.

    Parameters
    ----------
    project_dir:
        Path to the project root (where ivpm.yaml lives).
    dep_set:
        Dependency set to inspect.  None → use the project's default dep-set
        (first dep-set declared, or "default" if none exist).
    """

    def __init__(self, project_dir: str, dep_set: Optional[str] = None):
        self.project_dir = os.path.abspath(project_dir)
        self._requested_dep_set = dep_set

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def load(self) -> DepGraph:
        """Build and return the complete DepGraph."""
        (root_name, root_version, root_description,
         root_dep_set, root_declared, deps_dir) = self._load_root()

        lock = _load_lock(deps_dir)
        lock_available = lock is not None

        if not lock_available:
            rel_deps = os.path.relpath(deps_dir, self.project_dir)
            warnings.warn(
                "%s/package-lock.json not found — resolved identity "
                "unavailable. Run 'ivpm update' first." % rel_deps,
                stacklevel=3,
            )
            # Build minimal lock-like structure from declared deps only
            lock = {}

        # Build index: name → set of all packages that declare it
        requesters = self._build_requesters_index(deps_dir, lock, root_declared, root_dep_set)

        # Build top-level nodes (flat unique list); embed tree inside each node
        in_scope: Set[str] = set(root_declared)
        nodes = self._build_nodes(
            names=root_declared,
            specifier="root",
            lock=lock,
            requesters=requesters,
            deps_dir=deps_dir,
            in_scope=set(),      # nothing is shadowed at root level
            ancestors=set(),
            build_tree=True,
        )

        return DepGraph(
            project=root_name,
            version=root_version,
            dep_set=root_dep_set,
            nodes=nodes,
            lock_available=lock_available,
            description=root_description,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_root(self):
        """Parse the root ivpm.yaml; return
        (name, version, description, dep_set, dep_names, deps_dir).

        Reads through IvpmYamlReader (rather than a raw yaml parse) so that
        ``include:`` directives are resolved and dep-sets defined in included
        files are visible. Unlike _declared_deps, a malformed root manifest is
        surfaced: IvpmYamlReader raises SrcLoaderError, which propagates here.
        """
        yaml_path = os.path.join(self.project_dir, "ivpm.yaml")
        if not os.path.isfile(yaml_path):
            raise FileNotFoundError(
                f"No ivpm.yaml found in '{self.project_dir}'. "
                "Run 'ivpm init' or specify -p."
            )
        from ..ivpm_yaml_reader import IvpmYamlReader
        from ..proj_info import resolve_deps_dir
        with open(yaml_path) as f:
            pi = IvpmYamlReader().read(f, yaml_path, allow_include=True)

        name = pi.name or os.path.basename(self.project_dir)
        version = pi.version
        description = pi.description

        # Deps dir: honor the manifest's deps-dir (reusing the already-parsed
        # ProjInfo so we don't re-read the file), falling back to a lockfile
        # search when it isn't declared.
        deps_dir = resolve_deps_dir(self.project_dir, pi)

        # Determine which dep-set to use: explicit request, else the manifest's
        # declared default, else the first declared dep-set, else "default".
        if self._requested_dep_set:
            dep_set = self._requested_dep_set
        elif pi.default_dep_set:
            dep_set = pi.default_dep_set
        elif pi.dep_set_m:
            dep_set = next(iter(pi.dep_set_m))
        else:
            dep_set = "default"

        # Collect declared dep names (insertion-ordered)
        dep_names = (list(pi.dep_set_m[dep_set].packages.keys())
                     if dep_set in pi.dep_set_m else [])

        return name, version, description, dep_set, dep_names, deps_dir

    def _build_requesters_index(
        self,
        deps_dir: str,
        lock: dict,
        root_declared: List[str],
        root_dep_set: str,
    ) -> Dict[str, Set[str]]:
        """Build {pkg_name → set of all requesters (owner + others)}.

        The lock file records the winning requester ('resolved_by').  We also
        scan each sub-package's ivpm.yaml to find additional requesters.
        """
        requesters: Dict[str, Set[str]] = {}

        # Seed from root's declared deps
        for name in root_declared:
            requesters.setdefault(name, set()).add("root")

        # Seed from lock's resolved_by field. Keys are scope paths, so the
        # index is keyed by scope path too.
        for key, entry in lock.items():
            rb = entry.get("resolved_by") or "root"
            requesters.setdefault(key, set()).add(rb)

        # Scan each package's ivpm.yaml for additional requesters. Driven off
        # the lock rather than a directory listing, so nested packages are
        # covered and non-package entries (.ivpm, ivpm.json) are not.
        for key, entry in lock.items():
            pkg_dir = os.path.join(deps_dir, key)
            if not os.path.isdir(pkg_dir):
                continue
            ds = entry.get("dep_set") or "default"
            # A boundary's declared deps live in its own scope; everyone
            # else's live in the scope the package itself belongs to.
            if entry.get("deps_mode") == "nested":
                child_scope = "%s/%s/" % (key, entry.get("deps_dir") or "packages")
            else:
                child_scope = key[:key.rindex("/") + 1] if "/" in key else ""
            for dep_name in _declared_deps(pkg_dir, ds):
                requesters.setdefault(child_scope + dep_name, set()).add(key)

        return requesters

    def _build_nodes(
        self,
        names: List[str],
        specifier: str,
        lock: dict,
        requesters: Dict[str, Set[str]],
        deps_dir: str,
        in_scope: Set[str],
        ancestors: Set[str],
        build_tree: bool,
        scope: str = "",
    ) -> List[DepNode]:
        """Recursively build DepNode objects for the given list of package names.

        *scope* is the lock-key prefix for the dependency scope these names were
        resolved into -- "" at the root, else e.g. "toolB/packages/".
        """
        nodes = []
        for name in names:
            owner = self._owner(name, lock, scope)
            shadowed = name in in_scope

            also = sorted(requesters.get(scope + name, set()) - {owner})

            node = _build_node(name, owner, lock, shadowed, also, scope)

            # Enrich node with live environment data when lock-file fields are absent
            if not shadowed:
                live = _live_info(node.src, scope + name, deps_dir)
                if live.get("version_resolved") and node.version_resolved is None:
                    node.version_resolved = live["version_resolved"]
                if live.get("commit_resolved") and node.commit is None:
                    node.commit = live["commit_resolved"]

            if build_tree and not shadowed and (scope + name) not in ancestors \
                    and node.cycle_elided is None:
                # Recurse into this package's own deps
                pkg_dir = os.path.join(deps_dir, scope + name)
                child_dep_set = node.dep_set or "default"
                child_names = _declared_deps(pkg_dir, child_dep_set)
                if child_names:
                    # A boundary's dependencies live in ITS scope; a flat
                    # package's live in the same scope it does.
                    entry = (lock or {}).get(scope + name) or {}
                    if node.nested:
                        child_scope = "%s%s/%s/" % (
                            scope, name, entry.get("deps_dir") or "packages")
                        # A nested scope resolves independently, so nothing
                        # from an enclosing scope shadows it.
                        child_in_scope = set()
                    else:
                        child_scope = scope
                        child_in_scope = in_scope | set(names) - {name}
                    child_ancestors = ancestors | {scope + name}
                    node.deps = self._build_nodes(
                        names=child_names,
                        specifier=name,
                        lock=lock,
                        requesters=requesters,
                        deps_dir=deps_dir,
                        in_scope=child_in_scope,
                        ancestors=child_ancestors,
                        build_tree=True,
                        scope=child_scope,
                    )

            nodes.append(node)
        return nodes

    @staticmethod
    def _owner(name: str, lock: dict, scope: str = "") -> str:
        """Return the first-specifier (owner) for a package in *scope*."""
        entry = lock.get(scope + name)
        if entry:
            return entry.get("resolved_by") or "root"
        return "root"
