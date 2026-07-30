#****************************************************************************
#* git_clone_provider.py
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
"""Built-in git clone provider.

This is the default provider and the fallback for generic URLs.  The git
mechanics here were extracted from ``ivpm.cmds.cmd_clone.CmdClone`` so that
``clone`` dispatch is provider-agnostic; behavior is unchanged.
"""
import logging
import os
import subprocess
import time

from ..msg import note
from ..utils import resolve_clone_url, url_host
from ..site_config import resolve_git_auth_order, loaded_config_paths
from ..update_event import UpdateEvent, UpdateEventType
from .clone_provider import (
    CloneProvider, CloneRequest, CloneResult, CloneOption, ClaimStrength,
    options_to_paraminfo,
)

_logger = logging.getLogger("ivpm.clone.git_clone_provider")


class GitCloneProvider(CloneProvider):
    """Clone a git/GitHub repository.  Default provider; also the fallback for
    generic https/file locators (claimed WEAK so a purpose-built provider wins)."""

    # ------------------------------------------------------------------ #
    # Identity / discovery
    # ------------------------------------------------------------------ #
    @classmethod
    def provider_info(cls):
        from ..show.info_types import CloneProviderInfo
        opts = cls._git_options()
        return CloneProviderInfo(
            name="git",
            description="Clone a git/GitHub repository into a new workspace",
            schemes=[],           # works via claim() + being the default
            is_default=True,
            params=options_to_paraminfo(opts),
            notes=(
                "Recognizes git@host:path, ssh://, git://, and *.git URLs "
                "unambiguously; generic https:// / file:// / local paths are "
                "handled as a fallback.  Without an explicit ssh/anonymous "
                "override, the configured git auth order decides the transport "
                "(gh/ssh/https)."
            ),
        )

    @staticmethod
    def _git_options():
        return [
            CloneOption(
                flags=["--ssh"], dest="ssh", is_flag=True,
                help="Force SSH: rewrite an https:// URL to git@host:path form before cloning"),
            CloneOption(
                flags=["-a", "--anonymous"], dest="anonymous", is_flag=True,
                help="Force HTTPS: clone the URL as written (do not rewrite to SSH)"),
            CloneOption(
                flags=["--git-auth-order"], dest="git_auth_order", metavar="ORDER",
                help="Comma-separated git auth order to try (gh,ssh,https); "
                     "overrides IVPM_GIT_AUTH_ORDER and site config"),
        ]

    def options(self):
        # During the deprecation window these flags are ALSO accepted on the
        # top-level `clone` parser (design §11); CmdClone reads them from
        # req.args.  They are declared here so `ivpm show clone-providers git`
        # and `ivpm clone --provider git --help` describe them as git's own.
        return self._git_options()

    def schemes(self):
        return []

    def claim(self, src: str) -> int:
        s = src.strip()
        # Unambiguous git locators.
        if s.startswith("git@") or s.startswith("ssh://") or s.startswith("git://"):
            return ClaimStrength.STRONG
        # A .git suffix (optionally with a trailing ref fragment) is git.
        base = s.split("#", 1)[0].rstrip("/")
        if base.endswith(".git"):
            return ClaimStrength.STRONG
        # An existing local git working copy.
        if "://" not in s and os.path.isdir(os.path.join(s, ".git")):
            return ClaimStrength.STRONG
        # Generic locators git will happily try, but a purpose-built provider
        # should be able to outrank.
        if (s.startswith("https://") or s.startswith("http://")
                or s.startswith("file://") or "://" not in s):
            return ClaimStrength.WEAK
        return ClaimStrength.NONE

    def default_workspace_name(self, src: str):
        base = os.path.basename(src.rstrip("/"))
        if base.endswith(".git"):
            base = base[:-4]
        return base or None

    # ------------------------------------------------------------------ #
    # Root-project status
    # ------------------------------------------------------------------ #
    def probe(self, root_dir: str) -> int:
        # A real .git (dir for a normal clone, file for a git-worktree checkout)
        # is an unmistakable git checkout.
        git_dir = os.path.join(root_dir, ".git")
        if os.path.isdir(git_dir) or os.path.isfile(git_dir):
            return ClaimStrength.STRONG
        return ClaimStrength.NONE

    def root_status(self, root_dir: str):
        from ..pkg_status import git_working_tree_status
        return git_working_tree_status(root_dir, name="(root)")

    # ------------------------------------------------------------------ #
    # Clone
    # ------------------------------------------------------------------ #
    def clone(self, req: CloneRequest) -> CloneResult:
        """Resolve the clone URL, populate the repo, and handle branch
        selection.  Emits PACKAGE_* events for the TUI."""
        src = req.src
        target_dir = req.target_dir
        event_dispatcher = req.event_dispatcher
        suppress_output = req.suppress_output

        # Effective flags come from EITHER the provider form
        # (`ivpm clone --provider git <url> --ssh`, parsed into provider_args)
        # OR the top-level compat form (`ivpm clone <url> --ssh`, on args).
        ssh, anonymous, auth_order = self._effective_flags(req)

        # Explicit --ssh / --anonymous force a transport; otherwise the
        # configured git auth order decides.
        ssh_pref = None
        if ssh:
            ssh_pref = True
        elif anonymous:
            ssh_pref = False
        url = resolve_clone_url(src, ssh_pref, auth_order)

        self._log_auth_debug(src, url, ssh_pref, auth_order)

        clone_start_time = time.time()
        event_dispatcher.dispatch(UpdateEvent(
            event_type=UpdateEventType.PACKAGE_START,
            package_name="[clone]",
            package_type="git",
            package_src=url))

        try:
            rc = self._populate_repo(src, url, target_dir, event_dispatcher, suppress_output)

            if rc != 0:
                event_dispatcher.dispatch(UpdateEvent(
                    event_type=UpdateEventType.PACKAGE_ERROR,
                    package_name="[clone]",
                    error_message="Git clone failed"))
                return CloneResult(ok=False, message="git clone failed (rc=%d)" % rc)

            branch = req.branch
            if branch is not None:
                rc = self._select_branch(branch, target_dir, event_dispatcher, suppress_output)
                if rc != 0:
                    return CloneResult(ok=False,
                                       message="failed to checkout branch %s" % branch)

            event_dispatcher.dispatch(UpdateEvent(
                event_type=UpdateEventType.PACKAGE_COMPLETE,
                package_name="[clone]",
                duration=time.time() - clone_start_time))
        except Exception as e:
            event_dispatcher.dispatch(UpdateEvent(
                event_type=UpdateEventType.PACKAGE_ERROR,
                package_name="[clone]",
                error_message=str(e)))
            raise

        return CloneResult(ok=True, resolved_revision=self._head(target_dir))

    def _effective_flags(self, req: CloneRequest):
        """Resolve (ssh, anonymous, git_auth_order) from the provider-form args
        (preferred) falling back to the top-level compat args."""
        pa = req.provider_args
        args = req.args
        ssh = bool(getattr(pa, "ssh", False)) or bool(getattr(args, "ssh", False))
        anonymous = (bool(getattr(pa, "anonymous", False))
                     or bool(getattr(args, "anonymous", False)))
        auth_order = getattr(pa, "git_auth_order", None)
        if auth_order is None:
            auth_order = getattr(args, "git_auth_order", None)
        return ssh, anonymous, auth_order

    def _select_branch(self, branch, target_dir, event_dispatcher, suppress_output):
        """Check out an existing origin/<branch> or create a new local branch."""
        # Fetch to ensure remotes are up to date.
        if suppress_output:
            subprocess.run(["git", "fetch", "--all"], cwd=target_dir,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["git", "fetch", "--all"], cwd=target_dir)

        have_remote = False
        try:
            out = subprocess.check_output(
                ["git", "ls-remote", "--heads", "origin", branch], cwd=target_dir)
            have_remote = (len(out.decode().strip()) > 0)
        except Exception:
            have_remote = False

        if have_remote:
            cmd = ["git", "checkout", "-B", branch, "origin/%s" % branch]
        else:
            cmd = ["git", "checkout", "-b", branch]

        if suppress_output:
            status = subprocess.run(cmd, cwd=target_dir,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            status = subprocess.run(cmd, cwd=target_dir)

        if status.returncode != 0:
            event_dispatcher.dispatch(UpdateEvent(
                event_type=UpdateEventType.PACKAGE_ERROR,
                package_name="[clone]",
                error_message="Failed to checkout branch %s" % branch))
        return status.returncode

    @staticmethod
    def _head(target_dir):
        try:
            r = subprocess.run(["git", "rev-parse", "HEAD"],
                               cwd=target_dir, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ #
    # Git mechanics (moved verbatim from CmdClone)
    # ------------------------------------------------------------------ #
    def _transport(self, url):
        """Classify a clone URL's transport: 'ssh', 'https', 'local', or 'other'."""
        if url.startswith("git@") or url.startswith("ssh://"):
            return "ssh"
        if url.startswith("https://") or url.startswith("http://"):
            return "https"
        if url.startswith("file://") or "://" not in url:
            return "local"
        return "other"

    def _run(self, cmd, timeout=15):
        """Run a diagnostic command, returning (ok, combined_output)."""
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            out = (r.stdout or "") + (r.stderr or "")
            return r.returncode == 0, out.strip()
        except FileNotFoundError:
            return False, "%s: not found" % cmd[0]
        except Exception as e:
            return False, "%s: %s" % (cmd[0], e)

    def _log_auth_debug(self, src, url, ssh_pref=None, auth_order=None):
        """Log authentication diagnostics for the clone at DEBUG level."""
        if not _logger.isEnabledFor(logging.DEBUG):
            return

        transport = self._transport(url)
        lines = ["clone auth debug:",
                 "  requested src : %s" % src,
                 "  effective url : %s" % url,
                 "  transport     : %s" % transport]
        if ssh_pref is True:
            lines.append("  selection     : forced SSH (--ssh)")
        elif ssh_pref is False:
            lines.append("  selection     : forced HTTPS (--anonymous)")
        else:
            order = auth_order or resolve_git_auth_order(url_host(src))
            lines.append("  auth order    : %s" % ", ".join(order))
            configs = loaded_config_paths()
            if configs:
                lines.append("  config files  : %s" % ", ".join(configs))

        if transport == "ssh":
            lines.append("  SSH_AUTH_SOCK : %s" % os.environ.get("SSH_AUTH_SOCK", "(not set)"))
            ok, out = self._run(["ssh-add", "-l"], timeout=10)
            if ok:
                lines.append("  ssh-agent keys:")
                lines.extend("    %s" % ln for ln in out.splitlines())
            else:
                lines.append("  ssh-agent keys: none / agent unavailable (%s)" % out)
            sshdir = os.path.expanduser("~/.ssh")
            found = []
            if os.path.isdir(sshdir):
                for n in ("id_ed25519", "id_rsa", "id_ecdsa", "id_dsa"):
                    if os.path.isfile(os.path.join(sshdir, n)):
                        found.append(n)
            lines.append("  ~/.ssh keys   : %s" % (", ".join(found) if found else "(none found)"))
            host = url[4:].split(":", 1)[0] if url.startswith("git@") else None
            if host in ("github.com", "gitlab.com"):
                _, out = self._run(["ssh", "-o", "BatchMode=yes",
                                    "-o", "StrictHostKeyChecking=accept-new",
                                    "-T", "git@%s" % host], timeout=15)
                lines.append("  ssh -T %s:" % host)
                lines.extend("    %s" % ln for ln in out.splitlines())
        elif transport == "https":
            _, out = self._run(["gh", "auth", "status"], timeout=15)
            lines.append("  gh auth status:")
            lines.extend("    %s" % ln for ln in (out.splitlines() or ["(no output)"]))
            _, helpers = self._run(["git", "config", "--get-all", "credential.helper"], timeout=10)
            lines.append("  credential.helper: %s" % (helpers.replace("\n", ", ") if helpers else "(none configured)"))
            if not helpers:
                lines.append("    hint: run 'gh auth setup-git' to use your gh token for https clones")
        else:
            lines.append("  (no auth required for %s transport)" % transport)

        _logger.debug("\n".join(lines))

    def _run_git(self, cmd, event_dispatcher, suppress_output, progress=False, cwd=None):
        """Run a git command, routing progress to the Rich TUI when suppressing
        raw output.  Returns the process exit code."""
        if suppress_output:
            from ..git_progress import run_git_with_progress
            def _on_progress(msg):
                event_dispatcher.dispatch(UpdateEvent(
                    event_type=UpdateEventType.HANDLER_TASK_PROGRESS,
                    package_name="[clone]",
                    task_id="git:[clone]",
                    task_name="git",
                    task_message=msg))
            extra = ["--progress"] if progress else []
            return run_git_with_progress(cmd + extra, cwd=cwd, on_progress=_on_progress)
        else:
            return subprocess.run(cmd, cwd=cwd).returncode

    def _git_url_identity(self, url):
        """Normalize a git URL to a 'host/path' form for loose equality, so the
        ssh and https spellings of the same repo compare equal."""
        u = url.strip()
        for pfx in ("https://", "http://", "ssh://", "git://"):
            if u.startswith(pfx):
                u = u[len(pfx):]
                break
        if u.startswith("git@"):
            u = u[len("git@"):]
        u = u.replace(":", "/", 1)   # scp-style host:path -> host/path
        if u.endswith(".git"):
            u = u[:-4]
        return u.rstrip("/").lower()

    def _populate_repo(self, src, url, target_dir, event_dispatcher, suppress_output):
        """Fetch the repository into ``target_dir``, choosing a strategy based on
        the directory's current state:

          * empty / nonexistent   -> plain 'git clone <url> <target_dir>'
          * already a clone of src -> reuse in place (no clone)
          * non-empty, not a repo  -> clone in place via init/fetch/checkout

        Returns the git exit code (0 on success, including the reuse case).
        """
        from ..utils import fatal
        is_repo = os.path.isdir(os.path.join(target_dir, ".git"))
        non_empty = os.path.isdir(target_dir) and bool(os.listdir(target_dir))

        if is_repo:
            existing = ""
            try:
                existing = subprocess.check_output(
                    ["git", "remote", "get-url", "origin"],
                    cwd=target_dir, text=True).strip()
            except Exception:
                existing = ""
            if existing and self._git_url_identity(existing) not in (
                    self._git_url_identity(url), self._git_url_identity(src)):
                fatal("Directory '%s' already contains a git repository for a different "
                      "source (origin=%s); refusing to reuse it" % (target_dir, existing))
            if not suppress_output:
                note("Reusing existing clone in %s" % target_dir)
            return 0

        if non_empty:
            return self._clone_in_place(url, target_dir, event_dispatcher, suppress_output)

        return self._run_clone(url, target_dir, event_dispatcher, suppress_output)

    @staticmethod
    def _git_reads_as_path(url):
        """True when git would resolve ``url`` against the filesystem rather
        than treating it as a remote locator.  Mirrors git's rule: a colon
        before the first '/' means scp-style ssh, anything else with no scheme
        is a path."""
        if url.startswith("file://"):
            return True
        if "://" in url:
            return False
        return ":" not in url.split("/", 1)[0]

    def _run_clone(self, url, target_dir, event_dispatcher, suppress_output):
        """Run 'git clone <url> <target_dir>' for an empty/absent target.

        git decides local-vs-remote by testing the source against the
        filesystem, and it repeats that test *after* creating the destination
        directory.  So `git clone abc:def <cwd>/abc:def` finds the destination
        git itself just made, clones that empty repo into itself, and exits 0 --
        leaving a bogus workspace instead of failing on the unresolvable host.
        Running the clone from a scratch directory (after absolutizing a source
        that really is a local path) removes the ambiguity: a relative source
        that is not a real path can no longer resolve to the destination.
        """
        import shutil
        import tempfile

        src = url
        if self._git_reads_as_path(url) and not os.path.isabs(url) \
                and not url.startswith("file://") and os.path.exists(url):
            src = os.path.abspath(url)

        scratch = tempfile.mkdtemp(prefix="ivpm-clone-")
        try:
            rc = self._run_git(["git", "clone", src, target_dir],
                               event_dispatcher, suppress_output,
                               progress=True, cwd=scratch)
            if rc == 0 and self._is_self_clone(scratch, target_dir):
                # Backstop for the trap described above, should git ever find
                # another way into it: the clone "succeeded" by copying the
                # destination onto itself.
                shutil.rmtree(target_dir, ignore_errors=True)
                return 1
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return rc

    def _is_self_clone(self, base_dir, target_dir):
        """True when the fresh clone's origin points back at the clone itself."""
        try:
            origin = subprocess.check_output(
                ["git", "remote", "get-url", "origin"],
                cwd=target_dir, text=True).strip()
        except Exception:
            return False
        if not origin or not self._git_reads_as_path(origin):
            return False
        if origin.startswith("file://"):
            origin = origin[len("file://"):]
        resolved = os.path.realpath(os.path.join(base_dir, origin))
        return resolved == os.path.realpath(target_dir)

    def _clone_in_place(self, url, target_dir, event_dispatcher, suppress_output):
        """Clone into an existing, non-empty directory that is not yet a repo.

        'git clone' refuses a non-empty target, so initialise a repo, add the
        remote, fetch, and check out the remote's default branch.
        """
        rc = subprocess.run(["git", "init", "-q", target_dir]).returncode
        if rc != 0:
            return rc
        rc = subprocess.run(["git", "remote", "add", "origin", url], cwd=target_dir).returncode
        if rc != 0:
            return rc
        rc = self._run_git(["git", "fetch", "origin"], event_dispatcher,
                           suppress_output, progress=True, cwd=target_dir)
        if rc != 0:
            return rc

        # Determine the remote's default branch, falling back to main/master.
        default_ref = None
        try:
            out = subprocess.check_output(
                ["git", "remote", "show", "origin"], cwd=target_dir, text=True)
            for ln in out.splitlines():
                ln = ln.strip()
                if ln.startswith("HEAD branch:"):
                    default_ref = ln.split(":", 1)[1].strip()
                    break
        except Exception:
            default_ref = None
        if not default_ref or default_ref == "(unknown)":
            default_ref = None
            for cand in ("main", "master"):
                chk = subprocess.run(["git", "rev-parse", "--verify", "origin/%s" % cand],
                                     cwd=target_dir, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                if chk.returncode == 0:
                    default_ref = cand
                    break
        if not default_ref:
            return 1
        return subprocess.run(
            ["git", "checkout", "-B", default_ref, "origin/%s" % default_ref],
            cwd=target_dir).returncode
