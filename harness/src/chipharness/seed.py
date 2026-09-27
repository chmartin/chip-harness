"""Seed Atlas: plateau policy + deliberately limited harness h1. Idempotent.

    python -m chipharness.seed            # insert if missing
    python -m chipharness.seed --reset    # wipe trials/versions/lessons first (demo reset)
"""
from __future__ import annotations

import os
import sys

from . import db

# Tightened for the 4:30pm deadline (build plan: min 4 / cap 8).
PLATEAU_POLICY = {
    "_id": "plateau_policy",
    "min_trials": 4,
    "no_gain": {"window_valid_trials": 3, "min_gain_pct": 1.0},
    "stuck_rejecting": {"consecutive_rejects": 4},
    "budget_cap": {"max_trials": 8},
    "keep_min_gain_pct": 1.0,
    # the harness decides when to stop evolving (see graph.STOP_DEFAULTS)
    "stop": {"max_consecutive_retired": 3, "novelty_max_similarity": 0.8, "novelty_attempts": 3,
             "max_versions": 6, "max_iterations": 8},
}

DESIGN_MODEL = os.environ.get("DESIGN_MODEL", "anthropic/claude-sonnet-4.5")
EVOLUTION_MODEL = os.environ.get("EVOLUTION_MODEL", DESIGN_MODEL)

# h1 is intentionally weak: sees only summary slack, favors low-value fixes.
H1_CONFIG = {
    "reports_read": ["summary_slack"],
    "tools": [],
    "context": {"lessons": 0},
    "diagnosis_prompt": (
        "You are optimizing a Verilog MAC array for maximum clock frequency. "
        "You are shown the worst setup slack of the parent design. "
        "Propose one small change to the RTL that might improve timing."),
    "fix_families": [
        {"name": "micro_optimization", "weight": 0.5},
        {"name": "logic_restructure", "weight": 0.4},
        {"name": "pipeline_register", "weight": 0.1},
    ],
    "guardrails": ["testbench must pass", "keep module ports and parameters unchanged",
                   "no removal of MAC units"],
    "tier2_policy": {"max_tier1_regression_ns": None},
    "models": {"design": DESIGN_MODEL, "evolution": EVOLUTION_MODEL},
}


def seed(reset: bool = False) -> None:
    d = db.db()
    if reset:
        for c in ("trials", "harness_versions", "lessons"):
            d[c].delete_many({})
    db.settings().replace_one({"_id": "plateau_policy"}, PLATEAU_POLICY, upsert=True)
    if db.get_version("h1"):  # keep trials; refresh the seed config
        db.versions().update_one({"_id": "h1"}, {"$set": {"config": H1_CONFIG, "plateau_policy": PLATEAU_POLICY}})
    else:
        db.versions().insert_one({
            "_id": "h1", "version": 1, "parent_id": None, "created_at": db.now(),
            "created_by": "seed", "status": "active", "trigger": None, "verdict": None,
            "rationale": "Seed harness: summary slack only.", "config": H1_CONFIG,
            "plateau_policy": PLATEAU_POLICY, "diff": "",
            "stats": {"trials": 0, "valid_trials": 0, "best_fmax_mhz": None, "best_trial_id": None},
        })
    db.ensure_indexes()
    print("seeded:", [v["_id"] for v in db.versions().find({}, {"_id": 1})])


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
