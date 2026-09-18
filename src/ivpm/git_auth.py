#****************************************************************************
#* git_auth.py
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
"""Credential plumbing and decision provenance for git network operations.

:func:`ivpm.utils.resolve_clone_url` decides *which URL* to use; this module
decides *how to authenticate* the resulting git invocation, and records *why*
a particular URL and method were chosen.

Two things live here:

**Credential injection** (:func:`gh_credential_args`, :func:`git_cmd`).  The
``gh`` auth method used to mean only "clone the https URL as-is and hope a
credential helper is configured".  ``gh``'s token store and git's credential
store share nothing: ``gh auth status`` succeeding says nothing about whether
``git clone`` can authenticate.  ``credential.helper=!gh auth git-credential``
is the only bridge between them, so when the ``gh`` method wins we hand git
that bridge explicitly.

**Decision provenance** (:class:`AuthDecision`, the ``*_ex`` resolvers).  The
selection inputs -- url-map rules, the per-host auth order, the ssh override --
all used to discard where they came from, so "why did IVPM clone this over
https?" could not be answered from any log.  The ``_ex`` variants return the
same values plus their source; the plain functions remain as thin wrappers.
"""
import dataclasses as dc
import logging
import shlex
import shutil
from typing import List, Optional, Tuple

_logger = logging.getLogger("ivpm.git_auth")

#: Empty helper entry, which resets any helper inherited from the user's git
#: config so gh's is the only one consulted.  Without it a stale helper (or a
#: ``~/.netrc`` fallback) can still win and present a credential that is not
#: gh's token -- the failure mode that motivated this module.
GH_HELPER_RESET = "credential.helper="

#: Auth-order tokens that name "use the URL as written".
_HTTPS_METHODS = ("https", "anonymous")


def _is_http_url(url: str) -> bool:
    if not url:
        return False
    delim = url.find("://")
    if delim < 0:
        return False
    return url[:delim].lower() in ("http", "https")


def gh_path() -> Optional[str]:
    """Absolute path to the ``gh`` executable, or ``None``.

    The credential helper string is executed by ``/bin/sh``, whose ``PATH``
    need not match IVPM's, so the helper must name gh absolutely.
    """
    return shutil.which("gh")


def gh_credential_args(url: str) -> List[str]:
    """``-c`` arguments that make git authenticate with gh's token, or ``[]``.

    Returns ``['-c', 'credential.helper=', '-c',
    'credential.helper=!<gh> auth git-credential']`` when *url* is an http(s)
    URL on a host ``gh`` is authenticated for.  Returns ``[]`` when ``gh`` is
    not installed, is not authenticated for the host, or the URL is not
    http(s) -- ssh, ``file://``, ``git://`` and local paths never carry an
    https credential.
    """
    if not _is_http_url(url):
        return []
    from . import utils
    host = utils.url_host(url)
    if not host or not utils.gh_auth_available(host):
        return []
    path = gh_path()
    if path is None:
        return []
    return ["-c", GH_HELPER_RESET,
            "-c", "credential.helper=!%s auth git-credential" % shlex.quote(path)]


def auth_method_for(url: str, auth_order: List[str]) -> Optional[str]:
    """Which auth-order token applies to *url* (``gh``/``ssh``/``https``).

    Mirrors :func:`clone_url_candidates`' selection so a caller can tell *why*
    a URL was chosen without re-deriving it.  ``None`` when the order yields
    nothing applicable (the caller then falls back to the SSH rewrite).
    """
    from . import utils
    host = utils.url_host(url)
    for method in (auth_order or []):
        m = str(method).strip().lower()
        if m == "gh":
            if utils.gh_auth_available(host):
                return "gh"
        elif m == "ssh":
            return "ssh"
        elif m in _HTTPS_METHODS:
            return "https"
    return None


def git_cmd(base: List[str], url: str, method: Optional[str] = None) -> List[str]:
    """*base* with credential args injected immediately after ``git``.

    ``git_cmd(["git", "clone", ...], url)`` ->
    ``["git", "-c", ..., "-c", ..., "clone", ...]``.  Config passed with ``-c``
    also propagates to submodule operations, so a submodule over https
    authenticates the same way its superproject did.

    *method* is advisory: when given and not ``gh``, no injection happens even
    for an https URL, so an explicit ``https``/``anonymous`` choice keeps
    meaning "exactly as the user's git config would do it".  When ``None`` the
    URL alone decides (used by call sites that have no selected method, e.g.
    the ls-remote fallback to the mapped URL).
    """
    args = base if isinstance(base, list) else list(base)
    if method is not None and method != "gh":
        return list(args)
    cred = gh_credential_args(url)
    if not cred:
        return list(args)
    return [args[0]] + cred + list(args[1:])


