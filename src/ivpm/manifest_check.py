#****************************************************************************
#* manifest_check.py
#*
#* Deciding whether a package's language manifest is one IVPM can install
#* from, and saying precisely why when it is not.
#*
#* Two failures look identical from the outside and are not:
#*
#*   malformed   -- the file does not parse. Truncated JSON, broken TOML.
#*                  Rare, and something on disk is genuinely wrong.
#*   not a target -- the file parses and is perfectly valid, but is not an
#*                  installable project: a pyproject.toml carrying only
#*                  [tool.ruff], a package.json that is a workspace root.
#*                  Common, benign, and the reason auto-detection so often
#*                  enrolls packages that were never Python or Node packages.
#*
#* Validation catches only the first. That is the whole reason these checks
#* are a frequency reduction and not a safety net: a manifest can pass every
#* gate here and still fail to install, which is what content_attrib.py
#* exists to handle. Nothing downstream may assume a package that got past
#* these checks will install.
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
#****************************************************************************
import configparser
import dataclasses as dc
import json
import logging
import os
import re
from typing import Optional

_logger = logging.getLogger("ivpm.manifest_check")


@dc.dataclass
class ManifestDiag:
    """The verdict on one package's manifest.

    ``ok`` means "install from this". Otherwise ``malformed`` distinguishes
    "this file is broken" from "this file is fine but is not a project",
    which is what decides how loudly the decline is reported.
    """
    ok        : bool
    path      : str
    reason    : str = ""
    line      : Optional[int] = None
    col       : Optional[int] = None
    malformed : bool = False

    def located(self) -> str:
        """``path``, with line and column when the parser gave them.

        A parse error the user cannot navigate to is barely better than no
        parse error, so line and column are carried whenever available.
        """
        if self.line is None:
            return self.path
        if self.col is None:
            return "%s:%d" % (self.path, self.line)
        return "%s:%d:%d" % (self.path, self.line, self.col)

    def describe(self) -> str:
        return "%s: %s" % (self.located(), self.reason)


# --------------------------------------------------------------------------
# TOML
# --------------------------------------------------------------------------

# tomllib does not expose line/col as attributes the way json does; it puts
# them in the message ("... (at line 3, column 5)"). Digging them out is worth
# it so a broken pyproject.toml reports as precisely as a broken package.json.
_TOML_POS_RE = re.compile(r"at line (\d+), column (\d+)")


def _toml_error_pos(exc):
    m = _TOML_POS_RE.search(str(exc))
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _load_toml(path):
    """Return ``(data, ManifestDiag)``; exactly one of the two is None."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    try:
        with open(path, "rb") as fp:
            return tomllib.load(fp), None
    except Exception as e:
        line, col = _toml_error_pos(e)
        return None, ManifestDiag(
            ok=False, path=path, malformed=True, line=line, col=col,
            reason="could not be parsed as TOML (%s)" % e)


# --------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------

def check_python_manifest(pkg_path: str) -> Optional[ManifestDiag]:
    """Is the tree at *pkg_path* something pip/uv can install from?

    ``None`` means the handler claims nothing here: there is no Python
    metadata at all, so there is nothing to be right or wrong about.
    """
    if not pkg_path or not os.path.isdir(pkg_path):
        return None

    pyproject = os.path.join(pkg_path, "pyproject.toml")
    setup_py  = os.path.join(pkg_path, "setup.py")
    setup_cfg = os.path.join(pkg_path, "setup.cfg")

    has_pyproject = os.path.isfile(pyproject)
    has_setup_py  = os.path.isfile(setup_py)
    has_setup_cfg = os.path.isfile(setup_cfg)

    if not (has_pyproject or has_setup_py or has_setup_cfg):
        return None

    if has_pyproject:
        data, diag = _load_toml(pyproject)
        if diag is not None:
            return diag

        if isinstance(data.get("project"), dict) and data["project"].get("name"):
            return ManifestDiag(ok=True, path=pyproject)

        # A pyproject.toml with a build-system but no [project] is a valid
        # setuptools project whose metadata lives in setup.py/setup.cfg.
        if isinstance(data.get("build-system"), dict) and \
                (has_setup_py or has_setup_cfg):
            return ManifestDiag(ok=True, path=pyproject)

        if not has_setup_py and not has_setup_cfg:
            keys = ", ".join(sorted(k for k in data.keys())) or "nothing"
            return ManifestDiag(
                ok=False, path=pyproject,
                reason="declares no '[project] name' and there is no setup.py "
                       "or setup.cfg alongside it, so there is no package to "
                       "install (it contains: %s)" % keys)

    # setup.py cannot be validated without executing it, and executing an
    # arbitrary dependency's setup.py to decide whether to install it is not a
    # trade worth making. Its presence is taken as the declaration.
    if has_setup_py:
        return ManifestDiag(ok=True, path=setup_py)

    if has_setup_cfg:
        parser = configparser.ConfigParser()
        try:
            parser.read(setup_cfg)
        except configparser.Error as e:
            line = getattr(e, "lineno", None)
            return ManifestDiag(
                ok=False, path=setup_cfg, malformed=True, line=line,
                reason="could not be parsed (%s)" % e)
        if parser.has_option("metadata", "name"):
            return ManifestDiag(ok=True, path=setup_cfg)
        return ManifestDiag(
            ok=False, path=setup_cfg,
            reason="declares no '[metadata] name', so there is no package to "
                   "install")

    return None


# --------------------------------------------------------------------------
# Node
# --------------------------------------------------------------------------

def check_node_manifest(pkg_path: str) -> Optional[ManifestDiag]:
    """Is the tree at *pkg_path* something npm can install as a dependency?

    ``None`` means no package.json at all -- nothing claimed either way.
    """
    if not pkg_path:
        return None

    path = os.path.join(pkg_path, "package.json")
    if not os.path.isfile(path):
        return None

    try:
        with open(path) as fp:
            data = json.load(fp)
    except json.JSONDecodeError as e:
        return ManifestDiag(
            ok=False, path=path, malformed=True,
            line=getattr(e, "lineno", None), col=getattr(e, "colno", None),
            reason="is not valid JSON (%s)" % e.msg)
    except OSError as e:
        return ManifestDiag(
            ok=False, path=path, malformed=True,
            reason="could not be read (%s)" % e)

    if not isinstance(data, dict):
        return ManifestDiag(
            ok=False, path=path, malformed=True,
            reason="is valid JSON but not an object")

    # A workspace root manages other packages; it is not itself installable as
    # a dependency, and npm will refuse it. Checked before name/version
    # because a workspace root usually has both.
    if data.get("workspaces"):
        return ManifestDiag(
            ok=False, path=path,
            reason="is a workspace root ('workspaces' is declared), which npm "
                   "cannot install as a dependency")

    name = data.get("name")
    if not isinstance(name, str) or not name:
        return ManifestDiag(
            ok=False, path=path,
            reason="declares no 'name', so npm has nothing to install it as")

    version = data.get("version")
    if not isinstance(version, str) or not version:
        return ManifestDiag(
            ok=False, path=path,
            reason="declares no 'version'; npm requires one to install a "
                   "'file:' dependency")

    return ManifestDiag(ok=True, path=path)
