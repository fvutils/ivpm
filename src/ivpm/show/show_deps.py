#****************************************************************************
#* show_deps.py
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
"""Rendering for 'ivpm show deps'."""
import dataclasses
import json
import os
import sys
import warnings
from typing import List, Optional

from .dep_info import DepGraph, DepNode


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------

def _node_to_dict(node: DepNode, include_deps: bool = True) -> dict:
    d = dataclasses.asdict(node)
    if not include_deps:
        d.pop("deps", None)
    return d


def _flat_json(graph: DepGraph) -> str:
    """Flat list JSON — unique nodes only (no shadowed), sorted by name."""
    rows = []
    seen = set()

    def _collect(nodes):
        for node in nodes:
            if node.name not in seen and not node.shadowed:
                seen.add(node.name)
                rows.append(_node_to_dict(node, include_deps=False))
            _collect(node.deps)

    _collect(graph.nodes)
    rows.sort(key=lambda r: r["name"])
    return json.dumps(rows, indent=2)


def _tree_json(graph: DepGraph) -> str:
    """Nested tree JSON."""
    def _node_dict(node: DepNode) -> dict:
        d = _node_to_dict(node, include_deps=True)
        d["deps"] = [_node_dict(c) for c in node.deps]
        return d

    result = {
        "project": graph.project,
        "version": graph.version,
        "dep_set": graph.dep_set,
        "deps": [_node_dict(n) for n in graph.nodes],
    }
    return json.dumps(result, indent=2)


def _detail_json(node: DepNode) -> str:
    d = _node_to_dict(node, include_deps=True)
    d["deps"] = [_node_to_dict(c, include_deps=False) for c in node.deps]
    return json.dumps(d, indent=2)


# ---------------------------------------------------------------------------
# Plain-text helpers
# ---------------------------------------------------------------------------

def _ref_str(node: DepNode) -> str:
    parts = []
    url = node.url_label()
    if url:
        parts.append(url)
    ref = node.ref_label()
    if ref and ref != url:
        parts.append(f"@ {ref}" if url else ref)
    return "  ".join(parts)


def _plain_flat(graph: DepGraph) -> None:
    rows = []
    seen = set()

    def _collect(nodes):
        for node in nodes:
            if node.name not in seen and not node.shadowed:
                seen.add(node.name)
                rows.append(node)
            _collect(node.deps)

    _collect(graph.nodes)
    rows.sort(key=lambda n: n.name)

    col_name = max((len(r.name)            for r in rows), default=7)
    col_src  = max((len(r.src)             for r in rows), default=5)
    col_spec = max((len(r.specifier)       for r in rows), default=11)
    col_ver  = max((len(r.version_label()) for r in rows), default=7)

    hdr = (
        f"{'Package':<{col_name}}  {'Src':<{col_src}}  "
        f"{'Declared by':<{col_spec}}  {'Version':<{col_ver}}  URL / Ref"
    )
    print(hdr)
    print("-" * (len(hdr) + 20))

    for node in rows:
        ver     = node.version_label()
        ref_url = node.ref_url_label()
        print(
            f"{node.name:<{col_name}}  {node.src:<{col_src}}  "
            f"{node.specifier:<{col_spec}}  {ver:<{col_ver}}  {ref_url}"
        )
        if node.also_requested_by:
            padding = " " * (col_name + 2 + col_src + 2 + col_spec + 2 + col_ver + 2)
            also = ", ".join(node.also_requested_by)
            print(f"{padding}also: {also}")


def _plain_tree(graph: DepGraph) -> None:
    print(f"{graph.project}" + (f"  ({graph.dep_set})" if graph.dep_set else ""))

    def _print_node(node: DepNode, prefix: str, is_last: bool) -> None:
        connector = "└── " if is_last else "├── "
        src_tag = f"[{node.src}]" if node.src else ""

        # Show version and URL/ref as separate pieces when both are present
        ver     = node.version_label()
        ref_url = node.ref_url_label()
        info_parts = [p for p in (ver, ref_url) if p]
        info = "  ".join(info_parts)

        label = f"{node.name}  {src_tag}  {info}".rstrip()

        if node.shadowed:
            label += f"  ← provided by {node.specifier}"
        elif node.also_requested_by:
            also = ", ".join(node.also_requested_by)
            label += f"  (also: {also})"

        print(prefix + connector + label)

        if not node.shadowed:
            child_prefix = prefix + ("    " if is_last else "│   ")
            for i, child in enumerate(node.deps):
                _print_node(child, child_prefix, i == len(node.deps) - 1)

    for i, node in enumerate(graph.nodes):
        _print_node(node, "", i == len(graph.nodes) - 1)


