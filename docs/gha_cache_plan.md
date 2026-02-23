# IVPM Cache Backend Architecture – Findings & Plan

## Problem Statement

IVPM supports a local/shared filesystem cache (`IVPM_CACHE`) for git packages and manages
Python virtual environments via pip/uv.  When IVPM runs inside a GitHub Actions (GHA) workflow
the runner starts clean every time, so nothing is cached between runs unless the workflow author
adds explicit `actions/cache` steps.  The goal is to make IVPM transparently exploit the GHA
cache service **from inside its own Python code**, without requiring the user to add any extra
workflow steps.

Rather than bolting GHA support onto the existing `Cache` class, the right design is a
**pluggable cache backend** system: a common abstract interface, multiple backend
implementations, auto-detection, and explicit configuration.

---

## Findings

### 1. Current Cache Architecture

The existing `Cache` class (`src/ivpm/cache.py`) is a single monolithic implementation tied to
the local filesystem.  Its per-package interface:

| Method | Purpose |
|---|---|
| `has_version(pkg, ver)` | Check whether a version is in the cache |
| `store_version(pkg, ver, src)` | Move a cloned directory into the cache; make read-only |
| `link_to_deps(pkg, ver, deps_dir)` | Symlink cache entry into the project's `deps/` dir |
| `clean_older_than(days)` | Prune stale entries |
| `get_cache_info()` | Return size/version statistics |

`Cache` is instantiated inline inside individual package type handlers
(`package_git.py`, `package_http.py`, `package_gh_rls.py`) — with no injection point — and
`ProjectUpdateInfo` holds an optional `cache: Optional[Cache]` field that is currently unused
for injection.

### 2. GitHub Actions Cache Service

GHA runners expose a **Twirp/HTTP cache service** through two environment variables:

| Variable | Purpose |
|---|---|
| `ACTIONS_CACHE_URL` | Base URL of the cache service |
| `ACTIONS_RUNTIME_TOKEN` | Short-lived bearer token scoped to the current run |

Their presence is both the detection signal and the credential.  `GITHUB_ACTIONS=true` confirms
we are in GHA but the cache service could still be unavailable (e.g. disabled by policy), so
checking both vars is the right test.

The REST protocol (no official Python SDK; re-implemented from `@actions/cache`):
- `GET  …/cache?keys=KEY&version=VER` – look up; returns download URL on hit
- `POST …/caches` – reserve a new slot; returns `cacheId`
- `PATCH …/caches/{cacheId}` – upload content in 32 MB chunks
- `POST  …/caches/{cacheId}` – commit the entry

**GHA cache operates on whole directory trees** (archive + upload/download), not on individual
files.  However, each archive can be small if we cache **one package version per GHA cache
entry** rather than bundling everything together.

### 3. Granularity – Per-Package vs. Per-Collection

A naive first approach would be to snapshot the entire IVPM cache directory as a single GHA
archive keyed on `sha256(package-lock.json)`.  This is problematic:

| Problem | Impact |
|---|---|
| Packages are 10–100 MB each; 10+ packages = 1 GB archive | Long upload/download times; GHA 10 GB repo cap consumed quickly |
| Any package change rotates the key for every other package | Near-zero cache utilisation in fast-moving projects |
| All packages must be available before the update can start | Defeats the parallel async fetch in `PackageUpdater` |

**The right granularity is one GHA cache entry per `(package_name, version)`**, mirroring
exactly the existing `has_version` / `store_version` / `link_to_deps` per-package interface.
Each entry is one small tar.gz (~10–100 MB).  Adding or changing a single package does not
invalidate any other package's cache entry.  GHA's 7-day TTL resets per-entry when each package
is actually used, so actively-used packages stay warm.

### 4. pip / uv Cache Directories

Both tools support redirecting their download caches:
- pip: `--cache-dir` flag or `PIP_CACHE_DIR` env var
- uv:  `--cache-dir` flag or `UV_CACHE_DIR` env var

The Python venv and the pip/uv wheel cache are different in character from git/http packages —
they are not versioned by a commit hash, they are installed as a unit, and restoring a partial
venv is not useful.  These are handled as **two separate session-level GHA entries** (restore
before install, save after install), not as per-package entries.

