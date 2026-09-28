#****************************************************************************
#* query.py
#*
#* Copyright 2026 Matthew Ballance and Contributors
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
"""Query a Python environment for the agent skills and plugins it provides.

The query runs ``_env_query.py`` under the environment's own interpreter:
entry points must be loaded where they are installed, never in IVPM's own
process (a package's import may need native libraries or a Python version we
do not have).
"""
import dataclasses as dc
import json
import os
import re
import subprocess
from typing import List, Optional, Sequence

_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_env_query.py")
_RESULT_RE = re.compile(r"<<IVPM_SKILLS_JSON>>(.*?)<</IVPM_SKILLS_JSON>>", re.DOTALL)

DEFAULT_TIMEOUT = 60


class QueryError(Exception):
    """The environment could not be queried at all."""


@dc.dataclass
class EntryPointResult(object):
    group: str
    kind: str          # 'skills' or 'plugins'
    name: str
    dist: Optional[str]
    version: Optional[str]
    dirs: List[str]


@dc.dataclass
class ShareSkill(object):
    dir: str
    dist: Optional[str]
    version: Optional[str]


@dc.dataclass
class EntryPointError(object):
    group: str
    name: str
    dist: Optional[str]
    error: str

    def __str__(self):
        who = "%s (%s%s)" % (self.name, self.group,
                             ", from %s" % self.dist if self.dist else "")
        return "entry point %s: %s" % (who, self.error)


@dc.dataclass
class QueryResult(object):
    python: str
    prefix: str
    version: str
    entries: List[EntryPointResult] = dc.field(default_factory=list)
    share: List[ShareSkill] = dc.field(default_factory=list)
    errors: List[EntryPointError] = dc.field(default_factory=list)

    @property
    def skill_entries(self) -> List[EntryPointResult]:
        return [e for e in self.entries if e.kind == "skills"]

    @property
    def plugin_entries(self) -> List[EntryPointResult]:
        return [e for e in self.entries if e.kind == "plugins"]

    @classmethod
    def from_json(cls, data: dict) -> "QueryResult":
        return cls(
            python=data.get("python", ""),
            prefix=data.get("prefix", ""),
            version=data.get("version", ""),
            entries=[EntryPointResult(
                group=e.get("group", "agent.skills"),
                # Older payloads carry no 'kind'; they were always skills.
                kind=e.get("kind", "skills"),
                name=e["name"],
                dist=e.get("dist"),
                version=e.get("version"),
                dirs=list(e.get("dirs", []))) for e in data.get("entries", [])],
            share=[ShareSkill(dir=s["dir"], dist=s.get("dist"), version=s.get("version"))
                   for s in data.get("share", [])],
            errors=[EntryPointError(group=e.get("group", ""), name=e.get("name", ""),
                                    dist=e.get("dist"), error=e.get("error", ""))
                    for e in data.get("errors", [])])


def script_text() -> str:
    with open(_SCRIPT) as fh:
        return fh.read()


def run_query(argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT,
              env: Optional[dict] = None, cwd: Optional[str] = None) -> QueryResult:
    """Run the query with ``argv`` as the interpreter command.

    ``argv`` is ``[python]`` for an existing environment, or a launcher
    prefix such as ``[uv, 'run', ..., 'python']``. The script is passed with
    ``-c`` rather than as a file so the interpreter does not put IVPM's
    package directory on ``sys.path``.
    """
    cmd = list(argv) + ["-c", script_text()]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise QueryError("query timed out after %ds" % timeout)
    except OSError as exc:
        raise QueryError("could not run %s: %s" % (argv[0], exc))

    m = _RESULT_RE.search(r.stdout or "")
    if r.returncode != 0 or not m:
        detail = (r.stderr or "").strip() or (r.stdout or "").strip()
        if r.returncode == 0:
            detail = "no parseable output (stdout pollution?)"
        raise QueryError("query failed (exit %d): %s" % (r.returncode, detail))
    try:
        return QueryResult.from_json(json.loads(m.group(1)))
    except (ValueError, KeyError) as exc:
        raise QueryError("query produced malformed output: %s" % exc)


def query_env(python: str, timeout: float = DEFAULT_TIMEOUT,
              env: Optional[dict] = None) -> QueryResult:
    """Query the environment of interpreter ``python``.

    The process environment is inherited unless ``env`` is given, so a
    PYTHONPATH the workspace sets (direnv, say) is honored.
    """
    return run_query([python], timeout=timeout, env=env)
