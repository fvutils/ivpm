# FuseSoC Integration Examples

These examples demonstrate how IVPM integrates with FuseSoC to provide declarative IP management.

## Examples

### [Example 1: Registry-Based Core Resolution](./ex1-registry/)

Demonstrates VLNV-based core resolution from the public FuseSoC registry.

**Key Concepts:**
- Resolve VLNV identifiers via the FuseSoC registry
- Fetch cores from git repositories
- Automatic environment setup

**Quick Start:**
```bash
cd ex1-registry
ivpm update
. packages/packages.envrc
fusesoc core list
```

### [Example 2: Auto-Discovery of Cores](./ex2-custom/)

Demonstrates automatic discovery of `.core` files in any fetched package.

**Key Concepts:**
- Selective core imports (avoid importing tool packages)
- Auto-detect cores in multiple packages
- Integrate custom HDL libraries

**Quick Start:**
```bash
cd ex2-custom
ivpm update
. packages/packages.envrc
fusesoc core list
```

## Common Workflow

Both examples follow the same workflow:

```bash
# 1. Update dependencies (fetch packages + set up environment)
ivpm update

# 2. Activate the environment
. packages/packages.envrc

# 3. Use FuseSoC as normal
fusesoc core list
fusesoc lint <core>
fusesoc run <core> --target=sim
```

## What IVPM Does

- **Python Management**: Creates isolated venv with FuseSoC installed
- **Tool Installation**: Fetches Verilator (or other tools) from binary releases
- **Core Discovery**: Scans packages for `.core` files
- **Environment Setup**: Generates `FUSESOC_CORES` and shell environment files
- **State Persistence**: Stores state in `ivpm.json` for incremental updates

## Testing

To test these examples with the FuseSoC support from the IVPM repo:

```bash
cd ex1-registry  # or ex2-custom
PYTHONPATH=../../src ivpm update
. packages/packages.envrc
fusesoc core list
```

## Learn More

- **IVPM + FuseSoC Design**: See `../../docs/fusesoc-integration.md`
- **IVPM Documentation**: https://fvutils.github.io/ivpm
- **FuseSoC User Guide**: https://fusesoc.github.io/
