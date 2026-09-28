#****************************************************************************
#* manager.py
#*
#* Copyright 2026 Matthew Ballance and Contributors
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
"""The operations behind ``ivpm skills``: choose an environment, query it,
and install, uninstall or re-sync a chosen set of its skills into a directory.

Output is left to the caller: every operation returns data.
"""
import dataclasses as dc
import logging
import os
import shutil
import subprocess
import sys
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from . import install as _install
from . import naming as _naming
from . import query as _query
from . import select as _select
from . import state as _state
from .model import SkillEntry

_logger = logging.getLogger("ivpm.agent_skills.manager")

#: Query timeout when uv may have to download and build packages
UV_TIMEOUT = 900


class SkillsError(Exception):
    """A user-facing failure of an ``ivpm skills`` operation."""


# ---------------------------------------------------------------------- #
# Environment selection
# ---------------------------------------------------------------------- #

@dc.dataclass
class EnvChoice(object):
    #: Interpreter command: [python] or a uv launcher ending in 'python'
    argv: List[str]
    #: How it was chosen, for display: '--python', 'VIRTUAL_ENV', ...
    reason: str
    #: Package specs when the environment is built by uv from --with
    with_specs: List[str] = dc.field(default_factory=list)
    env: Optional[Dict[str, str]] = None
    timeout: float = _query.DEFAULT_TIMEOUT

    @property
    def label(self) -> str:
        if self.with_specs:
            return "uv --with %s" % " --with ".join(self.with_specs)
        return self.argv[0]


def venv_python(venv_dir: str) -> Optional[str]:
    for rel in (os.path.join("bin", "python"), os.path.join("Scripts", "python.exe"),
                os.path.join("Scripts", "python")):
        path = os.path.join(venv_dir, rel)
        if os.path.isfile(path):
            return path
    return None


def is_ivpm_project(root: str) -> bool:
    return os.path.isfile(os.path.join(root, "ivpm.yaml"))


def project_deps_dir(root: str) -> Optional[str]:
    """The deps dir of the IVPM project at ``root``, or None if not one."""
    if not is_ivpm_project(root):
        return None
    from ..proj_info import resolve_deps_dir
    return resolve_deps_dir(root)


def find_uv(environ: Mapping[str, str] = os.environ) -> Optional[List[str]]:
    """The uv command, or None.

    ``$UV`` comes first: uv sets it for every process it launches, which
    covers ``uvx ivpm`` running somewhere uv itself is not on PATH.
    """
    uv = environ.get("UV")
    if uv and os.path.isfile(uv):
        return [uv]
    uv = shutil.which("uv")
    if uv:
        return [uv]
    try:
        import importlib.util
        if importlib.util.find_spec("uv") is not None:
            return [sys.executable, "-m", "uv"]
    except (ImportError, ValueError):
        pass
    return None


