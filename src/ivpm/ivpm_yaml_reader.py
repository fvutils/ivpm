'''
Created on Jun 8, 2021

@author: mballance
'''
import difflib
import os
from typing import Dict, List
from .yamlsrc import SrcInfo, SrcLoaderError, load as yaml_load
from .package import Package
from .env_spec import EnvSpec

from .utils import fatal, getlocstr, warning
from .variables import resolve_variables
from ivpm.package import Package, PackageType, SourceType
from ivpm.packages_info import PackagesInfo
from ivpm.pkg_content_type import parse_type_field
from ivpm.dep_mode import parse_deps_mode

# Valid keys at the ``package:`` level in ivpm.yaml.
_KNOWN_PACKAGE_KEYS = {
    "name", "description", "version", "type", "with",
    "deps-dir", "deps-mode", "default-dep-set",
    "dep-sets", "setup-deps",
    "paths", "env",
    "vars", "include",
    # old-style keys – detected and rejected with a friendlier message
    "deps", "dev-deps",
}

# Valid keys inside ``package.with.python:``.
_KNOWN_PYTHON_WITH_KEYS = {"venv", "system-site-packages", "pre-release"}

# Valid keys inside ``package.with.node:``.
_KNOWN_NODE_WITH_KEYS = {"manager", "version", "env", "link-root"}

# Keys valid inside ``with:`` that are parsed by the core reader rather than
# dispatched to a handler. ``env`` is *reserved*, not handler-backed: there is
# no PackageHandlerEnv, since a no-op handler registered purely to satisfy key
# validation would show up in 'ivpm show handler' as something configurable.
_RESERVED_WITH_KEYS = {"env"}

def _keyloc(m, key, fallback=None):
    """Return a location anchor for *key* within mapping *m*.

    A ``None`` or scalar YAML value may carry no ``.srcinfo`` of its own (the
    loader only annotates maps, seqs, and non-null scalars), so a diagnostic
    about such a value has to be anchored elsewhere. Mapping keys *are*
    annotated, so the key itself is the most precise anchor available.
    Falls back to *fallback*, then to the enclosing mapping.
    """
    if isinstance(m, dict):
        for k in m.keys():
            if k == key and hasattr(k, "srcinfo"):
                return k
    if fallback is not None and hasattr(fallback, "srcinfo"):
        return fallback
    return m


def _suggest(unknown: str, valid) -> str:
    """Return a hint string when *unknown* is close to a known key, or ''."""
    matches = difflib.get_close_matches(unknown, valid, n=1, cutoff=0.6)
    return (" Did you mean '%s'?" % matches[0]) if matches else ""


def parse_env_directive(evar, out: List['EnvSpec']):
    """Parse a single ``env:`` directive mapping and append its EnvSpec to *out*.

    Shared by the ``with.env`` clause and the deprecated top-level ``env:``
    spelling, so both produce identical EnvSpec lists.
    """
    if not isinstance(evar, dict):
        fatal("Environment directive must be a mapping with a 'name' key: %s"
              % str(evar), evar)
    if "name" not in evar.keys():
        fatal("No variable-name specified: %s" % str(evar), evar)
    act = None
    act_s = None
    val = None
    for an,av in [
        ("value", EnvSpec.Act.Set),
        ("path", EnvSpec.Act.Path),
        ("path-append", EnvSpec.Act.PathAppend),
        ("path-prepend", EnvSpec.Act.PathPrepend)]:
        if an in evar.keys():
            if act is not None:
                fatal("Multiple variable-setting directives specified: %s and %s" % (
                    act_s, an), evar)
            act_s = an
            act = av
            val = evar[an]

    if act is None:
        fatal(
            "No variable-directive setting (value, path, path-append, path-prepend) specified",
            evar)
    out.append(EnvSpec(evar["name"], val, act))


