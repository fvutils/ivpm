#****************************************************************************
#* package_handler_python.py
#*
#* Copyright 2023 Matthew Ballance and Contributors
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
#* Created on:
#*     Author: 
#*
#****************************************************************************
import dataclasses as dc
import json
import logging
import platform
import re
import subprocess
import textwrap
import toposort
import os
import shutil
import sys
from typing import ClassVar, Dict, List, Optional, Set
from ..project_ops_info import ProjectUpdateInfo, ProjectBuildInfo
from ..utils import note, fatal, get_venv_python, setup_venv, resolve_pkg_path
from ..installer_run import run_installer, format_output_tail
from ..content_attrib import (
    ENROLLED_EXPLICIT, ENROLLED_PROBE, ENROLLED_PROVIDES, ENROLLED_SRC_TYPE,
    OriginMap, isolate, isolation_identified_by,
    isolation_note, report_content_failure, report_manifest_problem,
    probe_allowed, report_probe_adoption, set_enrollment,
)
from ..manifest_check import check_python_manifest
from ..pkg_content_type import PythonTypeData
from ..package import get_type_data

from ..package import Package, SourceType
from .package_handler import PackageHandler
from .handler_phases import HandlerPhase
from .scope_keys import pkg_key
from ..msg import warning
from ..perf import span_or_null
# HasType no longer used in root_when (handler self-gates via on_root_post_load)

_logger = logging.getLogger("ivpm.handlers.package_handler_python")


def _strict(update_info) -> bool:
    """Whether --strict was given. Absent everywhere but the CLI, so read
    defensively: handlers are driven directly by tests and by the API."""
    args = getattr(update_info, "args", None)
    return bool(getattr(args, "strict", False))

_PYTHON_SENTINEL_BEGIN = "# --- ivpm:python begin ---"
_PYTHON_SENTINEL_END   = "# --- ivpm:python end ---"


def find_sccs(deps_m : Dict[str,Set[str]]) -> List[Set[str]]:
    """Return the strongly-connected components of a dependency map.

    ``deps_m`` maps a package name to the set of packages it depends on.
    Edges to names that are not themselves keys of ``deps_m`` are ignored.
    Implemented iteratively (Tarjan) so that deep dependency chains cannot
    blow the recursion limit.
    """
    index_m = {}
    lowlink_m = {}
    on_stack = set()
    stack = []
    sccs = []
    counter = [0]

    for root in deps_m.keys():
        if root in index_m:
            continue

        # Each work-item is (node, iterator over its remaining successors)
        work = [(root, iter(sorted(deps_m[root])))]
        index_m[root] = lowlink_m[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)

        while work:
            node, it = work[-1]
            advanced = False
            for succ in it:
                if succ not in deps_m:
                    # Not a package we are ordering; no edge to follow
                    continue
                if succ not in index_m:
                    index_m[succ] = lowlink_m[succ] = counter[0]
                    counter[0] += 1
                    stack.append(succ)
                    on_stack.add(succ)
                    work.append((succ, iter(sorted(deps_m[succ]))))
                    advanced = True
                    break
                elif succ in on_stack:
                    lowlink_m[node] = min(lowlink_m[node], index_m[succ])
            if advanced:
                continue

            work.pop()
            if lowlink_m[node] == index_m[node]:
                scc = set()
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.add(w)
                    if w == node:
                        break
                sccs.append(scc)
            if work:
                parent = work[-1][0]
                lowlink_m[parent] = min(lowlink_m[parent], lowlink_m[node])

    return sccs


def toposort_dep_groups(deps_m : Dict[str,Set[str]]) -> List[Set[str]]:
    """Order packages into install groups, tolerating dependency cycles.

    Packages within a cycle cannot be ordered relative to one another, so
    each strongly-connected component is collapsed into a single group.  The
    members of such a group land in one requirements file and are therefore
    resolved together in a single install pass, which is what a mutual
    dependency requires anyway.
    """
    sccs = find_sccs(deps_m)
    scc_of = {}
    for i, scc in enumerate(sccs):
        for name in scc:
            scc_of[name] = i

    condensed = dict((i, set()) for i in range(len(sccs)))
    for name, deps in deps_m.items():
        for dep in deps:
            if dep in scc_of and scc_of[dep] != scc_of[name]:
                condensed[scc_of[name]].add(scc_of[dep])

    groups = []
    for grp in toposort.toposort(condensed):
        members = set()
        for i in grp:
            members |= sccs[i]
        if len(members):
            groups.append(members)

    for scc in sccs:
        if len(scc) > 1:
            _logger.debug("Dependency cycle among Python packages: %s", sorted(scc))

    return groups


def _write_python_envrc(deps_dir: str):
    """Write the ``packages/python/export.envrc`` direnv snippet for the venv.

    IVPM delegates environment activation to ``direnv``.  On Windows the
    supported setup is ``direnv`` + git-bash (Git-for-Windows), so the same
    bash ``export.envrc`` is emitted on every platform; the only per-platform
    difference is the venv bindir name (``Scripts`` vs ``bin``).
    """
    python_dir = os.path.join(deps_dir, "python")
    if not os.path.isdir(python_dir):
        return

    if platform.system() == "Windows":
        scripts_dir = os.path.join(python_dir, "Scripts")
        bindir_name = "Scripts" if os.path.isdir(scripts_dir) else "bin"
    else:
        bindir_name = "bin"

    envrc_path = os.path.join(python_dir, "export.envrc")
    with open(envrc_path, "w") as fp:
        fp.write("# Generated by IVPM python handler -- do not edit manually\n")
        fp.write("PATH_add %s\n" % bindir_name)


def _patch_packages_envrc_python(deps_dir: str):
    """Insert or replace the python ``source_env`` section in ``packages.envrc``.

    The synthetic ``python/`` venv dir is not a real package, so the direnv
    handler does not auto-collect its ``export.envrc``; this sentinel-wrapped
    section wires it in.  ``direnv`` applies it on every platform (git-bash on
    Windows).
    """
    envrc_path = os.path.join(deps_dir, "packages.envrc")

    new_section = (
        "%s\n"
        "source_env ./python/export.envrc\n"
        "%s\n"
    ) % (_PYTHON_SENTINEL_BEGIN, _PYTHON_SENTINEL_END)

    if not os.path.isfile(envrc_path):
        with open(envrc_path, "w") as fp:
            fp.write("# Generated by IVPM -- do not edit manually\n")
            fp.write(new_section)
    else:
        with open(envrc_path) as fp:
            content = fp.read()

        begin_idx = content.find(_PYTHON_SENTINEL_BEGIN)
        end_idx   = content.find(_PYTHON_SENTINEL_END)

        if begin_idx != -1 and end_idx != -1:
            end_of_line = content.find("\n", end_idx)
            end_of_line = len(content) if end_of_line == -1 else end_of_line + 1
            new_content = content[:begin_idx] + new_section + content[end_of_line:]
        else:
            new_content = content + "\n" + new_section

        with open(envrc_path, "w") as fp:
            fp.write(new_content)


# ---------------------------------------------------------------------------
# pyproject.toml harvesting helpers
# ---------------------------------------------------------------------------

