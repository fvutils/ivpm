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
"""Loading and validation of plugin manifests.

Two manifest formats are read:

* **Agent Plugins** -- ``<root>/plugin.json``, the vendor-neutral standard.
* **Claude Code** -- ``<root>/.claude-plugin/plugin.json``.  Most plugins
  published for Claude Code carry only this one.

When a directory has both, the Agent Plugins manifest is the plugin's identity
and the Claude manifest is kept alongside it (``PluginManifest.claude``) to be
shipped to Claude Code unchanged.

For the Agent Plugins format the central question is *is this manifest an
Agent Plugins manifest at all?*  ``plugin.json`` is a heavily overloaded
filename, so the answer is layered:

1. the file is named ``plugin.json``, parses as JSON, and is an object;
2. its ``$schema`` is exactly ``https://agent-plugins.org/schemas/<v>/plugin.schema.json``
   -- this is the actual type tag;
3. it satisfies the vendored schema for that version.

Layers 1 and 2 distinguish "not ours, stay quiet" from "ours, but broken, warn".
That distinction matters: a package may legitimately carry an unrelated
``plugin.json``, and warning about it would be noise.

The Claude format needs no such layering: nothing else uses a
``.claude-plugin`` directory, so its location is the type tag.

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

#: Where a Claude Code plugin keeps its manifest, relative to the plugin root.
CLAUDE_MANIFEST_DIR = ".claude-plugin"

FORMAT_AGENT_PLUGINS = "agent-plugins"
FORMAT_CLAUDE = "claude"

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

    ``root_dir`` is the plugin root, against which every plugin-relative path
    resolves: the directory containing ``plugin.json``, or the parent of
    ``.claude-plugin/``.
    """
    root_dir: str
    #: Agent Plugins version; None for a Claude-format manifest
    spec_version: Optional[str]
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
    #: FORMAT_AGENT_PLUGINS or FORMAT_CLAUDE -- which manifest is the identity
    format: str = FORMAT_AGENT_PLUGINS
    #: The parsed ``.claude-plugin/plugin.json``, when the plugin ships one
    claude: Optional[Mapping[str, Any]] = None


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


def claude_manifest_path(root_dir: str) -> str:
    return os.path.join(root_dir, CLAUDE_MANIFEST_DIR, MANIFEST_NAME)


def normalize_plugin_path(path: str) -> Optional[str]:
    """Resolve a plugin reference to its root directory.

    Accepts either spelling, per the extension-point contract, for either
    manifest format:

    * a path to a ``plugin.json`` file  -> its containing directory, or the
      parent of ``.claude-plugin/`` for a Claude manifest
    * a ``.claude-plugin`` directory holding ``plugin.json`` -> its parent
    * a directory containing ``plugin.json`` or ``.claude-plugin/plugin.json``
      -> that directory

    Returns None when the path is none of these.  A *directory* named
    ``plugin.json`` is not a manifest and yields None.
    """
    if not path:
        return None
    abspath = os.path.abspath(path)
    if os.path.basename(abspath) == MANIFEST_NAME and os.path.isfile(abspath):
        parent = os.path.dirname(abspath)
        if os.path.basename(parent) == CLAUDE_MANIFEST_DIR:
            return os.path.dirname(parent)
        return parent
    if not os.path.isdir(abspath):
        return None
    if (os.path.basename(abspath) == CLAUDE_MANIFEST_DIR
            and os.path.isfile(os.path.join(abspath, MANIFEST_NAME))):
        return os.path.dirname(abspath)
    if (os.path.isfile(os.path.join(abspath, MANIFEST_NAME))
            or os.path.isfile(claude_manifest_path(abspath))):
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
    """Load and validate the plugin rooted at ``root_dir``.

    The Agent Plugins manifest (``<root_dir>/plugin.json``) is preferred;
    failing that, ``<root_dir>/.claude-plugin/plugin.json`` is loaded as a
    Claude-format plugin.

    Returns ``(manifest, diagnostics)``.  A None manifest means the plugin was
    rejected; check the diagnostics' severity to tell "not ours" (info) from
    "ours and broken" (warning/error).

    Failure isolation follows the spec: unknown top-level keys and malformed
    *optional* fields are reported and dropped, and only ``$schema``/``name``
    problems reject the plugin.
    """
    diags: List[Diagnostic] = []
    manifest_path = os.path.join(root_dir, MANIFEST_NAME)
    has_claude = os.path.isfile(claude_manifest_path(root_dir))

    kind, version = classify(manifest_path)
    if kind is Classification.NOT_A_PLUGIN:
        if has_claude:
            return _load_claude(root_dir, diags)
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
        if has_claude:
            return _load_claude(root_dir, diags)
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
        claude=_read_claude(root_dir) if has_claude else None,
    ), diags)


