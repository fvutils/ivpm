'''
Created on Jan 19, 2020

@author: ballance
'''

import argparse
import os
import sys

from typing import Dict, List, Tuple

from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo
from ivpm.msg import setup_logging, SrcLoaderError
from .cmds.cmd_build import CmdBuild
from .cmds.cmd_cache import CmdCache
from .cmds.cmd_perf import CmdPerf
from .cmds.cmd_init import CmdInit
from .cmds.cmd_install import CmdInstall
from .cmds.cmd_update import CmdUpdate
from .cmds.cmd_clone import CmdClone
from .cmds.cmd_git_status import CmdGitStatus
from .cmds.cmd_git_update import CmdGitUpdate
from .cmds.cmd_pkg_info import CmdPkgInfo
from .cmds.cmd_share import CmdShare
from .cmds.cmd_snapshot import CmdSnapshot
from .cmds.cmd_status import CmdStatus
from .cmds.cmd_sync import CmdSync
from .cmds.cmd_destroy import CmdDestroy
from .show.cmd_show import CmdShow
from .site_config import parse_git_auth_order


def get_share_dir():
    """Return the path to the IVPM share directory"""
    ivpm_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(ivpm_dir, "share")


def _finalize_subparser_help(subparser, hidden_commands=()):
    hidden = set(hidden_commands)
    subparser._choices_actions = sorted(
        [
            action for action in subparser._choices_actions
            if getattr(action, "dest", None) not in hidden
        ],
        key=lambda action: action.dest)
    subparser.metavar = "{%s}" % ",".join(
        action.dest for action in subparser._choices_actions)

# Common `clone` option strings (do not extract these as provider args).
_COMMON_CLONE_OPT_STRINGS = {
    "--provider", "--ssh", "-a", "--anonymous", "--git-auth-order",
    "-b", "--branch", "--here", "-d", "--dep-set", "--py-uv", "--py-pip",
    "--py-system-site-packages", "--no-cache", "-D", "-h", "--help",
}
# Common `clone` options that consume a following value (used by the light
# argv scan that locates the src token before argparse runs).
_COMMON_CLONE_VALUE_OPTS = {
    "--provider", "--git-auth-order", "-d", "--dep-set", "-b", "--branch",
    "-D", "--log-level",
}


def _scan_clone_src_provider(rest):
    """Light scan of the post-'clone' argv for (forced_provider, src) before
    argparse runs.  Skips values of known value-taking common options so the
    first bare token found is the source locator, not an option value."""
    forced = None
    src = None
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--provider" and i + 1 < len(rest):
            forced = rest[i + 1]
            i += 2
            continue
        if a.startswith("--provider="):
            forced = a.split("=", 1)[1]
            i += 1
            continue
        if a.startswith("-"):
            key = a.split("=", 1)[0]
            if key in _COMMON_CLONE_VALUE_OPTS and "=" not in a:
                i += 2  # skip the option and its value
                continue
            i += 1
            continue
        if src is None:
            src = a
        i += 1
    return forced, src


def _clone_provider_only_spec(provider):
    """Map a provider's declared option strings (that don't collide with common
    clone options) to whether each consumes a value."""
    spec = {}
    try:
        opts = provider.options()
    except Exception:
        opts = []
    for o in opts:
        for f in o.flags:
            if f not in _COMMON_CLONE_OPT_STRINGS:
                spec[f] = (not o.is_flag)
    return spec


def _partition_clone_argv(rest, provider):
    """Split post-'clone' argv into (common_tokens, provider_tokens).

    Provider-only flags (and their values) are pulled out so the common
    argparse pass never sees them -- this is what lets a provider define a
    single-dash long option like ``-branch`` without colliding with the common
    ``-b`` short flag.  Returns (rest, []) when the provider has no such flags
    (e.g. git, whose options overlap the common set)."""
    spec = _clone_provider_only_spec(provider)
    if not spec:
        return list(rest), []
    common, prov = [], []
    i = 0
    while i < len(rest):
        tok = rest[i]
        key = tok.split("=", 1)[0] if tok.startswith("-") and "=" in tok else tok
        if key in spec:
            prov.append(tok)
            if spec[key] and "=" not in tok and i + 1 < len(rest):
                prov.append(rest[i + 1])
                i += 2
                continue
            i += 1
            continue
        common.append(tok)
        i += 1
    return common, prov


def _resolve_clone_provider_safe(args):
    """Resolve the clone provider from parsed args, or None if resolution fails
    (ambiguous / unknown) -- CmdClone will re-resolve and emit the real error."""
    try:
        from .clone.clone_provider_rgy import CloneProviderRgy
        return CloneProviderRgy.inst().resolve(
            args.src, forced=getattr(args, 'provider', None))
    except Exception:
        return None


def _maybe_clone_provider_help(argv):
    """Handle `ivpm clone [<src>|--provider N] ... --help` for NON-default
    providers before argparse (which would otherwise show the generic clone
    help).  Returns True if it printed provider help."""
    if not argv or argv[0] != "clone":
        return False
    rest = argv[1:]
    if not any(a in ("-h", "--help") for a in rest):
        return False
    forced, src = _scan_clone_src_provider(rest)
    if forced is None and src is None:
        return False  # `ivpm clone --help` -> generic help
    try:
        from .clone.clone_provider_rgy import CloneProviderRgy
        provider = CloneProviderRgy.inst().resolve(
            src if src else "https://x", forced=forced)
    except Exception:
        return False
    info = provider.provider_info()
    # git / default providers fall through to the generic clone help (which
    # already documents their options).
    if getattr(info, "is_default", False) and forced is None:
        return False
    parser = provider.build_arg_parser()
    if parser is None:
        print("Clone provider '%s' accepts no additional options." % info.name)
    else:
        parser.print_help()
    return True


