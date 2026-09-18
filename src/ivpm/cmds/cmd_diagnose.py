#****************************************************************************
#* cmd_diagnose.py
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
"""``ivpm diagnose git`` -- investigate git transport/credential problems.

Answers "how would IVPM fetch this, and why does it fail?" without having to
trigger a failing update, and gives support one command's output to ask for.
It prints the same :class:`~ivpm.git_auth.AuthDecision` the DEBUG trace emits
during a normal run, then the probe's checks, verdict, and remedies.
"""
import json
import os
import subprocess

from .. import git_auth
from ..msg import fatal, note
from ..site_config import loaded_config_paths


class CmdDiagnose(object):

    def __call__(self, args):
        what = getattr(args, "diagnose_cmd", None)
        if what != "git":
            fatal("usage: ivpm diagnose git <url-or-package-name>")
        return self.diagnose_git(args)

    # ------------------------------------------------------------------ #
    def diagnose_git(self, args):
        url = self._resolve_target(args)

        ssh_pref, ssh_pref_source = None, None
        if getattr(args, "ssh", False):
            ssh_pref, ssh_pref_source = True, "cli:--ssh"
        elif getattr(args, "anonymous", False):
            ssh_pref, ssh_pref_source = False, "cli:--anonymous"

        auth_order = getattr(args, "git_auth_order", None)
        candidates, decision = git_auth.clone_url_candidates_ex(
            url, ssh_pref, auth_order, ssh_pref_source=ssh_pref_source)

        from ..git_probe import probe_enabled, probe_git_auth
        probe = None
        if probe_enabled(args):
            probe = probe_git_auth(decision.effective_url, None, ssh_pref,
                                   decision.auth_order)

        ls_remote = None
        if getattr(args, "ls_remote", False):
            ls_remote = [self._try_ls_remote(u, m) for u, m in candidates]

        if getattr(args, "json", False):
            out = {
                "target": url,
                "decision": decision.as_dict(),
                "candidates": [{"url": u, "method": m} for u, m in candidates],
                "config_files": loaded_config_paths(),
                "probe": probe.as_dict() if probe is not None else None,
                "ls_remote": ls_remote,
            }
            print(json.dumps(out, indent=2))
            return

        print(decision.format("git auth decision"))
        configs = loaded_config_paths()
        if configs:
            print("  config files  : %s" % ", ".join(configs))
        if len(candidates) > 1:
            print("  retry order   : %s" % ", ".join(
                "%s (%s)" % (m, u) for u, m in candidates))

        if probe is not None:
            print("")
            print(probe.format_checks("git auth probe"))
            if probe.verdict:
                print("")
                print("verdict: %s" % probe.verdict)
                for r in probe.remedies:
                    print("  remedy: %s" % r)
        else:
            print("")
            print("probe skipped (--no-probe / IVPM_GIT_NO_PROBE)")

        if ls_remote is not None:
            print("")
            print("ls-remote per candidate:")
            for entry in ls_remote:
                print("  %-6s %s -> %s" % (entry["method"], entry["url"],
                                           entry["result"]))

    # ------------------------------------------------------------------ #
    def _resolve_target(self, args):
        """*target* as a URL: pass a URL through, or look a package name up.

        Resolving a package name the way an update would is the point -- the
        declared URL, not a hand-retyped one, is what carries the url-map rule
        and the host whose auth order is in question.
        """
        target = args.target
        if "://" in target or target.startswith("git@") or os.path.sep in target:
            return target

        project_dir = getattr(args, "project_dir", None) or os.getcwd()
        try:
            from ..utils import load_project_package_info
            infos = load_project_package_info(project_dir)
        except Exception as e:
            fatal("'%s' is not a URL, and the project in %s could not be read "
                  "to look it up as a package name (%s)"
                  % (target, project_dir, e))
            return target

        for info in infos:
            for dep_set in getattr(info, "dep_set_m", {}).values():
                for pkg in getattr(dep_set, "packages", {}).values():
                    if pkg.name == target and getattr(pkg, "url", None):
                        note("package '%s' declares url %s" % (target, pkg.url))
                        return pkg.url
        fatal("'%s' is neither a URL nor a git package declared in %s"
              % (target, project_dir))
        return target

    def _try_ls_remote(self, url, method):
        """One bounded ``git ls-remote`` against a candidate.

        Reports reachability per transport, which is the difference between
        "my credentials are wrong" and "this transport is blocked here"."""
        cmd = git_auth.git_cmd(["git", "ls-remote", "--heads", url], url, method)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as e:
            return {"url": url, "method": method,
                    "result": "could not run git (%s)" % type(e).__name__}
        if r.returncode == 0:
            n = len([ln for ln in (r.stdout or "").splitlines() if ln.strip()])
            return {"url": url, "method": method,
                    "result": "ok (%d heads)" % n, "rc": 0}
        from ..git_diagnose import classify_git_failure
        first = next((ln for ln in (r.stderr or "").splitlines() if ln.strip()),
                     "no output")
        return {"url": url, "method": method,
                "result": "failed (%s): %s" % (classify_git_failure(r.stderr), first),
                "rc": r.returncode}
