"""Phase 0 exit: run the baseline MAC through every stage and store it in Atlas.

    python -m chipharness.smoke              # full run incl. Tier 2 (~3-5 min)
"""
from __future__ import annotations

import json

from . import config, db, pipeline


def main() -> None:
    version = db.get_version("h1")
    if not version:
        raise SystemExit("run `python -m chipharness.seed` first")
    rtl = (config.DESIGNS_DIR / config.DEFAULT_TOP / f"{config.DEFAULT_TOP}.v").read_text()
    tid = pipeline.trial_id("h1", 0)
    doc = pipeline.evaluate(tid, rtl, version, 0, goal="Baseline (unmodified RTL)")
    doc.pop("design", None)
    print(json.dumps({k: doc[k] for k in ("_id", "status", "stages", "score")}, indent=2))
    print("stored:", db.get_trial(tid) is not None)


if __name__ == "__main__":
    main()
