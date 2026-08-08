#****************************************************************************
#* validate.py
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
"""A self-contained validator for the two vendored Agent Plugins schemas.

Deliberately *not* backed by the ``jsonschema`` package, even when it happens
to be installed.  Two reasons:

* IVPM's diagnostics must not depend on what else is in the environment.  A
  user with ``jsonschema`` installed and a user without it must get byte-identical
  ``ivpm show plugins --check`` output.
* ``jsonschema`` would be a new hard dependency for a job that needs ~10 keywords.

The schemas are vendored and immutable (the published schema identifiers never
change), so the supported keyword set below is fixed by inspection rather than
open-ended:  ``type``, ``const``, ``enum``, ``required``, ``properties``,
``additionalProperties``, ``propertyNames``, ``items``, ``minLength``,
``maxLength``, ``pattern``, ``oneOf``, ``not``, and local ``$ref``.

``test_agent_plugin_manifest.py`` cross-checks this implementation against the
real ``jsonschema`` package when it is importable, so the two cannot drift.
"""
import json
import os
import re
from typing import Any, Dict, List, Optional

# Schemas live under schemas/<spec-version>/<kind>.schema.json
_SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "schemas")

_schema_cache: Dict[str, dict] = {}


class SchemaError(Exception):
    """Raised when a vendored schema is missing or unreadable (an IVPM bug)."""


def load_schema(spec_version: str, kind: str) -> dict:
    """Load a vendored schema. ``kind`` is 'plugin' or 'mcp'."""
    key = "%s/%s" % (spec_version, kind)
    cached = _schema_cache.get(key)
    if cached is not None:
        return cached

    path = os.path.join(_SCHEMA_DIR, spec_version, "%s.schema.json" % kind)
    try:
        with open(path) as fh:
            schema = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SchemaError("cannot load vendored schema %s: %s" % (path, exc))

    _schema_cache[key] = schema
    return schema


class Error(object):
    """One schema violation: the failing keyword and where it occurred.

    ``path`` is a JSON pointer into the *instance*, so callers can decide
    per-field whether a violation is fatal or merely drops that field.
    """

    __slots__ = ("keyword", "path", "message")

    def __init__(self, keyword: str, path: str, message: str):
        self.keyword = keyword
        self.path = path
        self.message = message

    def __repr__(self):
        return "Error(%r, %r, %r)" % (self.keyword, self.path, self.message)

    def __eq__(self, other):
        return (isinstance(other, Error) and self.keyword == other.keyword
                and self.path == other.path)


def validate(instance: Any, schema: dict, root_schema: Optional[dict] = None,
             path: str = "") -> List[Error]:
    """Validate ``instance`` against ``schema``; return all violations found."""
    errors: List[Error] = []
    _validate(instance, schema, root_schema if root_schema is not None else schema,
              path, errors)
    return errors


# ---------------------------------------------------------------------------

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _type_ok(instance: Any, want: str) -> bool:
    if want in ("integer", "number"):
        # JSON booleans are not numbers, but Python bools are ints.
        if isinstance(instance, bool):
            return False
        return isinstance(instance, int) if want == "integer" \
            else isinstance(instance, (int, float))
    py = _TYPES.get(want)
    if py is None:
        return True
    if want != "boolean" and isinstance(instance, bool):
        # bool is a subclass of int; keep it out of every other type bucket
        return py is bool
    return isinstance(instance, py)


def _resolve(ref: str, root_schema: dict) -> dict:
    """Resolve a local JSON pointer ref ('#/$defs/server')."""
    if not ref.startswith("#/"):
        raise SchemaError("unsupported non-local $ref: %s" % ref)
    node: Any = root_schema
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def _join(path: str, token: Any) -> str:
    token = str(token).replace("~", "~0").replace("/", "~1")
    return "%s/%s" % (path, token)


def _validate(instance: Any, schema: dict, root_schema: dict, path: str,
              errors: List[Error]) -> None:
    if "$ref" in schema:
        _validate(instance, _resolve(schema["$ref"], root_schema), root_schema,
                  path, errors)
        # In these schemas $ref is always the sole keyword; anything alongside
        # it is still checked below, which matches 2020-12 semantics.

    if "type" in schema:
        want = schema["type"]
        wants = want if isinstance(want, list) else [want]
        if not any(_type_ok(instance, w) for w in wants):
            errors.append(Error("type", path,
                                "expected %s" % " or ".join(wants)))
            # Every remaining keyword assumes the right type; stop here.
            return

    if "const" in schema and instance != schema["const"]:
        errors.append(Error("const", path,
                            "must be %s" % json.dumps(schema["const"])))

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(Error("enum", path,
                            "must be one of %s" % json.dumps(schema["enum"])))

    if "not" in schema:
        # 'not' fails when the instance *does* match the excluded schema.
        if _validate_ok(instance, schema["not"], root_schema):
            errors.append(Error("not", path, "must not match the excluded schema"))

    if "oneOf" in schema:
        matched = [i for i, sub in enumerate(schema["oneOf"])
                   if _validate_ok(instance, sub, root_schema)]
        if len(matched) != 1:
            errors.append(Error("oneOf", path,
                                "matched %d of %d alternatives, expected exactly 1"
                                % (len(matched), len(schema["oneOf"]))))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(Error("minLength", path,
                                "shorter than %d characters" % schema["minLength"]))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(Error("maxLength", path,
                                "longer than %d characters" % schema["maxLength"]))
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(Error("pattern", path,
                                "does not match %s" % schema["pattern"]))

    if isinstance(instance, list) and "items" in schema:
        for i, item in enumerate(instance):
            _validate(item, schema["items"], root_schema, _join(path, i), errors)

    if isinstance(instance, dict):
        _validate_object(instance, schema, root_schema, path, errors)


def _validate_object(instance: dict, schema: dict, root_schema: dict, path: str,
                     errors: List[Error]) -> None:
    for key in schema.get("required", ()):
        if key not in instance:
            errors.append(Error("required", _join(path, key), "is required"))

    props = schema.get("properties", {})
    for key, value in instance.items():
        if key in props:
            _validate(value, props[key], root_schema, _join(path, key), errors)

    if "propertyNames" in schema:
        for key in instance:
            _validate(key, schema["propertyNames"], root_schema,
                      _join(path, key), errors)

    if "additionalProperties" in schema:
        addl = schema["additionalProperties"]
        extra = [k for k in instance if k not in props]
        if addl is False:
            for key in extra:
                errors.append(Error("additionalProperties", _join(path, key),
                                    "is not a recognized property"))
        elif isinstance(addl, dict):
            for key in extra:
                _validate(instance[key], addl, root_schema, _join(path, key),
                          errors)


def _validate_ok(instance: Any, schema: dict, root_schema: dict) -> bool:
    errors: List[Error] = []
    _validate(instance, schema, root_schema, "", errors)
    return not errors