def parse_with_section(with_data: dict, name: str, scope: str = "package"):
    """Parse a ``with:`` map into handler configurations.

    Pure function: validates keys/values (raising located ``fatal()`` on error)
    and returns a ``(python_config, node_config, handler_configs, env_settings)``
    bundle, where *python_config*/*node_config* are ``None`` when the
    corresponding ``python``/``node`` block is absent, *handler_configs* is a
    dict of the remaining (plugin) handler keys, and *env_settings* is the list
    of :class:`EnvSpec` parsed from ``with.env`` (empty when absent). *name* is
    the manifest filename and *scope* is the dotted-prefix scope label
    (``package`` or ``dep-set '<name>'``) used in error messages. Applicable to
    both the package-level and a dep-set-level ``with:`` block.
    """
    from .proj_info import VenvMode, PythonConfig, NodeConfig
    from .handlers.package_handler_rgy import PackageHandlerRgy

    python_config = None
    node_config = None
    handler_configs = {}
    env_settings = []

    if with_data is None:
        return python_config, node_config, handler_configs, env_settings

    # Build the set of valid keys dynamically from the extension registries so
    # that plugin handlers (e.g. direnv) are accepted without
    # hardcoding their names here. Package preparers read their settings from
    # the same 'with:' block, so their names are valid keys too.
    from .prepare import PackagePreparerRgy

    rgy = PackageHandlerRgy.inst()
    known_with_keys = {h.name for h in rgy.handlers if h.name} | _RESERVED_WITH_KEYS
    known_with_keys |= {n for n in PackagePreparerRgy.inst().names() if n}

    for key in with_data.keys():
        if key not in known_with_keys:
            hint = _suggest(key, known_with_keys)
            fatal("Unknown key '%s' in %s.with in %s.%s Valid keys: %s" % (
                key, scope, name, hint,
                ", ".join(sorted(known_with_keys))), key)

    if "python" in with_data.keys():
        py_data = with_data["python"]
        if py_data is None:
            py_data = {}
        cfg = PythonConfig()

        for key in py_data.keys():
            if key not in _KNOWN_PYTHON_WITH_KEYS:
                hint = _suggest(key, _KNOWN_PYTHON_WITH_KEYS)
                fatal(
                    "Unknown key '%s' in %s.with.python in %s.%s"
                    " Valid keys: %s" % (
                        key, scope, name, hint,
                        ", ".join(sorted(_KNOWN_PYTHON_WITH_KEYS))),
                    key)

        if "venv" in py_data:
            try:
                cfg.venv = VenvMode.parse(py_data["venv"])
            except ValueError as e:
                fatal(str(e) + " in %s.with.python in %s" % (scope, name),
                      py_data.get("venv"))
        if "system-site-packages" in py_data:
            cfg.system_site_packages = bool(py_data["system-site-packages"])
        if "pre-release" in py_data:
            cfg.pre_release = bool(py_data["pre-release"])
        python_config = cfg

    if "node" in with_data.keys():
        nd_data = with_data["node"]
        if nd_data is None:
            nd_data = {}
        cfg = NodeConfig()

        for key in nd_data.keys():
            if key not in _KNOWN_NODE_WITH_KEYS:
                hint = _suggest(key, _KNOWN_NODE_WITH_KEYS)
                fatal(
                    "Unknown key '%s' in %s.with.node in %s.%s"
                    " Valid keys: %s" % (
                        key, scope, name, hint,
                        ", ".join(sorted(_KNOWN_NODE_WITH_KEYS))),
                    key)

        if "manager" in nd_data:
            val = str(nd_data["manager"]).strip().lower()
            if val not in {"npm", "pnpm", "yarn"}:
                fatal("Invalid manager '%s' in %s.with.node in %s."
                      " Valid values: npm, pnpm, yarn" % (val, scope, name),
                      nd_data.get("manager"))
            cfg.manager = val
        if "version" in nd_data:
            cfg.version = str(nd_data["version"])
        if "env" in nd_data:
            cfg.env = bool(nd_data["env"])
        if "link-root" in nd_data:
            cfg.link_root = bool(nd_data["link-root"])
        node_config = cfg

    if "env" in with_data.keys():
        ev_data = with_data["env"]
        if ev_data is None:
            ev_data = []
        if not isinstance(ev_data, list):
            fatal("'%s.with.env' must be a list of environment directives in %s"
                  % (scope, name), ev_data)
        for evar in ev_data:
            parse_env_directive(evar, env_settings)

    # Config for non-python/non-node registered handlers so they can
    # retrieve their settings via ProjectUpdateInfo.handler_configs.
    # Reserved (core-parsed) keys are excluded: they are returned as typed
    # values, not passed through to a handler.
    for key, value in with_data.items():
        if key not in ("python", "node") and key not in _RESERVED_WITH_KEYS:
            handler_configs[key] = value

    return python_config, node_config, handler_configs, env_settings


def resolve_effective_with(proj_info, ds):
    """Compute the effective handler config for installing dep-set *ds*.

    Returns a ``(python_config, node_config, handler_configs, env_settings)``
    tuple where the package-level ``with:`` (already parsed onto *proj_info*) is
    overlaid by the selected dep-set's own ``with:`` (dep-set wins per key).
    When the dep-set declares no ``with:``, the package-level parsed configs are
    returned verbatim.

    Any clone-provided handler overlay already merged into
    ``proj_info.handler_configs`` is preserved: the dep-set's handler keys
    override per handler, but handlers the dep-set does not mention keep their
    (possibly overlay-augmented) package-level value.

    ``env_settings`` is the *concatenation* of the package-level and dep-set
    directives (package first) -- :func:`merge_with` performs the concatenation
    on the raw block, so re-parsing the merged block yields the full ordered
    list. A dep-set can add to or override individual variables, never clear
    the inherited set.
    """
    py_config = proj_info.python_config
    node_config = proj_info.node_config
    handler_configs = proj_info.handler_configs
    env_settings = proj_info.env_settings

    ds_with = getattr(ds, "with_raw", None)
    if ds_with:
        effective_raw = merge_with(getattr(proj_info, "with_raw", None) or {},
                                   ds_with)
        py_config, node_config, ds_handler_configs, env_settings = \
            parse_with_section(
                effective_raw, proj_info.name, "dep-set '%s'" % ds.name)
        handler_configs = dict(proj_info.handler_configs)
        handler_configs.update(ds_handler_configs)

    return py_config, node_config, handler_configs, env_settings