# --------------------------------------------------------------------------
# Candidate generation
# --------------------------------------------------------------------------

@dc.dataclass
class AuthDecision:
    """Why IVPM chose the transport and credentials it did, for one URL.

    A record of a decision *already made*.  It is never allowed to re-derive
    one: re-probing ``gh`` while formatting could observe different cache
    state and report something that is not what actually happened.
    """
    declared_url: str = ""
    mapped_url: str = ""
    url_map_rule: Optional[str] = None      # matched 'from' pattern, else None
    host: Optional[str] = None
    ssh_pref: Optional[bool] = None
    ssh_pref_source: Optional[str] = None   # 'package:ssh' | 'cli:--ssh' | ...
    auth_order: List[str] = dc.field(default_factory=list)
    auth_order_source: str = ""
    steps: List[Tuple[str, str, str]] = dc.field(default_factory=list)
    chosen_method: Optional[str] = None
    effective_url: str = ""
    credential_injection: str = "none"

    def format(self, title: str = "git auth decision") -> str:
        """An aligned multi-line block, in the style of the clone auth debug."""
        rows = [
            ("declared url", self.declared_url),
            ("url-map", self.url_map_rule if self.url_map_rule
                        else "(no rule matched)"),
            ("host", self.host or "(none)"),
            ("ssh override", "none" if self.ssh_pref is None
                             else "%s [%s]" % (self.ssh_pref,
                                               self.ssh_pref_source or "?")),
        ]
        if self.url_map_rule:
            rows.insert(2, ("mapped url", self.mapped_url))
        if self.ssh_pref is None:
            rows.append(("auth order", "%s  [%s]" % (
                ", ".join(self.auth_order) or "(empty)",
                self.auth_order_source)))
        lines = [title + ":"]
        width = max(len(k) for k, _ in rows)
        step_labels = []
        for i, (method, outcome, detail) in enumerate(self.steps, start=1):
            step_labels.append(("step %d %s" % (i, method), outcome, detail))
        width = max([width] + [len(k) for k, _, _ in step_labels])
        tail = [("chosen", self.chosen_method or "(none)"),
                ("effective url", self.effective_url),
                ("credentials", self.credential_injection)]
        width = max([width] + [len(k) for k, _ in tail])

        for k, v in rows:
            lines.append("  %-*s : %s" % (width, k, v))
        for k, outcome, detail in step_labels:
            lines.append("  %-*s : %s%s" % (width, k, outcome,
                                            ("  " + detail) if detail else ""))
        for k, v in tail:
            lines.append("  %-*s : %s" % (width, k, v))
        return "\n".join(lines)

    def as_dict(self) -> dict:
        d = dc.asdict(self)
        d["steps"] = [{"method": m, "outcome": o, "detail": t}
                      for m, o, t in self.steps]
        return d


def _gh_available_traced(host) -> Tuple[bool, str]:
    """``(authenticated, 'probed'|'cached')`` for *host*.

    ``gh_auth_available`` is ``lru_cache``d per host, so the second package on
    a host reuses the first package's answer.  Reporting which is which keeps
    a stale cached positive from reading as a fresh successful probe.
    """
    from . import utils
    fn = utils.gh_auth_available
    info = getattr(fn, "cache_info", None)
    before = info() if info is not None else None
    ok = fn(host)
    how = "probed"
    if before is not None:
        after = info()
        if after.hits > before.hits:
            how = "cached"
    return ok, how


def _credential_injection(method: Optional[str], effective_url: str) -> str:
    """Describe the credential source for the chosen method.

    Deliberately derived from the already-made decision plus
    :func:`shutil.which` -- never from a fresh ``gh`` probe (see
    :class:`AuthDecision`).
    """
    if not _is_http_url(effective_url):
        return "n/a (%s)" % ("ssh" if method == "ssh" else "no credentials")
    if method == "gh":
        path = gh_path()
        if path is not None:
            return "gh-helper:%s" % path
        return "none (gh not on PATH)"
    return "none (relying on user git config)"


