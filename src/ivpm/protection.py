#****************************************************************************
#* protection.py
#*
#* Copyright 2024 Matthew Ballance and Contributors
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
"""Access protection as a *value* a package carries.

Before this existed, a site's group policy lived only as filesystem state on the
prepared deps-dir directory -- which is why the cache could not honor it.  A
cached package is fetched straight into ``<cache>/<pkg>/...`` and never touches
the prepared directory, so the policy simply did not reach the bytes (see
``cache-protection-policy-design.md`` §1).

A :class:`ProtectionPolicy` is the policy expressed as data, resolved once by a
``PackagePreparer`` before anything is written, attached to the package, and
read by whatever ends up creating content.  Three properties matter:

* **It is an identity.**  :meth:`ProtectionPolicy.partition_key` makes the
  policy part of the cache path, so two workspaces wanting the same package
  under different protection get different entries instead of evicting each
  other forever.
* **It is applied at creation, not afterwards.**  Group ownership rides setgid
  inheritance on the partition directory, so it is correct for every fetch path
  with no O(files) ``chgrp`` walk.
* **It never widens.**  Everything here either sets a mode the site asked for
  or removes write permission from whatever is already there.
"""
import dataclasses as dc
import hashlib
import json
import os
import stat
import subprocess
from typing import Optional


#: Attribute a resolved policy is stashed under on a Package.  Private because
#: a policy is not part of the dependency *spec* -- a preparer prepares a
#: location, it does not choose content (pkg-prepare-design §2).
_PKG_ATTR = "_ivpm_protection"

#: Reserved prefix for a partition directory.
#:
#: Leads with ``@`` deliberately.  ``safe_version_key`` percent-escapes every
#: character outside ``[-A-Za-z0-9._+%]``, and ``package_name_problem`` rejects
#: the same set, so no package name and no version key -- however exotic the
#: upstream tag or ETag -- can ever produce a path component starting with
#: ``@``.  The partition namespace is therefore disjoint from content by
#: construction.
#:
#: A plain ``p.`` prefix was not: a release tag literally named ``p.1.0``
#: survives ``safe_version_key`` unchanged, so that entry would have been
#: rejected by ``_is_populated`` forever -- an entry that is published on every
#: run, never read back, and never reported.
PARTITION_PREFIX = "@protect."

#: Every write bit.  Sealing is defined as removing exactly these, so a sealed
#: mode can always be derived from the mode the site asked for.
_WRITE_BITS = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH


class ProtectionError(Exception):
    """A protection policy could not be applied or verified.

    Always fatal to the operation that raised it.  The whole point of the
    policy is who may read the content; proceeding when it could not be applied
    would publish readable-by-the-wrong-group bytes, which is the failure this
    machinery exists to prevent.
    """


