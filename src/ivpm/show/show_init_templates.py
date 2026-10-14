#****************************************************************************
#* show_init_templates.py
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
"""Rendering for 'ivpm show init-templates [name]' (and 'ivpm init --list').

Templates come from six places and a more local one silently replaces a more
general one of the same name, so the listing always says where each came
from and flags overrides.  Problems found while discovering templates (a
stale IVPM_INIT_TEMPLATE_PATH entry, a broken template pack) are listed in a
trailing Warnings section rather than hidden in a debug log.
"""
import dataclasses
import json
import sys

from ..tui_theme import make_console
from .info_types import InitTemplateInfo, ParamInfo, ShadowedTemplateInfo


def _get_rgy():
    from ..init_template.template_rgy import InitTemplateRgy
    return InitTemplateRgy.inst()


def to_info(t, with_questions: bool = False) -> InitTemplateInfo:
    info = InitTemplateInfo(
        name=t.name,
        description=t.description,
        origin=t.origin,
        provider=t.provider,
        version=t.version,
        category=t.category,
        src=t.src,
        ref=t.ref,
        trusted=bool(t.trusted),
        hidden=t.hidden,
        next_steps=t.next_steps,
        shadowed=[ShadowedTemplateInfo(origin=s.origin, src=s.src, provider=s.provider)
                  for s in t.shadowed])
    if with_questions and not t.is_remote:
        from ..init_template.template_dir import read_questions
        info.params = [
            ParamInfo(name=q.name, description=q.help,
                      default=None if q.default is None else str(q.default),
                      type_hint=q.type or "")
            for q in read_questions(t.src)]
    return info


def _get_all_infos(include_hidden: bool = False):
    return [to_info(t) for t in _get_rgy().templates(include_hidden)]


def _get_all_infos_or_report():
    """For the bare 'ivpm show' summary: a failure is reported, not raised."""
    try:
        return _get_all_infos()
    except Exception as e:
        print("ivpm: init templates: %s" % e, file=sys.stderr)
        return []


def _by_category(infos):
    """(category, [infos]) in first-seen order, uncategorized last."""
    groups = {}
    for info in infos:
        groups.setdefault(info.category or "", []).append(info)
    keys = [k for k in groups if k] + ([""] if "" in groups else [])
    return [(k, groups[k]) for k in keys]


def _origin(info) -> str:
    if info.provider and info.version:
        return "%s %s" % (info.provider, info.version)
    return info.origin


def _marker(info) -> str:
    return " (shadows %d)" % len(info.shadowed) if info.shadowed else ""


# ---------------------------------------------------------------------------
# Rich output
# ---------------------------------------------------------------------------

def _rich_list(infos, warnings=()):
    from rich.table import Table
    from rich import box

    console = make_console()
    if not infos:
        console.print("[label]No init templates are available.[/]")
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Template", style="cyan bold")
    table.add_column("Category", style="secondary")
    table.add_column("Description")
    table.add_column("Origin", style="secondary")
    for _cat, group in _by_category(infos):
        for info in group:
            table.add_row(info.name, info.category or "-", info.description,
                          _origin(info) + _marker(info))
    if infos:
        console.print(table)
    _rich_warnings(console, warnings)


def _rich_warnings(console, warnings):
    if warnings:
        console.print("[bold yellow]Warnings:[/]")
        for w in warnings:
            console.print("  %s" % w, markup=False, highlight=False)
        console.print()