def merge_with(base: dict, over: dict, _top: bool = True) -> dict:
    """Deep-merge two raw ``with:`` dicts, returning a NEW dict in which *over*
    wins on conflict.

    Nested maps are merged recursively; lists and scalars are replaced by
    *over*. Neither input is mutated and no value node from either input is
    shared into the result at the map level (leaf values are referenced as-is,
    preserving their ``.srcinfo``), so a base ``with:`` reused by several
    dep-sets is never modified in place.

    The top-level ``env`` key is the one exception: it **concatenates**
    (*base* first, *over* last) rather than replacing. Environment directives
    are additive by design -- emission order is precedence for ``value:``/
    ``path:`` (bash's last export wins), and ``path-prepend``/``path-append``
    must accumulate rather than be discarded. ``_top`` guards this to the
    outermost level so a nested handler key named ``env`` -- notably
    ``with.node.env``, a boolean -- keeps plain replace semantics.
    """
    if not base:
        return dict(over) if over else {}
    if not over:
        return dict(base)

    result = dict(base)
    for key, oval in over.items():
        bval = result.get(key)
        if _top and key == "env" and isinstance(bval, list) and isinstance(oval, list):
            # New list: never extend a caller's list in place.
            result[key] = list(bval) + list(oval)
        elif isinstance(bval, dict) and isinstance(oval, dict):
            result[key] = merge_with(bval, oval, _top=False)
        else:
            result[key] = oval
    return result


