#****************************************************************************
#* fscopy.py
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
"""Tree copies that do not quietly change who can read the result.

``shutil.copytree`` is wrong for cache content in two independent ways, and
both were live:

* Its default ``symlinks=False`` *dereferences* every symlink, so a link
  becomes a copy of its target -- and it raises outright on a dangling link,
  which is why a package with a broken symlink could not be patch-cached at
  all.
* ``copy2`` preserves mode, times and xattrs (including POSIX ACLs on Linux)
  but never touches ownership, so a cross-filesystem copy silently re-groups
  every file to whatever the destination's parent dictates.

:func:`copy_tree` fixes both and, when given a policy, applies it as each node
is created rather than walking the tree again afterwards.
"""
import os
import shutil
import stat

from .protection import ProtectionError, ProtectionPolicy, chgrp


def copy_tree(src: str, dst: str, policy=None) -> None:
    """Recursively copy *src* to *dst*, preserving who can read the result.

    Symlinks are recreated as symlinks -- never followed, never dereferenced,
    and never ``chmod``/``chown``-ed through.  Regular files carry their mode,
    timestamps and extended attributes (POSIX ACLs included).  Group ownership
    is preserved, or set from *policy* when one is in force.

    Raises :class:`ProtectionError` if protection cannot be reproduced.  That
    is deliberate and load-bearing: a copy that succeeds with the wrong group
    is indistinguishable from a correct one afterwards, so the only safe
    failure mode is to not produce it.
    """
    src_st = os.lstat(src)
    if not stat.S_ISDIR(src_st.st_mode):
        raise ProtectionError("%s is not a directory" % src)

    # Created private and widened at the end: until the children exist, the
    # directory's final mode may deny the writes needed to create them.
    os.makedirs(dst, 0o700)
    _copy_children(src, dst, policy)
    _finish_dir(src, dst, src_st, policy)


def _copy_children(src: str, dst: str, policy) -> None:
    for name in sorted(os.listdir(src)):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        st = os.lstat(s)

        if stat.S_ISLNK(st.st_mode):
            os.symlink(os.readlink(s), d)
            # lchown only: a symlink's own mode is meaningless on Linux, and
            # chmod/chown *through* it would modify a file outside this tree.
            _chgrp_link(d, st, policy)
        elif stat.S_ISDIR(st.st_mode):
            os.makedirs(d, 0o700)
            _copy_children(s, d, policy)
            _finish_dir(s, d, st, policy)
        elif stat.S_ISREG(st.st_mode):
            shutil.copyfile(s, d, follow_symlinks=False)
            _finish_file(s, d, st, policy)
        else:
            # Devices, fifos and sockets cannot be faithfully reproduced
            # without privilege.  Skipping one silently would publish an entry
            # that is missing a node its consumer expects.
            raise ProtectionError(
                "cannot cache %s: it is neither a file, directory nor symlink "
                "(mode 0o%o)" % (s, st.st_mode))


def _finish_file(src: str, dst: str, st, policy) -> None:
    # Group before mode: a non-root chown clears setuid/setgid, so setting the
    # mode first would lose those bits again.
    _apply_gid(dst, st, policy)
    # copystat carries mode, times and xattrs -- on Linux that includes
    # system.posix_acl_access, so a file's ACL survives the copy.
    shutil.copystat(src, dst, follow_symlinks=False)
    if policy is not None:
        os.chmod(dst, policy.file_mode)
    _verify(dst, st, policy, is_dir=False)


def _finish_dir(src: str, dst: str, st, policy) -> None:
    _apply_gid(dst, st, policy)
    shutil.copystat(src, dst, follow_symlinks=False)
    if policy is not None:
        os.chmod(dst, policy.dir_mode | stat.S_ISGID)
    _verify(dst, st, policy, is_dir=True)


def _apply_gid(path: str, src_st, policy) -> None:
    chgrp(path, policy.gid if policy is not None else src_st.st_gid)


def _chgrp_link(path: str, src_st, policy) -> None:
    gid = policy.gid if policy is not None else src_st.st_gid
    try:
        if os.lstat(path).st_gid != gid:
            os.lchown(path, -1, gid)
    except OSError as e:
        raise ProtectionError("could not set group on symlink %s: %s"
                              % (path, e)) from e


def _verify(path: str, src_st, policy, *, is_dir: bool) -> None:
    """Confirm the node really came out as intended.

    The check is cheap (one ``lstat`` on a path just written, so it is in
    cache) and it is the only thing standing between a filesystem that quietly
    ignores ``chown`` -- some network mounts do -- and a published entry that
    the wrong people can read.
    """
    st = os.lstat(path)
    want_gid = policy.gid if policy is not None else src_st.st_gid
    if st.st_gid != want_gid:
        raise ProtectionError(
            "%s came out with group %d, expected %d; this filesystem may not "
            "support the requested ownership" % (path, st.st_gid, want_gid))
    if policy is None:
        want = stat.S_IMODE(src_st.st_mode)
    else:
        want = (policy.dir_mode | stat.S_ISGID) if is_dir else policy.file_mode
    if stat.S_IMODE(st.st_mode) != stat.S_IMODE(want):
        raise ProtectionError(
            "%s came out with mode 0o%o, expected 0o%o"
            % (path, stat.S_IMODE(st.st_mode), stat.S_IMODE(want)))
