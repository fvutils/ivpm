# FuseSoC Example 2: Auto-Discovery of Cores

## Quick Start

```bash
cd ex2-custom
ivpm update
. packages/packages.envrc
fusesoc core list
fusesoc run --target sim ::uart16550:1.5.5
```

## What This Example Shows

- **Automatic Core Discovery**: IVPM scans all fetched packages for `.core` files without requiring explicit configuration
- **Multi-Project Setup**: Shows how multiple HDL libraries can be fetched and automatically discovered
- **Environment Integration**: Demonstrates how IVPM integrates with FuseSoC's environment (`FUSESOC_CORES`, `fusesoc.conf`)
- **Flexibility**: Easy to swap the example UART core with your own custom project

## Understanding the Flow

### ivpm.yaml Structure

```yaml
deps:
  - name: fusesoc
    src: pypi                    # Install FuseSoC from PyPI into managed venv
  
  - name: iverilog
    src: gh-rls                  # Pre-built Icarus Verilog binary
    url: https://github.com/edapack/iverilog-bin
  
  - name: uart16550
    url: https://github.com/olofk/uart16550.git  # Plain git clone
```

No `with:` clause is needed. IVPM's FuseSoC handler is always active and automatically scans all cloned packages for `.core` files, adding them to `FUSESOC_CORES`.

### Core Customization

The `uart16550.core` file in `packages/uart16550/` has been extended with a `sim` target that was not present in the upstream repository. This demonstrates how you can customize cloned packages for your project:

```yaml
targets:
  sim:
    default_tool: icarus
    filesets: [rtl]
    toplevel: uart_top
    tools:
      icarus:
        iverilog_options:
          - -g2012
```

This is an example of the flexibility IVPM provides — fetched packages are full working copies that you can customize before running FuseSoC.

### Using Your Own Custom Project

Replace `uart16550` with any git repo that contains `.core` files:

```yaml
- name: my-hdl-lib
  url: https://github.com/myorg/my-hdl-lib.git
```

## IVPM's Responsibilities

When you run `ivpm update`, IVPM:

1. **Creates Python venv** with FuseSoC installed
2. **Fetches Icarus Verilog** (iverilog) binary
3. **Clones the uart16550 repository** (or your custom project) into `packages/`
4. **Scans for .core files** in all packages:
   - Checks for valid CAPI=2 markers (avoids false positives like core dumps)
   - Supports explicit core directory declarations via `with.fusesoc.cores` in package's own `ivpm.yaml`
5. **Generates environment files**:
   - `packages/fusesoc-cores.txt` — core directory listing
   - `packages/fusesoc-cores.envrc` — `FUSESOC_CORES` export
   - `packages/packages.envrc` — aggregated environment
6. **Maintains state** in `ivpm.json` for incremental updates

## Next Steps: What You Can Do

After `ivpm update && . packages/packages.envrc`:

- **List available cores**:
  ```bash
  fusesoc core list
  ```

- **Inspect a core**:
  ```bash
  fusesoc core show ::uart16550:1.5.5
  ```

- **Validate cores**:
  ```bash
  fusesoc lint ::uart16550:1.5.5
  ```

- **Check discovered core directories**:
  ```bash
  cat packages/fusesoc-cores.txt
  ```

- **Run simulation** (uses the `sim` target defined in `packages/uart16550/uart16550.core`):
  ```bash
  fusesoc run --target sim ::uart16550:1.5.5
  ```

- **Modify IP and re-run FuseSoC commands**:
  ```bash
  # Edit files in packages/uart16550/
  fusesoc run --target sim ::uart16550:1.5.5
  ```

- **Add more packages**:
  Add additional dependencies to `ivpm.yaml` and re-run `ivpm update`. IVPM will auto-discover their cores and update the environment.

## Key Takeaway

**IVPM + FuseSoC Auto-Discovery = Declarative HDL Library Management**

Instead of:
1. Manually cloning each HDL library
2. Editing `fusesoc.conf` for each library
3. Maintaining environment setup scripts

You declare dependencies in `ivpm.yaml` and IVPM handles core discovery automatically.

## Switching to a Custom Project

To demonstrate with your own FuseSoC-enabled repository:

1. Update `ivpm.yaml`:
   ```yaml
   deps:
   - name: my-lib
     url: https://github.com/myorg/my-lib.git
   ```

2. Run `ivpm update`

3. Verify cores are discovered:
   ```bash
   fusesoc core list
   cat packages/fusesoc-cores.txt
   ```

## Learn More

- **IVPM Documentation**: https://fvutils.github.io/ivpm
- **FuseSoC User Guide**: https://fusesoc.github.io/
- **FuseSoC Registry**: https://github.com/fusesoc/fusesoc-cores