def _clone_providers_epilog():
    """Build the 'Available clone providers' block for `ivpm clone --help`."""
    try:
        from .clone.clone_provider_rgy import CloneProviderRgy
        infos = CloneProviderRgy.inst().all_infos()
    except Exception:
        return None
    if not infos:
        return None
    lines = ["Available clone providers:"]
    for info in infos:
        schemes = ("%s://" % ", ".join(info.schemes)) if info.schemes else "(by URL)"
        default = " [default]" if getattr(info, "is_default", False) else ""
        lines.append("  %-10s %-14s %s%s" % (
            info.name, schemes, info.description, default))
    lines.append("")
    lines.append("Run 'ivpm clone <scheme> --help' or 'ivpm show clone-providers' "
                 "for provider options.")
    return "\n".join(lines)


def get_parser(parser_ext : List = None, options_ext : List = None):
    """Create the argument parser"""
    subcommands : Dict[str, object] = {}
    
    # Build the epilog with the agent SKILL.md path
    share_dir = get_share_dir()
    skill_path = os.path.join(share_dir, "skills", "ivpm", "SKILL.md")
    epilog = f"Agent skill documentation: {skill_path}"
    
    parser = argparse.ArgumentParser(
        prog="ivpm",
        description="IVPM (Integrated View Package Manager) - A polyglot package manager that fetches dependencies from diverse sources and assembles unified project views.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    # Global options that apply to all sub-commands
    parser.add_argument("--log-level", dest="log_level",
        choices=["INFO", "DEBUG", "WARN", "NONE"],
        default="NONE",
        help="Set the logging level (default: NONE)")
    subparser = parser.add_subparsers()
    subparser.required = True
    subparser.dest = 'command'

    build_cmd = subparser.add_parser("build",
        help="Build all sub-projects with an IVPM-supported build infrastructure (Python)")
    build_cmd.add_argument("-d", "--dep-set", dest="dep_set", 
        help="Uses dependencies from specified dep-set instead of default")
    build_cmd.add_argument("-g", "--debug", 
        action="store_true",
        help="Enables debug for native extensions")
    build_cmd.set_defaults(func=CmdBuild())
    subcommands["build"] = build_cmd

    # Cache management commands
    cache_cmd = subparser.add_parser("cache",
        help="Manage the IVPM package cache")
    cache_subparser = cache_cmd.add_subparsers(dest="cache_cmd")
    cache_subparser.required = True

    cache_init_cmd = cache_subparser.add_parser("init",
        help="Initialize a new cache directory")
    cache_init_cmd.add_argument("cache_dir",
        help="Path to the cache directory to initialize")
    cache_init_cmd.add_argument("-s", "--shared", dest="shared", action="store_true",
        help="Set group inheritance (chmod g+s) for shared cache usage")
    cache_init_cmd.add_argument("-f", "--force", dest="force", action="store_true",
        help="Force reinitialization of existing directory")

    cache_info_cmd = cache_subparser.add_parser("info",
        help="Show cache information (packages, versions, sizes)")
    cache_info_cmd.add_argument("-c", "--cache-dir", dest="cache_dir",
        help="Cache directory (default: $IVPM_CACHE)")
    cache_info_cmd.add_argument("-v", "--verbose", dest="verbose", action="store_true",
        help="Show detailed version information")

    cache_clean_cmd = cache_subparser.add_parser("clean",
        help="Remove old cache entries")
    cache_clean_cmd.add_argument("-c", "--cache-dir", dest="cache_dir",
        help="Cache directory (default: $IVPM_CACHE)")
    cache_clean_cmd.add_argument("-d", "--days", dest="days", type=int, default=7,
        help="Remove entries unused for more than this many days (default: 7)")
    cache_clean_cmd.add_argument("-n", "--dry-run", dest="dry_run", action="store_true",
        help="List entries that would be removed without deleting anything")

    _finalize_subparser_help(cache_subparser)

    # 'perf' command — inspect persisted performance records
    perf_cmd = subparser.add_parser("perf",
        help="Inspect update performance records (deps/.ivpm/perf-*.json)")
    perf_subparser = perf_cmd.add_subparsers(dest="perf_cmd")
    perf_subparser.required = True

    def _add_project_dir(p):
        p.add_argument("-p", "--project-dir", dest="project_dir", default=None,
            help="Project directory (default: current directory)")

    perf_list_cmd = perf_subparser.add_parser("list",
        help="List available performance records, newest first")
    _add_project_dir(perf_list_cmd)

    perf_show_cmd = perf_subparser.add_parser("show",
        help="Show the breakdown for a record (default: the latest)")
    perf_show_cmd.add_argument("runid", nargs="?", default=None,
        help="Record runid (default: newest)")
    perf_show_cmd.add_argument("--compact", dest="compact", action="store_true",
        help="Omit the waterfall panel")
    _add_project_dir(perf_show_cmd)

    perf_export_cmd = perf_subparser.add_parser("export",
        help="Export a record to Chrome Trace format (Perfetto / chrome://tracing)")
    perf_export_cmd.add_argument("runid", nargs="?", default=None,
        help="Record runid (default: newest)")
    perf_export_cmd.add_argument("--format", dest="format", default="chrome",
        help="Export format (only 'chrome')")
    perf_export_cmd.add_argument("-o", "--output", dest="output", default=None,
        help="Write to FILE instead of stdout")
    _add_project_dir(perf_export_cmd)

    perf_diff_cmd = perf_subparser.add_parser("diff",
        help="Compare two records (runids or file paths)")
    perf_diff_cmd.add_argument("run_a", help="Baseline runid or perf-*.json path")
    perf_diff_cmd.add_argument("run_b", help="Comparison runid or perf-*.json path")
    _add_project_dir(perf_diff_cmd)

    perf_cmd.set_defaults(func=CmdPerf())
    _finalize_subparser_help(perf_subparser)

    cache_cmd.set_defaults(func=CmdCache())
    subcommands["cache"] = cache_cmd

    pkginfo_cmd = subparser.add_parser("pkg-info",
        help="Collect paths/files for a listed set of packages")
    pkginfo_cmd.add_argument("type", 
            choices=("incdirs", "paths", "libdirs", "libs", "flags"),
            help="Specifies what info to query")
    pkginfo_cmd.add_argument("-k", "--kind",
            help="Specifies qualifiers on the type of info to query")
    pkginfo_cmd.add_argument("pkgs", nargs="+")
    pkginfo_cmd.set_defaults(func=CmdPkgInfo())
    subcommands["pkginfo"] = pkginfo_cmd

    share_cmd = subparser.add_parser("share",
        help="Returns the 'share' directory, which includes cmake files, etc")
    share_cmd.add_argument("path", nargs=argparse.REMAINDER)
    share_cmd.set_defaults(func=CmdShare())
    subcommands["share"] = share_cmd

    clone_cmd = subparser.add_parser("clone",
        help="Create a new workspace from a Git URL or path")
    clone_cmd.add_argument("src", help="Source URL or path to clone")
    clone_cmd.add_argument("--provider", dest="provider", default=None,
        metavar="NAME",
        help="Force a specific clone provider (see 'ivpm show clone-providers'); "
             "by default the provider is inferred from the source URL")
    # The following flags belong to the git provider (see 'ivpm clone --provider
    # git --help'); they remain accepted at the top level during the deprecation
    # window (design §11).
    clone_cmd.add_argument("--ssh", dest="ssh", action="store_true",
        help="(git provider) Force SSH: rewrite an https:// URL to git@host:path form before cloning")
    clone_cmd.add_argument("-a", "--anonymous", dest="anonymous", action="store_true",
        help="(git provider) Force HTTPS: clone the URL as written (do not rewrite to SSH)")
    clone_cmd.add_argument("--git-auth-order", dest="git_auth_order",
        type=parse_git_auth_order, default=None,
        help="(git provider) Comma-separated git auth order to try (gh,ssh,https); overrides IVPM_GIT_AUTH_ORDER and site config")
    clone_cmd.add_argument("-b", "--branch", dest="branch",
        help="Target branch; checks out existing or creates new")
    clone_cmd.add_argument("workspace_dir", nargs="?",
        help="Target workspace directory; defaults to basename of src")
    clone_cmd.add_argument("--here", dest="here", action="store_true", default=False,
        help="Set up the workspace in the current directory instead of a new subdirectory. "
             "Idempotent: reuses an existing clone of src if already present, or clones into "
             "a non-empty directory in place.")
    clone_cmd.add_argument("-d", "--dep-set", dest="dep_set",
        help="Dependency set to use for ivpm update")
    clone_cmd.add_argument("--py-uv", dest="py_uv", action="store_true",
        help="Use 'uv' to manage virtual environment")
    clone_cmd.add_argument("--py-pip", dest="py_pip", action="store_true",
        help="Use 'pip' to manage virtual environment")
    clone_cmd.add_argument("--py-system-site-packages", dest="py_system_site_packages",
        action="store_true", default=False,
        help="Inherit system site-packages in the virtual environment (default: isolated)")
    clone_cmd.add_argument("--no-cache", dest="no_cache",
        action="store_true", default=False,
        help="Disable the cache for this clone's update; forces a null cache provider")
    clone_cmd.set_defaults(func=CmdClone())
    subcommands["clone"] = clone_cmd
    clone_cmd.add_argument("-D", dest="definitions", action="append",
        default=[], metavar="VAR=VALUE",
        help="Set variable VAR to VALUE, overriding the default in vars:")
    clone_cmd.epilog = _clone_providers_epilog()
    clone_cmd.formatter_class = argparse.RawDescriptionHelpFormatter

    update_cmd = subparser.add_parser("update",
        help="Fetches packages specified in ivpm.yaml that have not already been loaded")
    update_cmd.set_defaults(func=CmdUpdate())
    update_cmd.add_argument("-D", dest="definitions", action="append",
        default=[], metavar="VAR=VALUE",
        help="Set variable VAR to VALUE, overriding the default in vars:")
    update_cmd.add_argument("-p", "--project-dir", dest="project_dir",
        help="Specifies the project directory to use (default: cwd)")
    update_cmd.add_argument("-d", "--dep-set", dest="dep_set", action="append",
        metavar="DEP-SET",
        help="Uses dependencies from specified dep-set instead of default. "
             "May be repeated (or comma-separated) to install several dep-sets "
             "at once, e.g. -d default -d gui-tools")
    update_cmd.add_argument("-j", "--jobs", dest="jobs", type=int, default=None,
        help="Maximum number of parallel package fetches (default: number of CPU cores)")
    update_cmd.add_argument("--ssh", dest="ssh", action="store_true",
        help="Force SSH: rewrite https:// git URLs to git@host:path form before cloning")
    update_cmd.add_argument("-a", "--anonymous-git", dest="anonymous",
        action="store_true",
        help="Force HTTPS: clone git URLs as written (do not rewrite to SSH)")
    update_cmd.add_argument("--git-auth-order", dest="git_auth_order",
        type=parse_git_auth_order, default=None,
        help="Comma-separated git auth order to try (gh,ssh,https); overrides IVPM_GIT_AUTH_ORDER and site config")
    # '--py-' is the canonical prefix for Python-specific options. The
    # '--<verb>-py-install' spellings are retained as back-compat aliases.
    # dest is explicit because the old spelling came first historically, so
    # argparse derived 'skip_py_install' while the python handler reads
    # 'py_skip_install' -- leaving the flag a silent no-op.
    update_cmd.add_argument("--py-skip-install", "--skip-py-install",
        dest="py_skip_install",
        help="Skip installation of Python packages",
        action="store_true")
    update_cmd.add_argument("--py-force-install", "--force-py-install",
        dest="force_py_install",
        help="Forces a re-install of Python packages",
        action="store_true")
    update_cmd.add_argument("--py-prerls-packages",
        help="Enable installation of pre-release packages",
        action="store_true")
    update_cmd.add_argument("--py-uv",
        help="Use 'uv' to manage virtual environment",
        action="store_true")
    update_cmd.add_argument("--py-pip",
        help="Use 'pip' to manage virtual environment",
        action="store_true")
    update_cmd.add_argument("--py-system-site-packages", dest="py_system_site_packages",
        action="store_true", default=False,
        help="Inherit system site-packages in the virtual environment (default: isolated)")
    update_cmd.add_argument("--lock-file", dest="lock_file", default=None,
        help="Reproduce workspace from a package-lock.json file (ignores ivpm.yaml)")
    update_cmd.add_argument("--from", dest="from_manifest", default=None,
        metavar="PATH-OR-URL",
        help="Drive the update from an external manifest instead of the cwd "
             "ivpm.yaml; resolved deps land in the current directory. Accepts a "
             "manifest file, or a directory/URL containing ivpm.yaml "
             "(e.g. https://edapack.github.io)")
    update_cmd.add_argument("--deps-dir", dest="deps_dir_override", default=None,
        metavar="DIR",
        help="Directory to populate (overrides manifest 'deps-dir'; default: packages)")
    update_cmd.add_argument("--deps-source", dest="deps_source", action="append",
        default=None, metavar="PATH",
        help="Search PATH (a sibling deps/ dir) before the shared cache. Repeatable.")
    update_cmd.add_argument("--trust-deps-source", dest="trust_deps_source",
        action="store_true", default=False,
        help="Trust deps-source entries by name (skip lock-file verification)")
    update_cmd.add_argument("--deps-source-mode", dest="deps_source_mode",
        choices=("link", "copy"), default="link",
        help="How to materialize a deps-source hit (default: link)")
    update_cmd.add_argument("--no-worktree-deps-source", dest="no_worktree_deps_source",
        action="store_true", default=False,
        help="Disable automatic deps-source detection of the parent git worktree")
    update_cmd.add_argument("--refresh-all", dest="refresh_all",
        action="store_true", default=False,
        help="Re-fetch all packages regardless of existing package-lock.json state")
    update_cmd.add_argument("--force", dest="force",
        action="store_true", default=False,
        help="Suppress safety errors during refresh; implies --refresh-all")
    update_cmd.add_argument("--no-cache", dest="no_cache",
        action="store_true", default=False,
        help="Disable the cache for this update; forces a null cache provider")
    update_cmd.add_argument("-v", "--verbose", action="count", default=0,
        help="Increase transcript output detail (-v: activity, -vv: subprocess lines)")
    update_cmd.add_argument("--timing", "--profile", dest="timing",
        action="store_true", default=False,
        help="After the update, print a breakdown of where time was spent "
             "(also persisted to deps/.ivpm/perf-<runid>.json)")
    subcommands["update"] = update_cmd

    install_cmd = subparser.add_parser("install",
        help="Assemble a shared tool directory from one or more published "
             "manifests (the outdir IS the deps-dir)")
    install_cmd.set_defaults(func=CmdInstall())
    # Deliberately NOT named --deps-dir: on 'update' that flag names a
    # subdirectory relative to the project root, whereas here the value is a
    # path that *becomes* the deps-dir.
    install_cmd.add_argument("-o", "--outdir", dest="outdir", required=True,
        metavar="DIR",
        help="Directory to populate. This directory IS the deps-dir: it "
             "receives packages.envrc and the tools themselves directly, with "
             "no 'packages' level in between.")
    # Per-source options (--from, -d/--dep-set, -D/--define, --as) are split out
    # of argv before argparse sees it; see ivpm.install_spec. They are declared
    # here only so --help documents them.
    install_cmd.add_argument("--from", dest="_from_doc", default=None,
        metavar="PATH-OR-URL",
        help="A manifest to install from. Repeatable. Options following a "
             "--from apply to that source only: -d/--dep-set, -D VAR=VALUE, "
             "--as NAME. With no --from, the install recorded in the outdir's "
             "package-lock.json is replayed.")
    install_cmd.add_argument("--resolve", dest="resolve", action="append",
        default=[], metavar="PKG=SOURCE",
        help="Resolve a package collision by naming the source that wins, "
             "e.g. --resolve verilator=edapack. Repeatable. The winner's "
             "definition is taken whole.")
    install_cmd.add_argument("--on-collision", dest="on_collision",
        choices=("error", "first-wins", "last-wins"), default="error",
        help="What to do when sources disagree about a package (default: "
             "error). Non-default policies still warn per collision.")
    install_cmd.add_argument("--on-project-ref", dest="on_project_ref",
        choices=("error", "expand", "drop"), default="error",
        help="What to do with ${IVPM_PROJECT} references in a merged env:, "
             "which have no value in a tool directory (default: error). "
             "${IVPM_PACKAGES} is unaffected -- it is the outdir.")
    install_cmd.add_argument("--root-var", dest="root_var", default=None,
        metavar="NAME",
        help="Name of the variable packages.envrc exports for the tool "
             "directory, e.g. --root-var TOOLS_ROOT. IVPM_PACKAGES is still "
             "exported (as ${NAME}) so manifests that reference it keep "
             "working. Recorded in the lock and reused on a bare replay.")
    install_cmd.add_argument("-j", "--jobs", dest="jobs", type=int, default=None,
        help="Maximum number of parallel package fetches (default: number of CPU cores)")
    install_cmd.add_argument("--ssh", dest="ssh", action="store_true",
        help="Force SSH: rewrite https:// git URLs to git@host:path form before cloning")
    install_cmd.add_argument("-a", "--anonymous-git", dest="anonymous",
        action="store_true",
        help="Force HTTPS: clone git URLs as written (do not rewrite to SSH)")
    install_cmd.add_argument("--git-auth-order", dest="git_auth_order",
        type=parse_git_auth_order, default=None,
        help="Comma-separated git auth order to try (gh,ssh,https)")
    install_cmd.add_argument("--py-skip-install", "--skip-py-install",
        dest="py_skip_install", action="store_true",
        help="Skip installation of Python packages")
    install_cmd.add_argument("--py-force-install", "--force-py-install",
        dest="force_py_install", action="store_true",
        help="Forces a re-install of Python packages")
    install_cmd.add_argument("--py-prerls-packages", action="store_true",
        help="Enable installation of pre-release packages")
    install_cmd.add_argument("--py-uv", action="store_true",
        help="Use 'uv' to manage virtual environment")
    install_cmd.add_argument("--py-pip", action="store_true",
        help="Use 'pip' to manage virtual environment")
    install_cmd.add_argument("--py-system-site-packages",
        dest="py_system_site_packages", action="store_true", default=False,
        help="Inherit system site-packages in the virtual environment")
    install_cmd.add_argument("--refresh-all", dest="refresh_all",
        action="store_true", default=False,
        help="Re-fetch all packages regardless of existing package-lock.json state")
    install_cmd.add_argument("--no-cache", dest="no_cache",
        action="store_true", default=False,
        help="Disable the cache for this install")
    install_cmd.add_argument("-v", "--verbose", action="count", default=0,
        help="Increase transcript output detail")
    install_cmd.add_argument("--timing", "--profile", dest="timing",
        action="store_true", default=False,
        help="After the install, print a breakdown of where time was spent")
    subcommands["install"] = install_cmd
#    update_cmd.add_argument("-r", "--requirements", dest="requirements")
    
    init_cmd = subparser.add_parser("init",
        help="Creates an initial ivpm.yaml file")
    init_cmd.set_defaults(func=CmdInit())
    init_cmd.add_argument("-v", "--version", default="0.0.1")
    init_cmd.add_argument("-f", "--force", default=False, action='store_const', const=True)
    init_cmd.add_argument("name")
    subcommands["init"] = init_cmd
    
    git_status_cmd = subparser.add_parser("git-status",
        help=argparse.SUPPRESS)
    git_status_cmd.set_defaults(func=CmdGitStatus())
    git_status_cmd.add_argument("-p", "-project-dir", dest="project_dir")
    
    git_update_cmd = subparser.add_parser("git-update",
        help=argparse.SUPPRESS)
    git_update_cmd.set_defaults(func=CmdGitUpdate())
    git_update_cmd.add_argument("-p", "-project-dir", dest="project_dir")

    snapshot_cmd = subparser.add_parser("snapshot",
        help="Creates a snapshot of required packages")
    snapshot_cmd.set_defaults(func=CmdSnapshot())
    snapshot_cmd.add_argument("-p", "-project-dir", dest="project_dir")
    snapshot_cmd.add_argument("-r", "--rls-deps", dest="rls", action="store_true",
        help="Uses release deps from project root instead of dev deps")
    snapshot_cmd.add_argument("snapshot_dir", 
            help="Specifies the directory where the snapshot will be created")
    subcommands["snapshot"] = snapshot_cmd

    sync_cmd = subparser.add_parser("sync",
        help="Synchronizes dependent packages with an upstream source (if available)")
    sync_cmd.set_defaults(func=CmdSync())
    sync_cmd.add_argument("-p", "--project-dir", dest="project_dir", default=None,
        help="Directory to start from: a project directory or a deps-dir "
             "(default: discover from cwd)")
    sync_cmd.add_argument("-n", "--dry-run", dest="dry_run", action="store_true",
        default=False, help="Fetch and report sync-ability without merging")
    sync_cmd.add_argument("-j", "--jobs", dest="jobs", type=int, default=0,
        help="Number of parallel sync operations (default: CPU count)")
    sync_cmd.add_argument("--no-rich", action="store_true", default=False,
        help="Plain-text output without Rich formatting")
    sync_cmd.add_argument("-v", "--verbose", action="count", default=0,
        help="Increase transcript output detail (-v: per-package activity)")

    destroy_cmd = subparser.add_parser("destroy",
        help="Remove a root project and all its imports (inverse of clone)")
    destroy_cmd.add_argument("wsdir", nargs="?", default=None,
        help="Workspace directory to remove (required unless --deps-only)")
    destroy_cmd.add_argument("--deps-only", dest="deps_only", action="store_true",
        default=False,
        help="Remove only the imports/venv; keep the root project and ivpm.yaml")
    destroy_cmd.add_argument("-p", "--project-dir", dest="project_dir", default=None,
        help="Workspace root for --deps-only mode (default: cwd)")
    destroy_cmd.add_argument("-n", "--dry-run", dest="dry_run", action="store_true",
        default=False,
        help="Report what would be removed and the gate verdict; change nothing")
    destroy_cmd.add_argument("-f", "--force", action="store_true", default=False,
        help="Delete even when packages hold local modifications or unpushed commits")
    destroy_cmd.add_argument("-y", "--yes", action="store_true", default=False,
        help="Skip the interactive confirmation prompt")
    destroy_cmd.add_argument("-j", "--jobs", dest="jobs", type=int, default=0,
        help="Number of parallel gate/teardown operations (default: CPU count)")
    destroy_cmd.add_argument("--no-rich", action="store_true", default=False,
        help="Plain-text output without Rich formatting")
    destroy_cmd.add_argument("-v", "--verbose", action="count", default=0,
        help="Increase per-package detail (-v: list the blocking files/commits)")
    destroy_cmd.set_defaults(func=CmdDestroy())
    subcommands["destroy"] = destroy_cmd

    status_cmd = subparser.add_parser("status",
        help="Checks the status of sub-dependencies such as git repositories")
    status_cmd.set_defaults(func=CmdStatus())
    status_cmd.add_argument("-p", "--project-dir", dest="project_dir", default=None,
        help="Directory to start from: a project directory or a deps-dir "
             "(default: discover from cwd)")
    status_cmd.add_argument("-v", "--verbose", action="count", default=0,
        help="Show modified/untracked files (-v); also show pypi packages (-v -v)")
    status_cmd.add_argument("--no-rich", action="store_true", default=False,
        help="Plain-text output without Rich formatting")

    show_cmd = subparser.add_parser("show",
        help="Introspect registered package sources, content types, and handlers")
    show_cmd.add_argument("--schema", action="store_true", default=False,
        help="Emit a JSON Schema for ivpm.yaml covering all registered sources and types")
    show_cmd.add_argument("--json", action="store_true", default=False,
        help="Emit JSON instead of rich/text output")
    show_cmd.add_argument("--no-rich", dest="no_rich", action="store_true", default=False,
        help="Plain-text output without Rich formatting")
    show_subparser = show_cmd.add_subparsers(dest="show_cmd")
    show_subparser.required = False   # bare 'ivpm show' shows all categories

    show_source_cmd = show_subparser.add_parser("source",
        aliases=["src"],
        help="List registered package sources (where packages come from)")
    show_source_cmd.add_argument("name", nargs="?",
        help="Show detailed info for this source (omit to list all)")
    show_source_cmd.add_argument("--json", action="store_true", default=False,
        help="Emit JSON output")
    show_source_cmd.add_argument("--no-rich", dest="no_rich", action="store_true", default=False,
        help="Plain-text output")

    show_type_cmd = show_subparser.add_parser("type",
        help="List registered content types (what IVPM does with a package after fetching)")
    show_type_cmd.add_argument("name", nargs="?",
        help="Show detailed info for this type (omit to list all)")
    show_type_cmd.add_argument("--json", action="store_true", default=False,
        help="Emit JSON output")
    show_type_cmd.add_argument("--no-rich", dest="no_rich", action="store_true", default=False,
        help="Plain-text output")

    show_clone_cmd = show_subparser.add_parser("clone-providers",
        aliases=["clone-provider"],
        help="List clone-source providers (how 'ivpm clone' obtains a workspace)")
    show_clone_cmd.add_argument("name", nargs="?",
        help="Show detailed options for this provider (omit to list all)")
    show_clone_cmd.add_argument("--json", action="store_true", default=False,
        help="Emit JSON output")
    show_clone_cmd.add_argument("--no-rich", dest="no_rich", action="store_true", default=False,
        help="Plain-text output")

    show_handler_cmd = show_subparser.add_parser("handler",
        help="List registered package handlers (post-fetch processing hooks)")
    show_handler_cmd.add_argument("name", nargs="?",
        help="Show detailed info for this handler (omit to list all)")
    show_handler_cmd.add_argument("--order", action="store_true", default=False,
        help="Show the resolved root-phase execution order of all handlers")
    show_handler_cmd.add_argument("--json", action="store_true", default=False,
        help="Emit JSON output")
    show_handler_cmd.add_argument("--no-rich", dest="no_rich", action="store_true", default=False,
        help="Plain-text output")

    show_deps_cmd = show_subparser.add_parser("deps",
        help="Show project dependency tree and package information")
    show_deps_cmd.add_argument("name", nargs="?",
        help="Show full detail for this specific dependency (omit to list all)")
    show_deps_cmd.add_argument("--tree", "-t", action="store_true", default=False,
        help="Show hierarchical dependency tree instead of flat list")
    show_deps_cmd.add_argument("--json", action="store_true", default=False,
        help="Emit JSON output")
    show_deps_cmd.add_argument("--no-rich", dest="no_rich", action="store_true", default=False,
        help="Plain-text output without Rich formatting")
    show_deps_cmd.add_argument("-p", "--project-dir", dest="project_dir", default=None,
        help="Project root directory (default: cwd)")
    show_deps_cmd.add_argument("-d", "--dep-set", dest="dep_set", default=None,
        help="Dependency set to inspect (default: project default)")
    show_deps_cmd.add_argument("--dot", action="store_true", default=False,
        help="Emit a Graphviz DOT graph of the dependency relationships")
    show_deps_cmd.add_argument("-o", "--output", dest="output", default=None,
        help="Write output to FILE instead of stdout (useful with --dot)")
    show_deps_cmd.add_argument("--from", dest="from_manifest", default=None,
        metavar="PATH-OR-URL",
        help="Browse an external manifest's catalog (descriptions + dep-sets) "
             "without fetching dependencies. Accepts a manifest file, or a "
             "directory/URL containing ivpm.yaml (e.g. https://edapack.github.io)")

    show_plugins_cmd = show_subparser.add_parser("plugins",
        aliases=["plugin"],
        help="Show Agent Plugins provided by this project and its dependencies")
    show_plugins_cmd.add_argument("name", nargs="?",
        help="Show full detail for this specific plugin (omit to list all)")
    show_plugins_cmd.add_argument("--check", dest="check", default=None,
        metavar="PATH",
        help="Validate a plugin directory or plugin.json against the Agent "
             "Plugins specification and report conformance problems")
    show_plugins_cmd.add_argument("--mcp", action="store_true", default=False,
        help="Show the MCP servers these plugins declare (env values are never "
             "printed). Review this before enabling 'mcp: true'")
    show_plugins_cmd.add_argument("--json", action="store_true", default=False,
        help="Emit JSON output")
    show_plugins_cmd.add_argument("--no-rich", dest="no_rich", action="store_true", default=False,
        help="Plain-text output without Rich formatting")
    show_plugins_cmd.add_argument("-p", "--project-dir", dest="project_dir", default=None,
        help="Project root directory (default: cwd)")
    show_plugins_cmd.add_argument("-d", "--dep-set", dest="dep_set", default=None,
        help="Dependency set to inspect (default: project default)")

    show_config_cmd = show_subparser.add_parser("site-config",
        aliases=["config"],
        help="Show registered site configurations and the active effective settings")
    show_config_cmd.add_argument("name", nargs="?",
        help="Show detailed info for this site config (omit to list all)")
    show_config_cmd.add_argument("--json", action="store_true", default=False,
        help="Emit JSON output")
    show_config_cmd.add_argument("--no-rich", dest="no_rich", action="store_true", default=False,
        help="Plain-text output without Rich formatting")

    _finalize_subparser_help(show_subparser)

    show_cmd.set_defaults(func=CmdShow())
    subcommands["show"] = show_cmd

    if parser_ext is not None:
        for ext in parser_ext:
            ext(subparser)

    if options_ext is not None:
        for ext in options_ext:
            ext(subcommands)

    _finalize_subparser_help(subparser, ("git-status", "git-update"))

    return parser

#: Top-level options that consume a following value, so the subcommand scan
#: does not mistake that value for the subcommand name.
_GLOBAL_VALUE_OPTS = ("--log-level",)


def _find_subcommand_index(argv):
    """Index of the subcommand token in *argv*, or None.

    Returns the position of the first bare (non-option) token, skipping the
    values of global options that take one. Locating it by position -- rather
    than searching for the literal name -- keeps a *later* argument that
    happens to equal a subcommand name from being mistaken for it.
    """
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _GLOBAL_VALUE_OPTS:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return i
    return None


def find_subparser(parser, name):
    """The subparser registered under *name*, or None.

    Extensions can add options to a subcommand after the fact, so the split
    grammar has to read the option set off the live parser rather than a
    hard-coded list.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub = action.choices.get(name)
            if sub is not None:
                return sub
    return None


def main(project_dir=None):
    from .pkg_types.pkg_type_rgy import PkgTypeRgy
    import logging

    # Handle --version / -V before the subcommand parser (which requires a subcommand)
    if len(sys.argv) == 2 and sys.argv[1] in ("--version", "-V"):
        from ivpm.__version__ import get_version
        print(get_version())
        return

    # `ivpm clone <scheme> --help` shows the resolved provider's options before
    # argparse can intercept --help with the generic clone help (design §5.2).
    if _maybe_clone_provider_help(sys.argv[1:]):
        return

    # First things first: load any extensions
    if sys.version_info < (3, 10):
        from importlib_metadata import entry_points
    else:
        from importlib.metadata import entry_points

    discovered_plugins = entry_points(group='ivpm.ext')
    parser_ext = []
    options_ext = []
    # Guard against duplicate entry points that appear when stale
    # metadata (e.g. an old .egg-info) coexists with current .dist-info.
    seen_ext_modules = set()
    for p in discovered_plugins:
        if p.value in seen_ext_modules:
            continue
        seen_ext_modules.add(p.value)
        try:
            mod = p.load()
            if hasattr(mod, "ivpm_subcommand"):
                parser_ext.append(getattr(mod, "ivpm_subcommand"))
            elif hasattr(mod, "ivpm_options"):
                options_ext.append(mod)
            elif hasattr(mod, "ivpm_pkgtype"):
                from .show.info_types import ep_registration_kwargs
                prov = ep_registration_kwargs(p)
                pkg_types = []
                getattr(mod, "ivpm_pkgtype")(pkg_types)
                for pt in pkg_types:
                    PkgTypeRgy.inst().register(pt[0], pt[1], pt[2] if len(pt) > 2 else "", **prov)
        except Exception as e:
            print("Error: caught exception while loading IVPM extension %s (%s)" %(
                p.name,
                str(e)))
            raise e

    # Load package handlers (must happen before get_parser so handlers can register CLI options)
    from .handlers.package_handler_rgy import PackageHandlerRgy
    options_ext.append(PackageHandlerRgy.inst().add_handler_options)

    parser = get_parser(parser_ext, options_ext)

    raw_argv = sys.argv[1:]

    # 'install' uses an interleaved grammar (--from X -d a --from Y -d b) that
    # argparse cannot express, so argv is split into per-source groups before
    # argparse sees it. Only the global options reach the parser; the parsed
    # SourceSpecs are attached to args below.
    install_specs = None
    _cmd_idx = _find_subcommand_index(raw_argv)
    if _cmd_idx is not None and raw_argv[_cmd_idx] == "install":
        from .install_spec import (build_global_option_table,
                                   parse_source_groups, split_source_groups)
        head = raw_argv[:_cmd_idx + 1]
        tail = raw_argv[_cmd_idx + 1:]
        # Global options are hoisted out of the group they were typed in, so
        # 'install --from X -d sim -o tools' works like the canonical order.
        global_options = build_global_option_table(
            find_subparser(parser, "install"))
        global_argv, groups = split_source_groups(tail, global_options)
        install_specs = parse_source_groups(groups, global_options)
        raw_argv = head + global_argv

    # Custom parsing to allow trailing workspace dir after options for 'clone'
    args, extras = parser.parse_known_args(raw_argv)

    if install_specs is not None:
        args._source_specs = install_specs

    # For `clone`, pull provider-only options out of argv and reparse the rest
    # cleanly, so a provider's single-dash long option (e.g. '-branch') never
    # collides with a common short flag ('-b'). The extracted tokens are handed
    # to the provider in phase 2 (design §5.1).
    clone_provider_tokens = []
    if getattr(args, 'command', None) == 'clone':
        provider = _resolve_clone_provider_safe(args)
        if provider is not None:
            common_argv, clone_provider_tokens = _partition_clone_argv(raw_argv, provider)
            if clone_provider_tokens:
                args, extras = parser.parse_known_args(common_argv)

    # Handle -D flags that appear before the subcommand (in extras)
    remaining_extras = []
    i = 0
    while i < len(extras):
        e = extras[i]
        if e.startswith('-D') and len(e) > 2:
            # -Dvar=val (combined form)
            if not hasattr(args, 'definitions'):
                args.definitions = []
            args.definitions.append(e[2:])
        elif e == '-D' and i + 1 < len(extras):
            # -D var=val (separate form)
            if not hasattr(args, 'definitions'):
                args.definitions = []
            args.definitions.append(extras[i + 1])
            i += 1
        else:
            remaining_extras.append(e)
        i += 1
    extras = remaining_extras

    if getattr(args, 'command', None) == 'clone':
        # Rescue a lone trailing workspace_dir that landed in extras because it
        # followed options (bare token, not a provider option starting with '-').
        if getattr(args, 'workspace_dir', None) is None and len(extras) == 1 \
                and not extras[0].startswith('-'):
            args.workspace_dir = extras[0]
            extras = []
        # Provider-specific args (partitioned out above) are parsed in phase 2
        # by the resolved clone provider. Any remaining extras are genuine
        # unrecognized tokens and still error below.
        args._clone_extras = clone_provider_tokens
    if len(extras) != 0:
        print('ivpm: error: unrecognized arguments: ' + ' '.join(extras))
        sys.exit(2)

    # Setup logging based on --log-level option
    log_level = getattr(args, 'log_level', 'NONE')
    setup_logging(log_level)

    # If the user hasn't specified the project directory,
    # set the default
    if not hasattr(args, "project_dir") or getattr(args, "project_dir") is None:
        args.project_dir = project_dir

    try:
        args.func(args)
    except SrcLoaderError as e:
        # Diagnostics emitted through the reporter are already rendered.
        # However, some paths (e.g. raw YAML parse errors) may raise
        # SrcLoaderError without going through the reporter. Detect that
        # case and print the message so the user is never left with a
        # silent exit.
        if not e.diagnostics:
            print(str(e), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
    
