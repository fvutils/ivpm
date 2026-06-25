#****************************************************************************
#* patch.py
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
#*
#* Cache-aware dependency patching -- identity layer (Phase 1).
#*
#* This module is the pure, deterministic core of the patch feature: the
#* declared patch set, its content-addressed identity, the effective-version
#* derivation that turns a patched dependency into an ordinary cache entry,
#* and the on-disk patch manifest used for idempotency and change detection.
#*
#* It performs NO subprocess work and does not mutate dependency trees beyond
#* writing/reading the manifest file. The apply engine, the cache-mode
#* resolver, and the editable reconciliation state machine layer on top of
#* these types in later phases.
#*
#* See patch-cache-design.md and patch-cache-impl-plan.md.
#*
#****************************************************************************
import os
import json
import enum
import shutil
import hashlib
import datetime
import subprocess
import dataclasses as dc
from typing import List, Optional, Tuple


# Separator joining a base version and a patch-set id in an effective version.
# Part of the on-disk cache key -- changing it invalidates patched variants.
PATCH_SEP = "+patch."

# Location of the manifest within a patched tree (relative to the tree root).
PATCH_MANIFEST_REL = os.path.join(".ivpm", "patch-manifest.json")

# Manifest schema version. Bump only with a read-compatibility story.
IVPM_PATCH_VERSION = 1


class PatchError(Exception):
    """A patch could not be applied, or a patched tree could not be reconciled.

    Patch failures are always hard errors (fail-closed): a partially patched
    tree is never stored in the cache or left in ``deps/``.
    """
    pass


class PatchCapability(enum.Enum):
    """How much patching a source provider supports (contract sec. 2).

    A source declares its tier via ``Package.patch_capability()``. The reader
    rejects ``patches:`` on a ``NONE`` source; the resolver refuses the editable
    path for a source that is only ``CACHE`` rather than corrupting a tree.
    """
    NONE = 0       # not patchable (dir/file/module/virtual)
    CACHE = 1      # cache-mode patchable: base_version + fetch_pristine
    EDITABLE = 2   # + editable: retain_base + restore_pristine


def md5_file(path: str) -> str:
    """Return the MD5 hex digest of a file's raw bytes.

    MD5 is used per the explicit requirement that each patch *file* be
    fingerprinted with MD5 (the aggregate patch-set id uses SHA-256 to match
    the lock-file convention -- see ``PatchSet.patchset_id``).
    """
    h = hashlib.md5()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dc.dataclass(frozen=True)
class PatchSpec:
    """One declared patch file, fingerprinted and resolved.

    Frozen: a spec is a value object that participates in the patch-set
    identity. ``name``/``source`` are for display and messages only and are
    deliberately *not* part of ``patchset_id`` -- renaming a patch file without
    changing its bytes/params does not change the identity.
    """
    name: str                          # display/identity label (basename by default)
    source: str                        # path as written in ivpm.yaml (for messages)
    resolved_path: str                 # absolute path on disk
    md5: str                           # MD5 of the patch file's raw bytes
    strip: int = 1                     # -p<N> for git apply / patch
    directory: Optional[str] = None    # apply within this subdir (optional)
    # Optional engine override: "git" | "patch". None => auto-detect (git apply,
    # then GNU patch). Deliberately NOT part of patchset_id -- the resolved tree
    # is identical regardless of which engine produced it, so two consumers that
    # differ only in `tool` must still share one cached variant.
    tool: Optional[str] = None


