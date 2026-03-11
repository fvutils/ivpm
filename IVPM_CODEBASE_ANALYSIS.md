# IVPM Codebase Comprehensive Analysis

## 1. Overall Project Structure

### Root Directory Layout
```
/home/mballance/projects/fvutils/ivpm-show/
├── src/ivpm/                    # Main package source (~5544 LOC Python)
├── test/                        # Unit tests and test data
├── packages/                    # Managed dependencies (venv, etc.)
├── docs/                        # Documentation
├── pyproject.toml               # Project metadata and entry points
├── ivpm.yaml                    # IVPM's own package configuration
├── Agents.md                    # Development guide for AI agents
├── sync-design.md               # Design doc for sync command
└── README.md
```

### src/ivpm Directory Structure
```
src/ivpm/
├── __main__.py                  # Entry point, CLI parser setup
├── package.py                   # Base Package data model
├── packages_info.py             # PackagesInfo container class
├── package_updater.py           # Async package fetching orchestrator
├── project_ops.py               # High-level operations (update, build, sync, status)
├── project_ops_info.py          # Data classes for operation contexts
├── package_lock.py              # Lock file I/O (package-lock.json)
├── proj_info.py                 # Project info loader
├── msg.py                       # Message/logging utilities
├── utils.py                     # Utility functions
│
├── cmds/                        # CLI command implementations
│   ├── cmd_activate.py          # Activate shell environment
│   ├── cmd_build.py             # Build packages
│   ├── cmd_cache.py             # Manage cache (init, info, clean)
│   ├── cmd_clone.py             # Clone project from git/URL
│   ├── cmd_init.py              # Initialize ivpm.yaml
│   ├── cmd_update.py            # Fetch dependencies
│   ├── cmd_sync.py              # Sync with upstream (git pull)
│   ├── cmd_status.py            # Show package status
│   ├── cmd_git_status.py        # Deprecated: git status
│   ├── cmd_git_update.py        # Deprecated: git update
│   ├── cmd_pkg_info.py          # Query package info (flags, paths, libs)
│   ├── cmd_snapshot.py          # Create snapshot of packages
│   ├── cmd_share.py             # Return share directory
│   └── __init__.py
│
├── pkg_types/                   # Package type implementations (sources)
│   ├── pkg_type_rgy.py          # Package type registry (factory pattern)
│   ├── package.py               # (also referenced from parent)
│   ├── package_url.py           # Base class for URL-based packages
│   ├── package_git.py           # Git repository packages
│   ├── package_pypi.py          # PyPI Python packages
│   ├── package_dir.py           # Local directory packages
│   ├── package_file.py          # Local file packages (compressed)
│   ├── package_http.py          # HTTP download packages
│   ├── package_gh_rls.py        # GitHub releases
│   └── __init__.py
│
├── handlers/                    # Package handlers (post-load operations)
│   ├── package_handler_rgy.py   # Handler registry & loader
│   ├── package_handler.py       # Base PackageHandler class
│   ├── package_handler_list.py  # Composite handler (dispatcher)
│   ├── package_handler_python.py # Python venv & pip handler
│   ├── package_handler_direnv.py # direnv integration
│   ├── package_handler_skills.py # AI skills documentation
│   ├── handler_conditions.py    # Condition predicates for handler filtering
│   └── __init__.py
│
├── pkg_info/                    # Package info registry (compile flags, include dirs, etc.)
│   ├── pkg_info_rgy.py          # Registry for package metadata
│   ├── pkg_info_loader.py       # Load package.json metadata
│   ├── pkg_info.py              # PkgInfo data class
│   ├── pkg_compile_flags.py     # Compute compiler flags
│   └── __init__.py
│
├── pkg_content_type.py          # Type field parser (type: python, etc.)
├── pkg_status.py                # Status data model
├── pkg_sync.py                  # Sync data model
├── status_tui.py                # Status command TUI
├── sync_tui.py                  # Sync command TUI (if exists)
├── update_tui.py                # Update command TUI
├── update_event.py              # Event types for update/build progress
├── arg_utils.py                 # CLI argument parsing helpers
├── cache.py                     # Package cache management
├── project_sync.py              # Alternative sync path (setup venv)
├── ivpm_yaml_reader.py          # Parse ivpm.yaml
├── ivpm_yaml_writer.py          # Write ivpm.yaml
├── setup/                       # setuptools integration
├── templates/                   # Project templates (init command)
├── pywrap/                      # Python wrapper utilities
└── share/                       # CMake files, documentation
```

