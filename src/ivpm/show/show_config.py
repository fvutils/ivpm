#****************************************************************************
#* show_config.py
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
"""Rendering for 'ivpm show site-config [name]'."""
from ..tui_theme import make_console
import dataclasses
import json
import sys


def _get_all_config_infos():
    from ..site_config_rgy import SiteConfigRgy
    infos = SiteConfigRgy.inst().site_config_infos()
    for info in infos:
        info.settings = _effective_settings(info.name)
    return infos


def _get_config_info(name: str):
    for info in _get_all_config_infos():
        if info.name == name:
            return info
    return None


def _effective_settings(name: str) -> dict:
    """Resolve the values *this* config would apply, on its own.

    Cache dir and install args come straight from the config; git-auth-order is
    the config's own default (the global :func:`resolve_git_auth_order` also
    layers config-file rules, surfaced separately in the summary)."""
    from ..site_config_rgy import SiteConfigRgy
    cfg = SiteConfigRgy.inst()._instantiate(name)
    cache_dir = cfg._resolve_cache_dir()
    return {
        "cache_dir": cache_dir if cache_dir else "(caching disabled)",
        "ivpm_install_args": cfg.get_ivpm_install_args(),
        "git_auth_order": cfg.get_default_git_auth_order(),
        "git_auth_rules": [list(r) for r in cfg.get_git_auth_rules()],
    }


def _diagnostics() -> dict:
    """Process-wide site-config diagnostics shared across all configs."""
    from ..site_config import loaded_config_paths, resolve_git_auth_order
    from ..site_config_rgy import SiteConfigRgy
    rgy = SiteConfigRgy.inst()
    return {
        "active": rgy.active_name(),
        "loaded_config_files": loaded_config_paths(),
        "resolved_git_auth_order": resolve_git_auth_order(),
    }


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
    table.add_column("", justify="center")  # active marker
    table.add_column("Site Config", style="cyan bold")
    table.add_column("Description")
    if show_origin:
        table.add_column("Origin", style="secondary")

    for info in infos:
        marker = "[green]●[/]" if info.active else ""
        row = [marker, info.name, info.description]
        if show_origin:
            row.append(info.origin)
        table.add_row(*row)
    console.print(table)
    console.print("[label]● = active (last-registered wins; "
                  "override with IVPM_SITE_CONFIG_NAME or 'site-config:' in a config file)[/]")


def _rich_settings(settings, diag=None):
    console = make_console()
    console.print("[bold]Effective settings:[/]")
    console.print(f"  [cyan]cache dir:[/]         {settings['cache_dir']}")
    console.print(f"  [cyan]ivpm install args:[/] {' '.join(settings['ivpm_install_args'])}")
    console.print(f"  [cyan]git auth order:[/]    {', '.join(settings['git_auth_order'])}")
    if settings["git_auth_rules"]:
        console.print("  [cyan]git auth rules:[/]")
        for host, order in settings["git_auth_rules"]:
            console.print(f"    {host} -> {', '.join(order)}")
    if diag is not None:
        console.print(f"\n  [label]resolved git auth order (with config-file rules):[/] "
                      f"{', '.join(diag['resolved_git_auth_order'])}")
        files = diag["loaded_config_files"]
        console.print(f"  [label]loaded config files:[/] "
                      f"{', '.join(files) if files else '(none)'}")
    console.print()


def _rich_detail(info):
    console = make_console()
    active = " [green](active)[/]" if info.active else ""
    console.print(f"\n[bold cyan]Site Config:[/] [bold]{info.name}[/]{active}")
    if info.origin != "built-in":
        console.print(f"[label]Origin:[/] {info.origin}")
    if info.provider and info.provider != info.origin:
        console.print(f"[label]Provider:[/] {info.provider}")
    if info.version:
        console.print(f"[label]Version:[/] {info.version}")
    console.print(f"[bold]Description:[/] {info.description}\n")
    _rich_settings(info.settings, _diagnostics() if info.active else None)
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
        marker = "*" if info.active else " "
        origin = f"  [{info.origin}]" if show_origin else ""
        print(f"{marker} {info.name:<16} {info.description}{origin}")
    print("\n(* = active; override with IVPM_SITE_CONFIG_NAME or 'site-config:' in a config file)")


def _plain_settings(settings, diag=None):
    print("Effective settings:")
    print(f"  cache dir:         {settings['cache_dir']}")
    print(f"  ivpm install args: {' '.join(settings['ivpm_install_args'])}")
    print(f"  git auth order:    {', '.join(settings['git_auth_order'])}")
    if settings["git_auth_rules"]:
        print("  git auth rules:")
        for host, order in settings["git_auth_rules"]:
            print(f"    {host} -> {', '.join(order)}")
    if diag is not None:
        print(f"  resolved git auth order (with config-file rules): "
              f"{', '.join(diag['resolved_git_auth_order'])}")
        files = diag["loaded_config_files"]
        print(f"  loaded config files: {', '.join(files) if files else '(none)'}")


def _plain_detail(info):
    print(f"Site Config: {info.name}{' (active)' if info.active else ''}")
    if info.origin != "built-in":
        print(f"Origin:      {info.origin}")
    if info.provider and info.provider != info.origin:
        print(f"Provider:    {info.provider}")
    if info.version:
        print(f"Version:     {info.version}")
    print(f"Description: {info.description}")
    _plain_settings(info.settings, _diagnostics() if info.active else None)
    if info.notes:
        print("\nNotes:")
        for line in info.notes.splitlines():
            print(f"  {line}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class ShowConfig:
    def __call__(self, args):
        name = getattr(args, "name", None)
        as_json = getattr(args, "json", False)
        no_rich = getattr(args, "no_rich", False)

        if name:
            info = _get_config_info(name)
            if info is None:
                print(f"ivpm show site-config: unknown site config '{name}'", file=sys.stderr)
                sys.exit(1)
            if as_json:
                print(json.dumps(dataclasses.asdict(info), indent=2))
            elif no_rich or not sys.stdout.isatty():
                _plain_detail(info)
            else:
                _rich_detail(info)
        else:
            infos = _get_all_config_infos()
            if as_json:
                print(json.dumps({
                    "configs": [dataclasses.asdict(i) for i in infos],
                    "diagnostics": _diagnostics(),
                }, indent=2))
            elif no_rich or not sys.stdout.isatty():
                _plain_list(infos)
                print()
                active = next((i for i in infos if i.active), None)
                if active is not None:
                    _plain_settings(active.settings, _diagnostics())
            else:
                _rich_list(infos)
                make_console().print()
                active = next((i for i in infos if i.active), None)
                if active is not None:
                    _rich_settings(active.settings, _diagnostics())
