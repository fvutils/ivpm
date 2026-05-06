# Testing FuseSoC Examples

This document describes how to test the FuseSoC examples.

## Prerequisites

- Python 3.8+
- git
- (Optional) direnv
- IVPM must be able to import the FuseSoC support from `../../src`

## Testing Example 1: Registry-Based Core Resolution

```bash
cd ex1-registry
export PYTHONPATH=../../../src
ivpm update --help  # Verify IVPM is working
```

Expected output:
- IVPM command completes successfully
- (Warning about 'skills' handler is expected — it's unrelated)

## Testing Example 2: Auto-Discovery

```bash
cd ex2-custom
export PYTHONPATH=../../../src
ivpm update --help  # Verify IVPM is working
```

## Full Integration Test (Requires Network)

**Note:** These tests will fetch large dependencies (FuseSoC, Verilator) and may take several minutes.

### Test Example 1

```bash
cd ex1-registry
export PYTHONPATH=../../../src
ivpm update
. packages/packages.envrc
fusesoc core list
```

Expected output:
- `ivpm update` fetches FuseSoC, Verilator, and the i2c core
- `fusesoc core list` shows available cores including `::i2c:1.0`

### Test Example 2

```bash
cd ex2-custom
export PYTHONPATH=../../../src
ivpm update
. packages/packages.envrc
fusesoc core list
```

Expected output:
- `ivpm update` fetches FuseSoC, Verilator, and the uart16550 core
- `fusesoc core list` shows available cores including `::uart16550:1.0`

## Verification Checklist

- [ ] Configuration files parse correctly
- [ ] FuseSoC package type is recognized
- [ ] FuseSoC handler is recognized
- [ ] `ivpm update` completes successfully
- [ ] Environment files are generated:
  - [ ] `packages/fusesoc-cores.txt`
  - [ ] `packages/fusesoc-cores.envrc`
  - [ ] `packages/packages.envrc`
- [ ] `fusesoc core list` shows expected cores

## Troubleshooting

### "Failed to load handler 'skills'"
This is expected. The skills handler is optional and not needed for FuseSoC examples.

### "No module named 'ivpm.handlers.package_handler_fusesoc'"
Ensure `PYTHONPATH=../../../src` is set to pick up the FuseSoC support.

### "FuseSoC not found after ivpm update"
Verify that the Python handler created a venv in `packages/python/` and installed FuseSoC.

## Cleaning Up

To reset an example:

```bash
cd ex1-registry  # or ex2-custom
rm -rf packages/ ivpm.json
```