### 5. Cache Key Strategy

| Cache slot | GHA key | Fallback restore prefixes |
|---|---|---|
| Per git/http package | `ivpm-pkg-{OS}-{pkg_name}-{version}` | *(none — exact version or miss)* |
| Python venv | `ivpm-pyenv-{OS}-{pyver}-{sha256(python_pkgs_*.txt)}` | `ivpm-pyenv-{OS}-{pyver}-` |
| pip/uv wheel download cache | `ivpm-pip-{OS}-{sha256(python_pkgs_*.txt)}` | `ivpm-pip-{OS}-` |

`{OS}` = `RUNNER_OS` env var.  `{version}` for git packages = full commit hash; for HTTP/release
packages = content hash or version string.

Per-package entries use **exact-match lookup only** (no prefix fallback): a different commit is
a different package and must be fetched fresh.

---

## Proposed Architecture

### Core Abstraction – `CacheBackend`

```
src/ivpm/cache_backend/
    __init__.py
    base.py          # CacheBackend ABC + CacheResult dataclass
    filesystem.py    # FilesystemCacheBackend  (refactored from cache.py)
    gha.py           # GHACacheBackend
    registry.py      # BackendRegistry: auto-detect + explicit selection
```

#### `CacheBackend` ABC (`base.py`)

```python
class CacheBackend(ABC):
    # -- Per-package interface (called during update, once per package) --
    @abstractmethod
    def has_version(self, pkg: str, ver: str) -> bool: ...

    @abstractmethod
    def store_version(self, pkg: str, ver: str, src: str) -> str: ...

    @abstractmethod
    def link_to_deps(self, pkg: str, ver: str, deps_dir: str) -> str: ...

    # -- Session lifecycle (called once per ivpm update run) --
    def activate(self) -> None:
        """Called before the update begins. May restore from remote storage."""

    def deactivate(self, success: bool) -> None:
        """Called after the update. May persist to remote storage if success=True."""

    # -- Discovery --
    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Return True if this backend can be used in the current environment."""

    # -- Maintenance (optional) --
    def clean_older_than(self, days: int) -> int:
        return 0

    def get_info(self) -> dict:
        return {}
```

#### `FilesystemCacheBackend` (`filesystem.py`)

Direct refactor of the existing `Cache` class to implement `CacheBackend`.  No behavioural
changes; `is_available()` returns `True` when `IVPM_CACHE` is set or a path is provided.

#### `GHACacheBackend` (`gha.py`)

The backend has two distinct modes of operation, matching the two granularity levels:

**Per-package operations** (called once per package, potentially in parallel):
```
has_version(pkg, ver)
  ├─ Check local_dir/{pkg}/{ver}/ exists  → True (local hit, no GHA traffic)
  └─ GHA lookup: GET cache?keys=ivpm-pkg-{OS}-{pkg}-{ver}
       ├─ Hit  → download tar.gz, extract to local_dir/{pkg}/{ver}/, make read-only → True
       └─ Miss → False

store_version(pkg, ver, src)
  ├─ Move src → local_dir/{pkg}/{ver}/  (make read-only, same as filesystem backend)
  └─ Schedule GHA upload: tar.gz of local_dir/{pkg}/{ver}/
       POST reserve → PATCH chunks → POST commit
       (upload runs in a background thread; errors are warnings, not failures)

link_to_deps(pkg, ver, deps_dir)
  └─ Symlink local_dir/{pkg}/{ver}/ → deps_dir/{pkg}  (identical to FilesystemCacheBackend)
```

**Session-level operations** (venv + pip/uv wheel cache):
```
activate()
  ├─ Ensure local_dir exists
  ├─ GHA lookup: ivpm-pip-{OS}-{hash}  → download wheel cache if hit
  └─ GHA lookup: ivpm-pyenv-{OS}-{pyver}-{hash}  → extract venv if hit

deactivate(success=True)
  ├─ If venv was rebuilt: upload venv as ivpm-pyenv-{OS}-{pyver}-{hash}
  ├─ If pip cache changed: upload wheel cache as ivpm-pip-{OS}-{hash}
  └─ Wait for any background per-package uploads to complete
```

