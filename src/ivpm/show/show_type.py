#****************************************************************************
#* show_type.py
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
"""Rendering for 'ivpm show type [name]'."""
import dataclasses
import json
import sys


def _get_all_type_infos():
    from ..pkg_content_type_rgy import PkgContentTypeRgy
    rgy = PkgContentTypeRgy.inst()
    return [rgy.get(n).content_type_info() for n in rgy.names()]


def _get_type_info(name: str):
    from ..pkg_content_type_rgy import PkgContentTypeRgy
    rgy = PkgContentTypeRgy.inst()
    if not rgy.has(name):
        return None
    return rgy.get(name).content_type_info()


def _any_plugins(infos) -> bool:
    return any(i.origin != "built-in" for i in infos)


# ---------------------------------------------------------------------------
# Rich output
# ---------------------------------------------------------------------------

def _rich_list(infos):
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    show_origin = _any_plugins(infos)
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Type", style="cyan bold")
    table.add_column("Description")
    table.add_column("Parameters", style="dim")
    if show_origin:
        table.add_column("Origin", style="dim")

    for info in infos:
        params = ", ".join(p.name for p in info.params) or "(none)"
        row = [info.name, info.description, params]
        if show_origin:
            row.append(info.origin)
        table.add_row(*row)
    console.print(table)


def _rich_detail(info):
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    console.print(f"\n[bold cyan]Type:[/] [bold]{info.name}[/]")
    if info.origin != "built-in":
        console.print(f"[dim]Origin:[/] {info.origin}")
    console.print(f"[bold]Description:[/] {info.description}\n")

    if info.params:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 1))
        table.add_column("Parameter")
        table.add_column("Type", style="dim")
        table.add_column("Req", style="dim")
        table.add_column("Default", style="dim")
        table.add_column("Description")
        for p in info.params:
            table.add_row(
                f"[cyan]{p.name}[/]",
                p.type_hint,
                "[bold red]✓[/]" if p.required else "",
                p.default or "",
                p.description,
            )
        console.print(table)
    else:
        console.print("[dim](no parameters)[/]\n")

    if info.notes:
        console.print("[bold]Notes:[/]")
        for line in info.notes.splitlines():
            console.print(f"  {line}")
        console.print()


# ---------------------------------------------------------------------------
# Plain-text output
# ---------------------------------------------------------------------------

def _plain_list(infos):
    show_origin = _any_plugins(infos)
    for info in infos:
        params = ", ".join(p.name for p in info.params) or "(none)"
        origin = f"  [{info.origin}]" if show_origin else ""
        print(f"{info.name:<16} {info.description}{origin}")
        print(f"{'':16} with-params: {params}")


def _plain_detail(info):
    print(f"Type:        {info.name}")
    if info.origin != "built-in":
        print(f"Origin:      {info.origin}")
    print(f"Description: {info.description}")
    if info.params:
        print("\nWith-parameters:")
        for p in info.params:
            req = " (required)" if p.required else ""
            default = f"  default: {p.default}" if p.default else ""
            print(f"  {p.name:<14} [{p.type_hint}]{req}{default}")
            print(f"  {'':14} {p.description}")
    else:
        print("\n(no with-parameters)")
    if info.notes:
        print("\nNotes:")
        for line in info.notes.splitlines():
            print(f"  {line}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class ShowType:
    def __call__(self, args):
        name = getattr(args, "name", None)
        as_json = getattr(args, "json", False)
        no_rich = getattr(args, "no_rich", False)

        if name:
            info = _get_type_info(name)
            if info is None:
                print(f"ivpm show type: unknown type '{name}'", file=sys.stderr)
                sys.exit(1)
            if as_json:
                print(json.dumps(dataclasses.asdict(info), indent=2))
            elif no_rich or not sys.stdout.isatty():
                _plain_detail(info)
            else:
                _rich_detail(info)
        else:
            infos = _get_all_type_infos()
            if as_json:
                print(json.dumps([dataclasses.asdict(i) for i in infos], indent=2))
            elif no_rich or not sys.stdout.isatty():
                _plain_list(infos)
            else:
                _rich_list(infos)
