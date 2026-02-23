# IVPM GHA Cache Backend Plan

## Problem
IVPM supports a local filesystem cache for packages. We want it to also transparently exploit the GitHub Actions cache service when running inside GHA, without requiring workflow authors to add `actions/cache` steps. The architecture should be pluggable so other backends can be added.

## Approach
Pluggable `CacheBackend` ABC with:
- `FilesystemCacheBackend` — refactor of existing `Cache` class
- `GHACacheBackend` — two-level cache (local L1 + GHA REST API L2), per-package granularity
- `BackendRegistry` — auto-detection priority: gha → filesystem → none
- `--cache-backend {auto,filesystem,gha,none}` CLI flag on `update`

Per-package granularity (not per-collection): packages are 10–100 MB and combinations change rapidly. Each `(name, version)` is a separate GHA cache entry.

## Status

### Completed ✅
- `CacheBackend` ABC (`cache_backend/base.py`)
- `FilesystemCacheBackend` refactor (`cache_backend/filesystem.py`)
- `BackendRegistry` auto-detect (`cache_backend/registry.py`)
- `cache.py` compat shim
- Wire backend into update flow (`project_ops.py`)
- `GHACacheClient` pure-Python REST client (`cache_backend/gha_client.py`)
- `GHACacheBackend` two-level cache (`cache_backend/gha.py`)
- pip/uv cache dir wiring (`handlers/package_handler_python.py`)
- `--cache-backend` CLI flag (`__main__.py`)
- Layer 1 unit tests — 24 tests (`test/unit/test_cache_backend.py`)
- Layer 2 in-process HTTP server tests — 9 tests (`test/unit/test_gha_client.py`)
- **p4-venv** — venv restore/save hooks wired in `project_ops.py`; `try_restore_venv` added to ABC; cache backend now selected before venv setup
- **p4-yaml** — `CacheConfig` dataclass in `proj_info.py`; `cache:` block parsed by `IvpmYamlReader`; threaded through `BackendRegistry.select(config=...)`; backends accept `config` kwarg
- **p4-cmd-cache** — `cmd_cache.py` dispatches through `BackendRegistry`; `--backend` flag on `cache info/clean`
- **All 114 tests pass (3 skipped)**

### Remaining 🔲

#### p6-l3-tests — Layer 3 integration tests (Docker GHA server) ✅
Opt-in tests using `falcondev-oss/github-actions-cache-server:8` Docker image. Skipped unless `IVPM_TEST_GHA_SERVER` env var is set. 8 tests in `test/unit/test_gha_integration.py` covering `GHACacheClient` roundtrip and `GHACacheBackend` store/restore/eviction.

#### p6-l4-ci — Layer 4 GHA CI job ✅
Added `gha-cache-integration` job to `.github/workflows/ci.yml`. Runs the falcondev v8 cache server as a service container, executes all L3 integration tests, then runs `ivpm update --cache-backend gha` twice (first run populates, second verifies `Restored` messages appear).

## All tasks complete ✅

## Key Technical Details

### GHA Cache Protocol
- Service URL: `ACTIONS_CACHE_URL`, token: `ACTIONS_RUNTIME_TOKEN`
- `GET /_apis/artifactcache/cache?keys=K&version=V` → 200 `{archiveLocation}` or 204 (miss)
- `POST /_apis/artifactcache/caches` → `{cacheId}` or 409 if key exists
- `PATCH /_apis/artifactcache/caches/{id}` with `Content-Range` → 204
- `POST /_apis/artifactcache/caches/{id}` (commit) → 204 empty body
- `version` = `sha256(key)`, max chunk size 32 MB

### Two-Level Cache
- L1: local filesystem (`IVPM_CACHE` → `~/.cache/ivpm/`)
- L2: GHA service (async uploads via `ThreadPoolExecutor`, 4 workers)
- `has_version`: L1 first; L2 lookup + download to L1 on miss
- `store_version`: sync L1 + async L2 upload
- `deactivate()`: joins all pending uploads; skips on `success=False`

### Auto-detection
`BackendRegistry.select()` priority: CLI arg → `IVPM_CACHE_BACKEND` env → auto-detect (gha if both `ACTIONS_CACHE_URL` and `ACTIONS_RUNTIME_TOKEN` set, else filesystem if `IVPM_CACHE` set, else None)

### Key Files
- `src/ivpm/cache_backend/base.py` — ABC
- `src/ivpm/cache_backend/filesystem.py` — FilesystemCacheBackend
- `src/ivpm/cache_backend/gha_client.py` — REST client
- `src/ivpm/cache_backend/gha.py` — GHACacheBackend
- `src/ivpm/cache_backend/registry.py` — BackendRegistry
- `src/ivpm/project_ops.py` — update flow wiring
- `src/ivpm/handlers/package_handler_python.py` — pip/uv cache dir
- `test/unit/test_cache_backend.py` — L1 tests
- `test/unit/test_gha_client.py` — L2 tests