`local_dir` is resolved in priority order:
1. `IVPM_CACHE` env var (existing filesystem cache — GHA acts as a remote peer)
2. `cache.staging-dir` in `ivpm.yaml`
3. Default: `~/.cache/ivpm/` (persists across runs on the same machine)

This means the backend works as a **two-level cache**: the local filesystem is L1 (fast, no
network), and GHA is L2 (cross-run persistence between fresh runners).  On a developer's
machine with `IVPM_CACHE` set, GHA uploads happen but local hits are served from the filesystem;
the GHA layer only matters for CI runners that start clean.

`is_available()` returns `True` when `ACTIONS_CACHE_URL` and `ACTIONS_RUNTIME_TOKEN` are set.

#### `BackendRegistry` (`registry.py`)

```python
class BackendRegistry:
    _backends: list[type[CacheBackend]] = [
        GHACacheBackend,       # Highest priority when in GHA
        FilesystemCacheBackend,
    ]

    @classmethod
    def select(cls, explicit: Optional[str] = None) -> Optional[CacheBackend]:
        if explicit == "none":
            return None
        if explicit is not None:
            return cls._by_name(explicit)
        # auto-detect: first available
        for backend_cls in cls._backends:
            if backend_cls.is_available():
                return backend_cls()
        return None
```

`explicit` comes from (in priority order):
1. CLI flag `--cache-backend <name>`
2. `ivpm.yaml` `cache.backend:` field
3. `IVPM_CACHE_BACKEND` env var

---

## Integration Points in Existing Code

### `ProjectUpdateInfo` (`project_ops_info.py`)

Change `cache: Optional[Cache]` → `cache: Optional[CacheBackend]`.  The `activate()` /
`deactivate()` calls belong in the update orchestration, not in individual package handlers.

### `PackageUpdater` (`package_updater.py`)

```python
# Before starting package fetches:
if self.update_info.cache:
    self.update_info.cache.activate()

# After all packages complete:
if self.update_info.cache:
    self.update_info.cache.deactivate(success=not any_errors)
```

### Package handlers (`package_git.py`, `package_http.py`, `package_gh_rls.py`)

Replace the inline `cache = Cache()` instantiation with injection from `update_info.cache`.  If
`update_info.cache is None`, fall back to the current no-cache path.

### `package_handler_python.py`

The venv is a session-level concern, not a per-package one:
- `activate()` already restores the venv if available (before Python handler runs)
- The Python handler checks whether the venv is already complete (existing behaviour)
- If the venv was freshly built, mark it dirty on the backend so `deactivate()` uploads it
- Set `UV_CACHE_DIR` / `PIP_CACHE_DIR` to `backend.pip_cache_dir` before invoking pip/uv

### `cmd_update.py`

Construct the backend via `BackendRegistry.select(explicit=args.cache_backend)` and attach it
to `update_info.cache` before creating the `PackageUpdater`.

### `cmd_cache.py`

Extend to support `ivpm cache info --backend gha` etc.  Info/clean operations dispatch through
the backend interface.

---

## Configuration

### `ivpm.yaml`

```yaml
cache:
  backend: auto          # auto | filesystem | gha | none
  local-dir: ~/.cache/ivpm   # L1 local cache dir (default; overridden by IVPM_CACHE)
  key-prefix: myproject      # Prefix for GHA cache keys (default: ivpm)
  include-python-venv: true  # Save/restore packages/python/ (default: true)
  include-pip-cache: true    # Save/restore pip/uv wheel cache (default: true)
  max-age-days: 30           # Prune local entries older than this on deactivate
```

### CLI (`ivpm update`)

```
--cache-backend {auto,filesystem,gha,none}
```

### Environment Variables

| Variable | Purpose |
|---|---|
| `IVPM_CACHE` | Filesystem backend cache directory (existing) |
| `IVPM_CACHE_BACKEND` | Backend override (`auto`/`filesystem`/`gha`/`none`) |
| `ACTIONS_CACHE_URL` | GHA service URL (set by runner) |
| `ACTIONS_RUNTIME_TOKEN` | GHA service token (set by runner) |

---