---

## 2. CLI Framework & Command Structure

### Framework: **argparse** (Python standard library)

**Entry Point:** `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/__main__.py`

### Key Functions

- **`get_parser(parser_ext, options_ext)`** — Creates the main ArgumentParser with all subcommands
  - Builds subparsers for each command
  - Accepts extension points for custom subcommands (`parser_ext`) and custom options (`options_ext`)
  - Returns configured parser

- **`main(project_dir=None)`** — Entry point invoked by `ivpm` console script (pyproject.toml)
  - Loads entry points from `ivpm.ext` group (custom extensions)
  - Loads package type registry (`PkgTypeRgy`)
  - Loads handler registry (`PackageHandlerRgy`)
  - Calls `get_parser()`, parses args, invokes command handler

### Current Subcommands Registered

| Command | Handler Class | File | Purpose |
|---------|---------------|------|---------|
| `activate` | `CmdActivate` | `cmd_activate.py` | Activate shell with venv |
| `build` | `CmdBuild` | `cmd_build.py` | Build sub-projects |
| `cache` | `CmdCache` | `cmd_cache.py` | Manage cache (init, info, clean) |
| `init` | `CmdInit` | `cmd_init.py` | Initialize ivpm.yaml |
| `update` | `CmdUpdate` | `cmd_update.py` | Fetch dependencies |
| `sync` | `CmdSync` | `cmd_sync.py` | Sync with upstream |
| `status` | `CmdStatus` | `cmd_status.py` | Show package status |
| `clone` | `CmdClone` | `cmd_clone.py` | Clone project |
| `pkg-info` | `CmdPkgInfo` | `cmd_pkg_info.py` | Query package metadata |
| `share` | `CmdShare` | `cmd_share.py` | Show share dir |
| `snapshot` | `CmdSnapshot` | `cmd_snapshot.py` | Create snapshot |
| `git-status` | `CmdGitStatus` | `cmd_git_status.py` | Deprecated |
| `git-update` | `CmdGitUpdate` | `cmd_git_update.py` | Deprecated |

### Command Handler Pattern

Each command is a callable class with `__call__(self, args)`:

```python
# Example: CmdStatus from cmd_status.py (lines 1-19)
class CmdStatus(object):
    def __init__(self):
        pass

    def __call__(self, args):
        if args.project_dir is None:
            args.project_dir = os.getcwd()
        results = ProjectOps(args.project_dir).status(args=args)
        verbose = getattr(args, "verbose", 0)
        tui = create_status_tui(args)
        tui.render(results, verbose=verbose)
```

### Extension Points

1. **Custom subcommands** via entry point `ivpm.ext`:
   - Module must have `ivpm_subcommand(subparser)` function
   - Called during parser setup

2. **Custom options** via `ivpm_options`:
   - Module passed to `add_handler_options()` for option registration

3. **Custom package types** via `ivpm_pkgtype`:
   - Function `ivpm_pkgtype(pkg_types)` appends `(name, factory, description)` tuples

---

## 3. Package Sources (Package Types)

### Architecture: Registry Pattern + Factory Pattern

**Registry Class:** `PkgTypeRgy` at `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/pkg_types/pkg_type_rgy.py`

### Built-in Package Types

| Source Type | Factory | Class | Description |
|-------------|---------|-------|-------------|
| `"dir"` | `PackageDir.create` | `PackageDir` | Local directory (symlink/copy) |
| `"file"` | `PackageFile.create` | `PackageFile` | Local compressed file (tgz, zip, etc.) |
| `"http"` | `PackageHttp.create` | `PackageHttp` | HTTP/HTTPS download + extract |
| `"git"` | `PackageGit.create` | `PackageGit` | Git repository clone |
| `"pypi"` | `PackagePyPi.create` | `PackagePyPi` | Python package from PyPI |
| `"url"` | `PackageURL.create` | `PackageURL` | Generic URL (parent class) |
| `"gh-rls"` | `PackageGhRls.create` | `PackageGhRls` | GitHub release download |

