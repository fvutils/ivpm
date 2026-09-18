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
import re

from .cache import is_github_url, parse_github_url
from .utils import url_host


#: The classification vocabulary of :func:`classify_git_failure`.  Ordered
#: most-specific-first, which is also the order the needles are tested in.
GIT_FAILURE_CLASSES = (
    "dns",             # the host did not resolve
    "suspended",       # provider-side account action
    "ref_not_found",   # the repo is reachable; the ref is not
    "sso",             # a recognized identity that lacks SSO authorization
    "scope",           # a recognized token that lacks the required scope
    "no_credential",   # nothing usable ever reached git
    "not_found",       # no such repository (or no access, reported as 404)
    "auth",            # generic refusal
    "unknown",
)

#: Classifications where a *different transport* could plausibly succeed: a
#: user's SSH key may be authorized where their token is not, and vice versa.
_RETRYABLE = ("auth", "sso", "scope", "no_credential")

#: The org/enterprise SSO authorization URL GitHub embeds in its 403 body.
#: Echoing this one line is the whole remedy for an SSO failure.
_SSO_URL_RE = re.compile(
    r"https://[A-Za-z0-9._-]+/(?:enterprises|orgs)/[^\s/]+/"
    r"sso\?authorization_request=[^\s'\"]+")


def classify_git_failure(stderr_text) -> str:
    """Classify a git failure from its stderr alone.

    Returns one of :data:`GIT_FAILURE_CLASSES`.  Pure string inspection: no
    network, no subprocesses, no filesystem.

    The split that matters most is ``sso`` vs. ``no_credential``.  They look
    almost identical -- both are a 403 on an https clone -- and they have
    opposite fixes: one means "your token is fine, authorize it for the org",
    the other means "no credential ever reached git".
    """
    low = (stderr_text or "").lower()

    def _has(*needles):
        return any(n in low for n in needles)

    if _has("could not resolve host", "could not resolve hostname",
            "name or service not known", "temporary failure in name resolution"):
        return "dns"
    if _has("account is suspended", "account has been suspended"):
        return "suspended"
    if _has("remote branch", "reference is not a tree", "couldn't find remote ref",
            "did not match any file(s) known to git"):
        return "ref_not_found"
    if _has("saml sso", "enabled or enforced", "sso?authorization_request",
            "single sign-on"):
        return "sso"
    if _has("resource not accessible by personal access token",
            "insufficient scope", "sso is required"):
        return "scope"
    if _has("invalid username or token",
            "password authentication is not supported"):
        return "no_credential"
    if _has("repository not found", "repository does not exist",
            "cannot find repository", "not found", "does not exist",
            "not a git repository"):
        return "not_found"
    if _has("permission denied", "authentication failed",
            "could not read from remote repository", "invalid username or password",
            "access denied", "terminal prompts disabled"):
        return "auth"
    return "unknown"


def should_retry_other_transport(stderr_text) -> bool:
    """True when a *different* transport could plausibly succeed.

    Only auth-shaped failures qualify.  Retrying SSH after "branch does not
    exist" or a DNS failure wastes time and buries the real error.
    """
    return classify_git_failure(stderr_text) in _RETRYABLE


def sso_authorization_url(stderr_text):
    """The SSO authorization URL embedded in *stderr_text*, or ``None``."""
    m = _SSO_URL_RE.search(stderr_text or "")
    return m.group(0).rstrip(".,);") if m else None


def diagnose_git_failure(url, stderr_text, ssh_pref=None):
    """Return a list of human-readable hint lines for a failed git operation
    on *url*, given the command's captured *stderr_text*.

    The list is ordered most-useful-first and may be empty.  Callers typically
    render each line prefixed with ``hint:``.  No network access is performed.
    """
    kind = classify_git_failure(stderr_text)
    hints = []

    # --- host resolution -------------------------------------------------- #
    if kind == "dns":
        host = url_host(url) or _host_of(url) or url
        hints.append("could not resolve host %r -- check your network "
                     "connection and the host spelling in the URL" % host)
        return hints

    # --- account / access blocked ---------------------------------------- #
    if kind == "suspended":
        hints.append("the remote reports your account is suspended -- this is a "
                     "provider-side account action, not a problem with the URL; "
                     "contact the provider's support to resolve it")
        return hints

    # --- branch / ref not found (checked before the generic "not found") -- #
    if kind == "ref_not_found":
        hints.append("the requested branch/tag/commit was not found on the "
                     "remote -- verify the ref name")
        return hints

    # --- SAML SSO: a known identity that is not authorized for the org ---- #
    if kind == "sso":
        sso_url = sso_authorization_url(stderr_text)
        if sso_url:
            hints.append("the organization enforces SAML SSO and this "
                         "credential is not authorized for it -- visit %s to "
                         "authorize it, then retry" % sso_url)
        else:
            hints.append("the organization enforces SAML SSO and this "
                         "credential is not authorized for it -- authorize your "
                         "token for the organization (the remote's message "
                         "carries the authorization URL), then retry")
        hints.append("your credential IS being recognized; this is an "
                     "authorization step, not a missing credential")
        return hints

    # --- token present but under-scoped ---------------------------------- #
    if kind == "scope":
        host = url_host(url) or _host_of(url) or "the host"
        hints.append("the credential was accepted but lacks the scope this "
                     "operation needs -- for a gh token, 'gh auth refresh -h "
                     "%s -s repo'" % host)
        return hints

    # --- nothing usable reached git --------------------------------------- #
    if kind == "no_credential":
        hints.append("no usable credential was presented to the remote (it "
                     "reports the username/token as invalid, not unauthorized)")
        hints.append("check that a credential helper is configured for this "
                     "host -- 'git config --get-urlmatch credential.helper %s' "
                     "-- or that 'gh auth status' reports this host as logged "
                     "in; note 'gh auth setup-git' writes a host-scoped key "
                     "that a bare 'git config --get credential.helper' does "
                     "not show" % (url or "<url>"))
        return hints

    # --- repository does not exist / not found ---------------------------- #
    if kind == "not_found":
        hints.append("the remote reports the repository does not exist -- "
                     "check the URL for typos in the host, owner, and repo name")
        hints.extend(_url_breakdown(url))
        return hints

    # --- authentication --------------------------------------------------- #
    if kind == "auth":
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
    # scp-style '[user@]host:path' -- git's rule is a ':' before the first '/'.
    head = url.split("/", 1)[0]
    if ":" in head:
        return head.split(":", 1)[0].split("@")[-1] or None
    return None
