#****************************************************************************
#* install_merge.py
#*
#* Copyright 2018-2024 Matthew Ballance and Contributors
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
"""N-way merge of several published manifests into one synthesized root.

``ivpm install`` fetches each ``--from`` source, selects that source's
dep-sets, and merges the results into a single :class:`ProjInfo` that the
existing update machinery installs into the outdir.

Two things make this more than a dict update:

**Collisions are errors by default.** Two sources providing the same package
name at different versions is a real ambiguity, and silently picking one is
the kind of failure that costs a day to find. But a tool that refuses to
resolve is just as expensive, so every collision message prints both escapes:
``--resolve <pkg>=<alias>`` (surgical) and ``--on-collision`` (blunt).

**Order is explicit.** ``--from`` order determines PATH precedence in the
generated ``packages.envrc``; nothing here may depend on dict iteration order
to carry that.
"""
import dataclasses as dc
from typing import Dict, List, Optional, Tuple

from .env_spec import EnvSpec
from .install_spec import SourceSpec
from .utils import fatal, note, warning


#: --on-collision policies.
COLLISION_POLICIES = ("error", "first-wins", "last-wins")
#: --on-project-ref policies.
PROJECT_REF_POLICIES = ("error", "expand", "drop")

#: The variable with no referent in a tool directory. IVPM_PACKAGES is *not*
#: listed: it remains valid, being exactly the outdir.
PROJECT_REF_VARS = ("IVPM_PROJECT",)

#: The dep-set name of the synthesized root.
MERGED_DEP_SET = "install"

# Package attributes that describe *where a package came from and how it was
# declared* -- the identity two sources must agree on. Everything else is
# either resolution state (filled in later) or bookkeeping that legitimately
# differs between two identical declarations.
_IDENTITY_EXCLUDE = frozenset({
    "srcinfo", "path", "proj_info", "scope_key", "resolved_by",
    "resolved_by_key", "dep_set", "type_data", "self_types",
    "boundary_deps_dir", "cycle_elided", "from_ivpm_source",
    "setup_deps", "from_deps_source",
})


@dc.dataclass
class LoadedSource:
    """A fetched-and-read source, paired with the spec that requested it."""
    spec: SourceSpec
    proj_info: object       # ProjInfo
    dep_set: object         # PackagesInfo -- the selected dep-set(s), merged
    dep_set_names: List[str] = dc.field(default_factory=list)


def package_identity(pkg) -> tuple:
    """A hashable signature of a package *declaration*.

    Built by exclusion rather than by an allow-list of known fields: a source
    provider that adds a new declaration field gets it compared for free. The
    failure mode of over-inclusion is a spurious collision report -- loud and
    escapable -- which is the right way to be wrong here.
    """
    items = []
    for k, v in sorted(vars(pkg).items()):
        if k.startswith("_") or k in _IDENTITY_EXCLUDE:
            continue
        try:
            hash(v)
            items.append((k, v))
        except TypeError:
            items.append((k, repr(v)))
    return tuple(items)


def describe_package(pkg) -> str:
    """A short human description used in collision messages."""
    src = getattr(pkg, "src_type", None) or "?"
    if hasattr(src, "name"):
        src = src.name.lower()
    bits = []
    for attr in ("url", "version", "branch", "tag"):
        val = getattr(pkg, attr, None)
        if val:
            bits.append("%s=%s" % (attr, val))
    return "%s %s" % (src, " ".join(bits)) if bits else str(src)


#---------------------------------------------------------------------------
# Package merge
#---------------------------------------------------------------------------

