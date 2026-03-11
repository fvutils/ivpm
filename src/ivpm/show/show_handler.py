#****************************************************************************
#* show_handler.py
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
"""Rendering for 'ivpm show handler [name]'."""
import dataclasses
import json
import sys


def _get_all_handler_infos():
    from ..handlers.package_handler_rgy import PackageHandlerRgy
    return [h.handler_info() for h in PackageHandlerRgy.inst().handlers]


def _get_handler_info(name: str):
    from ..handlers.package_handler_rgy import PackageHandlerRgy
    for h in PackageHandlerRgy.inst().handlers:
        info = h.handler_info()
        if info.name == name:
            return info
    return None


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
    table.add_column("Handler", style="cyan bold")
    table.add_column("Phase", style="dim", justify="center")
    table.add_column("Description")
    if show_origin:
        table.add_column("Origin", style="dim")

    for info in infos:
        row = [info.name, str(info.phase), info.description]
        if show_origin:
            row.append(info.origin)
        table.add_row(*row)
    console.print(table)


def _rich_detail(info):
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    console.print(f"\n[bold cyan]Handler:[/] [bold]{info.name}[/]  [dim](phase {info.phase})[/]")
    if info.origin != "built-in":
        console.print(f"[dim]Origin:[/] {info.origin}")
    console.print(f"[bold]Description:[/] {info.description}\n")

    if info.conditions:
        console.print(f"[bold]Activation:[/] {info.conditions}\n")

    if info.params:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 1))
        table.add_column("Parameter / Key")
        table.add_column("Description")
        for p in info.params:
            table.add_row(f"[cyan]{p.name}[/]", p.description)
        console.print(table)

    if info.cli_options:
        console.print("[bold]CLI Options:[/]")
        for opt in info.cli_options:
            console.print(f"  [dim]{opt}[/]")
        console.print()

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
        origin = f"  [{info.origin}]" if show_origin else ""
        print(f"{info.name:<14} phase={info.phase}  {info.description}{origin}")


def _plain_detail(info):
    print(f"Handler:     {info.name}")
    print(f"Phase:       {info.phase}")
    if info.origin != "built-in":
        print(f"Origin:      {info.origin}")
    print(f"Description: {info.description}")
    if info.conditions:
        print(f"Activation:  {info.conditions}")
    if info.params:
        print("\nParameters / Keys:")
        for p in info.params:
            print(f"  {p.name:<18} {p.description}")
    if info.cli_options:
        print("\nCLI Options:")
        for opt in info.cli_options:
            print(f"  {opt}")
    if info.notes:
        print("\nNotes:")
        for line in info.notes.splitlines():
            print(f"  {line}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class ShowHandler:
    def __call__(self, args):
        name = getattr(args, "name", None)
        as_json = getattr(args, "json", False)
        no_rich = getattr(args, "no_rich", False)

        if name:
            info = _get_handler_info(name)
            if info is None:
                print(f"ivpm show handler: unknown handler '{name}'", file=sys.stderr)
                sys.exit(1)
            if as_json:
                print(json.dumps(dataclasses.asdict(info), indent=2))
            elif no_rich or not sys.stdout.isatty():
                _plain_detail(info)
            else:
                _rich_detail(info)
        else:
            infos = _get_all_handler_infos()
            if as_json:
                print(json.dumps([dataclasses.asdict(i) for i in infos], indent=2))
            elif no_rich or not sys.stdout.isatty():
                _plain_list(infos)
            else:
                _rich_list(infos)
