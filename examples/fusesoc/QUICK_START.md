# FuseSoC Examples — Quick Start

## One-Minute Overview

Two working examples that demonstrate IVPM's FuseSoC integration:

| Example | Focus | Command |
|---------|-------|---------|
| **ex1-registry** | VLNV-based core resolution | `ivpm update && fusesoc core list` |
| **ex2-custom** | Auto-discovery of cores | `ivpm update && fusesoc core list` |

## Running an Example

```bash
# Set up PYTHONPATH to use IVPM's FuseSoC support
export PYTHONPATH=$(pwd)/../../src

# Run Example 1
cd ex1-registry
ivpm update
. packages/packages.envrc
fusesoc core list

# Or run Example 2
cd ../ex2-custom
ivpm update
. packages/packages.envrc
fusesoc core list
```

## What Gets Created

After `ivpm update`, you'll have:

```
packages/
├── python/           # Virtual environment with FuseSoC
├── verilator/        # Verilator simulator binary
├── i2c/              # (Ex1) or uart16550/ (Ex2) - fetched cores
├── fusesoc-cores/    # Registry index (cached)
├── fusesoc-cores.txt      # Core directory listing
├── fusesoc-cores.envrc    # FUSESOC_CORES environment
└── packages.envrc         # Aggregated environment
```

## Key Files

- **README.md** (each dir) — Detailed explanation of what the example does
- **TEST.md** — How to test and verify
- **SUMMARY.txt** — What was created and why
- **../../docs/fusesoc-examples-design.md** — Design rationale

## Common Operations

### List available cores
```bash
fusesoc core list
```

### Show core details
```bash
fusesoc core show ::i2c:1.0      # (Example 1)
fusesoc core show ::uart16550:1.0 # (Example 2)
```

### Validate a core
```bash
fusesoc lint ::i2c:1.0
```

### Run a simulation (if available)
```bash
fusesoc run ::i2c:1.0 --target=sim
```

### Check what IVPM discovered
```bash
cat packages/fusesoc-cores.txt
```

## Switching Examples

```bash
# Clean up one example
cd ex1-registry && rm -rf packages ivpm.json && cd ..

# Run the other
cd ex2-custom
ivpm update
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `PYTHONPATH: command not found` | Use `export PYTHONPATH=../../src` |
| `Failed to load handler 'skills'` | Expected (unrelated handler). Ignore. |
| `ModuleNotFoundError: ivpm.handlers.package_handler_fusesoc` | Ensure `PYTHONPATH=../../src` is set |
| `fusesoc: command not found` | Run `. packages/packages.envrc` first |

## Learn More

- **Full Documentation** → See `README.md` in each example
- **Testing** → See `TEST.md`
- **Design** → See `../../docs/fusesoc-examples-design.md`
