"""Context the harness config can expose to agents (reports_read / tools)."""
from __future__ import annotations

import re
from pathlib import Path

from . import config


def _finish_rpt(trial_id: str) -> Path | None:
    hits = sorted((config.RUNS_DIR / trial_id / "orfs").glob("reports/**/6_finish.rpt"))
    return hits[0] if hits else None


def critical_path(trial_id: str | None, max_lines: int = 45) -> str:
    """Worst max-delay timing path from the ORFS finish report (cell-by-cell)."""
    p = _finish_rpt(trial_id) if trial_id else None
    if not p:
        return "(no critical-path report available for this trial)"
    text = p.read_text(errors="replace")
    sections = [sec for sec in re.split(r"^(?=finish report)", text, flags=re.M)
                if sec.startswith("finish report_checks -path_delay max")]
    blocks = [b for sec in sections
              for b in re.findall(r"(Startpoint:.*?slack \((?:MET|VIOLATED)\))", sec, re.S)]
    worst, worst_slack = None, None
    for b in blocks:
        m = re.search(r"(-?[\d.]+)\s+slack \(", b)
        if m and (worst_slack is None or float(m.group(1)) < worst_slack):
            worst, worst_slack = b, float(m.group(1))
    if not worst:
        return "(could not parse critical path)"
    lines = [l.rstrip() for l in worst.splitlines() if l.strip() and not l.rstrip().endswith("(net)") and not set(l.strip()) <= set("-")]
    return "\n".join(lines[:max_lines])


def tier1_log(trial_id: str | None, n: int = 30) -> str:
    p = config.RUNS_DIR / (trial_id or "_") / "tier1" / "yosys.log"
    if not p.exists():
        return "(no tier1 log)"
    lines = p.read_text(errors="replace").splitlines()
    i = max((k for k, l in enumerate(lines) if "Printing statistics" in l), default=len(lines) - n)
    return "\n".join(lines[i:i + n])
