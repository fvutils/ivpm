#****************************************************************************
#* manifest.py
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
"""Loading and validation of Agent Plugins ``plugin.json`` manifests.

The central question this module answers is *is this manifest an Agent Plugins
manifest at all?*  ``plugin.json`` is a heavily overloaded filename, so the
answer is layered:

1. the file is named ``plugin.json``, parses as JSON, and is an object;
2. its ``$schema`` is exactly ``https://agent-plugins.org/schemas/<v>/plugin.schema.json``
   -- this is the actual type tag;
3. it satisfies the vendored schema for that version.

Layers 1 and 2 distinguish "not ours, stay quiet" from "ours, but broken, warn".
That distinction matters: a package may legitimately carry an unrelated
``plugin.json``, and warning about it would be noise.

Diagnostics are *returned*, never printed, so the caller chooses the log level:
the agents handler warns, ``ivpm show plugins --check`` renders a report, and
auto-probe discards informational notes.
"""
import dataclasses as dc
import enum
import json
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import validate as _v

#: Agent Plugins specification versions this build understands.
SPEC_VERSIONS = ("1.0.0",)

MANIFEST_NAME = "plugin.json"

_PLUGIN_SCHEMA_RE = re.compile(
    r"^https://agent-plugins\.org/schemas/(\d+\.\d+\.\d+)/plugin\.schema\.json$")


class Classification(enum.Enum):
    """Outcome of the cheap identification pass (layers 1-2 above)."""

    #: Not an Agent Plugins manifest. Not an error -- callers stay quiet.
    NOT_A_PLUGIN = "not-a-plugin"
    #: An Agent Plugins manifest targeting a version this build cannot read.
    UNSUPPORTED_VERSION = "unsupported-version"
    #: An Agent Plugins manifest at a supported version.
    PLUGIN = "plugin"


@dc.dataclass(frozen=True)
class Diagnostic:
    """One problem found while loading a plugin.

    ``severity`` is "error" (the affected thing is unusable), "warning" (the
    affected field or component was dropped) or "info" (worth reporting to a
    plugin author, harmless to a consumer).  ``code`` is stable and
    machine-greppable; ``message`` is for humans.
    """
    severity: str
    code: str
    message: str
    path: Optional[str] = None

    def __str__(self):
        where = " (at %s)" % self.path if self.path else ""
        return "%s: %s%s" % (self.severity, self.message, where)


@dc.dataclass(frozen=True)
class PluginManifest:
    """A validated ``plugin.json``.

    ``root_dir`` is the directory containing the manifest -- the plugin root,
    against which every plugin-relative path resolves.
    """
    root_dir: str
    spec_version: str
    name: str
    version: Optional[str] = None
    description: Optional[str] = None
    author: Optional[Mapping[str, str]] = None
    homepage: Optional[str] = None
    repository: Optional[str] = None
    license: Optional[str] = None
    keywords: Tuple[str, ...] = ()
    extensions: Mapping[str, Any] = dc.field(default_factory=dict)
    #: Unknown top-level keys. The spec requires reporting and ignoring these
    #: rather than rejecting the plugin, so they are recorded, not fatal.
    unknown_keys: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def within(root: str, candidate: str) -> bool:
    """True when ``candidate`` resolves to ``root`` or something below it.

    Both sides are fully resolved first, so this is correct in the presence of
    symlinks -- including the shared-package-cache case, where a dependency in
    the workspace is a symlink into the cache.

    Note this is a *filesystem* containment check.  The archive extractor in
    ``pkg_types/package_file.py`` performs a superficially similar check over
    tar member names, but that one works in the archive's own posix namespace
    with nothing on disk yet, so the two cannot share an implementation.
    """
    root_r = os.path.realpath(root)
    cand_r = os.path.realpath(candidate)
    return cand_r == root_r or cand_r.startswith(root_r + os.sep)


def normalize_plugin_path(path: str) -> Optional[str]:
    """Resolve a plugin reference to its root directory.

    Accepts either spelling, per the extension-point contract:

    * a path to a ``plugin.json`` file  -> its containing directory
    * a directory containing ``plugin.json`` -> that directory

    Returns None when the path is neither.  A *directory* named ``plugin.json``
    is not a manifest and yields None.
    """
    if not path:
        return None
    abspath = os.path.abspath(path)
    if os.path.basename(abspath) == MANIFEST_NAME and os.path.isfile(abspath):
        return os.path.dirname(abspath)
    if os.path.isdir(abspath) and os.path.isfile(os.path.join(abspath, MANIFEST_NAME)):
        return abspath
    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(manifest_path: str) -> Tuple[Classification, Optional[str]]:
    """Cheap identification: filename, JSON shape, and ``$schema``.

    Returns ``(classification, spec_version)``.  Never raises -- an unreadable
    file, malformed JSON, or a non-object document is simply not a plugin.
    """
    data = _read_json(manifest_path)
    if not isinstance(data, dict):
        return (Classification.NOT_A_PLUGIN, None)

    schema_id = data.get("$schema")
    if not isinstance(schema_id, str):
        return (Classification.NOT_A_PLUGIN, None)

    m = _PLUGIN_SCHEMA_RE.match(schema_id)
    if m is None:
        return (Classification.NOT_A_PLUGIN, None)

    version = m.group(1)
    if version not in SPEC_VERSIONS:
        return (Classification.UNSUPPORTED_VERSION, version)
    return (Classification.PLUGIN, version)


