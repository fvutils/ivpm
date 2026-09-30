#****************************************************************************
#* mcp.py
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
"""Loading and validation of Agent Plugins ``mcp.json`` documents.

Parsing lives here; *aggregation* into a workspace-level configuration is a
separate, opt-in step (see the agents handler).  The split is deliberate:
``ivpm show plugins --mcp`` must be able to report exactly what a dependency
would wire up without anything having been written to the workspace.

Beyond the vendored JSON schema, the specification carries semantic rules the
schema explicitly defers ("URL semantics are defined by the Agent Plugins
specification"):

* ``command`` is a single executable token -- a bare name or a ``./``-prefixed
  plugin-relative path.  Never a shell string.
* plugin-relative paths must stay inside the filesystem-resolved plugin root.
* non-loopback URLs must be HTTPS.
* ``env`` must not define ``PLUGIN_ROOT`` or ``PLUGIN_DATA`` (schema-enforced,
  via ``propertyNames``).

Those are checked here, per server, so that one bad server never takes down its
siblings.
"""
import dataclasses as dc
import json
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from . import validate as _v
from .manifest import SPEC_VERSIONS, Diagnostic, PluginManifest, within

MCP_NAME = "mcp.json"
#: Claude Code's spelling, also found in plugins that target it
CLAUDE_MCP_NAME = ".mcp.json"

_MCP_SCHEMA_RE = re.compile(
    r"^https://agent-plugins\.org/schemas/(\d+\.\d+\.\d+)/mcp\.schema\.json$")

#: Server ``type`` -> ($defs key, is-legacy)
_TRANSPORTS = {
    "stdio": ("stdioServer", False),
    "streamable-http": ("streamableHttpServer", False),
    "sse": ("sseServer", True),
}

_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")


@dc.dataclass(frozen=True)
class McpServer:
    """One validated MCP server entry.

    Paths and placeholders are recorded as written.  ``${PLUGIN_ROOT}`` and
    ``${PLUGIN_DATA}`` are deliberately *not* expanded: expansion is the
    launching client's job and is defined as a single non-recursive pass, so
    expanding early would risk double expansion.
    """
    name: str
    type: str
    command: Optional[str] = None
    args: Tuple[str, ...] = ()
    env: Mapping[str, str] = dc.field(default_factory=dict)
    cwd: Optional[str] = None
    url: Optional[str] = None
    headers: Mapping[str, str] = dc.field(default_factory=dict)
    #: True for the deprecated 'sse' transport, which clients may not support.
    legacy: bool = False


@dc.dataclass(frozen=True)
class McpConfig:
    root_dir: str
    #: Agent Plugins version; None when read from Claude Code's spelling
    spec_version: Optional[str]
    servers: Tuple[McpServer, ...] = ()
    #: Where the servers were declared: 'mcp.json', '.mcp.json' or
    #: 'plugin.json#mcpServers'
    source: str = MCP_NAME


