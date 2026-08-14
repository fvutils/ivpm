#****************************************************************************
#* dep_materialize.py
#*
#* Copyright 2018-2024 Matthew Ballance and Contributors
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
"""Making a nested boundary writable.

A package resolved under ``deps-mode: nested`` must host its own deps-dir, so
its directory has to be a real, writable directory. But a cache hit (or a
deps-source hit) materializes it as a *symlink* to shared, read-only content --
and the mode is usually declared in the package's own manifest, which cannot be
read until after it has been materialized.

Rather than thread a "writable" flag down through every source provider, the
resolver **promotes after the fact**: once the effective mode resolves to
``nested``, a symlinked package is replaced by a writable copy of its target.
One mechanism covers cache hits, deps-source links and consumer-declared modes
alike, and no source provider changes at all.

See nested-deps-design.md §6.2 and nested-deps-impl-plan.md P1.
"""
import logging
import os
import shutil
import stat

_logger = logging.getLogger("ivpm.dep_materialize")


def _make_writable(path: str) -> None:
    """Restore owner write bits across *path*.

    Cache entries are stored read-only (``cache._make_readonly``); a copy taken
    out of the cache inherits those cleared bits and must not.
    """
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            p = os.path.join(root, name)
            if os.path.islink(p):
                continue
            try:
                os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR)
            except OSError:
                _logger.debug("Could not restore write bit on %s", p)
    try:
        os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
    except OSError:
        _logger.debug("Could not restore write bit on %s", path)


def promote_to_writable(pkg) -> bool:
    """Ensure *pkg* occupies a real, writable directory it can nest into.

    A no-op unless ``pkg.path`` is a symlink, so this is safe to call
    unconditionally and idempotent across re-runs: on the second ``ivpm
    update`` the boundary is already a real directory and nothing happens.

    Returns True if it converted a symlink into a copy.
    """
    path = getattr(pkg, "path", None)
    if path is None or not os.path.islink(path):
        return False

    target = os.path.realpath(path)
    if not os.path.isdir(target):
        # A dir/file link to a single file cannot host a deps-dir; leave it
        # alone and let the caller's own diagnostics speak.
        return False

    _logger.debug("Promoting nested boundary %s (was a link to %s)",
                  path, target)

    # Copy beside the link, then swap, so an interrupted copy never leaves the
    # package half-materialized under its real name.
    staging = path + ".ivpm-promote"
    if os.path.lexists(staging):
        shutil.rmtree(staging, ignore_errors=True)
    try:
        shutil.copytree(target, staging, symlinks=True)
        _make_writable(staging)
        os.unlink(path)
        os.rename(staging, path)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return True
