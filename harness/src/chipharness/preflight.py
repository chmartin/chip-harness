"""Pre-recording checklist for a demo run. Read-only; makes no model calls.

    CLOCK_MHZ=1500 python -m chipharness.preflight [--thread run2]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request

from . import config, db

THREAD = sys.argv[sys.argv.index("--thread") + 1] if "--thread" in sys.argv else "run2"
fails = 0


def check(ok: bool, msg: str, hint: str = "") -> None:
    global fails
    fails += 0 if ok else 1
    print(("  OK   " if ok else "  FAIL ") + msg + ("" if ok or not hint else f"\n         -> {hint}"))


def main() -> None:
    print(f"Preflight for thread '{THREAD}' at CLOCK_MHZ={config.DEFAULT_CLOCK_MHZ}\n")

    print("Environment")
    for k in ("MONGODB_URI", "MONGODB_DB", "OPENROUTER_API_KEY", "LANGSMITH_API_KEY"):
        check(bool(os.environ.get(k)), f"{k} set", "fill it in .env")
    check(os.environ.get("LANGSMITH_TRACING", "").lower() == "true", "LANGSMITH_TRACING=true")
    check(config.DEFAULT_CLOCK_MHZ == 1500, f"CLOCK_MHZ is {config.DEFAULT_CLOCK_MHZ}", "export CLOCK_MHZ=1500")
    check(os.path.basename(os.getcwd()) != "harness", "running from the repo root (logs/ is there)", "cd ..")

    print("\nAtlas")
    d = db.db()
    names = set(d.list_collection_names())
    check({"run1_trials", "run1_harness_versions"} <= names, "run 1 archived (run1_* collections)")
    vs = list(db.versions().find({}, {"_id": 1, "status": 1}))
    check([v["_id"] for v in vs] == ["h1"], f"harness_versions = {[v['_id'] for v in vs]}",
          "expected only h1: run `python -m chipharness.seed` (after archiving)")
    act = db.active_version()
    check(bool(act) and act["_id"] == "h1", f"active version = {act and act['_id']}")
    base = db.trials().find_one({"_id": "t-h1-00"})
    if not base:
        check(False, "baseline t-h1-00 exists", "run `python -m chipharness.smoke` (~5 min)")
    else:
        sc = base.get("score", {})
        check(base.get("status") == "done" and sc.get("valid"), f"baseline done and valid: fmax={sc.get('fmax_mhz')} MHz")
        check(base.get("clock_target_mhz") == config.DEFAULT_CLOCK_MHZ,
              f"baseline clock target = {base.get('clock_target_mhz')} MHz", "re-run smoke with CLOCK_MHZ=1500")
    n = db.trials().count_documents({"_id": {"$ne": "t-h1-00"}})
    check(n == 0, f"no other trials ({n})", "leftover trials would be reused as cached")
    check(d["lessons"].count_documents({}) == 0, f"no lessons ({d['lessons'].count_documents({})})")
    ck = d["checkpoints"].count_documents({"thread_id": THREAD}) if "checkpoints" in names else 0
    check(ck == 0, f"no checkpoints yet for thread '{THREAD}' ({ck})", "use a new --thread name")
    pol = db.settings().find_one({"_id": "plateau_policy"}) or {}
    check(pol.get("min_trials") == 4, f"plateau policy min_trials={pol.get('min_trials')}, cap={pol.get('budget_cap')}")

    print("\nOpenRouter")
    try:
        # /key reports this key's own spending cap; the account balance needs a
        # management key, so read it off openrouter.ai/credits instead.
        req = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                     headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"})
        k = json.load(urllib.request.urlopen(req, timeout=15))["data"]
        rem = k.get("limit_remaining")
        check(rem is None or rem >= 8, "key spend cap: " + ("none" if rem is None else f"${rem:.2f} left"),
              "raise the key's limit at openrouter.ai/keys")
        print("         account balance: read it off openrouter.ai/credits and write it down (run cost)")
    except Exception as e:  # noqa: BLE001
        check(False, f"key lookup failed: {e}", "check the key at openrouter.ai/keys")

    print("\nLangSmith")
    try:
        from langsmith import Client
        list(Client().list_projects(limit=1))
        check(True, "API key works")
    except Exception as e:  # noqa: BLE001
        check(False, f"LangSmith: {e}")
    try:
        import opentelemetry.exporter.otlp.proto.http  # noqa: F401
        check(True, "OTLP exporter installed (Strands spans)")
    except ImportError:
        check(False, "OTLP exporter installed", "pip install opentelemetry-exporter-otlp-proto-http")

    print("\nTools")
    check((config.OSS_CAD_SUITE / "bin" / "iverilog").exists(), "OSS CAD Suite found")
    if shutil.which("docker"):
        img = subprocess.run(["docker", "images", "-q", "openroad/orfs"], capture_output=True, text=True).stdout.strip()
        check(bool(img), "openroad/orfs image present", "is Docker Desktop running?")
        ps = subprocess.run(["docker", "ps", "-q", "--filter", "ancestor=openroad/orfs"],
                            capture_output=True, text=True).stdout.split()
        check(not ps, f"no leftover ORFS containers ({len(ps)})",
              "docker ps -q --filter ancestor=openroad/orfs | xargs docker kill")
    else:
        check(False, "docker on PATH")
    try:
        urllib.request.urlopen("https://chip-harness.vercel.app", timeout=15)
        check(True, "dashboard reachable")
    except Exception as e:  # noqa: BLE001
        check(False, f"dashboard: {e}")

    print("\n" + ("READY. Start recording, then launch." if not fails else f"{fails} check(s) failed - fix before recording."))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
