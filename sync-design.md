# IVPM `sync` Command — Redesign

## Current State

### What exists

| File | Role | Issues |
|------|------|--------|
| `cmds/cmd_sync.py` | Command entry point | Monolithic; does git ops directly; no TUI; no dry-run |
| `pkg_types/package_git.py` – `sync()` | Stub sync method | **Bug**: references undefined variable `dir`; never called |
| `project_ops.py` – `sync()` | High-level stub | Empty (`pass`) |
| `project_ops_info.py` – `ProjectSyncInfo` | Data carrier | Exists but empty — only holds `args` + `deps_dir` |
| `project_sync.py` – `ProjectSync.sync()` | Alternative sync path | Triggers venv re-setup; uses `PackageUpdater`; separate from cmd_sync |

### Current `cmd_sync.py` flow

```
CmdSync.__call__
 └─ os.listdir(packages_dir)          # walks filesystem, not lock file
    └─ check stat.S_IWUSR (editable?)
       └─ subprocess git branch
          └─ subprocess git fetch
             └─ subprocess git merge   # fatal() on failure (aborts all remaining)
 └─ _update_lock_after_sync()
```

**Key problems:**
1. All logic is inline — no delegation to package type classes
2. Uses `os.chdir()` (fragile, not thread-safe)
3. `fatal()` on first merge failure — no error collection or summary
4. Merge conflicts and dirty-tree cases both collapse to the same "failed" path with no user guidance
5. No dry-run support
6. No rich TUI — plain `print()` throughout
7. `PackageGit.sync()` stub is dead code with a bug (`dir` is undefined)
8. Non-git packages (PyPI, http, dir) are silently noted but never offered any sync pathway
9. Lock file update is a code-duplication of logic already in `write_lock`

---

## Proposed Design

### Guiding Principles

- **Mirror the status pattern** exactly: `CmdSync → ProjectOps.sync() → pkg.sync(sync_info) → SyncTUI`
- **Collect, don't abort** — gather all results before reporting; surface all problems at once
- **Actionable output** — for every non-ok outcome, emit the exact shell commands the user needs to run
- **Dry-run is a first-class mode** — `--dry-run` / `-n` runs fetch but no merge; reports what *would* happen

---

### 1. New data types

#### `pkg_sync.py`  (new file, mirrors `pkg_status.py`)

```python
import dataclasses as dc
from typing import List, Optional

class SyncOutcome(str, enum.Enum):
    UP_TO_DATE   = "up-to-date"    # already at latest
    SYNCED       = "synced"        # fast-forward merge succeeded
    CONFLICT     = "conflict"      # merge conflict — user must resolve
    DIRTY        = "dirty"         # uncommitted changes block merge; user decides action
    AHEAD        = "ahead"         # local commits not on origin — push or rebase needed
    ERROR        = "error"         # network / git command failure (causes non-zero exit)
    SKIPPED      = "skipped"       # read-only / non-git / tag-pinned

    # dry-run variants
    DRY_WOULD_SYNC     = "dry:sync"     # would fast-forward cleanly
    DRY_WOULD_CONFLICT = "dry:conflict" # would produce conflicts
    DRY_DIRTY          = "dry:dirty"    # dirty tree would block merge

@dc.dataclass
class PkgSyncResult:
    name: str
    src_type: str
    path: str
    outcome: SyncOutcome
    branch: Optional[str] = None
    old_commit: Optional[str] = None       # commit before merge
    new_commit: Optional[str] = None       # commit after merge (or would-be)
    commits_behind: Optional[int] = None   # commits fetched from upstream
    commits_ahead: Optional[int] = None    # local commits not on origin
    conflict_files: List[str] = dc.field(default_factory=list)
    dirty_files: List[str] = dc.field(default_factory=list)
    next_steps: List[str] = dc.field(default_factory=list)  # shell commands
    error: Optional[str] = None
    skipped_reason: Optional[str] = None
```

The `next_steps` list is the key UX mechanism — each entry is a ready-to-paste shell command:

| Outcome | `next_steps` / info shown |
|---------|---------------------------|
| `CONFLICT` | Conflicting file list + `git status`, `git mergetool`, `git merge --abort` commands |
| `DIRTY` | Modified file list only — user decides how to handle |
| `ERROR` | Error message + `git fetch` retry hint |
| `DRY_WOULD_CONFLICT` | `git diff HEAD..origin/{branch}` to preview |

---

### 2. `ProjectSyncInfo` extension

