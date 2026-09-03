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
    # Populated by PkgContentType.create_data() to record which type produced this data.
    type_name: str = dc.field(default="", init=False)
    # True when this type was declared by the package's own ivpm.yaml rather
    # than by the dependency entry that imported it. The two produce identical
    # data but carry different authority, and a diagnostic has to be located
    # at whichever file actually made the claim.
    self_declared: bool = dc.field(default=False, init=False)

    # Fields that are bookkeeping rather than user-settable configuration, and
    # so are never merged between a provider's declaration and a consumer's.
    _MERGE_EXCLUDED = ("type_name", "self_declared")

    def merge_over(self, base: 'TypeData') -> 'TypeData':
        """Return *self* laid over *base*, field by field.

        ``self`` is the consumer's declaration at the dependency entry and
        wins wherever it said anything; ``base`` is what the provider package
        declared about itself. ``None`` means "not specified", which is why
        every mergeable field has to default to ``None`` rather than to its
        effective default -- otherwise "unset" and "explicitly set to the
        default" are the same value, and a provider could never supply
        anything the consumer did not repeat.

        Returns ``self`` unchanged when the two are not the same content type:
        merging a 'python' declaration onto a 'node' one is a caller error,
        not something to paper over.
        """
        if base is None or type(base) is not type(self):
            return self

        merged = dc.replace(self)
        merged.type_name = self.type_name or base.type_name
        merged.self_declared = self.self_declared and base.self_declared

        for f in dc.fields(self):
            if f.name in self._MERGE_EXCLUDED:
                continue
            if getattr(self, f.name, None) is None:
                setattr(merged, f.name, getattr(base, f.name, None))
        return merged


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

    def content_type_info(self) -> 'ContentTypeInfo':
        """Return a ContentTypeInfo describing this content type.

        The default implementation returns a minimal info object with no
        parameter documentation.  Subclasses should override to add params.
        """
        from .show.info_types import ContentTypeInfo
        return ContentTypeInfo(name=self.name, description="")

    def get_json_schema(self) -> dict:
        """Return a JSON Schema dict describing the 'with:' block.

        Derived from content_type_info() by default.  Subclasses that add
        parameters should override content_type_info() instead; this method
        will build the schema automatically from the ParamInfo list.
        Falls back to an empty object schema if not overridden.
        """
        from .show.info_types import ContentTypeInfo
        info = self.content_type_info()
        if not info.params:
            return {"type": "object", "additionalProperties": False, "properties": {}}
        props = {}
        for p in info.params:
            entry = {}
            if p.type_hint == "bool":
                entry["type"] = "boolean"
            elif p.type_hint == "int":
                entry["type"] = "integer"
            else:
                entry["type"] = "string"
            if p.description:
                entry["title"] = p.description
            props[p.name] = entry
        required = [p.name for p in info.params if p.required]
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
        }
        if required:
            schema["required"] = required
        return schema


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
        data.type_name = self.name
        return data

    def content_type_info(self):
        from .show.info_types import ContentTypeInfo, ParamInfo
        return ContentTypeInfo(
            name="python",
            description="Install package into the managed Python virtual environment",
            params=[
                ParamInfo("extras", "PEP 508 extras to install (e.g. [tests, docs])"),
                ParamInfo("editable", "Install with -e (editable). Default: true for source packages", type_hint="bool"),
            ],
        )

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
        data = RawTypeData()
        data.type_name = self.name
        return data

    def content_type_info(self):
        from .show.info_types import ContentTypeInfo
        return ContentTypeInfo(
            name="raw",
            description="Package is fetched but not further processed — no install step",
        )


# ---------------------------------------------------------------------------
# Built-in: node
# ---------------------------------------------------------------------------

