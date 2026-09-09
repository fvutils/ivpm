#****************************************************************************
#* package_git.py
#*
#* Copyright 2023 Matthew Ballance and Contributors
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
#* Created on:
#*     Author: 
#*
#****************************************************************************
import logging
import os
import sys
import subprocess
import dataclasses as dc
from typing import Optional
from .package_url import PackageURL
from ..proj_info import ProjInfo
from ..project_ops_info import ProjectUpdateInfo, ProjectStatusInfo, ProjectSyncInfo
from ..utils import note, fatal, resolve_clone_url
from ..cache import is_github_url, parse_github_url
from ..git_progress import run_git_with_progress
from ..update_event import UpdateEvent, UpdateEventType
from ..load_plan import LoadAction
from ..perf import span_or_null

_logger = logging.getLogger("ivpm.pkg_types.package_git")


@dc.dataclass
class PackageGit(PackageURL):
    branch : str = None
    commit : str = None
    tag : str = None
    depth : str = None
    anonymous : bool = None
    ssh : bool = None  # rewrite https URL to git@host:path form
    resolved_commit : str = None  # actual commit hash after fetch

    # Set once PACKAGE_SRC_RESOLVED has been dispatched for this package, so the
    # repeated _get_effective_url calls per fetch report the rewrite only once.
    # Deliberately unannotated: an annotated attribute would become a dataclass
    # field and show up in the package's serialized form.
    _src_resolved_emitted = False

    def patch_capability(self):
        # Editable patchable: cache-mode plus in-place rollback via the git
        # history (retain_base / restore_pristine below). base_version is the
        # commit hash.
        from ..patch import PatchCapability
        return PatchCapability.EDITABLE

    def retain_base(self, pkg_dir, base_version, update_info):
        """No retained bytes needed -- base_version is the commit, recoverable
        from the clone. Record the provenance for the manifest writer."""
        self._base_ref = {"kind": "git", "version": base_version}

    def restore_pristine(self, pkg_dir, base_version, update_info) -> bool:
        """Revert the working tree to base_version, preserving .ivpm/.

        Safety: proceed only if the working tree's deviations are exactly the
        recorded patch result (the manifest result[] paths) plus .ivpm/. Any
        unrelated modification makes this return False so the caller errors
        rather than discarding the developer's work.
        """
        from ..patch import read_manifest
        manifest = read_manifest(pkg_dir)
        recorded = set()
        if manifest:
            for r in manifest.get("result", []):
                recorded.add(r.get("path"))

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=pkg_dir, capture_output=True, text=True)
        if status.returncode != 0:
            return False
        for line in status.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if " -> " in path:               # rename: take the destination
                path = path.split(" -> ")[-1].strip()
            if path == ".ivpm" or path.startswith(".ivpm/"):
                continue
            if path not in recorded:
                return False                 # unrelated edit -> refuse

        if subprocess.run(["git", "reset", "--hard", base_version],
                          cwd=pkg_dir, capture_output=True, text=True).returncode != 0:
            return False
        subprocess.run(["git", "clean", "-fdx", "-e", ".ivpm/"],
                       cwd=pkg_dir, capture_output=True, text=True)
        return True

    def _porcelain_paths(self, pkg_dir):
        """Dirty paths (excluding .ivpm/) from git status --porcelain, or None
        if git status fails."""
        status = subprocess.run(["git", "status", "--porcelain"],
                                cwd=pkg_dir, capture_output=True, text=True)
        if status.returncode != 0:
            return None
        paths = []
        for line in status.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ")[-1].strip()
            if path == ".ivpm" or path.startswith(".ivpm/"):
                continue
            paths.append(path)
        return paths

    def working_tree_dirty(self, pkg_dir):
        paths = self._porcelain_paths(pkg_dir)
        if paths is None:
            return None
        return len(paths) > 0

    def patch_tree_status(self, pkg_dir, base_version, allowed_paths):
        head = subprocess.run(["git", "rev-parse", "HEAD"],
                              cwd=pkg_dir, capture_output=True, text=True)
        if head.returncode != 0 or head.stdout.strip() != base_version:
            return "drift"      # moved HEAD (e.g. committed) -> drift
        paths = self._porcelain_paths(pkg_dir)
        if paths is None:
            return "drift"
        for path in paths:
            if path not in allowed_paths:
                return "drift"
        return "clean"

    # ------------------------------------------------------------------ #
    # Destroy gate (see destroy-design.md)                                #
    # ------------------------------------------------------------------ #

    def _porcelain_split(self, pkg_dir):
        """(modified, untracked) path lists from git status --porcelain,
        excluding .ivpm/ and (implicitly) .gitignore'd files. ([], []) on error."""
        r = subprocess.run(["git", "status", "--porcelain"],
                           cwd=pkg_dir, capture_output=True, text=True)
        if r.returncode != 0:
            return [], []
        modified, untracked = [], []
        for line in r.stdout.splitlines():
            if not line.strip():
                continue
            code = line[:2]
            path = line[3:].strip()
            if " -> " in path:                  # rename: take the destination
                path = path.split(" -> ")[-1].strip()
            if path == ".ivpm" or path.startswith(".ivpm/"):
                continue
            if code.startswith("??"):
                untracked.append(path)
            else:
                modified.append(path)
        return modified, untracked

    def removal_safety(self, remove_info):
        """git gate: classify this tree as SAFE / BLOCKED with structured
        evidence (modified / untracked / unpushed / local-branch / stash /
        patch-drift). Returns DATA only; the front-end formats it."""
        from ..pkg_remove import RemovalSafety, SafetyLevel, SafetyReason
        from ..package import Package

        path = self.path
        if path is None or not os.path.lexists(path):
            return RemovalSafety(SafetyLevel.SAFE)

        # cache-backed read-only mirror / deps-source mirror / dir-file link:
        # the base policy (unlink-safe, nothing to lose) is correct.
        if os.path.islink(path):
            return Package.removal_safety(self, remove_info)

        # Not a git repo on disk (incomplete / extracted) -> base policy.
        if not os.path.isdir(os.path.join(path, ".git")):
            return Package.removal_safety(self, remove_info)

        def _git(args):
            r = subprocess.run(["git"] + args, capture_output=True, text=True,
                               cwd=path, timeout=10)
            return r.returncode, r.stdout.strip()

        reasons = []

        # --- patched tree vs. plain tree -------------------------------- #
        # For a patched tree the recorded patch result is EXPECTED to deviate
        # from base, so we must not report those as "modified". Only drift
        # beyond the manifest's allowed paths (or a moved HEAD) is unsafe.
        from ..patch import read_manifest
        manifest = read_manifest(path)
        if manifest and manifest.get("base_version"):
            base_version = manifest["base_version"]
            allowed = set(r.get("path") for r in manifest.get("result", []))
            if self.patch_tree_status(path, base_version, allowed) == "drift":
                reasons.append(SafetyReason(
                    "patch-drift", data={"base": base_version}))
        else:
            modified, untracked = self._porcelain_split(path)
            if modified:
                reasons.append(SafetyReason("modified", items=modified))
            if untracked:
                reasons.append(SafetyReason("untracked", items=untracked))

        # --- unpushed commits / local-only branch ----------------------- #
        _, branch_raw = _git(["rev-parse", "--abbrev-ref", "HEAD"])
        branch = None if branch_raw == "HEAD" else branch_raw
        rc_up, upstream = _git(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
        if rc_up == 0 and upstream:
            ahead = 0
            rc_ab, ab_raw = _git(
                ["rev-list", "--left-right", "--count", "@{u}...HEAD"])
            if rc_ab == 0 and ab_raw:
                parts = ab_raw.split()
                if len(parts) == 2:
                    try:
                        ahead = int(parts[1])
                    except ValueError:
                        pass
            if ahead > 0:
                _, log_raw = _git(["log", "--format=%s", "@{u}..HEAD"])
                subjects = [s for s in log_raw.splitlines() if s.strip()][:20]
                reasons.append(SafetyReason(
                    "unpushed", items=subjects, count=ahead,
                    data={"upstream": upstream}))
        elif branch is not None:
            # On a branch with no upstream: its commits exist nowhere else.
            reasons.append(SafetyReason("local-branch", data={"branch": branch}))
        else:
            # Detached HEAD -- a commit/tag pin, or the user's own checkout.
            # Usually it sits on a commit the remote already has, so there is
            # nothing to lose. But committing while detached is possible, and
            # those commits are reachable from nothing but HEAD and the reflog:
            # re-cloning destroys them permanently. @{u} does not resolve here,
            # so the ahead/behind check above cannot see them -- ask the
            # question directly instead. This is the one place where "what does
            # no remote have?" must be asked of the commit graph rather than of
            # a tracking branch.
            # Only meaningful when there is somewhere the commits could have
            # come from. With no remote-tracking refs at all, `--not --remotes`
            # excludes nothing and would call the entire history unpushed --
            # true in the letter but useless, and it would block every repo
            # that simply has no remote configured.
            _, remote_refs = _git(["for-each-ref", "--count=1", "refs/remotes"])

            # Plain `rev-list` for the verdict: it is portable to any git, and
            # the decision to block must not hinge on pretty-printing support.
            # Subjects are fetched separately, purely as evidence.
            shas = []
            if remote_refs:
                rc_un, un_raw = _git(["rev-list", "HEAD", "--not", "--remotes"])
                if rc_un == 0:
                    shas = [s for s in un_raw.splitlines() if s.strip()]
            if shas:
                items = [s[:7] for s in shas[:20]]
                _, log_raw = _git(
                    ["log", "--format=%s", "--no-walk"] + shas[:20])
                subjects = [s for s in log_raw.splitlines() if s.strip()]
                if len(subjects) == len(items):
                    items = subjects
                reasons.append(SafetyReason(
                    "unpushed", items=items, count=len(shas),
                    data={"detached": True}))

        # --- stashed work ------------------------------------------------ #
        rc_st, stash_raw = _git(["stash", "list"])
        if rc_st == 0 and stash_raw:
            names = [s for s in stash_raw.splitlines() if s.strip()]
            reasons.append(SafetyReason("stash", items=names, count=len(names)))

        level = SafetyLevel.BLOCKED if reasons else SafetyLevel.SAFE
        return RemovalSafety(level, reasons)

    def update(self, update_info : ProjectUpdateInfo) -> ProjInfo:
        pkg_dir = os.path.join(update_info.deps_dir, self.name)
        self.path = pkg_dir.replace("\\", "/")

        # Report this package for cache statistics
        # Git packages are cacheable if cache=True, editable if cache is not True
        is_cacheable = self.cache is True
        is_editable = self.cache is not True  # Could be cached but isn't
        update_info.report_package(cacheable=is_cacheable, editable=is_editable)

        # Does this package need loading? The planner owns that decision (see
        # load_plan.py); an empty directory is NOT a loaded package, which is
        # what the old `os.path.exists(pkg_dir)` test got wrong.
        decision = update_info.get_load_planner().decide(self)

        if decision.action is LoadAction.RECONCILE:
            # A patched (or previously-patched) tree is reconciled, never
            # skipped, so a changed patch set is picked up. This holds whether
            # or not the tree is already on disk: the patch-aware resolver owns
            # the cache interaction and deliberately bypasses the
            # not-yet-patch-aware deps-source probe below.
            return self._update_with_patches(update_info, pkg_dir)

        if decision.is_resident:
            with span_or_null(getattr(update_info, "perf", None), "pkg.fast_path", package=self.name):
                note("package %s is already loaded" % self.name)
                self._capture_resolved_commit(pkg_dir)
                # Refresh the cache entry's last-referenced timestamp when this
                # dep is a cache symlink (no-op otherwise), so stale-GC sees it
                # as used.
                update_info.get_cache_provider().note_reference(self)
        else:
            # Try deps-source first — resolve commit, then check parent deps-dir(s)
            if update_info.deps_source is not None:
                with span_or_null(getattr(update_info, "perf", None), "git.deps_source", package=self.name) as s:
                    self._resolve_commit_for_deps_source(update_info)
                    if update_info.try_deps_source(self):
                        s.meta["hit"] = True
                        note("deps-source hit for %s" % self.name)
                        return ProjInfo.mkFromProj(pkg_dir)
                    s.meta["hit"] = False

            # Check if caching is enabled and supported
            if self.cache is True:
                # For GitHub URLs, use GitHub API; for others, use git ls-remote
                return self._update_with_cache(update_info, pkg_dir)
            elif self.cache is False:
                # Explicitly no cache - editable clone, depth controlled by self.depth
                return self._update_no_cache(update_info, pkg_dir)
            else:
                # cache not specified - clone with full history
                return self._update_full_clone(update_info, pkg_dir)

        return ProjInfo.mkFromProj(pkg_dir)

    def _get_github_commit_hash(self, owner: str, repo: str, ref: str = None, update_info: ProjectUpdateInfo = None) -> str:
        """Get the commit hash for a GitHub repo using the API or git ls-remote.
        
        For general git URLs, uses git ls-remote to get the hash.
        """
        import httpx
        
        if ref is None:
            ref = self.branch or self.tag or "HEAD"

        
        # Try GitHub API first if it's a GitHub URL
        if is_github_url(self._mapped_url()):
            try:
                # Use GitHub API to get the commit hash
                api_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}"
                response = httpx.get(api_url, follow_redirects=True, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    return data["sha"]
            except Exception:
                pass
        
        # Fallback to git ls-remote for any git URL
        return self._get_commit_hash_ls_remote(ref, update_info)
    
    def _get_commit_hash_ls_remote(self, ref: str = None, update_info: ProjectUpdateInfo = None) -> str:
        """Get commit hash using git ls-remote.

        Tries the effective URL (SSH when not anonymous) first, then falls back
        to the HTTPS spelling if that fails.  The fallback is the *remapped*
        URL, not the declared one: a git-url-map rule redirects where the
        repository is fetched from, so undoing it on the fallback path would
        silently resolve the ref against the upstream the rule steered away
        from.
        """
        if ref is None:
            ref = self.branch or self.tag or "HEAD"

        url = self._get_effective_url(update_info)
        urls_to_try = [url]
        mapped = self._mapped_url()
        if url != mapped:
            urls_to_try.append(mapped)

        for try_url in urls_to_try:
            result = self._ls_remote(try_url, ref)
            if result is not None:
                return result
        return None

    def _ls_remote(self, url: str, ref: str) -> str:
        """Run git ls-remote against a single URL/ref. Returns hash or None.

        The stderr of the last (failed) attempt is retained in
        ``self._last_git_err`` so callers can build a diagnostic message."""
        try:
            # Use git ls-remote to get the hash
            result = subprocess.run(
                ["git", "ls-remote", url, ref],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0 and result.stderr.strip():
                self._last_git_err = result.stderr.strip()
            if result.returncode == 0 and result.stdout.strip():
                # Output format: "hash\tref"
                return result.stdout.strip().split()[0]
            
            # Try refs/heads/ prefix for branches
            if not ref.startswith("refs/"):
                result = subprocess.run(
                    ["git", "ls-remote", url, f"refs/heads/{ref}"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip().split()[0]
                
                # Try refs/tags/ prefix for tags
                result = subprocess.run(
                    ["git", "ls-remote", url, f"refs/tags/{ref}"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip().split()[0]
        except Exception:
            pass
        
        return None

    def _resolve_commit_for_deps_source(self, update_info: ProjectUpdateInfo):
        """Populate self.resolved_commit (no clone) so deps-source matching
        can compare commit hashes.  Safe to call repeatedly; subsequent
        cache/clone paths will re-use the resolved value.
        """
        if self.resolved_commit is not None:
            return
        # If user pinned an exact commit, use it directly.
        if self.commit:
            self.resolved_commit = self.commit
            return
        ref = self.branch or self.tag or "HEAD"
        if is_github_url(self._mapped_url()):
            owner, repo = parse_github_url(self._mapped_url())
            h = self._get_github_commit_hash(owner, repo, ref, update_info)
        else:
            h = self._get_commit_hash_ls_remote(ref, update_info)
        if h is not None:
            self.resolved_commit = h

    def _update_with_cache(self, update_info: ProjectUpdateInfo, pkg_dir: str) -> ProjInfo:
        """Update using the cache."""
        note("loading package %s with cache" % self.name)
        
        ref = self.branch or self.tag or "HEAD"

        # Get the commit hash - use GitHub API for GitHub URLs, git ls-remote otherwise
        commit_hash = None
        with span_or_null(getattr(update_info, "perf", None), "git.resolve_hash", package=self.name) as s:
            if self.commit is not None:
                # A commit pin *is* the resolved hash. Resolving 'ref' here
                # instead keyed the cache on the moving default-branch tip (the
                # 'HEAD' fallback), so the pin was stored/looked up under the
                # wrong SHA and re-missed every time upstream advanced.
                s.meta["path"] = "pinned"
                commit_hash = self.commit
            elif is_github_url(self._mapped_url()):
                s.meta["path"] = "github_api"
                owner, repo = parse_github_url(self._mapped_url())
                commit_hash = self._get_github_commit_hash(owner, repo, ref, update_info)
            else:
                # Use git ls-remote for general git URLs
                s.meta["path"] = "ls_remote"
                commit_hash = self._get_commit_hash_ls_remote(ref, update_info)

        if commit_hash is None:
            fatal(self._augment_git_error(
                "Failed to resolve commit for %s (ref: %s)" % (self.url, ref),
                self.url, getattr(self, "_last_git_err", ""), update_info=update_info))

        self.resolved_commit = commit_hash

        provider = update_info.get_cache_provider()
        with span_or_null(getattr(update_info, "perf", None), "cache.lookup", package=self.name) as s:
            result = provider.lookup(self, commit_hash)
            s.meta["state"] = ("disabled" if result.is_disabled
                               else "hit" if result.is_hit else "miss")

        # If this dependency is not cacheable (no cache dir resolved),
        # fall back to a full editable clone.
        if result.is_disabled:
            update_info.report_cache_unconfigured()
            return self._update_full_clone(update_info, pkg_dir)

        # Cache hit - symlink to deps
        if result.is_hit:
            note("Cache hit for %s at %s" % (self.name, commit_hash[:12]))
            with span_or_null(getattr(update_info, "perf", None), "cache.materialize", package=self.name):
                provider.materialize(self, commit_hash)
            update_info.report_cache_hit()
            return ProjInfo.mkFromProj(pkg_dir)

        # Cache miss - clone without history
        note("Cache miss for %s - cloning" % self.name)
        update_info.report_cache_miss()

        # Clone to a temporary location first
        import shutil
        temp_dir = os.path.join(update_info.deps_dir, f".cache_temp_{self.name}")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

        # A failed fetch/checkout must not leave a partial .cache_temp_* tree
        # behind: the next run would then find (and delete) foreign state, and
        # the stale directory confuses anyone looking at deps_dir.
        try:
            self._clone_to_dir(update_info, temp_dir, depth=1)
        except BaseException:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

        # Store in cache and link
        with span_or_null(getattr(update_info, "perf", None), "cache.store", package=self.name):
            provider.store(self, commit_hash, temp_dir)
        with span_or_null(getattr(update_info, "perf", None), "cache.materialize", package=self.name):
            provider.materialize(self, commit_hash)

        return ProjInfo.mkFromProj(pkg_dir)

    def _resolve_commit(self, update_info: ProjectUpdateInfo) -> str:
        """Resolve the ref (branch/tag/HEAD) to a concrete commit hash."""
        ref = self.branch or self.tag or "HEAD"
        if is_github_url(self._mapped_url()):
            owner, repo = parse_github_url(self._mapped_url())
            h = self._get_github_commit_hash(owner, repo, ref, update_info)
        else:
            h = self._get_commit_hash_ls_remote(ref, update_info)
        if h is None:
            fatal(self._augment_git_error(
                "Failed to resolve commit for %s (ref: %s)" % (self.url, ref),
                self.url, getattr(self, "_last_git_err", ""), update_info=update_info))
        return h

    def fetch_pristine(self, update_info, dest_dir: str, base_version: str) -> None:
        """Materialize a writable pristine tree of base_version at dest_dir.
        A depth=1 clone at the resolved ref is exactly base_version (mirrors the
        cache-miss clone)."""
        self._clone_to_dir(update_info, dest_dir, depth=1)

    def _update_with_patches(self, update_info: ProjectUpdateInfo, pkg_dir: str) -> ProjInfo:
        """Resolve the commit, then hand off to the patch-aware resolver."""
        from ..patch import PatchAwareResolver
        with span_or_null(getattr(update_info, "perf", None), "git.resolve_hash", package=self.name):
            commit_hash = self._resolve_commit(update_info)
        self.resolved_commit = commit_hash
        with span_or_null(getattr(update_info, "perf", None), "patch.apply", package=self.name):
            return PatchAwareResolver().resolve(update_info, self, commit_hash)

    def _update_no_cache(self, update_info: ProjectUpdateInfo, pkg_dir: str) -> ProjInfo:
        """Editable clone without shared cache (cache=False). Depth controlled by self.depth."""
        note("loading package %s (no cache, editable)" % self.name)
        
        self._clone_to_dir(update_info, pkg_dir, depth=self.depth)
        self._capture_resolved_commit(pkg_dir)
        
        return ProjInfo.mkFromProj(pkg_dir)

    def _update_full_clone(self, update_info: ProjectUpdateInfo, pkg_dir: str) -> ProjInfo:
        """Full clone with history (cache unspecified)."""
        note("loading package %s" % self.name)
        
        self._clone_to_dir(update_info, pkg_dir, depth=self.depth)
        self._capture_resolved_commit(pkg_dir)
        
        return ProjInfo.mkFromProj(pkg_dir)

    def _capture_resolved_commit(self, pkg_dir: str):
        """Read the HEAD commit hash from a cloned repo and store in resolved_commit."""
        if self.resolved_commit is not None:
            return  # already set (e.g. by cache path)
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=pkg_dir, timeout=10
            )
            if result.returncode == 0:
                self.resolved_commit = result.stdout.strip()
        except Exception:
            pass


    def _ssh_pref(self, update_info: ProjectUpdateInfo = None):
        """Explicit SSH override as a tri-state (``True``/``False``/``None``).

        ``None`` means "no explicit preference — consult the auth order".
        Resolution (first match wins):
        * ``self.ssh`` (per-package ``ssh:`` option: true=SSH, false=as-written).
        * ``self.anonymous`` (legacy per-package knob): ``anonymous: true``
          forces the URL as written; ``anonymous: false`` no longer forces SSH —
          it defers to the auth order so ``gh`` can win (use ``ssh: true`` to
          force SSH).
        * ``--ssh`` / ``--anonymous`` CLI flags.
        """
        if self.ssh is not None:
            return self.ssh
        if self.anonymous is True:
            return False
        # self.anonymous is False/None -> defer to CLI flags / auth order
        args = update_info.args if update_info is not None else None
        if args is not None:
            if getattr(args, "ssh", False):
                return True
            if getattr(args, "anonymous", False):
                return False
        return None

    def _mapped_url(self) -> str:
        """The declared URL after any ``git-url-map`` rewrite, before auth/ssh
        resolution.

        This -- not ``self.url`` -- is the URL that says where the repository
        actually lives, so it is what host-specific behavior (e.g. "is this
        GitHub, so use the API?") must key off.  A github.com URL remapped to a
        local mirror is no longer a GitHub repository, and asking api.github.com
        for its commit hash would resolve a ref the mirror may not have.
        """
        from ..site_config import apply_git_url_map
        return apply_git_url_map(self.url)

    def _get_effective_url(self, update_info: ProjectUpdateInfo = None) -> str:
        """Return the clone/ls-remote URL.

        Applies any explicit per-package/CLI override; otherwise the configured
        git auth order (``gh``/``ssh``/``https``) decides whether to use the
        https URL as-is or rewrite it to git@host:path form.  ``file://`` and
        non-URL local paths are never converted.
        """
        auth_order = None
        if update_info is not None and update_info.args is not None:
            auth_order = getattr(update_info.args, "git_auth_order", None)
        url = resolve_clone_url(self.url, self._ssh_pref(update_info), auth_order)
        if url != self.url:
            self._emit_src_resolved(update_info, url)
        return url

    def _emit_src_resolved(self, update_info: ProjectUpdateInfo, url: str):
        """Report a rewritten fetch URL to this package's TUI row.

        Fires only when the effective URL differs from the declared one, so an
        unremapped package's display is unchanged.  ``_get_effective_url`` is
        called more than once per package (ls-remote, then clone); the emitted
        flag keeps that from producing duplicate rows in transcript mode.
        """
        if update_info is None or self._src_resolved_emitted:
            return
        dispatcher = getattr(update_info, "event_dispatcher", None)
        if dispatcher is None:
            return
        self._src_resolved_emitted = True
        dispatcher.dispatch(UpdateEvent(
            event_type=UpdateEventType.PACKAGE_SRC_RESOLVED,
            package_name=self.name,
            package_src=self.url,
            package_src_effective=url,
        ))

    def _emit_progress(self, update_info: ProjectUpdateInfo, message: str):
        """Forward a git progress message to this package's TUI row."""
        dispatcher = getattr(update_info, "event_dispatcher", None)
        if dispatcher is None:
            return
        dispatcher.dispatch(UpdateEvent(
            event_type=UpdateEventType.HANDLER_TASK_PROGRESS,
            package_name=self.name,
            task_id="git:%s" % self.name,
            task_name="git",
            task_message=message,
        ))

    def _run_git(self, git_cmd, update_info: ProjectUpdateInfo, cwd=None, progress=False):
        """Run a git command, returning ``(exit_code, stderr_text)``.

        git's stderr is always captured (the last lines are kept) so a failure
        can be diagnosed, regardless of display mode.  In Rich TUI mode
        (``suppress_output``) git's output is not printed; when ``progress`` is
        also set and an event dispatcher is available, git's per-phase
        percentages are streamed to the package's TUI row (e.g. "Receiving
        objects 42%").  Outside TUI mode git's native stderr is streamed to the
        terminal as before (and also captured).
        """
        captured = []
        if update_info.suppress_output:
            dispatcher = getattr(update_info, "event_dispatcher", None)
            on_progress = None
            if progress and dispatcher is not None:
                on_progress = lambda m: self._emit_progress(update_info, m)
                git_cmd = list(git_cmd) + ["--progress"]
            rc = run_git_with_progress(
                git_cmd, cwd=cwd, on_progress=on_progress, stderr_sink=captured)
            return rc, "\n".join(captured)
        # Non-TUI: tee git's stderr to the terminal while capturing it. Ask for
        # --progress so the in-place progress display survives the pipe.
        rc = run_git_with_progress(
            list(git_cmd) + (["--progress"] if progress else []),
            cwd=cwd, stderr_sink=captured, echo=True)
        return rc, "\n".join(captured)

    def _clone_to_dir(self, update_info: ProjectUpdateInfo, target_dir: str, depth=None):
        """Clone the repo to the specified directory.

        Uses subprocess cwd= rather than os.chdir() so that parallel fetches
        do not corrupt one another's working directory (chdir is process-global).
        """
        parent_dir = os.path.dirname(target_dir)

        if not os.path.isdir(parent_dir):
            os.makedirs(parent_dir)

        sys.stdout.flush()

        # Shallow + commit-pinned: `clone --depth N` fetches only the tip of the
        # ref it clones (the default branch when no branch/tag is declared), so
        # the pinned commit is usually absent from the resulting history and the
        # checkout below fails with "unable to read tree". Fetch the pinned
        # object itself instead, keeping the shallow intent of this path.
        if depth is not None and self.commit is not None:
            if self._fetch_pinned_commit(update_info, target_dir, depth):
                self._update_submodules(update_info, target_dir)
                return
            # The server refused the unadvertised-object request (no
            # uploadpack.allowReachableSHA1InWant -- the default for plain
            # local/self-hosted repos). Fall back to a clone with full history,
            # where the pinned commit is guaranteed present.
            _logger.debug("shallow fetch of %s failed; falling back to a full clone",
                          self.commit)
            import shutil
            shutil.rmtree(target_dir, ignore_errors=True)
            depth = None

        git_cmd = ["git", "clone"]

        if depth is not None:
            git_cmd.extend(["--depth", str(depth)])

        # -b takes a tag name as readily as a branch name (it sets the initial
        # checkout, detaching HEAD for a tag). Honoring 'tag:' here matches the
        # ref resolution everywhere else in this class -- _get_commit_hash_*
        # and the deps-source probe all read `self.branch or self.tag` -- and
        # without it a tag pin was silently ignored, leaving the clone on the
        # remote's default branch.
        ref = self.branch if self.branch is not None else self.tag
        if ref is not None:
            git_cmd.extend(["-b", str(ref)])

        url = self._get_effective_url(update_info)
        _logger.debug("Clone URL: %s", url)
        git_cmd.append(url)

        # Clone directly to the full target directory. git creates the
        # destination (and any missing leading dirs) for us.
        git_cmd.append(target_dir)

        _logger.debug("git_cmd: %s", str(git_cmd))

        # Stream fetch progress to the TUI when output is suppressed
        with span_or_null(getattr(update_info, "perf", None), "git.clone", package=self.name,
                          depth=depth):
            rc, err = self._run_git(git_cmd, update_info, progress=True)

        if rc != 0:
            fatal(self._augment_git_error(
                "Failed to clone %s (git exit %d)" % (url, rc), url, err,
                update_info=update_info))

        # Checkout a specific commit.
        #
        # Detaching, rather than `git reset --hard`: reset moves the *branch*
        # pointer to the pinned commit, leaving the clone on an ordinary branch
        # that merely happens to sit behind its upstream. That misrepresents the
        # state everywhere it is read -- `ivpm status` showed the branch name as
        # though the dependency tracked it, and `ivpm sync` would fast-forward
        # it straight off the pin. A detached HEAD is what a pinned checkout
        # actually is, and every consumer already knows how to render and
        # respect that.
        if self.commit is not None:
            git_cmd = ["git", "checkout", "--detach", self.commit]
            _logger.debug("git_cmd: %s", str(git_cmd))
            with span_or_null(getattr(update_info, "perf", None), "git.checkout", package=self.name):
                rc, err = self._run_git(git_cmd, update_info, cwd=target_dir)

            if rc != 0:
                fatal(self._augment_git_error(
                    "Failed to check out commit %s of %s (git exit %d)"
                    % (self.commit, url, rc), url, err, update_info=update_info))


        self._update_submodules(update_info, target_dir)

    def _fetch_pinned_commit(self, update_info: ProjectUpdateInfo,
                             target_dir: str, depth) -> bool:
        """Shallow-fetch exactly ``self.commit`` into a fresh repo at
        *target_dir* and detach HEAD onto it.

        Returns False (leaving *target_dir* for the caller to clean up) if any
        step fails -- most commonly because the remote does not serve
        unadvertised objects -- so the caller can fall back to a full clone.
        """
        url = self._get_effective_url(update_info)
        _logger.debug("Fetch URL: %s (pinned commit %s)", url, self.commit)

        os.makedirs(target_dir, exist_ok=True)

        steps = [
            ["git", "init"],
            ["git", "remote", "add", "origin", url],
            ["git", "fetch", "--depth", str(depth), "origin", self.commit],
            ["git", "checkout", "--detach", self.commit],
        ]

        with span_or_null(getattr(update_info, "perf", None), "git.fetch_commit",
                          package=self.name, depth=depth):
            for git_cmd in steps:
                _logger.debug("git_cmd: %s", str(git_cmd))
                rc, err = self._run_git(
                    git_cmd, update_info, cwd=target_dir,
                    progress=(git_cmd[1] == "fetch"))
                if rc != 0:
                    self._last_git_err = err
                    return False
        return True

    def _update_submodules(self, update_info: ProjectUpdateInfo, target_dir: str):
        # TODO: Existence of .gitmodules should trigger this
        if os.path.isfile(os.path.join(target_dir, ".gitmodules")):
            sys.stdout.flush()
            self._emit_progress(update_info, "updating submodules")
            git_cmd = ["git", "submodule", "update", "--init", "--recursive"]
            _logger.debug("git_cmd: %s", str(git_cmd))
            with span_or_null(getattr(update_info, "perf", None), "git.submodule", package=self.name):
                rc, err = self._run_git(git_cmd, update_info, cwd=target_dir, progress=True)

    def _augment_git_error(self, base_msg, url, stderr_text, update_info=None):
        """Build a fatal message from *base_msg* plus git's captured stderr and
        an offline diagnosis hint.  The result is multi-line: the summary, the
        tail of git's own output, then any 'hint:' lines."""
        from ..git_diagnose import diagnose_git_failure
        parts = [base_msg]

        tail = [ln for ln in (stderr_text or "").splitlines() if ln.strip()]
        if tail:
            parts.append("git reported:")
            parts.extend("  " + ln for ln in tail[-8:])

        ssh_pref = self._ssh_pref(update_info) if update_info is not None else self.ssh
        for hint in diagnose_git_failure(url, stderr_text, ssh_pref):
            parts.append("hint: " + hint)

        return "\n".join(parts)

    def status(self, status_info: ProjectStatusInfo):
        from ..pkg_status import PkgVcsStatus, git_working_tree_status

        pkg_dir = os.path.join(status_info.deps_dir, self.name)

        # git_working_tree_status is the single source of truth shared with
        # GitCloneProvider.root_status(); it returns None for a non-repo, which
        # we render as the "(not fetched)" placeholder here.
        st = git_working_tree_status(pkg_dir, self.name)
        if st is None:
            return PkgVcsStatus(
                name=self.name,
                src_type="git",
                path=pkg_dir,
                vcs="git",
                branch="(not fetched)",
                error="directory not found or not a git repo",
            )
        # The working tree cannot tell us a pin exists -- a pinned checkout and
        # a hand-detached one are the same on disk. The declaration is what
        # distinguishes them, and it reaches us through self.commit (set from
        # the manifest, or from the lock's 'commit_requested' when this package
        # was rebuilt from a lock entry, which is how `ivpm status` builds it).
        if self.commit is not None:
            st.pinned_commit = self.commit
        return st

    def sync(self, sync_info: ProjectSyncInfo):
        from ..pkg_sync import PkgSyncResult, SyncOutcome
        import stat as _stat

        pkg_dir = os.path.join(sync_info.deps_dir, self.name)
        dry_run = sync_info.dry_run

        # Tag-pinned packages have no meaningful "latest" to pull.
        if self.tag is not None:
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.SKIPPED,
                skipped_reason="pinned to tag %s" % self.tag,
            )

        # Neither do commit-pinned ones. A pin is a statement about which
        # commit this dependency must be at; advancing it to the branch tip
        # contradicts the manifest. Note this is NOT covered by the detached-
        # HEAD check below: _clone_to_dir pins with `git reset --hard`, which
        # moves the *branch* pointer, so a pinned clone sits on a normal branch
        # one or more commits behind its upstream and would fast-forward
        # straight off its pin.
        if self.commit is not None:
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.SKIPPED,
                skipped_reason="pinned to commit %s" % self.commit[:7],
            )

        # Read-only packages are cached; skip silently.
        try:
            mode = os.stat(pkg_dir).st_mode
            is_writable = bool(mode & _stat.S_IWUSR)
        except Exception:
            is_writable = False
        if not is_writable:
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.SKIPPED,
                skipped_reason="read-only (cached)",
            )

        # Must be a real git checkout.
        if not os.path.isdir(os.path.join(pkg_dir, ".git")):
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.ERROR,
                error="not a git repository",
            )

        def _git(*args):
            r = subprocess.run(
                ["git"] + list(args),
                capture_output=True, text=True, cwd=pkg_dir, timeout=60,
            )
            return r.returncode, r.stdout.strip(), r.stderr.strip()

        # Current branch (detached HEAD → skip).
        rc, branch, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
        if rc != 0:
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.ERROR, error="failed to determine branch",
            )
        if branch == "HEAD":
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.SKIPPED, skipped_reason="detached HEAD",
            )

        _, old_commit, _ = _git("rev-parse", "--short", "HEAD")

        # Detect dirty working tree.
        _, porcelain, _ = _git("status", "--porcelain")
        dirty_files = [ln for ln in porcelain.splitlines() if ln.strip()]

        # Fetch from origin (safe in both real and dry-run modes).
        rc, _, err = _git("fetch", "origin")
        if rc != 0:
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.ERROR, branch=branch,
                old_commit=old_commit,
                error="git fetch failed: %s" % err,
            )

        # Ahead / behind upstream.
        rc, ab_raw, _ = _git("rev-list", "--left-right", "--count",
                              "@{u}...HEAD")
        behind = ahead = 0
        if rc == 0 and ab_raw:
            parts = ab_raw.split()
            if len(parts) == 2:
                try:
                    behind, ahead = int(parts[0]), int(parts[1])
                except ValueError:
                    pass

        # Nothing to pull and no local commits → up-to-date.
        if behind == 0 and ahead == 0:
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.UP_TO_DATE,
                branch=branch, old_commit=old_commit,
            )

        # Strictly ahead (nothing to pull, but local work exists).
        if behind == 0 and ahead > 0:
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.AHEAD,
                branch=branch, old_commit=old_commit, commits_ahead=ahead,
            )

        # There are upstream commits to pull (behind > 0).
        # Dirty tree blocks a real merge.
        if dirty_files and not dry_run:
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.DIRTY,
                branch=branch, old_commit=old_commit,
                dirty_files=dirty_files,
            )

        if dry_run:
            # Diverged history (ahead > 0 as well) → would conflict.
            if ahead > 0:
                return PkgSyncResult(
                    name=self.name, src_type="git", path=pkg_dir,
                    outcome=SyncOutcome.DRY_WOULD_CONFLICT,
                    branch=branch, old_commit=old_commit,
                    commits_behind=behind, commits_ahead=ahead,
                )
            # Check whether a fast-forward is possible.
            rc_ff, _, _ = _git("merge-base", "--is-ancestor",
                               "HEAD", "origin/%s" % branch)
            if rc_ff == 0:
                if dirty_files:
                    return PkgSyncResult(
                        name=self.name, src_type="git", path=pkg_dir,
                        outcome=SyncOutcome.DRY_DIRTY,
                        branch=branch, old_commit=old_commit,
                        commits_behind=behind, dirty_files=dirty_files,
                    )
                return PkgSyncResult(
                    name=self.name, src_type="git", path=pkg_dir,
                    outcome=SyncOutcome.DRY_WOULD_SYNC,
                    branch=branch, old_commit=old_commit,
                    commits_behind=behind,
                )
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.DRY_WOULD_CONFLICT,
                branch=branch, old_commit=old_commit, commits_behind=behind,
            )

        # Real merge.
        rc, _, _ = _git("merge", "origin/%s" % branch)
        if rc != 0:
            # Collect unmerged files from index before aborting.
            _, st_out, _ = _git("status", "--porcelain")
            conflict_files = [
                ln[3:] for ln in st_out.splitlines()
                if ln[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD")
            ]
            _git("merge", "--abort")
            return PkgSyncResult(
                name=self.name, src_type="git", path=pkg_dir,
                outcome=SyncOutcome.CONFLICT,
                branch=branch, old_commit=old_commit, commits_behind=behind,
                conflict_files=conflict_files,
                next_steps=[
                    "cd %s && git status" % pkg_dir,
                    "cd %s && git mergetool" % pkg_dir,
                    "cd %s && git merge --abort" % pkg_dir,
                ],
            )

        _, new_commit, _ = _git("rev-parse", "--short", "HEAD")

        # Update submodules if the project uses them.
        if os.path.isfile(os.path.join(pkg_dir, ".gitmodules")):
            _git("submodule", "update", "--init", "--recursive")

        return PkgSyncResult(
            name=self.name, src_type="git", path=pkg_dir,
            outcome=SyncOutcome.SYNCED,
            branch=branch, old_commit=old_commit, new_commit=new_commit,
            commits_behind=behind,
        )
    
    @classmethod
    def dep_keys(cls):
        return super().dep_keys() | {
            "branch", "commit", "tag", "depth", "anonymous", "ssh"}

    def process_options(self, opts, si):
        super().process_options(opts, si)
        self.src_type = "git"

        if "anonymous" in opts.keys():
            self.anonymous = opts["anonymous"]

        if "ssh" in opts.keys():
            self.ssh = opts["ssh"]

        if "depth" in opts.keys():
            self.depth = opts["depth"]
                
        if "dep-set" in opts.keys():
            self.dep_set = opts["dep-set"]
               
        if "branch" in opts.keys():
            self.branch = opts["branch"]
                
        if "commit" in opts.keys():
            self.commit = opts["commit"]
        elif "commit_requested" in opts.keys():
            # ``opts`` is a package-lock.json entry, not a manifest dep -- the
            # shape `ivpm sync` builds from. It spells the pin
            # 'commit_requested'; 'branch', 'tag' and 'url' happen to be spelled
            # the same in both, which is why only the commit pin went missing
            # here. Without this, a commit-pinned package reached sync() with
            # self.commit None and was synced like an unpinned one.
            self.commit = opts["commit_requested"]

        if "tag" in opts.keys():
            self.tag = opts["tag"]


    @staticmethod
    def create(name, opts, si) -> 'PackageGit':
        pkg = PackageGit(name)
        pkg.process_options(opts, si)
        return pkg

    @staticmethod
    def get_live_info(name: str, deps_dir: str) -> dict:
        """Return the HEAD commit from the installed git package directory."""
        pkg_dir = os.path.join(deps_dir, name)
        real_dir = os.path.realpath(pkg_dir) if os.path.exists(pkg_dir) else pkg_dir
        if not os.path.isdir(real_dir):
            return {}
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=real_dir, capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return {"commit_resolved": r.stdout.strip()}
        except Exception:
            pass
        return {}

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="git",
            description="Git repository — cloned into packages/",
            params=[
                ParamInfo("url", "Repository URL (https:// or git@...)", required=True, type_hint="url"),
                ParamInfo("branch", "Branch to check out (default: repo default branch)"),
                ParamInfo("tag", "Tag to pin to; disables sync for this package"),
                ParamInfo("commit", "Specific commit SHA to check out"),
                ParamInfo("depth", "Shallow-clone depth (integer)", type_hint="int"),
                ParamInfo("cache", "Cache mode: true=shared cache+symlink (read-only), false=editable clone but never cached, omit=full editable clone", type_hint="bool"),
                ParamInfo("ssh", "Force SSH: rewrite the https URL to git@host:path form (overrides the auth order)", type_hint="bool"),
                ParamInfo("anonymous", "Legacy knob: anonymous:true clones https as written; anonymous:false defers to the auth order (use 'ssh' to force SSH)", type_hint="bool"),
            ],
            notes=(
                "Without an explicit ssh/anonymous override, the configured git auth order decides "
                "the transport (default 'gh,ssh': use the https URL as-is when gh is authenticated "
                "for the host, otherwise rewrite to git@host:path).  Per-host rules may be set in the "
                "site config; override the order globally with IVPM_GIT_AUTH_ORDER or per-invocation "
                "with --git-auth-order.  "
                "When cache: true, IVPM resolves the HEAD commit hash, stores the repo in a "
                "shared cache, and symlinks it read-only into packages/.  "
                "When cache: false, an editable clone is made directly in packages/ and the shared "
                "cache is never consulted (history controlled by 'depth:', full by default).  "
                "Omitting cache produces a full editable clone — the common case for co-developed deps."
            ),
        )

