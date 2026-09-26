"""Evaluate one RTL candidate end to end and build the contract `trials` doc."""
from __future__ import annotations

from hashlib import sha1
from pathlib import Path

from . import config, db, eda


def trial_id(version_id: str, iteration: int, slot: int | None = None) -> str:
    base = f"t-{version_id}-{iteration:02d}"
    return base if slot is None else f"{base}-{slot}"


def evaluate(tid: str, rtl_text: str, version: dict, iteration: int, *, parent_trial_id=None,
             goal: str = "", diagnosis: dict | None = None, llm: dict | None = None,
             clock_mhz: int = config.DEFAULT_CLOCK_MHZ, store: bool = True,
             parent_tier1_slack: float | None = None) -> dict:
    period = 1000.0 / clock_mhz
    top = config.DEFAULT_TOP
    run_dir = (config.RUNS_DIR / tid).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    rtl = run_dir / f"{top}.v"
    rtl.write_text(rtl_text)

    doc = {
        "_id": tid, "harness_version": version["_id"], "iteration": iteration,
        "parent_trial_id": parent_trial_id, "created_at": db.now(), "finished_at": None,
        "status": "running", "goal": goal, "clock_target_mhz": clock_mhz,
        "design": {"top": top, "rtl_sha": sha1(rtl_text.encode()).hexdigest()[:7],
                   "rtl_path": f"runs/{tid}/{top}.v", "rtl": rtl_text},
        "stages": {"testbench": None, "tier1": None, "tier2": None},
        "score": {"valid": False, "fmax_mhz": None, "reject_reason": None},
        "diagnosis": diagnosis or {}, "llm": llm or {}, "lesson_ids": [],
    }
    if store:
        db.upsert_trial(doc)

    skipped = {"status": "skipped"}
    tb = eda.testbench(rtl, run_dir / "tb")
    doc["stages"]["testbench"] = tb
    if tb["status"] != "pass":
        doc["stages"]["tier1"] = doc["stages"]["tier2"] = skipped
        doc["score"]["reject_reason"] = "testbench_fail" if tb["status"] == "fail" else "error"
        return _finish(doc, store)

    t1 = eda.tier1(rtl, run_dir / "tier1", period)
    doc["stages"]["tier1"] = t1
    # Tier 1's ABC estimate is pessimistic in absolute terms, so the filter is RELATIVE to the parent.
    max_reg = version["config"].get("tier2_policy", {}).get("max_tier1_regression_ns")
    regressed = (max_reg is not None and parent_tier1_slack is not None and t1["est_slack_ns"] is not None
                 and t1["est_slack_ns"] < parent_tier1_slack - max_reg)
    if t1["status"] != "pass" or regressed:
        doc["stages"]["tier2"] = skipped
        doc["score"]["reject_reason"] = "tier1_skip"
        return _finish(doc, store)

    t2 = eda.tier2(tid, rtl, period)
    doc["stages"]["tier2"] = t2
    if t2["status"] != "pass":
        doc["score"]["reject_reason"] = "tier2_fail" if t2["status"] == "fail" else "error"
    elif t2["drc_count"] != 0:
        doc["score"]["reject_reason"] = "drc"
    else:
        doc["score"] = {"valid": True, "fmax_mhz": t2["fmax_mhz"], "reject_reason": None}
    return _finish(doc, store)


def _finish(doc: dict, store: bool) -> dict:
    doc["status"] = "error" if doc["score"]["reject_reason"] == "error" else "done"
    doc["finished_at"] = db.now()
    if store:
        db.upsert_trial(doc)
        db.refresh_version_stats(doc["harness_version"])
    return doc
