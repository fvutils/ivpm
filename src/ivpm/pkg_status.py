#****************************************************************************
#* pkg_status.py
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
import dataclasses as dc
from typing import List, Optional


@dc.dataclass
class PkgVcsStatus:
    """Per-package VCS status returned by Package.status()."""
    name: str
    src_type: str           # "git", "dir", "pypi", …
    path: str               # absolute path in deps_dir
    vcs: str                # "git" | "none"
    branch: Optional[str] = None    # current branch; None if detached HEAD
    tag: Optional[str] = None       # exact tag at HEAD, else None
    commit: str = ""                # short HEAD hash (7 chars) or ""
    is_dirty: bool = False
    modified: List[str] = dc.field(default_factory=list)  # porcelain lines
    ahead: Optional[int] = None     # commits ahead of upstream; None = unknown
    behind: Optional[int] = None    # commits behind upstream; None = unknown
    error: Optional[str] = None     # set if status could not be determined
