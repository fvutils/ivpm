#****************************************************************************
#* package_ivpm_yaml.py
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
"""
``src: ivpm.yaml`` — a *dep-set factory* package source.

A factory dependency does not install any content of its own.  It fetches a
referenced ``ivpm.yaml`` (over http(s) or from a local path), selects one (or
more) of its dep-sets, and folds those packages into the consumer's dep-set.
The existing updater recursion does the folding: the factory's ``update()``
returns a ``ProjInfo`` and the main loop queues
``proj_info.get_dep_set(pkg.dep_set)`` as new dependencies
(``package_updater.py``).

``dep-set:`` may name a single dep-set or a list of them
(``dep-set: [core, extras, dev]``).  When several are requested they are merged
into one synthetic dep-set before folding; on package-name collision a
later-listed dep-set overrides an earlier one (matching the ``uses:``
inheritance semantics).  Each contributed leaf still records the *specific*
dep-set it came from via ``from_ivpm_source``.

The factory node therefore has **no packages-dir representation** — it is a
*virtual* node (``virtual = True``).  It is recorded in ``package-lock.json``
under a top-level ``ivpm_sources`` map (url -> fingerprint + dep-set) rather
than the normal ``packages`` map, and each contributed leaf carries a
``from_ivpm_source`` provenance field.
"""
import hashlib
import os
import dataclasses as dc

from .package_url import PackageURL
from ..project_ops_info import ProjectUpdateInfo
from ..utils import fatal, getlocstr