def _plain_detail(node: DepNode) -> None:
    def _kv(k, v):
        if v is not None and v != "" and v != []:
            print(f"{k:<20} {v}")

    print(f"Package:             {node.name}")
    _kv("Source:", node.src)
    _kv("URL:", node.url)
    _kv("Branch:", node.branch)
    _kv("Tag:", node.tag)
    _kv("Commit:", node.commit)
    _kv("Version:", node.version)
    _kv("Version (resolved):", node.version_resolved)
    _kv("Cache:", node.cache)
    _kv("Dep-set used:", node.dep_set)
    _kv("Declared by:", node.specifier)
    if node.also_requested_by:
        _kv("Also requested by:", ", ".join(node.also_requested_by))

    if node.deps:
        print("\nDeclared dependencies:")
        col = max(len(c.name) for c in node.deps)
        for child in node.deps:
            shadow = f"  ← provided by {child.specifier}" if child.shadowed else ""
            ref = _ref_str(child)
            src = f"[{child.src}]" if child.src else ""
            print(f"  {child.name:<{col}}  {src:<8}  {ref}{shadow}")


# ---------------------------------------------------------------------------
# Rich helpers
# ---------------------------------------------------------------------------

def _rich_flat(graph: DepGraph) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    rows = []
    seen = set()

    def _collect(nodes):
        for node in nodes:
            if node.name not in seen and not node.shadowed:
                seen.add(node.name)
                rows.append(node)
            _collect(node.deps)

    _collect(graph.nodes)
    rows.sort(key=lambda n: n.name)

    console = Console()
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Package", style="bold cyan")
    table.add_column("Src", style="dim")
    table.add_column("Declared by")
    table.add_column("Also requested by", style="dim")
    table.add_column("Version", style="yellow")
    table.add_column("URL / Ref", style="green")

    for node in rows:
        also = ", ".join(node.also_requested_by) if node.also_requested_by else ""
        table.add_row(
            node.name, node.src, node.specifier, also,
            node.version_label(), node.ref_url_label(),
        )

    console.print(table)


def _rich_tree(graph: DepGraph) -> None:
    from rich.console import Console
    from rich.tree import Tree
    from rich.text import Text

    console = Console()
    root_label = Text(graph.project, style="bold")
    root_label.append(f"  ({graph.dep_set})", style="dim")
    tree = Tree(root_label)

    def _add_node(parent_tree, node: DepNode) -> None:
        src_tag = Text(f"[{node.src}]", style="dim") if node.src else Text("")

        label = Text()
        label.append(node.name, style="bold cyan")
        label.append("  ")
        label.append_text(src_tag)

        ver = node.version_label()
        if ver:
            label.append("  ")
            label.append(ver, style="yellow")

        ref_url = node.ref_url_label()
        if ref_url:
            label.append("  ")
            label.append(ref_url, style="green")

        if node.shadowed:
            label.append(f"  ← provided by {node.specifier}", style="yellow italic")
        elif node.also_requested_by:
            also = ", ".join(node.also_requested_by)
            label.append(f"  (also: {also})", style="dim")

        branch = parent_tree.add(label)
        if not node.shadowed:
            for child in node.deps:
                _add_node(branch, child)

    for node in graph.nodes:
        _add_node(tree, node)

    console.print(tree)


