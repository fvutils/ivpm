# FuseSoC Example 1: Registry-Based Core Resolution

## Quick Start

```bash
cd ex1-registry
ivpm update
. packages/packages.envrc
fusesoc core list
```

## What This Example Shows

- **FuseSoC VLNV Resolution**: IVPM resolves the VLNV `::i2c:1.0` against the public FuseSoC registry to find the actual git repository and version tag
- **Python Tool Management**: IVPM creates an isolated Python virtual environment and installs FuseSoC via PyPI
- **Binary Tool Installation**: Verilator simulator is fetched as a pre-built binary, ready for simulation workflows
- **Automatic Core Discovery**: The FuseSoC handler automatically discovers `.core` files and generates the `FUSESOC_CORES` environment variable

## Understanding the Flow

### ivpm.yaml Structure

```yaml
deps:
  - name: fusesoc
    src: pypi                    # Install FuseSoC from PyPI into managed venv
  
  - name: verilator
    src: gh-rls                  # Pre-built GitHub Release binary
    url: https://github.com/edapack/verilator-bin
  
  - name: i2c
    src: fusesoc                 # VLNV resolution via the fusesoc-cores registry
    vlnv: "::i2c:1.0"
```

No `with:` clause is needed. IVPM's FuseSoC handler is always active and automatically:
- Creates a Python venv and installs `fusesoc` (because of `src: pypi`)
- Registers any fetched package containing `.core` files into `FUSESOC_CORES`

### .envrc File

The `.envrc` file (optional, requires direnv) automatically sources the IVPM-generated environment:
```bash
source_env packages/packages.envrc
```

Without direnv, manually source the environment:
```bash
. packages/packages.envrc
```

## IVPM's Responsibilities

When you run `ivpm update`, IVPM:

1. **Creates Python venv** in `packages/python/` with FuseSoC installed
2. **Fetches Verilator** binary from edapack and makes it available in PATH
3. **Resolves VLNV** `::i2c:1.0` by:
   - Cloning the `fusesoc-cores` registry (cached in `packages/fusesoc-cores/`)
   - Scanning `.core` files to find the matching VLNV entry
   - Extracting the git URL and version tag
4. **Clones the i2c repository** into `packages/i2c/`
5. **Discovers .core files** in all fetched packages
6. **Generates environment files**:
   - `packages/fusesoc-cores.txt` — machine-readable list of core directories
   - `packages/fusesoc-cores.envrc` — shell-sourceable file with `FUSESOC_CORES` export
   - `packages/packages.envrc` — aggregated environment (if direnv handler enabled)
7. **Stores state** in `ivpm.json` for incremental updates

## Next Steps: What You Can Do

After `ivpm update && . packages/packages.envrc`:

- **List available cores**:
  ```bash
  fusesoc core list
  ```

- **Inspect the i2c core**:
  ```bash
  fusesoc core show ::i2c:
  ```

- **Validate the core file**:
  ```bash
  fusesoc lint ::i2c:
  ```

- **Check discovered core directories**:
  ```bash
  cat packages/fusesoc-cores.txt
  ```

- **Run the lint target**:
  ```bash
  fusesoc run --target lint ::i2c:
  ```
  > **Note**: FuseSoC target/tool options (`--target`, `--tool`, `--flag`) must come **before** the core name. Arguments placed after the core name are passed directly to the backend tool.

- **Run a simulation** (if simulation targets exist):
  ```bash
  fusesoc run --target sim ::i2c:
  ```

## Key Takeaway

**IVPM + FuseSoC VLNV = Declarative IP Management**

Instead of manually:
1. Finding the git URL for a core
2. Cloning it
3. Editing `fusesoc.conf`
4. Installing and configuring FuseSoC

You simply declare the VLNV in `ivpm.yaml` and let IVPM handle the rest.

## Learn More

- **IVPM Documentation**: https://fvutils.github.io/ivpm
- **FuseSoC User Guide**: https://fusesoc.github.io/
- **FuseSoC Registry**: https://github.com/fusesoc/fusesoc-cores
