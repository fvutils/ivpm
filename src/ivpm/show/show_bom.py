#****************************************************************************
#* show_bom.py
#*
#* Copyright 2026 Matthew Ballance and Contributors
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
"""'ivpm show bom' -- the workspace's bill of materials.

One row per package in the resolved closure, joining what the manifest
*declared* (name, source, prose, pin) with what the lock *resolved* (version,
commit, reproducibility, patch fingerprints) and what the package itself
publishes (license, home page, documentation).

This is a pure projection over data that already exists: no resolution and no
fetching happen here. ``--json`` diffed between two release tags is a
supply-chain change report.
"""
import dataclasses as dc
import json
import os
import sys
import warnings
from typing import Dict, List, Optional

from ..tui_theme import make_console, S_LABEL
from .dep_info import DepGraph, DepNode


@dc.dataclass
class BomRow:
    """One package in the bill of materials."""
    name: str
    src: str = ""
    # Declared (manifest) -----------------------------------------------
    description: Optional[str] = None
    doc: Optional[str] = None
    # url / branch / tag / commit / version / module, as declared
    declared: Dict[str, str] = dc.field(default_factory=dict)
    # Resolved (lock) ---------------------------------------------------
    version_resolved: Optional[str] = None
    commit_resolved: Optional[str] = None
    # False for sources that cannot be pinned (dir:, module:); None with no lock.
    reproducible: Optional[bool] = None
    cache: Optional[bool] = None
    patches: List[dict] = dc.field(default_factory=list)
    patchset_id: Optional[str] = None
    # Published by the package itself (its ivpm.yaml, else its upstream
    # pyproject.toml / package.json) -----------------------------------
    license: Optional[str] = None
    homepage: Optional[str] = None
    documentation: Optional[str] = None
    # Graph position ----------------------------------------------------
    specifier: str = ""        # who declared it ("root" or a package name)
    dep_set: Optional[str] = None
    scope: str = ""

    def version_label(self) -> str:
        """Best available identity for display: resolved version, else commit,
        else whatever the manifest pinned."""
        if self.version_resolved:
            return self.version_resolved
        if self.commit_resolved:
            return self.commit_resolved[:8]
        for key in ("version", "tag", "branch", "commit", "module"):
            if key in self.declared:
                val = self.declared[key]
                return val[:8] if key == "commit" else val
        return ""

    def patch_label(self) -> str:
        """Short patch column: fingerprint prefixes, one per applied patch."""
        return ", ".join(
            "%s(%s)" % (p.get("name", "?"), str(p.get("md5", ""))[:8])
            for p in self.patches)


@dc.dataclass
class Bom:
    project: str
    version: Optional[str]
    dep_set: str
    rows: List[BomRow]
    lock_available: bool = True
    description: Optional[str] = None
    license: Optional[str] = None
    homepage: Optional[str] = None
    documentation: Optional[str] = None
    maintainers: List[str] = dc.field(default_factory=list)


def _unique_nodes(graph: DepGraph) -> List[DepNode]:
    """Flatten the graph to the unique, non-shadowed packages, sorted by name.

    Matches the flat view of 'show deps': a shadowed entry is another
    declaration of a package already listed, not a second package.
    """
    seen = set()
    rows: List[DepNode] = []

    def _collect(nodes):
        for node in nodes:
            key = node.scope + node.name
            if key not in seen and not node.shadowed:
                seen.add(key)
                rows.append(node)
            _collect(node.deps)

    _collect(graph.nodes)
    rows.sort(key=lambda n: (n.scope, n.name))
    return rows


def build_bom(graph: DepGraph, deps_dir: str) -> Bom:
    """Project a loaded DepGraph into a Bom.

    *deps_dir* is where the packages were materialized; it is read only for
    each package's published metadata (license / homepage / documentation).
    """
    from ..upstream_meta import package_metadata

    rows = []
    for node in _unique_nodes(graph):
        meta = package_metadata(os.path.join(deps_dir, node.scope + node.name))
        rows.append(BomRow(
            name=node.name,
            src=node.src,
            description=node.description,
            doc=node.doc,
            declared=dict(node.declared),
            version_resolved=node.version_resolved,
            commit_resolved=node.commit,
            reproducible=node.reproducible,
            cache=node.cache,
            patches=list(node.patches),
            patchset_id=node.patchset_id,
            license=meta.get("license"),
            homepage=meta.get("homepage"),
            documentation=meta.get("documentation"),
            specifier=node.specifier,
            dep_set=node.dep_set,
            scope=node.scope,
        ))

    return Bom(
        project=graph.project,
        version=graph.version,
        dep_set=graph.dep_set,
        rows=rows,
        lock_available=graph.lock_available,
        description=graph.description,
    )