### Registry Methods

```python
class PkgTypeRgy:
    def register(self, src: str, factory: Callable, description: str = ""):
        """Register a new package type"""
        self.src2fact_m[src] = (factory, description)
    
    def mkPackage(self, src: str, name: str, opts: dict, si) -> Package:
        """Create package instance using registered factory"""
        return self.src2fact_m[src][0](name, opts, si)
    
    def hasPkgType(self, src: str) -> bool:
        """Check if type is registered"""
        return src in self.src2fact_m.keys()
    
    def getSrcTypes(self):
        """List all registered source types"""
        return self.src2fact_m.keys()
    
    @classmethod
    def inst(cls):
        """Singleton accessor with auto-initialization"""
```

### Package Type Class Hierarchy

```
Package (base)
├── PackageURL (adds url, cache fields)
│   ├── PackageDir (local dir, link/copy)
│   ├── PackageFile (compressed file)
│   │   └── PackageHttp (HTTP download)
│   │       └── PackageGhRls (GitHub release)
│   └── PackageGit (Git repository)
└── PackagePyPi (PyPI package)
```

### Factory Pattern: `create()` Method

Each package type implements a static factory method:

```python
@staticmethod
def create(name: str, opts: dict, si) -> 'Package':
    pkg = PackageGit(name)  # instantiate
    pkg.process_options(opts, si)  # hydrate fields
    return pkg
```

Example: `PackageGit.create()` at `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/pkg_types/package_git.py` (lines 46-50 in superclass `PackageURL`)

```python
@staticmethod
def create(name, opts, si) -> 'Package':
    pkg = PackageURL(name)
    pkg.process_options(opts, si)
    return pkg
```

### Registration: Singleton Pattern

```python
class PkgTypeRgy:
    _inst = None

    def _load(self):
        self.register("dir", PackageDir.create, "Directory")
        self.register("git", PackageGit.create, "Git")
        self.register("pypi", PackagePyPi.create, "PyPi")
        # ... etc

    @classmethod
    def inst(cls):
        if cls._inst is None:
            cls._inst = PkgTypeRgy()
            cls._inst._load()
        return cls._inst
```

### Usage: PackageUpdater

At `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/package_updater.py` (lines ~50-60):

```python
rgy = PkgTypeRgy.inst()
for src in deps.keys():
    if rgy.hasPkgType(src):
        pkg = rgy.mkPackage(src, name, opts, si)
```

---

## 4. Package Handlers (Post-Load Operations)

### Architecture: Chain-of-Responsibility + Composite Pattern

**Base Class:** `PackageHandler` at `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/handlers/package_handler.py`

**Registry:** `PackageHandlerRgy` at `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/handlers/package_handler_rgy.py`

### Built-in Handlers (Registered via Entry Points)

| Handler | Class | File | Phase | Purpose |
|---------|-------|------|-------|---------|
| `python` | `PackageHandlerPython` | `package_handler_python.py` | 0 | Venv setup, pip install |
| `direnv` | `PackageHandlerDirenv` | `package_handler_direnv.py` | ? | Generate .envrc |
| `skills` | `PackageHandlerSkills` | `package_handler_skills.py` | ? | Parse SKILLS.md |

### Handler Lifecycle

Handlers operate in two phases:

#### 1. **Leaf Phase** (per-package, concurrent)
- Called for each package as it's loaded
- Safe to parallelize (protected by `_lock`)
- Methods:
  - `on_leaf_pre_load(pkg, update_info)` — Before package fetch
  - `on_leaf_post_load(pkg, update_info)` — After package ready on disk

#### 2. **Root Phase** (once, main thread)
- Called after all packages loaded
- Main work happens here (venv, pip install, generate files)
- Methods:
  - `on_root_pre_load(update_info)` — Before any packages fetched; reset state
  - `on_root_post_load(update_info)` — After all packages loaded; main work

### Handler Base Class Definition

