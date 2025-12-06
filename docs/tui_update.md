
# IVPM TUI Update

IVPM currently has many locations where information is directly
printed with 'print', and output from system commands is typically
just emitted to the console. 

Add a new option to IVPM (applies to all sub-commands) '--log-level=[INFO,DEBUG,WARN,NONE]'
that properly configures the 'logging' module. The default is 'NONE'

Update IVPM so all debug or verbose output goes through
the logging module. 

Add a utility for running subprocesses to allow the output to be
captured.

## Command-line output for 'update' operation

Update the IVPM 'update' command so:
- The commandline TUI operates in two modes:
  - 'rich'-based with high-level view
  - transcript-based mode with much more output

In both cases, the output is driven by listening to update
events.

### Rich-based TUI 
Under normal non-verbose (with --log-level=NONE) mode when the output is the console
and not a file, use a 'rich'-based output. 
- Create a stacked progress display that
  - Shows all packages currently being loaded with a progress spinner to the lft
    Example: ~ <pkgname> - <pkgtype> <pkgsrc>
  - When a package is fully loaded, stop the progress spinner and display a check-mark instead
  - When a package is fully loaded, update the line in the stacked progress display to 
    show how long it took to load the package and whether it hit the cache
    Example: x <pkgname> - <pkgtype> <pkgsrc> C - 10s
  - When the number of packages exceeds the visible rows on the console,
    remove the oldest completed package to free up space
- After all packages have been loaded, display a styled summary in a text box
- If an error is encountered while loading a package, mark the package in red and
  show its status with an 'x' and display the error message in the status line.
- Report the error in the summary, and suggest re-running with verbosity increased
  for more details

When using the Rich-based TUI, no output from subprocesses shall be displayed.

### Transcript-based mode
When the output is verbose (--log-level!=NONE) or when the output is directed to
a file, use a plain text output.
- Show the update operation for each package starting and finishing
  For example:
    >> <pkgname> - <pkgtype> <pkgsrc>
    << <pkgname>
- If verbose output is enabled, show the output of sub-processes inline