def merge_packages(loaded: List[LoadedSource],
                   on_collision: str = "error",
                   resolutions: Optional[Dict[str, str]] = None):
    """Merge each source's selected packages into one ordered mapping.

    Returns ``(packages, used_resolutions)``. Raises (via ``fatal``) on an
    unresolved collision under the default policy.
    """
    resolutions = dict(resolutions or {})
    aliases = {ls.spec.alias for ls in loaded}
    for pkg_name, alias in resolutions.items():
        if alias not in aliases:
            fatal("--resolve %s=%s names an unknown source. Known sources: %s"
                  % (pkg_name, alias, ", ".join(sorted(aliases))))

    # name -> (alias, pkg). Insertion order follows --from order, which is what
    # gives packages.envrc its PATH precedence.
    merged: Dict[str, object] = {}
    owner: Dict[str, str] = {}
    seen_names = set()
    used: Dict[str, str] = {}

    for ls in loaded:
        alias = ls.spec.alias
        for name, pkg in ls.dep_set.packages.items():
            seen_names.add(name)
            if name not in merged:
                merged[name] = pkg
                owner[name] = alias
                continue

            incumbent = merged[name]
            if package_identity(incumbent) == package_identity(pkg):
                # Same declaration from two catalogs: install once, say nothing.
                continue

            winner = _resolve_collision(
                name, owner[name], incumbent, alias, pkg,
                on_collision, resolutions)
            if winner == alias:
                merged[name] = pkg
                owner[name] = alias
            if name in resolutions:
                used[name] = resolutions[name]

    for pkg_name in resolutions:
        if pkg_name not in seen_names:
            fatal("--resolve %s=%s names a package that no source provides."
                  % (pkg_name, resolutions[pkg_name]))
        if pkg_name not in used:
            warning("--resolve %s=%s had no effect: no source disagrees about "
                    "'%s'. Drop the flag." % (
                        pkg_name, resolutions[pkg_name], pkg_name))

    return merged, used


def _resolve_collision(name, alias_a, pkg_a, alias_b, pkg_b,
                       on_collision, resolutions):
    """Decide which alias wins, or fail with an actionable message."""
    if name in resolutions:
        chosen = resolutions[name]
        if chosen not in (alias_a, alias_b):
            fatal("--resolve %s=%s names a source that does not provide '%s' "
                  "(it is provided by %s and %s)."
                  % (name, chosen, name, alias_a, alias_b))
        # The winner's definition is taken wholesale -- no field-level merging.
        # A half-merged package definition is not something either catalog
        # author ever tested.
        return chosen

    if on_collision == "error":
        fatal(_collision_message(name, alias_a, pkg_a, alias_b, pkg_b))

    winner = alias_a if on_collision == "first-wins" else alias_b
    # Non-default policies still warn per collision: the policy says how to
    # break a tie, not that ties are unremarkable.
    warning("package '%s' is provided by both '%s' and '%s' with different "
            "definitions; --on-collision=%s selects '%s'"
            % (name, alias_a, alias_b, on_collision, winner))
    return winner


def _collision_message(name, alias_a, pkg_a, alias_b, pkg_b) -> str:
    return (
        "package '%s' is provided by two sources with different definitions\n"
        "\n"
        "  %-10s %s\n"
        "  %-10s %s\n"
        "\n"
        "  Resolve this specific package:   --resolve %s=%s\n"
        "  Or set a policy for all of them: --on-collision=last-wins"
        % (name,
           alias_a, describe_package(pkg_a),
           alias_b, describe_package(pkg_b),
           name, alias_b))


#---------------------------------------------------------------------------
# Root-scoped manifest state
#---------------------------------------------------------------------------

