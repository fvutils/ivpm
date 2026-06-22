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
import logging
import os
import subprocess
import time

from ..utils import fatal, resolve_clone_url, url_host
from ..site_config import resolve_git_auth_order, loaded_config_paths
from ..project_ops import ProjectOps
from ..variables import parse_definitions
from ..update_event import UpdateEvent, UpdateEventType, UpdateEventDispatcher
from ..update_tui import create_update_tui, RichUpdateTUI

_logger = logging.getLogger("ivpm.cmd_clone")


class CmdClone(object):

    def __call__(self, args):
        # Determine workspace directory
        src = args.src
        wsdir = args.workspace_dir

        if wsdir is None:
            # Derive from basename of src (strip trailing .git if present)
            base = os.path.basename(src)
            if base.endswith('.git'):
                base = base[:-4]
            wsdir = base
        
        if os.path.isabs(wsdir):
            target_dir = wsdir
        else:
            target_dir = os.path.abspath(wsdir)
        
        if os.path.exists(target_dir):
            # Allow existing empty directory
            if os.listdir(target_dir):
                fatal("Workspace directory '%s' already exists and is not empty" % target_dir)
        
        # Get log level from args for TUI selection
        log_level = getattr(args, 'log_level', 'NONE')
        
        # Create event dispatcher and TUI for clone progress
        event_dispatcher = UpdateEventDispatcher()
        tui = create_update_tui(log_level)
        event_dispatcher.add_listener(tui)
        
        # Determine if we should suppress output (Rich TUI mode)
        suppress_output = isinstance(tui, RichUpdateTUI)
        
        # Start the TUI if it's Rich-based
        if isinstance(tui, RichUpdateTUI):
            tui.start()
        
        try:
            # Decide if we are handling a Git source. For now, support Git only.
            self._clone_git(src, target_dir, args, event_dispatcher, suppress_output)
        finally:
            # Stop TUI before handing off to update
            if isinstance(tui, RichUpdateTUI):
                tui.stop()

        # After cloning, run ivpm update in the new workspace
        # so dependencies are fetched according to options provided
        dep_set = getattr(args, 'dep_set', None)
        ivpm_yaml_path = os.path.join(target_dir, "ivpm.yaml")
        
        if os.path.isfile(ivpm_yaml_path):
            cli_overrides = parse_definitions(getattr(args, 'definitions', []))
            ProjectOps(target_dir).update(
                dep_set=dep_set,
                args=args,
                cli_overrides=cli_overrides,
            )
        else:
            if dep_set is not None:
                fatal("Dependency set '%s' specified but no ivpm.yaml exists in cloned project" % dep_set)
            # No ivpm.yaml and no dep_set specified - just skip update

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
        """Log authentication diagnostics for the clone at DEBUG level.

        Reports how the URL was selected (forced flag vs. auth order) plus the
        effective transport and the credentials relevant to it: SSH agent/keys
        for git@ URLs, gh and git credential helpers for https.  Visible with
        ``--log-level DEBUG``; the (sub)process probes only run when debug
        logging is actually enabled.
        """
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
            # Connectivity probe for the well-known forges (auth returns non-zero by design)
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

    def _clone_git(self, src, target_dir, args, event_dispatcher, suppress_output):
        # Resolve the clone URL.  Explicit --ssh / --anonymous force a
        # transport; otherwise the configured git auth order (gh/ssh/https)
        # decides — by default preferring gh when authenticated, else SSH.
        ssh_pref = None
        if getattr(args, 'ssh', False):
            ssh_pref = True
        elif getattr(args, 'anonymous', False):
            ssh_pref = False
        auth_order = getattr(args, 'git_auth_order', None)
        url = resolve_clone_url(src, ssh_pref, auth_order)

        self._log_auth_debug(src, url, ssh_pref, auth_order)

        # Signal clone start
        clone_start_time = time.time()
        event_dispatcher.dispatch(UpdateEvent(
            event_type=UpdateEventType.PACKAGE_START,
            package_name="[clone]",
            package_type="git",
            package_src=url
        ))
        
        try:
            git_cmd = ["git", "clone", url, target_dir]
            if suppress_output:
                from ..git_progress import run_git_with_progress
                def _on_progress(msg):
                    event_dispatcher.dispatch(UpdateEvent(
                        event_type=UpdateEventType.HANDLER_TASK_PROGRESS,
                        package_name="[clone]",
                        task_id="git:[clone]",
                        task_name="git",
                        task_message=msg))
                rc = run_git_with_progress(git_cmd + ["--progress"], on_progress=_on_progress)
            else:
                rc = subprocess.run(git_cmd).returncode

            if rc != 0:
                event_dispatcher.dispatch(UpdateEvent(
                    event_type=UpdateEventType.PACKAGE_ERROR,
                    package_name="[clone]",
                    error_message="Git clone failed"
                ))
                fatal("Git command \"%s\" failed" % str(git_cmd))

            # Handle branch selection/creation
            branch = getattr(args, 'branch', None)
            if branch is not None:
                # Determine if remote branch exists
                # First, fetch to ensure remotes are up to date
                if suppress_output:
                    subprocess.run(["git", "fetch", "--all"], cwd=target_dir, 
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.run(["git", "fetch", "--all"], cwd=target_dir)
                
                # Check if origin/branch exists
                have_remote = False
                try:
                    out = subprocess.check_output(["git", "ls-remote", "--heads", "origin", branch], cwd=target_dir)
                    have_remote = (len(out.decode().strip()) > 0)
                except Exception:
                    have_remote = False
                
                if have_remote:
                    # Checkout branch tracking origin/branch
                    if suppress_output:
                        status = subprocess.run(["git", "checkout", "-B", branch, f"origin/{branch}"], 
                                                cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        status = subprocess.run(["git", "checkout", "-B", branch, f"origin/{branch}"], cwd=target_dir)
                else:
                    # Create a new local branch from current HEAD
                    if suppress_output:
                        status = subprocess.run(["git", "checkout", "-b", branch], cwd=target_dir,
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        status = subprocess.run(["git", "checkout", "-b", branch], cwd=target_dir)
                
                if status.returncode != 0:
                    event_dispatcher.dispatch(UpdateEvent(
                        event_type=UpdateEventType.PACKAGE_ERROR,
                        package_name="[clone]",
                        error_message=f"Failed to checkout branch {branch}"
                    ))
                    fatal("Failed to checkout branch %s" % branch)
            
            # Signal clone complete
            event_dispatcher.dispatch(UpdateEvent(
                event_type=UpdateEventType.PACKAGE_COMPLETE,
                package_name="[clone]",
                duration=time.time() - clone_start_time
            ))
        except Exception as e:
            event_dispatcher.dispatch(UpdateEvent(
                event_type=UpdateEventType.PACKAGE_ERROR,
                package_name="[clone]",
                error_message=str(e)
            ))
            raise
