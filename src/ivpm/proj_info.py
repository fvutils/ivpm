#****************************************************************************
#* proj_info.py
#*
#* Copyright 2018-2024 Matthew Ballance and Contributors
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
#* Created on: Jan 19, 2020
#*     Author: mballance
#*
#****************************************************************************
import os
import dataclasses as dc
from enum import Enum, auto
from ivpm.packages_info import PackagesInfo
from .ivpm_yaml_reader import IvpmYamlReader
from .msg import error, fatal, note
from typing import Dict, List, Optional
from .env_spec import EnvSpec


class VenvMode(str, Enum):
    """Controls how (or whether) the python handler creates a virtual environment."""
    SKIP = "false"  # do not create a venv / skip all Python installation
    AUTO = "true"   # create venv, auto-detect tool (uv if available, else pip)
    UV   = "uv"     # create venv, force uv
    PIP  = "pip"    # create venv, force pip

    @staticmethod
    def parse(value) -> 'VenvMode':
        """Parse a yaml value (bool or str) into a VenvMode."""
        if isinstance(value, bool):
            return VenvMode.AUTO if value else VenvMode.SKIP
        s = str(value).strip().lower()
        for member in VenvMode:
            if member.value == s:
                return member
        valid = ", ".join(m.value for m in VenvMode)
        raise ValueError("Invalid venv value %r — expected one of: %s" % (value, valid))


@dc.dataclass
class PythonConfig:
    """Configuration for the python handler, parsed from ``package.with.python:``."""
    venv: VenvMode = VenvMode.AUTO
    system_site_packages: bool = False
    pre_release: bool = False


@dc.dataclass
class NodeConfig:
    """Configuration for the node handler, parsed from ``package.with.node:``."""
    manager: str = "npm"   # "npm" | "pnpm" | "yarn"
    version: str = None    # written to packages/node/.nvmrc; None means no .nvmrc
    env: bool = True       # patch packages.envrc with PATH/NODE_PATH


class ProjInfo():
    """Holds information read about a project from its IVPM file"""
    def __init__(self, is_src):
        self.is_src = is_src
        self.dependencies = []
        self.is_legacy = False

        # Dep-set to use when loading sub-dependencies
        self.target_dep_set = "default"
        self.dep_set_m : Dict[str,PackagesInfo] = {}
        self.setup_deps = set()
        
        self.ivpm_info = {}
        self.requirements_txt = None

        self.name = None
        self.version = None
        # Optional one-line summary from 'package.description'
        self.description = None
        # This should be set to the dep-set specified by the 'dep' 
        # statement or on the command-line
        self.default_dep_set = None
        self.deps_dir = "packages"

        self.process_deps = True
        self.paths : Dict[str, Dict[str, List[str]]] = {}
        self.env_settings : List[EnvSpec] = []
        # Raw (type_name, opts) pairs from 'package: { type: … }' in this project's ivpm.yaml.
        self.self_types : list = []
        # Configuration for the python handler from 'package.with.python:'.
        # None means the project did not declare ``with.python`` at all.
        self.python_config : Optional[PythonConfig] = None
        # Configuration for the node handler from 'package.with.node:'.
        # None means the project did not declare ``with.node`` at all.
        self.node_config : Optional[NodeConfig] = None
        # Generic handler configuration from 'package.with.<key>:' entries
        # that are not handled by the core reader.  Keyed by the with-key
        # name (e.g. "direnv"), value is the raw dict/value from YAML.
        self.handler_configs : Dict[str, object] = {}
        self.resolved_vars : Dict[str, str] = {}

    def has_dep_set(self, name):
        return name in self.dep_set_m.keys()
            
    def get_dep_set(self, name):
        return self.dep_set_m[name]
    
    def get_target_dep_set(self):
        if self.target_dep_set is None:
            raise Exception("target_dep_set is not specified")
        if self.target_dep_set not in self.dep_set_m.keys():
            raise Exception("Dep-set %s is not present in project %s" % (
                self.target_dep_set, self.name))
        return self.dep_set_m[self.target_dep_set]
    
    def set_dep_set(self, name, ds):
        self.dep_set_m[name] = ds

    def add_dependency(self, dep):
        self.dependencies.append(dep)

    @staticmethod
    def mkFromProj(proj_dir : str, cli_overrides=None, persisted_vars=None) -> 'ProjInfo':
        ret : ProjInfo = None
        
        # First, see if this is a new-style project
        if os.path.isfile(os.path.join(proj_dir, "ivpm.yaml")):
            note("Reading ivpm.yaml from project %s" % proj_dir)
            path = os.path.join(proj_dir, "ivpm.yaml");
            with open(path, "r") as fp:
                ret = IvpmYamlReader().read(
                    fp, path,
                    cli_overrides=cli_overrides,
                    persisted_vars=persisted_vars)
        else:
            # This doesn't appear to be an IVPM project
            # No IVPM-specific data to rely on here
            pass
        return ret

#    @property
#    def deps(self):
#        return self.dependencies


# Name of the deps-dir lockfile written by 'ivpm update'.
_LOCKFILE = "package-lock.json"


def find_lockfile_dir(project_dir: str, lockfile: str = _LOCKFILE) -> Optional[str]:
    """Search the direct sub-directories of *project_dir* for the one holding
    the lockfile (the deps dir).

    The conventional ``packages`` directory is preferred; otherwise the
    remaining sub-directories are scanned in a deterministic (sorted) order.
    Returns the absolute sub-directory path, or None when no lockfile is found.
    """
    conventional = os.path.join(project_dir, "packages")
    if os.path.isfile(os.path.join(conventional, lockfile)):
        return conventional
    try:
        names = sorted(os.listdir(project_dir))
    except OSError:
        return None
    for name in names:
        d = os.path.join(project_dir, name)
        if d == conventional:
            continue  # already checked above
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, lockfile)):
            return d
    return None


def resolve_deps_dir(project_dir: str,
                     proj_info: Optional['ProjInfo'] = None) -> str:
    """Locate the workspace deps directory (holds package-lock.json and the
    materialized sub-packages / perf records).

    Resolution order:
      1. Via ivpm.yaml, if present: use its declared ``deps-dir`` (defaults to
         ``packages`` when not explicitly set). Pass *proj_info* to reuse an
         already-parsed manifest and avoid a second read.
      2. Otherwise (no ivpm.yaml, or it could not be read): search the direct
         sub-directories for one containing the lockfile.
      3. Fall back to the conventional ``<project_dir>/packages``.
    """
    if proj_info is None:
        try:
            proj_info = ProjInfo.mkFromProj(project_dir)
        except Exception:
            # A malformed/unreadable manifest must not prevent us from locating
            # the deps dir; fall through to the lockfile search.
            proj_info = None
    if proj_info is not None and getattr(proj_info, "deps_dir", None):
        return os.path.join(project_dir, proj_info.deps_dir)
    found = find_lockfile_dir(project_dir)
    if found is not None:
        return found
    return os.path.join(project_dir, "packages")
