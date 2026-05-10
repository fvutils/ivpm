#****************************************************************************
#* dep_info.py
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
"""Data model for 'ivpm show deps'.

DepNode represents one resolved package entry.  DepGraph is the complete
picture for a project.  Both are pure data — no I/O happens here.
"""
import dataclasses as dc
from typing import List, Optional


@dc.dataclass
class DepNode:
    """One resolved package in the dependency graph."""
    name: str
    src: str                                    # "git", "pypi", "gh-rls", …
    specifier: str                              # "root" or package name of first declarer
    shadowed: bool = False                      # True when provided by a higher-level owner
    also_requested_by: List[str] = dc.field(default_factory=list)

    # Resolved identity — populated from package-lock.json; None when lock absent
    url: Optional[str] = None
    branch: Optional[str] = None
    tag: Optional[str] = None
    commit: Optional[str] = None
    version: Optional[str] = None
    version_resolved: Optional[str] = None
    cache: Optional[bool] = None
    dep_set: Optional[str] = None              # dep-set used for this pkg's sub-deps

    # Sub-dependencies (populated in tree mode; empty for flat/detail)
    deps: List['DepNode'] = dc.field(default_factory=list)

    def version_label(self) -> str:
        """Return the resolved (or requested) version for a dedicated Version column.

        Returns the resolved version if available, the requested version
        otherwise, and an empty string when no version information is present.
        """
        if self.version_resolved:
            return self.version_resolved
        if self.version:
            return self.version
        return ""

    def ref_url_label(self) -> str:
        """Return URL + git-style ref (branch / commit / tag) for display.

        Unlike ref_label(), version strings are never included here — version
        data should be shown in the separate 'Version' column via version_label().
        """
        parts = []
        url = self.url_label()
        if url:
            parts.append(url)
        ref = ""
        if self.commit:
            ref = self.commit[:8]
        elif self.tag:
            ref = self.tag
        elif self.branch:
            ref = self.branch
        if ref:
            parts.append(f"@ {ref}" if url else ref)
        return "  ".join(parts)

    def ref_label(self) -> str:
        """Return a short human-readable version/ref string for display.

        Used in tree and detail views where version and URL/ref are shown
        together.  For flat views prefer version_label() + ref_url_label().
        """
        if self.commit:
            return self.commit[:8]
        if self.tag:
            return self.tag
        if self.version_resolved:
            return self.version_resolved
        if self.version:
            return self.version
        if self.branch:
            return self.branch
        return ""

    def url_label(self) -> str:
        """Return a short URL suitable for display (strip protocol prefix)."""
        url = self.url or ""
        for prefix in ("https://", "http://", "git@", "file://"):
            if url.startswith(prefix):
                return url[len(prefix):]
        return url


@dc.dataclass
class DepGraph:
    """Complete dependency graph for one project workspace."""
    project: str                    # root project name
    version: Optional[str]          # root project version (may be None)
    dep_set: str                    # dep-set selected at root
    nodes: List[DepNode]            # top-level dep nodes (flat unique list; tree inside)
    lock_available: bool = True     # False → resolved identity fields will be None
