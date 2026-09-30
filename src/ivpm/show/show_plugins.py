#****************************************************************************
#* show_plugins.py
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
"""Rendering for 'ivpm show plugins [name]'.

Four modes:

* bare        -- list the Agent Plugins available in this project
* <name>      -- detail for one plugin
* ``--check`` -- validate a plugin (or manifest) path and report conformance
* ``--mcp``   -- show what MCP servers the plugins would wire up
* ``--from``  -- list what a marketplace offers, fetching only its catalog

``--mcp`` exists so a human can review MCP configuration *before* enabling it.
It therefore prints environment-variable **names only**, never values: those
routinely carry tokens, and this output should be safe to paste into an issue.
"""
import dataclasses
import json
import os
import sys

from ..tui_theme import make_console


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def _package_dirs(project_dir, proj_info, deps_dir, dep_set):
    """Return (name, dir, dep_agents_config) for each installed dependency.

    Prefers package-lock.json (which lists transitive packages too, with the
    'agents:' config each was resolved with) and falls back to the dep-set's
    declared names when the project has not been updated.
    """
    locked = {}
    lock_path = os.path.join(deps_dir, "package-lock.json")
    if os.path.isfile(lock_path):
        try:
            with open(lock_path) as fh:
                data = json.load(fh)
            locked = data.get("packages", data) or {}
        except (OSError, ValueError):
            locked = {}

    dep_set_obj = proj_info.dep_set_m.get(dep_set) if proj_info.dep_set_m else None
    packages = dep_set_obj.packages if dep_set_obj is not None else {}

    names = sorted(locked.keys()) or list(packages.keys())

    result = []
    for name in names:
        pkg_dir = os.path.join(deps_dir, name)
        if not os.path.isdir(pkg_dir):
            continue
        entry = locked.get(name)
        cfg = entry.get("agents") if isinstance(entry, dict) else None
        if cfg is None:
            cfg = getattr(packages.get(name), "agents_config", None)
        result.append((name, pkg_dir, cfg))
    return result


def _collect(project_dir, dep_set=None, with_mcp=True):
    """Discover every plugin visible from ``project_dir``.

    Returns ``(plugins, diagnostics, agents_config)``; the last is the
    project's ``with.agents`` block, which decides what installation leaves
    out.
    """
    from ..agent_plugins import discovery
    from ..proj_info import ProjInfo, resolve_deps_dir

    proj_info = ProjInfo.mkFromProj(project_dir)
    if proj_info is None:
        raise FileNotFoundError(
            "No ivpm.yaml found in '%s'. Run 'ivpm init' or specify -p."
            % project_dir)

    if dep_set is None:
        dep_set = (proj_info.default_dep_set
                   or (next(iter(proj_info.dep_set_m)) if proj_info.dep_set_m else "default"))

    deps_dir = resolve_deps_dir(project_dir, proj_info)
    project_name = proj_info.name or os.path.basename(os.path.normpath(project_dir))

    # The project's own patterns come straight off the already-parsed ProjInfo;
    # re-reading its ivpm.yaml through resolve_patterns() would emit a second
    # "Reading ivpm.yaml" note for no benefit.
    agents_cfg = proj_info.handler_configs.get("agents", {}) or {}
    project_patterns = agents_cfg.get("plugins", None)
    if project_patterns is not None:
        project_patterns = [str(p) for p in project_patterns]

    plugins, diags = discovery.discover(
        project_name, project_dir, project_patterns,
        kind="project", with_mcp=with_mcp)

    for name, pkg_dir, dep_cfg in _package_dirs(project_dir, proj_info, deps_dir, dep_set):
        found, fdiags = discovery.discover(
            name, pkg_dir,
            discovery.resolve_patterns(pkg_dir, dep_cfg),
            kind="dependency", with_mcp=with_mcp,
            overlays=(dep_cfg or {}).get("plugin_manifests"))
        plugins.extend(found)
        diags.extend(fdiags)

    return plugins, diags, agents_cfg


def _gates(agents_cfg):
    """``(emit_mcp, emit_exec)`` as the agents handler reads them."""
    agents_cfg = agents_cfg or {}
    emit_exec = agents_cfg.get("executables", None)
    return (bool(agents_cfg.get("mcp", False)),
            None if emit_exec is None else bool(emit_exec))


def _omitted(plugin, gates):
    """Components installation leaves out of this plugin, kind -> key."""
    from ..agent_plugins import components
    emit_mcp, emit_exec = gates
    if emit_exec is None:
        emit_exec = components.executables_default(plugin.kind)
    return components.omitted(plugin.components, emit_mcp, emit_exec)


def _format_label(m):
    if m.format == "claude":
        return "Claude Code"
    return "Agent Plugins %s" % m.spec_version


