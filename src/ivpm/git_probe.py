#****************************************************************************
#* git_probe.py
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
"""Active investigation of a failed git authentication.

:mod:`ivpm.git_diagnose` classifies a failure from its stderr and is
guaranteed offline.  This module does the opposite job: after a failure it
*runs read-only checks* -- is ``gh`` installed, is it authenticated, which
credential helper does git actually resolve for this URL, does the token have
access to this repository -- and returns a verdict.  The two are deliberately
separate modules so the offline guarantee over there cannot be eroded from
here.

The decisive check is ``gh api repos/<owner>/<repo>``.  It separates an
*authorization* problem (the token is not authorized for the organization)
from a *credential-plumbing* problem (the token is fine, but git was never
given a way to present it).  Those two look nearly identical in git's output
and have opposite fixes.

**Secrecy rules** (see the design plan's risk section; these are hard rules,
not preferences):

* Never open, read, or parse ``hosts.yml``, ``~/.git-credentials``, or any
  keyring.  ``gh auth status`` and ``gh api`` mask secrets themselves.
* ``~/.netrc`` is checked for *host-entry presence only*; no line from it is
  ever echoed.
* Never capture or log credential-helper stdout -- it is a token by definition.
* Never pass ``--show-token``.
* Every reported field is built from a fixed vocabulary (``configured``,
  ``not configured``, a host name, an account login, a helper name), never by
  transforming file contents.
"""
import dataclasses as dc
import logging
import os
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

_logger = logging.getLogger("ivpm.git_probe")

#: Environment variables that silently override gh's own token store -- the
#: classic "works for me, fails for them" cause.
_GH_TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN",
                 "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")

#: A credential-helper name we are willing to echo: no whitespace, and only
#: characters a helper name/path uses.  Anything else is reported as
#: "configured" without its text.
_HELPER_NAME_RE = re.compile(r"^[!A-Za-z0-9_./-]+$")

#: A scope name: lowercase letters and separators, *no digits*.  gh masks its
#: token in `gh auth status` output, and a masked token cannot match this, so
#: echoing matches cannot leak one.
_SCOPE_RE = re.compile(r"^[a-z][a-z:_-]*$")

_MISSING = "(not set)"


def probe_enabled(args=None) -> bool:
    """False when probing is disabled for this run.

    ``IVPM_GIT_NO_PROBE=1`` or a ``--no-probe`` flag turns it off, for CI and
    airgapped environments where running ``gh`` on a failure is pure latency.
    """
    if os.environ.get("IVPM_GIT_NO_PROBE", "").strip() not in ("", "0", "false"):
        return False
    if args is not None and getattr(args, "no_probe", False):
        return False
    return True


#: Failure classes a probe can actually speak to.  Probing a bad ref or an
#: unresolvable host only adds latency and noise.
_PROBE_WORTHY = ("auth", "sso", "scope", "no_credential")


def probe_worthwhile(url, stderr_text, args=None) -> bool:
    """Whether running a probe for this failure is worth the latency.

    Requires probing to be enabled, the locator to use a transport that has
    credentials at all (a ``file://`` mirror does not), and the failure to be
    auth-shaped.
    """
    if not probe_enabled(args):
        return False
    if _transport(url) not in ("https", "ssh"):
        return False
    from .git_diagnose import classify_git_failure
    return classify_git_failure(stderr_text) in _PROBE_WORTHY


@dc.dataclass
class ProbeResult:
    """The findings, conclusion, and remedies from one probe."""
    checks: List[Tuple[str, str]] = dc.field(default_factory=list)
    verdict: str = ""
    remedies: List[str] = dc.field(default_factory=list)
    transport: str = ""

    def format_checks(self, title: str = "git auth probe") -> str:
        if not self.checks:
            return title + ": (no checks ran)"
        width = max(len(k) for k, _ in self.checks)
        lines = [title + ":"]
        lines.extend("  %-*s : %s" % (width, k, v) for k, v in self.checks)
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "transport": self.transport,
            "checks": [{"check": k, "finding": v} for k, v in self.checks],
            "verdict": self.verdict,
            "remedies": list(self.remedies),
        }


def format_verdict(result: ProbeResult, max_remedies: int = 3) -> List[str]:
    """The tight, normal-verbosity rendering: verdict plus a few remedies.

    The full ``checks`` table is for DEBUG / ``ivpm diagnose git``; a failing
    update gets the conclusion and what to do about it.
    """
    if result is None or not result.verdict:
        return []
    lines = ["probe: " + result.verdict]
    for r in result.remedies[:max_remedies]:
        lines.append("  remedy: " + r)
    return lines


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------

