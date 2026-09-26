# EDA toolchain

- `vm_setup.sh` — one-time setup on an x86 Ubuntu VM (Docker + ORFS image).
- `eda_feasibility_test.sh` — timing test: testbench, Yosys synth, ORFS gcd + 4x4 INT4 MAC, parallel throughput. `PAR=<n>` sets parallel runs. Output goes to `eda/eda-test/` (gitignored).

Measured on M1 Max (Rosetta): 213 s per MAC full flow, ~46 full runs/hour at 4 in parallel.