@dc.dataclass
class PackageIvpmYaml(PackageURL):
    # etag / last-modified / content-hash of the fetched factory YAML, recorded
    # in the lock's ``ivpm_sources`` entry so a re-resolve can detect that the
    # factory's dep-set membership changed upstream.
    resolved_fingerprint: str = None

    # The dep-set(s) requested by the consumer, as authored (a single name or a
    # list). Preserved verbatim for the lock entry / spec_matches_lock, since
    # update() rewrites ``dep_set`` to a synthetic merged name when several are
    # requested. None until update() runs.
    requested_dep_set: object = None

    # Virtual: this node contributes deps but occupies no packages-dir slot.
    virtual = True

    @staticmethod
    def create(name, opts, si) -> 'PackageIvpmYaml':
        pkg = PackageIvpmYaml(name)
        pkg.process_options(opts, si)
        return pkg

    def process_options(self, opts, si):
        super().process_options(opts, si)   # sets url, cache, dep_set, srcinfo
        self.src_type = "ivpm.yaml"

    # -- canonical URL / cycle bookkeeping ----------------------------------

    def _canonical_url(self) -> str:
        """A stable key for cycle detection. Local paths canonicalize to their
        realpath; http(s) URLs are used verbatim."""
        url = self.url or ""
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return os.path.realpath(self._resolve_local_path(url))

    def _resolve_local_path(self, url: str) -> str:
        path = url[len("file://"):] if url.startswith("file://") else url
        if not os.path.isabs(path):
            # Resolve relative to the ivpm.yaml that declared this dependency.
            base = None
            if self.srcinfo is not None and getattr(self.srcinfo, "filename", None):
                base = os.path.dirname(self.srcinfo.filename)
            base = base or os.getcwd()
            path = os.path.join(base, path)
        return path

    # -- update -------------------------------------------------------------

    def update(self, update_info: ProjectUpdateInfo) -> 'ProjInfo':
        from ..ivpm_yaml_reader import IvpmYamlReader
        from ..packages_info import PackagesInfo

        # The factory YAML is cacheable; it occupies no packages-dir slot.
        update_info.report_package(cacheable=True)

        if self.url is None:
            fatal("Package '%s' (src: ivpm.yaml) requires a 'url:' @ %s" % (
                self.name, getlocstr(self)))

        # Normalize the requested dep-set(s) to a list. 'dep-set:' may be a
        # single name (the common case) or a list of names to merge. Preserve
        # the authored value for the lock entry before we possibly rewrite
        # ``dep_set`` to a synthetic merged name below.
        self.requested_dep_set = self.dep_set
        if isinstance(self.dep_set, (list, tuple)):
            dep_set_names = [str(d) for d in self.dep_set]
        else:
            dep_set_names = [str(self.dep_set)]

        # Cycle guard: a factory may not (transitively) reference itself.
        canon = self._canonical_url()
        chain = getattr(self, "_ivpm_source_chain", ())
        if canon in chain:
            cycle = " -> ".join(list(chain) + [canon])
            fatal("Cyclic ivpm.yaml factory reference: %s" % cycle)

        local_yaml = self._fetch_yaml(update_info)

        with open(local_yaml) as fp:
            proj = IvpmYamlReader().read(fp, local_yaml)

        # All requested dep-sets must exist in the referenced manifest.
        missing = [d for d in dep_set_names if not proj.has_dep_set(d)]
        if missing:
            available = sorted(proj.dep_set_m.keys())
            if available:
                avail_msg = "available dep-set(s): %s" % ", ".join(available)
            else:
                avail_msg = "the referenced ivpm.yaml defines no dep-sets"

            # Offer a "did you mean" suggestion for each missing name.
            import difflib
            suggestions = []
            for d in missing:
                close = difflib.get_close_matches(d, available, n=1, cutoff=0.6)
                if close:
                    suggestions.append("'%s' (did you mean '%s'?)" % (d, close[0]))
                else:
                    suggestions.append("'%s'" % d)

            fatal("Package '%s' (src: ivpm.yaml): referenced ivpm.yaml '%s' has "
                  "no dep-set(s): %s; %s @ %s" % (
                      self.name, self.url, ", ".join(suggestions),
                      avail_msg, getlocstr(self)))

        # Stamp provenance on the leaves of each selected dep-set (recording the
        # specific dep-set each came from), and propagate the include chain to
        # any leaf that is itself a factory.
        child_chain = tuple(chain) + (canon,)
        for dsname in dep_set_names:
            origin = "%s#%s" % (self.url, dsname)
            for leaf in proj.get_dep_set(dsname).packages.values():
                leaf.from_ivpm_source = origin
                if getattr(leaf, "src_type", None) == "ivpm.yaml":
                    leaf._ivpm_source_chain = child_chain

        # A single dep-set folds directly (``dep_set`` already names it). For
        # several, synthesize one merged dep-set and point ``dep_set`` at it so
        # the updater folds exactly one set. Merge left-to-right: a later-listed
        # dep-set overrides an earlier one on package-name collision.
        if len(dep_set_names) > 1:
            merged_name = "__ivpm_merged__:" + ",".join(dep_set_names)
            merged = PackagesInfo(merged_name)
            for dsname in dep_set_names:
                for leaf in proj.get_dep_set(dsname).packages.values():
                    merged.packages[leaf.name] = leaf
            proj.set_dep_set(merged_name, merged)
            self.dep_set = merged_name

        return proj

    # -- fetch & cache ------------------------------------------------------

    def _fetch_yaml(self, update_info: ProjectUpdateInfo) -> str:
        """Return a local readable path to the factory YAML and record its
        fingerprint on ``self.resolved_fingerprint``. Remote files are cached
        under ``<deps_dir>/.ivpm-sources/``; local files are read in place."""
        url = self.url
        if url.startswith("http://") or url.startswith("https://"):
            content = self._download(url)
            self.resolved_fingerprint = self._http_fingerprint(url) \
                or _sha256_bytes(content)
            cache_dir = os.path.join(update_info.deps_dir, ".ivpm-sources")
            os.makedirs(cache_dir, exist_ok=True)
            local = os.path.join(cache_dir, _safe_filename(url))
            with open(local, "wb") as f:
                f.write(content)
            return local

        # Local path
        path = self._resolve_local_path(url)
        if not os.path.isfile(path):
            fatal("Package '%s' (src: ivpm.yaml): file not found: %s @ %s" % (
                self.name, path, getlocstr(self)))
        with open(path, "rb") as f:
            self.resolved_fingerprint = _sha256_bytes(f.read())
        return path

    def _download(self, url: str) -> bytes:
        import httpx
        r = httpx.get(url, follow_redirects=True, timeout=30)
        if r.status_code < 200 or r.status_code >= 300:
            raise Exception("Failed to download %s: HTTP %d" % (url, r.status_code))
        return r.content

    def _http_fingerprint(self, url: str):
        """Best-effort etag/last-modified via a HEAD request; None on failure."""
        try:
            import httpx
            resp = httpx.head(url, follow_redirects=True, timeout=30)
            if "ETag" in resp.headers:
                return resp.headers["ETag"].strip('"').strip("'")
            if "Last-Modified" in resp.headers:
                return resp.headers["Last-Modified"]
        except Exception:
            pass
        return None

    # -- lock-file representation -------------------------------------------

    def _lock_dep_set(self):
        """The dep-set value to record in the lock: the authored single name or
        list (``requested_dep_set``), falling back to ``dep_set`` when update()
        has not run (e.g. unit tests that build an entry directly)."""
        if self.requested_dep_set is not None:
            return self.requested_dep_set
        return self.dep_set

    def get_lock_entry(self):
        # Virtual: emitted under the lock's ``ivpm_sources`` map, not the
        # normal ``packages`` map (see package_lock.write_lock).
        return {
            "src": "ivpm.yaml",
            "url": self.url,
            "dep_set": self._lock_dep_set(),
            "fingerprint": self.resolved_fingerprint,
            "reproducible": True,
            "virtual": True,
        }

    def spec_matches_lock(self, lock_entry):
        return (
            self.url == lock_entry.get("url")
            and self._lock_dep_set() == lock_entry.get("dep_set")
        )

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="ivpm.yaml",
            description="Dep-set factory — pulls a named dep-set from a referenced "
                        "ivpm.yaml. Contributes deps only; no packages-dir entry.",
            params=[
                ParamInfo("url", "http(s) URL or local path of the factory ivpm.yaml",
                          required=True, type_hint="url"),
                ParamInfo("dep-set", "Name of the dep-set to pull from the factory, "
                          "or a list of names to merge (later overrides earlier). "
                          "Default: the consuming dep-set's name"),
            ],
        )


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _safe_filename(url: str) -> str:
    """Stable, filesystem-safe cache filename for a factory URL."""
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    base = os.path.basename(url.split("?", 1)[0]) or "factory"
    if not base.endswith((".yaml", ".yml")):
        base = base + ".yaml"
    return "%s-%s" % (digest, base)
