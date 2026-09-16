#****************************************************************************
#* _compat.py
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
"""Standard-library shims for the Python versions IVPM supports (3.9+).

IVPM is frequently run by whatever interpreter a project already pins -- often
an older one than the developer's shell python -- so the floor here is the
oldest still-supported CPython, and every 3.10+ stdlib convenience has to be
spelled out rather than assumed.

Two APIs account for all of the current gap:

  * ``glob.glob(root_dir=)``            -- added in 3.10
  * ``importlib.metadata.entry_points(group=)`` -- added in 3.10; on 3.9
    ``entry_points()`` takes no arguments and returns a group -> list dict.
"""
import glob as _glob
import os
import sys
from typing import List

__all__ = ["glob_rel", "entry_points"]


def glob_rel(pattern: str,
             root_dir: str,
             recursive: bool = False) -> List[str]:
    """``glob.glob(pattern, root_dir=root_dir)`` -- matches relative to a root.

    Returns paths relative to ``root_dir``, as the 3.10+ builtin does.
    """
    if sys.version_info >= (3, 10):
        return list(_glob.glob(pattern, root_dir=root_dir, recursive=recursive))
    return _glob_rel_fallback(pattern, root_dir, recursive)


def _glob_rel_fallback(pattern: str,
                       root_dir: str,
                       recursive: bool = False) -> List[str]:
    """The pre-3.10 implementation of :func:`glob_rel`.

    Split out so the test suite can exercise it on ANY interpreter -- otherwise
    the only code path CI covers on a modern runner is the one that was never
    at risk.
    """
    # The root is a literal path, not part of the pattern: escape it so a
    # package checked out under a directory containing '[' or '*' still
    # matches only itself.
    base = _glob.escape(os.path.abspath(root_dir))
    matches = _glob.glob(os.path.join(base, pattern), recursive=recursive)
    root = os.path.abspath(root_dir)
    return [os.path.relpath(m, root) for m in matches]


class _EntryPointShim:
    """A 3.9 ``EntryPoint`` with the ``.dist`` back-reference 3.10+ provides.

    3.9's EntryPoint is a NamedTuple with no link back to the distribution that
    declared it, and no way to attach one (tuples are immutable). Provenance
    reporting -- ``ivpm show`` naming which package supplies a plugin -- needs
    that link, so wrap rather than degrade.
    """

    __slots__ = ("_ep", "dist")

    def __init__(self, ep, dist):
        self._ep = ep
        self.dist = dist

    @property
    def name(self):
        return self._ep.name

    @property
    def value(self):
        return self._ep.value

    @property
    def group(self):
        return self._ep.group

    def load(self):
        return self._ep.load()

    def __repr__(self):
        return "EntryPoint(name=%r, value=%r, group=%r)" % (
            self.name, self.value, self.group)


def entry_points(group: str) -> List:
    """``importlib.metadata.entry_points(group=...)`` as a list."""
    import importlib.metadata as _md

    if sys.version_info >= (3, 10):
        return list(_md.entry_points(group=group))
    return _entry_points_fallback(group)


def _entry_points_fallback(group: str) -> List:
    """The pre-3.10 implementation of :func:`entry_points`.

    Walking distributions rather than calling the argument-less
    ``entry_points()`` is what preserves the ``.dist`` back-reference; the flat
    dict that call returns has already thrown it away.

    Split out so the test suite can exercise it on any interpreter -- see
    :func:`_glob_rel_fallback`.
    """
    import importlib.metadata as _md
    import re

    found = []
    seen_dists = set()
    for dist in _md.distributions():
        # distributions() yields one entry per metadata directory found on the
        # path, so an editable install that still has a stale .egg-info beside
        # its .dist-info appears TWICE -- and every entry point it declares then
        # loads twice. 3.10+'s entry_points() collapses those by normalized
        # distribution name, first occurrence winning (sys.path order); do the
        # same, or a developer checkout reports doubled plugins.
        # Distribution.name is itself 3.10+; on 3.9 read it out of the metadata.
        name = getattr(dist, "name", None)
        if not name:
            try:
                name = dist.metadata["Name"]
            except Exception:
                name = None
        name = name or ""
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key:
            if key in seen_dists:
                continue
            seen_dists.add(key)
        for ep in (dist.entry_points or ()):
            if ep.group == group:
                found.append(_EntryPointShim(ep, dist))
    return found