## Implementation Phases

### Phase 1 – Refactor to backend abstraction (no behaviour change)
- Create `src/ivpm/cache_backend/` package
- `base.py`: `CacheBackend` ABC, `CacheResult`
- `filesystem.py`: `FilesystemCacheBackend` (move+refactor `Cache`)
- `registry.py`: `BackendRegistry` (auto-detect only; GHA slot returns `None` until Phase 2)
- Update imports in `cache.py` (re-export for backward compat), `package_git.py`,
  `package_http.py`, `package_gh_rls.py`, `project_ops_info.py`, `cmd_cache.py`
- Wire `activate()` / `deactivate()` into `PackageUpdater`
- All existing tests must still pass

### Phase 2 – GHA cache client
- `gha_client.py`: pure-Python REST client for GHA cache service  
  Constructor: `GHACacheClient(cache_url, token, key_prefix, os_name)`  
  - `lookup(key) -> Optional[str]` (download URL or None)
  - `download(url, dest_dir)` (stream tar.gz, extract, make read-only)
  - `upload(key, src_dir)` (tar.gz → reserve → chunked PATCH → commit)
  - `upload_async(key, src_dir)` (non-blocking; returns `Future`)
- Unit tests with mocked `urllib` responses (Layer 1)
- Protocol tests against in-process mock server (Layer 2)

### Phase 3 – `GHACacheBackend`
- `gha.py`: implement `CacheBackend` with per-package GHA entries
  - `has_version`: L1 filesystem check, then GHA lookup + download on miss
  - `store_version`: filesystem store + async GHA upload
  - `link_to_deps`: symlink from local_dir (unchanged from filesystem backend)
  - `activate()`: restore venv + pip cache from GHA (session-level)
  - `deactivate(success)`: upload venv + pip cache if dirty; wait for package uploads
- Wire `UV_CACHE_DIR`/`PIP_CACHE_DIR` in `package_handler_python.py`
- Integration tests (mock GHA env vars + in-process server)

### Phase 4 – Configuration & CLI
- Add `cache:` section to `ivpm.yaml` schema (`proj_info.py`)
- Add `--cache-backend` flag to `ivpm update`
- Add `IVPM_CACHE_BACKEND` env var support in `BackendRegistry`
- Update `cmd_cache.py` for multi-backend info/clean

### Phase 5 – Tests & Documentation
- `test/unit/test_cache_backend.py` – backend ABC contract tests
- `test/unit/test_gha_cache.py` – GHA-specific tests
- Update `docs/` and README

---

## Open Questions

1. **GHA cache protocol v2**: GHA announced breaking changes (March 2025).  The client must
   probe service version or handle both the legacy Twirp path and the new REST path.

2. **Permissions**: 403 on save should be logged as a warning and not fail the build.

3. **Cache size limits**: GHA imposes a ~10 GB per-repo soft cap and 7-day TTL.  The
   `max-age-days` config feeds `clean_older_than()` before each save.

4. **Concurrent matrix jobs uploading the same package**: Multiple jobs may race to upload
   `ivpm-pkg-{OS}-{pkg}-{ver}` for the same well-known tag (e.g. `verilator@v5.020`).  GHA
   handles this gracefully (first writer wins).  IVPM should treat "already exists" as success.

5. **Async upload lifecycle**: Per-package uploads fire during the parallel fetch phase in
   `PackageUpdater`.  `deactivate()` must join all outstanding upload futures before returning,
   so the run does not exit while uploads are still in flight.

6. **Self-hosted runners without cache service**: Handled by the `is_available()` check.

---

## Testing Strategy

Testing the GHA cache backend outside of GitHub Actions requires a layered approach.  Each layer
tests a different slice of the stack and has different setup costs.

### The Problem

The GHA cache service is only reachable from inside an actual GHA runner (or a compatible
emulator).  `ACTIONS_CACHE_URL` and `ACTIONS_RUNTIME_TOKEN` are injected by the runner and are
not available locally.  `act` (run-GHA-locally) stubs cache out; it does not emulate the service.

### Layer 1 – Pure Unit Tests (no HTTP)

