# Clone sub-command
The `clone` sub-command creates a new workspace from an existing
local or remote data source. The data-source type is determined by the
URL scheme and extension. A new extension mechanism similar to the
package type scheme used for dependencies must be implemented here to 
support making 'clone' a modular, extensible operation.

The clone sub-command must accept the following arguments and options.
The command format is:
ivpm clone [options] src-url/path [workspace_dir]

If 'workspace_dir' is not specified, then the basename of the src-url/path
is used. It is an error for this path to exist.

Options:
- -a/--anonymous -- controls whether Git repositories are cloned anonymously or using a key
- -b/--branch -- controls the target branch. Checks out the branch if it exists; checks out
  the tag of that name (detached) if no such branch exists; otherwise creates the branch
- -t/--tag -- checks out the named tag (detached HEAD). Errors if no such tag exists
- -r/--revision -- checks out the given commit hash / commit-ish (detached HEAD)
- -d/--dep-set -- Specifies the dependency set for ivpm to fetch
- --py-pip, --py-uv -- Specifies whether to use `pip` or `uv` to create the virtual environment

## Clone extension mechanism
Implementations provide a class with lifecycle methods to implement `clone` for a specific
source type. Lifecycle methods are:
- pre-clone -- enables checking
- clone
- post-clone



## Git implementation
Add a built-in git implementation of the clone extension mechanism. Implement ref selection by:
- cloning the default branch
- Check if the branch already exists (remotely, then locally) and check it out if it does
- If no such branch exists but a tag of that name does, check out the tag with a detached HEAD
- If neither exists, create a new branch

`--tag` and `--revision` always produce a detached HEAD and never create anything: an
unknown tag or an unresolvable revision is reported as an error.