def _run(cmd, timeout=15):
    """Run a read-only diagnostic command: ``(rc, stdout, stderr)``.

    Never raises: a missing executable or a timeout becomes a non-zero rc with
    an explanatory stderr, so one unavailable tool cannot abort a probe.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "%s: not found" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "%s: timed out after %ss" % (cmd[0], timeout)
    except Exception as e:
        return 1, "", "%s: %s" % (cmd[0], e)


def _transport(url: str) -> str:
    u = url or ""
    if u.startswith("https://") or u.startswith("http://"):
        return "https"
    if u.startswith("ssh://") or u.startswith("git@"):
        return "ssh"
    if u.startswith("file://"):
        return "local"
    if "://" not in u:
        return "ssh" if ":" in u.split("/", 1)[0] else "local"
    return "other"


def _ssh_host_port(url: str) -> Tuple[Optional[str], Optional[str]]:
    """``(host, port)`` for an ssh locator; *port* is ``None`` when default.

    An ``ssh://host:222/path`` URL carries a port that scp-form cannot
    express, and probing the default port instead would report "permission
    denied" from an entirely different service than the one git talks to.
    """
    if url.startswith("git@"):
        # scp form: [user@]host:path -- the ':' introduces the path, not a port
        return (url[len("git@"):].split(":", 1)[0] or None), None
    from .utils import url_host
    host = url_host(url)
    port = None
    if url.startswith("ssh://"):
        hostpart = url[len("ssh://"):].split("/", 1)[0].split("@")[-1]
        if ":" in hostpart:
            cand = hostpart.split(":", 1)[1]
            port = cand if cand.isdigit() else None
    return host, port


def _netrc_finding(host) -> str:
    """Whether ``~/.netrc`` exists and names *host* -- presence only.

    This is the quiet cause of the worst version of this failure: with no
    credential helper configured, git's HTTPS transport consults ``~/.netrc``
    via libcurl, so a stale entry there is presented *instead of* gh's token.
    The remote then reports an identity problem rather than a missing
    credential, which sends everyone looking in the wrong place.
    """
    path = os.path.join(os.path.expanduser("~"), ".netrc")
    if not os.path.isfile(path):
        return "no ~/.netrc"
    if not host:
        return "~/.netrc exists"
    try:
        with open(path, "r", errors="replace") as fp:
            # Only a membership test; nothing read here is ever echoed.
            names_host = any(host in ln for ln in fp)
    except OSError:
        return "~/.netrc exists (unreadable)"
    if names_host:
        return ("~/.netrc exists and names this host -- it may shadow the "
                "credential helper")
    return "~/.netrc exists but does not name this host"


def _effective_helper(url, timeout) -> str:
    """The helper git actually resolves for *url*.

    Uses ``--get-urlmatch``, which is the only query that sees the host-scoped
    keys ``gh auth setup-git`` writes (``credential.https://host.helper``).  A
    bare ``--get-all credential.helper`` reports "none configured" for a
    correctly-configured user and sends them to re-run a command they already
    ran.
    """
    rc, out, _ = _run(["git", "config", "--get-urlmatch",
                       "credential.helper", url], timeout=timeout)
    if rc != 0 or not out:
        return "not configured for this URL"
    value = out.splitlines()[0].strip()
    if "gh auth git-credential" in value:
        return "gh (credential.helper=!gh auth git-credential)"
    name = value.split()[0] if value.split() else ""
    if _HELPER_NAME_RE.match(name):
        return "configured: %s" % name
    return "configured (name not reported)"


def _gh_scopes(status_text) -> List[str]:
    """Scope names from ``gh auth status`` output.

    Only tokens matching :data:`_SCOPE_RE` are returned, so nothing
    token-shaped can come out of here even if gh's output format changes.
    """
    for ln in (status_text or "").splitlines():
        low = ln.lower()
        if "token scopes" in low:
            raw = re.split(r"[\s,'\"]+", ln.split(":", 1)[-1])
            return [t for t in raw if t and _SCOPE_RE.match(t)][:20]
    return []


def _gh_token_source(status_text) -> str:
    """``keyring`` / ``plaintext`` / ``unknown``, inferred from gh's own output.

    Never by reading gh's config files (see the module docstring).
    """
    low = (status_text or "").lower()
    if "keyring" in low:
        return "keyring"
    if "token:" in low:
        return "plaintext or keyring (gh does not say)"
    return "unknown"


# --------------------------------------------------------------------------
# The probe
# --------------------------------------------------------------------------

def probe_git_auth(url, stderr_text=None, ssh_pref=None, auth_order=None,
                   timeout=15) -> ProbeResult:
    """Run read-only checks against *url* and return a verdict.

    Bounded: every subprocess is capped at *timeout* seconds, and the whole
    probe targets well under ten.  Never raises -- a check that fails becomes
    a "could not determine" finding.  Never emits secret values.
    """
    try:
        return _probe(url, stderr_text, ssh_pref, auth_order, timeout)
    except Exception as e:                                  # pragma: no cover
        _logger.debug("git auth probe failed", exc_info=True)
        return ProbeResult(
            checks=[("probe", "could not determine (%s)" % type(e).__name__)],
            verdict="", remedies=[])


def _probe(url, stderr_text, ssh_pref, auth_order, timeout) -> ProbeResult:
    from .git_diagnose import classify_git_failure

    transport = _transport(url)
    res = ProbeResult(transport=transport)
    res.checks.append(("url", url or "(none)"))
    res.checks.append(("transport", transport))
    if auth_order:
        res.checks.append(("auth order", ", ".join(auth_order)))

    kind = classify_git_failure(stderr_text) if stderr_text else "unknown"
    if stderr_text:
        res.checks.append(("failure class", kind))

    if transport == "https":
        _probe_https(url, kind, stderr_text, res, timeout)
    elif transport == "ssh":
        _probe_ssh(url, res, timeout, diagnosing=bool(stderr_text))
    else:
        res.checks.append(("credentials", "no credentials are used for a %s "
                                          "locator" % transport))
        res.verdict = ("this is a %s locator, so authentication is not "
                       "involved in the failure" % transport)
    return res


def _probe_https(url, kind, stderr_text, res: ProbeResult, timeout):
    from .cache import is_github_url, parse_github_url
    from .git_diagnose import classify_git_failure, sso_authorization_url
    from .utils import url_host

    host = url_host(url)
    res.checks.append(("host", host or "(none)"))

    # --- gh presence / identity / store ------------------------------------
    gh = shutil.which("gh")
    res.checks.append(("gh", ("installed at %s" % gh) if gh else "not installed"))

    env_override = [n for n in _GH_TOKEN_ENV if os.environ.get(n, "").strip()]
    res.checks.append(("gh token env", ", ".join(
        "%s is set" % n for n in env_override) if env_override
        else "no token environment override"))
    cfg_dir = os.environ.get("GH_CONFIG_DIR", "").strip()
    res.checks.append(("GH_CONFIG_DIR", cfg_dir if cfg_dir else _MISSING))

    gh_authed = False
    gh_status_text = ""
    scopes = []
    if gh and host:
        rc, out, err = _run([gh, "auth", "status", "--hostname", host],
                            timeout=timeout)
        gh_authed = (rc == 0)
        gh_status_text = out + "\n" + err
        res.checks.append(("gh auth", "authenticated for %s" % host if gh_authed
                           else "not authenticated for %s" % (host or "(none)")))
        scopes = _gh_scopes(gh_status_text)
        res.checks.append(("gh token scopes", ", ".join(scopes) if scopes
                           else "not reported"))
        res.checks.append(("gh token source", _gh_token_source(gh_status_text)))
        if gh_authed:
            rc, out, _ = _run([gh, "api", "user", "--jq", ".login"],
                              timeout=timeout)
            res.checks.append(("gh identity", out if rc == 0 and out
                               else "could not determine"))

    # --- what git itself would use ----------------------------------------
    res.checks.append(("effective helper", _effective_helper(url, timeout)))
    res.checks.append(("netrc", _netrc_finding(host)))
    res.checks.append(("askpass/prompt", _prompt_env_finding()))

    # --- the decisive cross-check ------------------------------------------
    repo_state, repo_detail = "not run", ""
    owner = repo = None
    if gh and gh_authed and is_github_url(url):
        owner, repo = parse_github_url(url)
    if owner and repo:
        rc, out, err = _run([gh, "api", "repos/%s/%s" % (owner, repo),
                             "--jq", ".full_name"], timeout=timeout)
        api_kind = classify_git_failure(err)
        if rc == 0 and out:
            repo_state, repo_detail = "ok", out
        elif api_kind == "sso":
            repo_state = "sso"
            repo_detail = sso_authorization_url(err) or ""
        elif "404" in err or api_kind == "not_found":
            repo_state = "not_found"
        elif api_kind == "scope":
            repo_state = "scope"
        else:
            repo_state = "error"
            repo_detail = "gh api exited %d" % rc
        res.checks.append(("gh api repos/%s/%s" % (owner, repo),
                           repo_state + ((" (%s)" % repo_detail)
                                         if repo_detail else "")))

    _https_verdict(res, url, host, kind, stderr_text, gh, gh_authed,
                   repo_state, repo_detail, scopes, env_override,
                   diagnosing=bool(stderr_text))


def _prompt_env_finding() -> str:
    """Which credential-prompt environment variables are in play.

    Names (and ``GIT_TERMINAL_PROMPT``'s 0/1 value) only -- enough to explain
    a silent failure or an unexpected credential source.
    """
    parts = []
    for name in ("GIT_ASKPASS", "SSH_ASKPASS"):
        if os.environ.get(name, "").strip():
            parts.append("%s is set" % name)
    tp = os.environ.get("GIT_TERMINAL_PROMPT", "").strip()
    if tp in ("0", "1"):
        parts.append("GIT_TERMINAL_PROMPT=%s" % tp)
    elif tp:
        parts.append("GIT_TERMINAL_PROMPT is set")
    return ", ".join(parts) if parts else "no askpass/prompt overrides"


def _https_verdict(res, url, host, kind, stderr_text, gh, gh_authed,
                   repo_state, repo_detail, scopes, env_override,
                   diagnosing=True):
    """Turn the https findings into one conclusion and concrete remedies."""
    from .git_diagnose import sso_authorization_url

    helper = dict(res.checks).get("effective helper", "")
    netrc = dict(res.checks).get("netrc", "")
    netrc_shadow = "may shadow" in netrc

    if gh is None:
        res.verdict = ("'gh' is not installed on this machine, so the 'gh' "
                       "auth method cannot supply a credential for %s"
                       % (host or "this host"))
        res.remedies = [
            "install the GitHub CLI (gh) and run 'gh auth login --hostname %s'"
            % (host or "<host>"),
            "or set the auth order to ssh: IVPM_GIT_AUTH_ORDER=ssh",
        ]
        return

    if not gh_authed:
        res.verdict = ("gh is installed but not authenticated for %s, so no "
                       "credential source is configured for this clone"
                       % (host or "this host"))
        res.remedies = ["gh auth login --hostname %s" % (host or "<host>")]
        if env_override:
            res.remedies.append(
                "note %s is set in this environment and overrides gh's own "
                "token store" % env_override[0])
        return

    if repo_state == "ok" and not diagnosing:
        # A preflight, not a post-mortem.
        res.verdict = ("gh's token is valid and authorized for %s; an https "
                       "fetch will authenticate with it" % repo_detail)
        res.remedies = []
        if netrc_shadow:
            res.remedies.append(
                "~/.netrc names %s: with no credential helper configured git "
                "consults it via libcurl, so a stale entry there can be "
                "presented instead of gh's token" % host)
        return

    if repo_state == "ok":
        res.verdict = ("gh's token is valid and authorized for %s, so the "
                       "credential is fine -- git was not able to present it"
                       % repo_detail)
        res.remedies = [
            "ivpm injects gh's credential helper for the 'gh' auth method; "
            "if this persists, run 'gh auth setup-git' so plain git can "
            "authenticate too",
        ]
        if netrc_shadow:
            res.remedies.insert(
                0, "~/.netrc names %s: git consults it via libcurl and may "
                   "present a stale credential from there instead of gh's "
                   "token -- remove or update that entry" % host)
        if "not configured" in helper:
            res.remedies.append(
                "no credential helper resolves for this URL "
                "('git config --get-urlmatch credential.helper %s' is empty)"
                % url)
        return

    if repo_state == "sso" or kind == "sso":
        sso_url = repo_detail or sso_authorization_url(stderr_text)
        res.verdict = ("gh's token is recognized but is not SSO-authorized "
                       "for the organization that owns this repository")
        res.remedies = []
        if sso_url:
            res.remedies.append("authorize the token at %s, then retry" % sso_url)
        res.remedies.append(
            "or mint an authorized token: 'gh auth refresh -h %s -s repo'"
            % (host or "<host>"))
        return

    if repo_state == "not_found":
        res.verdict = ("gh is authenticated, but this repository is not "
                       "visible to that account -- it does not exist, or the "
                       "account has no access to it")
        res.remedies = [
            "verify the owner and repository name in the URL",
            "verify that your account is a member of the owning organization",
        ]
        return

    if repo_state == "scope" or kind == "scope":
        res.verdict = ("gh's token is recognized but lacks the scope this "
                       "operation needs%s"
                       % (" (scopes: %s)" % ", ".join(scopes) if scopes else ""))
        res.remedies = ["gh auth refresh -h %s -s repo" % (host or "<host>")]
        return

    if kind == "no_credential" or "not configured" in helper:
        res.verdict = ("gh is authenticated for %s, but git resolves no "
                       "credential helper for this URL, so nothing presented "
                       "gh's token to the remote" % (host or "this host"))
        res.remedies = ["gh auth setup-git"]
        if netrc_shadow:
            res.remedies.insert(
                0, "~/.netrc names %s and is consulted by git's https "
                   "transport when no helper is configured -- a stale entry "
                   "there is presented instead of gh's token" % host)
        return

    res.verdict = ("gh is authenticated for %s and a credential helper is "
                   "configured; the cause could not be narrowed further"
                   % (host or "this host"))
    res.remedies = ["run 'ivpm diagnose git %s --log-level DEBUG' for the "
                    "full check table" % url]


def _probe_ssh(url, res: ProbeResult, timeout, diagnosing=True):
    """SSH-side checks: agent, keys on disk, and the host's own verdict.

    Lifted from ``GitCloneProvider._log_auth_debug`` so there is one
    implementation of these checks rather than two.
    """
    host, port = _ssh_host_port(url)
    res.checks.append(("host", (("%s:%s" % (host, port)) if port else host)
                       or "(none)"))
    res.checks.append(("SSH_AUTH_SOCK", os.environ.get("SSH_AUTH_SOCK", _MISSING)))

    rc, out, err = _run(["ssh-add", "-l"], timeout=min(timeout, 10))
    agent_keys = [ln.strip() for ln in out.splitlines() if ln.strip()] if rc == 0 else []
    res.checks.append(("ssh-agent keys", "%d loaded" % len(agent_keys)
                       if agent_keys else "none / agent unavailable"))
    for k in agent_keys[:5]:
        res.checks.append(("  agent key", k))

    sshdir = os.path.join(os.path.expanduser("~"), ".ssh")
    found = []
    if os.path.isdir(sshdir):
        for n in ("id_ed25519", "id_rsa", "id_ecdsa", "id_dsa"):
            if os.path.isfile(os.path.join(sshdir, n)):
                found.append(n)
    res.checks.append(("~/.ssh keys", ", ".join(found) if found else "(none found)"))

    ssh_ok = None
    ssh_out = ""
    if host:
        cmd = ["ssh", "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=accept-new"]
        if port:
            cmd += ["-p", port]
        rc, out, err = _run(cmd + ["-T", "git@%s" % host], timeout=timeout)
        ssh_out = (out + " " + err).strip()
        # A git host answers an interactive request with a greeting and a
        # non-zero exit, so rc alone cannot be the verdict.
        low = ssh_out.lower()
        if "successfully authenticated" in low or "logged in as" in low \
                or "welcome to" in low:
            ssh_ok = True
        elif "permission denied" in low:
            ssh_ok = False
        label = "ssh -T git@%s%s" % (host, (" -p %s" % port) if port else "")
        res.checks.append((label,
                           ssh_out.splitlines()[0] if ssh_out
                           else "no output (rc=%d)" % rc))

    if ssh_ok is True and not diagnosing:
        # A preflight, not a post-mortem: report readiness, do not diagnose a
        # failure that has not happened.
        res.verdict = ("SSH authentication to %s succeeds; this transport is "
                       "ready to use" % host)
        res.remedies = []
    elif ssh_ok is True:
        res.verdict = ("SSH authentication to %s succeeds, so the key is fine "
                       "-- the failure is about access to this specific "
                       "repository, not about credentials" % host)
        res.remedies = ["verify the owner/repository name and that your "
                        "account has access to it"]
    elif ssh_ok is False or (not agent_keys and not found):
        res.verdict = ("SSH has no usable key for %s: %s"
                       % (host or "this host",
                          "no keys in the agent and none in ~/.ssh"
                          if (not agent_keys and not found)
                          else "the host refused the keys offered"))
        res.remedies = [
            "load your key: 'ssh-add ~/.ssh/id_ed25519'",
            "or add your public key to your account on %s" % (host or "the host"),
            "or use the https transport: --git-auth-order gh,https",
        ]
    else:
        res.verdict = ("could not confirm SSH authentication to %s"
                       % (host or "this host"))
        res.remedies = ["run 'ssh %s-T git@%s' to see what the host reports"
                        % (("-p %s " % port) if port else "",
                           host or "<host>")]
