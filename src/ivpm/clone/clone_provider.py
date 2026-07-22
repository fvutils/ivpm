#****************************************************************************
#* clone_provider.py
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
"""Core abstractions for pluggable `ivpm clone` source providers.

A clone provider knows how to (a) recognize the locators it handles
(``schemes()`` / ``claim()``), (b) declare any extra CLI options it accepts
(``options()``), and (c) materialize a working tree from a locator into a
target directory (``clone()``).

See clone-source-provider-design.md.
"""
import argparse
import dataclasses as dc
from typing import Callable, List, Optional


class ClaimStrength:
    """How strongly a provider claims a locator.  Resolution picks the highest
    non-``NONE`` tier present; a dedicated scheme match (handled separately in
    the registry) outranks every claim.  A tie *within* the winning tier is an
    ambiguity error."""
    NONE = 0     # not mine
    WEAK = 10    # generic fallback (e.g. "looks like some git URL")
    STRONG = 20  # positively recognized (host/pattern match)
    SCHEME = 30  # matched one of my dedicated schemes (highest; via registry)


@dc.dataclass
class CloneRequest:
    """Everything a provider needs to materialize a tree.  Built by CmdClone."""
    src: str                                  # locator as typed by the user
    target_dir: str                           # absolute path the tree must end up in
    branch: Optional[str]                     # common --branch/ref (may be None)
    # Provider-specific parsed args (from build_arg_parser); None when the
    # provider declared no extra options.
    provider_args: Optional[argparse.Namespace]
    # Progress/logging plumbing (already created by CmdClone).
    event_dispatcher: object                  # UpdateEventDispatcher
    suppress_output: bool
    # The full CLI Namespace, for providers that read common knobs
    # (the git provider reads --ssh/--anonymous/--git-auth-order here).
    args: object
    # PtyRunner-style ``(context, label, secret) -> str`` callback, so a
    # provider that shells out to an interactive tool (e.g. one that prompts
    # for a password) can surface the prompt.
    # None when running non-interactively.  Built by CmdClone from its TUI.
    prompt_callback: Optional[Callable] = None


@dc.dataclass
class CloneRootConfig:
    """Configuration a provider forwards to the post-clone ``ivpm update``.

    A provider's source of truth may know things the checked-out tree does not
    (permissions, a deps-dir, a fallback configuration).  A provider returns
    this on its :class:`CloneResult` so CmdClone can drive the update with data
    that is not present in (or must be merged with) a local ``ivpm.yaml``.
    """
    # Used ONLY when the cloned tree has no ivpm.yaml: a synthesized
    # ``package:`` mapping fed through the normal yaml reader.
    default_package: Optional[dict] = None
    # Merged underneath the effective handler_configs (the local ivpm.yaml wins
    # on conflict).  Applied whether or not the tree has an ivpm.yaml.
    handler_overlay: Optional[dict] = None


@dc.dataclass
class CloneResult:
    """Outcome of a provider's clone()."""
    ok: bool
    resolved_revision: Optional[str] = None   # concrete revision, for logging/manifest
    message: str = ""
    root_config: Optional[CloneRootConfig] = None   # forwarded to post-clone update


@dc.dataclass
class CloneOption:
    """One CLI option a provider accepts.

    The single source of truth that feeds BOTH argparse and the
    ``ivpm show clone-providers`` / ``--help`` renderers, so the documentation
    is generated, never hand-maintained (see ``build_parser_from_options`` and
    ``options_to_paraminfo``)."""
    flags: List[str]                          # e.g. ["-branch"] or ["--node", "-n"]
    dest: str                                 # attribute name on provider_args
    help: str
    required: bool = False
    default: Optional[str] = None
    is_flag: bool = False                     # store_true (boolean switch)
    metavar: Optional[str] = None             # value placeholder for help


def build_parser_from_options(prog: str, options: List[CloneOption]) -> argparse.ArgumentParser:
    """Translate a provider's declarative ``options()`` into an argparse parser.

    ``prog`` is used in the ``--help`` header (e.g. ``ivpm clone myvcs``).  Each
    ``CloneOption`` maps to a single ``add_argument`` call; ``is_flag`` options
    become ``store_true`` switches, the rest take a value."""
    parser = argparse.ArgumentParser(prog=prog, add_help=True)
    for opt in options:
        kwargs = {"dest": opt.dest, "help": opt.help}
        if opt.is_flag:
            kwargs["action"] = "store_true"
            kwargs["default"] = bool(opt.default) if opt.default is not None else False
        else:
            kwargs["required"] = opt.required
            kwargs["default"] = opt.default
            if opt.metavar is not None:
                kwargs["metavar"] = opt.metavar
        parser.add_argument(*opt.flags, **kwargs)
    return parser