**What**: Test everything above the HTTP layer in isolation.  
**Tools**: stdlib `unittest` + `unittest.mock`.  
**Coverage**:

- `BackendRegistry.select()` logic (explicit / env var / auto-detect)
- `GHACacheBackend.is_available()` — mock env vars
- Cache key computation (`gha_cache_key.py`) — feed known lock-file contents, assert SHA-256 output
- `FilesystemCacheBackend` — already testable on the filesystem, no mocking needed
- `GHACacheBackend` with a mocked `gha_client` — test that `activate()` calls `client.restore()`,
  `deactivate(success=True)` calls `client.save()`, `deactivate(success=False)` does not save

```python
class TestGHACacheBackendLogic(TestBase):
    def test_activate_calls_restore(self):
        with patch("ivpm.cache_backend.gha.GHACacheClient") as MockClient:
            MockClient.return_value.restore.return_value = True
            backend = GHACacheBackend(staging_dir=self.testdir)
            backend.activate()
            MockClient.return_value.restore.assert_called_once()

    def test_deactivate_saves_on_success(self):
        ...

    def test_deactivate_skips_save_on_failure(self):
        ...
```

**Pros**: Fast (milliseconds), no external deps, runs everywhere.  
**Cons**: Does not exercise the actual HTTP client code path.

---

### Layer 2 – In-Process Mock HTTP Server

**What**: Spin up a minimal Python HTTP server in a background thread that implements the GHA
cache API subset used by `gha_client.py`.  Point `ACTIONS_CACHE_URL` at it.  
**Tools**: stdlib `http.server`, `threading`.  No Docker, no external process.  
**Coverage**:

- `GHACacheClient.lookup()` — server returns hit/miss
- `GHACacheClient.download()` — server streams a known tar.gz
- `GHACacheClient.reserve()` / `upload_chunk()` / `commit()` — server accepts upload, verifies chunks

```python
class FakeGHACacheHandler(BaseHTTPRequestHandler):
    """Minimal GHA cache protocol handler for tests."""
    cache_store: dict = {}   # shared across requests in a test

    def do_GET(self):
        # GET /_apis/artifactcache/cache?keys=K&version=V
        ...
    def do_POST(self):
        # POST /_apis/artifactcache/caches  -> reserve
        # POST /_apis/artifactcache/caches/{id} -> commit
        ...
    def do_PATCH(self):
        # PATCH /_apis/artifactcache/caches/{id} -> upload chunk
        ...

class TestGHACacheClient(TestBase):
    server: HTTPServer
    server_thread: threading.Thread

    @classmethod
    def setUpClass(cls):
        FakeGHACacheHandler.cache_store = {}
        cls.server = HTTPServer(("localhost", 0), FakeGHACacheHandler)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever)
        cls.server_thread.daemon = True
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        super().setUp()
        os.environ["ACTIONS_CACHE_URL"] = f"http://localhost:{self.port}/"
        os.environ["ACTIONS_RUNTIME_TOKEN"] = "test-token"

    def test_restore_cache_hit(self):
        # Pre-populate cache store with a known tar.gz
        ...

    def test_restore_cache_miss(self):
        ...

    def test_save_cache_roundtrip(self):
        # Save a directory, then restore it and compare contents
        ...
```

Using `HTTPServer(("localhost", 0), ...)` lets the OS pick a free port, avoiding conflicts.

**Pros**: Tests the real HTTP client code; stdlib only; fast (<1 s per test); no Docker.  
**Cons**: Must implement the cache protocol subset correctly in the mock handler — this is also
a useful spec document for `gha_client.py`.

---

### Layer 3 – falcondev-oss/github-actions-cache-server (Optional Integration)

**What**: A production-compatible, open-source GHA cache server image.  
**Repo**: https://github.com/falcondev-oss/github-actions-cache-server  
**Protocol**: Full REST implementation of the GHA cache API (v2), filesystem or S3 backend.

**Usage pattern** — as a Docker Compose service in CI:

```yaml
# docker-compose.test.yml
services:
  cache-server:
    image: ghcr.io/falcondev-oss/github-actions-cache-server:latest
    ports:
      - "3000:3000"
    environment:
      API_BASE_URL: http://localhost:3000
      STORAGE_DRIVER: filesystem
      STORAGE_FILESYSTEM_PATH: /data
    volumes:
      - /tmp/cache-data:/data
```