def _read_json(path: str) -> Any:
    if os.path.basename(path) != MANIFEST_NAME:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_plugin(root_dir: str) -> Tuple[Optional[PluginManifest], List[Diagnostic]]:
    """Load and validate ``<root_dir>/plugin.json``.

    Returns ``(manifest, diagnostics)``.  A None manifest means the plugin was
    rejected; check the diagnostics' severity to tell "not ours" (info) from
    "ours and broken" (warning/error).

    Failure isolation follows the spec: unknown top-level keys and malformed
    *optional* fields are reported and dropped, and only ``$schema``/``name``
    problems reject the plugin.
    """
    diags: List[Diagnostic] = []
    manifest_path = os.path.join(root_dir, MANIFEST_NAME)

    kind, version = classify(manifest_path)
    if kind is Classification.NOT_A_PLUGIN:
        diags.append(Diagnostic(
            "info", "manifest.not-a-plugin",
            "%s is not an Agent Plugins manifest (no recognized $schema)"
            % manifest_path))
        return (None, diags)
    if kind is Classification.UNSUPPORTED_VERSION:
        diags.append(Diagnostic(
            "warning", "manifest.unsupported-version",
            "%s targets Agent Plugins %s; this build supports %s"
            % (manifest_path, version, ", ".join(SPEC_VERSIONS))))
        return (None, diags)

    data = _read_json(manifest_path)
    schema = _v.load_schema(version, "plugin")
    props: Dict[str, dict] = schema.get("properties", {})

    # Unknown top-level keys: report, ignore, keep loading (spec requirement).
    # This is exactly where a strict schema run would reject the document --
    # the published schema sets additionalProperties:false -- so unknown keys
    # are split off *before* validation rather than validated and downgraded.
    unknown = tuple(sorted(k for k in data if k not in props))
    for key in unknown:
        diags.append(Diagnostic(
            "info", "manifest.unknown-key",
            "unknown top-level key '%s' ignored" % key, path="/" + key))

    # --- core fields: failures here reject the whole plugin ---------------
    name = data.get("name")
    if "name" not in data:
        diags.append(Diagnostic("error", "name.missing",
                                "manifest has no 'name'", path="/name"))
        return (None, diags)

    name_errors = _v.validate(name, props["name"], schema, "/name")
    if name_errors:
        for err in name_errors:
            diags.append(Diagnostic("error", _NAME_CODES.get(err.keyword, "name.invalid"),
                                    "invalid plugin name %s: %s"
                                    % (json.dumps(name), _NAME_RULES.get(
                                        err.keyword, err.message)),
                                    path="/name"))
        return (None, diags)

    # --- optional fields: a bad one is dropped, the plugin survives -------
    values: Dict[str, Any] = {}
    for key in ("version", "description", "author", "homepage", "repository",
                "license", "keywords", "extensions"):
        if key not in data:
            continue
        errors = _v.validate(data[key], props[key], schema, "/" + key)
        if errors:
            for err in errors:
                diags.append(Diagnostic(
                    "warning", "field.invalid",
                    "ignoring '%s': %s" % (key, err.message), path=err.path))
            continue
        values[key] = data[key]

    extensions = dict(values.get("extensions") or {})

    return (PluginManifest(
        root_dir=os.path.abspath(root_dir),
        spec_version=version,
        name=name,
        version=values.get("version"),
        description=values.get("description"),
        author=values.get("author"),
        homepage=values.get("homepage"),
        repository=values.get("repository"),
        license=values.get("license"),
        keywords=tuple(values.get("keywords") or ()),
        extensions=extensions,
        unknown_keys=unknown,
    ), diags)


_NAME_CODES = {
    "pattern": "name.charset",
    "minLength": "name.length",
    "maxLength": "name.length",
    "type": "name.type",
}

# The schema's raw regex is accurate but unreadable in an error message, so
# state the rule it encodes instead. The regex remains the authority; these
# strings only describe it.
_NAME_RULES = {
    "pattern": ("names use lowercase letters, digits, '-' and '.', must start "
                "and end with a letter or digit, and may not contain '--' or '..'"),
    "minLength": "names must be at least 1 character",
    "maxLength": "names must be at most 64 characters",
    "type": "name must be a string",
}


# ---------------------------------------------------------------------------
# Component discovery
# ---------------------------------------------------------------------------

def iter_skill_dirs(manifest: PluginManifest) -> Tuple[List[str], List[Diagnostic]]:
    """Return the plugin's skill directories.

    Per the spec these are the *immediate* subdirectories of ``skills/`` that
    contain a regular file named ``SKILL.md``.  Nothing is searched recursively:
    ``skills/a/b/SKILL.md`` is not a skill.

    A missing ``skills/`` is not an error.  A ``skills`` that exists but is not
    a directory disqualifies only this component type.  Symlinked skill
    directories are followed but must stay within the plugin root.
    """
    diags: List[Diagnostic] = []
    skills_dir = os.path.join(manifest.root_dir, "skills")

    if not os.path.exists(skills_dir):
        return ([], diags)
    if not os.path.isdir(skills_dir):
        diags.append(Diagnostic(
            "warning", "skills.not-a-directory",
            "%s exists but is not a directory; no skills loaded" % skills_dir))
        return ([], diags)

    found: List[str] = []
    for entry in sorted(os.listdir(skills_dir)):
        path = os.path.join(skills_dir, entry)
        if not os.path.isdir(path):
            continue
        if not within(manifest.root_dir, path):
            diags.append(Diagnostic(
                "error", "skills.escapes-root",
                "skill '%s' resolves outside the plugin root; skipped" % entry,
                path="skills/" + entry))
            continue
        skill_md = os.path.join(path, "SKILL.md")
        if not os.path.isfile(skill_md):
            diags.append(Diagnostic(
                "warning", "skills.no-skill-md",
                "skill directory '%s' has no SKILL.md; skipped" % entry,
                path="skills/" + entry))
            continue
        found.append(path)

    return (found, diags)
