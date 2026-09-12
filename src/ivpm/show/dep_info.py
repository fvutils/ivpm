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
from typing import Dict, List, Optional


# Keys on a dependency entry that express *what was declared* (as opposed to
# what was resolved). Rendered for the BOM's "declared spec" column and for the
# spec of a dep-set entry that inheritance displaced.
_SPEC_KEYS = ("branch", "tag", "commit", "version", "module", "modulefile")


def declared_spec(pkg) -> Dict[str, str]:
    """Return the declared-spec fields of a *Package* as a plain dict.

    Only keys the entry actually declared appear, so an entry that pinned
    nothing yields ``{}`` rather than a row of nulls.
    """
    spec = {}
    url = getattr(pkg, "url", None)
    if url:
        spec["url"] = str(url)
    for key in _SPEC_KEYS:
        val = getattr(pkg, key, None)
        if val:
            spec[key] = str(val)
    return spec


def spec_label(pkg) -> str:
    """Render a Package's declared spec as one short human-readable string."""
    spec = declared_spec(pkg)
    ref = next((f"{k}={spec[k]}" for k in _SPEC_KEYS if k in spec), "")
    url = spec.get("url", "")
    if url and ref:
        return f"{url}@{ref}"
    return url or ref


@dc.dataclass
class OverrideInfo:
    """A dep-set entry that shadowed one inherited from a base dep-set."""
    base: str                       # base dep-set whose entry was displaced
    spec: str = ""                  # rendered spec of the DISPLACED entry
    displaced: Dict[str, str] = dc.field(default_factory=dict)


@dc.dataclass
class DepSetInfo:
    """Declared metadata for one dep-set, including its inheritance delta.

    ``contains`` is the full resolved leaf set. ``own``/``inherited_from``/
    ``overrides`` are the delta against the bases: which names this dep-set
    declared itself, which base supplied each of the rest, and which of its own
    declarations displaced a base's.
    """
    name: str
    description: Optional[str] = None
    doc: Optional[str] = None
    kind: Optional[str] = None
    uses: List[str] = dc.field(default_factory=list)
    contains: List[str] = dc.field(default_factory=list)
    own: List[str] = dc.field(default_factory=list)
    inherited_from: Dict[str, str] = dc.field(default_factory=dict)
    overrides: Dict[str, OverrideInfo] = dc.field(default_factory=dict)


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

    # Prose from the DECLARING manifest's dependency entry -- never from the
    # lock, which records what was resolved rather than what was declared.
    description: Optional[str] = None
    doc: Optional[str] = None
    # What the manifest entry declared (url/branch/tag/commit/version/module),
    # as opposed to what the lock resolved it to. See declared_spec().
    declared: Dict[str, str] = dc.field(default_factory=dict)

    # Resolved facts from the lock (None when no lock is available).
    reproducible: Optional[bool] = None
    patchset_id: Optional[str] = None
    # Patch fingerprints as recorded in the lock: name / source / md5 / ...
    patches: List[dict] = dc.field(default_factory=list)

    # Nested-dependency fields (nested-deps-design.md §7.5)
    scope: str = ""                            # scope path prefix; "" at the root
    nested: bool = False                       # opened a nested scope of its own
    # Set when the resolver stopped descending because an enclosing scope
    # already provides this package: the scope path it is provided at.
    cycle_elided: Optional[str] = None

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
    description: Optional[str] = None  # root project 'description' (may be None)
    doc: Optional[str] = None          # root project 'doc' (may be None)
    # Declared metadata for the selected dep-set, including its inheritance
    # delta. None when the manifest declares no such dep-set.
    dep_set_info: Optional[DepSetInfo] = None