def clone_url_candidates_ex(url, ssh_pref, auth_order=None,
                            ssh_pref_source=None,
                            auth_order_source=None
                            ) -> Tuple[List[Tuple[str, str]], AuthDecision]:
    """``([(effective_url, method), ...], decision)`` -- what to try, and why.

    The list is ordered and deduplicated by URL: when two methods resolve to
    the same URL there is nothing to retry, so the second is dropped.  An
    explicit *ssh_pref* short-circuits to a single candidate, because it is an
    override, not a preference.
    """
    from . import utils
    from .site_config import (apply_git_url_map_ex, parse_git_auth_order,
                              resolve_git_auth_order_ex)

    declared = url
    mapped, rule = apply_git_url_map_ex(url)
    host = utils.url_host(mapped)

    dec = AuthDecision(declared_url=declared, mapped_url=mapped,
                       url_map_rule=rule, host=host,
                       ssh_pref=ssh_pref, ssh_pref_source=ssh_pref_source)

    if ssh_pref is True:
        eff = utils.https_to_ssh_url(mapped)
        dec.chosen_method = "ssh"
        dec.steps = [("ssh", "ACCEPTED", "forced by %s"
                      % (ssh_pref_source or "an explicit override"))]
        dec.effective_url = eff
        dec.credential_injection = _credential_injection("ssh", eff)
        return [(eff, "ssh")], dec
    if ssh_pref is False:
        dec.chosen_method = "https"
        dec.steps = [("https", "ACCEPTED", "forced by %s"
                      % (ssh_pref_source or "an explicit override"))]
        dec.effective_url = mapped
        dec.credential_injection = _credential_injection("https", mapped)
        return [(mapped, "https")], dec

    if auth_order is None:
        order, order_source = resolve_git_auth_order_ex(host)
    else:
        order = parse_git_auth_order(auth_order)
        order_source = auth_order_source or "cli:--git-auth-order"
    dec.auth_order = list(order)
    dec.auth_order_source = order_source

    candidates: List[Tuple[str, str]] = []
    seen = set()

    def _add(eff, method):
        if eff in seen:
            return False
        seen.add(eff)
        candidates.append((eff, method))
        if dec.chosen_method is None:
            dec.chosen_method = method
        return True

    # `won` (dec.chosen_method) is the method that *selected* the URL. The
    # methods after it are no part of that selection, but their URLs are what
    # a failed auth attempt falls back to (see PackageGit._clone_to_dir), so
    # they are still collected as retry candidates -- except `gh`, which
    # cannot be offered without probing, and probing here would risk reporting
    # something other than what the selection actually saw.
    exhausted = False
    for method in order:
        m = str(method).strip().lower()
        won = dec.chosen_method
        if m == "gh":
            if won is not None or exhausted:
                # Never probe gh to populate a trace: the answer could differ
                # from the one the selection actually saw.
                dec.steps.append((m, "not evaluated (%s won)" % won,
                                  "not offered as a retry candidate "
                                  "(would require probing gh)"))
                continue
            ok, how = _gh_available_traced(host)
            if ok:
                dec.steps.append((m, "ACCEPTED",
                                  "gh auth status --hostname %s -> rc=0 (%s)"
                                  % (host, how)))
                _add(mapped, "gh")
            else:
                dec.steps.append((m, "rejected",
                                  "gh is not installed or not authenticated "
                                  "for %s (%s)" % (host or "(no host)", how)))
        elif m == "ssh" or m in _HTTPS_METHODS:
            eff = utils.https_to_ssh_url(mapped) if m == "ssh" else mapped
            name = "ssh" if m == "ssh" else "https"
            if exhausted:
                dec.steps.append((m, "not evaluated (%s won)" % won, ""))
                continue
            added = _add(eff, name)
            if won is None:
                dec.steps.append((m, "ACCEPTED", "always applicable (terminal)"))
            else:
                dec.steps.append((m, "not evaluated (%s won)" % won,
                                  "retry candidate: %s" % eff if added
                                  else "same URL as %s" % won))
            # 'ssh'/'https' always apply, so nothing after them can ever be
            # reached -- for selection or for retry.
            exhausted = True
        else:
            # Silently ignoring this is how a typo'd order ("gh, shh") used to
            # degrade to the final SSH fallback with no message at all.
            dec.steps.append((m, "ignored: unrecognized method '%s'" % m, ""))
            _logger.warning("ignoring unrecognized git auth method %r "
                            "(expected one of gh, ssh, https)", m)

    if not candidates:
        # The order yielded nothing applicable: fall back to the SSH rewrite,
        # which is the historical default.
        eff = utils.https_to_ssh_url(mapped)
        _add(eff, "ssh")
        dec.steps.append(("ssh", "ACCEPTED",
                          "fallback: no method in the order applied"))
        dec.chosen_method = "ssh"

    dec.effective_url = candidates[0][0]
    dec.credential_injection = _credential_injection(dec.chosen_method,
                                                     dec.effective_url)
    return candidates, dec


def clone_url_candidates(url, ssh_pref, auth_order=None) -> List[Tuple[str, str]]:
    """Ordered ``[(effective_url, method), ...]`` to try, deduplicated by URL."""
    return clone_url_candidates_ex(url, ssh_pref, auth_order)[0]
