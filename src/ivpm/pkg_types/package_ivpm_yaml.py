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
referenced ``ivpm.yaml`` (over http(s) or from a local path), selects one of
its dep-sets, and folds those packages into the consumer's dep-set.  The
existing updater recursion does the folding: the factory's ``update()`` returns
a ``ProjInfo`` and the main loop queues ``proj_info.get_dep_set(pkg.dep_set)``
as new dependencies (``package_updater.py``).

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

        # The factory YAML is cacheable; it occupies no packages-dir slot.
        update_info.report_package(cacheable=True)

        if self.url is None:
            fatal("Package '%s' (src: ivpm.yaml) requires a 'url:' @ %s" % (
                self.name, getlocstr(self)))

        # Cycle guard: a factory may not (transitively) reference itself.
        canon = self._canonical_url()
        chain = getattr(self, "_ivpm_source_chain", ())
        if canon in chain:
            cycle = " -> ".join(list(chain) + [canon])
            fatal("Cyclic ivpm.yaml factory reference: %s" % cycle)

        local_yaml = self._fetch_yaml(update_info)

        with open(local_yaml) as fp:
            proj = IvpmYamlReader().read(fp, local_yaml)

        # Stamp provenance on the leaves of the selected dep-set, and propagate
        # the include chain to any leaf that is itself a factory.
        if proj.has_dep_set(self.dep_set):
            child_chain = tuple(chain) + (canon,)
            origin = "%s#%s" % (self.url, self.dep_set)
            for leaf in proj.get_dep_set(self.dep_set).packages.values():
                leaf.from_ivpm_source = origin
                if getattr(leaf, "src_type", None) == "ivpm.yaml":
                    leaf._ivpm_source_chain = child_chain

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

    def get_lock_entry(self):
        # Virtual: emitted under the lock's ``ivpm_sources`` map, not the
        # normal ``packages`` map (see package_lock.write_lock).
        return {
            "src": "ivpm.yaml",
            "url": self.url,
            "dep_set": self.dep_set,
            "fingerprint": self.resolved_fingerprint,
            "reproducible": True,
            "virtual": True,
        }

    def spec_matches_lock(self, lock_entry):
        return (
            self.url == lock_entry.get("url")
            and self.dep_set == lock_entry.get("dep_set")
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
                ParamInfo("dep-set", "Name of the dep-set to pull from the factory "
                          "(default: the consuming dep-set's name)"),
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