_VERSION_OPERATORS = frozenset('<>=!~')

import re as _re
_PEP508_NAME_RE = _re.compile(r'^([A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?)')


def _resolve_pyproject_url(url: str, pkg=None, proj_dir: str = None) -> str:
    """Strip ``file://`` prefix, expand environment variables, and resolve a
    relative path against the ivpm.yaml that declared *pkg*."""
    from ..utils import resolve_pkg_path

    path = url
    if path.startswith("file://"):
        path = path[len("file://"):]
    if pkg is not None:
        return resolve_pkg_path(pkg, path, proj_dir)
    return os.path.expandvars(path)


def _pep508_split(spec: str):
    """Return ``(normalised_name, remainder)`` for a PEP 508 specifier.

    *normalised_name* has runs of ``[-_.]`` folded to ``-`` (PEP 503).
    *remainder* is everything after the name (extras, version, markers).
    """
    spec = spec.strip()
    m = _PEP508_NAME_RE.match(spec)
    if not m:
        return spec, ""
    raw_name = m.group(1)
    remainder = spec[m.end():]
    normalised = _re.sub(r'[-_.]+', '-', raw_name).lower()
    return normalised, remainder


def _project_name_at(path: str):
    """Return the normalised distribution name of the project rooted at *path*.

    Reads ``[project] name`` from pyproject.toml, falling back to
    ``[metadata] name`` in setup.cfg.  Returns None when neither declares one
    -- a setup.py that computes its name at runtime, for instance.
    """
    if not path:
        return None

    pyproject = os.path.join(path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        try:
            with open(pyproject, "rb") as fp:
                data = tomllib.load(fp)
            name = (data.get("project") or {}).get("name")
            if isinstance(name, str) and name.strip():
                return _pep508_split(name)[0]
        except Exception as e:
            # msg.warning, not _logger.warning: the logging logger is silent
            # under the default configuration, so this used to be invisible
            # precisely when it mattered.
            warning("could not read the project name from %s (%s)"
                    % (pyproject, e))

    setup_cfg = os.path.join(path, "setup.cfg")
    if os.path.isfile(setup_cfg):
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg.read(setup_cfg)
            name = cfg.get("metadata", "name", fallback=None)
            if name and name.strip():
                return _pep508_split(name)[0]
        except Exception as e:
            _logger.warning("could not read the project name from %s (%s)",
                            setup_cfg, e)

    return None


def _expand_dep_group(groups: dict, name: str, seen: frozenset) -> list:
    """Recursively expand a PEP 735 dependency-group, resolving ``include-group``."""
    if name not in groups or name in seen:
        return []
    seen = seen | {name}
    result = []
    for entry in groups[name]:
        if isinstance(entry, str):
            result.append(entry)
        elif isinstance(entry, dict) and "include-group" in entry:
            result.extend(_expand_dep_group(groups, entry["include-group"], seen))
    return result


def _extract_toml_section(data: dict, section: str) -> list:
    """Return a flat list of PEP 508 strings from a named *section*.

    Understood section names:

    * ``dependencies``
    * ``optional-dependencies.<extra>``
    * ``dependency-groups.<group>``

    Returns ``[]`` when the section is absent (no error).
    """
    project = data.get("project", {})

    if section == "dependencies":
        return list(project.get("dependencies", []))

    if section.startswith("optional-dependencies."):
        extra = section[len("optional-dependencies."):]
        opt = project.get("optional-dependencies", {})
        return list(opt.get(extra, []))

    if section.startswith("dependency-groups."):
        group = section[len("dependency-groups."):]
        return _expand_dep_group(data.get("dependency-groups", {}), group, frozenset())

    return []


@dc.dataclass
class PackageHandlerPython(PackageHandler):
    name:               ClassVar[str]            = "python"
    description:        ClassVar[str]            = "Installs Python packages into the managed virtual environment"
    leaf_when:          ClassVar[Optional[List]] = None               # always inspect every package
    root_when:          ClassVar[Optional[List]] = None               # always run root; early-exit below handles no-python projects
    phase:              ClassVar[str]            = HandlerPhase.INSTALL
    conditions_summary: ClassVar[str]            = "leaf: all packages; root: always (skips if no Python packages and no python config)"

    pkgs_info  : Dict[str,Package] = dc.field(default_factory=dict)
    src_pkg_s  : Set[str] = dc.field(default_factory=set)
    pypi_pkg_s : Set[str] = dc.field(default_factory=set)
    _pyproject_toml_pkgs : list = dc.field(default_factory=list)
    # Which emitted requirement came from which package. Populated as the
    # requirements files are written, where the Package is already in hand, so
    # it is exact by construction rather than reconstructed after the fact.
    _origins : OriginMap = dc.field(default_factory=OriginMap)
    # build-system requirement spec -> the source package that declared it
    _build_requires_src : dict = dc.field(default_factory=dict)
    use_uv : bool = False
    debug : bool = True

    def reset(self):
        self.pkgs_info  = {}
        self.src_pkg_s  = set()
        self.pypi_pkg_s = set()
        self._pyproject_toml_pkgs = []
        # Which emitted requirement came from which package. Populated as the
        # requirements files are written, where the Package is already in
        # hand, so it is exact rather than reconstructed.
        self._origins = OriginMap()
        # build-system requirement spec -> the source package that declared it
        self._build_requires_src = {}

    @classmethod
    def handler_info(cls):
        from ..show.info_types import HandlerInfo, ParamInfo
        return HandlerInfo(
            name=cls.name,
            description=cls.description,
            phase=cls.phase,
            conditions=cls.conditions_summary,
            params=[
                ParamInfo("type: python", "Mark a package for Python installation (used in the 'type:' field of an ivpm.yaml dep entry)"),
            ],
            cli_options=[
                "update: --py-uv         Use 'uv' instead of pip to manage the virtual environment",
                "update: --py-pip         Force use of pip (overrides uv detection)",
                "update: --py-skip-install  Skip Python package installation",
                "update: --py-force-install  Force re-install of all Python packages",
                "update: --py-prerls-packages  Allow pre-release packages",
                "update: --py-system-site-packages  Inherit system site-packages in the venv",
                "clone:  --py-uv / --py-pip / --py-system-site-packages  (same as update)",
            ],
        )

    def on_leaf_post_load(self, pkg: Package, update_info):
        # Virtual nodes (e.g. `src: ivpm.yaml` factories) have no installed
        # directory to scan -- they only contribute deps.
        if getattr(pkg, "virtual", False):
            return
        add = False
        if pkg.src_type == "pypi":
            with self._lock:
                self.pypi_pkg_s.add(pkg.name)
            set_enrollment(pkg, "python", ENROLLED_SRC_TYPE)
            add = True
        elif pkg.src_type == "pyproject.toml":
            with self._lock:
                self._pyproject_toml_pkgs.append(pkg)
            # Virtual package — do not add to pkgs_info or set pkg_type
            return
        elif get_type_data(pkg, PythonTypeData) is not None:
            # Explicit type: python -- either at the dependency entry or in the
            # package's own ivpm.yaml. Which one decides where a later failure
            # gets located, so keep them apart.
            td = get_type_data(pkg, PythonTypeData)
            set_enrollment(pkg, "python",
                           ENROLLED_PROVIDES if getattr(td, "self_declared", False)
                           else ENROLLED_EXPLICIT)
            with self._lock:
                self.src_pkg_s.add(pkg.name)
            add = True
        elif pkg.pkg_type is not None and pkg.pkg_type == PackageHandlerPython.name:
            set_enrollment(pkg, "python", ENROLLED_EXPLICIT)
            with self._lock:
                self.src_pkg_s.add(pkg.name)
            add = True
        elif probe_allowed(pkg, "python") and hasattr(pkg, "path"):
            # Auto-detection. Finding Python metadata is not the same as
            # finding an installable project: a pyproject.toml carrying only
            # [tool.ruff] is valid, common, and nothing pip can install. The
            # gate rejects those without troubling the user, which is the
            # cheapest possible fix for a mis-enrollment.
            diag = check_python_manifest(pkg.path)
            if diag is not None:
                set_enrollment(pkg, "python", ENROLLED_PROBE,
                               evidence=os.path.basename(diag.path))
                if diag.ok:
                    add = True
                    report_probe_adoption(
                        pkg, "python", evidence=os.path.basename(diag.path),
                        strict=_strict(update_info))
                    with self._lock:
                        self.src_pkg_s.add(pkg.name)
                else:
                    report_manifest_problem(
                        pkg, diag, "python",
                        strict=_strict(update_info))
        if add:
            pkg.pkg_type = PackageHandlerPython.name
            with self._lock:
                self._record_python_pkg(pkg)

    def _record_python_pkg(self, pkg) -> None:
        """Record a Python package for installation, keyed by distribution name.

        Deliberately keyed by *name*, not by scope: there is one venv per
        workspace, and one venv cannot hold two versions of a distribution.
        Nesting isolates the dependency *tree*, not the Python environment.

        So when two scopes contribute the same distribution, one of them will
        not be importable -- say so, and resolve it deterministically in favour
        of the one nearest the root (matching the dv-flow convention). Caller
        holds the lock.
        """
        prior = self.pkgs_info.get(pkg.name)
        if prior is not None and prior is not pkg:
            prior_key = pkg_key(prior)
            new_key = pkg_key(pkg)
            if prior_key != new_key:
                keep, drop = ((prior, pkg)
                              if prior_key.count("/") <= new_key.count("/")
                              else (pkg, prior))
                warning(
                    "Python distribution '%s' is contributed by two dependency "
                    "scopes: '%s' and '%s'. Nesting isolates the dependency "
                    "tree, but not the Python environment -- one venv cannot "
                    "hold two versions of a distribution. Installing '%s'; the "
                    "other will not be importable. To resolve, make the two "
                    "agree on a version, or exclude one with 'deps: skip'." % (
                        pkg.name, prior_key, new_key, pkg_key(keep)))
                self.pkgs_info[pkg.name] = keep
                return
        self.pkgs_info[pkg.name] = pkg

    def _harvest_pyproject_toml(self, pkg, update_info=None) -> list:
        """Read a ``pyproject.toml`` and return a list of ``PackagePyPi`` entries.

        Entries whose normalised name already exists in ``self.pypi_pkg_s``
        (explicit ``src: pypi`` entries) are skipped — explicit wins.
        """
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        from ..pkg_types.package_pypi import PackagePyPi

        proj_dir = (getattr(update_info, "project_dir", None) or os.getcwd())

        toml_path = getattr(pkg, "toml_path", None)
        if toml_path:
            # Relative paths are relative to the ivpm.yaml that declared the
            # entry -- not to the root project.
            path = resolve_pkg_path(pkg, toml_path, proj_dir)
        else:
            path = _resolve_pyproject_url(pkg.url, pkg, proj_dir)
        if not os.path.isfile(path):
            source_ref = getattr(pkg, "toml_path", None) or getattr(pkg, "url", None) or "<unknown>"
            fatal(
                "src: pyproject.toml — file not found: %s "
                "(resolved from '%s')" % (path, source_ref)
            )
            return []

        with open(path, "rb") as fp:
            try:
                data = tomllib.load(fp)
            except Exception as e:
                fatal("src: pyproject.toml — failed to parse %s: %s" % (path, e))
                return []

        include = list(pkg.include)

        # Expand "all" into every available section
        if "all" in include:
            include = ["dependencies"]
            project = data.get("project", {})
            include += [
                "optional-dependencies.%s" % e
                for e in project.get("optional-dependencies", {}).keys()
            ]
            include += [
                "dependency-groups.%s" % g
                for g in data.get("dependency-groups", {}).keys()
            ]

        result = []
        seen_names = set()  # dedup within this file

        for section in include:
            specs = _extract_toml_section(data, section)
            if not specs:
                _logger.debug(
                    "pyproject.toml: section '%s' is empty or absent in %s",
                    section, path,
                )

            for spec in specs:
                name, _ = _pep508_split(spec)
                if not name:
                    continue
                if name in self.pypi_pkg_s:
                    _logger.debug(
                        "Skipping '%s' from pyproject.toml — explicit entry wins",
                        name,
                    )
                    continue
                if name in seen_names:
                    continue
                seen_names.add(name)

                p = PackagePyPi(name)
                p.src_type = "pypi"
                p._raw_spec = spec.strip()  # full PEP 508 specifier passed verbatim to pip/uv
                result.append(p)

        return result

    def _resolve_venv_mode(self, update_info: ProjectUpdateInfo):
        """Determine effective VenvMode from CLI flags, skip_venv, and yaml config.

        Priority (highest first):
          1. CLI --py-skip-install (py_skip_install)     → SKIP
          2. update_info.skip_venv                       → SKIP
          3. yaml with.python.venv == false              → SKIP (cannot be overridden by tool flags)
          4. CLI --py-uv                                 → UV
          5. CLI --py-pip                                → PIP
          6. yaml with.python.venv (uv / pip / true)    → as specified
          7. Default                                     → AUTO
        """
        from ..proj_info import VenvMode

        if getattr(update_info.args, "py_skip_install", False):
            return VenvMode.SKIP
        if update_info.skip_venv:
            return VenvMode.SKIP
        # YAML SKIP wins over tool-selection CLI flags
        if (update_info.python_config is not None and
                update_info.python_config.venv == VenvMode.SKIP):
            return VenvMode.SKIP
        # Tool-selection CLI flags override yaml tool preference
        if getattr(update_info.args, "py_uv", False):
            return VenvMode.UV
        if getattr(update_info.args, "py_pip", False):
            return VenvMode.PIP
        if update_info.python_config is not None:
            return update_info.python_config.venv
        return VenvMode.AUTO

    def on_destroy(self, remove_info):
        """Remove the managed virtual environment (packages/python). Honors
        --keep-venv (post-MVP) and dry_run."""
        if getattr(remove_info, "keep_venv", False):
            return None
        python_dir = os.path.join(remove_info.deps_dir, "python")
        if not os.path.isdir(python_dir):
            return None
        if getattr(remove_info, "dry_run", False):
            return [python_dir]
        from ..package import _rmtree_force
        _rmtree_force(python_dir)
        return [python_dir]

    def on_root_post_load(self, update_info: ProjectUpdateInfo):
        from ..proj_info import VenvMode

        # Expand any src: pyproject.toml virtual packages into concrete pypi entries.
        # Runs before the has_python_pkgs check so a project with only a
        # pyproject.toml entry still triggers venv creation.
        for vp in self._pyproject_toml_pkgs:
            for p in self._harvest_pyproject_toml(vp, update_info):
                if p.name not in self.pypi_pkg_s:
                    self.pypi_pkg_s.add(p.name)
                    self.pkgs_info[p.name] = p

        # If no Python packages were detected and the project does not
        # explicitly configure ``with.python``, there is nothing to do.
        has_python_pkgs = bool(self.pypi_pkg_s or self.src_pkg_s or self.pkgs_info)
        has_python_config = (
            update_info.python_config is not None
            and update_info.python_config.venv != VenvMode.SKIP
        )
        if not has_python_pkgs and not has_python_config:
            return

        # --- Determine how to install ivpm into the venv ---
        # Query the site config for the install spec (e.g. ["ivpm"] for
        # PyPI, or ["/path/to/ivpm-2.2.4.whl"] for a local wheel).
        # Store the raw args for injection into the requirements file.
        # Skipped when the workspace resolves 'ivpm' itself -- either as a PyPI
        # dependency or as a source package. In the source case the workspace
        # checkout is installed editable in a later phase, so injecting the
        # site-config spec here would install a second, unrelated IVPM over it
        # and then immediately replace it again.
        if "ivpm" not in self.pypi_pkg_s and "ivpm" not in self.src_pkg_s:
            from ..site_config import get_site_config
            self._ivpm_install_args = get_site_config().get_ivpm_install_args()
            _logger.info("IVPM install spec from site config: %s", self._ivpm_install_args)

        venv_mode = self._resolve_venv_mode(update_info)

        if venv_mode == VenvMode.SKIP:
            note("Skipping Python package installation")
            return

        python_dir = os.path.join(update_info.deps_dir, "python")

        # --- Create venv if it doesn't exist yet ---
        if not os.path.isdir(python_dir):
            system_site_packages = False
            if update_info.python_config is not None:
                system_site_packages = update_info.python_config.system_site_packages
            # CLI flag overrides yaml
            if getattr(update_info.args, "py_system_site_packages", False):
                system_site_packages = True

            # Map VenvMode → uv_pip argument for setup_venv
            if venv_mode == VenvMode.UV:
                uv_pip = "uv"
            elif venv_mode == VenvMode.PIP:
                uv_pip = "pip"
            else:
                uv_pip = "auto"

            suppress_output = getattr(update_info, 'suppress_output', False)
            with span_or_null(getattr(update_info, "perf", None), "venv.create", mode=uv_pip), \
                 self.task_context(update_info, "venv-create", "Creating Python virtual environment") as task:
                try:
                    setup_venv(
                        python_dir,
                        uv_pip=uv_pip,
                        suppress_output=suppress_output,
                        system_site_packages=system_site_packages,
                    )
                except Exception as e:
                    raise
        else:
            note("python virtual environment already exists")

        with span_or_null(getattr(update_info, "perf", None), "envrc.write"):
            _write_python_envrc(update_info.deps_dir)
            _patch_packages_envrc_python(update_info.deps_dir)

        if getattr(update_info.args, "py_uv", False):
            self.use_uv = True
        elif getattr(update_info.args, "py_pip", False):
            self.use_uv = False
        else:
            if shutil.which("uv") is not None:
                self.use_uv = True

        # Check whether packages were already installed
        if os.path.isfile(os.path.join(update_info.deps_dir, "python_pkgs_1.txt")):
            if update_info.force_py_install:
                note("Forcing re-install of Python packages")
            else:
                note("Python packages already installed. Use --py-force-install to force re-install")
                self._push_entrypoint_agent_dirs(python_dir, update_info)
                return
        else:
            note("Installing Python packages")


        # Assemble the per-phase requirements files. Opened as an explicit
        # span (not a with-block) to avoid reindenting the long assembly body;
        # closed just before the install phase below.
        _perf = getattr(update_info, "perf", None)
        _reqs_span = _perf.open_span("reqs.assemble") if _perf is not None else None

        # Build up a dependency map for Python package installation and
        # order the source packages based on it
        python_deps_m = self._build_python_deps_m()
        pysrc_pkg_order = toposort_dep_groups(python_deps_m)
        if self.debug:
            _logger.debug("python_deps_m: %s", str(python_deps_m))
            _logger.debug("pysrc_pkg_order: %s", str(pysrc_pkg_order))

        python_deps_m = {}

        python_requirements_paths = []

        # Setup deps are a special category. We need to 
        # install them first -- possibly even before
        # installing other pypi packages
        setup_deps_s = set()
        # for pkg,deps in update_info.setup_deps.items():
        #     for dep in deps:
        #         if dep not in setup_deps_s:
        #             setup_deps_s.add(dep)
        #             if dep in self.pypi_pkg_s:
        #                 self.pypi_pkg_s.remove(dep)

        # for proj,deps in self.pkgs_info.setup_deps.items():
        #     for dep in deps:
        #         if dep not in setup_deps_s:
        #             setup_deps_s.add(dep)
        #             if dep in self.pypi_pkg_s:
        #                 self.pypi_pkg_s.remove(dep)
        _logger.debug("setup_deps_s: %s", str(setup_deps_s))

        if len(setup_deps_s) > 0:
            setup_deps_pkgs = []
            for dep in setup_deps_s:
                setup_deps_pkgs.append(self.pkgs_info[dep])

            requirements_path = os.path.join(
                update_info.deps_dir, "python_pkgs_%d.txt" % (
                len(python_requirements_paths)+1))
            self._write_requirements_txt(
                update_info.deps_dir,
                setup_deps_pkgs, 
                requirements_path)
            python_requirements_paths.append(requirements_path)

        # Inject ivpm install args from site config as a requirements file.
        # This may be ["ivpm"] (PyPI) or ["/path/to/wheel.whl"] (local).
        if hasattr(self, '_ivpm_install_args') and self._ivpm_install_args:
            requirements_path = os.path.join(
                update_info.deps_dir, "python_pkgs_%d.txt" % (
                len(python_requirements_paths)+1))
            with open(requirements_path, "w") as fp:
                for arg in self._ivpm_install_args:
                    fp.write("%s\n" % arg)
            python_requirements_paths.append(requirements_path)

        # Build backends next, before anything that needs to be built. Because
        # the install runs with --no-build-isolation, these are not provisioned
        # for us; a source package whose backend is missing cannot build.
        src_pkg_names = [name for grp in pysrc_pkg_order for name in grp]
        build_requires = self._collect_build_requires(
            update_info.deps_dir, src_pkg_names)
        if len(build_requires) > 0:
            requirements_path = os.path.join(
                update_info.deps_dir, "python_pkgs_%d.txt" % (
                len(python_requirements_paths)+1))
            with open(requirements_path, "w") as fp:
                for spec in build_requires:
                    fp.write("%s\n" % spec)
                    # Attributed to the package that declared the requirement,
                    # not to the backend named in the spec: a failure here is
                    # the declaring package's problem to fix.
                    owner = self.pkgs_info.get(
                        self._build_requires_src.get(spec))
                    if owner is not None:
                        self._origins.record(
                            spec, owner, requirements_path, language="python",
                            dist=_pep508_split(spec)[0])
            python_requirements_paths.append(requirements_path)

        # Next, create a requirements file for all
        # non-setup-dep PyPi packages
        python_pkgs = []
        _logger.debug("pypi_pkg_s: %s", str(self.pypi_pkg_s))
        for pypi_p in self.pypi_pkg_s:
            python_pkgs.append(self.pkgs_info[pypi_p])

        if len(python_pkgs) > 0:
            requirements_path = os.path.join(
                update_info.deps_dir, "python_pkgs_%d.txt" % (
                len(python_requirements_paths)+1))

            self._write_requirements_txt(
                update_info.deps_dir,
                python_pkgs, 
                requirements_path)
            python_requirements_paths.append(requirements_path)

        # Now, add requirement files for any source packages
        for pydep_s in pysrc_pkg_order:
            python_pkgs = []
            for key in pydep_s:
                
                # A future iteration does not need to install this
                self.pypi_pkg_s.discard(key)
                self.src_pkg_s.discard(key)
                
                # Note: for completeness, should collect Python 
                # packages known to be required by this pre-dep
                
                if key not in self.pkgs_info.keys():
                    raise Exception("Package %s not found in packages-info" % key)
                
                pkg : Package = self.pkgs_info[key]
                python_pkgs.append(pkg)
                
            if len(python_pkgs):
                requirements_path = os.path.join(
                    update_info.deps_dir, "python_pkgs_%d.txt" % (len(python_requirements_paths)+1))
                self._write_requirements_txt(
                    update_info.deps_dir,
                    python_pkgs, 
                    requirements_path)
                python_requirements_paths.append(requirements_path)
            
        if _reqs_span is not None:
            _perf.close_span(_reqs_span)

        if len(python_requirements_paths):
            import sys
            import platform

            ps = ";" if platform.system() == "Windows" else ":"
            env = os.environ.copy()
            env["PYTHONPATH"] = ps.join(sys.path)

            n = len(python_requirements_paths)
            note("Installing Python dependencies in %d phases" % n)
            suppress_output = getattr(update_info, 'suppress_output', False)
            with span_or_null(getattr(update_info, "perf", None), "pip.install",
                              phases=n, mode=("uv" if self.use_uv else "pip")), \
                 self.task_context(update_info, "python-install", "Installing Python packages") as task:
                for i, reqfile in enumerate(python_requirements_paths, 1):
                    task.progress(
                        f"Installing package set {i}/{n}",
                        step=i, total=n,
                    )
                    self._install_requirements(
                        os.path.join(update_info.deps_dir, "python"),
                        reqfile,
                        getattr(update_info.args, "py_prerls_packages", False),
                        self.use_uv,
                        suppress_output=suppress_output,
                        task=task,
                        force=update_info.force_py_install,
                        update_info=update_info)

        self._push_entrypoint_agent_dirs(python_dir, update_info)

    def _push_entrypoint_agent_dirs(self, python_dir: str, update_info) -> None:
        """Query the agent entry-point groups from the managed venv.

        Two groups are queried in one subprocess: 'agent.skills' (with the
        legacy 'ivpm.skill' alias), whose results are pushed to
        ``pending_skill_dirs``, and 'agent.plugins', whose results are pushed to
        ``pending_plugin_dirs``.  The agents handler consumes both.

        An 'agent.plugins' entry-point may return either a plugin root or a path
        to its ``plugin.json``; normalization happens on the IVPM side so the
        in-venv script stays dependency-free.
        """
        venv_python = get_venv_python(python_dir)
        if not os.path.isfile(venv_python):
            return

        # Note: result is wrapped in sentinel markers so we can recover the JSON
        # even when imported modules print noise to stdout at import time.
        # Queries both 'agent.skills' (current) and 'ivpm.skill' (deprecated),
        # deduplicating by (group-prioritized) entry-point name so dual-publishing
        # packages don't produce duplicates.
        script = textwrap.dedent("""\
            import importlib.metadata, json, sys
            result = []
            seen_names = set()
            for group, kind in (('agent.skills', 'skills'), ('ivpm.skill', 'skills'),
                                ('agent.plugins', 'plugins')):
                for ep in importlib.metadata.entry_points(group=group):
                    if (kind, ep.name) in seen_names:
                        continue
                    try:
                        fn = ep.load()
                        dirs = fn() if callable(fn) else str(fn)
                        if isinstance(dirs, (str, bytes)):
                            dirs = [str(dirs)]
                        result.append({'name': ep.name, 'kind': kind, 'dirs': list(dirs)})
                        seen_names.add((kind, ep.name))
                    except Exception as exc:
                        sys.stderr.write('ivpm: entrypoint %s (%s) error: %s\\n' % (ep.name, group, exc))
            sys.stdout.write('<<IVPM_SKILLS_JSON>>' + json.dumps(result) + '<</IVPM_SKILLS_JSON>>\\n')
        """)

        try:
            r = subprocess.run(
                [venv_python, "-c", script],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if r.returncode != 0:
                print("ivpm: warning: agent entrypoint query failed:\n%s"
                      % r.stderr.strip(), file=sys.stderr)
                return
            if r.stderr.strip():
                print("ivpm: warning: agent entrypoint errors "
                      "(affected skills/plugins will not be linked):\n%s"
                      % r.stderr.strip(), file=sys.stderr)
            m = re.search(r'<<IVPM_SKILLS_JSON>>(.*?)<</IVPM_SKILLS_JSON>>', r.stdout, re.DOTALL)
            if not m:
                print("ivpm: warning: agent entrypoint query produced no parseable "
                      "output (stdout pollution?)", file=sys.stderr)
                return
            data = json.loads(m.group(1))
        except Exception as exc:
            print("ivpm: warning: failed to query agent.skills/agent.plugins entrypoints: %s"
                  % exc, file=sys.stderr)
            return

        for item in data:
            ep_name = item["name"]
            # Older in-venv payloads carry no 'kind'; they were always skills.
            kind = item.get("kind", "skills")
            for path in item.get("dirs", []):
                if kind == "plugins":
                    update_info.pending_plugin_dirs.append((ep_name, os.path.normpath(path)))
                else:
                    update_info.pending_skill_dirs.append((ep_name, os.path.normpath(path)))

    def get_lock_entries(self, deps_dir: str) -> dict:
        """Return pip-resolved package versions from the managed venv.

        Queries the venv's Python interpreter with ``pip list --format=json``.
        This works regardless of whether ``pip`` or ``uv`` was used to install
        packages — both write to the same venv site-packages directory.
        """
        python_dir = os.path.join(deps_dir, "python")
        if not os.path.isdir(python_dir):
            return {}

        venv_python = get_venv_python(python_dir)
        if not os.path.isfile(venv_python):
            return {}

        try:
            result = subprocess.run(
                [venv_python, "-m", "pip", "list", "--format=json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                _logger.warning("pip list failed: %s", result.stderr.strip())
                return {}

            import json
            raw = json.loads(result.stdout)
            versions = {item["name"]: item["version"] for item in raw}
            return {"python_packages": versions}
        except Exception as e:
            _logger.warning("Failed to query pip versions for lock file: %s", e)
            return {}

    def _build_python_deps_m(self) -> Dict[str,Set[str]]:
        """Map each source Python package to the source packages it depends on.

        Only edges between packages that IVPM installs from source are
        recorded: PyPI packages are installed in an earlier phase, and
        non-Python packages are never installed into the venv at all.

        A dependency edge is recorded no matter which package resolved the
        dependency.  A package declaring a dependency needs that dependency
        present when it is installed, even when the dependency was pulled in
        by the root project or by a sibling.  Cycles that this may introduce
        are handled by :func:`toposort_dep_groups`.
        """
        python_deps_m = {}
        _logger.debug("src_pkg_s: %s", str(self.src_pkg_s))
        for pyp in self.src_pkg_s:
            _logger.debug("pyp: %s", pyp)
            p = self.pkgs_info[pyp]
            if pyp not in python_deps_m.keys():
                python_deps_m[pyp] = set()

            if p.proj_info is not None:
                _logger.debug("non-none proj_info")
                # TODO: see if the package specifies the package set
                if p.proj_info.has_dep_set(p.proj_info.target_dep_set):
                    for dp in p.proj_info.get_dep_set(p.proj_info.target_dep_set).keys():
                        if dp != pyp and dp in self.src_pkg_s:
                            python_deps_m[pyp].add(dp)
                else:
                    _logger.warning("Project %s does not contain its target dependency set (%s)",
                        p.proj_info.name,
                        p.proj_info.target_dep_set)
                    for d in p.proj_info.dep_set_m.keys():
                        _logger.debug("Dep-Set: %s", d)

        return python_deps_m

    def build(self, build_info : ProjectBuildInfo):
        # Order the source packages based on their dependencies
        python_deps_m = self._build_python_deps_m()
        pysrc_pkg_order = toposort_dep_groups(python_deps_m)
        if self.debug:
            _logger.debug("python_deps_m: %s", str(python_deps_m))
            _logger.debug("pysrc_pkg_order: %s", str(pysrc_pkg_order))

        env = os.environ.copy()
        env["DEBUG"] = "1" if build_info.debug else "0"
        for pkg_s in pysrc_pkg_order:
            for pkg in pkg_s:
                p = self.pkgs_info[pkg]
                if p.pkg_type == PackageHandlerPython.name:
                    if os.path.isfile(os.path.join(build_info.deps_dir, pkg, "setup.py")):
                        cmd = [
                            sys.executable,
                            'setup.py',
                            'build_ext',
                            '--inplace'
                        ]
                        result = run_installer(
                            cmd,
                            env=env,
                            cwd=os.path.join(build_info.deps_dir, pkg))

                        if not result.ok:
                            # The build's own output is the only explanation of
                            # why it failed; quoting the tail beats "Failed to
                            # build <pkg>" with the reason discarded.
                            fatal("failed to build package %s%s" % (
                                pkg, format_output_tail(result.lines)),
                                loc=getattr(p, "srcinfo", None))
                        
    def _install_requirements(self,
                              python_dir,
                              requirements_file,
                              use_pre,
                              use_uv,
                              suppress_output=False,
                              task=None,
                              force=False,
                              update_info=None):
        """Installs the requirements specified in a file.

        If *task* is provided, stdout/stderr are captured and parsed for
        progress messages that are emitted via task.progress().

        When *force* is set, the installer is told to rebuild and reinstall
        the requirements even if the venv already satisfies them. Without this
        the installer simply audits the existing environment, which makes
        --force-py-install a no-op.

        The forced reinstall is scoped to the distributions this file names,
        never applied blanket. Requirements are installed in dependency-ordered
        phases, so an earlier phase's editable install is a legitimate way for
        a later phase's dependency to be satisfied. A blanket reinstall marks
        those transitive dependencies for reinstallation too and sends the
        resolver to PyPI looking for workspace-only packages that were never
        published there.
        """

        # Output is always captured; suppress_output and the presence of a task
        # only decide whether it is also displayed. A failure has to be able to
        # quote the installer whatever mode the run was in.
        quiet = suppress_output or task is not None

        if use_uv:
            env = os.environ.copy()
            env["VIRTUAL_ENV"] = python_dir

            cmd = [
                shutil.which("uv"),
                "pip",
                "install",
                "--verbose",
                # Ensure user-specified packages are used during build
                # An isolated build installs its own release packages
                "--no-build-isolation", 
                "-r",
                requirements_file
            ]

            if use_pre:
                cmd.append("--pre")

            if force:
                for dist in self._reinstall_targets(requirements_file):
                    cmd.extend(["--reinstall-package", dist])

            result = run_installer(cmd, env=env, task=task, quiet=quiet,
                                   line_parser=self._uv_line_parser)
            if not result.ok:
                self._report_install_failure(
                    requirements_file, result, update_info,
                    python_dir=python_dir, use_uv=True, use_pre=use_pre,
                    env=env)
        else: # Use pip
            import sys
            import platform

            ps = ";" if platform.system() == "Windows" else ":"
            env = os.environ.copy()
            env["PYTHONPATH"] = ps.join(sys.path)

            cmd = [
                get_venv_python(python_dir),
                "-m",
                "ivpm.pywrap",
                get_venv_python(python_dir),
                "-m",
                "pip",
                "install",
                "-r",
                requirements_file]

            if use_pre:
                cmd.append("--pre")

            cmds = [cmd]

            if force:
                # pip has no per-package equivalent of uv's --reinstall-package,
                # and a blanket --force-reinstall would drag in the transitive
                # dependencies this phase must leave alone. Instead let the pass
                # above resolve and install normally, then reinstall exactly the
                # named requirements with --no-deps.
                cmds.append(cmd + ["--force-reinstall", "--no-deps"])

            for c in cmds:
                result = run_installer(c, env=env, cwd=python_dir, task=task,
                                       quiet=quiet,
                                       line_parser=self._pip_line_parser)

                if not result.ok:
                    self._report_install_failure(
                        requirements_file, result, update_info,
                        python_dir=python_dir, use_uv=False, use_pre=use_pre,
                        env=env)

    def _report_install_failure(self, requirements_file, result, update_info,
                                python_dir=None, use_uv=False, use_pre=False,
                                env=None):
        """Attribute a failed install and report it. Always raises.

        Two routes to the culprit:

        1. The installer named a distribution, and that distribution is one we
           recorded emitting. Cheap and exact when it fires -- and it fires
           only when the installer happens to have worded things the way this
           version of IVPM expects.
        2. Re-install each of the phase's packages on its own and see which
           ones fail. Expensive, but it answers *which* without ever asking
           *why*, so no failure mode can slip past it and no installer
           rewording can break it.

        Route 1 is an optimization. Route 2 is the guarantee. Nothing may be
        built on the assumption that route 1 succeeds -- see the fast-path
        independence test in test_install_isolation.py.
        """
        lines = result.lines
        pkgs_by_key = getattr(update_info, "all_pkgs_by_key", None) or {}
        output = format_output_tail(lines)
        installer = "%s (exit %d)" % (" ".join(result.cmd), result.returncode)

        contributors = self._origins.by_group(requirements_file)

        origins = []
        identified_by = None
        note_text = None

        dist = _installer_failed_dist(lines)
        if dist is not None:
            origin = self._origins.resolve_dist(dist, group=requirements_file)
            if origin is not None:
                origins = [origin]
                identified_by = "the installer named '%s' in its output" % dist

        if not origins and len(contributors) > 1 and python_dir:
            iso = isolate(
                contributors,
                lambda o: self._retry_one(o, python_dir, use_uv, use_pre, env))
            if iso.culprits:
                origins = iso.culprits
            identified_by = isolation_identified_by(iso)
            note_text = isolation_note(iso)

        if not origins:
            origins = contributors
            if identified_by is None and origins:
                identified_by = "every package in this install phase"

        if not origins:
            # Nothing was recorded for this file -- it was written by a path
            # that does not record provenance (the site-config ivpm spec).
            # Report what there is rather than inventing an owner.
            fatal("failed to install Python packages%s"
                  % _format_installer_error(lines))
            return

        report_content_failure(
            "python", origins, pkgs_by_key,
            group=requirements_file,
            installer=installer,
            output=output,
            identified_by=identified_by,
            note_text=note_text)

    def _retry_one(self, origin, python_dir, use_uv, use_pre, env) -> bool:
        """Install a single requirement by itself; True if it succeeds.

        Run against the *real* venv with ``--no-deps``. Earlier phases have
        already been installed there, so each package still sees the
        dependencies it is entitled to, and only the one under test is being
        judged. ``--dry-run`` is not an option: it skips the build, and the
        build is where most of these failures actually live.

        This mutates the environment. The environment has already failed, so
        that is acceptable -- but the report says a diagnostic pass happened,
        because a user who later inspects the venv deserves to know why it
        looks the way it does.
        """
        import tempfile

        fd, path = tempfile.mkstemp(prefix="ivpm_isolate_", suffix=".txt")
        try:
            with os.fdopen(fd, "w") as fp:
                fp.write("%s\n" % origin.spec)

            if use_uv:
                cmd = [shutil.which("uv"), "pip", "install",
                       "--no-build-isolation", "--no-deps", "-r", path]
            else:
                cmd = [get_venv_python(python_dir), "-m", "pip", "install",
                       "--no-deps", "-r", path]
            if use_pre:
                cmd.append("--pre")

            return run_installer(cmd, env=env, cwd=python_dir, quiet=True).ok
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    @classmethod
    def _uv_line_parser(cls, line: str):
        return cls._parse_installer_line(line, True)

    @classmethod
    def _pip_line_parser(cls, line: str):
        return cls._parse_installer_line(line, False)

    @staticmethod
    def _parse_installer_line(line: str, use_uv: bool):
        """Return a short human-readable status string from one line of uv/pip output, or None."""
        stripped = line.strip()
        if not stripped:
            return None

        if use_uv:
            # "   Building pkgname @ file://..."  or  "      Built pkgname @ file://..."
            for prefix in ("Building ", "Built "):
                if stripped.startswith(prefix):
                    rest = stripped[len(prefix):]
                    name = rest.split(" @ ")[0].strip()
                    return f"{prefix.strip()} {name}"
            # "Resolved N packages in Xms", "Installed N packages in Xms",
            # "Prepared N packages in Xs", "Uninstalled N package..."
            for prefix in ("Resolved ", "Installed ", "Prepared ", "Uninstalled "):
                if stripped.startswith(prefix):
                    return stripped
            # "Using Python X environment at: ..."
            if stripped.startswith("Using Python"):
                return stripped
            # "DEBUG Selecting: pkg==version [compatible] (wheel.whl)"
            # → "Selecting pkg==version"
            if stripped.startswith("DEBUG Selecting: "):
                rest = stripped[len("DEBUG Selecting: "):]
                # "pkg==version [compatible] (...)" → "pkg==version"
                pkg_ver = rest.split(" [")[0].split(" (")[0].strip()
                return f"Selecting {pkg_ver}"
        else:
            # pip output
            if stripped.startswith("Collecting "):
                # "Collecting requests>=2.0" → "Collecting requests"
                pkg = stripped[len("Collecting "):].split(" ")[0]
                return f"Collecting {pkg}"
            if stripped.startswith("Downloading "):
                pkg = stripped[len("Downloading "):].split(" ")[0]
                return f"Downloading {pkg}"
            if stripped.startswith("Building wheel for "):
                pkg = stripped[len("Building wheel for "):].split(" ")[0]
                return f"Building {pkg}"
            if stripped.startswith("Successfully installed "):
                return stripped[:80]  # may be long; cap it

        return None


    def _reinstall_targets(self, requirements_file) -> List[str]:
        """Return the distribution names a requirements file directly names.

        Used to scope a forced reinstall to the requirements themselves,
        leaving their already-installed dependencies alone. Editable entries
        are resolved to the name declared by the project they point at, since
        the installer keys reinstallation on distribution name and not path.

        An entry whose name cannot be determined is dropped: omitting it costs
        a package its forced rebuild, whereas guessing wrong names a different
        distribution -- or none at all, which some installers reject outright.
        """
        targets = []
        seen = set()
        try:
            with open(requirements_file, "r") as fp:
                lines = fp.readlines()
        except OSError as e:
            warning("could not read requirements file %s (%s); nothing in it "
                    "will be force-reinstalled" % (requirements_file, e))
            return targets

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("-e ") or line.startswith("--editable"):
                path = line.split(None, 1)[1].strip() if " " in line else ""
                if line.startswith("--editable="):
                    path = line.split("=", 1)[1].strip()
                dist = _project_name_at(path)
                if dist is None:
                    # Visible, not logged: the user asked for a forced
                    # reinstall, and this is IVPM quietly not doing it for
                    # one of their packages. --py-force-install silently
                    # doing nothing is indistinguishable from a bug.
                    warning(
                        "could not determine the distribution name of "
                        "editable requirement %s (no '[project] name' in "
                        "pyproject.toml and no '[metadata] name' in "
                        "setup.cfg); it will NOT be force-reinstalled" % path)
                    continue
            elif line.startswith("-"):
                # A bare installer option (--pre, --index-url, ...) names nothing.
                continue
            elif line.endswith(".whl") or os.path.sep in line:
                # A wheel or local archive; the installer always reinstalls
                # these from the file, so no marking is needed.
                continue
            else:
                dist = _pep508_split(line)[0]
                if not dist:
                    continue

            if dist not in seen:
                seen.add(dist)
                targets.append(dist)

        return targets

    def _collect_build_requires(self, deps_dir, pkg_names) -> List[str]:
        """Return the PEP 517 build requirements declared by source packages.

        Packages are installed with ``--no-build-isolation`` so that each one
        builds against the workspace's editable packages rather than fresh
        copies pulled from PyPI.  The trade-off is that the installer no longer
        provisions ``[build-system] requires``, so a package whose build
        backend is not already present in the venv fails to build at all
        (``ModuleNotFoundError: No module named '<backend>'``).  Collecting the
        declarations here restores the half of build isolation that was given
        up, without giving back the isolation itself.

        Requirements naming a workspace package are skipped: those are already
        supplied by the editable install, and resolving them from PyPI would
        shadow the local source with a released copy.

        Specifiers are de-duplicated verbatim rather than by distribution name,
        so two packages asking for different floors of the same backend both
        constrain the resolve (and a genuine conflict surfaces as a resolution
        error instead of being silently decided here).
        """
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        workspace = set()
        for name in self.pkgs_info.keys():
            workspace.add(_pep508_split(name)[0])

        seen = set()
        requires = []
        for name in pkg_names:
            path = os.path.join(deps_dir, name, "pyproject.toml")
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as fp:
                    data = tomllib.load(fp)
            except Exception as e:
                # The old comment here claimed the install would report this
                # "far more precisely than we can". Under --no-build-isolation
                # it does not: the user gets a ModuleNotFoundError raised from
                # inside uv, naming the build backend and neither the package
                # nor the file. Report it here, where both are known.
                diag = check_python_manifest(os.path.dirname(path))
                located = diag.located() if diag is not None else path
                warning("package '%s': could not read build requirements from "
                        "%s (%s). If it needs a build backend that is not "
                        "already installed, its build will fail with a bare "
                        "ModuleNotFoundError." % (name, located, e),
                        getattr(self.pkgs_info.get(name), "srcinfo", None))
                continue

            build_system = data.get("build-system")
            if not isinstance(build_system, dict):
                continue
            for spec in (build_system.get("requires") or []):
                if not isinstance(spec, str) or not spec.strip():
                    continue
                spec = spec.strip()
                dist = _pep508_split(spec)[0]
                if dist in workspace:
                    _logger.debug(
                        "build requirement %s of %s is a workspace package; "
                        "leaving it to the editable install", dist, name)
                    continue
                if spec in seen:
                    continue
                seen.add(spec)
                requires.append(spec)
                # Remember who asked for it. A build backend that fails to
                # install is otherwise unattributable: the spec names the
                # backend, and nothing names the package that needs it.
                self._build_requires_src[spec] = name
                _logger.debug("build requirement %s (from %s)", spec, name)

        return requires

    def _write_requirements_txt(self,
                                packages_dir,
                                python_pkgs : List[Package],
                                file):
        """Writes a requirements file for pip to use in installing packages.

        Each emitted line is also recorded against the package that
        contributed it. This is the only point at which the two are known
        together: once the file is written it is just text, and recovering the
        mapping afterwards means guessing.
        """
        with open(file, "w") as fp:

            def emit(line, pkg, dist=None):
                fp.write("%s\n" % line)
                self._origins.record(line, pkg, file, language="python",
                                     dist=dist)

            for pkg in python_pkgs:

                if getattr(pkg, "src_type", None) != "pypi":
                    # Local source package installed from the packages dir. Keyed
                    # on src_type, not on a 'url' attribute: some source types
                    # have no 'url' but are still local editable trees (and lack
                    # the 'version'/'extras' fields the PyPI branch below needs).
                    # Determine editability: type_data takes priority, then default True
                    editable = True
                    td = get_type_data(pkg, PythonTypeData)
                    if td is not None and td.editable is not None:
                        editable = td.editable

                    # Extras from type_data
                    extras = None
                    if td is not None:
                        extras = td.extras
                    extras_str = "[%s]" % ",".join(extras) if extras else ""

                    # Prefer the location the source provider actually fetched
                    # into. Reconstructing it as <root deps-dir>/<name> assumes
                    # every package sits directly in the root deps-dir, which
                    # `deps-mode: nested` makes false: a package below a nested
                    # boundary lives in *its parent's* deps-dir. The venv stays
                    # root-scoped either way (one venv cannot hold two versions
                    # of a distribution), so the requirement line has to name
                    # the real path -- otherwise the installer is handed a
                    # directory that does not exist and reports the package as
                    # missing rather than mislocated.
                    pkg_path = getattr(pkg, "path", None) or "%s/%s" % (
                        packages_dir, pkg.name)
                    pkg_path = pkg_path.replace("\\", "/")
                    # The installer refers to a local tree by its declared
                    # distribution name, which need not match the directory.
                    dist = _project_name_at(pkg_path) or pkg.name
                    if editable:
                        emit("-e %s%s" % (pkg_path, extras_str), pkg, dist)
                    else:
                        emit("%s%s" % (pkg_path, extras_str), pkg, dist)
                else:
                    # PyPi package — build PEP 508 specifier: name[extras]version
                    # If a raw specifier was stored (e.g. from src: pyproject.toml),
                    # emit it verbatim so extras and markers are preserved exactly.
                    raw_spec = getattr(pkg, "_raw_spec", None)
                    if raw_spec is not None:
                        emit(raw_spec, pkg, _pep508_split(raw_spec)[0])
                    else:
                        # Extras: prefer type_data if present, fall back to pkg.extras (PackagePyPi)
                        td = get_type_data(pkg, PythonTypeData)
                        if td is not None and td.extras is not None:
                            extras = td.extras
                        else:
                            extras = getattr(pkg, "extras", None)
                        extras_str = "[%s]" % ",".join(extras) if extras else ""
                        if pkg.version is not None:
                            if pkg.version[0] in _VERSION_OPERATORS:
                                emit("%s%s%s" % (pkg.name, extras_str, pkg.version),
                                     pkg, pkg.name)
                            else:
                                emit("%s%s==%s" % (pkg.name, extras_str, pkg.version),
                                     pkg, pkg.name)
                        else:
                            emit("%s%s" % (pkg.name, extras_str), pkg, pkg.name)


def _installer_failed_dist(captured_lines: list):
    """The distribution the installer *said* it choked on, or None.

    A fast path, and nothing more. It reads installer prose, so it is wrong
    the moment uv or pip rewords a message, and it is silent about failure
    modes that name nothing at all. Callers must treat None as ordinary and
    fall back to a mechanism that cannot come up empty -- never as a reason to
    give up on attribution.
    """
    for line in reversed(captured_lines or []):
        m = re.search(r'Failed to build `([^`@\s]+)', line)
        if m:
            return m.group(1)
        stripped = line.strip()
        if stripped.startswith("Building "):
            return stripped[len("Building "):].split(" @ ")[0].split(" ")[0]
    return None


def _format_installer_error(captured_lines: list, tail: int = 20) -> str:
    """Return a newline-prefixed string with the last *tail* lines of installer
    output, or an empty string when nothing was captured.
    Prepends the name of the package being built when identifiable."""
    if not captured_lines:
        return ""
    body = format_output_tail(captured_lines, tail)
    if not body:
        return ""

    pkg_context = _installer_failed_dist(captured_lines)
    prefix = ("\n  while building package: %s" % pkg_context) if pkg_context else ""
    return prefix + body