def load_mcp(manifest: PluginManifest) -> Tuple[Optional[McpConfig], List[Diagnostic]]:
    """Load the plugin's MCP configuration, if it has any.

    ``<plugin-root>/mcp.json`` (Agent Plugins) is preferred.  Without it, the
    Claude Code spelling is read: ``.mcp.json``, or ``mcpServers`` in the
    Claude manifest.

    A missing file is not an error.  A version mismatch against the owning
    ``plugin.json`` invalidates the *MCP configuration only* -- the plugin and
    its skills remain valid, per the spec.
    """
    diags: List[Diagnostic] = []
    path = os.path.join(manifest.root_dir, MCP_NAME)

    if not os.path.exists(path):
        return _load_claude_mcp(manifest, diags)

    if os.path.isfile(os.path.join(manifest.root_dir, CLAUDE_MCP_NAME)):
        diags.append(Diagnostic(
            "info", "mcp.duplicate",
            "plugin has both %s and %s; %s is used" % (MCP_NAME, CLAUDE_MCP_NAME, MCP_NAME)))
    if not os.path.isfile(path):
        diags.append(Diagnostic("warning", "mcp.not-a-file",
                                "%s is not a regular file; ignored" % path))
        return (None, diags)

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        diags.append(Diagnostic("error", "mcp.unreadable",
                                "cannot read %s: %s" % (path, exc)))
        return (None, diags)

    if not isinstance(data, dict):
        diags.append(Diagnostic("error", "mcp.not-an-object",
                                "%s must contain a JSON object" % path))
        return (None, diags)

    schema_id = data.get("$schema")
    m = _MCP_SCHEMA_RE.match(schema_id) if isinstance(schema_id, str) else None
    if m is None:
        diags.append(Diagnostic(
            "error", "mcp.bad-schema",
            "%s has no recognized Agent Plugins MCP $schema" % path,
            path="/$schema"))
        return (None, diags)

    version = m.group(1)
    if manifest.spec_version is not None and version != manifest.spec_version:
        diags.append(Diagnostic(
            "error", "mcp.version-skew",
            "%s targets Agent Plugins %s but plugin.json targets %s; "
            "MCP configuration ignored" % (path, version, manifest.spec_version),
            path="/$schema"))
        return (None, diags)

    schema = _v.load_schema(version, "mcp")

    servers_raw = data.get("mcpServers")
    if not isinstance(servers_raw, dict):
        diags.append(Diagnostic(
            "error", "mcp.no-servers",
            "%s has no 'mcpServers' object" % path, path="/mcpServers"))
        return (None, diags)

    for key in sorted(k for k in data if k not in schema.get("properties", {})):
        diags.append(Diagnostic("info", "mcp.unknown-key",
                                "unknown top-level key '%s' ignored" % key,
                                path="/" + key))

    servers: List[McpServer] = []
    for name in sorted(servers_raw):
        server, sdiags = _load_server(name, servers_raw[name], manifest, schema)
        diags.extend(sdiags)
        if server is not None:
            servers.append(server)

    return (McpConfig(root_dir=manifest.root_dir, spec_version=version,
                      servers=tuple(servers)), diags)


def _load_claude_mcp(manifest: PluginManifest,
                     diags: List[Diagnostic]) -> Tuple[Optional[McpConfig], List[Diagnostic]]:
    """Read MCP servers declared the way Claude Code expects them.

    Claude's server entries differ from Agent Plugins' only in spelling: no
    ``type`` means stdio, ``http`` means streamable HTTP, and the plugin root
    is ``${CLAUDE_PLUGIN_ROOT}``.  Each entry is respelled and then held to the
    same per-server rules, so ``ivpm show plugins --mcp`` reviews both formats
    alike.
    """
    declared = manifest.claude.get("mcpServers") if manifest.claude else None
    if declared is not None:
        source = "plugin.json#mcpServers"
        if isinstance(declared, dict) and "mcpServers" not in declared:
            servers_raw = declared
        else:
            servers_raw = _read_claude_mcp_refs(manifest, declared, diags)
    elif os.path.isfile(os.path.join(manifest.root_dir, CLAUDE_MCP_NAME)):
        source = CLAUDE_MCP_NAME
        servers_raw = _read_claude_mcp_refs(manifest, "./" + CLAUDE_MCP_NAME, diags)
    else:
        return (None, diags)

    if servers_raw is None:
        return (None, diags)

    schema = _v.load_schema(SPEC_VERSIONS[-1], "mcp")
    servers: List[McpServer] = []
    for name in sorted(servers_raw):
        raw = servers_raw[name]
        if isinstance(raw, dict):
            raw = _respell_claude_server(raw)
        server, sdiags = _load_server(name, raw, manifest, schema)
        diags.extend(sdiags)
        if server is not None:
            servers.append(server)

    return (McpConfig(root_dir=manifest.root_dir, spec_version=None,
                      servers=tuple(servers), source=source), diags)