def _as_dict(plugin, gates=(False, None)):
    m = plugin.manifest
    return {
        "name": m.name,
        "owner": plugin.owner_name,
        "kind": plugin.kind,
        "format": m.format,
        "version": m.version,
        "description": m.description,
        "spec_version": m.spec_version,
        "root_dir": m.root_dir,
        "license": m.license,
        "homepage": m.homepage,
        "repository": m.repository,
        "keywords": list(m.keywords),
        "extensions": sorted(m.extensions.keys()),
        "unknown_keys": list(m.unknown_keys),
        "skills": [os.path.basename(d) for d in plugin.skill_dirs],
        "mcp_servers": [_server_as_dict(s) for s in (plugin.mcp.servers if plugin.mcp else ())],
        "components": {k: list(v) for k, v in plugin.components.items()},
        "omitted": _omitted(plugin, gates),
        "diagnostics": [dataclasses.asdict(d) for d in plugin.diagnostics],
    }


def _component_lines(plugin, gates):
    """``(label, text)`` rows describing components beyond skills.

    Everything Claude Code alone understands is listed, then whatever
    installation leaves out and the key that would bring it in.
    """
    from ..agent_plugins import components
    rows = []
    only = ["%s (%s)" % (k, ", ".join(v)) for k, v in plugin.components.items()
            if k in components.CLAUDE_ONLY_KINDS]
    if only:
        rows.append(("Claude Code only", "; ".join(only)))
    omitted = _omitted(plugin, gates)
    if omitted:
        rows.append(("Not installed", "; ".join(
            "%s (%s: false)" % (k, key) for k, key in omitted.items())))
    return rows