def _rich_detail(node: DepNode) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    console.print(f"\n[bold cyan]{node.name}[/]  [dim][{node.src}][/]\n")

    def _row(k, v):
        if v is not None and v != "" and v != []:
            table.add_row(k, str(v))

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("Key", style="dim", width=22)
    table.add_column("Value")

    _row("URL", node.url)
    _row("Branch", node.branch)
    _row("Tag", node.tag)
    _row("Commit", node.commit)
    _row("Version", node.version)
    _row("Version (resolved)", node.version_resolved)
    _row("Cache", node.cache)
    _row("Dep-set used", node.dep_set)
    _row("Declared by", node.specifier)
    if node.also_requested_by:
        _row("Also requested by", ", ".join(node.also_requested_by))

    console.print(table)

    if node.deps:
        console.print("[bold]Declared dependencies:[/]")
        dep_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        dep_table.add_column("Name", style="cyan")
        dep_table.add_column("Src", style="dim")
        dep_table.add_column("Ref", style="green")
        dep_table.add_column("Note", style="yellow italic")
        for child in node.deps:
            note = f"← provided by {child.specifier}" if child.shadowed else ""
            dep_table.add_row(child.name, f"[{child.src}]", _ref_str(child), note)
        console.print(dep_table)


# ---------------------------------------------------------------------------
# Warning banner
# ---------------------------------------------------------------------------

def _warn_no_lock(no_rich: bool) -> None:
    msg = (
        "Warning: packages/package-lock.json not found — "
        "resolved identity unavailable. Run 'ivpm update' first."
    )
    if no_rich or not sys.stdout.isatty():
        print(msg, file=sys.stderr)
    else:
        try:
            from rich.console import Console
            Console(stderr=True).print(f"[yellow]{msg}[/]")
        except ImportError:
            print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class ShowDeps:
    def __call__(self, args):
        name     = getattr(args, "name", None)
        as_tree  = getattr(args, "tree", False)
        as_json  = getattr(args, "json", False)
        no_rich  = getattr(args, "no_rich", False)
        proj_dir = getattr(args, "project_dir", None) or os.getcwd()
        dep_set  = getattr(args, "dep_set", None)

        # Mutual exclusion: --tree and <name>
        if as_tree and name:
            print("ivpm show deps: --tree and <name> are mutually exclusive.", file=sys.stderr)
            sys.exit(1)

        from .dep_loader import DepLoader
        loader = DepLoader(proj_dir, dep_set=dep_set)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                graph = loader.load()
            except FileNotFoundError as exc:
                print(f"ivpm show deps: {exc}", file=sys.stderr)
                sys.exit(1)

        for w in caught:
            _warn_no_lock(no_rich)

        if name:
            self._show_detail(name, graph, as_json, no_rich)
        elif as_tree:
            self._show_tree(graph, as_json, no_rich)
        else:
            self._show_flat(graph, as_json, no_rich)

    # ------------------------------------------------------------------

    def _show_flat(self, graph: DepGraph, as_json: bool, no_rich: bool) -> None:
        if as_json:
            print(_flat_json(graph))
        elif no_rich or not sys.stdout.isatty():
            _plain_flat(graph)
        else:
            _rich_flat(graph)

    def _show_tree(self, graph: DepGraph, as_json: bool, no_rich: bool) -> None:
        if as_json:
            print(_tree_json(graph))
        elif no_rich or not sys.stdout.isatty():
            _plain_tree(graph)
        else:
            _rich_tree(graph)

    def _show_detail(self, name: str, graph: DepGraph, as_json: bool, no_rich: bool) -> None:
        node = self._find_node(name, graph.nodes)
        if node is None:
            known = sorted(self._collect_names(graph.nodes))
            print(f"ivpm show deps: dependency '{name}' not found.", file=sys.stderr)
            if known:
                print(f"Known packages: {', '.join(known)}", file=sys.stderr)
            sys.exit(1)

        if as_json:
            print(_detail_json(node))
        elif no_rich or not sys.stdout.isatty():
            _plain_detail(node)
        else:
            _rich_detail(node)

    @staticmethod
    def _find_node(name: str, nodes: List[DepNode]) -> Optional[DepNode]:
        """Depth-first search for a non-shadowed node by name."""
        for node in nodes:
            if node.name == name and not node.shadowed:
                return node
            found = ShowDeps._find_node(name, node.deps)
            if found:
                return found
        return None

    @staticmethod
    def _collect_names(nodes: List[DepNode]) -> List[str]:
        names = []
        for node in nodes:
            if not node.shadowed:
                names.append(node.name)
            names.extend(ShowDeps._collect_names(node.deps))
        return names
