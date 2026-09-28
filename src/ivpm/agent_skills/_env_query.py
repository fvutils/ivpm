#****************************************************************************
#* _env_query.py
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
"""Report the agent skills and plugins an environment provides.

This file is NOT imported by IVPM. Its text is run with ``python -c`` under
the *target* environment's interpreter (see ``query.py``), so it must:

- use the standard library only (IVPM is usually not installed there);
- run on every Python IVPM supports in a venv, including 3.9, where
  ``entry_points()`` takes no arguments and returns a group -> list dict;
- never let a misbehaving entry point spoil the result. Each one is loaded
  and called with stdout/stderr captured, and any failure is reported for
  that entry point alone.

The result is one JSON object on stdout between sentinel markers, so noise a
package prints outside the captured region cannot corrupt it.
"""
import io
import json
import os
import sys

BEGIN = "<<IVPM_SKILLS_JSON>>"
END = "<</IVPM_SKILLS_JSON>>"

# (group, kind). 'ivpm.skill' is the deprecated alias of 'agent.skills'; a
# name registered in both is reported once, from 'agent.skills'.
GROUPS = (("agent.skills", "skills"),
          ("ivpm.skill", "skills"),
          ("agent.plugins", "plugins"))

SHARE_SUBDIR = os.path.join("share", "agent-skills")


def _metadata():
    import importlib.metadata
    return importlib.metadata


def _eps(group):
    md = _metadata()
    if sys.version_info >= (3, 10):
        return list(md.entry_points(group=group))
    return list(md.entry_points().get(group, []))


_EP_DISTS = None


def _dist_of(group, ep):
    """(name, version) of the distribution that registered ``ep``."""
    dist = getattr(ep, "dist", None)
    if dist is not None:
        try:
            return dist.metadata["Name"], dist.version
        except Exception:
            pass
    # Python < 3.10: EntryPoint has no .dist. Build the map once.
    global _EP_DISTS
    if _EP_DISTS is None:
        _EP_DISTS = {}
        for d in _metadata().distributions():
            try:
                for e in d.entry_points:
                    _EP_DISTS[(e.group, e.name, e.value)] = (d.metadata["Name"], d.version)
            except Exception:
                continue
    return _EP_DISTS.get((group, ep.name, ep.value), (None, None))


def _as_paths(value):
    """Normalize an entry point's return value to a list of path strings.

    Returns (paths, None) or (None, error).
    """
    if isinstance(value, bytes):
        return [os.fsdecode(value)], None
    if isinstance(value, str) or hasattr(value, "__fspath__"):
        return [os.fspath(value)], None
    try:
        items = list(value)
    except TypeError:
        return None, ("returned %s; expected a path or an iterable of paths"
                      % type(value).__name__)
    paths = []
    for item in items:
        if isinstance(item, bytes):
            paths.append(os.fsdecode(item))
        elif isinstance(item, str) or hasattr(item, "__fspath__"):
            paths.append(os.fspath(item))
        else:
            return None, ("returned an item of type %s; expected a path"
                          % type(item).__name__)
    return paths, None


def _call_ep(ep):
    """Load and call one entry point with its output captured."""
    saved = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = io.StringIO()
    try:
        obj = ep.load()
        return obj() if callable(obj) else obj
    finally:
        sys.stdout, sys.stderr = saved


def query_entry_points():
    entries = []
    errors = []
    seen = set()
    for group, kind in GROUPS:
        for ep in _eps(group):
            if (kind, ep.name) in seen:
                continue
            dist, version = _dist_of(group, ep)
            try:
                value = _call_ep(ep)
            except BaseException as exc:  # SystemExit from a CLI module, too
                if isinstance(exc, KeyboardInterrupt):
                    raise
                errors.append({"group": group, "name": ep.name, "dist": dist,
                               "error": "%s: %s" % (type(exc).__name__, exc)})
                continue
            paths, err = _as_paths(value)
            if err is not None:
                errors.append({"group": group, "name": ep.name, "dist": dist,
                               "error": err})
                continue
            seen.add((kind, ep.name))
            entries.append({"group": group, "kind": kind, "name": ep.name,
                            "dist": dist, "version": version,
                            "dirs": [os.path.normpath(p) for p in paths]})
    return entries, errors


def _share_owners(share_dir):
    """Map each skill dir under share_dir to (dist, version) via RECORD."""
    owners = {}
    marker = "share/agent-skills/"
    real_share = os.path.realpath(share_dir)
    try:
        dists = list(_metadata().distributions())
    except Exception:
        return owners
    for d in dists:
        try:
            files = d.files or []
        except Exception:
            continue
        for f in files:
            if marker not in str(f).replace("\\", "/"):
                continue
            try:
                full = os.path.realpath(str(d.locate_file(f)))
            except Exception:
                continue
            rel = os.path.relpath(full, real_share)
            if rel.startswith(".."):
                continue
            top = rel.split(os.sep)[0]
            owners.setdefault(top, (d.metadata["Name"], d.version))
    return owners


def _prefixes():
    """Every installation prefix whose share/ may hold skills.

    Usually just sys.prefix. A layered environment -- 'uv run --with', for
    one -- keeps each layer's packages under its own prefix, so each
    site-packages directory (<prefix>/lib/pythonX.Y/site-packages, or
    <prefix>/Lib/site-packages on Windows) contributes its prefix too.
    """
    out = [sys.prefix]
    for p in sys.path:
        if os.path.basename(p) != "site-packages":
            continue
        parent = os.path.dirname(p)
        if os.path.basename(parent).lower() == "lib":
            prefix = os.path.dirname(parent)
        else:
            prefix = os.path.dirname(os.path.dirname(parent))
        out.append(prefix)
    seen = set()
    result = []
    for p in out:
        key = os.path.realpath(p)
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def query_share():
    result = []
    for prefix in _prefixes():
        share_dir = os.path.join(prefix, SHARE_SUBDIR)
        if not os.path.isdir(share_dir):
            continue
        owners = _share_owners(share_dir)
        for name in sorted(os.listdir(share_dir)):
            skill_dir = os.path.join(share_dir, name)
            if not os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
                continue
            dist, version = owners.get(name, (None, None))
            result.append({"dir": skill_dir, "dist": dist, "version": version})
    return result


def main():
    entries, errors = query_entry_points()
    result = {
        "python": sys.executable,
        "prefix": sys.prefix,
        "version": "%d.%d.%d" % sys.version_info[:3],
        "entries": entries,
        "share": query_share(),
        "errors": errors,
    }
    sys.stdout.write(BEGIN + json.dumps(result) + END + "\n")


if __name__ == "__main__":
    main()
