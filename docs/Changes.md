
# 2.4.0
- Add parallel fetch for packages

# 2.3.0
- Add support for package cache

# 2.2.0
- Add 'clone' command that automates setting up a workspace

# 2.1.0
- Have `uv` use non-isolated builds for Python packages. This enables source 
  binary package builds to find headers in other editable packages.

- Pull-through dep-set of super, vs defaulting to 'default' for sub-deps
- Store meta-data about packages in the packages directory (check/default dep-set)
  - Should have a 'default dep-set' setting
- Each package should be able to report out its full configuration post-update / post-sync
- Each package should be called on a 'snapshot' operation to clean up irrelevant data (eg .git)
- Should be able to specify manifest file via -f (-f last_tapeout.ivpm.yaml)
- Only reinstall Python packages if a change is detected (?)
- Replace git-update with sync
- Replace git-status with status
