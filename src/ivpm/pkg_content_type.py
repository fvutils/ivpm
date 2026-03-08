#****************************************************************************
#* pkg_content_type.py
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
import dataclasses as dc
from .utils import fatal, getlocstr


@dc.dataclass
class TypeData:
    """Base class for type-specific package data produced by PkgContentType.create_data()."""
    pass


class PkgContentType:
    """Describes a content type and validates its 'with:' parameters.

    Subclasses represent a named content type (e.g. 'python', 'raw').
    They validate the 'with:' sub-mapping from a package dep entry and
    return a populated TypeData object.
    """

    @property
    def name(self) -> str:
        raise NotImplementedError()

    def create_data(self, with_opts: dict, si) -> TypeData:
        """Validate with_opts and return a populated TypeData.

        with_opts is the dict from the 'with:' key (may be empty).
        si is the source-info object used for error location reporting.
        Implementations must call fatal() on unknown or invalid keys.
        """
        raise NotImplementedError()

    def get_json_schema(self) -> dict:
        """Return a JSON Schema dict describing the 'with:' block.

        Used to generate / validate the ivpm.json schema.
        Returns an empty object schema by default (no parameters accepted).
        """
        return {"type": "object", "additionalProperties": False, "properties": {}}


# ---------------------------------------------------------------------------
# Built-in: python
# ---------------------------------------------------------------------------

@dc.dataclass
class PythonTypeData(TypeData):
    """Type-specific data for packages processed by the Python handler."""
    extras: list = None    # PEP 508 extras, e.g. ["tests", "docs"]
    editable: bool = None  # Install with -e; None means "use default" (True for source pkgs)


class PythonContentType(PkgContentType):
    """Content type 'python': installs a package into the managed venv."""

    @property
    def name(self) -> str:
        return "python"

    def create_data(self, with_opts: dict, si) -> PythonTypeData:
        known = {"extras", "editable"}
        for k in with_opts:
            if k not in known:
                fatal("Unknown parameter '%s' for type 'python' @ %s" % (
                    k, getlocstr(with_opts[k]) if hasattr(with_opts[k], 'srcinfo') else str(si)))
        data = PythonTypeData()
        if "extras" in with_opts:
            raw = with_opts["extras"]
            data.extras = [str(e) for e in raw] if isinstance(raw, list) else [str(raw)]
        if "editable" in with_opts:
            data.editable = bool(with_opts["editable"])
        return data

    def get_json_schema(self) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "extras": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}}
                    ],
                    "title": "PEP 508 extras to install (e.g. [tests, docs])"
                },
                "editable": {
                    "type": "boolean",
                    "title": "Install as editable (-e). Default true for source packages."
                }
            }
        }


# ---------------------------------------------------------------------------
# Built-in: raw
# ---------------------------------------------------------------------------

@dc.dataclass
class RawTypeData(TypeData):
    """Type-specific data for raw (unprocessed) packages."""
    pass


class RawContentType(PkgContentType):
    """Content type 'raw': package is fetched but not further processed."""

    @property
    def name(self) -> str:
        return "raw"

    def create_data(self, with_opts: dict, si) -> RawTypeData:
        if with_opts:
            fatal("type 'raw' does not accept any 'with:' parameters")
        return RawTypeData()
