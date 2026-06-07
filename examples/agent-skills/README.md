# Agent-Skills Integration Example

This example shows how IVPM fetches pre-built EDA tool binaries **and** wires the
*agent skills* that ship with those tools into your AI assistant. This ensures
that an AI agent working in the project immediately knows how to drive the tools.

We fetch **yosys** (RTL synthesis), **verilator**, and **nextpnr** 
(FPGA place-and-route) as
binary releases from the [EDAPack](https://github.com/edapack) GitHub-release
feed, then ask our Agent to take a small `blink.v` design through the flow.

## What's here

| File          | Purpose                                                      |
|---------------|--------------------------------------------------------------|
| `ivpm.yaml`   | Fetches the yosys/nextpnr binaries and enables Claude skills |
| `blink.v`     | A minimal LED-blinker Verilog module to synthesize           |
| `tb_blink.sv` | Simple simulation testbench                                  |
| `.envrc`      | Activates the tool environment via `direnv`                  |

## Quick start

```bash
cd examples/agent-skills
ivpm update                       # fetch binaries + install agent skills
. packages/packages.envrc         # put yosys / nextpnr-ecp5 on PATH
                                  # (or: direnv allow)
```

Then open your AI assistant in this directory and paste the following prompt:
```
Simulate the blink design and testbench in Verilator, using available skills.
Then, synthesize the design targeting ecp5 using yosys and nextpnr
```

## How the agent-skills wiring works

`ivpm.yaml` enables Agent Skills at the project level:

```yaml
package:
  with:
    agents:
      claude: true        # mirror skills into .claude/skills/ too
```

After `ivpm update`, IVPM has created links for every discovered skill:

```
.agents/skills/            # agent-neutral (any skills-aware agent)
.claude/skills/            # Claude Code reads these automatically
├── yosys-bin-yosys  -> ../../packages/yosys-bin/skills/yosys
├── yosys-bin-sby    -> ../../packages/yosys-bin/skills/sby
├── yosys-bin-sv2v   -> ../../packages/yosys-bin/skills/sv2v
└── nextpnr-skill-nextpnr -> ../../packages/nextpnr-skill/skills/nextpnr
```

Each link points at a directory containing a `SKILL.md`. Claude Code running in
this directory discovers them and loads the relevant one on demand — so when you
ask it to "synthesize with yosys", it already knows the `synth_ecp5` flow.

## The takeaway

A single `ivpm update` fetched the EDA tool *binaries* and delivered their
*know-how* to the agent in one step. The human declares intent in `ivpm.yaml`;
the agent gets a working toolchain plus the skills to use it.

