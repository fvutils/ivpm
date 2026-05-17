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
# DOT / Graphviz helpers
# ---------------------------------------------------------------------------

# Colours by package source type
_SRC_COLORS = {
    "git":     "lightyellow",
    "pypi":    "lightgreen",
    "gh-rls":  "lightcyan",
    "http":    "lightsalmon",
    "file":    "lightgrey",
}
_ROOT_COLOR   = "lightblue"
_DEFAULT_COLOR = "white"


def _dot_id(name: str) -> str:
    """Return a safe DOT node identifier."""
    return '"' + name.replace('"', '\\"') + '"'


def _dot_label(name: str, node: DepNode) -> str:
    """Build a multi-line DOT label for a package node."""
    parts = [name]
    ver = node.version_label()
    if ver:
        parts.append(ver)
    if node.src:
        parts.append(f"[{node.src}]")
    return "\\n".join(parts)


def _dot_graph(graph: DepGraph) -> str:
    """Return a Graphviz DOT representation of the dependency graph."""
    lines: List[str] = []
    lines.append("digraph deps {")
    lines.append("    rankdir=LR;")
    lines.append('    node [shape=box, style=filled, fontname="Helvetica"];')
    lines.append("")

    # Root project node
    root_id = _dot_id(graph.project)
    root_ver = f"\\n{graph.version}" if graph.version else ""
    lines.append(
        f"    {root_id} [label={_dot_id(graph.project + root_ver)}, "
        f'fillcolor="{_ROOT_COLOR}", shape=ellipse];'
    )

    # Collect unique nodes and edges via depth-first traversal
    seen_nodes: dict = {}   # name → DepNode
    edges: List[tuple] = [] # (from_name, to_name)

    def _visit(nodes: List[DepNode], parent: str) -> None:
        for node in nodes:
            edges.append((parent, node.name))
            if node.name not in seen_nodes:
                seen_nodes[node.name] = node
            if not node.shadowed:
                _visit(node.deps, node.name)

    _visit(graph.nodes, graph.project)

    # Emit package nodes
    lines.append("")
    for name, node in sorted(seen_nodes.items()):
        color = _SRC_COLORS.get(node.src, _DEFAULT_COLOR)
        style = "dashed" if node.shadowed else "filled"
        label = _dot_label(name, node)
        lines.append(
            f"    {_dot_id(name)} [label={_dot_id(label)}, "
            f'fillcolor="{color}", style="{style}"];'
        )

    # Emit edges
    lines.append("")
    for from_name, to_name in edges:
        lines.append(f"    {_dot_id(from_name)} -> {_dot_id(to_name)};")

    lines.append("}")
    return "\n".join(lines)


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
        as_dot   = getattr(args, "dot", False)
        no_rich  = getattr(args, "no_rich", False)
        proj_dir = getattr(args, "project_dir", None) or os.getcwd()
        dep_set  = getattr(args, "dep_set", None)
        output   = getattr(args, "output", None)

        # Mutual exclusion checks
        if as_tree and name:
            print("ivpm show deps: --tree and <name> are mutually exclusive.", file=sys.stderr)
            sys.exit(1)
        if as_dot and name:
            print("ivpm show deps: --dot and <name> are mutually exclusive.", file=sys.stderr)
            sys.exit(1)
        if as_dot and (as_json or as_tree):
            print("ivpm show deps: --dot is mutually exclusive with --json and --tree.", file=sys.stderr)
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
            self._show_detail(name, graph, as_json, no_rich, output)
        elif as_dot:
            self._show_dot(graph, output)
        elif as_tree:
            self._show_tree(graph, as_json, no_rich, output)
        else:
            self._show_flat(graph, as_json, no_rich, output)

    # ------------------------------------------------------------------

    def _write_output(self, text: str, output: Optional[str]) -> None:
        """Write text to a file or stdout."""
        if output:
            with open(output, "w") as f:
                f.write(text)
                if not text.endswith("\n"):
                    f.write("\n")
        else:
            print(text)

    def _show_flat(self, graph: DepGraph, as_json: bool, no_rich: bool, output: Optional[str] = None) -> None:
        if as_json:
            self._write_output(_flat_json(graph), output)
        elif output or no_rich or not sys.stdout.isatty():
            import io, contextlib
            if output:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    _plain_flat(graph)
                self._write_output(buf.getvalue().rstrip("\n"), output)
            else:
                _plain_flat(graph)
        else:
            _rich_flat(graph)

    def _show_tree(self, graph: DepGraph, as_json: bool, no_rich: bool, output: Optional[str] = None) -> None:
        if as_json:
            self._write_output(_tree_json(graph), output)
        elif output or no_rich or not sys.stdout.isatty():
            import io, contextlib
            if output:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    _plain_tree(graph)
                self._write_output(buf.getvalue().rstrip("\n"), output)
            else:
                _plain_tree(graph)
        else:
            _rich_tree(graph)

    def _show_dot(self, graph: DepGraph, output: Optional[str] = None) -> None:
        dot_text = _dot_graph(graph)
        self._write_output(dot_text, output)

    def _show_detail(self, name: str, graph: DepGraph, as_json: bool, no_rich: bool, output: Optional[str] = None) -> None:
        node = self._find_node(name, graph.nodes)
        if node is None:
            known = sorted(self._collect_names(graph.nodes))
            print(f"ivpm show deps: dependency '{name}' not found.", file=sys.stderr)
            if known:
                print(f"Known packages: {', '.join(known)}", file=sys.stderr)
            sys.exit(1)

        if as_json:
            self._write_output(_detail_json(node), output)
        elif output or no_rich or not sys.stdout.isatty():
            import io, contextlib
            if output:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    _plain_detail(node)
                self._write_output(buf.getvalue().rstrip("\n"), output)
            else:
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
