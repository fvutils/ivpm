#****************************************************************************
#* cmd_clone.py
#*
#* Copyright 2025 Matthew Ballance and Contributors
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
"""`ivpm clone` -- provider-agnostic orchestration.

CmdClone resolves the target directory, selects a clone provider (git by
default; see clone-source-provider-design.md), lets the provider materialize the
working tree, then runs `ivpm update` if the result is an IVPM project.  The
git-specific mechanics live in ivpm.clone.git_clone_provider.
"""
import logging
import os

from ..msg import warning
from ..utils import fatal
from ..project_ops import ProjectOps
from ..variables import parse_definitions
from ..update_event import UpdateEventDispatcher
from ..update_tui import create_update_tui, RichUpdateTUI
from ..clone.clone_provider_rgy import CloneProviderRgy
from ..clone.clone_provider import CloneRequest

_logger = logging.getLogger("ivpm.cmd_clone")

# Git-only flags accepted on the top-level `clone` parser during the deprecation
# window (design §11). They properly belong to the git provider's options().
_GIT_ONLY_FLAGS = ("ssh", "anonymous", "git_auth_order")


class CmdClone(object):

    def __call__(self, args):
        src = args.src
        extras = list(getattr(args, '_clone_extras', []) or [])

        # Resolve the provider from src (or an explicit --provider override).
        forced = getattr(args, 'provider', None)
        provider = CloneProviderRgy.inst().resolve(src, forced=forced)

        # Parse provider-specific args from the leftover tokens.  These were
        # partitioned out of argv in __main__ so the common parser never saw
        # them (avoiding collisions like provider '-branch' vs common '-b').
        provider_args = self._parse_provider_args(provider, extras)

        # Git-only-flag compatibility shim / deprecation (design §11).
        self._check_git_only_flags(args, provider)

        # Resolve the target/workspace directory.
        target_dir = self._resolve_target_dir(args, src, provider)

        # Progress plumbing.
        log_level = getattr(args, 'log_level', 'NONE')
        event_dispatcher = UpdateEventDispatcher()
        tui = create_update_tui(log_level)
        event_dispatcher.add_listener(tui)
        suppress_output = isinstance(tui, RichUpdateTUI)

        if isinstance(tui, RichUpdateTUI):
            tui.start()

        # A provider that shells out to an interactive tool can surface a
        # password prompt through this callback, mirroring how `ivpm update`
        # does it.
        prompt_callback = None
        if hasattr(tui, "make_prompt_callback"):
            prompt_callback = tui.make_prompt_callback()

        req = CloneRequest(
            src=src,
            target_dir=target_dir,
            branch=getattr(args, 'branch', None),
            provider_args=provider_args,
            event_dispatcher=event_dispatcher,
            suppress_output=suppress_output,
            args=args,
            prompt_callback=prompt_callback,
        )

        try:
            result = provider.clone(req)
        finally:
            if isinstance(tui, RichUpdateTUI):
                tui.stop()

        if result is None or not result.ok:
            msg = (result.message if result is not None and result.message
                   else "clone failed")
            # A provider that reports a detailed, multi-line explanation owns
            # the message verbatim; a terse one gets the provider attributed.
            if "\n" in msg:
                fatal(msg)
            else:
                fatal("Clone via provider '%s' failed: %s" % (
                    provider.provider_info().name, msg))

        # After cloning, run ivpm update in the new workspace so dependencies
        # are fetched according to options provided.  The provider's result may
        # carry a root_config (handler overlay and/or synthesized package) that
        # the update must honor.
        self._post_clone_update(args, target_dir, result)

        # Record which clone provider produced this workspace so `ivpm status`
        # can describe the root project (design: root-status-design.md §3.1).
        self._stamp_root_record(target_dir, src, provider, result)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _resolve_target_dir(self, args, src, provider):
        wsdir = args.workspace_dir
        here = getattr(args, 'here', False)

        if here:
            if wsdir is not None and os.path.abspath(wsdir) != os.getcwd():
                fatal("--here cannot be combined with an explicit workspace directory ('%s')" % wsdir)
            return os.getcwd()

        if wsdir is None:
            wsdir = provider.default_workspace_name(src)
            if not wsdir:
                # Fall back to the basename heuristic.
                base = os.path.basename(src.rstrip("/"))
                if base.endswith('.git'):
                    base = base[:-4]
                wsdir = base

        target_dir = wsdir if os.path.isabs(wsdir) else os.path.abspath(wsdir)

        if os.path.exists(target_dir) and os.listdir(target_dir):
            # Non-empty existing dir is allowed only for providers that clone
            # in place (git handles this); leave the check to the provider by
            # not failing here would change behavior, so preserve the guard for
            # the common case where the dir must be new/empty.
            if not self._provider_allows_nonempty(provider, target_dir):
                fatal("Workspace directory '%s' already exists and is not empty" % target_dir)
        return target_dir

    def _provider_allows_nonempty(self, provider, target_dir):
        """Git can clone into a non-empty dir or reuse an existing clone; other
        providers get the historical "must be empty" guard unless they opt in."""
        # Git provider handles non-empty targets itself (reuse / clone-in-place).
        if provider.provider_info().name == "git":
            return True
        return bool(getattr(provider, "allows_nonempty_target", False))

    def _parse_provider_args(self, provider, extras):
        parser = provider.build_arg_parser()
        if parser is None:
            if extras:
                fatal("Provider '%s' accepts no options, but got: %s" % (
                    provider.provider_info().name, " ".join(extras)))
            return None
        # argparse errors (bad/unknown token) exit(2) with a provider-scoped prog.
        return parser.parse_args(extras)

    def _check_git_only_flags(self, args, provider):
        """Warn/deprecate the top-level git-only flags (design §11)."""
        passed = [f for f in _GIT_ONLY_FLAGS
                  if getattr(args, f, None) not in (None, False)]
        if not passed:
            return
        pretty = ", ".join("--%s" % f.replace("_", "-") for f in passed)
        if provider.provider_info().name != "git":
            warning("git-only clone flag(s) %s are ignored for provider '%s'" % (
                pretty, provider.provider_info().name))
        else:
            _logger.debug(
                "top-level git flags %s are deprecated; prefer "
                "'ivpm clone --provider git ...' form", pretty)

    def _stamp_root_record(self, target_dir, src, provider, result):
        """Stamp the root clone-provider identity into the lock file.

        Best-effort: only meaningful when the post-clone update wrote a lock
        (i.e. the tree is an IVPM project).  stamp_root_record no-ops when the
        lock is absent, so a non-IVPM clone simply relies on the status probe.

        Works for *bare* workspaces too (no root ``ivpm.yaml``): the deps-dir is
        discovered from the lock, and the provider's forwarded ``root_config``
        (``default_package`` / ``handler_overlay``) is persisted so a later
        ``ivpm update`` can reproduce the driving config.
        """
        from ..proj_info import ProjInfo
        from ..package_lock import stamp_root_record, find_ivpm_deps_dir

        proj_info = ProjInfo.mkFromProj(target_dir)
        if proj_info is not None:
            deps_dir = os.path.join(target_dir, proj_info.deps_dir)
        else:
            deps_dir = find_ivpm_deps_dir(target_dir)
            if deps_dir is None:
                return

        stamp_root_record(
            deps_dir,
            provider=provider.provider_info().name,
            src=src,
            resolved_revision=(result.resolved_revision if result else None),
            root_config=self._serialize_root_config(result),
        )

    @staticmethod
    def _serialize_root_config(result):
        """Project a provider's CloneRootConfig onto a JSON-serializable dict for
        the lock's ``root.config`` block, or None when there is nothing to store."""
        rc = getattr(result, "root_config", None) if result else None
        if rc is None:
            return None
        out = {}
        if getattr(rc, "default_package", None) is not None:
            out["default_package"] = rc.default_package
        if getattr(rc, "handler_overlay", None) is not None:
            out["handler_overlay"] = rc.handler_overlay
        return out or None

    def _post_clone_update(self, args, target_dir, result=None):
        rc = result.root_config if result is not None else None
        handler_overlay = rc.handler_overlay if rc is not None else None
        default_package = rc.default_package if rc is not None else None

        dep_set = getattr(args, 'dep_set', None)
        ivpm_yaml_path = os.path.join(target_dir, "ivpm.yaml")
        cli_overrides = parse_definitions(getattr(args, 'definitions', []))

        if os.path.isfile(ivpm_yaml_path):
            # A real ivpm.yaml drives the update; the provider's overlay is
            # merged underneath it (the local manifest wins on conflict).
            ProjectOps(target_dir).update(
                dep_set=dep_set,
                args=args,
                cli_overrides=cli_overrides,
                handler_overlay=handler_overlay,
            )
        elif default_package is not None:
            # Bare tree (no ivpm.yaml): drive the update from the provider's
            # synthesized config.
            ProjectOps(target_dir).update(
                dep_set=dep_set,
                args=args,
                cli_overrides=cli_overrides,
                default_config=default_package,
                handler_overlay=handler_overlay,
            )
        else:
            if dep_set is not None:
                fatal("Dependency set '%s' specified but no ivpm.yaml exists in cloned project" % dep_set)
            # No ivpm.yaml and no provider config - just skip update
