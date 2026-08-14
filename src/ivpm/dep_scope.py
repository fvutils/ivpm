#****************************************************************************
#* dep_scope.py
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
#****************************************************************************
"""Dependency scopes -- the tree that ``deps-mode: nested`` builds.

A *scope* is one deps-dir plus the namespace of packages resolved into it.
A flat workspace has exactly one scope (the root), which is today's behavior
unchanged. A package resolved under ``deps-mode: nested`` becomes a *boundary*:
it opens a child scope rooted at its own deps-dir, and its dependencies resolve
there rather than at the root.

This module is pure -- no filesystem, no network. The resolver
(``package_updater``) owns all I/O and all mutation of the tree, and mutates it
only from the main loop (see nested-deps-impl-plan.md P2), which is why nothing
here needs locking.

See nested-deps-design.md §4.
"""
import os
import dataclasses as dc
from typing import Dict, Iterator, Optional

from .dep_mode import FLATTEN, NESTED
from .msg import fatal

#: Backstop against a nesting chain that never repeats an identity (e.g. an
#: unbounded sequence of distinct versions). The ancestry check in
#: check_recursion is the actual termination proof; this only bounds pathology.
_DEFAULT_MAX_DEPTH = 32


def max_dep_depth() -> int:
    """The nesting-depth backstop, overridable via ``IVPM_MAX_DEP_DEPTH``."""
    raw = os.environ.get("IVPM_MAX_DEP_DEPTH")
    if raw is None:
        return _DEFAULT_MAX_DEPTH
    try:
        value = int(raw)
    except ValueError:
        fatal("IVPM_MAX_DEP_DEPTH must be an integer, got '%s'" % raw)
    if value < 1:
        fatal("IVPM_MAX_DEP_DEPTH must be >= 1, got %d" % value)
    return value


@dc.dataclass
class DepScope(object):
    """One deps-dir and the namespace of packages resolved into it."""

    #: Path of this scope relative to its parent's deps-dir ("" for the root),
    #: e.g. "toolB/packages".
    name : str

    #: Absolute path of this scope's deps directory.
    deps_dir : str

    parent : Optional['DepScope'] = None

    #: The mode inherited by dependencies resolved into this scope. The root
    #: scope starts at the root manifest's declared mode (default "flatten").
    mode : str = FLATTEN

    #: This scope's namespace: bare package name -> Package.
    packages : Dict[str, 'Package'] = dc.field(default_factory=dict)

    #: Child scopes, keyed by their ``name``.
    children : Dict[str, 'DepScope'] = dc.field(default_factory=dict)

    #: The package that opened this scope (None for the root).
    owner : Optional['Package'] = None

    @property
    def is_root(self) -> bool:
        return self.parent is None

    @property
    def path(self) -> str:
        """This scope's stable identity: its path relative to the root
        deps-dir. "" for the root, else e.g. "toolB/packages/libC/packages"."""
        if self.parent is None:
            return ""
        prefix = self.parent.path
        return "%s/%s" % (prefix, self.name) if prefix else self.name

    @property
    def depth(self) -> int:
        return 0 if self.parent is None else self.parent.depth + 1

    def ancestors(self) -> Iterator['DepScope']:
        """Yield this scope, then each enclosing scope, up to the root."""
        scope = self
        while scope is not None:
            yield scope
            scope = scope.parent

    def open_child(self, pkg, deps_dir_name: str, mode: str) -> 'DepScope':
        """Open (or return the already-open) child scope owned by *pkg*.

        *pkg.path* must already be materialized as a real directory -- the
        resolver promotes a symlinked boundary first (see P1).
        """
        name = "%s/%s" % (pkg.name, deps_dir_name)
        existing = self.children.get(name)
        if existing is not None:
            return existing
        child = DepScope(
            name=name,
            deps_dir=os.path.join(pkg.path, deps_dir_name),
            parent=self,
            mode=mode,
            owner=pkg)
        self.children[name] = child
        return child

    def find_package(self, name: str) -> Optional['Package']:
        """Look up *name* in this scope only. Scopes do NOT fall back to their
        parent: a nested sub-tree resolves independently, which is the whole
        point of nesting."""
        return self.packages.get(name)


