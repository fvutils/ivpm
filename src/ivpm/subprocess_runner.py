#****************************************************************************
#* subprocess_runner.py
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
"""
Subprocess utilities with output capture support.
"""
import logging
import subprocess
from typing import List, Optional, Tuple, Union

_logger = logging.getLogger("ivpm.subprocess")


class SubprocessRunner:
    """
    Utility for running subprocesses with output capture.
    
    Supports both capturing output for logging and passing through
    for verbose display.
    """
    
    @staticmethod
    def run(
        cmd: Union[str, List[str]],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        capture_output: bool = True,
        check: bool = False,
        timeout: Optional[float] = None
    ) -> Tuple[int, str, str]:
        """
        Run a subprocess command.
        
        Args:
            cmd: Command to run (string or list of strings)
            cwd: Working directory
            env: Environment variables
            capture_output: If True, capture stdout/stderr; if False, pass through
            check: If True, raise CalledProcessError on non-zero return
            timeout: Timeout in seconds
            
        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        if isinstance(cmd, str):
            shell = True
        else:
            shell = False
        
        _logger.debug("Running command: %s", cmd)
        
        if capture_output:
            result = subprocess.run(
                cmd,
                shell=shell,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            
            if stdout:
                _logger.debug("stdout: %s", stdout)
            if stderr:
                _logger.debug("stderr: %s", stderr)
            
            if check and result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode, cmd, stdout, stderr
                )
            
            return (result.returncode, stdout, stderr)
        else:
            result = subprocess.run(
                cmd,
                shell=shell,
                cwd=cwd,
                env=env,
                timeout=timeout
            )
            
            if check and result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, cmd)
            
            return (result.returncode, "", "")
    
    @staticmethod
    def run_with_logging(
        cmd: Union[str, List[str]],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        timeout: Optional[float] = None,
        verbose: bool = False
    ) -> Tuple[int, str, str]:
        """
        Run a subprocess and log output appropriately.
        
        Args:
            cmd: Command to run
            cwd: Working directory
            env: Environment variables
            timeout: Timeout in seconds
            verbose: If True, show output in real-time; if False, capture for logging
            
        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        return SubprocessRunner.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=not verbose,
            timeout=timeout
        )
