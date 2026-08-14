#****************************************************************************
#* package.py
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
#* Created on: Jun 8, 2021
#*     Author: mballance
#*
#****************************************************************************

import logging
import os
import shutil
import stat
import sys
import dataclasses as dc
from enum import Enum, auto
from typing import Dict, List, Set, Optional, Tuple
from .project_ops_info import ProjectUpdateInfo
from .patch import PatchSpec, PatchSet, PatchCapability
from .utils import fatal, getlocstr

_logger = logging.getLogger("ivpm.package")


def _rmtree_force(path):
    """rmtree that recovers from cleared write bits (read-only cache copies,
    Windows dir/file copies). Never follows symlinks (rmtree's default)."""
    def _retry(func, p, *exc):
        # A read-only file cannot be unlinked from a read-only parent, so
        # restore the write bit on both before retrying.
        parent = os.path.dirname(p)
        for target in (parent, p):
            try:
                os.chmod(target, stat.S_IRWXU)
            except OSError:
                pass
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:
        shutil.rmtree(path, onerror=_retry)

class PackageType(Enum):
    Raw = auto()
    Python = auto()
    Unknown = auto()
    
PackageType2Spec = {
    PackageType.Raw     : "raw",
    PackageType.Python  : "python",
    PackageType.Unknown : "unknown"
    }

Spec2PackageType = {
    "raw"     : PackageType.Raw,
    "python"  : PackageType.Python,
    "unknown" : PackageType.Unknown
    }
    
class SourceType(Enum):
    Git = auto()
    Jar = auto()
    Tgz = auto()
    Txz = auto()
    Zip = auto()
    PyPi = auto()

Ext2SourceType = {
        ".git" : SourceType.Git,
        ".jar" : SourceType.Jar,
        ".tar.gz" : SourceType.Tgz,
        ".tar.xz" : SourceType.Txz,
        ".tgz" : SourceType.Tgz,
        ".zip" : SourceType.Zip
        }

SourceType2Ext = {
        SourceType.Git : ".git",
        SourceType.Jar : ".jar",
        SourceType.Tgz : ".tar.gz",
        SourceType.Txz : ".tar.xz",
        SourceType.Zip : ".zip"
    }

SourceType2Spec = {
        SourceType.Git  : "git",
        SourceType.Jar  : "jar",
        SourceType.Tgz  : "tgz",
        SourceType.Txz  : "txz",
        SourceType.Zip  : "zip",
        SourceType.PyPi : "pypi",
    }
Spec2SourceType = {
        "git"  : SourceType.Git,
        "jar"  : SourceType.Jar,
        "tgz"  : SourceType.Tgz,
        "txz"  : SourceType.Txz,
        "zip"  : SourceType.Zip,
        "pypi" : SourceType.PyPi
    }

