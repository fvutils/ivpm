#****************************************************************************
#* cmd_show.py
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
"""CmdShow — dispatcher for 'ivpm show [source|type|handler]'."""
import json
import sys


class CmdShow:
    def __call__(self, args):
        # --schema: emit a JSON Schema for ivpm.yaml
        if getattr(args, "schema", False):
            _emit_schema()
            return

        sub = getattr(args, "show_cmd", None)

        if sub in ("source", "src"):
            from .show_source import ShowSource
            ShowSource()(args)
        elif sub == "type":
            from .show_type import ShowType
            ShowType()(args)
        elif sub == "handler":
            from .show_handler import ShowHandler
            ShowHandler()(args)
        else:
            # No sub-command: show all three categories
            _show_all(args)


def _show_all(args):
    """Show a compact summary of all three registries."""
    no_rich = getattr(args, "no_rich", False)
    as_json = getattr(args, "json", False)

    from .show_source import _get_all_source_infos, _rich_list as src_rich, _plain_list as src_plain
    from .show_type import _get_all_type_infos, _rich_list as type_rich, _plain_list as type_plain
    from .show_handler import _get_all_handler_infos, _rich_list as handler_rich, _plain_list as handler_plain

    src_infos = _get_all_source_infos()
    type_infos = _get_all_type_infos()
    handler_infos = _get_all_handler_infos()

    if as_json:
        import dataclasses
        print(json.dumps({
            "sources": [dataclasses.asdict(i) for i in src_infos],
            "types":   [dataclasses.asdict(i) for i in type_infos],
            "handlers":[dataclasses.asdict(i) for i in handler_infos],
        }, indent=2))
        return

    if no_rich or not sys.stdout.isatty():
        print("=== Package Sources ===")
        src_plain(src_infos)
        print("\n=== Content Types ===")
        type_plain(type_infos)
        print("\n=== Handlers ===")
        handler_plain(handler_infos)
    else:
        from rich.console import Console
        console = Console()
        console.print("\n[bold underline]Package Sources[/]  [dim](ivpm show source <name> for details)[/]")
        src_rich(src_infos)
        console.print("[bold underline]Content Types[/]  [dim](ivpm show type <name> for details)[/]")
        type_rich(type_infos)
        console.print("[bold underline]Handlers[/]  [dim](ivpm show handler <name> for details)[/]")
        handler_rich(handler_infos)


def _emit_schema():
    """Emit a JSON Schema for ivpm.yaml combining all sources and content types."""
    import dataclasses
    from ..pkg_types.pkg_type_rgy import PkgTypeRgy
    from ..pkg_content_type_rgy import PkgContentTypeRgy

    src_rgy = PkgTypeRgy.inst()
    type_rgy = PkgContentTypeRgy.inst()

    # Build the per-source 'src:' discriminated dep schema
    src_schemas = {}
    for info in src_rgy.getAllSourceInfo():
        props = {}
        required = []
        for p in info.params:
            entry = {}
            if p.type_hint == "bool":
                entry["type"] = "boolean"
            elif p.type_hint == "int":
                entry["type"] = "integer"
            elif p.type_hint == "url":
                entry["type"] = "string", 
                entry = {"type": "string", "format": "uri-reference"}
            else:
                entry["type"] = "string"
            if p.description:
                entry["description"] = p.description
            if p.default:
                entry["default"] = p.default
            props[p.name] = entry
            if p.required:
                required.append(p.name)
        src_schemas[info.name] = {
            "description": info.description,
            "properties": props,
            "required": required,
        }

    # Build the per-content-type 'type:' schema
    type_schemas = {}
    for name in type_rgy.names():
        ct = type_rgy.get(name)
        type_schemas[name] = ct.get_json_schema()

    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ivpm.yaml",
        "description": "IVPM project configuration",
        "type": "object",
        "properties": {
            "package": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                    "dep-sets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "deps": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "src": {
                                                "type": "string",
                                                "enum": list(src_schemas.keys()),
                                            },
                                            "type": {
                                                "oneOf": [
                                                    {"type": "string", "enum": list(type_schemas.keys())},
                                                    {"type": "array"},
                                                    {"type": "object"},
                                                ],
                                            },
                                        },
                                        "required": ["name"],
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "x-ivpm-sources": src_schemas,
        "x-ivpm-content-types": type_schemas,
    }
    print(json.dumps(schema, indent=2))