def options_to_paraminfo(options: List[CloneOption]) -> list:
    """Project a provider's ``options()`` into ``ParamInfo`` records so
    ``ivpm show clone-providers`` describes exactly what the parser accepts."""
    from ..show.info_types import ParamInfo
    params = []
    for opt in options:
        # Prefer the long flag for display; keep the raw flags in the name so the
        # user sees exactly what to type (e.g. "-branch" vs "--branch").
        name = ", ".join(opt.flags)
        params.append(ParamInfo(
            name=name,
            description=opt.help,
            required=opt.required,
            default=(None if opt.default in (None, False) else str(opt.default)),
            type_hint=("bool" if opt.is_flag else "str"),
        ))
    return params


class CloneProvider:
    """Base class for clone-source providers.

    Subclasses override ``provider_info`` and ``clone`` at minimum, and usually
    one of ``schemes``/``claim`` so the registry can route locators to them."""

    # --- identity / discovery ------------------------------------------ #
    @classmethod
    def provider_info(cls) -> "CloneProviderInfo":
        """Self-description for ``ivpm show clone-providers`` (name, schemes,
        description, params).  Mirrors PkgSourceInfo/HandlerInfo."""
        raise NotImplementedError

    def schemes(self) -> List[str]:
        """Dedicated URL schemes this provider owns, without ``://``
        (e.g. ``['myvcs']``).  A scheme match beats any ``claim()``."""
        return []

    def claim(self, src: str) -> int:
        """Return a ``ClaimStrength`` for a generic (non-owned-scheme) locator.
        Return ``NONE`` to decline.  Providers should be conservative: only the
        built-in git provider returns ``WEAK`` for generic https/git URLs."""
        return ClaimStrength.NONE

    # --- root-project status (optional) --------------------------------- #
    def probe(self, root_dir: str) -> int:
        """Return a ``ClaimStrength`` for an *on-disk working tree* at
        ``root_dir``.

        This is the directory-shaped analogue of :meth:`claim` (which
        recognizes a locator string).  Used only when the lock did not record
        which provider produced the root, so ``ivpm status`` can still discover
        it.  Return ``ClaimStrength.NONE`` (default) to decline.  Be
        conservative -- return ``STRONG`` only for an unmistakable checkout so
        the tiered probe stays unambiguous (a tie omits the root line)."""
        return ClaimStrength.NONE

    def root_status(self, root_dir: str) -> Optional["object"]:
        """Describe the VCS state of the root working tree at ``root_dir`` as a
        ``PkgVcsStatus`` (with ``is_root=True``), or ``None`` if it cannot be
        determined.  Called for the provider selected by
        ``CloneProviderRgy.resolve_root()``.  Must be read-only and fast (local
        queries only)."""
        return None

    # --- CLI (declarative, single source of truth) ---------------------- #
    def options(self) -> List[CloneOption]:
        """Declare this provider's extra CLI options declaratively.  IVPM builds
        the argparse parser from these AND renders them in
        ``ivpm show clone-providers`` / ``--help``.  Return ``[]`` for none."""
        return []

    def build_arg_parser(self) -> Optional[argparse.ArgumentParser]:
        """Return a parser for this provider's extra args, or ``None``.

        The default implementation builds one from ``options()``; most providers
        never override this.  An override is the escape hatch for a CLI that a
        flat option list cannot express (mutually-exclusive groups, etc.); such
        a provider SHOULD still return ``options()`` so ``show`` can describe it."""
        opts = self.options()
        if not opts:
            return None
        return build_parser_from_options(
            "ivpm clone %s" % self.provider_info().name, opts)

    # --- workspace naming ----------------------------------------------- #
    def default_workspace_name(self, src: str) -> Optional[str]:
        """Default directory name to derive from ``src`` when the user gives no
        explicit workspace dir.  Return ``None`` to let CmdClone fall back to
        its basename heuristic."""
        return None

    # --- work ----------------------------------------------------------- #
    def clone(self, req: CloneRequest) -> CloneResult:
        """Materialize the working tree at ``req.target_dir``.  Emit
        UpdateEvents through ``req.event_dispatcher``.  Must NOT run
        ``ivpm update`` -- CmdClone owns that."""
        raise NotImplementedError