@dc.dataclass
class Package(object):
    """Contains leaf-level information about a single package"""
    name : str
    srcinfo : object = None
    path : str = None
    pkg_type : PackageType = None
    src_type : str = None
    # type_data holds the list of validated TypeData objects produced from the 'type:' field.
    # Set by IvpmYamlReader; empty list means no explicit type (auto-detection may still apply).
    type_data : List['TypeData'] = dc.field(default_factory=list)
    # self_types holds the raw (type_name, opts) pairs read from the package's own ivpm.yaml.
    # Populated during dep resolution by package_updater; empty until then.
    self_types : List[Tuple[str, dict]] = dc.field(default_factory=list)
    # agents_config holds the 'agents:' dict from the dep entry (consumer-specified override).
    agents_config : Optional[dict] = None
    # patches holds the resolved, MD5-fingerprinted patch list from the dep entry's
    # 'patches:' key (empty by default -> today's behavior exactly). Populated by
    # IvpmYamlReader.read_deps for patch-capable sources only.
    patches : List['PatchSpec'] = dc.field(default_factory=list)

    process_deps : bool = True
    # Consumer-declared 'deps-mode' from this dependency entry. None means the
    # consumer said nothing, so the producer's manifest (or the enclosing
    # scope's mode) decides. See dep_scope.effective_mode.
    deps_mode : Optional[str] = None
    # Set by the resolver when a dependency cycle was elided at this package:
    # the scope path of the ancestor that already provides the same identity.
    cycle_elided : Optional[str] = None
    # Set by the resolver when this package opened a nested scope: the name of
    # the deps-dir it hosts. None means it is not a boundary.
    boundary_deps_dir : Optional[str] = None
    # This package's unique key: its path relative to the root deps-dir. Equal
    # to ``name`` in a flat workspace, so handlers that key on it behave
    # identically there. Set by the resolver before any handler sees the
    # package.
    scope_key : str = None
    # scope_key of the package that resolved this one (None at the root). The
    # scope-aware counterpart of ``resolved_by``, which is a bare name and so
    # is ambiguous once two scopes can hold the same name.
    resolved_by_key : str = None
    setup_deps : Set[str] = dc.field(default_factory=set)
    dep_set : str = None
    proj_info : 'ProjInfo'= None
    # Track which package caused this dependency to be resolved.
    # None means it was resolved at the root level.
    resolved_by : str = None
    # Provenance: set to "<url>#<dep-set>" when this package was contributed by
    # a `src: ivpm.yaml` dep-set factory (see package_ivpm_yaml.py).
    from_ivpm_source : Optional[str] = None

    # Virtual packages exist in memory (in all_pkgs) but have no packages-dir
    # representation -- they contribute deps without occupying a directory.
    # Overridden to True by factory sources (e.g. PackageIvpmYaml). Consumers
    # of all_pkgs/deps_dir test ``getattr(pkg, "virtual", False)`` to skip them.
    # (Plain class attribute, not a dataclass field -- it is identity, not data.)
    virtual = False

    # Option keys accepted on a dependency entry regardless of which source it
    # selects. These are consumed by the reader (IvpmYamlReader.read_deps) or by
    # Package.process_options, not by any single source provider. Each source
    # provider extends this set in dep_keys() with the options it understands.
    _BASE_DEP_KEYS = frozenset({
        "name",     # dependency name (required)
        "src",      # explicit source-type selector
        "type",     # content-type field
        "with",     # deprecated; reader emits a targeted migration error
        "agents",   # per-dep agents configuration
        "dep-set",  # which dep-set to pull from the sub-package
        "deps",     # 'skip' to suppress dependency processing
        "deps-mode", # consumer override of how this dep's own deps are placed
        "patches",  # cache-aware dependency patching (patch-capable sources only)
    })

    @classmethod
    def dep_keys(cls) -> Set[str]:
        """Return the set of option keys this source accepts on a dependency
        entry. The reader rejects any key not in this set, so validation is
        context-sensitive: a git dep and a pypi dep accept different options.
        Source providers override and extend via ``super().dep_keys() | {...}``,
        mirroring the ``process_options`` override chain."""
        return set(Package._BASE_DEP_KEYS)

    @property
    def patchset(self) -> 'PatchSet':
        """The resolved patch set for this package (empty by default)."""
        return PatchSet(tuple(self.patches))

    # --- Patch participation hooks (see patch-source-provider-contract.md) ---
    # The default Package is tier-0 (NONE): not patchable. Patch-capable source
    # providers override patch_capability() and fetch_pristine(); editable ones
    # additionally override retain_base()/restore_pristine().

    def patch_capability(self) -> 'PatchCapability':
        """How much patching this source supports. Default: NONE (tier 0)."""
        return PatchCapability.NONE

    def fetch_pristine(self, update_info, dest_dir: str, base_version: str) -> None:
        """Materialize a writable pristine tree of base_version at dest_dir.

        Must not apply patches and must not chmod the tree read-only (the
        resolver owns read-only locking). Default raises -- a source that
        declares a non-NONE patch_capability must override this."""
        raise NotImplementedError(
            "%s does not support patching (no fetch_pristine)" % self.src_type)

    def retain_base(self, pkg_dir: str, base_version: str, update_info) -> None:
        """Retain whatever is needed to restore pristine later (editable mode).
        Called once, after the pristine tree is materialized and before any
        patch is applied. Default: no-op."""
        pass

    def restore_pristine(self, pkg_dir: str, base_version: str, update_info) -> bool:
        """Revert pkg_dir to pristine base_version, preserving .ivpm/. Return
        True on success, False if pristine cannot be SAFELY restored (the caller
        then errors rather than destroying work). Default: False."""
        return False

    def working_tree_dirty(self, pkg_dir: str) -> Optional[bool]:
        """For a tree with NO patch manifest: does it have user modifications
        vs. its base? True/False, or None when cleanliness cannot be determined
        (e.g. an archive tree with no VCS and no base snapshot -> treated as
        pristine, never dirty). Default: None."""
        return None

    def patch_tree_status(self, pkg_dir: str, base_version: str, allowed_paths) -> str:
        """For a patched tree: is it 'clean' (equals base + the recorded patch
        result, i.e. only allowed_paths deviate from base), 'drift' (deviates
        beyond them), or 'unknown' (cannot tell)? Default: 'unknown'."""
        return "unknown"

    def build(self, pkgs_info):
        pass

    def status(self, pkgs_info):
        pass

    def sync(self, sync_info):
        from .pkg_sync import PkgSyncResult, SyncOutcome
        src = str(getattr(self, "src_type", "") or "non-vcs")
        return PkgSyncResult(
            name=self.name,
            src_type=src,
            path=os.path.join(sync_info.deps_dir, self.name),
            outcome=SyncOutcome.SKIPPED,
            skipped_reason=src,
        )

    # --- Destroy participation hooks (see destroy-design.md) ---
    # removal_safety() is the GATE: each source classifies ITSELF as
    # SAFE / UNVERIFIABLE / BLOCKED and supplies structured evidence (NOT
    # formatted prose). remove() is the TEARDOWN. `destroy` only aggregates;
    # it contains no source-specific logic.

    def removal_safety(self, remove_info) -> 'RemovalSafety':
        """Classify this package for the destroy gate, WITH justification.

        Returns a RemovalSafety verdict carrying the level and structured
        evidence (SafetyReason data, not formatted text). The base, VCS-agnostic
        policy:
          * missing path                         -> SAFE
          * symlink (cache read-only / deps-source / dir-file link)
                                                 -> SAFE (+ informational reason)
          * writable real dir, working_tree_dirty() is True   -> BLOCKED
          * writable real dir, working_tree_dirty() is None    -> UNVERIFIABLE
          * writable real dir, working_tree_dirty() is False   -> SAFE

        VCS sources override to return richer BLOCKED evidence (git:
        unpushed / modified / untracked / local-branch / stash / patch-drift)."""
        from .pkg_remove import RemovalSafety, SafetyLevel, SafetyReason

        path = self.path
        if path is None or not os.path.lexists(path):
            return RemovalSafety(SafetyLevel.SAFE)

        if os.path.islink(path):
            # cache-backed read-only mirror, deps-source mirror, or dir/file
            # link -- the content is shared/immutable or owned elsewhere.
            # Unlinking is inherently safe; never recurse into the target.
            target = None
            try:
                target = os.readlink(path)
            except OSError:
                pass
            return RemovalSafety(SafetyLevel.SAFE, [
                SafetyReason("symlink", data={"target": target})])

        dirty = self.working_tree_dirty(path)
        if dirty is True:
            return RemovalSafety(SafetyLevel.BLOCKED, [SafetyReason("modified")])
        elif dirty is None:
            return RemovalSafety(SafetyLevel.UNVERIFIABLE, [
                SafetyReason("unverifiable", label="no VCS / no base snapshot")])
        else:
            return RemovalSafety(SafetyLevel.SAFE)

    def remove(self, remove_info) -> 'PkgRemoveResult':
        """Remove this package, honoring its materialization shape and any state
        held outside its directory.

        Base implementation:
          * symlink (cache read-only / deps-source / dir-file link)
                -> os.unlink the link; NEVER recurse into the target
          * plain file
                -> os.unlink
          * writable directory
                -> rmtree, recovering from cleared write bits
          * missing path
                -> no-op (idempotent)

        Source types that register state elsewhere (Perforce client/view,
        git worktree/submodule, editable installs) override this to clean that
        state first, then remove the local tree. ``remove_info.dry_run`` reports
        the planned action and mutates nothing."""
        from .pkg_remove import PkgRemoveResult, RemoveOutcome

        path = self.path
        src = str(getattr(self, "src_type", "") or "non-vcs")
        dry = getattr(remove_info, "dry_run", False)

        def _result(removal, outcome, **kw):
            return PkgRemoveResult(name=self.name, src_type=src, path=path,
                                   removal=removal, outcome=outcome, **kw)

        if path is None or not os.path.lexists(path):
            return _result("noop", RemoveOutcome.SKIPPED)

        # symlink (any mode) or plain file -> unlink the link/file only
        if os.path.islink(path) or not os.path.isdir(path):
            if dry:
                return _result("unlink", RemoveOutcome.SKIPPED, removed_paths=[path])
            os.unlink(path)
            return _result("unlink", RemoveOutcome.REMOVED, removed_paths=[path])

        # writable real directory -> rmtree (does not follow symlinks)
        if dry:
            return _result("rmtree", RemoveOutcome.SKIPPED, removed_paths=[path])
        _rmtree_force(path)
        return _result("rmtree", RemoveOutcome.REMOVED, removed_paths=[path])

    def update(self, update_info : ProjectUpdateInfo) -> 'ProjInfo':
        from .proj_info import ProjInfo

        # Report this package for cache statistics (base packages are not cacheable)
        update_info.report_package(cacheable=False)

        info = ProjInfo.mkFromProj(
            os.path.join(update_info.deps_dir, self.name))
        
        return info
    
    def process_options(self, opts, si):
        self.srcinfo = si

        if "dep-set" in opts.keys():
            ds = opts["dep-set"]
            if not isinstance(ds, str):
                fatal(
                    "Package '%s': 'dep-set' must be a single dep-set name (a string), "
                    "not a %s @ %s\n"
                    "  Each dependency entry selects exactly one dep-set. To pull "
                    "several dep-sets from the same source, add one dependency entry "
                    "per dep-set, each with a distinct 'name'." % (
                        self.name, type(ds).__name__, getlocstr(ds)),
                    ds)
            _logger.debug("Using dep-set %s for package %s",
                ds, self.name)
            self.dep_set = ds

        if "deps-mode" in opts.keys():
            from .dep_mode import parse_deps_mode
            # Located against the entry's srcinfo: variable substitution
            # rebuilds scalar strings, dropping their own srcinfo.
            self.deps_mode = parse_deps_mode(opts["deps-mode"], si)

        if "deps" in opts.keys():
            if opts["deps"] == "skip":
                self.process_deps = False
            else:
                fatal("Unknown value for 'deps': %s" % opts["deps"])

        # Set pkg_type from 'type:' for backward compatibility.
        # The authoritative typed data is stored in type_data by IvpmYamlReader.
        if "type" in opts.keys():
            from .pkg_content_type import parse_type_field
            pairs = parse_type_field(opts["type"])
            if pairs:
                first_name = pairs[0][0]
                if first_name in Spec2PackageType.keys():
                    self.pkg_type = Spec2PackageType[first_name]

    @staticmethod
    def get_live_info(name: str, deps_dir: str) -> dict:
        """Query the live environment for additional display info.

        Called by 'ivpm show deps' when lock-file data is incomplete.
        Subclasses override this to inspect the installed state (e.g. scan
        dist-info dirs for PyPI packages, or read git HEAD for git packages).
        Returns a dict with any subset of: ``version_resolved``,
        ``commit_resolved``.
        """
        return {}

    def get_lock_entry(self):
        """Return extra fields for this package's lock-file entry.

        Subclasses (especially extension package types) override this to
        contribute type-specific identity fields to package-lock.json.
        Return None to fall through to the built-in type-specific
        serialization in package_lock._entry_from_pkg().
        """
        return None

    def spec_matches_lock(self, lock_entry):
        """Compare this package's current spec against a lock-file entry.

        Return True if the specs match, False if they differ, or
        None to fall through to the built-in comparison in
        package_lock._spec_matches_lock().
        """
        return None
    
    @staticmethod
    def mk(name, opts, si) -> 'Package':
        raise NotImplementedError()


def get_type_data(pkg: 'Package', cls):
    """Return the first TypeData entry in pkg.type_data that is an instance of cls, or None."""
    for td in pkg.type_data:
        if isinstance(td, cls):
            return td
    return None