```python
@dc.dataclass
class PackageHandler(object):
    # Metadata (override in subclasses)
    name:        ClassVar[Optional[str]] = None
    description: ClassVar[Optional[str]] = None
    phase:       ClassVar[int]  = 0
    
    # Conditions (list of callables or None = always true)
    leaf_when:   ClassVar[Optional[List]] = None  # callable(pkg) -> bool
    root_when:   ClassVar[Optional[List]] = None  # callable(packages_list) -> bool

    # Instance state
    _lock: threading.Lock = dc.field(default_factory=threading.Lock, ...)

    # Lifecycle methods
    def reset(self): pass
    def on_leaf_pre_load(self, pkg, update_info): pass
    def on_leaf_post_load(self, pkg, update_info): pass
    def on_root_pre_load(self, update_info): self.reset()
    def on_root_post_load(self, update_info): pass
    
    # Other hooks
    def build(self, build_info): pass
    def add_options(self, subcommands: dict): pass  # Register CLI options
    def get_lock_entries(self, deps_dir: str) -> dict: return {}
    
    # Task progress context manager
    @contextmanager
    def task_context(self, info, task_id: str, task_name: str):
        # Emits UpdateEvents for progress tracking
        ...
```

### Composite Handler: PackageHandlerList

Located at `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/handlers/package_handler_list.py`:

- Delegates all callbacks to registered handlers
- Sorts handlers by `phase` before root callbacks
- Evaluates `leaf_when` and `root_when` conditions
- Accumulates all packages for root-phase condition evaluation

```python
@dc.dataclass
class PackageHandlerList(PackageHandler):
    handlers: List[PackageHandler] = dc.field(default_factory=list)
    _all_pkgs: list = dc.field(...)  # Accumulated for root_when eval

    def on_leaf_post_load(self, pkg, update_info):
        self._all_pkgs.append(pkg)
        for h in self.handlers:
            if self._leaf_conditions_pass(h, pkg):
                h.on_leaf_post_load(pkg, update_info)

    def on_root_post_load(self, update_info):
        passing = [h for h in self.handlers if self._root_conditions_pass(h)]
        passing.sort(key=lambda h: type(h).phase)
        for h in passing:
            h.on_root_post_load(update_info)
```

### Handler Registration

At `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/handlers/package_handler_rgy.py` (lines 32-61):

```python
class PackageHandlerRgy(object):
    def _load(self):
        # Built-in handlers
        self.addHandler(PackageHandlerPython)
        self.addHandler(PackageHandlerDirenv)
        self.addHandler(PackageHandlerSkills)

        # Discover via entry points (group="ivpm.handlers")
        for ep in entry_points(group="ivpm.handlers"):
            cls = ep.load()
            self.addHandler(cls)

    @classmethod
    def inst(cls) -> 'PackageHandlerRgy':
        if cls._inst is None:
            cls._inst = PackageHandlerRgy()
            cls._inst._load()
        return cls._inst
```

### Example: PackageHandlerPython

At `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/handlers/package_handler_python.py` (lines 41-86):

```python
@dc.dataclass
class PackageHandlerPython(PackageHandler):
    name:      ClassVar[str]            = "python"
    leaf_when: ClassVar[Optional[List]] = None  # Always inspect
    root_when: ClassVar[Optional[List]] = [HasType("python")]  # Only if Python pkgs present
    phase:     ClassVar[int]            = 0

    pkgs_info: Dict[str, Package] = dc.field(default_factory=dict)
    src_pkg_s: Set[str] = dc.field(default_factory=set)
    pypi_pkg_s: Set[str] = dc.field(default_factory=set)

    def on_leaf_post_load(self, pkg: Package, update_info):
        # Detect Python packages (PyPI, explicit type: python, setup.py/setup.cfg/pyproject.toml)
        if pkg.src_type == "pypi":
            self.pypi_pkg_s.add(pkg.name)
        # ... other detection logic
        self.pkgs_info[pkg.name] = pkg

    def on_root_post_load(self, update_info: ProjectUpdateInfo):
        # Setup venv, install packages
        with self.task_context(update_info, "python-venv", "Python Environment"):
            # Create/activate venv
            # Run pip/uv install
            ...
```