```python
# In test runner or pytest conftest:
# export ACTIONS_CACHE_URL=http://localhost:3000/
# export ACTIONS_RUNTIME_TOKEN=any-token
```

This layer would be an opt-in CI job (e.g. `test-integration`), skipped unless the
`IVPM_TEST_GHA_SERVER` env var or `--integration` flag is set.

**Pros**: Full protocol compatibility; catches subtle API mismatches.  
**Cons**: Requires Docker; heavier setup; adds CI time.  Overkill for day-to-day development.

---

### Layer 4 – Real GitHub Actions (End-to-End)

The existing CI workflow (`.github/workflows/ci.yml`) runs inside real GHA.  Adding a job that
runs `ivpm update` with `--cache-backend gha` will exercise the full stack against the real
service.  A subsequent job can verify that a second run produces a cache hit.

```yaml
jobs:
  test-gha-cache:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: First run (should populate cache)
        run: ivpm update --cache-backend gha
      - name: Second run (should hit cache)
        run: ivpm update --cache-backend gha
        # Assert cache-hit in output or exit code
```

**Pros**: Tests the actual GHA service; catches protocol version issues.  
**Cons**: Only runs in GHA; slow feedback; can't be run locally.

---

### Recommended Test Structure

```
test/unit/
    test_cache_backend_abc.py     # Layer 1 – contract tests for any backend
    test_filesystem_backend.py    # Layer 1 – FilesystemCacheBackend
    test_gha_backend_logic.py     # Layer 1 – GHACacheBackend with mocked client
    test_gha_cache_key.py         # Layer 1 – key computation
    test_gha_client.py            # Layer 2 – GHACacheClient vs in-process server
    test_gha_integration.py       # Layer 3 – skipped unless IVPM_TEST_GHA_SERVER set
```

### Design Principle: Testability First

The architecture should be shaped so that every layer is independently testable:

1. `gha_client.py` takes `cache_url` and `token` as constructor parameters (not read from env
   directly) — so tests can pass `http://localhost:{port}/` without monkeypatching `os.environ`.
2. `GHACacheBackend` takes a `client` parameter (or factory) — so unit tests can inject a mock.
3. The in-process mock handler serves as the living specification of `gha_client.py`'s expected
   wire protocol; if the handler and the client disagree, a test fails.

---

## Files to Create / Modify

| File | Action |
|---|---|
| `src/ivpm/cache_backend/__init__.py` | **Create** |
| `src/ivpm/cache_backend/base.py` | **Create** – `CacheBackend` ABC |
| `src/ivpm/cache_backend/filesystem.py` | **Create** – refactored from `cache.py` |
| `src/ivpm/cache_backend/gha_client.py` | **Create** – GHA REST client |
| `src/ivpm/cache_backend/gha.py` | **Create** – `GHACacheBackend` |
| `src/ivpm/cache_backend/registry.py` | **Create** – `BackendRegistry` |
| `src/ivpm/cache.py` | **Modify** – re-export `FilesystemCacheBackend` as `Cache` for compat |
| `src/ivpm/project_ops_info.py` | **Modify** – `cache` field type → `CacheBackend` |
| `src/ivpm/package_updater.py` | **Modify** – call `activate`/`deactivate` |
| `src/ivpm/pkg_types/package_git.py` | **Modify** – use injected backend |
| `src/ivpm/pkg_types/package_http.py` | **Modify** – use injected backend |
| `src/ivpm/pkg_types/package_gh_rls.py` | **Modify** – use injected backend |
| `src/ivpm/cmds/cmd_update.py` | **Modify** – construct backend via registry |
| `src/ivpm/cmds/cmd_cache.py` | **Modify** – multi-backend dispatch |
| `src/ivpm/handlers/package_handler_python.py` | **Modify** – pip/uv cache dir from backend |
| `src/ivpm/proj_info.py` | **Modify** – `cache:` yaml section |
| `test/unit/test_cache_backend.py` | **Create** |
| `test/unit/test_gha_cache.py` | **Create** |
