# Self-Evolving Chip-Design Harness

A harness that designs hardware and rewrites itself. A **design loop** has an LLM edit Verilog, gates it on a testbench, and scores it with a real open-source RTL-to-GDS flow (Yosys + OpenROAD, Nangate45). When the score plateaus, an **evolution loop** rewrites the harness itself — which reports it reads, how it diagnoses, which fixes it prefers, its guardrails — and versions the result. MongoDB Atlas holds trials, harness versions, and retrievable lessons.

Built for The Harness Engineering & Model Wrangling Hackathon (MongoDB NYC, Sep 26 2026), Problem Statement 1: Recursive Harnessing. Inspired by [Meta harness makes 10 times better Kimi K3 chip](https://www.luoluo.ai/blog/kimi-k3).

## Layout
| Path | What |
|---|---|
| `contract/` | Interface contract (data shapes) + mock JSON. Read this first. |
| `eda/` | Toolchain setup and feasibility test (`vm_setup.sh`, `eda_feasibility_test.sh`) |
| `harness/` | Python: design and evolution loops, ORFS report parser, Atlas access |
| `dashboard/` | Next.js dashboard (Vercel) |

## Quick start
```bash
cp .env.example .env            # fill in keys
cd harness && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && pytest
```
EDA toolchain: see `eda/` (Docker image `openroad/orfs`; run with `LEC_CHECK=0` on Apple Silicon).

## Prepared before the event (Sep 25)
Planning and environment setup only: the interface contract and mock data (`contract/`), the toolchain setup and feasibility test (`eda/`, including the parser test fixtures copied from its output), and repo skeleton files (README, `.gitignore`, `.env.example`, `harness/pyproject.toml`). No harness code. No commits were made before the event. All harness logic, agent loops, and the dashboard were built on Sep 26.
