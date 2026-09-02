#****************************************************************************
#* cmd_install.py
#*
#* 'ivpm install' -- assemble a shared tool directory from one or more
#* published manifests. Unlike 'update', the destination directory *is* the
#* deps-dir: <outdir>/packages.envrc, <outdir>/verilator, <outdir>/python.
#****************************************************************************
import json
import logging
import os
import re

from ivpm.project_ops import ProjectOps
from ivpm.utils import fatal, note, warning

_logger = logging.getLogger("ivpm.cmd_install")


class CmdInstall(object):

    def __call__(self, args):
        from ivpm.install_merge import LoadedSource, merge_sources
        from ivpm.install_spec import SourceSpec, assign_manifest_aliases
        from ivpm.project_ops_info import InstallMode

        outdir = os.path.abspath(args.outdir)
        specs = list(getattr(args, "_source_specs", []) or [])
        resolutions = _parse_resolutions(getattr(args, "resolve", None))

        recorded = _read_install_record(outdir)
        root_var = _check_root_var(getattr(args, "root_var", None))

        if not specs:
            # Bare replay: reproduce the tool directory recorded in the lock.
            if recorded is None:
                fatal("no --from given and %s has no recorded install to "
                      "replay. Name at least one source with --from." % outdir)
            specs = [SourceSpec(src=s["from"], alias=s.get("as"),
                                alias_explicit=s.get("as") is not None,
                                dep_sets=list(s.get("dep_sets") or []),
                                definitions=dict(s.get("definitions") or {}))
                     for s in recorded["sources"]]
            if not resolutions:
                resolutions = dict(recorded.get("collision_resolutions") or {})
            note("Replaying the install recorded in %s/package-lock.json "
                 "(%d source(s))" % (outdir, len(specs)))
        elif recorded is not None:
            # Supplying --from replaces the recorded spec rather than adding to
            # it. A tool tree that silently accumulates sources across
            # invocations is worse than one that makes you say so -- but the
            # user should see exactly what changed.
            _report_source_diff(recorded["sources"], specs)

        # An outdir that recorded a root variable keeps it unless this run says
        # otherwise: the name is a property of the tool directory (consumers
        # reference it), not of the invocation that happened to create it.
        # Say --root-var IVPM_PACKAGES to go back to the bare default.
        if root_var is None and recorded is not None:
            root_var = recorded.get("root_var")
        root_var = root_var or None

        loaded = _load_sources(specs, args)
        specs = assign_manifest_aliases(
            specs, [ls.proj_info.name for ls in loaded])

        proj_info, used = merge_sources(
            loaded, outdir=outdir,
            on_collision=args.on_collision,
            resolutions=resolutions,
            on_project_ref=args.on_project_ref)

        install_record = {
            "install_mode": "toolchain",
            "sources": [
                {"from": s.src, "as": s.alias,
                 "dep_sets": list(s.dep_sets),
                 "definitions": dict(s.definitions)}
                for s in specs
            ],
        }
        # Record every resolution the user asked for, not just the ones that
        # fired: a resolution that is stale today may be needed again after an
        # upstream bump, and dropping it would silently re-break the replay.
        if resolutions:
            install_record["collision_resolutions"] = dict(resolutions)
        if root_var is not None:
            install_record["root_var"] = root_var

        os.makedirs(outdir, exist_ok=True)
        ProjectOps(outdir).update(
            args=args,
            merged_proj_info=proj_info,
            install_mode=InstallMode.TOOLCHAIN,
            install_record=install_record,
            deps_dir_override=".",
            root_var=root_var,
            force_py_install=getattr(args, "force_py_install", False),
            refresh_all=getattr(args, "refresh_all", False),
            timing=getattr(args, "timing", False))


#---------------------------------------------------------------------------
# Helpers
#---------------------------------------------------------------------------

def _load_sources(specs, args):
    """Fetch and read each source, selecting its dep-sets."""
    from ivpm.install_merge import LoadedSource
    from ivpm.ivpm_yaml_reader import IvpmYamlReader
    from ivpm.remote import fetch_manifest

    loaded = []
    for spec in specs:
        fetched = fetch_manifest(spec.src)
        try:
            with open(fetched.local_path) as fp:
                pi = IvpmYamlReader().read(
                    fp, fetched.local_path,
                    cli_overrides=spec.definitions,
                    persisted_vars={},     # external manifest: start clean
                    allow_include=not fetched.is_remote,
                    is_root=True)
        finally:
            fetched.cleanup()
        if pi is None:
            fatal("Could not read a manifest from %s" % spec.src)
        # Record where it actually came from -- fetch_manifest may have
        # appended /ivpm.yaml to a bare host URL.
        spec.src = fetched.origin
        names, ds = ProjectOps._getDepSets(pi, spec.dep_sets)
        loaded.append(LoadedSource(spec=spec, proj_info=pi, dep_set=ds,
                                   dep_set_names=list(names)))
    return loaded


def _parse_resolutions(values):
    """Parse repeatable ``--resolve <pkg>=<alias>`` into a dict."""
    out = {}
    for entry in values or []:
        if "=" not in entry:
            fatal("--resolve requires <package>=<source-alias>, got: %s" % entry)
        pkg, _, alias = entry.partition("=")
        if not pkg or not alias:
            fatal("--resolve requires a non-empty package and alias: %s" % entry)
        out[pkg] = alias
    return out


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_root_var(name):
    """Validate ``--root-var``.

    Returns the name, ``None`` when the flag was not given (so a recorded name
    can be inherited), or ``""`` when the user explicitly asked for the default
    -- spelled ``--root-var IVPM_PACKAGES``, which is how you drop a name the
    outdir recorded last time.
    """
    if name is None:
        return None
    name = name.strip()
    if name == "" or name == "IVPM_PACKAGES":
        return ""
    if not _IDENT_RE.match(name):
        fatal("--root-var must be a shell variable name "
              "([A-Za-z_][A-Za-z0-9_]*), got: %s" % name)
    if name == "IVPM_PROJECT":
        fatal("--root-var cannot be IVPM_PROJECT: that name means 'the project "
              "root', which a tool directory does not have. Pick a name that "
              "says what the directory is, e.g. --root-var TOOLS_ROOT.")
    return name


def _read_install_record(outdir):
    """Return the ``install`` record from *outdir*'s lock, or None."""
    from ivpm.package_lock import read_lock

    lock_path = os.path.join(outdir, "package-lock.json")
    if not os.path.isfile(lock_path):
        return None
    try:
        lock = read_lock(lock_path)
    except Exception as e:
        _logger.debug("could not read lock for install replay: %s", e)
        return None
    if not lock.get("sources"):
        return None
    return lock


def _report_source_diff(recorded, specs):
    """Print what changed between the recorded spec and the one just given."""
    old = {s.get("from"): s for s in recorded}
    new = {s.src: s for s in specs}

    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [
        k for k in new if k in old
        and list(old[k].get("dep_sets") or []) != list(new[k].dep_sets)
    ]
    if not (added or removed or changed):
        return

    note("Replacing the recorded install spec:")
    for k in removed:
        note("  - removed  %s" % k)
    for k in added:
        note("  + added    %s%s" % (
            k, " (%s)" % ", ".join(new[k].dep_sets) if new[k].dep_sets else ""))
    for k in changed:
        note("  ~ dep-sets %s: %s -> %s" % (
            k, ", ".join(old[k].get("dep_sets") or ["<default>"]),
            ", ".join(new[k].dep_sets or ["<default>"])))