def _server_as_dict(server):
    """Serialize an MCP server. Environment *values* are deliberately omitted."""
    return {
        "name": server.name,
        "type": server.type,
        "command": server.command,
        "args": list(server.args),
        "env_keys": sorted(server.env.keys()),
        "cwd": server.cwd,
        "url": server.url,
        "header_names": sorted(server.headers.keys()),
        "legacy": server.legacy,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _rich_list(plugins):
    from rich.table import Table
    from rich import box

    console = make_console()
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Plugin", style="cyan bold")
    table.add_column("Format", style="secondary")
    table.add_column("Version", style="secondary")
    table.add_column("Skills", justify="right")
    table.add_column("MCP", justify="right")
    table.add_column("Provided by", style="secondary")
    table.add_column("Description")

    for p in plugins:
        n_mcp = len(p.mcp.servers) if p.mcp else 0
        table.add_row(p.name, p.manifest.format, p.manifest.version or "",
                      str(len(p.skill_dirs)), str(n_mcp) if n_mcp else "",
                      "%s (%s)" % (p.owner_name, p.kind),
                      p.manifest.description or "")
    console.print(table)


def _plain_list(plugins):
    for p in plugins:
        n_mcp = len(p.mcp.servers) if p.mcp else 0
        print("%-24s %-13s %-10s skills=%-3d mcp=%-3d %s"
              % (p.name, p.manifest.format, p.manifest.version or "-",
                 len(p.skill_dirs), n_mcp, p.owner_name))


def _plain_detail(p, gates):
    m = p.manifest
    print("Plugin:       %s" % m.name)
    print("Provided by:  %s (%s)" % (p.owner_name, p.kind))
    print("Format:       %s" % _format_label(m))
    if m.version:
        print("Version:      %s" % m.version)
    if m.description:
        print("Description:  %s" % m.description)
    if m.license:
        print("License:      %s" % m.license)
    if m.homepage:
        print("Homepage:     %s" % m.homepage)
    print("Root:         %s" % m.root_dir)
    if p.skill_dirs:
        print("\nSkills:")
        for d in p.skill_dirs:
            print("  %s" % os.path.basename(d))
    if p.mcp and p.mcp.servers:
        print("\nMCP servers:")
        for s in p.mcp.servers:
            print("  %s" % _server_line(s))
    rows = _component_lines(p, gates)
    if rows:
        print("")
        for label, text in rows:
            print("%-17s %s" % (label + ":", text))
    if m.extensions:
        print("\nExtension namespaces:")
        for ns in sorted(m.extensions):
            print("  %s" % ns)
    if m.unknown_keys:
        print("\nIgnored unknown manifest keys: %s" % ", ".join(m.unknown_keys))
    _print_diagnostics(p.diagnostics)


def _rich_detail(p, gates):
    console = make_console()
    m = p.manifest
    console.print("\n[bold cyan]Plugin:[/] [bold]%s[/]  [label](%s)[/]"
                  % (m.name, _format_label(m)))
    console.print("[label]Provided by:[/] %s (%s)" % (p.owner_name, p.kind))
    if m.version:
        console.print("[label]Version:[/] %s" % m.version)
    if m.description:
        console.print("[bold]Description:[/] %s" % m.description)
    if m.license:
        console.print("[label]License:[/] %s" % m.license)
    if m.homepage:
        console.print("[label]Homepage:[/] %s" % m.homepage)
    console.print("[label]Root:[/] %s" % m.root_dir)

    if p.skill_dirs:
        console.print("\n[bold]Skills:[/]")
        for d in p.skill_dirs:
            console.print("  [cyan]%s[/]" % os.path.basename(d))
    if p.mcp and p.mcp.servers:
        console.print("\n[bold]MCP servers:[/]")
        for s in p.mcp.servers:
            console.print("  %s" % _server_line(s))
    rows = _component_lines(p, gates)
    if rows:
        console.print("")
        for label, text in rows:
            console.print("[label]%s:[/] %s" % (label, text))
    if m.extensions:
        console.print("\n[bold]Extension namespaces:[/]")
        for ns in sorted(m.extensions):
            console.print("  [secondary]%s[/]" % ns)
    if m.unknown_keys:
        console.print("\n[label]Ignored unknown manifest keys:[/] %s"
                      % ", ".join(m.unknown_keys))
    _print_diagnostics(p.diagnostics)


def _server_line(s):
    """One-line server summary. Never includes environment *values*."""
    if s.type == "stdio":
        detail = " ".join([s.command] + list(s.args))
        if s.cwd:
            detail += "  (cwd %s)" % s.cwd
        if s.env:
            detail += "  [env: %s]" % ", ".join(sorted(s.env))
    else:
        detail = s.url
        if s.headers:
            detail += "  [headers: %s]" % ", ".join(sorted(s.headers))
    legacy = "  (legacy transport)" if s.legacy else ""
    return "%-20s %-16s %s%s" % (s.name, s.type, detail, legacy)


def _print_diagnostics(diags, prefix="", include_info=True):
    """Render diagnostics.

    ``include_info`` is False for the project listing: informational notes there
    are dominated by "this unrelated plugin.json is not an Agent Plugins
    manifest", which is precisely the noise auto-probe is meant to avoid.  An
    author who wants to see them asks for a specific plugin, or uses --check.
    """
    if not include_info:
        diags = [d for d in diags if d.severity != "info"]
    if not diags:
        return
    print("")
    for d in diags:
        print("%s%-8s %-28s %s" % (prefix, d.severity, d.code, d.message))


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------

def _run_check(path, as_json):
    """Validate one plugin path. Exit status 1 when anything needs fixing."""
    from ..agent_plugins import discovery

    plugin, diags = discovery.load_from_reference(path, owner_name="check", kind="check")
    problems = [d for d in diags if d.severity in ("error", "warning")]

    if as_json:
        print(json.dumps({
            "path": path,
            "valid": plugin is not None and not problems,
            "plugin": _as_dict(plugin) if plugin is not None else None,
            "diagnostics": [dataclasses.asdict(d) for d in diags],
        }, indent=2))
    else:
        if plugin is not None:
            print("%s: %s manifest for '%s'"
                  % (path, _format_label(plugin.manifest), plugin.name))
            print("  skills:      %d" % len(plugin.skill_dirs))
            print("  mcp servers: %d" % (len(plugin.mcp.servers) if plugin.mcp else 0))
            for kind, where in plugin.components.items():
                if kind != "mcp":
                    print("  %-12s %s" % (kind + ":", ", ".join(where)))
        else:
            print("%s: not a usable plugin" % path)
        for d in diags:
            print("  %-8s %-28s %s" % (d.severity, d.code, d.message))
        if plugin is not None and not problems:
            print("  OK")

    sys.exit(1 if (plugin is None or problems) else 0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _fetch_marketplace(src, marketplace_file, workdir):
    """Load a marketplace's catalog, fetching as little as possible.

    A git marketplace is shallow-cloned into ``workdir``; a catalog URL is
    downloaded; a local marketplace is read in place.  Nothing is written to
    the workspace.
    """
    import subprocess
    from ..agent_plugins import marketplace as mk
    from ..pkg_types.package_marketplace import _classify_url

    kind, url, catalog = _classify_url(src, None)
    marketplace_file = marketplace_file or catalog

    if kind == "url":
        import httpx
        r = httpx.get(url, follow_redirects=True, timeout=30)
        if r.status_code < 200 or r.status_code >= 300:
            raise RuntimeError("cannot fetch %s: HTTP %d" % (url, r.status_code))
        try:
            data = json.loads(r.content)
        except ValueError as exc:
            raise RuntimeError("%s is not JSON: %s" % (url, exc))
        return mk.parse(data, url, None)

    if kind == "git":
        root = os.path.join(workdir, "marketplace")
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        proc = subprocess.run(["git", "clone", "-q", "--depth", "1", url, root],
                              capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise RuntimeError("cannot clone %s: %s" % (url, proc.stderr.strip()))
    else:
        root = url[len("file://"):]

    path = mk.find_catalog(root, marketplace_file)
    if path is None:
        raise RuntimeError("no marketplace catalog (%s) in %s" % (
            marketplace_file or " or ".join(mk.CATALOG_LOCATIONS), src))
    return mk.load(path, root)


def _entry_location(entry):
    src = entry.source
    if entry.source_kind == "relative":
        return "./" + src["path"]
    if entry.source_kind == "git-subdir":
        return "%s  %s" % (src["url"], src["path"])
    if "url" in src:
        pin = src.get("sha") or src.get("ref")
        return src["url"] + ("@" + pin if pin else "")
    return src.get("package") or src.get("source") or ""


def _run_from(src, marketplace_file, as_json):
    import tempfile
    workdir = tempfile.mkdtemp(prefix="ivpm-marketplace-")
    try:
        try:
            mkt = _fetch_marketplace(src, marketplace_file, workdir)
        except RuntimeError as exc:
            print("ivpm show plugins: %s" % exc, file=sys.stderr)
            sys.exit(1)
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)

    if as_json:
        print(json.dumps({
            "marketplace": mkt.name,
            "description": mkt.description,
            "plugins": [{
                "name": e.name,
                "description": e.description,
                "version": e.version,
                "category": e.category,
                "source_kind": e.source_kind,
                "location": _entry_location(e),
                "installable": e.supported,
            } for e in mkt.entries],
            "diagnostics": [dataclasses.asdict(d) for d in mkt.diagnostics],
        }, indent=2))
    else:
        if mkt.name:
            print("Marketplace: %s%s" % (mkt.name,
                                         " -- " + mkt.description if mkt.description else ""))
            print("")
        for e in mkt.entries:
            flag = "" if e.supported else "  (not installable: %s source)" % e.source_kind
            print("%-24s %-10s %s%s" % (e.name, e.source_kind, _entry_location(e), flag))
            if e.description:
                print("%-24s %s" % ("", e.description))
        _print_diagnostics(list(mkt.diagnostics))
    if any(d.severity == "error" for d in mkt.diagnostics):
        sys.exit(1)


class ShowPlugins:
    def __call__(self, args):
        name = getattr(args, "name", None)
        as_json = getattr(args, "json", False)
        no_rich = getattr(args, "no_rich", False)
        check = getattr(args, "check", None)
        mcp_only = getattr(args, "mcp", False)
        from_mkt = getattr(args, "from_marketplace", None)

        if check:
            _run_check(check, as_json)
            return

        if from_mkt:
            _run_from(from_mkt, getattr(args, "marketplace_file", None), as_json)
            return

        project_dir = getattr(args, "project_dir", None) or os.getcwd()
        try:
            plugins, diags, agents_cfg = _collect(project_dir, getattr(args, "dep_set", None))
        except FileNotFoundError as exc:
            print("ivpm show plugins: %s" % exc, file=sys.stderr)
            sys.exit(1)

        plugins.sort(key=lambda p: (p.name, p.owner_name))
        gates = _gates(agents_cfg)

        if name:
            matches = [p for p in plugins if p.name == name]
            if not matches:
                print("ivpm show plugins: no plugin named '%s'" % name, file=sys.stderr)
                sys.exit(1)
            plugins = matches

        if mcp_only:
            self._render_mcp(plugins, as_json, no_rich)
            return

        if as_json:
            print(json.dumps({
                "plugins": [_as_dict(p, gates) for p in plugins],
                "diagnostics": [dataclasses.asdict(d) for d in diags],
            }, indent=2))
            return

        plain = no_rich or not sys.stdout.isatty()

        if name:
            (_plain_detail if plain else _rich_detail)(plugins[0], gates)
            return

        if not plugins:
            print("No plugins found in this project.")
        elif plain:
            _plain_list(plugins)
        else:
            _rich_list(plugins)
        _print_diagnostics(diags, include_info=False)

    def _render_mcp(self, plugins, as_json, no_rich):
        rows = [(p, s) for p in plugins for s in (p.mcp.servers if p.mcp else ())]

        if as_json:
            print(json.dumps({"servers": [
                dict(_server_as_dict(s), plugin=p.name,
                     qualified_name="%s.%s" % (p.name, s.name),
                     plugin_root=p.root_dir)
                for p, s in rows]}, indent=2))
            return

        if not rows:
            print("No MCP servers are declared by the plugins in this project.")
            return

        print("MCP servers declared by this project's plugins.")
        print("Environment variable values are not shown.\n")
        for p, s in rows:
            print("  %s.%s" % (p.name, _server_line(s)))
        print("\nAggregation is opt-in: set 'mcp: true' under package.with.agents "
              "in ivpm.yaml to wire these up.")
