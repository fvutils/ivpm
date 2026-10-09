"""Every name IVPM creates under ``<cache>/<pkg>/``, in one place.

A shared cache is used by several IVPM releases at once, and none of them
checks more than the name and an entry manifest before linking a workspace to
a directory.  So the names *are* the correctness argument: a name this release
creates must never be one an older release could compute for something else.
See cache-naming-design.md for the full reasoning; the short version:

* **Alphabet.**  Every name is ``[A-Za-z0-9._-]``.  ``@`` (Perl
  interpolation, Perforce), ``%`` (Perforce, make patterns), ``+`` (Verilog
  ``+incdir+``) and ``~`` (zsh extended globs, MSVC on Windows) are all
  avoided.
* **Shape.**  Releases 2.33-2.40 escape with ``%`` but leave ``[-A-Za-z0-9._+]``
  alone, so they could produce any name in that alphabet.  What they actually
  produce is narrower: all-hex (a commit), ``..._<12 hex>`` (http, gh-rls),
  or ``...+patch.<16 hex>``.  Every form below that only this release
  creates avoids all three shapes: it is never all-hex, never ends in ``_``
  plus exactly 12 hex, and never contains ``+``.

Changing anything here re-keys the cache: entries stored under the old name
are simply never found again (and age out), so it is safe, but costly.
"""
import hashlib
import re

# --- partitions ----------------------------------------------------------

#: A protection partition is ``protect_p<12 hex>``.  The ``p`` before the
#: digest is what keeps it off the ``_<12 hex>`` shape old releases produce.
PARTITION_PREFIX = "protect_p"
_PARTITION_RE = re.compile(r"^protect_p[0-9a-f]{12}$")

#: Partition prefixes of earlier releases (2.36-2.40).  Recognised wherever
#: the cache is scanned or a workspace link is resolved, never created.
LEGACY_PARTITION_PREFIXES = ("@protect.",)


def partition_name(digest_hex: str) -> str:
    return PARTITION_PREFIX + digest_hex[:12]


def is_partition_name(name: str) -> bool:
    """Is *name* a partition directory, current or legacy?"""
    return bool(_PARTITION_RE.match(name)) or \
        name.startswith(LEGACY_PARTITION_PREFIXES)


# --- divergent copies ----------------------------------------------------

#: A divergent copy is ``<key>_alt_<32 hex>``.
ALT_SEP = "_alt_"
_ALT_RE = re.compile(r"^(?P<key>.+)_alt_[0-9a-f]{32}$")

#: The 2.40 spelling, ``<key>~alt~<id>``: recognised, never created.
LEGACY_ALT_MARKER = "~alt~"


def divergent_name(key: str, uuid_hex: str) -> str:
    return key + ALT_SEP + uuid_hex


def is_divergent(name: str) -> bool:
    return bool(_ALT_RE.match(name)) or LEGACY_ALT_MARKER in name


def canonical_key(name: str) -> str:
    """The canonical version key a (possibly divergent) entry name is for."""
    m = _ALT_RE.match(name)
    if m:
        return m.group("key")
    return name.split(LEGACY_ALT_MARKER, 1)[0]


# --- version keys --------------------------------------------------------

#: Patched variants are ``<base key>_patch_<16 hex>`` (see ivpm.patch).
PATCH_SEP = "_patch_"
_PATCHED_RE = re.compile(r"_patch_[0-9a-f]{16}$")

#: An escaped key is ``<stem>_k<24 hex>``.
_ESCAPED_RE = re.compile(r"_k[0-9a-f]{24}$")

_PLAIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PLAIN_MAX = 128
_STEM_MAX = 64
_STEM_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")

#: Cache machinery is named by these; a key containing one would be swept,
#: or read as a sidecar, by this release or an older one.
_MACHINERY_SUBSTRINGS = (".staging.", ".gc.")
_MACHINERY_SUFFIXES = (".meta.json",)
_MACHINERY_NAMES = ("policy.json",)


def is_plain_key(version: str) -> bool:
    """Is *version* stored under its own name?

    Plain keys are shared with every older release, which stores them under
    the same name with the same meaning -- so this is deliberately the common
    case (commit hashes, ``<tag>_<digest>``, ``<etag>_<digest>``).
    """
    if not _PLAIN_RE.match(version) or len(version) > _PLAIN_MAX:
        return False
    if any(s in version for s in _MACHINERY_SUBSTRINGS):
        return False
    if version.endswith(_MACHINERY_SUFFIXES) or version in _MACHINERY_NAMES:
        return False
    # A key must not be mistaken for one of this release's own forms.
    if _ESCAPED_RE.search(version) or _ALT_RE.match(version) \
            or _PARTITION_RE.match(version):
        return False
    return True


def version_key(version: str) -> str:
    """The directory name a cache entry for *version* is stored under.

    A plain key is returned unchanged.  Anything else becomes
    ``<stem>_k<24 hex>``: the stem keeps it recognisable, the digest of the
    *raw* version keeps it unique.  The stem drops ``.``, so it can never
    spell a machinery pattern, and it is bounded, so the name always fits.

    Not idempotent -- an escaped name is not itself plain -- so it is applied
    to versions, never to names read back off disk.
    """
    if not isinstance(version, str) or not version:
        raise ValueError("a cache version key must be a non-empty string")
    if is_plain_key(version):
        return version
    stem = _STEM_UNSAFE.sub("-", version)[:_STEM_MAX].lstrip("-_")
    digest = hashlib.sha256(version.encode("utf-8")).hexdigest()[:24]
    return "%s_k%s" % (stem, digest)


def is_new_form(name: str) -> bool:
    """Is *name* one only this release (2.41+) creates?

    Every entry such a release publishes carries a manifest, so a
    manifest-less directory under one of these names was not published by
    IVPM and must not be served.
    """
    return bool(_ESCAPED_RE.search(name) or _PATCHED_RE.search(name)
                or _ALT_RE.match(name) or _PARTITION_RE.match(name))
