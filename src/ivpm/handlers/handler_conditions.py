#****************************************************************************
#* handler_conditions.py
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
#****************************************************************************
"""
Built-in conditions for leaf_when and root_when handler declarations.

Two callable protocols are used:

  leaf_when conditions:  callable(pkg: Package) -> bool
      Evaluated per-package on the fetch thread. Used to skip leaf dispatch
      for packages that a handler does not care about.

  root_when conditions:  callable(packages: list[Package]) -> bool
      Evaluated once against the full accumulated package list before
      on_root_post_load() is called. Used to skip entire root phases when
      no relevant packages were detected.

All conditions in a list are AND'd. None is equivalent to always-active.
"""


class _Always:
    """Sentinel condition that always returns True. Useful as an explicit marker."""

    def __call__(self, arg) -> bool:
        return True

    def __repr__(self) -> str:
        return "ALWAYS"


ALWAYS = _Always()


class HasType:
    """root_when condition: True if any package in the list has the named type.

    Checks pkg.pkg_type (set by leaf handlers) and pkg.type_data (set from
    ivpm.yaml type: declarations).

    Signature: callable(packages: list[Package]) -> bool
    """

    def __init__(self, type_name: str):
        self.type_name = type_name

    def __call__(self, packages) -> bool:
        for p in packages:
            # pkg_type is set by leaf handlers (e.g. PackageHandlerPython)
            if getattr(p, 'pkg_type', None) == self.type_name:
                return True
            # type_data list is set from ivpm.yaml type: declarations
            for td in (getattr(p, 'type_data', None) or []):
                if getattr(td, 'type_name', None) == self.type_name:
                    return True
        return False

    def __repr__(self) -> str:
        return f"HasType({self.type_name!r})"


class HasSourceType:
    """leaf_when or root_when condition filtering by package source type.

    When used as leaf_when:  callable(pkg: Package) -> bool
    When used as root_when:  callable(packages: list[Package]) -> bool
        (returns True if any package matches)
    """

    def __init__(self, src_type: str):
        self.src_type = src_type

    def __call__(self, arg) -> bool:
        # Support both single-package (leaf) and list (root) usage
        if isinstance(arg, list):
            return any(getattr(p, 'src_type', None) == self.src_type for p in arg)
        return getattr(arg, 'src_type', None) == self.src_type

    def __repr__(self) -> str:
        return f"HasSourceType({self.src_type!r})"