def uv_cache_dir(environ: Mapping[str, str] = os.environ) -> Optional[str]:
    """uv's cache directory: links into it would break on 'uv cache prune'."""
    if environ.get("UV_CACHE_DIR"):
        return environ["UV_CACHE_DIR"]
    uv = find_uv(environ)
    if uv is not None:
        try:
            r = subprocess.run(uv + ["cache", "dir"], capture_output=True,
                               text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    if sys.platform == "win32":
        base = environ.get("LOCALAPPDATA")
        return os.path.join(base, "uv", "cache") if base else None
    if sys.platform == "darwin":
        return os.path.expanduser(os.path.join("~", "Library", "Caches", "uv"))
    base = environ.get("XDG_CACHE_HOME") or os.path.expanduser(os.path.join("~", ".cache"))
    return os.path.join(base, "uv")


def _own_env_offers_skills() -> bool:
    """True when IVPM's own environment has skills from other packages."""
    from .._compat import entry_points
    for group in ("agent.skills", "ivpm.skill", "agent.plugins"):
        try:
            eps = entry_points(group=group)
        except Exception:
            continue
        if any(ep.name != "ivpm" for ep in eps):
            return True
    from . import _env_query
    return any(os.path.isdir(os.path.join(p, _env_query.SHARE_SUBDIR))
               for p in _env_query._prefixes())


def is_under(path: str, parent: Optional[str]) -> bool:
    if not parent:
        return False
    path = os.path.realpath(path)
    parent = os.path.realpath(parent)
    return path == parent or path.startswith(parent + os.sep)


def choose_env(root: str, python: Optional[str] = None,
               with_specs: Sequence[str] = (),
               upgrade: bool = False, offline: bool = False,
               environ: Mapping[str, str] = os.environ) -> EnvChoice:
    """Pick the environment whose skills are offered. First that applies:

    1. ``--with SPEC``: an environment uv builds from those specs
    2. ``--python PATH``
    3. ``root`` is an IVPM project with a managed venv
    4. ``$VIRTUAL_ENV``
    5. ``root/.venv``
    6. the interpreter running IVPM

    except that an IVPM run by uvx whose own environment has skills from
    other packages (``uvx --with pkg ivpm ...``) uses that environment
    right after 2.
    """
    if with_specs:
        uv = find_uv(environ)
        if uv is None:
            raise SkillsError(
                "--with needs uv, which was not found (looked at $UV, PATH and "
                "'python -m uv'). Install it (https://docs.astral.sh/uv/) or use "
                "--python with an environment that already has the packages")
        argv = uv + ["run", "--no-project", "--isolated"]
        for spec in with_specs:
            argv += ["--with", spec]
        if upgrade:
            argv.append("--upgrade")
        if offline:
            argv.append("--offline")
        argv.append("python")
        # The environment must be exactly what the specs say: an inherited
        # PYTHONPATH or VIRTUAL_ENV would leak other packages (and their
        # skills) into it.
        env = {k: v for k, v in environ.items()
               if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")}
        return EnvChoice(argv=argv, reason="--with", with_specs=list(with_specs),
                         env=env, timeout=UV_TIMEOUT)

    if python:
        if not os.path.isfile(python) and shutil.which(python) is None:
            raise SkillsError("--python: no interpreter at '%s'" % python)
        return EnvChoice(argv=[python], reason="--python")

    # 'uvx --with pkg ivpm skills ...': the packages are in IVPM's own
    # environment, and that is plainly the one meant -- even when the shell
    # still has some other venv activated.
    if is_under(sys.prefix, uv_cache_dir(environ)) and _own_env_offers_skills():
        return EnvChoice(argv=[sys.executable], reason="uvx environment")

    deps_dir = project_deps_dir(root)
    if deps_dir is not None:
        py = venv_python(os.path.join(deps_dir, "python"))
        if py is not None:
            return EnvChoice(argv=[py], reason="IVPM project venv")

    if environ.get("VIRTUAL_ENV"):
        py = venv_python(environ["VIRTUAL_ENV"])
        if py is not None:
            return EnvChoice(argv=[py], reason="VIRTUAL_ENV")

    py = venv_python(os.path.join(root, ".venv"))
    if py is not None:
        return EnvChoice(argv=[py], reason=".venv")

    return EnvChoice(argv=[sys.executable], reason="interpreter running ivpm")


# ---------------------------------------------------------------------- #
# What an environment offers
# ---------------------------------------------------------------------- #

@dc.dataclass
class EnvInfo(object):
    choice: EnvChoice
    result: _query.QueryResult
    available: List[_select.Available]


def _ivpm_available() -> Optional[_select.Available]:
    """IVPM's own skill, offered even when the environment lacks IVPM."""
    from .. import skills as _ivpm_skills
    from . import frontmatter
    try:
        from ..__version__ import get_version
        version = get_version()
    except Exception:
        version = None
    for path in _ivpm_skills.get_skill_dirs():
        fields = frontmatter.parse(os.path.join(path, "SKILL.md")) or {}
        if fields.get("name"):
            return _select.Available(
                name=fields["name"], ep_name="ivpm", dist="ivpm", version=version,
                path=path, description=fields.get("description", ""))
    return None


def query(choice: EnvChoice) -> EnvInfo:
    try:
        result = _query.run_query(choice.argv, timeout=choice.timeout, env=choice.env)
    except _query.QueryError as exc:
        raise SkillsError("could not query %s: %s" % (choice.label, exc))
    available = _select.available_from_query(result)
    if not any(av.ep_name == "ivpm" for av in available):
        own = _ivpm_available()
        if own is not None:
            available.append(own)
    available.sort(key=lambda av: (av.name, av.provider))
    return EnvInfo(choice=choice, result=result, available=available)


# ---------------------------------------------------------------------- #
# Ownership
# ---------------------------------------------------------------------- #

def _target_dirs(root: str) -> Dict[str, str]:
    """state key -> directory, for every target (enabled or not)."""
    out = {"agents_skills": os.path.join(root, ".agents", "skills")}
    for key, subdir, _ in _install.TOOL_TARGETS:
        out["%s_skills" % key] = os.path.join(root, subdir)
    return out


def ownership(root: str, st: Optional[_state.SkillsState]) -> Dict[str, Dict[str, str]]:
    """state key -> {name: owner} for every entry in every target dir.

    owner is 'ivpm skills', 'ivpm update' or 'unmanaged'.
    """
    ours = {k: {e.name for e in v} for k, v in (st.installed.items() if st else [])}
    handler = _state.handler_installed(project_deps_dir(root))
    out: Dict[str, Dict[str, str]] = {}
    for key, path in _target_dirs(root).items():
        names = {}
        if os.path.isdir(path):
            for name in os.listdir(path):
                if name.startswith("."):
                    continue
                if name in ours.get(key, ()):
                    names[name] = "ivpm skills"
                elif name in handler.get(key, ()):
                    names[name] = "ivpm update"
                else:
                    names[name] = "unmanaged"
        out[key] = names
    return out


# ---------------------------------------------------------------------- #
# Install / uninstall / sync
# ---------------------------------------------------------------------- #

@dc.dataclass
class ApplyReport(object):
    installed: List[Tuple[str, _select.Available]] = dc.field(default_factory=list)
    #: Selections the environment no longer provides (left as they were)
    missing: List[_state.Selection] = dc.field(default_factory=list)
    #: dist -> (old version, new version)
    version_changes: Dict[str, Tuple[Optional[str], Optional[str]]] = dc.field(default_factory=dict)
    mode: str = "link"
    #: Why copy mode was used when link was asked for
    copy_reason: Optional[str] = None
    targets: List[str] = dc.field(default_factory=list)


def _resolve_selection(sel: _state.Selection,
                       available: List[_select.Available]) -> Optional[_select.Available]:
    for av in available:
        if av.key == sel.key:
            return av
    # The provider was renamed (an entry point per skill became one for all,
    # say) but the same distribution still ships a skill of that name.
    if sel.dist:
        for av in available:
            if av.name == sel.skill and _select._norm_dist(av.dist) == _select._norm_dist(sel.dist):
                return av
    return None


def _check_conflicts(root: str, st: _state.SkillsState,
                     new: List[Tuple[str, _select.Available]]):
    owners = ownership(root, st)
    dests: Dict[str, _select.Available] = {}
    for dest, av in new:
        if dest in dests and dests[dest].key != av.key:
            raise SkillsError(
                "'%s' and '%s' would both be installed as '%s'; install one "
                "of them with --as NAME" % (dests[dest].qualified, av.qualified, dest))
        dests[dest] = av
        for key, names in owners.items():
            owner = names.get(dest)
            if owner == "ivpm update":
                raise SkillsError(
                    "'%s' is already installed by 'ivpm update' (the agents "
                    "handler). In an IVPM project, choose entry-point skills with "
                    "'with.agents.entrypoints' in ivpm.yaml, or install this one "
                    "under another name with --as NAME" % dest)
            if owner == "unmanaged":
                raise SkillsError(
                    "%s already exists and was not created by IVPM; remove it or "
                    "install under another name with --as NAME"
                    % os.path.join(_target_dirs(root)[key], dest))


def _plan(st: _state.SkillsState, available: List[_select.Available]
          ) -> Tuple[List[Tuple[str, _select.Available]], List[_state.Selection]]:
    planned, missing = [], []
    for sel in st.selections:
        av = _resolve_selection(sel, available)
        if av is None:
            missing.append(sel)
        else:
            planned.append((sel.dest_name, av))
    return planned, missing


def apply(root: str, st: _state.SkillsState, info: EnvInfo,
          cache_dir: Optional[str] = None) -> ApplyReport:
    """Make the target directories match ``st.selections``.

    Previously installed entries are removed and rewritten, except those of
    selections the environment no longer provides: those are left in place
    (and reported) rather than silently deleted.
    """
    planned, missing = _plan(st, info.available)
    _check_conflicts(root, st, planned)

    agents = st.agents or list(_install.AGENTS)
    targets = _install.build_targets(root, agents)

    # Copy, not link, when the source could vanish: a link into uv's cache
    # dangles after 'uv cache prune', and a --with environment is uv's.
    mode = st.mode
    copy_reason = None
    if mode == "link":
        if info.choice.with_specs:
            mode, copy_reason = "copy", "the environment was built by uv (--with)"
        elif cache_dir and any(is_under(av.path, cache_dir) for _, av in planned):
            mode, copy_reason = "copy", "the skills live in uv's cache (%s)" % cache_dir
    for tgt in targets:
        os.makedirs(tgt.path, exist_ok=True)
    if mode == "link" and not _install.symlinks_supported(targets[0].path):
        mode, copy_reason = "copy", "this filesystem does not support symlinks"

    # Remove what we wrote before -- all of it except entries of selections
    # we cannot re-install now.
    keep_names = {sel.dest_name for sel in missing}
    prev = {k: [e.name for e in v if e.name not in keep_names]
            for k, v in st.installed.items()}
    _install.remove_managed(root, prev)
    kept = {k: [e for e in v if e.name in keep_names] for k, v in st.installed.items()}

    skill_assigned: List[Tuple[str, SkillEntry]] = []
    plugin_assigned: List[Tuple[str, object]] = []
    src_of: Dict[str, str] = {}
    for dest, av in planned:
        if av.kind == "plugin":
            from ..agent_plugins import discovery as _discovery
            plugin, diags = _discovery.load_from_reference(
                av.path, av.ep_name or av.name, kind="plugin-dependency")
            for d in diags:
                if d.severity != "info":
                    _logger.warning("%s: %s", av.qualified, d.message)
            if plugin is None:
                continue
            plugin_assigned.append((dest, plugin))
            for skill_dir in plugin.skill_dirs:
                entry = SkillEntry("plugin-dependency", plugin.owner_name, plugin.root_dir,
                                   skill_dir, plugin_name=plugin.name)
                name = _naming.name_candidates(entry)[0]
                skill_assigned.append((name, entry))
                src_of[name] = skill_dir
        else:
            entry = SkillEntry("dependency", av.ep_name or "share", av.path, av.path,
                               source="entrypoint" if av.ep_name else "share")
            skill_assigned.append((dest, entry))
            src_of[dest] = av.path

    linker = _install.Linker(root, use_symlinks=(mode == "link"))
    managed, _ = _install.populate(linker, targets, skill_assigned, plugin_assigned)

    installed: Dict[str, List[_state.InstalledEntry]] = {}
    for key, names in managed.items():
        entries = []
        for name in names:
            h = None
            if mode == "copy" and name in src_of:
                h = _install.content_hash(src_of[name])
            entries.append(_state.InstalledEntry(name=name, mode=mode, hash=h))
        installed[key] = entries
    for key, entries in kept.items():
        installed.setdefault(key, []).extend(entries)
    st.installed = {k: v for k, v in installed.items() if v}

    old_dists = dict(st.resolved.get("dists", {}))
    new_dists = {av.dist: av.version for _, av in planned if av.dist}
    changes = {d: (old_dists.get(d), v) for d, v in new_dists.items()
               if d in old_dists and old_dists[d] != v}
    st.resolved = {"python": info.result.python or info.choice.label,
                   "dists": dict(sorted(new_dists.items()))}
    st.mode = st.mode or "link"
    _state.save(root, st)

    return ApplyReport(installed=planned, missing=missing, version_changes=changes,
                       mode=mode, copy_reason=copy_reason,
                       targets=[t.path for t in targets])


def install(root: str, info: EnvInfo, selectors: Sequence[str], all_: bool = False,
            agents: Optional[Sequence[str]] = None, mode: Optional[str] = None,
            as_name: Optional[str] = None,
            cache_dir: Optional[str] = None) -> ApplyReport:
    st = _state.load(root) or _state.SkillsState(agents=list(_install.AGENTS))
    if agents is not None:
        st.agents = _normalize_agents(agents)
    if mode is not None:
        st.mode = mode
    for spec in info.choice.with_specs:
        if spec not in st.with_specs:
            st.with_specs.append(spec)

    if all_:
        chosen = [av for av in info.available]
    else:
        try:
            chosen = _select.resolve(selectors, info.available)
        except _select.SelectionError as exc:
            raise SkillsError(str(exc))
    if info.choice.reason == "uvx environment":
        # 'uvx --with pkg ivpm skills install': uvx does not tell us its specs,
        # but the distributions that provide the chosen skills are the recipe
        # 'sync' needs to rebuild the environment.
        for av in chosen:
            if av.dist and av.dist != "ivpm" and av.dist not in st.with_specs:
                st.with_specs.append(av.dist)
    if as_name is not None and len(chosen) != 1:
        raise SkillsError("--as needs exactly one skill; the selectors match %d"
                          % len(chosen))

    for av in chosen:
        sel = st.find(av.key)
        if sel is None:
            sel = _state.Selection(provider=av.provider, skill=av.name, dist=av.dist)
            st.selections.append(sel)
        if as_name is not None:
            sel.as_name = as_name if as_name != av.name else None
    return apply(root, st, info, cache_dir)


def _normalize_agents(agents: Sequence[str]) -> List[str]:
    out: List[str] = []
    for item in agents:
        for a in str(item).split(","):
            a = a.strip()
            if not a:
                continue
            if a == "all":
                return list(_install.AGENTS)
            if a not in _install.AGENTS:
                raise SkillsError("unknown agent '%s'; choose from %s, all"
                                  % (a, ", ".join(_install.AGENTS)))
            if a not in out:
                out.append(a)
    # .agents/skills is always written: it is the neutral view
    if "agents" not in out:
        out.insert(0, "agents")
    return out


def _selection_available(sel: _state.Selection) -> _select.Available:
    ep = sel.provider[3:] if sel.provider.startswith("ep:") else None
    return _select.Available(name=sel.skill, ep_name=ep, dist=sel.dist,
                             version=None, path="")


def uninstall(root: str, selectors: Sequence[str], all_: bool = False
              ) -> List[_state.Selection]:
    """Remove selections and exactly the entries installed for them.

    Needs no environment: everything required is in the state file.
    """
    st = _state.load(root)
    if st is None or not st.selections:
        raise SkillsError("no skills installed by 'ivpm skills' in %s" % root)
    if all_:
        removed = list(st.selections)
    else:
        removed = []
        for s in selectors:
            hits = [sel for sel in st.selections
                    if _select.matches(s, _selection_available(sel), sel.dest_name)]
            if not hits:
                raise SkillsError("'%s' matches no installed skill" % s)
            removed += [h for h in hits if h not in removed]

    names = set()
    for sel in removed:
        names.add(sel.dest_name)
    # A plugin's unbundled skills are named '<plugin>-<skill>'
    prefixes = tuple("%s-" % sel.dest_name for sel in removed)
    doomed = {k: [e.name for e in v if e.name in names or e.name.startswith(prefixes)]
              for k, v in st.installed.items()}
    _install.remove_managed(root, doomed)
    st.installed = {k: [e for e in v if e.name not in doomed.get(k, [])]
                    for k, v in st.installed.items()}
    st.installed = {k: v for k, v in st.installed.items() if v}
    st.selections = [sel for sel in st.selections if sel not in removed]
    if not st.selections:
        st.with_specs = []
    _state.save(root, st)
    return removed


def sync_choice(root: str, python: Optional[str], with_specs: Sequence[str],
                upgrade: bool = False, offline: bool = False,
                environ: Mapping[str, str] = os.environ) -> EnvChoice:
    """The environment for commands that act on an existing state file.

    A state file built with --with is rebuilt from its own specs (plus any new
    ones), unless an interpreter is named explicitly.
    """
    st = _state.load_quiet(root)
    specs = list(with_specs)
    if st is not None and st.with_specs and python is None:
        specs = st.with_specs + [s for s in specs if s not in st.with_specs]
    return choose_env(root, python, specs, upgrade=upgrade, offline=offline,
                      environ=environ)


def sync(root: str, info: EnvInfo, cache_dir: Optional[str] = None) -> ApplyReport:
    st = _state.load(root)
    if st is None:
        raise SkillsError("no %s in %s; nothing to sync" % (_state.STATE_REL, root))
    return apply(root, st, info, cache_dir)


# ---------------------------------------------------------------------- #
# Status
# ---------------------------------------------------------------------- #

@dc.dataclass
class Problem(object):
    kind: str       # 'missing', 'dangling', 'stale', 'absent', 'version'
    name: str
    target: str
    detail: str


def status(root: str, info: Optional[EnvInfo]) -> Tuple[Optional[_state.SkillsState], List[Problem]]:
    st = _state.load(root)
    problems: List[Problem] = []
    if st is None:
        return None, problems

    dirs = _target_dirs(root)
    for key, entries in st.installed.items():
        base = dirs.get(key)
        if base is None:
            continue
        for ent in entries:
            path = os.path.join(base, ent.name)
            if os.path.islink(path) and not os.path.exists(path):
                problems.append(Problem("dangling", ent.name, key,
                                        "link target %s is gone" % os.readlink(path)))
            elif not os.path.lexists(path):
                problems.append(Problem("absent", ent.name, key,
                                        "recorded as installed but not present"))

    if info is not None:
        for sel in st.selections:
            av = _resolve_selection(sel, info.available)
            if av is None:
                problems.append(Problem("missing", sel.dest_name, "",
                                        "%s/%s is no longer provided by the environment"
                                        % (sel.provider, sel.skill)))
                continue
            for key, entries in st.installed.items():
                for ent in entries:
                    if ent.name == sel.dest_name and ent.mode == "copy" and ent.hash \
                            and ent.hash != _install.content_hash(av.path):
                        problems.append(Problem("stale", ent.name, key,
                                                "the source changed since it was copied; "
                                                "run 'ivpm skills sync'"))
            old = st.resolved.get("dists", {}).get(av.dist) if av.dist else None
            if old and av.version and old != av.version:
                problems.append(Problem("version", sel.dest_name, "",
                                        "%s is %s now, %s when installed"
                                        % (av.dist, av.version, old)))
    return st, problems
