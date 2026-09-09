#****************************************************************************
#* package_url.py
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
import dataclasses as dc
from typing import Optional
from ..package import Package

@dc.dataclass
class PackageURL(Package):
    url : str = None
    cache : Optional[bool] = None  # True/False/None (unspecified)

    @classmethod
    def dep_keys(cls):
        return super().dep_keys() | {"url", "cache"}

    def process_options(self, opts, si):
        super().process_options(opts, si)

        if "url" in opts.keys():
            self.url = opts["url"]

        if "cache" in opts.keys():
            self.cache = bool(opts["cache"])

    @staticmethod
    def create(name, opts, si) -> 'PackageURL':
        """Build the concrete package ``src: url`` names.

        PackageURL is the abstract base of PackageDir/PackageFile/PackageHttp
        and has no ``update()`` of its own, so returning one made ``src: url``
        report success and fetch nothing at all. It resolves to the same type
        the reader would have auto-detected had ``src:`` been omitted -- which
        is what "the fetch method is resolved from the URL" has always meant.
        """
        from ..utils import fatal, getlocstr
        from .package_dir import PackageDir
        from .package_file import PackageFile
        from .package_http import PackageHttp

        url = opts.get("url")
        if url is None:
            fatal("Package '%s': 'src: url' requires a 'url' @ %s" % (
                name, getlocstr(opts)), opts)
        url = str(url)

        if url.endswith(".git"):
            fatal("Package '%s': 'src: url' cannot fetch a git repository "
                  "(url: %s) @ %s\n  Use 'src: git' instead." % (
                      name, url, getlocstr(opts)), opts)

        if url.startswith("http://") or url.startswith("https://"):
            cls = PackageHttp
        elif url.startswith("file://"):
            # A file:// archive is a PackageFile; a plain directory is a
            # PackageDir. The extension is what separates them.
            cls = PackageFile if PackageFile.ext_from_url(url) else PackageDir
        else:
            fatal("Package '%s': cannot determine how to fetch '%s' @ %s\n"
                  "  'src: url' understands http://, https:// and file:// "
                  "URLs; name another src explicitly for anything else." % (
                      name, url, getlocstr(opts)), opts)

        pkg = cls(name)
        pkg.process_options(opts, si)
        # process_options recorded the literal spec "url". Report the resolved
        # type instead, so the lock file records (and can reproduce) the entry
        # exactly as the auto-detected spelling does.
        if isinstance(pkg, PackageFile):
            pkg.src_type = pkg.archive_ext
        elif isinstance(pkg, PackageDir):
            pkg.src_type = "dir"
        return pkg

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="url",
            description="Generic URL (resolved by extension: .tar.gz, .zip, .jar, etc.)",
            params=[
                ParamInfo("url", "Package URL; source type inferred from file extension", required=True, type_hint="url"),
                ParamInfo("cache", "Cache this package (true=shared cache+symlink, false=no cache, omit=editable)", type_hint="bool"),
            ],
        )

