# Interface Contract — v1.2

Collections: `harness_versions`, `trials`, `lessons`, `harness_settings`. Shared shapes for everyone. Code against this, not against each other. Changes need Chris's OK; bump the version at the top.

**Conventions**
- Two loops: the **design loop** runs every trial (agent edits RTL → testbench → Tier 1 → Tier 2 → score); the **evolution loop** runs on a plateau and rewrites the harness config as a new version.
- Languages: Python for harness, agent loops and parser; Next.js (TypeScript) for the Vercel dashboard.
- Database: `chipharness`. IDs are strings. Timestamps are ISO-8601 UTC strings (stored as BSON Date in Atlas).
- Units are in field names: `_mhz`, `_ns`, `_um2`, `_mw`, `_s`, `_usd`.
- Missing / not-run values are `null`, never `0`.
- Mock data: `mock/harness_versions.json`, `mock/trials.json`, `mock/lessons.json`, `mock/harness_settings.json`.

---

## 1. `harness_versions` — one doc per harness generation (the evolution loop)

```json
{
  "_id": "h2",
  "version": 2,
  "parent_id": "h1",                  // null for the seed
  "created_at": "2026-09-26T15:12:00Z",
  "created_by": "evolution-loop",    // "seed" | "evolution-loop" | "human"
  "status": "active",                 // "active" (running its trials) | "kept" (beat parent) | "retired" (did not)
  "trigger": {                        // why this version exists; null for the seed
    "reason": "no_gain",              // "no_gain" | "stuck_rejecting" | "budget_cap" | "manual"
    "window_trial_ids": ["t-h1-04", "t-h1-05", "t-h1-06", "t-h1-07", "t-h1-08"],
    "parent_best_fmax_mhz": 468.0
  },
  "verdict": {                        // null while status is "active"
    "best_fmax_mhz": 719.0,
    "gain_over_parent_pct": 53.6,
    "kept": true                      // gain_over_parent_pct >= plateau_policy.keep_min_gain_pct
  },
  "rationale": "Harness only read summary slack; it kept proposing knob tweaks. Now reads critical-path report and prefers structural fixes.",
  "config": {
    "reports_read": ["summary_slack", "critical_path"],
    "diagnosis_prompt": "…full prompt text…",
    "fix_families": [                 // priors the design loop samples from
      {"name": "pipeline_register", "weight": 0.4},
      {"name": "accumulator_width", "weight": 0.3},
      {"name": "flow_knob",         "weight": 0.1}
    ],
    "guardrails": ["testbench must pass", "no removal of MAC units"],
    "tier2_policy": {"min_tier1_slack_ns": -0.5},  // skip full flow if Tier 1 is worse than this
    "models": {"design": "openrouter/<model>", "evolution": "openrouter/<model>"}
  },
  "plateau_policy": {…},             // copy of harness_settings "plateau_policy" at creation
  "diff": "--- h1\n+++ h2\n…unified diff of config…",
  "stats": {                          // updated as trials finish
    "trials": 8,
    "valid_trials": 6,
    "best_fmax_mhz": 719.0,
    "best_trial_id": "t-h2-04"
  }
}
```

### Plateau policy (fixed; the evolution loop cannot change it)

Stored in `harness_settings` (one doc, `_id: "plateau_policy"`) and copied into each version at creation for traceability.

```json
{
  "_id": "plateau_policy",
  "min_trials": 6,              // no trigger is checked before this many trials on a version
  "no_gain": {"window_valid_trials": 5, "min_gain_pct": 1.0},   // best valid fmax gained < 1% over the last 5 valid trials
  "stuck_rejecting": {"consecutive_rejects": 5},                // 5 rejected trials in a row
  "budget_cap": {"max_trials": 15},                             // hard stop per version
  "keep_min_gain_pct": 1.0      // a new version is kept only if its best beats the parent's best by >= 1%
}
```
Trigger order when several hold at once: `stuck_rejecting`, then `no_gain`, then `budget_cap`. A retired version's parent becomes active again, and the failed attempt is written to `lessons` with `outcome: "hurt"`.