```python
@dc.dataclass
class ProjectSyncInfo(ProjectOpsInfo):
    dry_run: bool = False
    packages_filter: Optional[List[str]] = None  # limit to specific packages
    # (event_dispatcher could be added later for live progress, as in update)
```

---

### 3. `PackageGit.sync()` implementation

Fix and replace the existing broken stub. Signature stays the same; return type changes to `PkgSyncResult`.

```
sync(sync_info):
  pkg_dir = deps_dir / self.name

  if not writable (stat check):
      return PkgSyncResult(outcome=SKIPPED, reason="read-only (cached)")

  if not .git dir:
      return PkgSyncResult(outcome=ERROR, error="not a git repo")

  branch = git rev-parse --abbrev-ref HEAD
  if HEAD == "HEAD":          # detached
      return PkgSyncResult(outcome=SKIPPED, reason="detached HEAD")

  old_commit = git rev-parse HEAD

  # Check dirty
  porcelain = git status --porcelain
  if porcelain and not dry_run:
      return PkgSyncResult(outcome=DIRTY, dirty_files=..., next_steps=[...])

  # Fetch (safe in both real and dry-run)
  git fetch origin

  # Check ahead/behind
  behind, ahead = git rev-list --left-right --count @{u}...HEAD
  if ahead > 0 and behind == 0:
      return PkgSyncResult(outcome=AHEAD, commits_ahead=ahead, ...)
  if ahead > 0 and behind > 0:
      # diverged — treat as AHEAD (user must rebase/push before sync)
      return PkgSyncResult(outcome=AHEAD, commits_ahead=ahead, commits_behind=behind, ...)
  if behind == 0:
      return PkgSyncResult(outcome=UP_TO_DATE, ...)

  if dry_run:
      # Simulate: is it a fast-forward?
      ff_possible = git merge-base --is-ancestor HEAD origin/{branch}
      if ff_possible:
          if porcelain:
              return PkgSyncResult(outcome=DRY_DIRTY, ...)
          return PkgSyncResult(outcome=DRY_WOULD_SYNC, commits_behind=behind, ...)
      else:
          return PkgSyncResult(outcome=DRY_WOULD_CONFLICT, commits_behind=behind, ...)

  # Real merge
  result = git merge origin/{branch}
  if returncode != 0:
      conflict_files = git diff --name-only --diff-filter=U
      git merge --abort   (optional cleanup)
      return PkgSyncResult(outcome=CONFLICT, conflict_files=..., next_steps=[...])

  new_commit = git rev-parse HEAD
  return PkgSyncResult(outcome=SYNCED, old_commit=old_commit, new_commit=new_commit, ...)
```

**Submodule handling** (currently missing in both old and new):
After a successful merge, check for `.gitmodules` and run `git submodule update --init --recursive` if present.

---

### 4. Base `Package.sync()` default

```python
# package.py
def sync(self, sync_info: ProjectSyncInfo) -> 'PkgSyncResult':
    from .pkg_sync import PkgSyncResult, SyncOutcome
    return PkgSyncResult(
        name=self.name, src_type=self.src_type,
        path=os.path.join(sync_info.deps_dir, self.name),
        outcome=SyncOutcome.SKIPPED, skipped_reason="not a VCS package"
    )
```

This means all non-git types (PyPI, http, dir, url) get a clean "skipped" result automatically.

---

### 5. `ProjectOps.sync()` — fill in the stub

```python
def sync(self, dep_set=None, args=None):
    from .pkg_sync import PkgSyncResult, SyncOutcome
    from .project_ops_info import ProjectSyncInfo
    from .pkg_types.pkg_type_rgy import PkgTypeRgy
    from .package_lock import read_lock, write_lock

    proj_info = ProjInfo.mkFromProj(self.root_dir)
    deps_dir = os.path.join(self.root_dir, proj_info.deps_dir)
    lock_path = os.path.join(deps_dir, "package-lock.json")

    lock = read_lock(lock_path)
    packages = lock.get("packages", {})

    dry_run = getattr(args, "dry_run", False)
    sync_info = ProjectSyncInfo(args=args, deps_dir=deps_dir, dry_run=dry_run)
    rgy = PkgTypeRgy.inst()

    results = []
    for name, entry in packages.items():
        src = entry.get("src", "")
        pkg = rgy.mkPackage(src, name, entry, None) if rgy.hasPkgType(src) else None
        if pkg is None:
            results.append(PkgSyncResult(
                name=name, src_type=src,
                path=os.path.join(deps_dir, name),
                outcome=SyncOutcome.SKIPPED, skipped_reason="unknown type"
            ))
            continue
        pkg.path = os.path.join(deps_dir, name)
        results.append(pkg.sync(sync_info))

    # Update lock file for actually synced packages (not dry-run)
    if not dry_run:
        _update_lock_commits(lock, results, deps_dir)
        write_lock(lock_path, lock)

    return sorted(results, key=lambda r: r.name)
```

