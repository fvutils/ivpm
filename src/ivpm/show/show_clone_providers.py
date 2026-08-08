#****************************************************************************
#* show_clone_providers.py
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
"""Rendering for 'ivpm show clone-providers [name]'."""
from ..tui_theme import make_console
import dataclasses
import json
import sys


def _get_all_infos():
    from ..clone.clone_provider_rgy import CloneProviderRgy
    return CloneProviderRgy.inst().all_infos()


def _get_info(name: str):
    from ..clone.clone_provider_rgy import CloneProviderRgy
    return CloneProviderRgy.inst().info_for(name)


def _schemes_str(info):
    return ("%s://" % ", ".join(info.schemes)) if info.schemes else "(by URL)"


def _any_plugins(infos) -> bool:
    return any(i.origin != "built-in" for i in infos)


# ---------------------------------------------------------------------------
# Rich output
# ---------------------------------------------------------------------------

def _rich_list(infos):
    from rich.table import Table
    from rich import box

    console = make_console()
    show_origin = _any_plugins(infos)
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Provider", style="cyan bold")
    table.add_column("Default", style="secondary")
    table.add_column("Schemes", style="secondary")
    table.add_column("Description")
    if show_origin:
        table.add_column("Origin", style="secondary")

    for info in infos:
        row = [info.name,
               "yes" if getattr(info, "is_default", False) else "",
               _schemes_str(info),
               info.description]
        if show_origin:
            row.append(info.origin)
        table.add_row(*row)
    console.print(table)


def _rich_detail(info):
    from rich.table import Table
    from rich import box

    console = make_console()
    console.print(f"\n[bold cyan]Clone provider:[/] [bold]{info.name}[/]")
    console.print(f"[label]Schemes:[/] {_schemes_str(info)}")
    if getattr(info, "is_default", False):
        console.print("[label]Default:[/] yes")
    if info.origin != "built-in":
        console.print(f"[label]Origin:[/] {info.origin}")
    if info.provider and info.provider != info.origin:
        console.print(f"[label]Provider:[/] {info.provider}")
    if info.version:
        console.print(f"[label]Version:[/] {info.version}")
    console.print(f"[bold]Description:[/] {info.description}\n")

    if info.params:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 1))
        table.add_column("Option")
        table.add_column("Type", style="secondary")
        table.add_column("Req", style="secondary")
        table.add_column("Default", style="secondary")
        table.add_column("Description")
        for p in info.params:
            table.add_row(
                f"[cyan]{p.name}[/]",
                "switch" if p.type_hint == "bool" else p.type_hint,
                "[bold red]✓[/]" if p.required else "",
                p.default or "",
                p.description,
            )
        console.print(table)
    else:
        console.print("[label](no options; see 'ivpm clone %s --help')[/]\n" % (
            (info.schemes[0] + "://") if info.schemes else info.name))

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
        default = " [default]" if getattr(info, "is_default", False) else ""
        origin = f"  [{info.origin}]" if show_origin else ""
        print(f"{info.name:<12} {_schemes_str(info):<14} {info.description}{default}{origin}")


def _plain_detail(info):
    print(f"Clone provider: {info.name}")
    print(f"Schemes:        {_schemes_str(info)}")
    if getattr(info, "is_default", False):
        print("Default:        yes")
    if info.origin != "built-in":
        print(f"Origin:         {info.origin}")
    if info.provider and info.provider != info.origin:
        print(f"Provider:       {info.provider}")
    if info.version:
        print(f"Version:        {info.version}")
    print(f"Description:    {info.description}")
    if info.params:
        print("\nOptions:")
        for p in info.params:
            req = " (required)" if p.required else ""
            default = f"  default: {p.default}" if p.default else ""
            kind = "switch" if p.type_hint == "bool" else p.type_hint
            print(f"  {p.name:<18} [{kind}]{req}{default}")
            print(f"  {'':18} {p.description}")
    else:
        hint = (info.schemes[0] + "://") if info.schemes else info.name
        print(f"\nOptions: (none; see 'ivpm clone {hint} --help')")
    if info.notes:
        print("\nNotes:")
        for line in info.notes.splitlines():
            print(f"  {line}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class ShowCloneProviders:
    def __call__(self, args):
        name = getattr(args, "name", None)
        as_json = getattr(args, "json", False)
        no_rich = getattr(args, "no_rich", False)

        if name:
            info = _get_info(name)
            if info is None:
                print(f"ivpm show clone-providers: unknown provider '{name}'",
                      file=sys.stderr)
                sys.exit(1)
            if as_json:
                print(json.dumps(dataclasses.asdict(info), indent=2))
            elif no_rich or not sys.stdout.isatty():
                _plain_detail(info)
            else:
                _rich_detail(info)
        else:
            infos = _get_all_infos()
            if as_json:
                print(json.dumps([dataclasses.asdict(i) for i in infos], indent=2))
            elif no_rich or not sys.stdout.isatty():
                _plain_list(infos)
            else:
                _rich_list(infos)