---

## 5. Data Model: Package

### Base Package Class

At `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/package.py` (lines 92-150):

```python
@dc.dataclass
class Package(object):
    # Core identity
    name: str
    srcinfo: object = None  # Source info from YAML
    path: str = None  # Disk location after fetch
    
    # Type classification
    pkg_type: PackageType = None  # Raw, Python, Unknown
    src_type: str = None  # "git", "pypi", "http", etc.
    
    # Type metadata (from ivpm.yaml "type:" field)
    type_data: List['TypeData'] = dc.field(default_factory=list)
    self_types: List[Tuple[str, dict]] = dc.field(default_factory=list)
    
    # Dependency control
    process_deps: bool = True  # Process sub-deps?
    setup_deps: Set[str] = dc.field(default_factory=set)  # Python setup deps
    dep_set: str = None  # Override dep-set
    proj_info: 'ProjInfo' = None
    resolved_by: str = None  # Package that resolved this
    
    # Methods
    def build(self, pkgs_info): pass
    def status(self, pkgs_info): pass
    def sync(self, sync_info) -> PkgSyncResult: ...
    def update(self, update_info) -> ProjInfo: ...
    def process_options(self, opts, si): ...
```

### Specific Package Types

#### PackageGit
At `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/pkg_types/package_git.py` (lines 37-45):

```python
@dc.dataclass
class PackageGit(PackageURL):
    branch: str = None
    commit: str = None
    tag: str = None
    depth: str = None
    anonymous: bool = None
    resolved_commit: str = None  # Actual commit after fetch
    
    def update(self, update_info) -> ProjInfo:
        # Handles caching, cloning, commit capture
        ...
    
    def sync(self, sync_info) -> PkgSyncResult:
        # Git fetch + merge (stub/incomplete in current code)
        ...
```

#### PackagePyPi
At `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/pkg_types/package_pypi.py` (lines 25-50):

```python
@dc.dataclass
class PackagePyPi(Package):
    version: str = None
    resolved_version: str = None  # Actual installed version
    extras: list = None  # PEP 508 extras ["litellm"]
    
    def process_options(self, opts, si):
        self.src_type = "pypi"
        if "version" in opts:
            self.version = opts["version"]
        if "extras" in opts:
            self.extras = [str(e) for e in opts["extras"]]
```

#### PackageURL (parent for dir, file, http, gh-rls)
At `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/pkg_types/package_url.py` (lines 26-44):

```python
@dc.dataclass
class PackageURL(Package):
    url: str = None
    cache: Optional[bool] = None  # True/False/None
```

---

## 6. Existing 'show' / 'list' Commands

### Current Status

**No dedicated 'show' or 'list' command exists yet.**

### Related Commands

1. **`status`** (CmdStatus at `cmd_status.py`)
   - Shows git repo status, file diffs, Python packages
   - Uses `ProjectOps.status()` → `status_tui.create_status_tui(args).render(results)`
   - File: `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/cmds/cmd_status.py`

2. **`pkg-info`** (CmdPkgInfo at `cmd_pkg_info.py`)
   - Queries package metadata: `incdirs`, `paths`, `libdirs`, `libs`, `flags`
   - Uses `PkgInfoRgy` registry
   - File: `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/cmds/cmd_pkg_info.py`

3. **`cache info`** (CmdCache subcommand)
   - Shows cache contents, sizes, versions
   - File: `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/cmds/cmd_cache.py` (lines 63-120)

---

## 7. Entry Points (pyproject.toml)

At `/home/mballance/projects/fvutils/ivpm-show/pyproject.toml` (lines 23-32):

```toml
[project.scripts]
ivpm = "ivpm.__main__:main"

[project.entry-points."ivpm.handlers"]
python = "ivpm.handlers.package_handler_python:PackageHandlerPython"
direnv = "ivpm.handlers.package_handler_direnv:PackageHandlerDirenv"
skills = "ivpm.handlers.package_handler_skills:PackageHandlerSkills"
```

