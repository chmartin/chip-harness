# Self-Evolving Chip-Design Harness

A harness that designs hardware and rewrites itself.

- **Design loop.** LLM agents edit the Verilog of a 4×4 INT4 multiply-accumulate array to raise its clock frequency. A randomized, self-checking testbench gates every candidate. A real open-source RTL-to-GDS flow (Yosys + OpenROAD, Nangate45) scores it after full place-and-route.
- **Evolution loop.** When progress plateaus, an evolution agent reads the trial history in MongoDB Atlas, diagnoses why the harness is stuck, and writes a new harness version. It can change the agent's prompt, the reports it sees, the tools it may call, the fix families it samples, how many past lessons it gets and when to skip the expensive flow. Each new version must beat its parent by ≥ 1% or it is retired, and the failure is stored as a lesson.

Built for The Harness Engineering & Model Wrangling Hackathon (MongoDB NYC, Sep 26 2026), **Problem Statement 1: Recursive Harnessing**. Inspired by [Meta harness makes 10 times better Kimi K3 chip](https://www.luoluo.ai/blog/kimi-k3).

## Why it matters
- **The problem.** Timing closure, getting a chip to hit its clock speed, is a slow loop that depends on experts: edit the RTL, re-verify it, synthesize, place and route, read the timing reports, repeat. The real answer only arrives at the end of each expensive pass.
- **The gap.** AI agents can take on that loop, but only as well as the harness around them: what they see, which tools they have, what they remember. Today humans tune that harness by hand.
- **What this does.** The harness tunes itself, against a verifier that can't be gamed. A change survives only if routed silicon says it helped, and every change is diffed, explained and stored in MongoDB Atlas. The result is more reliable agents with no human prompt-engineering, less wasted compute, and a full audit trail.
- **Where else it applies.** Any engineering task with a hard check: compilers, GPU kernels, query plans.

## Results (Sep 26)
### What harness evolution got us (run 2, 1.5 GHz target, fully traced)
| | Seed harness h1 | Evolved harnesses h2–h9 |
|---|---|---|
| Agent designs passing verification | 1 of 8 (13%) | **32 of 32 (100%)** |
| Testbench failures | 7 in a row | **0** |
| Agent time per proposal round | ~10 s | ~1½ min median (agents now test their drafts before submitting) |

- **Self-diagnosis:** the plateau rule flagged h1 as stuck 4 min 19 s into the run. The evolution agent read the failures from Atlas and wrote h2 in 17 s.
- **8 rewrites, 16–24 s each.** All 8 gave the design agents the `tier1_check` self-test tool and the critical-path report.
- **Keep rule:** 0 of 8 rewrites were kept. None beat its parent's best fmax by the 1% bar: h1 found a 1529.8 MHz design in its first iteration, and the best evolved design was 1545.4 MHz (h4, +0.9%). The harness refused to count noise as progress.
- **Compute:** 40 trials in 96 min real time on one laptop (including one kill and resume from the Atlas checkpoints). Place-and-route used 4.6 CPU-hours over 33 runs (about 8.4 CPU-min each). The agents used about 13 min, and model calls cost about $13 on OpenRouter (the whole day, including run 1's $8.23, came to $21.10, all Claude Sonnet 4.5). The testbench gate rejected 7 broken designs in seconds, skipping about 1 CPU-hour of routing.
- **Known limitation:** the evolution agent always branched from h1 and re-derived much the same fix. The next step is to put the retired versions' lessons into its context.
- **Log:** [`logs/run2_1500.log`](logs/run2_1500.log). Every trial, harness version, rationale and diff is on the [dashboard](https://chip-harness.vercel.app/).

### Run 1 (1 GHz target): the rewrite made the chip 46.5% faster
The seed harness failed the testbench 4 of 4 times. Its rewrite h2 was **kept** and raised fmax from 909.5 to 1332.2 MHz, measured after full place-and-route with 0 DRC violations. Details below; data archived in Atlas as the `run1_*` collections, log in [`logs/run1_h1_to_h5.txt`](logs/run1_h1_to_h5.txt).

### Not built (cut for time)
- vector search over lessons (lessons are retrieved with a plain query instead)
- Atlas as a job queue for extra worker machines
- EC2 workers

## What happened in run 1 (1 GHz target)
- **h1** is the deliberately weak seed harness: summary slack only, no tools, no lessons. All 4 of its attempts at pipelining broke the design (e.g. registering the product without delaying the enable), and the testbench rejected every one. That triggered the plateau rule `stuck_rejecting`.
- **The evolution agent** read those failures from Atlas and produced **h2**. h2 gives the design agent a `tier1_check` tool, which runs the testbench and a fast synthesis on its own draft. h2 also shows the agent the critical-path report and weights pipelining fixes higher.
- **h2** pipelined correctly and raised fmax from **909.5 MHz** (baseline) to about **1330 MHz (+46%)**, measured after full place-and-route with 0 DRC violations.
- **The ceiling.** h3 and h4 found no further gain and were **retired**, as the keep/retire rule intends. The cause was the measurement: once a design clears the clock target, OpenROAD stops optimizing, so every good design reads about the same slack. h5 was created, and then the run stopped when the OpenRouter credits ran out. Run 2 raises the target to 1.5 GHz.
- **Cost:** run 1 cost $8.23 in OpenRouter credits (186 model calls, 1.95M tokens).

The live dashboard shows every trial, every harness version, its rationale and its config diff: **https://chip-harness.vercel.app/**

## How it works
```
LangGraph StateGraph (checkpointed in Atlas with MongoDBSaver)

 pick_parent ─► propose ×4 ─► evaluate ×4 ─► check_plateau ─┬─► pick_parent
 (best trial     (Strands       testbench                     │
  so far)         design         Tier 1: Yosys+ABC (seconds)  └─► evolve (Strands evolution agent)
                  agent)         Tier 2: OpenROAD P&R (~5 min)       └─► new harness version ─► pick_parent
```

**Score.** fmax = 1000 / (clock period − signed worst setup slack). A trial is valid only if the testbench passes, the flow completes, and DRC = 0. The scorer, the testbench and the plateau policy are fixed: the evolution loop cannot change them, which prevents reward hacking.

**Plateau policy** (in `harness_settings`). Checked after 4 trials on a version. Any of these triggers evolution:
- 4 rejects in a row
- < 1% gain over the last 3 valid trials
- 8 trials on the version

**What evolves** (`harness_versions.config`):
- `diagnosis_prompt`
- `reports_read`: `summary_slack`, `critical_path`, `tier1_stats`
- `tools`: `tier1_check`
- `fix_families` with sampling weights
- `context.lessons`
- `tier2_policy.max_tier1_regression_ns`
- `guardrails`

## MongoDB Atlas
| Collection | Role |
|---|---|
| `trials` | One document per RTL attempt: every stage's result, the score, the full RTL, the agent's goal and diagnosis, token usage |
| `harness_versions` | One document per harness generation: config, parent, trigger, verdict, rationale, unified diff |
| `lessons` | What helped or hurt, fed back into later prompts when the harness enables it |
| `harness_settings` | The fixed plateau policy |
| LangGraph checkpoints | Loop state after every node; `--resume` continues a killed run from the last node |
| `run1_*` | Run 1 (1 GHz), archived by renaming the collections before the clean run 2 |

The evolution agent queries `trials` through a tool. The dashboard reads Atlas directly with a read-only user.

## Stack
- **Agents:** [Strands Agents](https://strandsagents.com) (AWS) for the design and evolution agents, with models via [OpenRouter](https://openrouter.ai)
- **Orchestration:** [LangGraph](https://langchain-ai.github.io/langgraph/), with checkpoints in Atlas (`langgraph-checkpoint-mongodb`)
- **Tracing:** [LangSmith](https://smith.langchain.com), turned on with `LANGSMITH_TRACING=true`:
  - LangGraph nodes are traced natively.
  - Strands agent spans are exported over OpenTelemetry to LangSmith's OTLP endpoint. This needs `opentelemetry-exporter-otlp-proto-http`.
  - The OpenRouter activity log shows per-call cost.
- **EDA:** iverilog, Yosys + ABC (OSS CAD Suite), OpenROAD-flow-scripts (`openroad/orfs` Docker image), Nangate45
- **Data:** MongoDB Atlas
- **Dashboard:** Next.js on Vercel

## Run it
Prerequisites: Python ≥ 3.10, Docker, [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build) at `eda/eda-test/oss-cad-suite` (or set `OSS_CAD_SUITE`), an Atlas cluster and an OpenRouter key.
```bash
cp .env.example .env                                   # MONGODB_URI, MONGODB_DB, OPENROUTER_API_KEY (+ optional LANGSMITH_*)
python3 -m venv harness/.venv && source harness/.venv/bin/activate
pip install -e "harness[dev]"
python harness/tests/test_parser.py                    # parser tests (no deps)

mkdir -p harness/eda_assets && docker run --rm --platform linux/amd64 openroad/orfs \
  cat /OpenROAD-flow-scripts/flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib \
  > harness/eda_assets/NangateOpenCellLibrary_typical.lib

export CLOCK_MHZ=1500                                  # clock target (default 1000); not checkpointed, so set it on resume too
python -m chipharness.seed                             # plateau policy + seed harness h1
python -m chipharness.smoke                            # baseline trial t-h1-00 (~5 min)
python -m chipharness.preflight                        # read-only checks: Atlas state, keys, Docker, dashboard
python -m chipharness.graph --thread run2       # both loops; the harness decides when to stop
python -m chipharness.graph --thread run2 --resume                  # continue after a kill (Ctrl-C)
```
Run from the repo root. `python -m chipharness.seed --reset` wipes trials, versions and lessons, so rename them first if you want to keep a run.
Dashboard: see `dashboard/README.md`.

On Apple Silicon, ORFS runs under Rosetta with `LEC_CHECK=0`: about 5 min per full flow at 1 GHz, 4 in parallel.

## Layout
| Path | What |
|---|---|
| `harness/src/chipharness/` | `graph.py` (loops), `agents.py` (Strands agents), `pipeline.py` (one trial end to end), `eda.py` (testbench, Tier 1, Tier 2), `parser.py` (ORFS reports), `reports.py` (agent context), `db.py`, `seed.py`, `smoke.py`, `preflight.py` (read-only pre-run checks) |
| `harness/designs/mac_array/` | Baseline RTL and the testbench gate |
| `harness/tests/` | Parser tests on saved ORFS runs |
| `contract/` | Interface contract (data shapes) and mock JSON |
| `dashboard/` | Next.js dashboard |
| `eda/` | Toolchain setup and feasibility test |

## Thanks
- **[OpenRouter](https://openrouter.ai)**: thank you for the hackathon credit boost. Every design and evolution agent call ran on it, with one-key access to models.
- **MongoDB**, for the Atlas Hackathon Sandbox, and **Cerebral Valley**, for hosting.
- **AWS** (Strands Agents), **LangChain** (LangGraph) and **Vercel**, for the tools this runs on.
- The **OpenROAD**, **Yosys** and **OSS CAD Suite** projects, for open-source chip design.

## Prepared before the event (Sep 25)
Planning and environment setup only:
- the interface contract and mock data (`contract/`)
- the toolchain setup and feasibility test (`eda/`, including the parser test fixtures copied from its output)
- repo skeleton files (README, `.gitignore`, `.env.example`, `harness/pyproject.toml`)

No harness code was written and no commits were made before the event. All harness logic, agent loops and the dashboard were built on Sep 26.
