#****************************************************************************
#* scope_keys.py
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
"""Keying handler accumulations by scope rather than by bare name.

Handlers accumulate one entry per package. Keyed by bare name, two packages
with the same name in different dependency scopes silently overwrite each
other -- one of them just disappears from the venv, the envrc, or the flow map.

Every helper here degrades to the bare name when the resolver did not set a
scope key, so a flat workspace produces byte-identical output to before
nesting existed, and a synthetic Package in a test still works.

See nested-deps-design.md §7.1 and nested-deps-impl-plan.md §7.
"""
import os


def pkg_key(pkg) -> str:
    """The accumulation key for *pkg*: its path relative to the root deps-dir.

    Equal to ``pkg.name`` in a flat workspace.
    """
    return getattr(pkg, "scope_key", None) or pkg.name


def resolver_key(pkg):
    """The key of the package that resolved *pkg*, or None at the root."""
    key = getattr(pkg, "resolved_by_key", None)
    if key is not None:
        return key
    return getattr(pkg, "resolved_by", None)


def pkg_rel_dir(pkg, deps_dir: str) -> str:
    """*pkg*'s directory relative to the root *deps_dir*, slash-separated.

    Prefers the real materialized path, so a package is always referenced
    where it actually is; falls back to the scope key.
    """
    path = getattr(pkg, "path", None)
    if path:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(deps_dir))
        if not rel.startswith(".."):
            return rel.replace(os.sep, "/")
    return pkg_key(pkg)