## 2. `trials` — one doc per RTL attempt (the design loop)

```json
{
  "_id": "t-h2-04",
  "harness_version": "h2",
  "iteration": 4,
  "parent_trial_id": "t-h2-03",       // trial whose RTL this edited; null for a fresh start
  "created_at": "2026-09-26T15:40:00Z",
  "finished_at": "2026-09-26T15:44:10Z",
  "status": "done",                   // "queued" | "running" | "done" | "error"
  "goal": "Register the accumulator row select to break the sel→out path…",
  "clock_target_mhz": 1000,
  "design": {
    "top": "mac_array",
    "rtl_sha": "a1b2c3d",
    "rtl_path": "trials/t-h2-04/mac_array.v"
  },
  "stages": {
    "testbench": {"status": "pass", "failures": 0, "log_tail": "TB PASS", "duration_s": 1.2},
    "tier1":     {"status": "pass", "cells": 1843, "area_um2": 2410.5, "est_slack_ns": -0.12, "duration_s": 2.0},
    "tier2":     {"status": "pass", "wns_ns": -0.39, "tns_ns": -12.4, "area_um2": 2680.1,
                  "drc_count": 0, "power_mw": 4.1, "duration_s": 213}
  },
  "score": {
    "valid": true,                    // testbench pass AND tier2 pass AND drc_count == 0
    "fmax_mhz": 719.4,                // 1000 / (clock_period_ns - wns_ns); null if not valid
    "reject_reason": null             // "testbench_fail" | "tier1_skip" | "tier2_fail" | "drc" | "error"
  },
  "diagnosis": {
    "critical_path": "acc_fast → adder → acc_out",
    "bottleneck": "24-bit accumulate adder",
    "fix_family": "accumulator_width",
    "notes": "…"
  },
  "llm": {"model": "openrouter/<model>", "tokens_in": 18230, "tokens_out": 2410,
          "cost_usd": 0.07, "trace_url": "https://smith.langchain.com/…"},
  "lesson_ids": ["l-011"]
}
```

Stage `status` values: `"pass" | "fail" | "skipped" | "error"`. A skipped stage keeps its other fields `null`.

## 3. `lessons` — retrievable memory

```json
{
  "_id": "l-011",
  "created_at": "2026-09-26T15:44:30Z",
  "trial_id": "t-h2-04",
  "harness_version": "h2",
  "text": "Hierarchical 12→24-bit fold shortens the accumulate critical path; watch overflow at K≥32.",
  "fix_family": "accumulator_width",
  "outcome": "helped",                // "helped" | "hurt" | "neutral"
  "delta_fmax_mhz": 251.4,
  "tags": ["timing", "accumulator"]
}
```
Vector index `lessons_text` on `text` (Atlas Automated Embeddings). Query: nearest lessons to the current diagnosis text, filtered by `outcome != "neutral"`.

---

## 4. Report parser (Python)

```python
def parse_orfs(out_dir: str, clock_period_ns: float) -> dict:
    """out_dir = the copied ORFS run folder (contains reports/ and logs/).
    Returns the `stages.tier2` dict above plus "fmax_mhz".
    Never raises on a failed run: returns status "fail" with whatever fields were found, others null."""
```
Test against saved runs in `harness/tests/fixtures/` (`gcd`, `mac1`).

## 5. Dashboard reads (Vercel)

- **Evolution chart:** `trials` where `score.valid`, sorted by `created_at`; x = order, y = `score.fmax_mhz`, color = `harness_version`; vertical markers at each `harness_versions.created_at` where `created_by == "evolution-loop"`. Invalid trials shown as grey ticks at the bottom.
- **Harness diff:** `harness_versions` by `version`; show `diff`, `rationale`, `trigger`.
- **Trial table:** latest 50 `trials`: id, harness, iteration, status, fmax, wns, drc, testbench, fix_family, reject_reason.