@dc.dataclass(frozen=True)
class ProtectionPolicy:
    """The access protection one package's content must carry.

    ``dir_mode`` and ``file_mode`` are the modes content has *while being
    built* -- they include write permission, because a fetch has to write.  The
    seal derives the published mode by removing write bits (:func:`seal_mode`),
    so a site states one mode and gets the writable and read-only forms of it
    consistently.
    """

    gid: int
    dir_mode: int = 0o2770
    file_mode: int = 0o0660
    #: POSIX *default* ACL in ``setfacl`` text form, applied to the partition
    #: directory and inherited by the kernel.  Only needed for named-user or
    #: named-group entries; plain group protection does not require it.
    default_acl: Optional[str] = None
    #: Informational only -- never part of the identity.  A group *name* can
    #: map to different gids on different hosts sharing one NFS cache, so the
    #: numeric gid is what the partition key is built from.
    group_name: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.gid, int) or self.gid < 0:
            raise ProtectionError("protection policy needs a numeric gid, got %r"
                                  % (self.gid,))
        for name in ("dir_mode", "file_mode"):
            mode = getattr(self, name)
            if not isinstance(mode, int) or not 0 <= mode <= 0o7777:
                raise ProtectionError(
                    "protection policy %s must be a mode in 0..0o7777, got %r"
                    % (name, mode))

    # --- identity ---------------------------------------------------------

    def identity(self) -> dict:
        """The fields the partition key is computed from.

        ``group_name`` is deliberately absent: including it would split one
        policy into two partitions across hosts whose group databases spell the
        same gid differently.
        """
        return {
            "gid": self.gid,
            "dir_mode": self.dir_mode,
            "file_mode": self.file_mode,
            "default_acl": self.default_acl,
        }

    def partition_key(self) -> str:
        """The directory name this policy's entries live under.

        A digest rather than the group name, for two reasons.  The whole policy
        has to be in the key -- two consumers wanting the same group under
        different modes would otherwise collide and evict each other.  And
        ``<cache>/<pkg>/`` stays group-writable so users can create their own
        partitions, which makes partition names readable by everyone; a digest
        keeps group names out of that listing.
        """
        blob = json.dumps(self.identity(), sort_keys=True).encode()
        return PARTITION_PREFIX + hashlib.sha256(blob).hexdigest()[:12]

    def describe(self) -> dict:
        """The ``policy.json`` written into the partition, for humans.

        A partition directory named ``@protect.7f3a91c2`` is otherwise unexplainable
        without an index; this makes ``ls`` plus one ``cat`` enough.
        """
        d = dict(self.identity())
        d["dir_mode_octal"] = "0o%o" % self.dir_mode
        d["file_mode_octal"] = "0o%o" % self.file_mode
        d["partition"] = self.partition_key()
        if self.group_name:
            d["group_name"] = self.group_name
        return d

    def __str__(self):
        who = self.group_name or ("gid %d" % self.gid)
        return "group %s, dirs 0o%o, files 0o%o%s" % (
            who, self.dir_mode, self.file_mode,
            ", +default ACL" if self.default_acl else "")

    # --- construction -----------------------------------------------------

    @classmethod
    def for_group(cls, group, dir_mode: int = 0o2770, file_mode: int = 0o0660,
                  default_acl: Optional[str] = None) -> 'ProtectionPolicy':
        """Build a policy from a group *name or gid*.

        The convenience a preparer actually wants: sites express policy as
        ``"server2"``, not as an integer they have to look up themselves.
        """
        gid, name = resolve_group(group)
        return cls(gid=gid, dir_mode=dir_mode, file_mode=file_mode,
                   default_acl=default_acl, group_name=name)


def resolve_group(group):
    """``(gid, name)`` for a group given as a name, a gid, or a numeric string.

    Raises :class:`ProtectionError` with the name that failed -- a typo'd group
    in a site config is otherwise reported as a bare ``KeyError`` from deep
    inside a worker thread.
    """
    import grp
    if isinstance(group, int):
        try:
            return group, grp.getgrgid(group).gr_name
        except (KeyError, OverflowError):
            # A gid with no entry in the group database is legitimate on hosts
            # sharing an NFS cache with a directory they do not resolve.
            return group, None
    text = str(group)
    if text.isdigit():
        return resolve_group(int(text))
    try:
        e = grp.getgrnam(text)
    except KeyError:
        raise ProtectionError(
            "unknown group '%s' in the protection policy; it must exist in "
            "the group database on every host that writes this cache" % text)
    return e.gr_gid, e.gr_name


# --- attaching a policy to a package ---------------------------------------

def set_policy(pkg, policy: Optional[ProtectionPolicy]) -> None:
    setattr(pkg, _PKG_ATTR, policy)


def policy_for(pkg) -> Optional[ProtectionPolicy]:
    """The resolved policy for *pkg*, or None when no preparer set one.

    None is the overwhelmingly common case -- IVPM ships no preparers -- and it
    means "keep the historical layout and preserve whatever modes arrive",
    never "apply a default policy".
    """
    return getattr(pkg, _PKG_ATTR, None)


# --- mode helpers -----------------------------------------------------------

def seal_mode(current_mode: int, policy_mode: Optional[int] = None) -> int:
    """The published mode for a node: the intended mode, minus write.

    With a policy, the intended mode is the site's.  Without one it is whatever
    the content arrived with, so sealing *preserves restrictions* instead of
    imposing a floor -- the previous code forced ``2555`` on every directory,
    which published a ``0750`` tree world-traversable.
    """
    base = current_mode if policy_mode is None else policy_mode
    return stat.S_IMODE(base) & ~_WRITE_BITS


def dir_seal_mode(current_mode: int,
                  policy: Optional[ProtectionPolicy]) -> int:
    return seal_mode(current_mode, None if policy is None else policy.dir_mode)


def file_seal_mode(current_mode: int,
                   policy: Optional[ProtectionPolicy]) -> int:
    return seal_mode(current_mode, None if policy is None else policy.file_mode)


