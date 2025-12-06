
# IVPM Cache

## Configuration

Caching is only enabled when the `IVPM_CACHE` environment variable is set to point to the cache directory.

## Overview

It's often useful to cache package data:
- Reduces network traffic to checkout version-controlled files
- Reduces disk space for shared dependencies that aren't being edited

The IVPM dependency entry must be updated to support a 'cache: <bool>' 
attribute. Cache can be true, false, or unspecified.

The IVPM cache is organized by dependency name. For example:

- name: default-dev
  deps:
  - name: gtest
    url: https://github.com/google/google-test.git

In this case, the top-level cache entry is 'gtest'. 

Use of the cache is optional, with the package type class handling
the type-specific details of using the cache. IVPM provides a path 
to the package-specific cache directory to the package type class when
the cache is enabled.

Packages that use the cache are generally expected to:
- Be able to determine version information about the source 
- Have a means to determine whether a matching entry exists in the cache

Cached packages are always read-only

During the update operation, packages must signal whether they hit a
cache entry. The top-level update command must report cache hit rate.

## Git
When cache is not enabled, a cacheable git URL is cloned with no history,
and the contents are made read-only 

When cache is not enabled, non-cacheable git URLs (or packages without
cache specified) are cloned will full history by default

Only github repos are cacheable. Throw an error if cache is enabled,
a git URL package is specified as cacheable, and the URL is not github.

For github URLs, use the API to get the hash of the latest commit on
the specified branch. Check if the commit is in the cache. If so, symlink
the commit into the dependencies directory. If not, clone the package
without history into a cache-directory entry with the hash of the
latest commit. Make all files in the clone read-only, then symblink
it into the dependencies directory.

## General URL
When cache is not enabled, a cacheable general URL is fetched, unpacked,
and the contents are made read-only 

When the cache is enabled, a cacheable general URL (eg .tar.gz) results
in the package class fetching the modified date for the file via the URL. 
If a directory with that date exists in the cache, the is symlinked into
the deps directory. If not, the URL is fetched, it is unpacked in the 
proper cache subdir, made read-only, and linked into the deps dir.

## Cleaning the cache
Add a command to manage the cache. Operations like:
- Seeing the packages, number of cached entries, and total size in the cache
- Removing entries by date -- eg older than a week