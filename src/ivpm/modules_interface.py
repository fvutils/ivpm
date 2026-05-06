#****************************************************************************
#* modules_interface.py
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
ModulesInterface -- variant-aware subprocess wrapper for querying the
Environment Modules system (Modules 3.x/4.x and Lmod).

This is the single place where IVPM interacts with ``modulecmd.tcl`` or
``lmod`` subprocesses.  All methods call ``subprocess.run()`` and parse
stdout/stderr -- IVPM never calls ``exec()`` or evaluates modulecmd-generated
Python code.
"""
import dataclasses as dc
import enum
import logging
import os
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

_logger = logging.getLogger("ivpm.modules_interface")


class ModulesVariant(enum.Enum):
    MODULES_3X_TCL = "modules-3x"
    MODULES_4X     = "modules-4x"
    LMOD           = "lmod"
    UNKNOWN        = "unknown"


class ModulesError(Exception):
    """Raised when a modulecmd subprocess fails or returns unexpected output."""
    pass


@dc.dataclass
class ModulesInterface:
    """Variant-aware interface to the Environment Modules system.

    Use :func:`detect_variant` to create an instance, or construct
    directly when the variant and command path are known (e.g. from
    explicit handler config).
    """
    variant: ModulesVariant = ModulesVariant.UNKNOWN
    cmd_path: Optional[str] = None       # path to modulecmd.tcl or lmod
    tclsh_path: Optional[str] = None     # path to tclsh (Modules 3.x/4.x)

    # ------------------------------------------------------------------ #
    # Public query methods                                                #
    # ------------------------------------------------------------------ #

    def is_avail(self, module: str) -> bool:
        """Check whether *module* is available in the current MODULEPATH."""
        self._require_known()
        try:
            if self.variant == ModulesVariant.LMOD:
                r = self._run_lmod(["avail", "--terse", module], check=False)
                return bool(r.stderr.strip())
            else:
                r = self._run_modulecmd(["avail", "--terse", module], check=False)
                return bool(r.stderr.strip())
        except ModulesError:
            return False

    def module_path(self, module: str) -> Optional[str]:
        """Return the absolute path to the modulefile for *module*, or None."""
        self._require_known()
        try:
            if self.variant == ModulesVariant.LMOD:
                return self._module_path_lmod(module)
            elif self.variant == ModulesVariant.MODULES_4X:
                return self._module_path_4x(module)
            else:
                return self._module_path_3x(module)
        except ModulesError as e:
            _logger.debug("module_path(%s) failed: %s", module, e)
            return None

    def module_show(self, module: str) -> str:
        """Return raw ``module show`` output (stderr) for *module*."""
        self._require_known()
        if self.variant == ModulesVariant.LMOD:
            r = self._run_lmod(["show", module])
            return r.stderr
        else:
            r = self._run_modulecmd(["show", module])
            return r.stderr

    def avail(self, pattern: str = "") -> List[str]:
        """List available modules matching *pattern*."""
        self._require_known()
        if self.variant == ModulesVariant.LMOD:
            r = self._run_lmod(["avail", "--terse", pattern], check=False)
            raw = r.stderr
        else:
            r = self._run_modulecmd(["avail", "--terse", pattern], check=False)
            raw = r.stderr
        # Parse terse output: one module per line, skip directory headers
        modules = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.endswith(":") or line.startswith("-"):
                continue
            line = re.sub(r'\(default\)$', '', line).strip()
            if line:
                modules.append(line)
        return modules

    # ------------------------------------------------------------------ #
    # Variant-specific module_path implementations                        #
    # ------------------------------------------------------------------ #

    def _module_path_4x(self, module: str) -> Optional[str]:
        """Modules 4.x: ``modulecmd.tcl python path <module>``."""
        r = self._run_modulecmd(["path", module])
        path = r.stderr.strip()
        if not path:
            path = self._parse_path_stdout(r.stdout)
        return path if path and os.path.exists(path) else None

    def _module_path_3x(self, module: str) -> Optional[str]:
        """Modules 3.x: ``tclsh modulecmd.tcl python path <module>``.

        Some AMD variants emit ``execfile('/tmp/modulescript_...')`` on
        stdout instead of the real modulefile path.  The temp script
        contains ``result.append('/real/path/to/modulefile')``.  When
        the initial parse returns a temp-script path we read the script
        to extract the real path, falling back to ``module show``.
        """
        r = self._run_modulecmd(["path", module])
        path = r.stderr.strip()
        if not path:
            path = self._parse_path_stdout(r.stdout)

        # Detect AMD modulecmd temp-script paths and resolve the real
        # modulefile path from the script contents or module show.
        if path and os.path.basename(path).startswith("modulescript_"):
            real = self._resolve_path_from_modulescript(path)
            if real is None:
                real = self._module_path_from_show(module)
            if real is not None:
                path = real

        return path if path and os.path.exists(path) else None

    def _resolve_path_from_modulescript(self, script_path: str) -> Optional[str]:
        """Read an AMD modulecmd temp script and extract the real modulefile path."""
        try:
            with open(script_path) as fh:
                for line in fh:
                    m = re.match(r"\s*result\.append\(['\"](.*?)['\"]\)", line)
                    if m:
                        candidate = m.group(1)
                        if os.path.exists(candidate):
                            return candidate
        except OSError:
            pass
        return None

    def _module_path_from_show(self, module: str) -> Optional[str]:
        """Extract the modulefile path from ``module show`` output.

        Works like _module_path_lmod: the first line that is a valid
        filesystem path (after stripping a trailing colon) is taken as
        the modulefile location.
        """
        try:
            show_output = self.module_show(module)
        except ModulesError:
            return None
        for line in show_output.splitlines():
            line = line.strip().rstrip(":")
            if line and os.path.exists(line):
                return line
        return None

    def _module_path_lmod(self, module: str) -> Optional[str]:
        """Lmod: parse ``$LMOD_CMD python show <module>`` stderr for the filename line."""
        r = self._run_lmod(["show", module])
        for line in r.stderr.splitlines():
            line = line.strip().rstrip(":")
            if line and os.path.exists(line):
                return line
        return None

    @staticmethod
    def _parse_path_stdout(stdout: str) -> Optional[str]:
        """Extract a modulefile path from modulecmd stdout output."""
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("import ") or line.startswith("#"):
                continue
            m = re.search(r"['\"]([^'\"]+)['\"]", line)
            if m:
                candidate = m.group(1)
                if os.path.sep in candidate:
                    return candidate
            if line.startswith(os.path.sep):
                return line
        return None

    # ------------------------------------------------------------------ #
    # Subprocess helpers                                                   #
    # ------------------------------------------------------------------ #

    def _run_modulecmd(self, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run modulecmd.tcl with the appropriate invocation for the variant."""
        if self.variant == ModulesVariant.MODULES_3X_TCL:
            tclsh = self.tclsh_path or "tclsh"
            cmd = [tclsh, self.cmd_path, "python"] + args
        else:
            cmd = [self.cmd_path, "python"] + args
        _logger.debug("Running: %s", " ".join(cmd))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except FileNotFoundError as e:
            raise ModulesError("modulecmd not found: %s" % e) from e
        except subprocess.TimeoutExpired as e:
            raise ModulesError("modulecmd timed out: %s" % e) from e
        if check and r.returncode != 0:
            raise ModulesError(
                "modulecmd %s failed (rc=%d): %s" % (
                    " ".join(args), r.returncode, r.stderr.strip()))
        return r

    def _run_lmod(self, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run Lmod command."""
        cmd = [self.cmd_path, "python"] + args
        _logger.debug("Running: %s", " ".join(cmd))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except FileNotFoundError as e:
            raise ModulesError("lmod not found: %s" % e) from e
        except subprocess.TimeoutExpired as e:
            raise ModulesError("lmod timed out: %s" % e) from e
        if check and r.returncode != 0:
            raise ModulesError(
                "lmod %s failed (rc=%d): %s" % (
                    " ".join(args), r.returncode, r.stderr.strip()))
        return r

    def _require_known(self):
        """Raise ModulesError if variant is UNKNOWN."""
        if self.variant == ModulesVariant.UNKNOWN:
            raise ModulesError(
                "No Environment Modules installation detected. "
                "Set MODULESHOME or LMOD_CMD, or configure variant/modulecmd "
                "in handler_configs['modules'].")


# ---------------------------------------------------------------------- #
# Detection                                                               #
# ---------------------------------------------------------------------- #

def detect_variant(
    variant_override: Optional[str] = None,
    cmd_override: Optional[str] = None,
) -> ModulesInterface:
    """Probe the environment for the modules installation and return a configured interface.

    When *variant_override* or *cmd_override* are provided (from handler
    config), they take precedence over auto-detection.
    """
    if variant_override and variant_override != "auto":
        variant = ModulesVariant(variant_override)
        cmd = cmd_override
        tclsh = None
        if variant in (ModulesVariant.MODULES_3X_TCL, ModulesVariant.MODULES_4X):
            if cmd is None:
                cmd = _find_modulecmd()
            tclsh = shutil.which("tclsh")
        elif variant == ModulesVariant.LMOD:
            if cmd is None:
                cmd = os.environ.get("LMOD_CMD")
        return ModulesInterface(variant=variant, cmd_path=cmd, tclsh_path=tclsh)

    # Auto-detect
    lmod_cmd = os.environ.get("LMOD_CMD")
    if lmod_cmd and os.path.isfile(lmod_cmd):
        _logger.debug("Detected Lmod via LMOD_CMD=%s", lmod_cmd)
        return ModulesInterface(
            variant=ModulesVariant.LMOD,
            cmd_path=cmd_override or lmod_cmd)

    modules_home = os.environ.get("MODULESHOME")
    if modules_home:
        modulecmd = cmd_override or _find_modulecmd_in(modules_home)
        if modulecmd:
            tclsh = shutil.which("tclsh")
            variant = _probe_modules_version(modulecmd, tclsh)
            _logger.debug("Detected %s via MODULESHOME=%s", variant, modules_home)
            return ModulesInterface(
                variant=variant, cmd_path=modulecmd, tclsh_path=tclsh)

    modulecmd = cmd_override or _find_modulecmd()
    if modulecmd:
        tclsh = shutil.which("tclsh")
        variant = _probe_modules_version(modulecmd, tclsh)
        _logger.debug("Detected %s via PATH lookup", variant)
        return ModulesInterface(
            variant=variant, cmd_path=modulecmd, tclsh_path=tclsh)

    _logger.debug("No Environment Modules installation detected")
    return ModulesInterface(variant=ModulesVariant.UNKNOWN)


def _find_modulecmd_in(modules_home: str) -> Optional[str]:
    """Look for modulecmd.tcl inside a MODULESHOME directory."""
    for name in ("modulecmd.tcl", "libexec/modulecmd.tcl"):
        candidate = os.path.join(modules_home, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _find_modulecmd() -> Optional[str]:
    """Search PATH for modulecmd or modulecmd.tcl."""
    for name in ("modulecmd.tcl", "modulecmd"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _probe_modules_version(modulecmd: str, tclsh: Optional[str]) -> ModulesVariant:
    """Determine whether *modulecmd* is Modules 3.x or 4.x+ by running --version."""
    try:
        cmd = [modulecmd, "--version"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        output = r.stdout + r.stderr
        if re.search(r'Modules\s+Release\s+[4-9]', output):
            return ModulesVariant.MODULES_4X
        if re.search(r'VERSION\s*=\s*3', output) or re.search(r'Tcl\s+3', output):
            return ModulesVariant.MODULES_3X_TCL
    except Exception as e:
        _logger.debug("Version probe failed: %s", e)

    if tclsh and modulecmd.endswith(".tcl"):
        return ModulesVariant.MODULES_3X_TCL

    return ModulesVariant.MODULES_4X