**Extension points for custom code:**
- `ivpm.ext` — Custom subcommands/options/types (loaded in `__main__.py` lines 261-280)
- `ivpm.handlers` — Custom handlers (loaded in `package_handler_rgy.py` lines 54-60)

---

## 8. How Handlers Self-Describe

### Metadata (ClassVar)

```python
@dc.dataclass
class PackageHandler:
    name:        ClassVar[Optional[str]] = None        # e.g., "python"
    description: ClassVar[Optional[str]] = None        # e.g., "Python environment"
    phase:       ClassVar[int] = 0                     # Execution order
```

### Example: PackageHandlerPython
```python
@dc.dataclass
class PackageHandlerPython(PackageHandler):
    name:      ClassVar[str] = "python"
    leaf_when: ClassVar[Optional[List]] = None
    root_when: ClassVar[Optional[List]] = [HasType("python")]
    phase:     ClassVar[int] = 0
```

### Conditions for Activation

- **`leaf_when`**: List of predicates `callable(pkg: Package) -> bool`
  - If all return True, handler processes the package
  - `None` = always process
  
- **`root_when`**: List of predicates `callable(packages: List[Package]) -> bool`
  - If all return True, handler runs root phase
  - `None` = always run
  - Example: `[HasType("python")]` — only run if any package has type "python"

### Discovery

Handlers are discovered and listed via:

1. **Built-in:** Hardcoded in `PackageHandlerRgy._load()` (python, direnv, skills)
2. **Entry points:** Group `"ivpm.handlers"` loaded by importlib.metadata
3. **Queried at:** `main()` in `__main__.py` before parser setup, so handlers can register CLI options

---

## 9. Event System for Progress (UpdateEvent)

At `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/update_event.py`:

### Event Types

```python
class UpdateEventType(Enum):
    VENV_START, VENV_COMPLETE, VENV_ERROR
    PACKAGE_FETCH_START, PACKAGE_FETCH_PROGRESS, PACKAGE_FETCH_COMPLETE, PACKAGE_FETCH_ERROR
    HANDLER_TASK_START, HANDLER_TASK_PROGRESS, HANDLER_TASK_END, HANDLER_TASK_ERROR
```

### Event Data

```python
@dc.dataclass
class UpdateEvent:
    event_type: UpdateEventType
    package_name: Optional[str] = None
    task_id: Optional[str] = None
    task_name: Optional[str] = None
    task_message: Optional[str] = None
    task_step: Optional[int] = None
    task_total: Optional[int] = None
    parent_task_id: Optional[str] = None
    duration: Optional[float] = None
    error_message: Optional[str] = None
```

### Dispatcher Pattern

```python
class UpdateEventDispatcher:
    def add_listener(self, listener):
        self.listeners.append(listener)
    
    def dispatch(self, event: UpdateEvent):
        for listener in self.listeners:
            listener.on_event(event)
```

### Usage in Handlers

At `package_handler.py` (lines 172-206):

```python
@contextmanager
def task_context(self, info, task_id: str, task_name: str):
    handle = TaskHandle(info, task_id, task_name)
    dispatcher = getattr(info, 'event_dispatcher', None)
    if dispatcher:
        dispatcher.dispatch(UpdateEvent(
            event_type=UpdateEventType.HANDLER_TASK_START,
            task_id=task_id,
            task_name=task_name,
        ))
    try:
        yield handle
    except Exception as e:
        if dispatcher:
            dispatcher.dispatch(UpdateEvent(
                event_type=UpdateEventType.HANDLER_TASK_ERROR,
                ...
            ))
        raise
```

---

## 10. TUI Framework (Terminal User Interface)

### Status TUI
File: `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/status_tui.py`

Factory pattern:
```python
def create_status_tui(args):
    no_rich = getattr(args, "no_rich", False)
    if not no_rich and sys.stdout.isatty():
        return RichStatusTUI()
    return TranscriptStatusTUI()
```

### Update TUI
File: `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/update_tui.py`

Same factory approach for rich vs. plain-text output.

### TUI Interface

Expects `.render(results, **kwargs)` method:
- **RichStatusTUI**: Uses `rich` library for formatted tables, colors, panels
- **TranscriptStatusTUI**: Plain-text fallback