def _read_claude_mcp_refs(manifest: PluginManifest, refs: Any,
                          diags: List[Diagnostic]) -> Optional[Dict[str, Any]]:
    """Merge the ``mcpServers`` of one or more ``./``-relative JSON files."""
    items = [refs] if isinstance(refs, (str, dict)) else refs
    if not isinstance(items, list):
        diags.append(Diagnostic("warning", "mcp.not-an-object",
                                "'mcpServers' must be an object, a path, or a list"))
        return None
    merged: Dict[str, Any] = {}
    for item in items:
        if isinstance(item, dict):
            doc = item
        elif isinstance(item, str) and item.startswith("./"):
            path = os.path.join(manifest.root_dir, item[2:])
            if not within(manifest.root_dir, path):
                diags.append(Diagnostic("warning", "mcp.escapes-root",
                                        "%s resolves outside the plugin root; ignored" % item))
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                diags.append(Diagnostic("error", "mcp.unreadable",
                                        "cannot read %s: %s" % (path, exc)))
                continue
        else:
            diags.append(Diagnostic("warning", "mcp.not-an-object",
                                    "ignoring MCP reference %s" % json.dumps(item)))
            continue
        if not isinstance(doc, dict):
            diags.append(Diagnostic("error", "mcp.not-an-object",
                                    "MCP configuration %s must be a JSON object" % item))
            continue
        servers = doc.get("mcpServers", doc)
        if isinstance(servers, dict):
            merged.update(servers)
    return merged


#: Fields the Agent Plugins schema allows per transport. Claude Code accepts
#: more (timeouts, OAuth settings); those are its business, and the plugin's
#: own file is what Claude Code reads, so they are dropped from the review copy
#: rather than failing the server.
_SERVER_FIELDS = {
    "stdio": ("type", "command", "args", "env", "cwd"),
    "streamable-http": ("type", "url", "headers"),
    "sse": ("type", "url", "headers"),
}