def merge_root_config(loaded: List[LoadedSource], on_collision: str = "error"):
    """Merge the root-scoped fields of each source's ``ProjInfo``.

    Returns a dict of merged values. Scalar disagreements are collisions with
    the same treatment as package collisions: hard error unless a policy is
    set. Only ``--on-collision`` can break a scalar tie -- ``--resolve`` is
    keyed by package name and has nothing to say about a config key.
    """
    env_settings: List[EnvSpec] = []
    paths: Dict[str, dict] = {}
    handler_configs: Dict[str, object] = {}
    python_config = node_config = None
    python_owner = node_owner = None
    deps_mode = None
    deps_mode_owner = None

    for ls in loaded:
        alias = ls.spec.alias
        pi = ls.proj_info

        # Concatenate in --from order: this is the PATH precedence guarantee.
        env_settings.extend(pi.env_settings or [])
        for key, val in (pi.paths or {}).items():
            paths.setdefault(key, {})
            _merge_paths_entry(paths[key], val)

        python_config, python_owner = _pick_config(
            "with.python", python_config, python_owner,
            pi.python_config, alias, on_collision)
        node_config, node_owner = _pick_config(
            "with.node", node_config, node_owner,
            pi.node_config, alias, on_collision)

        for key, val in (pi.handler_configs or {}).items():
            if key not in handler_configs:
                handler_configs[key] = val
            else:
                handler_configs[key] = _merge_handler_config(
                    key, handler_configs[key], val, alias, on_collision)

        if pi.deps_mode is not None:
            if deps_mode is None:
                deps_mode, deps_mode_owner = pi.deps_mode, alias
            elif deps_mode != pi.deps_mode:
                _scalar_conflict("deps-mode", deps_mode_owner, deps_mode,
                                 alias, pi.deps_mode, on_collision)
                if on_collision == "last-wins":
                    deps_mode, deps_mode_owner = pi.deps_mode, alias

    return {
        "env_settings": env_settings,
        "paths": paths,
        "python_config": python_config,
        "node_config": node_config,
        "handler_configs": handler_configs,
        # A tool directory is a flat tree of tools; nesting them would put a
        # tool at a path nobody's PATH mentions.
        "deps_mode": deps_mode or "flatten",
    }


def _merge_paths_entry(dst: dict, src: dict):
    for k, v in (src or {}).items():
        if isinstance(v, list):
            cur = dst.setdefault(k, [])
            for item in v:
                if item not in cur:
                    cur.append(item)
        else:
            dst.setdefault(k, v)


def _pick_config(label, incumbent, incumbent_alias, candidate, alias,
                 on_collision):
    """Merge two dataclass configs, or report the first differing field."""
    if candidate is None:
        return incumbent, incumbent_alias
    if incumbent is None:
        return candidate, alias
    if incumbent == candidate:
        return incumbent, incumbent_alias

    for f in dc.fields(incumbent):
        a, b = getattr(incumbent, f.name), getattr(candidate, f.name)
        if a != b:
            _scalar_conflict("%s.%s" % (label, f.name),
                             incumbent_alias, a, alias, b, on_collision)
    if on_collision == "last-wins":
        return candidate, alias
    return incumbent, incumbent_alias


def _merge_handler_config(key, incumbent, candidate, alias, on_collision):
    """Recursive dict merge; differing scalar leaves are conflicts."""
    if isinstance(incumbent, dict) and isinstance(candidate, dict):
        out = dict(incumbent)
        for k, v in candidate.items():
            if k not in out:
                out[k] = v
            else:
                out[k] = _merge_handler_config(
                    "%s.%s" % (key, k), out[k], v, alias, on_collision)
        return out
    if isinstance(incumbent, list) and isinstance(candidate, list):
        out = list(incumbent)
        for item in candidate:
            if item not in out:
                out.append(item)
        return out
    if incumbent == candidate:
        return incumbent
    _scalar_conflict("with.%s" % key, "an earlier source", incumbent,
                     alias, candidate, on_collision)
    return candidate if on_collision == "last-wins" else incumbent


def _scalar_conflict(key, alias_a, val_a, alias_b, val_b, on_collision):
    if on_collision == "error":
        fatal("sources disagree on '%s'\n"
              "\n"
              "  %-10s %s\n"
              "  %-10s %s\n"
              "\n"
              "  Configuration keys cannot be resolved with --resolve (which "
              "is keyed by package name).\n"
              "  Set a policy instead:            --on-collision=last-wins"
              % (key, alias_a, val_a, alias_b, val_b))
    warning("sources disagree on '%s' (%s=%s, %s=%s); --on-collision=%s applies"
            % (key, alias_a, val_a, alias_b, val_b, on_collision))


#---------------------------------------------------------------------------
# ${IVPM_PROJECT} references
#---------------------------------------------------------------------------

