#****************************************************************************
#* clone_provider_rgy.py
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
"""Registry + resolution for pluggable `ivpm clone` providers.

Resolution (see clone-source-provider-design.md §4) uses precedence tiers:
a dedicated ``scheme://`` match beats a ``claim()``; within the winning claim
tier, more than one claimant is an ambiguity error.
"""
import logging
import re
import sys
from typing import Dict, List, Optional

from .clone_provider import CloneProvider, ClaimStrength

_logger = logging.getLogger("ivpm.clone.clone_provider_rgy")

# scheme://  -> capture "scheme" (RFC-3986-ish).  scp-style git@host:path and
# bare/Windows paths have no "://" and so are left for claim().
_SCHEME_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9+.\-]*)://')


def _parse_scheme(src: str) -> Optional[str]:
    m = _SCHEME_RE.match(src)
    return m.group(1).lower() if m else None


class CloneProviderRgy(object):
    _inst = None

    def __init__(self):
        self._providers: List[CloneProvider] = []
        self._name2provider: Dict[str, CloneProvider] = {}
        self._scheme2provider: Dict[str, CloneProvider] = {}
        # Stored, provenance-stamped info per provider name.  provider_info()
        # returns a fresh instance each call (like PkgSourceInfo.source_info),
        # so the registry keeps the canonical, stamped copy for `ivpm show`.
        self._name2info: Dict[str, "CloneProviderInfo"] = {}

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register(self, provider: CloneProvider, provenance: dict = None) -> "CloneProviderInfo":
        info = provider.provider_info()
        name = info.name
        if name in self._name2provider:
            if self._name2provider[name] is provider:
                return self._name2info[name]  # identical duplicate
            raise Exception(
                "Duplicate clone-provider name '%s'" % name)
        for s in provider.schemes():
            s = s.lower()
            if s in self._scheme2provider:
                raise Exception(
                    "Duplicate clone-provider scheme '%s' (providers '%s' and '%s')" % (
                        s,
                        self._scheme2provider[s].provider_info().name,
                        name))
            self._scheme2provider[s] = provider
        if provenance:
            info.origin = provenance.get("origin", info.origin)
            if info.provider is None:
                info.provider = provenance.get("provider")
            if info.version is None:
                info.version = provenance.get("version")
        # Single source of truth: if the provider declared options() but did not
        # pre-populate params, project the options into params so `ivpm show`
        # describes exactly what the parser accepts (design §5.4).
        if not info.params:
            try:
                opts = provider.options()
            except Exception:
                opts = []
            if opts:
                from .clone_provider import options_to_paraminfo
                info.params = options_to_paraminfo(opts)
        self._providers.append(provider)
        self._name2provider[name] = provider
        self._name2info[name] = info
        return info

    # ------------------------------------------------------------------ #
    # Accessors (for `ivpm show clone-providers`)
    # ------------------------------------------------------------------ #
    def all_providers(self) -> List[CloneProvider]:
        return list(self._providers)

    def all_infos(self) -> List["CloneProviderInfo"]:
        return [self._name2info[n] for n in self._name2provider.keys()]

    def info_for(self, name: str) -> Optional["CloneProviderInfo"]:
        return self._name2info.get(name)

    def get(self, name: str) -> Optional[CloneProvider]:
        return self._name2provider.get(name)

    def provider_names(self) -> List[str]:
        return list(self._name2provider.keys())

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #
    def resolve(self, src: str, forced: str = None) -> CloneProvider:
        """Resolve ``src`` to exactly one provider (design §4).

        Precedence: explicit ``--provider`` > dedicated ``scheme://`` >
        strongest ``claim()`` tier.  Ties within the winning claim tier, an
        unknown forced name, an unknown scheme with no claimant, or no match at
        all are fatal.
        """
        from ..utils import fatal

        # 1. Explicit override.
        if forced is not None:
            provider = self._name2provider.get(forced)
            if provider is None:
                fatal("Unknown clone provider '%s'. Known providers: %s "
                      "(see 'ivpm show clone-providers')" % (
                          forced, ", ".join(self.provider_names())))
            return provider

        # 2. Dedicated scheme.
        scheme = _parse_scheme(src)
        if scheme is not None and scheme in self._scheme2provider:
            return self._scheme2provider[scheme]

        # 3. Claim tiers.
        best_tier = ClaimStrength.NONE
        claimants: List[CloneProvider] = []
        for provider in self._providers:
            try:
                tier = provider.claim(src)
            except Exception as e:
                _logger.warning("clone provider '%s' raised in claim(): %s",
                                provider.provider_info().name, e)
                continue
            if tier <= ClaimStrength.NONE:
                continue
            if tier > best_tier:
                best_tier = tier
                claimants = [provider]
            elif tier == best_tier:
                claimants.append(provider)

        if len(claimants) == 1:
            return claimants[0]

        if len(claimants) > 1:
            names = ", ".join(p.provider_info().name for p in claimants)
            fatal("'%s' is claimed by multiple clone providers: %s. "
                  "Use a dedicated scheme (e.g. myvcs://...) or --provider NAME "
                  "to disambiguate (see 'ivpm show clone-providers')." % (
                      src, names))

        # 4. No match.
        fatal("No clone provider recognizes '%s'. "
              "See 'ivpm show clone-providers' for the available providers." % src)

    def resolve_root(self, root_dir: str,
                     recorded_provider: str = None) -> Optional[CloneProvider]:
        """Select the provider that describes the root working tree, or ``None``.

        Precedence (design §5): a provider name recorded in the lock's ``root``
        block wins; otherwise probe every provider and take the strongest
        ``probe()`` tier.  Unlike :meth:`resolve`, this NEVER fatals -- root
        status is informational, so a no-match or an ambiguous tie simply omits
        the root line (returning ``None``).
        """
        # 1. Recorded provider wins.
        if recorded_provider:
            provider = self._name2provider.get(recorded_provider)
            if provider is not None:
                return provider
            _logger.debug("lock recorded clone provider '%s' which is not "
                          "installed; falling back to probe", recorded_provider)

        # 2. Tiered probe.
        best_tier = ClaimStrength.NONE
        contenders: List[CloneProvider] = []
        for provider in self._providers:
            try:
                tier = provider.probe(root_dir)
            except Exception as e:
                _logger.debug("clone provider '%s' raised in probe(): %s",
                              provider.provider_info().name, e)
                continue
            if tier <= ClaimStrength.NONE:
                continue
            if tier > best_tier:
                best_tier = tier
                contenders = [provider]
            elif tier == best_tier:
                contenders.append(provider)

        if len(contenders) == 1:
            return contenders[0]

        if len(contenders) > 1:
            # Ambiguous: omit rather than fatal (status is informational).
            _logger.debug(
                "root probe ambiguous at %s: %s -- omitting root status",
                root_dir,
                ", ".join(p.provider_info().name for p in contenders))
        return None

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def _load(self):
        # Built-in default provider.
        from .git_clone_provider import GitCloneProvider
        self.register(GitCloneProvider())
        self._load_plugins()

    def _load_plugins(self):
        if sys.version_info < (3, 10):
            from importlib_metadata import entry_points
        else:
            from importlib.metadata import entry_points
        from ..show.info_types import ep_registration_kwargs

        for ep in entry_points(group="ivpm.clone_providers"):
            try:
                cls = ep.load()
                provider = cls()
                name = provider.provider_info().name
                # A built-in already registered under this name (e.g. git, which
                # is registered directly AND published as an entry point) takes
                # precedence -- skip the duplicate rather than error.
                if name in self._name2provider:
                    _logger.debug("clone provider '%s' already registered; "
                                  "skipping entry point '%s'", name, ep.name)
                    continue
                # Stamp entry-point provenance so `ivpm show clone-providers`
                # attributes plugins correctly.
                self.register(provider, provenance=ep_registration_kwargs(ep))
                _logger.debug("Loaded clone provider '%s' from entry point", name)
            except Exception as e:
                _logger.warning(
                    "Failed to load clone-provider entry point '%s': %s",
                    ep.name, e)

    @classmethod
    def inst(cls) -> "CloneProviderRgy":
        if cls._inst is None:
            cls._inst = CloneProviderRgy()
            cls._inst._load()
        return cls._inst