class IvpmYamlReader(object):
    
    def __init__(self):
        self.debug = False
        pass
    
    def read(self, fp, name, cli_overrides=None, persisted_vars=None,
             allow_include=True, is_root=False) -> 'ProjInfo':
        """Read a manifest into a ProjInfo.

        *is_root* is True only when *name* is the manifest of the project the
        user is operating on. Deprecation diagnostics are emitted only for the
        root manifest: a dependency's ivpm.yaml is not the user's to edit, so
        warning about it would be unactionable noise.
        """
        from ivpm.proj_info import ProjInfo

        ret = ProjInfo(is_src=True)

        # File I/O streams have a name field that is read-only.
        # Add a name field to non-I/O streams
        if not hasattr(fp, "name"):
            fp.name = name

        # Load the ``package:`` body, recursively merging any ``include:``
        # files first. Variables are resolved once, post-merge (so an include
        # may reference variables defined by the includer). ``allow_include`` is
        # False for remote (URL) manifests, whose includes cannot be resolved
        # against a local directory.
        pkg = self._load_merged_pkg(fp, name, allow_include=allow_include)

        # Resolve ${{var}} references before any other processing
        pkg, resolved_vars = resolve_variables(
            pkg, cli_overrides or {}, persisted_vars or {})

        if "name" not in pkg.keys():
            fatal("Missing 'name' key in package (file %s)" % name, pkg)

        for key in pkg.keys():
            if key not in _KNOWN_PACKAGE_KEYS:
                hint = _suggest(key, _KNOWN_PACKAGE_KEYS)
                fatal(
                    "Unknown tag '%s' at package level in %s.%s"
                    " Valid tags: %s" % (
                        key, name, hint,
                        ", ".join(sorted(_KNOWN_PACKAGE_KEYS))),
                    key)

        ret.name = pkg["name"]
        ret.resolved_vars = resolved_vars
        if "description" in pkg.keys():
            ret.description = pkg["description"]
        if "version" in pkg.keys():
            ret.version = pkg["version"]
        else:
            ret.version = None

        if "type" in pkg.keys():
            ret.self_types = parse_type_field(pkg["type"])

        if "with" in pkg.keys():
            # Retain the raw block so a selected dep-set's own 'with:' can be
            # merged onto it at update time.
            ret.with_raw = pkg["with"]

        # Top-level 'env:' is a deprecated spelling of 'with: { env: [...] }'.
        # Fold it into the raw with-block so there is exactly one downstream
        # path. This MUST run before _read_with_section() below, which is what
        # populates ret.env_settings.
        if "env" in pkg.keys():
            self._fold_toplevel_env(ret, pkg["env"], name, is_root)

        if ret.with_raw is not None:
            self._read_with_section(ret, ret.with_raw, name)

        # Specify where sub-packages are stored. Defaults to 'packages'        
        if "deps-dir" in pkg.keys():
            ret.deps_dir = pkg["deps-dir"]

        # How dependencies resolved beneath this package are placed. None means
        # "not declared" -- the enclosing scope's mode is inherited.
        # Located against the enclosing node: variable substitution rebuilds
        # scalar strings, so the value itself no longer carries srcinfo.
        if "deps-mode" in pkg.keys():
            ret.deps_mode = parse_deps_mode(pkg["deps-mode"], pkg)

        if "default-dep-set" in pkg.keys():
            ret.default_dep_set = pkg["default-dep-set"]
        
        if "deps" in pkg.keys() or "dev-deps" in pkg.keys():
            # old-style format
            fatal("Package %s uses old-style ivpm.yaml format" % ret.name)
        elif "dep-sets" in pkg.keys():
            # new-style format
            self.read_dep_sets(ret, pkg["dep-sets"], name)
        else:
            # no dependencies at all
            warning("no dependencies")
        
        if "setup-deps" in pkg.keys():
            for sd in pkg["setup-deps"]:
                ret.setup_deps.add(sd)

        if "paths" in pkg.keys():
            ps = pkg["paths"]
            for ps_kind in ps.keys():
                self.read_path_set(
                    ret,
                    os.path.dirname(name),
                    ps_kind,
                    ps[ps_kind])
        
        return ret

    def _fold_toplevel_env(self, info: 'ProjInfo', env_data, name: str,
                           is_root: bool):
        """Fold the deprecated top-level ``env:`` list into ``info.with_raw``.

        Emits a single deprecation warning per manifest (root only, located at
        the offending block) and prepends the top-level directives to any
        ``with.env`` declared in the same file, so the more specific spelling
        wins a same-variable conflict within one file.
        """
        if is_root:
            warning(
                "top-level 'env:' is deprecated; move these directives under "
                "'with: { env: [...] }'. Top-level 'env:' will be removed in "
                "a future release.",
                env_data)

        if env_data is None:
            env_data = []
        if not isinstance(env_data, list):
            fatal("'env' must be a list of environment directives in %s" % name,
                  env_data)

        with_raw = dict(info.with_raw) if info.with_raw else {}
        with_env = with_raw.get("env") or []
        if not isinstance(with_env, list):
            fatal("'package.with.env' must be a list of environment directives"
                  " in %s" % name, with_env)
        with_raw["env"] = list(env_data) + list(with_env)
        info.with_raw = with_raw

    def _load_merged_pkg(self, fp, name, _visited=None, allow_include=True):
        """Load ``package:`` from *name*, recursively merging any ``include:``
        files into it. Returns the merged package dict (variables NOT yet
        resolved). Cross-file nodes retain their original ``.srcinfo`` because
        the merge keeps the original value objects rather than copying them.

        *_visited* is the set of canonical paths on the current include chain
        (ancestors of *name*), used to detect cyclic includes. A copy is passed
        down each branch, so a file reached by two independent paths (a diamond)
        is permitted; only a true cycle is fatal.

        *allow_include* is False for remote (URL) manifests: ``include:`` is
        resolved against the local filesystem, which is meaningless for a file
        fetched from a URL, so it is rejected with a clear message instead.
        """
        if _visited is None:
            _visited = set()
        _visited = set(_visited)
        _visited.add(os.path.realpath(name))

        try:
            data = yaml_load(fp, name=name)
        except SrcLoaderError as e:
            # Route YAML parse errors through the diagnostic reporter
            # so they are rendered (plain or TUI) before propagating.
            # Extract the raw problem text to avoid duplicating the
            # location prefix that fatal() adds from e.srcinfo.
            problem = e.message
            si = e.srcinfo
            if si is not None:
                loc_prefix = str(si) + ": "
                if problem.startswith(loc_prefix):
                    problem = problem[len(loc_prefix):]
            fatal(problem, si)

        if data is None:
            fatal("Empty ivpm.yaml file %s" % name)
        if "package" not in data.keys():
            fatal("Missing 'package' section in ivpm.yaml file %s" % name, data)
        pkg = data["package"]

        if "include" in pkg.keys() and not allow_include:
            fatal("Remote manifests may not use 'include:' (in %s); inline the "
                  "contents or host a single self-contained manifest" % name,
                  pkg["include"])

        if "include" in pkg.keys():
            includes = pkg["include"]
            if not isinstance(includes, list):
                fatal("'include' must be a list of file paths in %s" % name,
                      includes)
            base_dir = os.path.dirname(name)
            for inc in includes:
                if not isinstance(inc, str):
                    fatal("'include' entries must be file-path strings in %s"
                          % name, inc)
                inc_path = inc if os.path.isabs(inc) \
                    else os.path.join(base_dir, inc)
                if os.path.realpath(inc_path) in _visited:
                    fatal("Cyclic include detected: %s includes %s, which is "
                          "already on the include chain" % (name, inc_path),
                          inc)
                if not os.path.isfile(inc_path):
                    fatal("Include file '%s' (referenced from %s) does not exist"
                          % (inc_path, name), inc)
                with open(inc_path) as inc_fp:
                    inc_pkg = self._load_merged_pkg(inc_fp, inc_path, _visited)
                self._merge_pkg(pkg, inc_pkg, base=name, incl_path=inc_path)
            # 'include' is consumed by the merge; it is not a ProjInfo field.
            del pkg["include"]

        return pkg

    # Keys handled explicitly by ``_merge_pkg``; everything else falls through
    # to the generic "adopt-if-absent, local wins" rule.
    _MERGE_HANDLED_KEYS = {
        "name", "version", "dep-sets",
        "with", "vars", "paths",
        "env", "setup-deps",
    }

    def _merge_pkg(self, local, incl, base, incl_path):
        """Fold the included ``package:`` dict *incl* into the includer *local*,
        in place. The includer (*local*) always wins on conflict. Node objects
        from *incl* are adopted by reference so their ``.srcinfo`` is preserved.

        Implements the design's merge rules:
          - ``name``/``version`` may not be set by an include (identity is
            anchored to the root) -> fatal.
          - ``dep-sets`` merge by name; a name defined in both files is fatal
            (no deps concatenation across files).
          - ``with``/``vars`` deep-merge (local wins on scalar/list conflict).
          - ``paths`` deep-merge as a map; leaf lists append.
          - ``env``/``setup-deps`` list-append.
          - everything else (``type``, ``deps-dir``, ...): adopt if absent,
            otherwise local wins.
        """
        # Identity may only come from the root file.
        for ident in ("name", "version"):
            if ident in incl.keys():
                fatal("Include '%s' may not set '%s' @ %s ; package identity is "
                      "anchored to the root file %s" % (
                          incl_path, ident, getlocstr(incl[ident]), base),
                      incl[ident])

        # dep-sets: merge by name, duplicate across files is fatal.
        if "dep-sets" in incl.keys():
            if "dep-sets" not in local.keys():
                local["dep-sets"] = incl["dep-sets"]
            else:
                local_ds = local["dep-sets"]
                existing = {}
                for ds in local_ds:
                    if isinstance(ds, dict) and "name" in ds.keys():
                        existing[str(ds["name"])] = ds
                for ds in incl["dep-sets"]:
                    if isinstance(ds, dict) and "name" in ds.keys():
                        nm = str(ds["name"])
                        if nm in existing:
                            fatal("Duplicate dep-set '%s' @ %s ; previously "
                                  "defined @ %s" % (
                                      nm, getlocstr(ds),
                                      getlocstr(existing[nm])))
                        existing[nm] = ds
                    local_ds.append(ds)

        # with/vars: deep-merge maps, local wins (lists also local-wins).
        # Exception: 'with.env' list-appends (include after local), matching
        # the top-level 'env:' rule below. Environment directives are additive
        # in both spellings, so an include's env: is never discarded.
        for k in ("with", "vars"):
            if k in incl.keys():
                if k not in local.keys():
                    local[k] = incl[k]
                elif isinstance(local[k], dict) and isinstance(incl[k], dict):
                    incl_env = incl[k].get("env") if k == "with" else None
                    self._deep_merge_map(local[k], incl[k], append_lists=False)
                    if k == "with" and isinstance(incl_env, list):
                        local_env = local[k].get("env")
                        # Identity check: when local had no 'env', the merge
                        # adopted the include's list outright -- nothing to do.
                        if isinstance(local_env, list) and local_env is not incl_env:
                            local[k]["env"] = list(local_env) + list(incl_env)
                # else: local wins, keep as-is

        # paths: deep-merge map, leaf lists append.
        if "paths" in incl.keys():
            if "paths" not in local.keys():
                local["paths"] = incl["paths"]
            elif isinstance(local["paths"], dict) \
                    and isinstance(incl["paths"], dict):
                self._deep_merge_map(
                    local["paths"], incl["paths"], append_lists=True)

        # env/setup-deps: top-level list append (include after local).
        for k in ("env", "setup-deps"):
            if k in incl.keys():
                if k not in local.keys():
                    local[k] = incl[k]
                elif isinstance(local[k], list) and isinstance(incl[k], list):
                    local[k].extend(incl[k])
                # else: local wins

        # Everything else (type, deps-dir, default-dep-set, ...): local wins,
        # adopt only if absent locally.
        for k in incl.keys():
            if k in self._MERGE_HANDLED_KEYS:
                continue
            if k not in local.keys():
                local[k] = incl[k]

    def _deep_merge_map(self, local, incl, append_lists):
        """Recursively merge map *incl* into map *local*, in place. *local*
        wins on scalar conflict. Nested maps recurse. List values concatenate
        (local first) when *append_lists*, otherwise *local* wins. Adopted
        values keep their original node identity / ``.srcinfo``."""
        for k in incl.keys():
            if k not in local.keys():
                local[k] = incl[k]
            else:
                lv, iv = local[k], incl[k]
                if isinstance(lv, dict) and isinstance(iv, dict):
                    self._deep_merge_map(lv, iv, append_lists)
                elif append_lists and isinstance(lv, list) \
                        and isinstance(iv, list):
                    lv.extend(iv)
                # else: scalar (or list when not appending) -> local wins

    def _read_with_section(self, info: 'ProjInfo', with_data: dict, name: str):
        """Parse the package-level ``with:`` map onto *info*.

        Thin wrapper over :func:`parse_with_section`; retained so the
        package-level call site is unchanged.
        """
        py_cfg, node_cfg, handler_cfgs, env_settings = parse_with_section(
            with_data, name, "package")
        if py_cfg is not None:
            info.python_config = py_cfg
        if node_cfg is not None:
            info.node_config = node_cfg
        for key, value in handler_cfgs.items():
            info.handler_configs[key] = value
        # Sole writer of env_settings: both the 'with.env' clause and the
        # deprecated top-level 'env:' (folded into with_raw) arrive here.
        info.env_settings = env_settings

    def read_dep_sets(self, info : 'ProjInfo', dep_sets, name : str = "<unknown>"):
        if not isinstance(dep_sets, list):
            fatal("Expect body of dep-sets to be a list, not %s" % str(type(dep_sets)),
                  dep_sets)

        seen_ds = {}
        for ds_ent in dep_sets:
            if not isinstance(ds_ent, dict):
                fatal("Dependency set is not a dict", ds_ent)
            if "name" not in ds_ent.keys():
                fatal("No name associated with dependency set", ds_ent)
            if "deps" not in ds_ent.keys() and "uses" not in ds_ent.keys():
                fatal("Dependency set must have a 'deps' or 'uses' entry", ds_ent)

            ds_name = ds_ent["name"]
            if str(ds_name) in seen_ds:
                fatal("Duplicate dep-set '%s' @ %s ; previously defined @ %s" % (
                    ds_name, getlocstr(ds_ent), getlocstr(seen_ds[str(ds_name)])))
            seen_ds[str(ds_name)] = ds_ent
            ds = PackagesInfo(ds_name)
            default_dep_set = None

            if "description" in ds_ent.keys():
                ds.description = ds_ent["description"]

            if "kind" in ds_ent.keys():
                # Optional explicit classification ("package" | "collection").
                # Absent -> inferred downstream from dep count / name / 'uses'.
                ds.kind = str(ds_ent["kind"])

            if "uses" in ds_ent.keys():
                # 'uses' may name a single base dep-set or a list of them.
                uses = ds_ent["uses"]
                if isinstance(uses, (list, tuple)):
                    ds.uses = [str(u) for u in uses]
                else:
                    ds.uses = [str(uses)]

            if "with" in ds_ent.keys():
                # Per-dep-set handler configuration. Stored raw and merged with
                # the package-level 'with:' when this dep-set is the selected
                # install target. Validate now so key/value errors are located.
                ds.with_raw = ds_ent["with"]
                # Result discarded: the effective config (including 'env') is
                # recomputed from the merged raw block at install time by
                # resolve_effective_with(). This call is for validation only.
                parse_with_section(
                    ds_ent["with"], name, "dep-set '%s'" % ds_name)

            if "deps-mode" in ds_ent.keys():
                # Applies to the dependencies of this dep-set's packages.
                ds.deps_mode = parse_deps_mode(ds_ent["deps-mode"], ds_ent)

            if "default-dep-set" in ds_ent.keys():
                default_dep_set = ds_ent["default-dep-set"]

            # 'deps' is optional for 'uses'-only (compound) dep-sets, which
            # inherit all their packages from one or more base dep-sets.
            deps = ds_ent.get("deps", [])

            if not isinstance(deps, list):
                # Covers 'deps:' with no value at all, which parses to None
                # and carries no srcinfo -- anchor on the 'deps' key instead.
                fatal("deps is not a list (got %s)" % type(deps).__name__,
                      _keyloc(ds_ent, "deps", deps))
            self.read_deps(ds, deps, default_dep_set)
            info.set_dep_set(ds.name, ds)

        self._resolve_dep_set_inheritance(info)

    def _resolve_dep_set_inheritance(self, info: 'ProjInfo'):
        """
        Merge inherited packages for every dep-set that declares one or more
        'uses' bases. Bases are merged left-to-right (a later base overrides an
        earlier one), then the current dep-set's own packages win on name
        collision. Detects cycles and unknown base names.
        """
        dep_set_m = info.dep_set_m
        resolved = set()

        def resolve(name, visiting):
            if name in resolved:
                return
            ds = dep_set_m[name]
            if not ds.uses:
                resolved.add(name)
                return
            if name in visiting:
                cycle = " -> ".join(list(visiting) + [name])
                fatal("Cyclic dep-set inheritance detected: %s" % cycle)

            visiting.add(name)
            merged_pkgs = {}
            merged_opts = {}
            merged_with = {}
            for base_name in ds.uses:
                if base_name not in dep_set_m:
                    fatal(
                        "dep-set '%s' references unknown base dep-set '%s'"
                        % (name, base_name))
                resolve(base_name, visiting)
                base_ds = dep_set_m[base_name]
                # Accumulate bases in declared order; later bases win.
                merged_pkgs.update(base_ds.packages)
                merged_opts.update(base_ds.options)
                if base_ds.with_raw:
                    merged_with = merge_with(merged_with, base_ds.with_raw)
            visiting.discard(name)

            # Finally, the current dep-set's own entries override the bases.
            merged_pkgs.update(ds.packages)
            ds.packages = merged_pkgs
            merged_opts.update(ds.options)
            ds.options = merged_opts
            # The dep-set's own 'with:' overlays the merged bases (own wins).
            if ds.with_raw:
                merged_with = merge_with(merged_with, ds.with_raw)
            ds.with_raw = merged_with or None

            resolved.add(name)

        for ds_name in list(dep_set_m.keys()):
            resolve(ds_name, set())
        

    def read_deps(self, ret : PackagesInfo, deps, default_dep_set):
        from .pkg_types.pkg_type_rgy import PkgTypeRgy
        from .pkg_content_type_rgy import PkgContentTypeRgy
        
        for d in deps:
            if not isinstance(d, dict):
                # An empty list item ('- ' with nothing after it) parses to
                # None, and a bare scalar is a common mistake for what must be
                # a mapping. Neither carries usable srcinfo, so anchor the
                # diagnostic on the enclosing 'deps' sequence.
                fatal("Dependency entry must be a mapping with a 'name' key, "
                      "not %s" % type(d).__name__, deps)
            si = d.srcinfo

            if "name" not in d.keys():
                fatal("Missing 'name' key in dependency", si)

            if d["name"] in ret.keys():
                pkg1 = ret[d["name"]]
                fatal("Duplicate package %s @ %s ; previously speciifed @ %s" % (
                    d["name"], getlocstr(d), getlocstr(pkg1)))

            url = d["url"] if "url" in d.keys() else None

            # Determine the source of this package:
            # - Git
            # - http
            # ...

            src = "<unknown>"
            if "src" in d.keys():
                src = d["src"]
            elif "pypi" in d.keys() and d["pypi"]:
                src = "pypi"
            else:
                # Auto-probing the package based on the URL. The user can always
                # specify the source explicitly
                if url is None:
                    fatal("no src specified for package %s and no URL specified" % d["name"], d)

                if url.endswith(".git"):
                    src = "git"                
                elif url.startswith("http://") or url.startswith("https://"):
                    src = "http"
                elif url.startswith("file://"):
                    src = "file"
                else:
                    pt_rgy = PkgTypeRgy.inst()
                    fatal(
                        "Package '%s': cannot determine source type from url '%s' @ %s\n"
                        "  Please specify 'src' as one of: %s" % (
                            d["name"], url, getlocstr(d),
                            ", ".join(pt_rgy.getSrcTypes())), d)

            pt_rgy = PkgTypeRgy.inst()
            
            if not pt_rgy.hasPkgType(src):
                fatal(
                    "Package '%s': unknown source type '%s' @ %s\n"
                    "  Known types: %s" % (
                        d["name"], src, getlocstr(d),
                        ", ".join(pt_rgy.getSrcTypes())), d)
            pkg = PkgTypeRgy.inst().mkPackage(src, str(d["name"]), d, si)

            # Validate keys against the *source-specific* accepted set. Each
            # provider declares the options it understands via dep_keys(), so a
            # key valid for one source (e.g. git's 'branch') is rejected on a
            # source that ignores it. The package source itself owns the policy.
            valid_keys = pkg.dep_keys()
            for key in d.keys():
                if key not in valid_keys:
                    hint = _suggest(key, valid_keys)
                    fatal(
                        "Unknown tag '%s' on dependency '%s' (src: %s) @ %s.%s"
                        " Valid tags: %s" % (
                            key, d["name"], src, getlocstr(d), hint,
                            ", ".join(sorted(valid_keys))),
                        key)

            # Resolve content type from 'type:' field (string, dict, or list form).
            # 'with:' is no longer supported; options are now inline in the type dict.
            ct_rgy = PkgContentTypeRgy.inst()
            if "with" in d.keys():
                fatal("Package '%s': 'with:' is no longer supported; "
                      "use inline options instead, e.g. type: { python: { editable: false } } @ %s" % (
                          pkg.name, getlocstr(d["with"])))
            if "type" in d.keys():
                raw = parse_type_field(d["type"])
                for type_name, opts in raw:
                    if not ct_rgy.has(type_name):
                        fatal("Package '%s': unknown type '%s' @ %s ; known types: %s" % (
                            pkg.name, type_name, getlocstr(d["type"]),
                            ", ".join(ct_rgy.names())))
                    pkg.type_data.append(ct_rgy.get(type_name).create_data(opts, si))

            # A 'src: ivpm.yaml' factory contributes deps without occupying a
            # directory, so it has nowhere to host a nested deps-dir.
            if getattr(pkg, "virtual", False) and pkg.deps_mode is not None:
                fatal(
                    "Package '%s': 'deps-mode' is not valid on a '%s' dependency @ %s\n"
                    "  A dep-set factory occupies no directory and so cannot open "
                    "a nested scope. Declare 'deps-mode' on the packages it "
                    "contributes, or in their manifests." % (
                        pkg.name, src, getlocstr(d)),
                    d)

            # Capture per-dep agents config (skill-path override / non-IVPM dep declaration)
            if "agents" in d.keys():
                pkg.agents_config = dict(d["agents"])

            # Parse 'patches:' (cache-aware dependency patching). Allowed only on
            # patch-capable sources; each path resolves against the declaring
            # ivpm.yaml's directory and is MD5-fingerprinted into a PatchSpec.
            if "patches" in d.keys():
                pkg.patches = self._read_patches(pkg, d, src, si)

            # Unless specified, load the same dep-set from sub-packages
            if pkg.dep_set is None:
                pkg.dep_set_inherited = True
                if default_dep_set is not None:
                    pkg.dep_set = default_dep_set
                else:
                    pkg.dep_set = ret.name