def _bom_json(bom: Bom) -> str:
    return json.dumps({
        "project": bom.project,
        "version": bom.version,
        "dep_set": bom.dep_set,
        "description": bom.description,
        "license": bom.license,
        "homepage": bom.homepage,
        "documentation": bom.documentation,
        "maintainers": bom.maintainers,
        "lock_available": bom.lock_available,
        "packages": [dc.asdict(r) for r in bom.rows],
    }, indent=2)


def _bom_plain(bom: Bom) -> None:
    hdr = bom.project + (f"  v{bom.version}" if bom.version else "")
    print(f"{hdr}  ({bom.dep_set})")
    print()

    cols = [
        ("Package", lambda r: r.name),
        ("Src", lambda r: r.src),
        ("Version / Ref", lambda r: r.version_label()),
        ("License", lambda r: r.license or ""),
        ("Repro", lambda r: "" if r.reproducible is None
                            else ("yes" if r.reproducible else "no")),
        ("Patches", lambda r: r.patch_label()),
        ("Declared by", lambda r: r.specifier),
    ]
    widths = [max(len(title), max((len(fn(r)) for r in bom.rows), default=0))
              for title, fn in cols]

    print("  ".join(t.ljust(w) for (t, _), w in zip(cols, widths)))
    print("-" * (sum(widths) + 2 * (len(cols) - 1)))
    for r in bom.rows:
        print("  ".join(fn(r).ljust(w) for (_, fn), w in zip(cols, widths)))
        if r.description:
            print(f"    {r.description}")


def _bom_rich(bom: Bom) -> None:
    from rich.table import Table
    from rich.text import Text
    from rich import box

    console = make_console()
    hdr = Text(bom.project, style="bold cyan")
    if bom.version:
        hdr.append(f"  v{bom.version}", style="green")
    hdr.append(f"  ({bom.dep_set})", style=S_LABEL)
    console.print(hdr)
    if bom.description:
        console.print(Text(f"  {bom.description}", style="italic"))
    console.print()

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Package", style="bold cyan")
    table.add_column("Src", style="secondary")
    table.add_column("Version / Ref", style="yellow")
    table.add_column("License")
    table.add_column("Repro")
    table.add_column("Patches", style="magenta")
    table.add_column("Declared by", style="secondary")
    table.add_column("Description")

    for r in bom.rows:
        if r.reproducible is None:
            repro = ""
        else:
            repro = "[green]yes[/]" if r.reproducible else "[yellow]no[/]"
        table.add_row(r.name, r.src, r.version_label(), r.license or "",
                      repro, r.patch_label(), r.specifier, r.description or "")

    console.print(table)


def _warn_no_lock(no_rich: bool) -> None:
    msg = ("Warning: package-lock.json not found — the BOM will report no "
           "resolved versions. Run 'ivpm update' first.")
    if no_rich or not sys.stdout.isatty():
        print(msg, file=sys.stderr)
    else:
        try:
            make_console(stderr=True).print(f"[yellow]{msg}[/]")
        except ImportError:
            print(msg, file=sys.stderr)


class ShowBom:
    def __call__(self, args):
        as_json = getattr(args, "json", False)
        no_rich = getattr(args, "no_rich", False)
        proj_dir = getattr(args, "project_dir", None) or os.getcwd()
        dep_set = getattr(args, "dep_set", None)
        output = getattr(args, "output", None)

        from .dep_loader import DepLoader
        from ..proj_info import resolve_deps_dir

        loader = DepLoader(proj_dir, dep_set=dep_set)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                graph = loader.load()
            except FileNotFoundError as exc:
                print(f"ivpm show bom: {exc}", file=sys.stderr)
                sys.exit(1)
        for _ in caught:
            _warn_no_lock(no_rich)

        deps_dir = resolve_deps_dir(proj_dir)
        bom = build_bom(graph, deps_dir)

        # Root project metadata: the same manifest-first rule as every row.
        root_meta = _root_metadata(proj_dir)
        bom.license = root_meta.get("license")
        bom.homepage = root_meta.get("homepage")
        bom.documentation = root_meta.get("documentation")
        bom.maintainers = root_meta.get("maintainers") or []

        if as_json:
            _write_output(_bom_json(bom), output)
        elif output or no_rich or not sys.stdout.isatty():
            if output:
                import io, contextlib
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    _bom_plain(bom)
                _write_output(buf.getvalue().rstrip("\n"), output)
            else:
                _bom_plain(bom)
        else:
            _bom_rich(bom)


def _root_metadata(proj_dir: str) -> dict:
    """Root project metadata, including 'maintainers' (which is manifest-only)."""
    from ..upstream_meta import package_metadata
    from ..proj_info import ProjInfo

    meta = dict(package_metadata(proj_dir))
    try:
        info = ProjInfo.mkFromProj(proj_dir)
    except Exception:
        info = None
    if info is not None and getattr(info, "maintainers", None):
        meta["maintainers"] = list(info.maintainers)
    return meta


def _write_output(text: str, output: Optional[str]) -> None:
    if output:
        with open(output, "w") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
    else:
        print(text)