def _rich_detail(info, warnings=()):
    console = make_console()
    console.print(f"\n[bold cyan]Init template:[/] [bold]{info.name}[/]")
    console.print(f"[label]Source:[/] {info.src}", highlight=False)
    if info.ref:
        console.print(f"[label]Ref:[/] {info.ref}")
    if info.category:
        console.print(f"[label]Category:[/] {info.category}")
    console.print(f"[label]Origin:[/] {info.origin}")
    if info.provider and info.provider != info.origin:
        console.print(f"[label]Provider:[/] {info.provider}")
    if info.version:
        console.print(f"[label]Version:[/] {info.version}")
    console.print(f"[label]Trusted:[/] {'yes' if info.trusted else 'no'}")
    if info.hidden:
        console.print("[label]Hidden:[/] yes")
    console.print(f"[bold]Description:[/] {info.description}")
    if info.next_steps:
        console.print(f"[label]Next steps:[/] {info.next_steps}")
    if info.params:
        from rich.table import Table
        from rich import box
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("Question", style="cyan")
        table.add_column("Type", style="secondary")
        table.add_column("Default", style="secondary")
        table.add_column("Help")
        for p in info.params:
            table.add_row(p.name, p.type_hint or "-",
                          "-" if p.default is None else p.default, p.description)
        console.print(table)
    if info.shadowed:
        console.print("[bold]Shadows:[/]")
        for s in info.shadowed:
            console.print("  %s: %s" % (s.origin, s.src), markup=False, highlight=False)
    console.print()
    _rich_warnings(console, warnings)


# ---------------------------------------------------------------------------
# Plain-text output
# ---------------------------------------------------------------------------

def _plain_list(infos, warnings=()):
    if not infos:
        print("No init templates are available.")
    groups = _by_category(infos)
    for cat, group in groups:
        if len(groups) > 1 or cat:
            print("%s:" % (cat or "other"))
        for info in group:
            print("  %-20s %s  [%s]%s" % (info.name, info.description, _origin(info),
                                          _marker(info)))
    _plain_warnings(warnings)


def _plain_warnings(warnings):
    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print("  %s" % w)


def _plain_detail(info, warnings=()):
    print(f"Init template:    {info.name}")
    print(f"Source:           {info.src}")
    if info.ref:
        print(f"Ref:              {info.ref}")
    if info.category:
        print(f"Category:         {info.category}")
    print(f"Origin:           {info.origin}")
    if info.provider and info.provider != info.origin:
        print(f"Provider:         {info.provider}")
    if info.version:
        print(f"Version:          {info.version}")
    print(f"Trusted:          {'yes' if info.trusted else 'no'}")
    if info.hidden:
        print("Hidden:           yes")
    print(f"Description:      {info.description}")
    if info.next_steps:
        print(f"Next steps:       {info.next_steps}")
    if info.params:
        print("\nQuestions:")
        for p in info.params:
            default = "" if p.default is None else " (default: %s)" % p.default
            kind = " [%s]" % p.type_hint if p.type_hint else ""
            print(f"  {p.name}{kind}{default}  {p.description}".rstrip())
    if info.shadowed:
        print("\nShadows:")
        for s in info.shadowed:
            print(f"  {s.origin}: {s.src}")
    _plain_warnings(warnings)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class ShowInitTemplates:
    def __call__(self, args):
        name = getattr(args, "name", None)
        as_json = getattr(args, "json", False)
        no_rich = getattr(args, "no_rich", False)
        include_hidden = getattr(args, "all", False)

        rgy = _get_rgy()
        warnings = rgy.warnings()

        if name:
            t = rgy.get(name)
            if t is None:
                msg = "ivpm show init-templates: unknown init template '%s'" % name
                close = rgy.suggest(name)
                if close:
                    msg += " (did you mean: %s?)" % ", ".join(close)
                print(msg, file=sys.stderr)
                sys.exit(1)
            info = to_info(t, with_questions=True)
            if as_json:
                print(json.dumps(dataclasses.asdict(info), indent=2))
            elif no_rich:
                _plain_detail(info, warnings)
            else:
                _rich_detail(info, warnings)
            return

        infos = [to_info(t) for t in rgy.templates(include_hidden)]
        if as_json:
            print(json.dumps([dataclasses.asdict(i) for i in infos], indent=2))
            for w in warnings:
                print("ivpm: warning: %s" % w, file=sys.stderr)
        elif no_rich:
            _plain_list(infos, warnings)
        else:
            _rich_list(infos, warnings)
