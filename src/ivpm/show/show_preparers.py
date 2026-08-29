#****************************************************************************
#* show_preparers.py
#*
#* Copyright 2025 Matthew Ballance and Contributors
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
"""Rendering for 'ivpm show preparers [name]'.

Preparers are a policy surface: what runs before a package is written, in what
order, and where it came from. IVPM ships none, so an empty list is the normal
state and says so explicitly rather than printing nothing.
"""
import dataclasses
import json
import sys

from ..tui_theme import make_console

_EMPTY_NOTE = (
    "No package preparers are registered.\n"
    "  A preparer runs before each package is populated: it can configure the\n"
    "  target directory (group, mode, ACL) so fetched content inherits it, and\n"
    "  can refuse a package. Register one via the 'ivpm.pkg_preparers'\n"
    "  entry-point group.")


def _get_all_infos():
    from ..prepare import PackagePreparerRgy
    return PackagePreparerRgy.inst().preparer_infos()


def _get_info(name: str):
    for info in _get_all_infos():
        if info.name == name:
            return info
    return None


def _dispatch_order(infos):
    """Same key the dispatcher uses: (order, name). Total and stable."""
    return sorted(infos, key=lambda i: (i.order, i.name))


def _any_plugins(infos) -> bool:
    return any(i.origin != "built-in" for i in infos)


# ---------------------------------------------------------------------------
# Rich output
# ---------------------------------------------------------------------------

def _rich_list(infos):
    from rich.table import Table
    from rich import box

    console = make_console()
    if not infos:
        console.print("[label]%s[/]" % _EMPTY_NOTE)
        return

    show_origin = _any_plugins(infos)
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Preparer", style="cyan bold")
    table.add_column("Order", style="secondary", justify="right")
    table.add_column("Scope", style="secondary")
    table.add_column("Description")
    if show_origin:
        table.add_column("Origin", style="secondary")

    for info in _dispatch_order(infos):
        row = [info.name,
               str(info.order),
               "all packages" if info.always else "on populate",
               info.description]
        if show_origin:
            row.append(info.origin)
        table.add_row(*row)
    console.print(table)


def _rich_detail(info):
    console = make_console()
    console.print(f"\n[bold cyan]Package preparer:[/] [bold]{info.name}[/]")
    console.print(f"[label]Dispatch order:[/] {info.order}")
    console.print("[label]Runs for:[/] %s" % (
        "every package" if info.always
        else "packages about to be populated"))
    if info.origin != "built-in":
        console.print(f"[label]Origin:[/] {info.origin}")
    if info.provider and info.provider != info.origin:
        console.print(f"[label]Provider:[/] {info.provider}")
    if info.version:
        console.print(f"[label]Version:[/] {info.version}")
    console.print(f"[bold]Description:[/] {info.description}\n")
    if info.notes:
        console.print("[bold]Notes:[/]")
        for line in info.notes.splitlines():
            console.print(f"  {line}")
        console.print()


# ---------------------------------------------------------------------------
# Plain-text output
# ---------------------------------------------------------------------------

def _plain_list(infos):
    if not infos:
        print(_EMPTY_NOTE)
        return
    show_origin = _any_plugins(infos)
    for info in _dispatch_order(infos):
        scope = "all packages" if info.always else "on populate"
        origin = f"  [{info.origin}]" if show_origin else ""
        print(f"{info.name:<18} {info.order:>5}  {scope:<13} "
              f"{info.description}{origin}")


def _plain_detail(info):
    print(f"Package preparer: {info.name}")
    print(f"Dispatch order:   {info.order}")
    print("Runs for:         %s" % (
        "every package" if info.always
        else "packages about to be populated"))
    if info.origin != "built-in":
        print(f"Origin:           {info.origin}")
    if info.provider and info.provider != info.origin:
        print(f"Provider:         {info.provider}")
    if info.version:
        print(f"Version:          {info.version}")
    print(f"Description:      {info.description}")
    if info.notes:
        print("\nNotes:")
        for line in info.notes.splitlines():
            print(f"  {line}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class ShowPreparers:
    def __call__(self, args):
        name = getattr(args, "name", None)
        as_json = getattr(args, "json", False)
        no_rich = getattr(args, "no_rich", False)

        if name:
            info = _get_info(name)
            if info is None:
                print(f"ivpm show preparers: unknown preparer '{name}'",
                      file=sys.stderr)
                sys.exit(1)
            if as_json:
                print(json.dumps(dataclasses.asdict(info), indent=2))
            elif no_rich:
                _plain_detail(info)
            else:
                _rich_detail(info)
            return

        infos = _get_all_infos()
        if as_json:
            print(json.dumps(
                [dataclasses.asdict(i) for i in _dispatch_order(infos)],
                indent=2))
        elif no_rich:
            _plain_list(infos)
        else:
            _rich_list(infos)