---

## 11. Project Operations Flow

### High-Level: ProjectOps Class

At `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/project_ops.py`:

```python
@dc.dataclass
class ProjectOps(object):
    root_dir: str
    debug: bool = False

    def update(self, dep_set=None, force_py_install=False, skip_venv=False, 
               args=None, lock_file=None, refresh_all=False, force=False):
        # 1. Load project info
        # 2. Setup venv
        # 3. Get handler registry
        # 4. Call on_root_pre_load
        # 5. Run PackageUpdater (async)
        # 6. Call on_root_post_load (handlers do main work)
        # 7. Write package-lock.json
        ...

    def build(self, dep_set=None, args=None, debug=False):
        # Similar pattern, calls handler.build()
        ...

    def sync(self, dep_set=None, args=None):
        # Stub to be implemented (sync-design.md)
        ...

    def status(self, args=None):
        # Load lock file
        # Call pkg.status(status_info) for each package
        # Return list of StatusResult
        ...
```

### Flow: `update` Command

```
ivpm update
  ↓
CmdUpdate.__call__(args)
  ↓
ProjectOps(project_dir).update(args=args, ...)
  ↓
1. ProjInfo.mkFromProj(root_dir)  [load ivpm.yaml]
2. PackageHandlerRgy.inst().mkHandler()
3. pkg_handler.on_root_pre_load(update_info)
4. PackageUpdater._update_async(packages_info)  [async fetch]
   - For each package:
     - pkg_handler.on_leaf_pre_load(pkg)
     - pkg.update(update_info)  [pkg-specific fetch logic]
     - pkg_handler.on_leaf_post_load(pkg)
5. pkg_handler.on_root_post_load(update_info)  [main work: venv, pip, etc.]
6. write_lock(deps_dir, all_pkgs, handler_contributions)
  ↓
RichUpdateTUI renders progress
```

---

## 12. Data Flow: Package Lock File

### Lock File Structure

File: `packages/package-lock.json`

```json
{
  "packages": {
    "package_name": {
      "src": "git|pypi|http|...",
      "url": "https://...",
      "resolved_commit": "abc123...",
      "version": "1.0.0",
      "resolved_version": "1.0.1",
      ...
    },
    ...
  },
  "sha256": "hash_of_entire_lock_file"
}
```

### Lock File I/O

At `/home/mballance/projects/fvutils/ivpm-show/src/ivpm/package_lock.py`:

- `read_lock(path) -> dict` — Parse JSON
- `write_lock(deps_dir, packages, handler_entries) -> None` — Serialize, compute hash
- `check_lock_changes(deps_dir, current_specs) -> dict` — Detect spec changes vs. lock
- `IvpmLockReader` — Parse and reproduce workspace from lock

---

## 13. Extending IVPM: Plugin Architecture

### Add a Custom Subcommand

1. Create a module with:
   ```python
   def ivpm_subcommand(subparser):
       # Add subcommand and its options
       cmd = subparser.add_parser("mycommand", help="...")
       cmd.set_defaults(func=MyCommandHandler())
   ```

2. Register via entry point in `pyproject.toml`:
   ```toml
   [project.entry-points."ivpm.ext"]
   mycommand = "mypackage.mymod:ivpm_subcommand"
   ```

3. Loaded at line 261-268 of `__main__.py`:
   ```python
   discovered_plugins = entry_points(group='ivpm.ext')
   for p in discovered_plugins:
       mod = p.load()
       if hasattr(mod, "ivpm_subcommand"):
           parser_ext.append(getattr(mod, "ivpm_subcommand"))
   ```

### Add a Custom Handler

1. Subclass `PackageHandler`:
   ```python
   @dc.dataclass
   class MyHandler(PackageHandler):
       name = "myhandler"
       root_when = [SomeCondition()]
       
       def on_leaf_post_load(self, pkg, update_info):
           # Process package
           ...
       
       def on_root_post_load(self, update_info):
           # Do main work
           ...
       
       def add_options(self, subcommands):
           # Register CLI flags for main commands
           ...
   ```

