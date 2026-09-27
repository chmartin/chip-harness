"""Both loops as one LangGraph StateGraph, checkpointed in MongoDB Atlas.

    pick_parent -> propose (x4, Strands) -> evaluate (x4: testbench, Tier 1, Tier 2)
      -> check_plateau -> [evolve (Strands) -> pick_parent] | pick_parent | END

    python -m chipharness.graph --thread demo1               # runs until the harness decides to stop
    python -m chipharness.graph --thread demo1 --resume      # continue after a kill
    python -m chipharness.graph --thread demo1 --max-iterations 20   # optional safety ceiling

The harness decides when to stop evolving (see STOP_DEFAULTS / plateau_policy.stop):
  - converged: N consecutive harness versions retired without beating their base
  - search_exhausted: the evolution agent could not propose a change set that differs from
    everything already tried from this base (checked by change_signature similarity)
  - agent_stop: after at least one failed attempt, the evolution agent judges further evolution futile
  - version_cap / max_iterations: safety ceilings only, not the normal exit
"""
from __future__ import annotations

import argparse
import difflib
import json
import random
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, TypedDict

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.graph import END, StateGraph

from . import agents, config, db, pipeline

SLOTS = 4

# Overridable per deployment via plateau_policy.stop in Atlas.
STOP_DEFAULTS = {
    "max_consecutive_retired": 3,    # evolution attempts in a row that fail to beat their base
    "novelty_max_similarity": 0.8,   # Jaccard over change signatures; >= this counts as a repeat
    "novelty_attempts": 3,           # proposals per evolve step before declaring the search exhausted
    "max_versions": 6,               # safety ceiling (run 2 reached h9)
    "max_iterations": 8,             # safety ceiling on design iterations, x4 trials each (run 2 used 10)
}


def stop_policy() -> dict:
    return {**STOP_DEFAULTS, **((db.plateau_policy() or {}).get("stop") or {})}


class S(TypedDict, total=False):
    version_id: str
    iteration: int
    iters_left: int
    parent_trial_id: str
    proposals: list
    trial_ids: list
    plateau: Optional[str]
    stop: Optional[str]