#            print("Using dep-set %s for package %s" % (pkg.dep_set, pkg.name))

            ret.add_package(pkg)

            if self.debug:                    
                print("pkg_type (%s): %s" % (pkg.url, str(pkg.pkg_type)))

        if self.debug:
            print("ret: %s %d packages" % (str(ret), len(ret.packages)))
        return ret

    def _read_patches(self, pkg, d, src, si):
        """Parse a dependency's 'patches:' list into resolved PatchSpec objects.

        Rejects patches on a source that declares no patch capability, resolves
        each path against the declaring ivpm.yaml's directory, and fails with a
        located error on a missing file or an unknown patch option.
        """
        from .patch import PatchSpec, PatchCapability, md5_file

        if pkg.patch_capability() == PatchCapability.NONE:
            fatal("Package '%s': source type '%s' does not support patches @ %s" % (
                pkg.name, src, getlocstr(d)), d)

        base_dir = "."
        if si is not None and getattr(si, "filename", None):
            base_dir = os.path.dirname(si.filename) or "."

        raw = d["patches"]
        if not isinstance(raw, (list, tuple)):
            fatal("Package '%s': 'patches' must be a list @ %s" % (
                pkg.name, getlocstr(d)), d)

        specs = []
        for elem in raw:
            loc = getattr(elem, "srcinfo", None) or si
            tool = None
            if isinstance(elem, str):
                file_rel, strip, directory = elem, 1, None
            elif hasattr(elem, "keys"):
                unknown = set(elem.keys()) - {"file", "strip", "directory", "tool"}
                if unknown:
                    fatal("Package '%s': unknown patch option(s): %s @ %s" % (
                        pkg.name, ", ".join(sorted(unknown)), getlocstr(d)), loc)
                if "file" not in elem.keys():
                    fatal("Package '%s': patch entry missing 'file' @ %s" % (
                        pkg.name, getlocstr(d)), loc)
                file_rel = str(elem["file"])
                strip = int(elem["strip"]) if "strip" in elem.keys() else 1
                directory = elem["directory"] if "directory" in elem.keys() else None
                tool = elem["tool"] if "tool" in elem.keys() else None
                if tool is not None and tool not in ("git", "patch"):
                    fatal("Package '%s': patch 'tool' must be 'git' or 'patch', "
                          "got '%s' @ %s" % (pkg.name, tool, getlocstr(d)), loc)
            else:
                fatal("Package '%s': patch entry must be a string or mapping @ %s" % (
                    pkg.name, getlocstr(d)), loc)

            resolved = file_rel if os.path.isabs(file_rel) \
                else os.path.normpath(os.path.join(base_dir, file_rel))
            if not os.path.isfile(resolved):
                fatal("Package '%s': patch file not found: %s @ %s" % (
                    pkg.name, resolved, getlocstr(d)), loc)

            specs.append(PatchSpec(
                name=os.path.basename(file_rel), source=file_rel,
                resolved_path=resolved, md5=md5_file(resolved),
                strip=strip, directory=directory, tool=tool))
        return specs

    def read_path_set(self, info : 'ProjInfo', path, ps_kind : str, ps):
        if ps_kind not in info.paths.keys():
            info.paths[ps_kind] = {}
        path_kind_s = info.paths[ps_kind]

        for p_kind in ps.keys():
            if p_kind not in path_kind_s.keys():
                path_kind_s[p_kind] = []
            for p in ps[p_kind]:
                path_kind_s[p_kind].append(os.path.join(path, p))

    def process_env_directive(self,
                              info : 'ProjInfo',
                              evar : Dict):
        """Parse *evar* and append the resulting EnvSpec to *info*.

        Thin wrapper over :func:`parse_env_directive`, retained for callers
        that hold a ``ProjInfo``.
        """
        parse_env_directive(evar, info.env_settings)