---

### 6. `CmdSync` — thin command handler

```python
class CmdSync:
    def __call__(self, args):
        if args.project_dir is None:
            args.project_dir = os.getcwd()
        results = ProjectOps(args.project_dir).sync(args=args)
        dry_run = getattr(args, "dry_run", False)
        tui = create_sync_tui(args)
        tui.render(results, dry_run=dry_run)
        # Only exit non-zero on true fatal errors (network failure, git crash, etc.)
        # CONFLICT, DIRTY, AHEAD are informational — user is told, tool succeeded
        if any(r.outcome == SyncOutcome.ERROR for r in results):
            sys.exit(1)
```

---

### 7. `sync_tui.py` — new TUI (mirrors `status_tui.py`)

#### TUI columns for the results table

| Col | Content |
|-----|---------|
| `●` | Outcome icon (see below) |
| Package | Name |
| Branch | current branch |
| Old→New | commit range (only for SYNCED / dry-run variants) |
| Status | outcome label |
| Δ | `↓N` commits fetched |

**Outcome icons / colors:**

| Outcome | Icon | Color |
|---------|------|-------|
| `SYNCED` | `↑` | green |
| `UP_TO_DATE` | `=` | dim |
| `CONFLICT` | `✗` | bold red |
| `DIRTY` | `✎` | bold yellow |
| `AHEAD` | `↑!` | bold yellow |
| `ERROR` | `!` | bold red |
| `SKIPPED` | `—` | dim |
| `DRY_WOULD_SYNC` | `→` | cyan |
| `DRY_WOULD_CONFLICT` | `?` | yellow |
| `DRY_DIRTY` | `?` | yellow |

#### Post-table: next-steps block

For each result with `next_steps`, emit a panel. **For `DIRTY` packages, list the locally-modified files only — do not prescribe what the user should do with them.** For `CONFLICT` packages, show conflicting files and recovery commands.

```
╭─ Attention ──────────────────────────────────────────────────────────╮
│  foo  [conflict]                                                      │
│    Conflicting files:                                                 │
│      src/foo.c                                                        │
│    cd packages/foo && git status                                      │
│    cd packages/foo && git mergetool                                   │
│    cd packages/foo && git merge --abort                               │
│                                                                       │
│  bar  [dirty — cannot sync until resolved]                            │
│    Modified files:                                                    │
│      README.md                                                        │
│      src/bar.h                                                        │
╰──────────────────────────────────────────────────────────────────────╯
```

The `dirty_files` list (from `git status --porcelain`) is shown as-is; the user decides whether to stash, commit, or discard.

#### Summary panel

```
╭─ Sync ─── 2 synced · 1 up-to-date · 1 conflict · 3 skipped ─────────╮
│  [dry-run mode — no changes were made]                                │
╰──────────────────────────────────────────────────────────────────────╯
```

Border color: green (all ok) / yellow (skipped/dirty) / red (conflict or error)

#### `create_sync_tui(args)`

```python
def create_sync_tui(args):
    no_rich = getattr(args, "no_rich", False)
    if not no_rich and sys.stdout.isatty():
        return RichSyncTUI()
    return TranscriptSyncTUI()
```

---

### 8. Argument parser additions (`__main__.py`)

```python
sync_cmd.add_argument("-p", "--project-dir", dest="project_dir", default=None)
sync_cmd.add_argument("-n", "--dry-run", dest="dry_run", action="store_true", default=False,
    help="Fetch and check sync-ability without merging")
sync_cmd.add_argument("--no-rich", action="store_true", default=False,
    help="Plain-text output without Rich formatting")
```

---

## File Change Summary

| File | Change |
|------|--------|
| `pkg_sync.py` | **NEW** — `SyncOutcome` enum + `PkgSyncResult` dataclass |
| `sync_tui.py` | **NEW** — `RichSyncTUI`, `TranscriptSyncTUI`, `create_sync_tui()` |
| `cmds/cmd_sync.py` | **REPLACE** — delegate to `ProjectOps.sync()` + TUI; add `sys.exit(1)` on failures |
| `project_ops.py` | **FILL STUB** — implement `sync()` using registry pattern |
| `project_ops_info.py` | **EXTEND** — add `dry_run`, `packages_filter` to `ProjectSyncInfo` |
| `pkg_types/package_git.py` | **REPLACE** `sync()` stub — full implementation returning `PkgSyncResult` |
| `package.py` | **IMPROVE** — base `sync()` returns `PkgSyncResult(SKIPPED)` |
| `__main__.py` | **EXTEND** — add `-n/--dry-run`, `--no-rich`, `-p` to sync subcommand |
| `package_lock.py` | **REVIEW** — ensure `write_lock` handles partial updates cleanly |

