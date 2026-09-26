"""Parse an ORFS run folder into the `stages.tier2` dict (contract §4).

wns_ns holds the SIGNED worst setup slack (finish__timing__setup__ws): ORFS clamps
wns to 0 when timing is met, which would hide headroom. fmax = 1000 / (period - ws).
"""
from __future__ import annotations

import json
from pathlib import Path

REPORT = "6_report.json"
ROUTE = "5_2_route.json"


def _find(out_dir: Path, name: str) -> Path | None:
    hits = sorted(out_dir.glob(f"logs/**/{name}"))
    return hits[0] if hits else None


def _load(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def fmax_mhz(clock_period_ns: float, ws_ns: float | None) -> float | None:
    if ws_ns is None:
        return None
    crit = clock_period_ns - ws_ns
    return round(1000.0 / crit, 1) if crit > 0 else None


def parse_orfs(out_dir: str | Path, clock_period_ns: float) -> dict:
    """Never raises on a failed run: returns status "fail" with whatever was found."""
    out_dir = Path(out_dir)
    rep = _load(_find(out_dir, REPORT))
    route = _load(_find(out_dir, ROUTE))

    ws = rep.get("finish__timing__setup__ws")
    power_w = rep.get("finish__power__total")
    drc = route.get("detailedroute__route__drc_errors")

    result = {
        "status": "pass" if (rep and ws is not None) else "fail",
        "wns_ns": round(ws, 4) if ws is not None else None,
        "tns_ns": rep.get("finish__timing__setup__tns"),
        "area_um2": rep.get("finish__design__instance__area__stdcell"),
        "drc_count": int(drc) if drc is not None else None,
        "power_mw": round(power_w * 1000, 3) if power_w is not None else None,
        "duration_s": None,  # set by the caller (eda.tier2)
    }
    result["fmax_mhz"] = fmax_mhz(clock_period_ns, ws) if result["status"] == "pass" else None
    return result


if __name__ == "__main__":
    import sys
    print(json.dumps(parse_orfs(sys.argv[1], float(sys.argv[2])), indent=2))