def _mentions_project_ref(value) -> Optional[str]:
    text = value if isinstance(value, str) else str(value)
    for var in PROJECT_REF_VARS:
        if ("${%s}" % var) in text or ("$%s" % var) in text:
            return var
    return None


def apply_project_ref_policy(env_settings: List[EnvSpec], outdir: str,
                             policy: str = "error") -> List[EnvSpec]:
    """Handle ``${IVPM_PROJECT}`` in merged ``env:`` settings.

    There is no project in a tool directory, so such a reference is wrong by
    construction. ``error`` (default) says so; ``expand`` substitutes the
    outdir and warns; ``drop`` removes the setting and warns. The last two
    exist because "the references will be wrong and I accept that" is a
    legitimate position for a user who knows what those variables feed.

    ``IVPM_PACKAGES`` is deliberately untouched: it is the outdir and stays
    correct.
    """
    out: List[EnvSpec] = []
    for spec in env_settings:
        var = _mentions_project_ref(spec.val)
        if var is None:
            out.append(spec)
            continue
        if policy == "error":
            fatal("env setting '%s' references ${%s}, which has no value in a "
                  "tool directory (there is no project root).\n"
                  "  Substitute the tool directory: --on-project-ref=expand\n"
                  "  Or drop the setting entirely:  --on-project-ref=drop"
                  % (spec.var, var))
        if policy == "drop":
            warning("dropping env setting '%s': it references ${%s}, which has "
                    "no value in a tool directory" % (spec.var, var))
            continue
        warning("env setting '%s' references ${%s}; expanding it to the tool "
                "directory (%s), which is not what the manifest meant"
                % (spec.var, var, outdir))
        new_val = str(spec.val)
        for v in PROJECT_REF_VARS:
            new_val = new_val.replace("${%s}" % v, outdir).replace("$%s" % v, outdir)
        out.append(EnvSpec(spec.var, new_val, spec.act))
    return out


#---------------------------------------------------------------------------
# Top level
#---------------------------------------------------------------------------

def merge_sources(loaded: List[LoadedSource],
                  outdir: str,
                  on_collision: str = "error",
                  resolutions: Optional[Dict[str, str]] = None,
                  on_project_ref: str = "error"):
    """Merge *loaded* sources into a synthesized root ``ProjInfo``.

    Returns ``(proj_info, used_resolutions)``. The result carries a single
    dep-set named :data:`MERGED_DEP_SET`, which is what ``install`` asks the
    updater to install.
    """
    from .packages_info import PackagesInfo
    from .proj_info import ProjInfo

    if not loaded:
        fatal("no sources given: 'ivpm install' needs at least one --from")

    packages, used = merge_packages(loaded, on_collision, resolutions)
    root = merge_root_config(loaded, on_collision)
    env_settings = apply_project_ref_policy(
        root["env_settings"], outdir, on_project_ref)

    ds = PackagesInfo(MERGED_DEP_SET)
    ds.packages = packages
    for ls in loaded:
        ds.setup_deps.update(ls.dep_set.setup_deps)
        ds.options.update(ls.dep_set.options)

    pi = ProjInfo(is_src=False)
    pi.name = "ivpm-tool-directory"
    pi.description = "Synthesized from %d source(s): %s" % (
        len(loaded), ", ".join(ls.spec.alias for ls in loaded))
    pi.deps_dir = "."
    pi.default_dep_set = MERGED_DEP_SET
    pi.target_dep_set = MERGED_DEP_SET
    pi.set_dep_set(MERGED_DEP_SET, ds)
    pi.env_settings = env_settings
    pi.paths = root["paths"]
    pi.python_config = root["python_config"]
    pi.node_config = root["node_config"]
    pi.handler_configs = root["handler_configs"]
    pi.deps_mode = root["deps_mode"]
    # resolved_vars are per-source by construction (each source got its own -D
    # overrides), so there is no single merged value to record.
    pi.resolved_vars = {}

    note("Merged %d package(s) from %d source(s)" % (len(packages), len(loaded)))
    return pi, used