def _read_claude(root_dir: str) -> Optional[Dict[str, Any]]:
    try:
        with open(claude_manifest_path(root_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


#: Claude manifest fields that carry over to PluginManifest, with their type
_CLAUDE_FIELDS = {
    "version": str,
    "description": str,
    "author": dict,
    "homepage": str,
    "repository": str,
    "license": str,
    "keywords": list,
}


def _load_claude(root_dir: str,
                 diags: List[Diagnostic]) -> Tuple[Optional[PluginManifest], List[Diagnostic]]:
    """Load ``<root_dir>/.claude-plugin/plugin.json``.

    Claude Code's schema is open and growing, so unknown keys are not
    reported.  The name must satisfy the Agent Plugins name rules even so:
    IVPM uses it as a directory name in every target.
    """
    manifest_path = claude_manifest_path(root_dir)
    data = _read_claude(root_dir)
    if data is None:
        diags.append(Diagnostic(
            "warning", "claude.bad-manifest",
            "%s is not a readable JSON object" % manifest_path))
        return (None, diags)

    # Claude Code names a plugin without a 'name' after its directory.
    name = data.get("name", os.path.basename(os.path.abspath(root_dir)))
    schema = _v.load_schema(SPEC_VERSIONS[-1], "plugin")
    name_errors = _v.validate(name, schema["properties"]["name"], schema, "/name")
    if name_errors:
        for err in name_errors:
            diags.append(Diagnostic("error", _NAME_CODES.get(err.keyword, "name.invalid"),
                                    "invalid plugin name %s in %s: %s"
                                    % (json.dumps(name), manifest_path, _NAME_RULES.get(
                                        err.keyword, err.message)),
                                    path="/name"))
        return (None, diags)

    values: Dict[str, Any] = {}
    for key, typ in _CLAUDE_FIELDS.items():
        if key not in data:
            continue
        value = data[key]
        if key == "repository" and isinstance(value, dict):
            value = value.get("url")
        if key == "keywords" and isinstance(value, list):
            value = [k for k in value if isinstance(k, str)]
        if not isinstance(value, typ):
            diags.append(Diagnostic("warning", "field.invalid",
                                    "ignoring '%s': expected %s" % (key, typ.__name__),
                                    path="/" + key))
            continue
        values[key] = value

    if data.get("userConfig"):
        diags.append(Diagnostic(
            "warning", "claude.user-config",
            "plugin '%s' declares userConfig; IVPM cannot supply it, so the "
            "plugin may need configuring in Claude Code" % name,
            path="/userConfig"))

    return (PluginManifest(
        root_dir=os.path.abspath(root_dir),
        spec_version=None,
        name=name,
        version=values.get("version"),
        description=values.get("description"),
        author=values.get("author"),
        homepage=values.get("homepage"),
        repository=values.get("repository"),
        license=values.get("license"),
        keywords=tuple(values.get("keywords") or ()),
        format=FORMAT_CLAUDE,
        claude=data,
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

    A Claude manifest may name further skill locations in ``skills`` (a
    ``./``-relative path or a list of them).  Each names either one skill (it
    holds ``SKILL.md``) or a container of skills, like ``skills/``.

    A missing ``skills/`` is not an error.  A ``skills`` that exists but is not
    a directory disqualifies only this component type.  Symlinked skill
    directories are followed but must stay within the plugin root.
    """
    diags: List[Diagnostic] = []
    found: List[str] = []
    seen = set()

    def add(path):
        key = os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            found.append(path)

    for path in _scan_skills_container(manifest.root_dir, "skills", diags):
        add(path)

    for rel in _claude_skill_paths(manifest, diags):
        path = os.path.join(manifest.root_dir, rel)
        if not within(manifest.root_dir, path):
            diags.append(Diagnostic(
                "error", "skills.escapes-root",
                "skill path '%s' resolves outside the plugin root; skipped" % rel,
                path="/skills"))
            continue
        if not os.path.isdir(path):
            diags.append(Diagnostic(
                "warning", "skills.not-a-directory",
                "skill path '%s' is not a directory; skipped" % rel,
                path="/skills"))
            continue
        if os.path.isfile(os.path.join(path, "SKILL.md")):
            add(path)
            continue
        for skill in _scan_skills_container(manifest.root_dir, rel, diags):
            add(skill)

    return (found, diags)


def _claude_skill_paths(manifest: PluginManifest,
                        diags: List[Diagnostic]) -> List[str]:
    """Relative skill locations named by the Claude manifest's ``skills``."""
    if manifest.claude is None or "skills" not in manifest.claude:
        return []
    value = manifest.claude["skills"]
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
        diags.append(Diagnostic("warning", "field.invalid",
                                "ignoring 'skills': expected a path or a list of paths",
                                path="/skills"))
        return []
    out = []
    for item in items:
        if not isinstance(item, str) or not item.startswith("./"):
            diags.append(Diagnostic("warning", "field.invalid",
                                    "ignoring skills path %s: paths start with './'"
                                    % json.dumps(item), path="/skills"))
            continue
        rel = os.path.normpath(item[2:])
        if rel != "skills":
            out.append(rel)
    return out


def _scan_skills_container(root_dir: str, rel: str,
                           diags: List[Diagnostic]) -> List[str]:
    """Immediate subdirectories of ``<root_dir>/<rel>`` that hold ``SKILL.md``."""
    skills_dir = os.path.join(root_dir, rel)

    if not os.path.exists(skills_dir):
        return []
    if not os.path.isdir(skills_dir):
        diags.append(Diagnostic(
            "warning", "skills.not-a-directory",
            "%s exists but is not a directory; no skills loaded" % skills_dir))
        return []

    found: List[str] = []
    for entry in sorted(os.listdir(skills_dir)):
        path = os.path.join(skills_dir, entry)
        if not os.path.isdir(path):
            continue
        label = "%s/%s" % (rel.replace(os.sep, "/"), entry)
        if not within(root_dir, path):
            diags.append(Diagnostic(
                "error", "skills.escapes-root",
                "skill '%s' resolves outside the plugin root; skipped" % entry,
                path=label))
            continue
        skill_md = os.path.join(path, "SKILL.md")
        if not os.path.isfile(skill_md):
            diags.append(Diagnostic(
                "warning", "skills.no-skill-md",
                "skill directory '%s' has no SKILL.md; skipped" % entry,
                path=label))
            continue
        found.append(path)

    return found