def _respell_claude_server(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(raw)
    if "type" not in out and "command" in out:
        out["type"] = "stdio"
    elif out.get("type") == "http":
        out["type"] = "streamable-http"
    fields = _SERVER_FIELDS.get(out.get("type"))
    if fields is not None:
        out = {k: v for k, v in out.items() if k in fields}

    def respell(value):
        if not isinstance(value, str):
            return value
        return (value.replace("${CLAUDE_PLUGIN_ROOT}", "${PLUGIN_ROOT}")
                .replace("${CLAUDE_PLUGIN_DATA}", "${PLUGIN_DATA}"))

    command = out.get("command")
    if isinstance(command, str) and command.startswith("${CLAUDE_PLUGIN_ROOT}/"):
        # Agent Plugins spells a plugin-relative executable './x'
        out["command"] = "./" + command[len("${CLAUDE_PLUGIN_ROOT}/"):]
    cwd = out.get("cwd")
    if isinstance(cwd, str) and cwd.startswith("${CLAUDE_PLUGIN_ROOT}/"):
        out["cwd"] = "./" + cwd[len("${CLAUDE_PLUGIN_ROOT}/"):]
    if isinstance(out.get("args"), list):
        out["args"] = [respell(a) for a in out["args"]]
    if isinstance(out.get("env"), dict):
        out["env"] = {k: respell(v) for k, v in out["env"].items()}
    return out


# ---------------------------------------------------------------------------

def _load_server(name: str, raw: Any, manifest: PluginManifest,
                 schema: dict) -> Tuple[Optional[McpServer], List[Diagnostic]]:
    """Validate one server. A failure here skips this server only."""
    diags: List[Diagnostic] = []
    ptr = "/mcpServers/" + name

    if not isinstance(raw, dict):
        diags.append(Diagnostic("warning", "server.not-an-object",
                                "server '%s' is not an object; skipped" % name,
                                path=ptr))
        return (None, diags)

    transport = raw.get("type")
    if not isinstance(transport, str):
        diags.append(Diagnostic("warning", "server.no-type",
                                "server '%s' has no 'type'; skipped" % name,
                                path=ptr))
        return (None, diags)

    entry = _TRANSPORTS.get(transport)
    if entry is None:
        # Not an error: a client is free not to support a transport, and a
        # future spec version may add one. Skip it, keep the siblings.
        diags.append(Diagnostic(
            "warning", "server.unsupported-transport",
            "server '%s' uses unsupported transport '%s'; skipped"
            % (name, transport), path=ptr))
        return (None, diags)

    defs_key, legacy = entry
    errors = _v.validate(raw, schema["$defs"][defs_key], schema, ptr)
    if errors:
        for err in errors:
            diags.append(Diagnostic("warning", "server.invalid",
                                    "server '%s' is invalid: %s" % (name, err.message),
                                    path=err.path))
        return (None, diags)

    if transport == "stdio":
        return _load_stdio(name, raw, manifest, ptr, diags, legacy)
    return _load_http(name, raw, transport, ptr, diags, legacy)


def _load_stdio(name, raw, manifest, ptr, diags, legacy):
    command = raw["command"]

    # "Single token" is the whole point: a shell string here would mean the
    # launching client runs it through a shell.
    if command.split() != [command]:
        diags.append(Diagnostic(
            "warning", "server.command-not-a-token",
            "server '%s' command %s is not a single executable token; skipped"
            % (name, json.dumps(command)), path=ptr + "/command"))
        return (None, diags)

    if command.startswith("./"):
        resolved = os.path.join(manifest.root_dir, command[2:])
        if not within(manifest.root_dir, resolved):
            diags.append(Diagnostic(
                "warning", "server.command-escapes-root",
                "server '%s' command %s resolves outside the plugin root; skipped"
                % (name, json.dumps(command)), path=ptr + "/command"))
            return (None, diags)
    elif os.sep in command or "/" in command or "\\" in command:
        # Anything with a separator that is not './'-prefixed is neither a bare
        # name nor a legal plugin-relative path.
        diags.append(Diagnostic(
            "warning", "server.command-path",
            "server '%s' command %s must be a bare executable name or a "
            "'./'-relative path; skipped" % (name, json.dumps(command)),
            path=ptr + "/command"))
        return (None, diags)

    cwd = raw.get("cwd")
    if cwd is not None and cwd.startswith("./"):
        resolved = os.path.join(manifest.root_dir, cwd[2:])
        if not within(manifest.root_dir, resolved):
            diags.append(Diagnostic(
                "warning", "server.cwd-escapes-root",
                "server '%s' cwd %s resolves outside the plugin root; skipped"
                % (name, json.dumps(cwd)), path=ptr + "/cwd"))
            return (None, diags)

    return (McpServer(
        name=name,
        type="stdio",
        command=command,
        args=tuple(raw.get("args") or ()),
        env=dict(raw.get("env") or {}),
        cwd=cwd,
        legacy=legacy,
    ), diags)


def _load_http(name, raw, transport, ptr, diags, legacy):
    url = raw["url"]
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        diags.append(Diagnostic(
            "warning", "server.url-invalid",
            "server '%s' url %s must be an absolute http(s) URL; skipped"
            % (name, json.dumps(url)), path=ptr + "/url"))
        return (None, diags)

    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        diags.append(Diagnostic(
            "warning", "server.url-insecure",
            "server '%s' url %s uses plain HTTP to a non-loopback host; skipped"
            % (name, json.dumps(url)), path=ptr + "/url"))
        return (None, diags)

    return (McpServer(
        name=name,
        type=transport,
        url=url,
        headers=dict(raw.get("headers") or {}),
        legacy=legacy,
    ), diags)


def _is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    host = host.lower()
    if host in _LOOPBACK_HOSTS:
        return True
    # The whole 127.0.0.0/8 block is loopback, not just 127.0.0.1.
    try:
        import ipaddress
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