def log(msg: str) -> None:
    print(f"[{db.now():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- nodes
def pick_parent(s: S) -> S:
    v = db.active_version()
    if v is None:  # a previous run stopped after retiring the last attempt: continue from the best kept
        v = db.versions().find_one({"status": "kept"}, sort=[("verdict.best_fmax_mhz", -1), ("version", -1)])
        db.versions().update_one({"_id": v["_id"]}, {"$set": {"status": "active"}})
    if v["_id"] != s.get("version_id"):
        s["version_id"], s["iteration"] = v["_id"], 1
    else:
        done = [t["iteration"] for t in db.version_trials(v["_id"])]
        s["iteration"] = max(done + [s.get("iteration", 1) - 1]) + 1
    best = db.best_trial() or db.get_trial("t-h1-00")
    log(f"{v['_id']} iter {s['iteration']}: parent {best['_id']} fmax={best['score'].get('fmax_mhz')}")
    return {"version_id": v["_id"], "iteration": s["iteration"], "parent_trial_id": best["_id"]}


def propose(s: S) -> S:
    v = db.get_version(s["version_id"])
    parent = db.get_trial(s["parent_trial_id"])
    fams = v["config"]["fix_families"]
    rng = random.Random(f"{v['_id']}-{s['iteration']}")
    picks = rng.choices([f["name"] for f in fams], weights=[f["weight"] for f in fams], k=SLOTS)

    def one(slot_fam):
        slot, fam = slot_fam
        tid = pipeline.trial_id(v["_id"], s["iteration"], slot)
        if (t := db.get_trial(tid)) and t["status"] == "done":
            return {"trial_id": tid, "cached": True}
        p = agents.propose(v, parent, fam, slot)
        return {"trial_id": tid, "fix_family": fam, **p}

    with ThreadPoolExecutor(SLOTS) as ex:
        props = list(ex.map(one, enumerate(picks)))
    for p in props:
        log(f"  {p['trial_id']}: {p.get('fix_family')} - {p.get('goal') or p.get('error') or 'cached'}"[:160])
    if all("error" in p and "rtl" not in p for p in props):
        # every LLM call failed (credits, key, provider down): stop instead of burning iterations.
        # Resume later with --resume; errored trials are retried.
        raise SystemExit(f"all {len(props)} proposals failed - stopping. First error: {props[0]['error'][:300]}")
    return {"proposals": props}


def evaluate(s: S) -> S:
    v = db.get_version(s["version_id"])
    parent = db.get_trial(s["parent_trial_id"])
    pf = parent["score"].get("fmax_mhz")

    def one(p):
        if p.get("cached"):
            return db.get_trial(p["trial_id"])
        diag = {"fix_family": p.get("fix_family"), "bottleneck": p.get("bottleneck"), "notes": ""}
        if "rtl" not in p:
            doc = {"_id": p["trial_id"], "harness_version": v["_id"], "iteration": s["iteration"],
                   "parent_trial_id": parent["_id"], "created_at": db.now(), "finished_at": db.now(),
                   "status": "error", "goal": "", "clock_target_mhz": config.DEFAULT_CLOCK_MHZ,
                   "design": None, "stages": {"testbench": None, "tier1": None, "tier2": None},
                   "score": {"valid": False, "fmax_mhz": None, "reject_reason": "error"},
                   "diagnosis": {**diag, "notes": p.get("error", "")}, "llm": p.get("llm") or {},
                   "lesson_ids": []}
            db.upsert_trial(doc)
            return doc
        return pipeline.evaluate(p["trial_id"], p["rtl"], v, s["iteration"], parent_trial_id=parent["_id"],
                                 goal=p.get("goal", ""), diagnosis=diag, llm=p.get("llm"),
                                 parent_tier1_slack=((parent["stages"].get("tier1") or {}).get("est_slack_ns")))

    with ThreadPoolExecutor(SLOTS) as ex:
        docs = list(ex.map(one, s["proposals"]))

    for d in docs:
        sc = d["score"]
        log(f"  {d['_id']}: valid={sc['valid']} fmax={sc['fmax_mhz']} reject={sc['reject_reason']}")
        _lesson(d, v, pf)
    db.refresh_version_stats(v["_id"])
    return {"trial_ids": [d["_id"] for d in docs], "proposals": [],
            "iters_left": s["iters_left"] - 1}


def _lesson(d: dict, v: dict, parent_fmax: float | None) -> None:
    if d["score"]["reject_reason"] == "error" and not d.get("design"):
        return
    f = d["score"]["fmax_mhz"]
    delta = round(f - parent_fmax, 1) if (f and parent_fmax) else None
    if not d["score"]["valid"]:
        outcome, res = "hurt", f"rejected ({d['score']['reject_reason']})"
    elif delta is not None and delta > 0.5:
        outcome, res = "helped", f"fmax {f} MHz (+{delta})"
    elif delta is not None and delta < -0.5:
        outcome, res = "hurt", f"fmax {f} MHz ({delta})"
    else:
        outcome, res = "neutral", f"fmax {f} MHz (no change)"
    tb = (d["stages"].get("testbench") or {})
    extra = f" TB: {tb.get('log_tail', '')[-200:]}" if tb.get("status") == "fail" else ""
    lid = f"l-{d['_id']}"
    db.add_lesson({"_id": lid, "created_at": db.now(), "trial_id": d["_id"], "harness_version": v["_id"],
                   "text": f"{d['diagnosis'].get('fix_family')}: {d.get('goal', '')} -> {res}.{extra}"[:800],
                   "fix_family": d["diagnosis"].get("fix_family"), "outcome": outcome,
                   "delta_fmax_mhz": delta, "tags": []})
    db.trials().update_one({"_id": d["_id"]}, {"$set": {"lesson_ids": [lid]}})


def check_plateau(s: S) -> S:
    v = db.get_version(s["version_id"])
    pol = db.plateau_policy()
    ts = [t for t in db.version_trials(v["_id"])
          if t.get("iteration", 0) > 0 and t.get("design")]  # skip baseline + LLM/infra errors
    reason = None
    if len(ts) >= pol["min_trials"]:
        k = pol["stuck_rejecting"]["consecutive_rejects"]
        w = pol["no_gain"]["window_valid_trials"]
        valid = [t["score"]["fmax_mhz"] for t in ts if t["score"]["valid"]]
        start = (v.get("trigger") or {}).get("parent_best_fmax_mhz") or _fmax("t-h1-00") or 0
        before = max([start] + valid[:-w]) if len(valid) >= w else None
        if len(ts) >= k and all(not t["score"]["valid"] for t in ts[-k:]):
            reason = "stuck_rejecting"
        elif before is not None and max(valid) < before * (1 + pol["no_gain"]["min_gain_pct"] / 100):
            reason = "no_gain"
        elif len(ts) >= pol["budget_cap"]["max_trials"]:
            reason = "budget_cap"
    log(f"{v['_id']}: {len(ts)} trials, best={v['stats'].get('best_fmax_mhz')} plateau={reason}")
    return {"plateau": reason}


def _fmax(tid):
    t = db.get_trial(tid)
    return t and t["score"].get("fmax_mhz")


def evolve(s: S) -> S:
    cur = db.get_version(s["version_id"])
    pol = db.plateau_policy()
    cur_best = cur["stats"].get("best_fmax_mhz") or 0
    base = cur
    if cur.get("parent_id"):
        parent_best = cur["trigger"]["parent_best_fmax_mhz"] or 0
        gain = round(100 * (cur_best - parent_best) / parent_best, 2) if parent_best else 0.0
        kept = gain >= pol["keep_min_gain_pct"]
        db.versions().update_one({"_id": cur["_id"]}, {"$set": {
            "status": "kept" if kept else "retired",
            "verdict": {"best_fmax_mhz": cur_best, "gain_over_parent_pct": gain, "kept": kept}}})
        log(f"{cur['_id']} verdict: {'KEPT' if kept else 'RETIRED'} ({gain:+.1f}% vs parent)")
        if not kept:
            db.add_lesson({"_id": f"l-{cur['_id']}-retired", "created_at": db.now(), "trial_id": None,
                           "harness_version": cur["_id"], "outcome": "hurt", "fix_family": "harness",
                           "delta_fmax_mhz": round(cur_best - parent_best, 1), "tags": ["harness"],
                           "text": (f"Harness change {cur.get('change_signature') or '(unrecorded)'} did not help "
                                   f"({gain:+.1f}%): {cur.get('rationale', '')}")[:800]})
            base = db.get_version(cur["parent_id"])
    else:
        db.versions().update_one({"_id": cur["_id"]}, {"$set": {"status": "kept"}})

    # ---- decide whether evolving further is worth it (the harness decides, not the user)
    sp = stop_policy()
    recent = list(db.versions().find({"status": {"$in": ["kept", "retired"]}, "parent_id": {"$ne": None}},
                                     {"status": 1}).sort("version", -1))
    streak = next((i for i, x in enumerate(recent) if x["status"] != "retired"), len(recent))
    if streak >= sp["max_consecutive_retired"]:
        return _stop("converged", f"{streak} consecutive harness versions failed to beat their base")
    if db.versions().count_documents({}) >= sp["max_versions"]:
        return _stop("version_cap", f"{sp['max_versions']} harness versions (safety ceiling)")

    # ---- learning path: everything already tried from this base, so the agent does not repeat it
    tried = []
    for sib in db.versions().find({"parent_id": base["_id"], "status": {"$in": ["kept", "retired"]}}).sort("version", 1):
        tried.append({"id": sib["_id"], "verdict": sib["status"].upper(),
                      "gain_pct": (sib.get("verdict") or {}).get("gain_over_parent_pct") or 0.0,
                      "signature": sib.get("change_signature") or agents.change_signature(sib["config"], base["config"]),
                      "rationale": sib.get("rationale", "")})

    base_best = (db.best_trial() or {}).get("score", {}).get("fmax_mhz")
    log(f"evolving from {base['_id']} (reason {s['plateau']}; {len(tried)} earlier attempts from this base) ...")
    feedback, out, llm_calls = None, None, []
    for attempt in range(1, sp["novelty_attempts"] + 1):
        cand = agents.evolve(base, s["plateau"], tried=tried, feedback=feedback)
        llm_calls.append(cand["llm"])
        if cand.get("stop"):
            if tried:
                return _stop("agent_stop", cand["rationale"])
            feedback = "stopping is only allowed after at least one attempt from this base has been evaluated."
            continue
        sig = cand["signature"]
        dup = max(tried, key=lambda t: agents.similarity(sig, t["signature"]), default=None)
        sim = agents.similarity(sig, dup["signature"]) if dup else 0.0
        if not sig or sig == ["guardrails"]:
            feedback = "it makes no structural change to the base config."
        elif sim >= sp["novelty_max_similarity"]:
            feedback = (f"its changes {sig} are {sim:.0%} the same as {dup['id']} ({dup['signature']}), "
                        f"which was {dup['verdict']} at {dup['gain_pct']:+.1f}%. Try a different direction.")
        else:
            out = cand
            break
        log(f"  proposal {attempt}/{sp['novelty_attempts']} rejected: {feedback}"[:300])
    if out is None:
        return _stop("search_exhausted", f"no novel harness change after {sp['novelty_attempts']} proposals")

    n = max(x["version"] for x in db.versions().find({}, {"version": 1})) + 1
    vid = f"h{n}"
    a = json.dumps(base["config"], indent=2).splitlines()
    b = json.dumps(out["config"], indent=2).splitlines()
    db.versions().insert_one({
        "_id": vid, "version": n, "parent_id": base["_id"], "created_at": db.now(),
        "created_by": "evolution-loop", "status": "active",
        "trigger": {"reason": s["plateau"], "window_trial_ids": [t["_id"] for t in db.version_trials(cur["_id"])][-5:],
                    "parent_best_fmax_mhz": base_best},
        "verdict": None, "rationale": out["rationale"], "config": out["config"], "plateau_policy": pol,
        "change_signature": out["signature"], "novelty_attempts": len(llm_calls),
        "diff": "\n".join(difflib.unified_diff(a, b, base["_id"], vid, lineterm="")),
        "stats": {"trials": 0, "valid_trials": 0, "best_fmax_mhz": None, "best_trial_id": None},
        "llm": out["llm"]})
    log(f"created {vid} {out['signature']}: {out['rationale']}")
    return {"version_id": vid, "iteration": 1, "plateau": None}


def _stop(reason: str, detail: str) -> S:
    best = db.best_trial()
    db.settings().replace_one({"_id": "run_status"}, {
        "_id": "run_status", "stopped_at": db.now(), "reason": reason, "detail": detail,
        "best_trial_id": best and best["_id"], "best_fmax_mhz": best and best["score"]["fmax_mhz"],
        "versions": db.versions().count_documents({})}, upsert=True)
    log(f"STOP ({reason}): {detail}")
    return {"stop": reason, "plateau": None}


def route(s: S) -> str:
    if s.get("iters_left", 0) <= 0:
        log("STOP (max_iterations): safety ceiling reached")
        return END
    return "evolve" if s.get("plateau") else "pick_parent"


def after_evolve(s: S) -> str:
    return END if s.get("stop") else "pick_parent"


def build(checkpointer=None):
    g = StateGraph(S)
    for name, fn in [("pick_parent", pick_parent), ("propose", propose), ("evaluate", evaluate),
                     ("check_plateau", check_plateau), ("evolve", evolve)]:
        g.add_node(name, fn)
    g.set_entry_point("pick_parent")
    g.add_edge("pick_parent", "propose")
    g.add_edge("propose", "evaluate")
    g.add_edge("evaluate", "check_plateau")
    g.add_conditional_edges("check_plateau", route, {"evolve": "evolve", "pick_parent": "pick_parent", END: END})
    g.add_conditional_edges("evolve", after_evolve, {"pick_parent": "pick_parent", END: END})
    return g.compile(checkpointer=checkpointer)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-iterations", "--iterations", dest="iterations", type=int, default=None,
                    help="optional safety ceiling on design iterations (x4 trials each); "
                         "by default the harness decides when to stop")
    ap.add_argument("--thread", default="main")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()
    import os
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is empty - set it in .env (repo root)")
    # drop trials that failed before producing a design, so they are retried
    n = db.trials().delete_many({"status": "error", "design": None}).deleted_count
    if n:
        log(f"removed {n} errored trials (no design) for retry")
    saver = MongoDBSaver(db.client(), db_name=config.MONGODB_DB)
    graph = build(saver)
    cfg = {"configurable": {"thread_id": a.thread}, "recursion_limit": 1000}
    ceiling = a.iterations or stop_policy()["max_iterations"]
    graph.invoke(None if a.resume else {"iters_left": ceiling}, cfg)
    best = db.best_trial()
    log(f"done. best: {best and best['_id']} {best and best['score']['fmax_mhz']} MHz")


if __name__ == "__main__":
    main()
