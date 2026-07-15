#****************************************************************************
#* git_diagnose.py
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
"""Turn a failed git clone/ls-remote into actionable hints.

The git mechanics report failures tersely ("fatal: repository '...' not
found").  :func:`diagnose_git_failure` classifies that captured stderr and
adds a short, targeted hint about the most likely cause and what to check.

This module is deliberately **offline**: it inspects only the command's own
output and the locator string.  It performs no network access and contacts no
provider, so it never hangs, never sends credentials, and never touches a rate
limit -- it just makes git's terse errors easier to act on.
"""
from .cache import is_github_url, parse_github_url
from .utils import url_host


def diagnose_git_failure(url, stderr_text, ssh_pref=None):
    """Return a list of human-readable hint lines for a failed git operation
    on *url*, given the command's captured *stderr_text*.

    The list is ordered most-useful-first and may be empty.  Callers typically
    render each line prefixed with ``hint:``.  No network access is performed.
    """
    low = (stderr_text or "").lower()
    hints = []

    def _has(*needles):
        return any(n in low for n in needles)

    # --- host resolution -------------------------------------------------- #
    if _has("could not resolve host", "could not resolve hostname",
            "name or service not known", "temporary failure in name resolution"):
        host = url_host(url) or _host_of(url) or url
        hints.append("could not resolve host %r -- check your network "
                     "connection and the host spelling in the URL" % host)
        return hints

    # --- account / access blocked ---------------------------------------- #
    if _has("account is suspended", "account has been suspended"):
        hints.append("the remote reports your account is suspended -- this is a "
                     "provider-side account action, not a problem with the URL; "
                     "contact the provider's support to resolve it")
        return hints

    # --- branch / ref not found (check before the generic "not found") ---- #
    if _has("remote branch", "reference is not a tree", "couldn't find remote ref",
            "did not match any file(s) known to git"):
        hints.append("the requested branch/tag/commit was not found on the "
                     "remote -- verify the ref name")
        return hints

    # --- repository does not exist / not found ---------------------------- #
    if _has("repository not found", "repository does not exist",
            "not found", "does not exist", "not a git repository"):
        hints.append("the remote reports the repository does not exist -- "
                     "check the URL for typos in the host, owner, and repo name")
        hints.extend(_url_breakdown(url))
        return hints

    # --- authentication --------------------------------------------------- #
    if _has("permission denied", "authentication failed",
            "could not read from remote repository", "invalid username or password",
            "access denied", "terminal prompts disabled"):
        hints.append("the remote refused authentication -- for a private "
                     "repository, authenticate first (e.g. 'gh auth login', or "
                     "add an SSH key) or verify you have access")
        if ssh_pref is True or (url or "").startswith("git@"):
            hints.append("this clone uses SSH; confirm 'ssh -T git@%s' succeeds "
                         "and your key is loaded (ssh-add -l)"
                         % (url_host(url) or _host_of(url) or "the host"))
        return hints

    return hints


def _url_breakdown(url):
    """Echo how the URL parses, so a typo in a specific component stands out.

    Offline only: shows what IVPM extracted from the locator (e.g. the GitHub
    owner/repo) without contacting anything.
    """
    if is_github_url(url):
        owner, repo = parse_github_url(url)
        if owner and repo:
            return ["parsed as GitHub owner=%r repo=%r -- confirm both are "
                    "spelled correctly" % (owner, repo)]
    return []


def _host_of(url):
    if not url:
        return None
    if "://" in url:
        return url.split("://", 1)[1].split("/", 1)[0]
    if url.startswith("git@"):
        return url[4:].split(":", 1)[0]
    return None
