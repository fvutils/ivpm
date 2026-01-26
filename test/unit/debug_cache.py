#!/usr/bin/env python3
import os
import sys
import stat
import shutil

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

from test_base import TestBase

tb = TestBase()
tb.setUp()

# Test with cache
print("=" * 60)
print("Testing with cache=true and cache=false/None")
print("=" * 60)

tb.mkFile("ivpm.yaml", """
package:
    name: test_debug_cache
    dep-sets:
        - name: default-dev
          deps:
            - name: pkg_cached
              url: https://github.com/fvutils/vlsim.git
              anonymous: true
              cache: true
            - name: pkg_no_cache
              url: https://github.com/fvutils/vlsim.git
              anonymous: true
              cache: false
            - name: pkg_editable
              url: https://github.com/fvutils/vlsim.git
              anonymous: true
""")

print("\nRunning ivpm update...")
tb.ivpm_update(skip_venv=True)

print("\nChecking packages...")
for pkg_name in ["pkg_cached", "pkg_no_cache", "pkg_editable"]:
    pkg_path = os.path.join(tb.testdir, f"packages/{pkg_name}")
    if os.path.exists(pkg_path):
        is_link = os.path.islink(pkg_path)
        if is_link:
            target = os.readlink(pkg_path)
            target_mode = os.stat(target).st_mode
            is_writable = bool(target_mode & stat.S_IWUSR)
            print(f"\n{pkg_name}:")
            print(f"  Type: symlink -> {target}")
            print(f"  Target writable: {is_writable}")
            print(f"  Target mode: {oct(target_mode)}")
        else:
            pkg_mode = os.stat(pkg_path).st_mode
            is_writable = bool(pkg_mode & stat.S_IWUSR)
            print(f"\n{pkg_name}:")
            print(f"  Type: directory")
            print(f"  Writable: {is_writable}")
            print(f"  Mode: {oct(pkg_mode)}")
    else:
        print(f"\n{pkg_name}: NOT FOUND")

print("\n" + "=" * 60)
