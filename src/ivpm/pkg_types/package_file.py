#****************************************************************************
#* package_file.py
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
import ntpath
import os
import posixpath
import shutil
import tarfile
from zipfile import ZipFile
import dataclasses as dc
from .package_url import PackageURL
from ..proj_info import ProjInfo
from ..project_ops_info import ProjectUpdateInfo
from ..utils import getlocstr

@dc.dataclass
class PackageFile(PackageURL):
    unpack : bool = None

    def update(self, update_info : ProjectUpdateInfo) -> ProjInfo:
        # Report this package for cache statistics (file packages are not cacheable by default)
        update_info.report_package(cacheable=False)

        pkg_dir = os.path.join(update_info.deps_dir, self.name)
        self.path = pkg_dir.replace("\\", "/")

        if not os.path.isdir(pkg_dir):
            # TODO: need to fetch the source...

            # Install (unpack) the file 
            if self.unpack:
                self._install(self.url, pkg_dir)

        if os.path.isdir(pkg_dir):
            return ProjInfo.mkFromProj(pkg_dir)
        else:
            return None
    
    def _install(self, pkg_src, pkg_path):
        if self.src_type in (".tar.gz", ".tar.xz", ".tar.bz2"):
            self._install_tgz(pkg_src, pkg_path)
        elif self.src_type in (".jar", ".zip"):
            self._install_zip(pkg_src, pkg_path)
        else:
            hint = ""
            if self.url and ("github.com" in self.url or self.url.endswith(".git") or "/" in self.url.rstrip("/")):
                hint = "\n  Hint: if this is a git repository, add 'src: git' to the package entry."
            raise Exception(
                "Package '%s': unsupported archive type '%s' (url: %s) @ %s\n"
                "  Supported types: .tar.gz, .tar.xz, .tar.bz2, .jar, .zip%s" % (
                    self.name, self.src_type if self.src_type else "<none detected>",
                    self.url, getlocstr(self), hint))

    def _install_tgz(self, pkg_src, pkg_path):
        pkg_path = os.path.abspath(pkg_path)
        tf = tarfile.open(pkg_src)

        for fi in tf:
            if fi.name.find("/") != -1:
                # Strip the first path component from the name
                first_slash = fi.name.find("/")
                fi.name = fi.name[first_slash+1:]

                # A hard link's linkname is relative to the archive root, so it
                # needs the same leading component stripped as the member name.
                # A *symbolic* link's target is relative to the link's own
                # directory (or absolute) -- both endpoints move together when
                # the root is stripped, so rewriting it corrupts the target. It
                # used to be stripped too, which shifted every relative target
                # up one level and made filter='data' reject real source
                # archives (uv, OpenROAD-flow-scripts) as linking outside the
                # destination.
                if fi.islnk():
                    if fi.linkname.find("/") != -1:
                        first_slash_link = fi.linkname.find("/")
                        fi.linkname = fi.linkname[first_slash_link+1:]

                if fi.issym():
                    # Symlinks are extracted by hand -- see _extract_symlink.
                    self._extract_symlink(fi, pkg_path)
                    continue

                # filter='data' sanitizes each member (rejects absolute paths,
                # '..' traversal, and links pointing outside pkg_path). This is
                # the default in Python 3.14; setting it explicitly silences the
                # 3.12+ DeprecationWarning and keeps behavior consistent.
                tf.extract(fi, path=pkg_path, filter='data')
        tf.close()

    def _extract_symlink(self, fi, pkg_path):
        """Create one symlink member, checking containment ourselves.

        Python's 'data' filter resolves a symlink target against the extraction
        root rather than against the link's own directory before 3.12
        (3.10.12/3.11.4 carry that backport, and CI runs 3.10). There, every
        relative target beginning with '..' is rejected even when it stays well
        inside the archive -- which is most of them in a real source tree. The
        containment rule we want is the 3.12+ one, so apply it directly: resolve
        the target against the link's directory and require the result to stay
        within the package.
        """
        if posixpath.isabs(fi.linkname) or ntpath.isabs(fi.linkname):
            raise tarfile.AbsoluteLinkError(fi)

        # Containment of the link itself (filter='data' would have checked this).
        name = posixpath.normpath(fi.name)
        if name == ".." or name.startswith("../"):
            raise tarfile.OutsideDestinationError(fi, name)

        target = posixpath.normpath(posixpath.join(posixpath.dirname(name), fi.linkname))
        if target == ".." or target.startswith("../"):
            raise tarfile.LinkOutsideDestinationError(fi, target)

        link_path = os.path.join(pkg_path, name.replace("/", os.sep))
        link_dir = os.path.dirname(link_path)
        if link_dir:
            os.makedirs(link_dir, exist_ok=True)
        if os.path.lexists(link_path):
            os.unlink(link_path)
        os.symlink(fi.linkname, link_path)

    def _install_zip(self, pkg_src, pkg_path):
        pkg_src = os.path.abspath(pkg_src)
        pkg_path = os.path.abspath(pkg_path)

        if os.path.exists(pkg_path):
            shutil.rmtree(pkg_path)

        with ZipFile(pkg_src, 'r') as zipObj:
            for zi in zipObj.infolist():
                dest = zipObj.extract(zi, pkg_path)

                # zipfile discards the unix mode recorded in external_attr, so
                # an extracted tool distribution (protoc, verilator, ...) comes
                # out non-executable and unusable. Restore the permission bits
                # for archives created on unix (create_system 3), masked to
                # rwx-only: setuid/setgid/sticky from an archive are not
                # something we want to honor.
                if zi.create_system == 3 and not zi.is_dir():
                    mode = (zi.external_attr >> 16) & 0o777
                    if mode:
                        os.chmod(dest, mode)

    @classmethod
    def dep_keys(cls):
        return super().dep_keys() | {"unpack"}

    def process_options(self, opts, si):
        super().process_options(opts, si)

        if "src" in opts.keys():
            self.src_type = opts["src"]
        else:
            ext = os.path.splitext(self.url)
            if ext == ".tgz":
                self.src_type = ".tar.gz"
            else:
                self.src_type = os.path.splitext(self.url)[1]
                if self.src_type in [".gz", ".xz", ".bz2"]:
                    pdot = self.url.rfind('.')
                    pdot = self.url.rfind('.', 0, pdot-1)
                    self.src_type = self.url[pdot:]

        if "unpack" in opts.keys():
            self.unpack = opts["unpack"]
        else:
            if self.src_type in [".jar"]:
                self.unpack = False
            else:
                self.unpack = True

    @staticmethod
    def create(name, opts, si) -> 'PackageFile':
        pkg = PackageFile(name)
        pkg.process_options(opts, si)
        return pkg

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="file",
            description="Local or pre-fetched archive file (.tar.gz, .zip, .jar, etc.)",
            params=[
                ParamInfo("url", "Path or URL to the archive file", required=True, type_hint="url"),
                ParamInfo("unpack", "Unpack the archive into the packages directory (default: true except .jar)", type_hint="bool"),
                ParamInfo("cache", "Cache this package", type_hint="bool"),
            ],
        )