def scope_path(scope: DepScope, name: str) -> str:
    """The registry/lock key for package *name* resolved into *scope*.

    In a flat workspace this is just the bare package name, so lock keys and
    ``all_pkgs`` keys are unchanged from today.
    """
    prefix = scope.path
    return "%s/%s" % (prefix, name) if prefix else name


def effective_mode(pkg, scope: DepScope) -> str:
    """The mode governing the dependencies *of* *pkg*, resolved in *scope*.

    Precedence (design §4.2):

      1. the consumer's dep-entry ``deps-mode``  -- always wins
      2. the producer's own manifest ``deps-mode``
      3. the enclosing scope's mode              -- inherited

    A declared ``flatten`` at (1) or (2) stops propagation, which is why "not
    declared" is represented as None rather than defaulting to "flatten".
    """
    consumer = getattr(pkg, "deps_mode", None)
    if consumer is not None:
        return consumer

    proj_info = getattr(pkg, "proj_info", None)
    if proj_info is not None:
        # A dep-set-level declaration is more specific than the package level.
        ds_mode = None
        dep_set = getattr(pkg, "dep_set", None)
        if dep_set is not None and proj_info.has_dep_set(dep_set):
            ds_mode = getattr(proj_info.get_dep_set(dep_set), "deps_mode", None)
        if ds_mode is not None:
            return ds_mode
        producer = getattr(proj_info, "deps_mode", None)
        if producer is not None:
            return producer

    return scope.mode


def pkg_identity(pkg) -> str:
    """A stable identity string for *pkg*: what it actually resolved to.

    Derived from the same fields the lock records, so the recursion guard and
    the lock cannot drift. Resolved identity (commit / version) is used when
    available; an unresolved package falls back to its spec (url, branch, tag),
    which is still enough to recognize a repeat.
    """
    from .package_lock import _entry_from_pkg
    entry = _entry_from_pkg(pkg)
    # resolved_by / dep_set / reproducible describe the *context* a package was
    # reached through, not which package it is.
    items = sorted((k, str(v)) for k, v in entry.items()
                   if k not in ("resolved_by", "dep_set", "reproducible"))
    return ";".join("%s=%s" % (k, v) for k, v in items)


def check_recursion(pkg, scope: DepScope, max_depth: Optional[int] = None
                    ) -> Optional[str]:
    """Decide whether descending into *pkg* under *scope* would recur forever.

    Returns the ``path`` of the enclosing scope whose owner is the same package
    (same name, same resolved identity) -- meaning the sub-tree beneath *pkg*
    has already been resolved above and descending again would repeat it. The
    caller elides: it still materializes *pkg*, but does not process its deps.

    Returns None when there is no repeat.

    Because a repeat of an identity terminates the chain, an infinite descent
    requires infinitely many *distinct* identities. *max_depth* is a backstop
    against that pathology and raises rather than eliding.
    """
    if max_depth is None:
        max_depth = max_dep_depth()

    identity = None
    for ancestor in scope.ancestors():
        owner = ancestor.owner
        if owner is None:
            continue
        if owner.name != pkg.name:
            continue
        if identity is None:
            identity = pkg_identity(pkg)
        if pkg_identity(owner) == identity:
            return ancestor.parent.path if ancestor.parent is not None else ""

    if scope.depth >= max_depth:
        chain = _format_chain(pkg, scope)
        fatal(
            "Nested dependency depth limit (%d) exceeded while resolving '%s'.\n"
            "  Chain: %s\n"
            "  Every level resolved a *different* version, so the cycle guard "
            "could not terminate the descent. Pin a version, or set "
            "IVPM_MAX_DEP_DEPTH to raise the limit." % (
                max_depth, pkg.name, chain))

    return None


def _format_chain(pkg, scope: DepScope) -> str:
    """Render the nesting chain, root-first, ending at *pkg*."""
    owners = []
    for ancestor in scope.ancestors():
        if ancestor.owner is not None:
            owners.append(ancestor.owner.name)
    owners.reverse()
    owners.append(pkg.name)
    return " -> ".join(owners)


def is_nested(mode: str) -> bool:
    return mode == NESTED