---

## Open Issues

### Bugs to fix along the way

1. **`PackageGit.sync()` has a NameError** — line 400 references undefined `dir`. This code has never been callable.
2. **`cmd_sync.py` uses `os.chdir()`** — this is not thread-safe. All new code should pass `cwd=` to `subprocess.run()`.
3. **`_update_lock_after_sync()` re-implements lock I/O** — should use existing `write_lock` / `read_lock`.

### Design decisions needing confirmation

4. **Auto-stash**: Should `--dry-run` detect dirty trees as blocking, or should we offer `--stash` to auto-stash before merge and pop after? (Tradeoff: convenience vs. surprising side effects.)

5. **Parallel fetch**: `git fetch` for each package is currently sequential. Packages are independent — we could parallelize using `asyncio` + semaphore (same pattern as `PackageUpdater`). Worth doing here or leave for a follow-up?

6. **project_sync.py vs ProjectOps.sync()**: There are two sync paths:
   - `project_sync.py` – full "setup" sync (venv + deps via `PackageUpdater`)
   - `cmd_sync.py` / `ProjectOps.sync()` – the "pull latest" sync
   These have different purposes but the naming is confusing. Consider renaming `project_sync.py` to `project_setup.py` or similar to avoid confusion.

7. **Non-git package sync**: PyPI packages could check for a newer version matching constraints. `cache: true` packages could re-resolve to the latest commit. **Not in scope for this change — leave for future.** The `SKIPPED` + `skipped_reason` fields and the `Package.sync()` base default ensure the architecture already supports adding this later without structural changes.

8. **Tag-pinned packages**: A git package with `tag:` set should be `SKIPPED` with a note "pinned to tag X" — syncing a tag-pinned package doesn't make sense. **Confirmed: detect this case and report it explicitly.** Add a check in `PackageGit.sync()` before fetch: if `self.tag is not None`, return `PkgSyncResult(outcome=SKIPPED, skipped_reason="pinned to tag %s" % self.tag)`.

9. **Exit code semantics**: **Only a true fatal error (network failure, git command crash, missing project metadata) should produce a non-zero exit code.** `CONFLICT` and `DIRTY` are informational outcomes — the user is informed and can act, but the tool itself completed successfully. Update `CmdSync` accordingly:

```python
# Only exit non-zero on true errors
fatal_outcomes = [r for r in results if r.outcome == SyncOutcome.ERROR]
if fatal_outcomes:
    sys.exit(1)
```

10. **`--packages` filter**: Useful for "sync just this one dep". Low effort to add (pass through `packages_filter` on `ProjectSyncInfo` and filter in `ProjectOps.sync()`). Include now or later?

### Overlooked opportunities

11. **Submodule sync**: After a successful merge, if `.gitmodules` exists, run `git submodule update --init --recursive`. Currently missing in both old and new code paths.

12. **Post-sync hook**: `ivpm.yaml` has a `handlers` mechanism. A `post-sync` hook would let projects run `make` or similar after packages update.

13. **`--summary` / CI mode**: In CI, a `--summary` flag could emit a GitHub Actions step summary (markdown table) via `$GITHUB_STEP_SUMMARY`. Low effort; high value for monorepo workflows.

14. **Ahead-of-upstream warning**: **Confirmed: implement this.** If a package is *ahead* of origin (local commits not pushed), report it as a distinct outcome rather than letting a non-fast-forward merge failure confuse the user. Add `AHEAD` to `SyncOutcome`:

```python
AHEAD = "ahead"  # local commits exist that are not on origin
```

Detection: after fetch, if `ahead > 0` (from `git rev-list --left-right --count @{u}...HEAD`), return `AHEAD` with `commits_ahead` count and a note to push or rebase. This also correctly handles the case where `behind == 0` and `ahead > 0` — currently both map silently to "up to date".

15. **Lock file integrity**: **Confirmed: fix the SHA256 bug.** The current `_update_lock_after_sync()` computes the hash before inserting the `sha256` key, then writes the dict with the key already in it — so the stored hash never matches the file content. The fix is to centralise this in `write_lock`: serialize the body without `sha256`, compute the hash, then insert it before the final write (and verify the same way on read). All callers benefit.
