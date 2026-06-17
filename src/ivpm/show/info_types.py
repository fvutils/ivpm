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
    # Distribution that provides this entry, and its version. Populated
    # automatically for entry-point plugins (from the providing package's
    # metadata) and may also be passed explicitly at registration time.
    provider: Optional[str] = None
    version: Optional[str] = None


# Distribution name of IVPM itself. IVPM's own sources/types/handlers are
# registered through the same entry-point groups as third-party plugins, so we
# recognise this provider and keep those entries flagged as "built-in".
IVPM_DIST = "ivpm"


def ep_provenance(ep):
    """Return ``(provider, version)`` for an entry point's distributing package.

    Used to auto-populate ``provider``/``version`` on registry entries loaded
    from entry points.  Returns ``(None, None)`` when the distribution metadata
    is unavailable (e.g. an entry point synthesized without a backing dist).
    """
    dist = getattr(ep, "dist", None)
    if dist is None:
        return (None, None)
    provider = getattr(dist, "name", None)
    if provider is None:
        try:
            provider = dist.metadata["Name"]
        except Exception:
            provider = None
    version = getattr(dist, "version", None)
    return (provider, version)


def ep_registration_kwargs(ep) -> dict:
    """Map an entry point to ``origin``/``version``/``provider`` registration kwargs.

    Entries provided by IVPM itself yield an empty dict so the registry's
    built-in defaults apply; third-party plugins yield their distribution name
    as both ``origin`` and ``provider`` along with the distribution ``version``.
    """
    provider, version = ep_provenance(ep)
    if not provider or provider == IVPM_DIST:
        return {}
    return {"origin": provider, "version": version, "provider": provider}


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