# --- applying a policy to a directory --------------------------------------

def apply_to_dir(path: str, policy: ProtectionPolicy, *,
                 setgid: bool = True) -> None:
    """Put *path* under *policy*: group, mode, and default ACL.

    Ordering is not arbitrary.  ``chown`` clears setuid/setgid on many systems
    when performed by a non-root user, so the group is set *before* the mode;
    doing it the other way round silently drops the setgid bit and with it the
    group inheritance this whole approach depends on.

    *setgid* is what makes the policy free: every file and directory created
    inside this one inherits the group with no walk afterwards.  It is only
    turned off for a staging root that is about to be published as content.
    """
    chgrp(path, policy.gid)
    mode = policy.dir_mode | (stat.S_ISGID if setgid else 0)
    try:
        os.chmod(path, mode)
    except OSError as e:
        raise ProtectionError("could not set mode 0o%o on %s: %s"
                              % (mode, path, e)) from e
    if policy.default_acl:
        _set_default_acl(path, policy.default_acl)


def chgrp(path: str, gid: int, *, follow_symlinks: bool = True) -> None:
    """Set *path*'s group, or explain why it could not be set.

    A failure here is almost always one thing: the invoking user is not a
    member of the group the policy names.  Saying so is the difference between
    a fixable message and a mystery, because the alternative -- proceeding --
    publishes content under the wrong group, which is exactly the bug this
    machinery exists to prevent.
    """
    try:
        st = os.lstat(path)
    except OSError as e:
        raise ProtectionError("could not stat %s: %s" % (path, e)) from e
    if st.st_gid == gid:
        return                       # already correct; do not risk a chown
    try:
        if stat.S_ISLNK(st.st_mode):
            os.lchown(path, -1, gid)
        else:
            os.chown(path, -1, gid, follow_symlinks=follow_symlinks)
    except OSError as e:
        raise ProtectionError(
            "could not set group %s on %s: %s\n"
            "  The user running IVPM must be a member of that group."
            % (_gname(gid), path, e)) from e


def verify_dir(path: str, policy: ProtectionPolicy) -> None:
    """Confirm *path* really carries *policy*, or raise.

    Called after creating a partition directory that another worker may have
    created first.  Trusting a concurrently-created directory without checking
    is how a partition ends up with the right *name* and the wrong protection,
    which is undetectable afterwards -- every entry inside it inherits the
    wrong group and looks perfectly consistent.
    """
    try:
        st = os.lstat(path)
    except OSError as e:
        raise ProtectionError("could not stat %s: %s" % (path, e)) from e
    if st.st_gid != policy.gid:
        raise ProtectionError(
            "%s has group %s but this policy requires %s"
            % (path, _gname(st.st_gid), _gname(policy.gid)))
    if not (st.st_mode & stat.S_ISGID):
        raise ProtectionError(
            "%s is not setgid, so content created in it would not inherit "
            "group %s" % (path, _gname(policy.gid)))
    if stat.S_IMODE(st.st_mode) & 0o777 != policy.dir_mode & 0o777:
        raise ProtectionError(
            "%s has mode 0o%o but this policy requires 0o%o"
            % (path, stat.S_IMODE(st.st_mode) & 0o777, policy.dir_mode & 0o777))


def _gname(gid: int) -> str:
    import grp
    try:
        return "%s(%d)" % (grp.getgrgid(gid).gr_name, gid)
    except (KeyError, OverflowError):
        return "gid %d" % gid


def _set_default_acl(path: str, spec: str) -> None:
    """Apply a POSIX *default* ACL so the kernel inherits it to new content.

    Shelled out to ``setfacl`` rather than encoded by hand: the binary
    ``system.posix_acl_default`` xattr format is platform-specific, and getting
    it subtly wrong produces an ACL that looks applied and grants the wrong
    access.  Only reached when a site configures ``default_acl``; plain group
    protection never needs it.
    """
    try:
        r = subprocess.run(["setfacl", "-d", "-m", spec, path],
                           capture_output=True, text=True)
    except (OSError, ValueError) as e:
        raise ProtectionError(
            "could not run setfacl to apply the default ACL on %s: %s\n"
            "  Install acl(1), or drop 'default_acl' from the policy."
            % (path, e)) from e
    if r.returncode != 0:
        raise ProtectionError(
            "setfacl -d -m %s %s failed: %s"
            % (spec, path, (r.stderr or r.stdout or "").strip()))
