# PTY-Based Interactive Subprocess Design

## Problem Statement

IVPM runs a Rich-based TUI (`Live()` display) while executing subprocesses for operations like
`update`, `sync`, and `build`. Some of those subprocesses — git clones over SSH, pip installs
with credential managers, GPG-signed commits — may block waiting for user input (passwords,
passphrases, host-key confirmations, MFA tokens).

Currently these prompts are either silently dropped (because the subprocess detects a pipe and
suppresses them) or they hang indefinitely with no feedback to the user.

This document describes a strategy and implementation plan for gracefully detecting subprocess
input requests and surfacing them to the user via Rich, while preserving the TUI experience.

---

## Core Strategy

Three independent problems must be solved together:

1. **Detection** — recognise when a subprocess is blocking for input
2. **Rich pause/resume** — cleanly suspend the `Live()` display, prompt the user, then restore it
3. **PTY plumbing** — ensure the subprocess actually emits its prompts in the first place

---

## Layer 1: PTY — The Non-Negotiable Foundation

### Why pipes are not enough

Tools like `git`, `ssh`, `sudo`, and `gpg` call `isatty()` on their stdin/stdout. When they
detect a pipe they either:
- suppress the password prompt entirely and fail silently, or
- emit the prompt to `/dev/tty` directly (bypassing Python's file descriptors completely).

A PTY (pseudo-terminal) makes the subprocess believe it is connected to a real terminal, so
prompts appear on the PTY master fd where Python can read and intercept them.

### Recommended library: `ptyprocess`

`ptyprocess` is the lower-level library that `pexpect` uses internally. It is preferred here
because:
- it gives direct access to the PTY master file descriptor for custom `select()`-based I/O
- it avoids `pexpect`'s opinionated `expect()` pattern-matching, which conflicts with streaming
  output to the TUI
- it is a small, stable, well-maintained package

```python
from ptyprocess import PtyProcessUnicode

proc = PtyProcessUnicode.spawn(
    ['git', 'clone', 'git@github.com:org/repo.git'],
    dimensions=(24, 120),
)
```

### Non-TUI / non-TTY fallback

When IVPM is running without a TTY (CI, log-level output mode, piped output), the PTY path is
bypassed entirely. `subprocess.Popen` with pipes is used as today. This preserves existing
behaviour for scripted/automated use.

---

## Layer 2: Input-Needed Detection — Two Complementary Signals

Neither signal alone is reliable; both are used in combination.

### Signal A: Pattern Matching (primary, high precision)

A catalogue of regex patterns covers the vast majority of real-world prompts:

```python
import re
from typing import NamedTuple

class PromptPattern(NamedTuple):
    pattern: re.Pattern
    label: str      # display label shown to the user
    secret: bool    # True → mask input (password mode)

PROMPT_PATTERNS: list[PromptPattern] = [
    # Passwords / passphrases
    PromptPattern(re.compile(r'[Pp]assword\s*:\s*$'),           'Password',    True),
    PromptPattern(re.compile(r'[Pp]assphrase\s*:\s*$'),         'Passphrase',  True),
    PromptPattern(re.compile(r'Enter passphrase for key'),       'Passphrase',  True),
    PromptPattern(re.compile(r'Enter PIN'),                      'PIN',         True),
    PromptPattern(re.compile(r'Token\s*:\s*$'),                  'Token',       True),
    # Usernames / identity
    PromptPattern(re.compile(r'[Uu]sername\s*:\s*$'),           'Username',    False),
    PromptPattern(re.compile(r'[Uu]ser\s*:\s*$'),               'User',        False),
    # SSH host verification
    PromptPattern(re.compile(
        r'Are you sure you want to continue connecting.*\(yes/no', re.DOTALL),
                                                                 'yes/no',      False),
    # Generic yes/no / confirmation
    PromptPattern(re.compile(r'\(yes/no(/\[fingerprint\])?\)'), 'yes/no',      False),
    PromptPattern(re.compile(r'\[y/n\]\s*:?\s*$', re.I),       'y/n',         False),
    PromptPattern(re.compile(r'\[Y/n\]\s*:?\s*$'),              'Y/n',         False),
    PromptPattern(re.compile(r'\[n/Y\]\s*:?\s*$'),              'n/Y',         False),
    PromptPattern(re.compile(r'Proceed\?.*\[y/N\]', re.I),     'Proceed y/N', False),
    # sudo
    PromptPattern(re.compile(r'\[sudo\] password'),             'sudo password', True),
    # pip / uv credential prompts
    PromptPattern(re.compile(r'Please provide credentials'),    'Credentials', False),
]
```

When any pattern matches the most recently accumulated output chunk, input is needed
immediately — no timeout required.

### Signal B: Quiescence Timeout (heuristic fallback)

When the subprocess stops producing output for `QUIESCENCE_TIMEOUT` seconds *after* emitting at
least one byte in the current "work unit", it *may* be waiting for input. This catches prompts
that do not match the pattern catalogue.

```python
QUIESCENCE_TIMEOUT = 0.35  # seconds — conservative to avoid false positives
```

On a quiescence-triggered detection, the raw accumulated output text is shown to the user as
context, and a free-form text input is offered.

**Important:** quiescence is only checked when the subprocess has produced output recently.  A
process that is simply doing slow work (compiling, resolving dependencies) must not be
misidentified as waiting for input. The heuristic is gated on "we received output, then silence
followed", not just "silence".

---

## Layer 3: I/O Loop Architecture

The I/O loop runs in a dedicated **background thread**, leaving the main thread free to drive
the Rich TUI. The two threads communicate through a pair of `threading.Event` objects and a
small shared-state dict protected by a `threading.Lock`.

```
┌──────────────────────────────────────────────────────────────────────┐
│                          PTY I/O Thread                              │
│                                                                      │
│  loop:                                                               │
│    r = select([master_fd], timeout=QUIESCENCE_TIMEOUT)              │
│    if readable:                                                      │
│        chunk = os.read(master_fd)                                    │
│        accumulate chunk                                              │
│        if pattern_match(chunk):                                      │
│            → set prompt_needed_event                                 │
│            → wait on response_event                                  │
│            → write response to master_fd                            │
│            → clear both events, continue loop                       │
│        else:                                                         │
│            → emit chunk to TUI output callback                      │
│    elif timeout and accumulated_since_last_emit:                    │
│        → set prompt_needed_event (quiescence heuristic)             │
│        → wait on response_event                                     │
│        → write response to master_fd                               │
│        → continue loop                                              │
│    if not proc.isalive(): break                                      │
└──────────────────────────────────────────────────────────────────────┘
              │ prompt_needed_event
              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   Main Thread (TUI / event loop)                    │
│                                                                      │
│  on prompt_needed_event:                                            │
│    1. live.stop()                                                    │
│    2. console.print(context_text)   ← show subprocess output so far │
│    3. response = Prompt.ask(label, password=secret, console=console) │
│    4. shared_state['response'] = response                           │
│    5. live.start()                                                   │
│    6. response_event.set()                                          │
└──────────────────────────────────────────────────────────────────────┘
```

### Why this direction of control?

The main thread owns the `Live` instance; only it may call `live.stop()` / `live.start()`.
Calling these from a background thread is not thread-safe. The I/O thread must therefore
*signal* the need and *wait* for the main thread to handle it.

---

## Layer 4: Rich Live Pause/Resume

Rich's `Live` display cannot tolerate `input()` or `Prompt.ask()` while it is rendering.
The correct API is `live.stop()` / `live.start()`, **not** exiting and re-entering the context
manager (which would erase and re-draw the display):

```python
def handle_subprocess_prompt(
    live: Live,
    console: Console,
    context_text: str,
    label: str,
    secret: bool,
) -> str:
    live.stop()
    try:
        if context_text.strip():
            # Show what the subprocess printed before blocking
            console.print(f"\n[dim]{context_text.strip()}[/dim]")
        response = Prompt.ask(
            f"[bold yellow]⚠  {label}[/bold yellow]",
            password=secret,
            console=console,
        )
    finally:
        live.start()   # always resume, even on Ctrl-C / exception
    return response
```

---

## Edge Cases

| Situation | Handling |
|-----------|----------|
| User hits Ctrl-C during prompt | `finally: live.start()` restores display; exception propagates normally |
| Process exits before response is written | Check `proc.isalive()` before `os.write()`; log warning and continue |
| Output arrives *after* a prompt but *before* user responds | Buffer it in the I/O thread; flush to TUI callback after `live.start()` |
| Multiple sequential prompts (username → password) | I/O thread loops naturally; each `select()` timeout or pattern match triggers a new handshake |
| Subprocess emits prompt with no trailing newline | `select()` returns immediately when bytes are available; no newline needed |
| Non-TTY / CI mode | Skip PTY entirely; use `subprocess.Popen` with pipes as today |
| Windows | `ptyprocess` is Unix-only; document this; fall back to pipe-based Popen on Windows |
| Very wide/ANSI-escaped output in prompt context | Strip ANSI before displaying context text in Rich; use `re.sub(r'\x1b\[[0-9;]*m', '', text)` |

---

## Implementation Plan

### Phase 0 — Groundwork

**0.1 Add `ptyprocess` dependency**

Add `ptyprocess` to `pyproject.toml` (and `requirements.txt` / `ivpm.yaml` as appropriate).
Guard the import with a platform check so Windows users get a clear error rather than a
mysterious `ModuleNotFoundError`.

**0.2 Write unit tests for pattern matching**

Before writing any I/O code, create `test/unit/test_prompt_patterns.py` that:
- asserts each `PROMPT_PATTERNS` entry matches its intended trigger string
- asserts none of the patterns fire on innocent output lines (false-positive guard)

---

### Phase 1 — PTY Runner

**1.1 Create `src/ivpm/pty_runner.py`**

New class `PtyRunner` that wraps `ptyprocess.PtyProcessUnicode`:

```python
class PtyRunner:
    def __init__(
        self,
        cmd: list[str],
        cwd: str | None = None,
        env: dict | None = None,
        output_callback: Callable[[str], None] | None = None,
        prompt_callback: Callable[[str, str, bool], str] | None = None,
        dimensions: tuple[int, int] = (24, 200),
    ): ...

    def run(self) -> int:
        """Spawn process, drive I/O loop in this thread, return exit code."""
        ...

    def run_async(self) -> threading.Thread:
        """Spawn process and drive I/O loop in a background thread."""
        ...
```

- `output_callback(text)` — called for normal output lines; TUI implementation will route
  these to the Rich console or log
- `prompt_callback(context, label, secret) -> response` — called when input is needed; TUI
  implementation will pause Live and call `Prompt.ask()`

**1.2 Implement the I/O loop inside `PtyRunner.run()`**

Key implementation notes:
- Use `os.read(master_fd, 4096)` inside `select()` with `QUIESCENCE_TIMEOUT`
- Accumulate bytes since the last `output_callback` invocation
- On each read, scan accumulated bytes for pattern matches
- On quiescence timeout with non-empty accumulator, fire prompt heuristic
- After writing response, clear accumulator and reset quiescence clock
- Use `proc.isalive()` as the loop-exit condition; also handle `OSError` on `os.read()`
  (raised when PTY closes)

**1.3 Write integration test**

`test/unit/test_pty_runner.py` — spawn a small Python script that:
1. prints some normal output
2. `print("Password: ", end="", flush=True)` then calls `input()`
3. prints the received value back

Assert that `PtyRunner` detects the prompt, invokes `prompt_callback`, the response is
printed by the script, and exit code is 0.

---

### Phase 2 — TUI Integration

**2.1 Add `prompt_callback` to `RichUpdateTUI`**

In `src/ivpm/update_tui.py`, add:

```python
def make_prompt_callback(self) -> Callable[[str, str, bool], str]:
    """Return a prompt_callback suitable for PtyRunner."""
    def _callback(context: str, label: str, secret: bool) -> str:
        return handle_subprocess_prompt(
            self.live, self.console, context, label, secret
        )
    return _callback
```

**2.2 Add `prompt_callback` to `RichSyncTUI`**

Same pattern in `src/ivpm/sync_tui.py`.

**2.3 Add a shared `handle_subprocess_prompt()` helper**

Place in a new `src/ivpm/tui_utils.py` (or inline in each TUI file if preferred):

```python
from rich.live import Live
from rich.console import Console
from rich.prompt import Prompt

def handle_subprocess_prompt(
    live: Live,
    console: Console,
    context: str,
    label: str,
    secret: bool,
) -> str:
    live.stop()
    try:
        if context.strip():
            clean = re.sub(r'\x1b\[[0-9;]*[mGKHF]', '', context).strip()
            console.print(f"[dim]{clean}[/dim]")
        return Prompt.ask(
            f"[bold yellow]⚠  {label}[/bold yellow]",
            password=secret,
            console=console,
        )
    finally:
        live.start()
```

---

### Phase 3 — Wire Up Subprocess Callers

**3.1 Modify `subprocess_runner.py`**

Add a `use_pty: bool` parameter and a `prompt_callback` parameter to `SubprocessRunner.run()`.
When `use_pty=True` and a `prompt_callback` is provided, delegate to `PtyRunner` instead of
`subprocess.run()`. Return the same `(returncode, stdout, stderr)` tuple.

**3.2 Modify `ivpm_subprocess.py`**

Add a `pty: bool = False` parameter to `ivpm_popen()`. When `pty=True`, return a `PtyRunner`
configured with the same environment setup that `ivpm_popen` currently applies.

**3.3 Identify callers that need PTY**

Audit subprocess invocations and enable PTY for operations that may prompt:

| Caller | Operation | Enable PTY? |
|--------|-----------|-------------|
| `PackageUpdater` (git clone/fetch) | git over SSH | Yes |
| `cmd_build.py` | pip install | Yes (credentials) |
| `cmd_build.py` | setup.py / cmake | Maybe (uncommon) |
| `cmd_clone.py` | git clone | Yes |
| Internal venv creation | `python -m venv` | No |

PTY is **only** enabled when `use_rich=True` (i.e., stdout is a TTY and log level is NONE).
In non-interactive mode, the existing pipe-based path is unchanged.

---

### Phase 4 — Threading Handshake (for async TUI operations)

The update and sync TUI operations drive the `Live` display from the main thread while
subprocesses run in worker threads (or the main thread sequentially). The `PtyRunner` I/O loop
needs to signal back to whichever thread owns the `Live` display.

**4.1 Thread-safe prompt handshake**

Add to `PtyRunner`:

```python
self._prompt_event = threading.Event()
self._response_event = threading.Event()
self._prompt_state: dict = {}   # context, label, secret, response
self._state_lock = threading.Lock()
```

When `run_async()` is used and a prompt is needed, the background I/O thread:
1. Populates `_prompt_state` under the lock
2. Sets `_prompt_event`
3. Waits on `_response_event`

The caller (TUI main thread) polls `_prompt_event` (or registers a callback on it) to drive
the `handle_subprocess_prompt()` call and then sets `_response_event`.

**4.2 Event-system integration (optional, preferred)**

Rather than polling, add a new event type to IVPM's existing event dispatcher:

```python
class UpdateEventType(Enum):
    ...
    SUBPROCESS_INPUT_REQUIRED   # new
    SUBPROCESS_INPUT_COMPLETE   # new (for logging / audit)
```

`PtyRunner` fires `SUBPROCESS_INPUT_REQUIRED` via the dispatcher; `RichUpdateTUI` handles it
by invoking `handle_subprocess_prompt()` and posting the response back.

---

### Phase 5 — Tests and Polish

**5.1 Test: quiescence heuristic**

Spawn a process that writes output then calls `time.sleep(1)` (not `input()`). Assert that the
heuristic fires after `QUIESCENCE_TIMEOUT` and the prompt callback is invoked.

**5.2 Test: multiple sequential prompts**

Spawn a script that asks for username then password in sequence. Assert both callbacks fire with
correct `secret` values and the process receives both responses.

**5.3 Test: Ctrl-C during prompt**

Simulate `KeyboardInterrupt` in the `prompt_callback`. Assert `live.start()` is called in the
`finally` block and the `Live` display is restored.

**5.4 Test: non-TTY fallback**

Run `PtyRunner` with `use_pty=False`. Assert it uses `subprocess.run()` and the
`prompt_callback` is never invoked.

**5.5 ANSI stripping**

Verify that ANSI escape sequences in subprocess output (colours, cursor movement) are stripped
before being shown as prompt context in the Rich console.

**5.6 Docs**

Update `docs/tui_update.md` and any relevant notes to document the PTY behaviour and the
`use_pty` flag.

---

## File Map

```
src/ivpm/
├── pty_runner.py           ← NEW: PtyRunner class + I/O loop
├── prompt_patterns.py      ← NEW: PROMPT_PATTERNS catalogue
├── tui_utils.py            ← NEW: handle_subprocess_prompt() helper
├── subprocess_runner.py    ← MODIFIED: add use_pty + prompt_callback
├── ivpm_subprocess.py      ← MODIFIED: add pty= parameter
├── update_tui.py           ← MODIFIED: add make_prompt_callback()
├── sync_tui.py             ← MODIFIED: add make_prompt_callback()
└── update_event.py         ← MODIFIED: add SUBPROCESS_INPUT_REQUIRED event (Phase 4)

test/unit/
├── test_prompt_patterns.py ← NEW: unit tests for pattern catalogue
├── test_pty_runner.py      ← NEW: integration tests for PtyRunner
└── test_tui_prompt.py      ← NEW: tests for handle_subprocess_prompt + Live stop/start
```

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `ptyprocess` | `>=0.7` | PTY spawn + management |
| `rich` | already present | Live display, Prompt.ask |

`ptyprocess` has no dependencies of its own and is already an indirect dependency of many
Python tools (it is pulled in by `pexpect`, Jupyter, etc.), so version conflicts are unlikely.

---

## Non-Goals

- **Windows support for PTY**: `ptyprocess` is Unix-only. Windows users will continue to use
  the pipe-based path. A future enhancement could use `winpty` or `conpty` for Windows PTY
  support, but that is out of scope here.
- **Automating credentials**: This design intentionally never stores or auto-fills passwords.
  The user is always prompted interactively.
- **Full pexpect integration**: `pexpect` is not introduced as a dependency; `ptyprocess` alone
  is sufficient and keeps the dependency footprint small.
