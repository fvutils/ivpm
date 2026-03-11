#****************************************************************************
#* info_types.py
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
"""Shared self-description dataclasses used by the `ivpm show` command.

All three registries (package sources, content types, handlers) report themselves
using subclasses of RegistryEntryInfo so that the rendering layer can treat them
uniformly.
"""
import dataclasses as dc
from typing import List, Optional


@dc.dataclass
class ParamInfo:
    """Describes a single YAML parameter accepted by a source, type, or handler."""
    name: str
    description: str
    required: bool = False
    default: Optional[str] = None
    type_hint: str = "str"   # "str" | "bool" | "int" | "url"


@dc.dataclass
class RegistryEntryInfo:
    """Base self-description for any registered IVPM extension point."""
    name: str
    description: str
    params: List[ParamInfo] = dc.field(default_factory=list)
    notes: str = ""
    origin: str = "built-in"  # "built-in" | entry-point name (e.g. "mypkg.ext")


@dc.dataclass
class PkgSourceInfo(RegistryEntryInfo):
    """Self-description for a package source (registered in PkgTypeRgy)."""
    pass


@dc.dataclass
class ContentTypeInfo(RegistryEntryInfo):
    """Self-description for a package content type (registered in PkgContentTypeRgy)."""
    pass


@dc.dataclass
class HandlerInfo(RegistryEntryInfo):
    """Self-description for a package handler (registered in PackageHandlerRgy)."""
    phase: int = 0
    conditions: str = ""
    # Human-readable list of CLI options this handler adds, e.g.:
    # ["update: --py-uv", "update: --py-pip"]
    cli_options: List[str] = dc.field(default_factory=list)