@dc.dataclass
class NodeTypeData(TypeData):
    """Type-specific data for packages processed by the Node handler.

    Both fields default to ``None``, meaning "not specified", so that a
    provider's declaration and a consumer's ``with:`` can be merged field by
    field (see ``TypeData.merge_over``). Concrete defaults are applied by
    ``resolved()`` at the point the handler commits to using the data --
    defaulting at construction would make "unset" indistinguishable from
    "explicitly set to the default", and a provider could then never supply a
    value the consumer had not already repeated.
    """
    dev: bool = None    # install as devDependency (effective default: False)
    link: bool = None   # editable file: install (effective default: True)

    DEFAULTS = {"dev": False, "link": True}

    def resolved(self) -> 'NodeTypeData':
        """A copy with every unspecified field filled in with its default."""
        out = dc.replace(self)
        out.type_name = self.type_name
        out.self_declared = self.self_declared
        for name, default in self.DEFAULTS.items():
            if getattr(out, name) is None:
                setattr(out, name, default)
        return out


class NodeContentType(PkgContentType):
    """Content type 'node': installs a source package into the managed node environment."""

    @property
    def name(self) -> str:
        return "node"

    def create_data(self, with_opts: dict, si) -> NodeTypeData:
        known = {"dev", "link"}
        for k in with_opts:
            if k not in known:
                fatal("Unknown parameter '%s' for type 'node' @ %s" % (
                    k, getlocstr(with_opts[k]) if hasattr(with_opts[k], 'srcinfo') else str(si)))
        data = NodeTypeData()
        if "dev" in with_opts:
            data.dev = bool(with_opts["dev"])
        if "link" in with_opts:
            data.link = bool(with_opts["link"])
        data.type_name = self.name
        return data

    def content_type_info(self):
        from .show.info_types import ContentTypeInfo, ParamInfo
        return ContentTypeInfo(
            name="node",
            description="Install source package into the managed Node.js environment via npm link",
            params=[
                ParamInfo("dev", "Install as devDependency (default: false)", type_hint="bool"),
                ParamInfo("link", "Use npm link (editable install). Default: true for source packages", type_hint="bool"),
            ],
        )

    def get_json_schema(self) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "dev": {
                    "type": "boolean",
                    "title": "Install as devDependency (default: false)"
                },
                "link": {
                    "type": "boolean",
                    "title": "Use npm link (editable install). Default: true for source packages."
                }
            }
        }


# ---------------------------------------------------------------------------
# YAML type-field parser
# ---------------------------------------------------------------------------

def parse_type_field(value) -> list:
    """Normalise any supported 'type:' YAML value to List[Tuple[str, dict]].

    Accepted forms:
      str              →  [(value, {})]
      {name: opts}     →  [(name, opts or {})]
      [str | {n:o}, …] →  above rules applied per element
    """
    def _item(v):
        if isinstance(v, str):
            return [(v, {})]
        if isinstance(v, dict):
            return [(str(k), (opts if isinstance(opts := vv, dict) else {}))
                    for k, vv in v.items()]
        fatal("Unexpected value in 'type:' field: %r" % (v,))

    if isinstance(value, list):
        result = []
        for elem in value:
            result.extend(_item(elem))
        return result
    return _item(value)


# ---------------------------------------------------------------------------
# Built-in: module
# ---------------------------------------------------------------------------

@dc.dataclass
class ModuleTypeData(TypeData):
    """Type-specific data for packages processed by the modules handler."""
    load: bool = True        # emit 'module load' into envrc
    module: str = None       # module specifier (copied from PackageModule.module)


class ModuleContentType(PkgContentType):
    """Content type 'module': emit module load and/or resolve root directory."""

    @property
    def name(self) -> str:
        return "module"

    def create_data(self, with_opts: dict, si) -> ModuleTypeData:
        known = {"load"}
        for k in with_opts:
            if k not in known:
                fatal("type 'module' does not accept parameter '%s' "
                      "(known: %s)" % (k, ", ".join(sorted(known))))
        data = ModuleTypeData()
        if "load" in with_opts:
            data.load = bool(with_opts["load"])
        data.type_name = self.name
        return data

    def content_type_info(self):
        from .show.info_types import ContentTypeInfo, ParamInfo
        return ContentTypeInfo(
            name="module",
            description="Environment Module -- emit module load and/or resolve root directory for handler discovery",
            params=[
                ParamInfo("load", "Emit 'module load' into envrc (default: true)", type_hint="bool"),
            ],
        )
