#!/usr/bin/env python3
"""Cross-release cache interop: older IVPM releases sharing a cache with this one.

A shared cache is used by several releases at once, and the requirement is
that no release is ever served the wrong entry (cache-naming-design.md).  This
runs the *real* cache code of each older release (``git archive <tag> src``)
against one cache directory together with the working tree's:

1. this tree publishes every key kind, protected and not;
2. the old release looks them all up (and runs its normal-operation sweep);
3. the old release publishes the same keys;
4. both look everything up again.

Every hit must hold the version (and protection) that was asked for.  Shared
plain keys are expected to hit across releases; everything else is expected to
miss rather than cross over.

    python test/stress/cache_interop.py v2.37.0 v2.40.2

Needs the tags in the local clone (not a shallow CI checkout).  Exit status is
non-zero if any wrong entry is served.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

#: Raw versions as the package types produce them.
KEYS = [
    "0123456789abcdef0123456789abcdef01234567",      # git commit
    "v1.2.3_0123456789ab",                            # gh-rls tag + digest
    "5d8c72a5edda8d6a:3239_0123456789ab",             # http, IIS-style ETag
    "rL0Y20zC+Fzt72VPzMSk2A==_0123456789ab",          # http, base64 ETag
    "v1.staging.2_0123456789ab",                      # machinery-shaped tag
]

_STORE = r'''
import os, sys, json
from ivpm.cache import DirectoryCacheStore
try:                                   # protection arrived in 2.36
    from ivpm.protection import ProtectionPolicy
    POLICIES = (None, ProtectionPolicy(gid=os.getgid()))
except ImportError:
    POLICIES = (None,)
cache, work, tag, keys = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
st = DirectoryCacheStore(cache)
for i, k in enumerate(keys):
    for p in POLICIES:
        src = os.path.join(work, "%s_%d_%s" % (tag, i, p is not None))
        os.makedirs(src)
        with open(os.path.join(src, "who"), "w") as fp:
            fp.write("%s|%s|%s" % (tag, k, p is not None))
        if p is None:
            st.store_version("libX", k, src)
        else:
            st.store_version("libX", k, src, policy=p)
print(json.dumps({}))
'''

_LOOKUP = r'''
import os, sys, json
from ivpm.cache import DirectoryCacheStore
try:                                   # protection arrived in 2.36
    from ivpm.protection import ProtectionPolicy
    POLICIES = (None, ProtectionPolicy(gid=os.getgid()))
except ImportError:
    POLICIES = (None,)
cache, keys = sys.argv[1], json.loads(sys.argv[2])
st = DirectoryCacheStore(cache)
out = {}
for k in keys:
    for p in POLICIES:
        kw = {} if p is None else {"policy": p}
        who = None
        if st.has_version("libX", k, **kw):
            with open(os.path.join(st.get_version_cache_dir("libX", k, **kw),
                                   "who")) as fp:
                who = fp.read()
        out["%s|%s" % (k, p is not None)] = who
st.ensure_cache_dir("libX")          # the opportunistic, normal-operation sweep
print(json.dumps(out))
'''


def _run(src_root, code, *args):
    prog = "import sys; sys.path.insert(0, %r)\n" % os.path.join(src_root, "src")
    r = subprocess.run([sys.executable, "-c", prog + code] + list(args),
                       capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stderr)
        raise SystemExit("cache code at %s failed" % src_root)
    return json.loads(r.stdout.strip().splitlines()[-1])


def _check(release, label, view):
    ok = True
    for key, who in sorted(view.items()):
        k, prot = key.rsplit("|", 1)
        if who is None:
            status = "miss"
        else:
            wtag, wk, wprot = who.split("|")
            right = wk == k and wprot == prot
            ok &= right
            status = "hit (%s-written)%s" % (wtag, "" if right
                                             else "  <-- WRONG ENTRY")
        print("%-8s %-27s %-40s protected=%-5s %s"
              % (release, label, k, prot, status))
    return ok


def main(tags):
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        for tag in tags:
            old = os.path.join(tmp, "src-" + tag)
            os.makedirs(old)
            archive = subprocess.run(["git", "-C", REPO, "archive", tag, "src"],
                                     capture_output=True, check=True).stdout
            subprocess.run(["tar", "-x", "-C", old], input=archive, check=True)

            cache = os.path.join(tmp, tag, "cache")
            work = os.path.join(tmp, tag, "work")
            os.makedirs(work)
            keys = json.dumps(KEYS)
            _run(REPO, _STORE, cache, work, "new", keys)
            ok &= _check(tag, "old reads new-written", _run(old, _LOOKUP, cache, keys))
            _run(old, _STORE, cache, work, "old", keys)
            ok &= _check(tag, "new reads mixed", _run(REPO, _LOOKUP, cache, keys))
            ok &= _check(tag, "old reads mixed", _run(old, _LOOKUP, cache, keys))
            print()
    print("no wrong entries" if ok else "WRONG ENTRIES SERVED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["v2.37.0", "v2.40.2"]))