@dc.dataclass(frozen=True)
class PatchSet:
    """An ordered, immutable collection of patch specs.

    Declaration order is application order and is part of the identity:
    reordering yields a different ``patchset_id``.
    """
    specs: Tuple[PatchSpec, ...] = ()

    @property
    def is_empty(self) -> bool:
        return len(self.specs) == 0

    @property
    def patchset_id(self) -> str:
        """Canonical, order-sensitive digest of the patch set's content+params.

        *** ON-DISK CONTRACT -- DO NOT CHANGE THIS ENCODING. ***

        The id is the SHA-256 of a canonical JSON array, one object per spec in
        declaration order, carrying the file's MD5, strip level, and subdirectory
        (the position ``i`` makes the digest order-sensitive). Any change to the
        key set, key names, ordering, or JSON separators silently invalidates
        every cached patched variant and every recorded manifest. Pinned per
        design sec. 5.2.
        """
        payload = json.dumps(
            [
                {"i": i, "md5": s.md5, "strip": s.strip, "dir": s.directory}
                for i, s in enumerate(self.specs)
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def effective_version(base_version: str, patchset: PatchSet) -> str:
    """Map ``(base_version, patchset)`` to the version string used as the cache key.

    An empty patch set returns the base version *byte-for-byte*, so a build that
    merely *could* be patched shares pristine cache entries with every existing
    consumer (full back-compat, no fragmentation). A non-empty set appends
    ``+patch.`` and the first 16 hex chars of the patch-set id -- collision-safe
    within one package's variant set; the full id lives in the manifest.
    """
    if patchset.is_empty:
        return base_version
    return base_version + PATCH_SEP + patchset.patchset_id[:16]


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (informational only)."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_manifest(tree_dir: str,
                   base_version: str,
                   src_type: str,
                   patchset: PatchSet,
                   base_ref: Optional[dict] = None,
                   result: Optional[list] = None) -> dict:
    """Write ``.ivpm/patch-manifest.json`` into a patched tree and return it.

    The manifest is the on-disk record that makes idempotency and change
    detection work: it records the base, the patch-set id, and every applied
    patch's MD5. ``base_ref`` (optional) names where this tree's pristine comes
    from (cache entry, git commit, or retained archive); ``result`` (optional)
    fingerprints every path the patch set touched, for the cleanliness check in
    later phases. ``applied_at`` is informational and is excluded from every
    equality check (see ``manifest_matches``).
    """
    manifest = {
        "ivpm_patch_version": IVPM_PATCH_VERSION,
        "base_src": src_type,
        "base_version": base_version,
        "patchset_id": patchset.patchset_id,
        "applied": [
            {
                "name": s.name,
                "source": s.source,
                "md5": s.md5,
                "strip": s.strip,
                "directory": s.directory,
            }
            for s in patchset.specs
        ],
        "applied_at": _utc_now_iso(),
    }
    if base_ref is not None:
        manifest["base"] = base_ref
    if result is not None:
        manifest["result"] = result

    path = os.path.join(tree_dir, PATCH_MANIFEST_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fp:
        json.dump(manifest, fp, indent=2, sort_keys=True)
    return manifest


def read_manifest(tree_dir: str) -> Optional[dict]:
    """Read a tree's patch manifest, or return None if it has none."""
    path = os.path.join(tree_dir, PATCH_MANIFEST_REL)
    if not os.path.isfile(path):
        return None
    with open(path, "r") as fp:
        return json.load(fp)


def manifest_matches(manifest: Optional[dict],
                     base_version: str,
                     patchset: PatchSet) -> bool:
    """Return True iff ``manifest`` records exactly ``(base_version, patchset)``.

    This is the idempotency check behind the hot path: when it returns True the
    tree is already base + this patch set and nothing needs to be (re-)applied.

    It compares the recorded base, the patch-set id, and each recorded per-file
    MD5 (in order) against ``patchset``. Because the caller builds ``patchset``
    with freshly computed MD5s, an equal MD5 list means the on-disk patch files
    are unchanged (change detection). ``applied_at`` and ``result[]`` are
    intentionally ignored -- they never affect identity.
    """
    if manifest is None:
        return False
    if manifest.get("base_version") != base_version:
        return False
    if manifest.get("patchset_id") != patchset.patchset_id:
        return False
    recorded = [a.get("md5") for a in manifest.get("applied", [])]
    desired = [s.md5 for s in patchset.specs]
    return recorded == desired


#****************************************************************************
#* Apply engine (Phase 2)
#*
#* Apply an ordered patch set to a writable tree. Fail closed: the first
#* rejected hunk aborts the whole package (the caller deletes any staging
#* tree so a partial variant is never stored). Reject path-traversal patches
#* before either engine runs -- third-party patch files are untrusted.
#****************************************************************************

def _run(cmd, cwd):
    """Run a command, capturing output; return the CompletedProcess."""
    return subprocess.run(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _diff_target_paths(patch_path: str) -> List[str]:
    """Extract candidate target file paths from a unified or context diff.

    Used only by the traversal guard, so it errs toward over-collection: a
    spurious non-path token can never *cause* an escape (it has no ``..``), and
    a real target path is what we must check. ``/dev/null`` (file creation/
    deletion sentinel) is skipped.
    """
    paths = []
    try:
        with open(patch_path, "r", errors="replace") as fp:
            for line in fp:
                for prefix in ("+++ ", "--- ", "*** "):
                    if line.startswith(prefix):
                        rest = line[len(prefix):].split("\t")[0].rstrip("\r\n").strip()
                        if rest and rest != "/dev/null":
                            paths.append(rest)
                        break
    except OSError:
        pass
    return paths


def _strip_leading(path: str, strip: int) -> str:
    parts = path.replace("\\", "/").split("/")
    if strip > 0:
        parts = parts[strip:]
    return "/".join(parts)


def _path_escapes(raw: str, strip: int, cwd: str, boundary: str) -> bool:
    """True if applying ``raw`` (after ``-p<strip>``) would touch a path outside
    ``boundary`` (the package tree root)."""
    rel = _strip_leading(raw, strip)
    if rel in ("", "."):
        return False
    if os.path.isabs(rel):
        return True
    full = os.path.realpath(os.path.join(cwd, rel))
    base = os.path.realpath(boundary)
    return full != base and not full.startswith(base + os.sep)


def _reject_path_traversal(spec: PatchSpec, cwd: str, boundary: str, pkg_name: str) -> None:
    """Raise PatchError if any target path in ``spec`` escapes ``boundary``.

    Engine-independent and fail-loud: it runs before either apply engine, so a
    malicious patch never reaches ``git apply``/``patch`` at all. ``git apply``
    additionally refuses escaping paths on its own (we never pass
    ``--unsafe-paths``); this guard is the authoritative one and the only
    protection on the GNU ``patch`` fallback path.
    """
    for raw in _diff_target_paths(spec.resolved_path):
        if _path_escapes(raw, spec.strip, cwd, boundary):
            raise PatchError(
                "refusing to apply %s to %s: patch targets a path outside the "
                "package tree (%s)" % (spec.name, pkg_name, raw))


def _git_apply(cwd: str, spec: PatchSpec) -> bool:
    """Apply ``spec`` with ``git apply`` in ``cwd``. Return False (do not raise)
    when git cannot apply, so the caller can fall back to GNU ``patch``.

    ``git apply`` validates with ``--check`` before mutating, works inside and
    outside a git repo, and -- crucially -- is NOT given ``--unsafe-paths`` so it
    refuses path-traversal hunks. An already-applied patch is treated as success
    (a clean ``--reverse --check`` proves it is already present), so the engine
    is idempotent.
    """
    base = ["git", "apply", "-p%d" % spec.strip]
    if _run(base + ["--check", spec.resolved_path], cwd).returncode == 0:
        return _run(base + [spec.resolved_path], cwd).returncode == 0
    # Not applicable forward -- is it already applied? (reverse would apply)
    if _run(base + ["--reverse", "--check", spec.resolved_path], cwd).returncode == 0:
        return True
    return False


def _patch_tool(cwd: str, spec: PatchSpec) -> bool:
    """Apply ``spec`` with GNU ``patch`` in ``cwd`` (fallback for context diffs
    that ``git apply`` cannot parse). Return False when ``patch`` is absent or
    cannot apply.

    ``--forward`` makes an already-applied hunk a skip rather than a reverse-
    apply. As with the git engine, an already-applied patch (clean
    ``--reverse --dry-run``) is reported as idempotent success.
    """
    if shutil.which("patch") is None:
        return False
    base = ["patch", "-p%d" % spec.strip]
    fwd = base + ["--forward", "-i", spec.resolved_path]
    if _run(fwd + ["--dry-run"], cwd).returncode == 0:
        return _run(fwd, cwd).returncode == 0
    # Already applied? a clean reverse dry-run proves the content is present.
    if _run(base + ["--reverse", "--dry-run", "-i", spec.resolved_path], cwd).returncode == 0:
        return True
    return False


def apply_patchset(target_dir: str, patchset: PatchSet, base_version: str, pkg) -> None:
    """Apply every spec in ``patchset`` to ``target_dir`` in declaration order.

    ``base_version`` is part of the documented resolver-facing signature and is
    reserved for diagnostics/future use. Raises PatchError on the first spec
    that cannot be applied (fail-closed); the caller is responsible for deleting
    a partially built staging tree.
    """
    pkg_name = getattr(pkg, "name", "?")
    for spec in patchset.specs:
        cwd = os.path.join(target_dir, spec.directory or "")
        _reject_path_traversal(spec, cwd, target_dir, pkg_name)
        tool = spec.tool or "auto"
        if tool in ("auto", "git") and _git_apply(cwd, spec):
            continue
        if tool in ("auto", "patch") and _patch_tool(cwd, spec):
            continue
        raise PatchError(
            "failed to apply %s to %s (hunk rejected, or no usable patch tool)"
            % (spec.name, pkg_name))


#****************************************************************************
#* Cache-mode resolver (Phase 4)
#*
#* Turn a patched dependency into an ordinary cache entry whose version is the
#* effective version (base + patch-set id). Base-first: the pristine base is
#* always cached, and each patched variant is a full copy of it with the patch
#* set applied. Editable (cache-disabled) patching is a later phase -- here a
#* DISABLED lookup is a hard, actionable error.
#****************************************************************************

def _rmtree_if_exists(path: str) -> None:
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def _make_writable(path: str) -> None:
    """Add owner-write to every entry in a tree (cached bases are read-only, so
    a copy of one must be re-opened for writing before patches can apply)."""
    import stat
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            p = os.path.join(root, name)
            try:
                os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR)
            except OSError:
                pass
    try:
        os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
    except OSError:
        pass


def _copy_tree(src: str, dst: str) -> None:
    """Copy a (possibly read-only) cached base into a writable staging dir."""
    shutil.copytree(src, dst)
    _make_writable(dst)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_rel(root: str) -> set:
    """Relative paths of every file under root, excluding the .ivpm metadata dir."""
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        if ".ivpm" in dirnames:
            dirnames.remove(".ivpm")
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            out.add(os.path.relpath(full, root))
    return out


def _diff_tree(base_path: str, staging: str) -> list:
    """Fingerprint every path the patch set added/modified/deleted (manifest
    ``result[]``). Drives the cleanliness check in later phases."""
    base = _walk_rel(base_path)
    stag = _walk_rel(staging)
    result = []
    for rel in sorted(stag - base):
        result.append({"path": rel, "op": "added",
                       "sha256": _sha256_file(os.path.join(staging, rel))})
    for rel in sorted(base - stag):
        result.append({"path": rel, "op": "deleted"})
    for rel in sorted(base & stag):
        sh = _sha256_file(os.path.join(staging, rel))
        if sh != _sha256_file(os.path.join(base_path, rel)):
            result.append({"path": rel, "op": "modified", "sha256": sh})
    return result


class PatchAwareResolver:
    """Coordinate the patch/cache dance for one patched dependency.

    Centralizes lookup -> (base-first) build -> store -> materialize so the
    package types don't duplicate it (mirroring how CacheProvider centralizes
    cache acquisition). The package type calls this only when the patch set is
    non-empty; the resolver assumes non-empty.
    """

    def resolve(self, update_info, pkg, base_version: str):
        from .proj_info import ProjInfo
        from .utils import note

        patchset = pkg.patchset
        deps_dir = update_info.deps_dir
        pkg_dir = os.path.join(deps_dir, pkg.name)
        eff = effective_version(base_version, patchset)

        provider = update_info.get_cache_provider()
        result = provider.lookup(pkg, eff)

        # DISABLED ("uncacheable"): editable in-place patching with reconciliation.
        if result.is_disabled:
            update_info.report_cache_unconfigured()
            return self._apply_in_place(update_info, pkg, pkg_dir, base_version)

        # HIT: the exact patched variant is cached -- never re-apply.
        if result.is_hit:
            note("Cache hit for patched %s (%s)" % (pkg.name, eff))
            provider.materialize(pkg, eff)
            update_info.report_cache_hit()
            return ProjInfo.mkFromProj(pkg_dir)

        # MISS: derive the variant from the cached pristine base.
        update_info.report_cache_miss()
        base_path = self._ensure_base(provider, pkg, base_version, update_info)
        staging = os.path.join(deps_dir, ".patch_stage_%s" % pkg.name)
        _rmtree_if_exists(staging)
        try:
            _copy_tree(base_path, staging)
            apply_patchset(staging, patchset, base_version, pkg)
            write_manifest(
                staging, base_version, getattr(pkg, "src_type", None), patchset,
                base_ref={"kind": "cache", "version": base_version},
                result=_diff_tree(base_path, staging))
        except BaseException:
            _rmtree_if_exists(staging)   # never store a partial variant
            raise
        provider.store(pkg, eff, staging)     # atomic; race-safe; consumes staging
        provider.materialize(pkg, eff)
        return ProjInfo.mkFromProj(pkg_dir)

    def _ensure_base(self, provider, pkg, base_version: str, update_info) -> str:
        """Guarantee the pristine base is a cache entry; return its path. The
        base is fetched at most once no matter how many patch sets target it."""
        res = provider.lookup(pkg, base_version)
        if res.is_hit:
            return res.cached_path
        tmp = os.path.join(update_info.deps_dir, ".patch_base_%s" % pkg.name)
        _rmtree_if_exists(tmp)
        pkg.fetch_pristine(update_info, tmp, base_version)   # network fetch, once
        return provider.store(pkg, base_version, tmp)        # base now shared, RO

    def _apply_in_place(self, update_info, pkg, pkg_dir, base_version):
        """Editable (cache-disabled) patching: classify the on-disk tree and run
        the reconciliation state machine (design sec. 5.12)."""
        patchset = pkg.patchset
        state = classify(pkg_dir, base_version, patchset, pkg)
        return reconcile(state, update_info, pkg, pkg_dir, base_version, patchset)


#****************************************************************************
#* Editable re-establish (Phase 5)
#*
#* Restore an editable tree to pristine (via the source provider's
#* restore_pristine), then re-apply the patch set and rewrite the manifest.
#* Used by the editable reconciliation state machine (Phase 6) for the rows
#* that change or remove patches on a clean tree.
#****************************************************************************

def _snapshot_tree(root: str) -> dict:
    """Map every file (excluding .ivpm) under root to its sha256."""
    return {rel: _sha256_file(os.path.join(root, rel)) for rel in _walk_rel(root)}


def _diff_snapshot(before: dict, root: str) -> list:
    """Build a manifest result[] from a before-snapshot and the current tree."""
    after = _snapshot_tree(root)
    result = []
    for rel in sorted(set(after) - set(before)):
        result.append({"path": rel, "op": "added", "sha256": after[rel]})
    for rel in sorted(set(before) - set(after)):
        result.append({"path": rel, "op": "deleted"})
    for rel in sorted(set(before) & set(after)):
        if before[rel] != after[rel]:
            result.append({"path": rel, "op": "modified", "sha256": after[rel]})
    return result


def _reestablish(update_info, pkg, pkg_dir: str, base_version: str, patchset: PatchSet):
    """Restore pkg_dir to pristine, re-apply patchset, rewrite the manifest.

    Errors (fatal) when the source provider cannot SAFELY restore pristine
    (unrelated edits, or no retained base) -- never discards user work silently.
    The rewritten manifest's result[] is computed by snapshotting the restored
    pristine tree and diffing after apply, so it is exact for any source type.
    """
    from .utils import fatal
    from .proj_info import ProjInfo

    # The base provenance does not change on a patch-set change -- preserve the
    # existing manifest's base block, falling back to one retain_base recorded.
    existing = read_manifest(pkg_dir)
    base_ref = getattr(pkg, "_base_ref", None)
    if base_ref is None and existing is not None:
        base_ref = existing.get("base")

    if not pkg.restore_pristine(pkg_dir, base_version, update_info):
        fatal("cannot re-establish patched package %s: pristine base not "
              "restorable (unrelated local edits, or missing retained base). "
              "Remove %s and re-run ivpm update." % (pkg.name, pkg_dir))

    before = _snapshot_tree(pkg_dir)
    apply_patchset(pkg_dir, patchset, base_version, pkg)
    result = _diff_snapshot(before, pkg_dir)
    write_manifest(pkg_dir, base_version, getattr(pkg, "src_type", None),
                   patchset, base_ref=base_ref, result=result)
    return ProjInfo.mkFromProj(pkg_dir)


#****************************************************************************
#* Editable reconciliation state machine (Phase 6)
#*
#* Every ivpm update reconciles the on-disk state of an editable deps/<pkg>
#* with the desired (base_version, patchset). classify() reports the on-disk
#* state; reconcile() applies the design's complete transition matrix (sec.
#* 5.12). The governing safety invariant: IVPM mutates an editable tree only
#* when its sole deviation from the recorded base is IVPM's own patch
#* application -- any other deviation is drift and is refused, not auto-fixed.
#****************************************************************************

class PatchState(enum.Enum):
    ABSENT = "absent"                  # pkg_dir does not exist
    PRISTINE = "pristine"              # no manifest; unpatched, no detectable edits
    DIRTY_PRISTINE = "dirty_pristine"  # no manifest; user-modified (git-only)
    PATCHED_CLEAN = "patched_clean"    # manifest; exactly base + recorded patches
    PATCHED_DRIFT = "patched_drift"    # manifest; deviates from base + recorded


@dc.dataclass
class _State:
    kind: PatchState
    manifest: Optional[dict] = None


def _is_patched_clean(pkg, pkg_dir: str, base_version: str, manifest: dict) -> bool:
    """A patched tree is clean iff (a) every recorded result[] path still matches
    its recorded fingerprint, (b) no path outside the recorded set deviates from
    base (type-specific), and (c) the recorded base equals the desired base."""
    if manifest.get("base_version") != base_version:
        return False                                   # (c)
    result = manifest.get("result", [])
    for r in result:                                   # (a)
        p = os.path.join(pkg_dir, r["path"])
        if r.get("op") == "deleted":
            if os.path.exists(p):
                return False
        else:
            if not os.path.isfile(p) or _sha256_file(p) != r.get("sha256"):
                return False
    allowed = set(r["path"] for r in result)           # (b)
    return pkg.patch_tree_status(pkg_dir, base_version, allowed) == "clean"


def classify(pkg_dir: str, base_version: str, patchset: PatchSet, pkg) -> _State:
    """Report the on-disk state of an editable pkg_dir (design sec. 5.12)."""
    if not (os.path.exists(pkg_dir) or os.path.islink(pkg_dir)):
        return _State(PatchState.ABSENT)
    manifest = read_manifest(pkg_dir)
    if manifest is None:
        # No manifest: pristine or (git-only) dirty-pristine. An archive tree's
        # cleanliness is unknowable without a base snapshot -> always pristine.
        if pkg.working_tree_dirty(pkg_dir) is True:
            return _State(PatchState.DIRTY_PRISTINE)
        return _State(PatchState.PRISTINE)
    if _is_patched_clean(pkg, pkg_dir, base_version, manifest):
        return _State(PatchState.PATCHED_CLEAN, manifest)
    return _State(PatchState.PATCHED_DRIFT, manifest)


def _delete_ivpm(pkg_dir: str) -> None:
    d = os.path.join(pkg_dir, ".ivpm")
    if os.path.isdir(d):
        shutil.rmtree(d)


def _first_editable_apply(update_info, pkg, pkg_dir, base_version, patchset):
    """Rows 2 & 4: retain the base, apply the patch set, write the manifest."""
    from .proj_info import ProjInfo
    pkg.retain_base(pkg_dir, base_version, update_info)
    before = _snapshot_tree(pkg_dir)
    apply_patchset(pkg_dir, patchset, base_version, pkg)
    result = _diff_snapshot(before, pkg_dir)
    write_manifest(pkg_dir, base_version, getattr(pkg, "src_type", None),
                   patchset, base_ref=getattr(pkg, "_base_ref", None),
                   result=result)
    return ProjInfo.mkFromProj(pkg_dir)


_DRIFT_ERROR = (
    "package %s has local modifications beyond its applied patches; refusing to "
    "re-establish (this would discard your changes). Your tree is left untouched.\n"
    "  - to discard the changes and re-establish: remove '%s', then re-run "
    "'ivpm update'")


def reconcile(state, update_info, pkg, pkg_dir, base_version, patchset):
    """Apply the editable transition matrix (design sec. 5.12, rows 1-12)."""
    from .utils import fatal
    from .proj_info import ProjInfo

    kind = state.kind
    manifest = state.manifest
    unpatched = patchset.is_empty
    same_set = (manifest is not None and not unpatched
                and manifest.get("patchset_id") == patchset.patchset_id)

    def noop():
        return ProjInfo.mkFromProj(pkg_dir)

    def drift_error():
        fatal(_DRIFT_ERROR % (pkg.name, pkg_dir))

    if kind == PatchState.ABSENT:
        pkg.fetch_pristine(update_info, pkg_dir, base_version)        # row 1/2
        return noop() if unpatched else _first_editable_apply(
            update_info, pkg, pkg_dir, base_version, patchset)

    if kind == PatchState.PRISTINE:
        return noop() if unpatched else _first_editable_apply(        # row 3/4
            update_info, pkg, pkg_dir, base_version, patchset)

    if kind == PatchState.DIRTY_PRISTINE:
        if unpatched:
            return noop()                                            # row 11
        drift_error()                                                # row 12

    if kind == PatchState.PATCHED_CLEAN:
        if unpatched:                                                # row 5
            if not pkg.restore_pristine(pkg_dir, base_version, update_info):
                drift_error()
            _delete_ivpm(pkg_dir)
            return noop()
        if same_set:
            return noop()                                            # row 6
        return _reestablish(update_info, pkg, pkg_dir, base_version, patchset)  # row 7

    # PATCHED_DRIFT
    if same_set:
        return noop()                                                # row 8 (leave dev work)
    drift_error()                                                    # rows 9, 10