2. Register via entry point:
   ```toml
   [project.entry-points."ivpm.handlers"]
   myhandler = "mypackage:MyHandler"
   ```

3. Automatically discovered in `package_handler_rgy.py` (lines 54-60)

### Add a Custom Package Type

1. Subclass `Package` or an existing type:
   ```python
   @dc.dataclass
   class PackageSvn(Package):
       url: str = None
       revision: str = None
       
       @staticmethod
       def create(name, opts, si):
           pkg = PackageSvn(name)
           pkg.src_type = "svn"
           pkg.url = opts.get("url")
           # ...
           return pkg
   ```

2. Register via entry point:
   ```python
   def ivpm_pkgtype(pkg_types):
       pkg_types.append(("svn", PackageSvn.create, "Subversion"))
   ```

3. Loaded at lines 271-275 of `__main__.py`:
   ```python
   elif hasattr(mod, "ivpm_pkgtype"):
       pkg_types = []
       getattr(mod, "ivpm_pkgtype")(pkg_types)
       for pt in pkg_types:
           PkgTypeRgy.inst().register(pt[0], pt[1], pt[2] if len(pt) > 2 else "")
   ```

---

## 14. Key Files Quick Reference

| File | Lines | Purpose |
|------|-------|---------|
| `src/ivpm/__main__.py` | 312 | CLI entry point, parser, extension loader |
| `src/ivpm/package.py` | ~178 | Base Package class, enums |
| `src/ivpm/packages_info.py` | ~65 | PackagesInfo container |
| `src/ivpm/pkg_types/pkg_type_rgy.py` | 72 | Package type registry & factory |
| `src/ivpm/pkg_types/package_git.py` | ~500+ | Git package implementation |
| `src/ivpm/pkg_types/package_pypi.py` | 51 | PyPI package implementation |
| `src/ivpm/handlers/package_handler.py` | 220 | Base handler class |
| `src/ivpm/handlers/package_handler_rgy.py` | 83 | Handler registry & loader |
| `src/ivpm/handlers/package_handler_list.py` | 128 | Composite handler |
| `src/ivpm/handlers/package_handler_python.py` | ~300+ | Python handler (venv, pip) |
| `src/ivpm/project_ops.py` | ~400+ | High-level ops (update, build, sync, status) |
| `src/ivpm/package_updater.py` | ~500+ | Async package fetching |
| `src/ivpm/package_lock.py` | ~200+ | Lock file I/O |
| `src/ivpm/update_event.py` | ~100 | Event types for progress |
| `src/ivpm/status_tui.py` | ~200+ | Status display TUI |
| `src/ivpm/cmds/cmd_update.py` | ~30 | Update command handler |
| `src/ivpm/cmds/cmd_status.py` | 19 | Status command handler |
| `src/ivpm/cmds/cmd_sync.py` | ~50 | Sync command handler |
| `pyproject.toml` | 34 | Project config, entry points |

---

## 15. Architecture Patterns Used

1. **Singleton Pattern**: `PkgTypeRgy.inst()`, `PackageHandlerRgy.inst()`, `PkgInfoRgy.inst()`
2. **Factory Pattern**: Package types use static `create()` methods registered in registry
3. **Registry Pattern**: Central lookup (pkg types, handlers, package info)
4. **Composite Pattern**: `PackageHandlerList` aggregates all handlers
5. **Chain-of-Responsibility**: Handlers chain callbacks (pre→load→post)
6. **Observer/Dispatcher Pattern**: `UpdateEventDispatcher` notifies TUI listeners
7. **Dataclass Pattern**: `@dataclass` for data containers throughout
8. **Context Manager Pattern**: `task_context()` for progress tracking
9. **Extension Points**: Entry points for plugins (custom commands, types, handlers)

---

## 16. Recommended Reading

1. **For new commands**: Study `cmds/cmd_status.py` + `status_tui.py` (already has TUI pattern)
2. **For package types**: Study `pkg_types/package_git.py` vs. `package_pypi.py`
3. **For handlers**: Study `handlers/package_handler_python.py` (active example)
4. **For event flow**: Read `sync-design.md` (comprehensive redesign proposal)
5. **For testing**: Read `Agents.md` (development guide)

