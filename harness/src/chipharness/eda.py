"""EDA stages: testbench gate, Tier 1 (Yosys+ABC estimate), Tier 2 (ORFS in Docker).

Each returns the matching `stages.<name>` dict from the contract. None of them raise
on a bad design; failures come back as status "fail" / "error".
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config
from .parser import parse_orfs

BIN = config.OSS_CAD_SUITE / "bin"
LIB = config.ASSETS_DIR / "NangateOpenCellLibrary_typical.lib"
TB = config.DESIGNS_DIR / config.DEFAULT_TOP / "tb.v"
IO_DELAY_NS = 0.1


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        return -1, f"TIMEOUT after {timeout}s\n{e.stdout or ''}"
    except FileNotFoundError as e:
        return -2, f"tool not found: {e}"


def _tail(text: str, n: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


# ---------------------------------------------------------------- testbench gate
def testbench(rtl: Path, work: Path, timeout: int = 120) -> dict:
    t0 = time.time()
    work.mkdir(parents=True, exist_ok=True)
    vvp = work / "tb.vvp"
    rc, out = _run([str(BIN / "iverilog"), "-g2012", "-o", str(vvp), str(TB), str(rtl)], work, timeout)
    if rc == 0:
        rc, out = _run([str(BIN / "vvp"), "-n", str(vvp)], work, timeout)
    (work / "tb.log").write_text(out)
    passed = rc == 0 and "TB PASS" in out
    m = re.search(r"TB FAIL \((\d+) errors", out)
    return {
        "status": "pass" if passed else ("error" if rc < 0 else "fail"),
        "failures": 0 if passed else (int(m.group(1)) if m else None),
        "log_tail": _tail(out),
        "duration_s": round(time.time() - t0, 1),
    }


# ---------------------------------------------------------------- tier 1
def _abc_script(work: Path, ps: int) -> Path:
    """Yosys' default mapping script plus `stime -p`, which prints `Delay = X ps`.
    (A file, because `;` inside `yosys -p` would split the command.)"""
    cmds = ["strash", "&get -n", "&fraig -x", "&put", "scorr", "dc2", "dretime", "strash",
            "&get -n", "&dch -f", f"&nf -D {ps}", "&put", "stime -p"]
    p = work / "abc.script"
    p.write_text("\n".join(cmds) + "\n")
    return p


def tier1(rtl: Path, work: Path, clock_period_ns: float, top: str = config.DEFAULT_TOP,
          timeout: int = 300) -> dict:
    t0 = time.time()
    work.mkdir(parents=True, exist_ok=True)
    ps = int(clock_period_ns * 1000)
    script = (f"read_verilog -sv {rtl}; synth -flatten -top {top}; "
              f"dfflibmap -liberty {LIB}; abc -D {ps} -liberty {LIB} -script {_abc_script(work, ps)}; "
              f"opt_clean; stat -liberty {LIB}")
    rc, out = _run([str(BIN / "yosys"), "-q", "-l", str(work / "yosys.log"), "-p", script], work, timeout)
    log = (work / "yosys.log").read_text() if (work / "yosys.log").exists() else out
    area = re.findall(r"Chip area for (?:top )?module.*?:\s*([\d.]+)", log)
    cells = re.findall(r"Number of cells:\s*(\d+)", log) or re.findall(r"^\s*(\d+)\s+cells\s*$", log, re.M)
    delay = re.findall(r"Delay\s*=\s*([\d.]+)\s*ps", log)
    ok = rc == 0 and bool(area)
    est = round(clock_period_ns - float(delay[-1]) / 1000.0, 3) if delay else None
    return {
        "status": "pass" if ok else ("error" if rc < 0 else "fail"),
        "cells": int(cells[-1]) if cells else None,
        "area_um2": float(area[-1]) if area else None,
        "est_slack_ns": est,
        "duration_s": round(time.time() - t0, 1),
        **({} if ok else {"log_tail": _tail(log)}),
    }


# ---------------------------------------------------------------- tier 2
def _write_orfs_inputs(run_dir: Path, rtl: Path, clock_period_ns: float, top: str) -> None:
    dst = run_dir / f"{top}.v"
    if Path(rtl).resolve() != dst.resolve():
        shutil.copy(rtl, dst)
    (run_dir / "config.mk").write_text(
        f"export DESIGN_NAME      = {top}\n"
        f"export PLATFORM         = nangate45\n"
        f"export VERILOG_FILES    = /work/{top}.v\n"
        f"export SDC_FILE         = /work/constraint.sdc\n"
        f"export CORE_UTILIZATION = 40\n"
        f"export PLACE_DENSITY    = 0.60\n")
    (run_dir / "constraint.sdc").write_text(
        f"create_clock -name core_clock -period {clock_period_ns} [get_ports clk]\n"
        f"set_input_delay  {IO_DELAY_NS} -clock core_clock [delete_from_list [all_inputs] [get_ports clk]]\n"
        f"set_output_delay {IO_DELAY_NS} -clock core_clock [all_outputs]\n")


def tier2(trial_id: str, rtl: Path, clock_period_ns: float, top: str = config.DEFAULT_TOP,
          timeout: int = config.TIER2_TIMEOUT_S) -> dict:
    """Full ORFS flow in Docker. Output lands in runs/<trial_id>/orfs/."""
    t0 = time.time()
    run_dir = (config.RUNS_DIR / trial_id).resolve()
    if (run_dir / "orfs").exists():
        shutil.rmtree(run_dir / "orfs")
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_orfs_inputs(run_dir, rtl, clock_period_ns, top)
    inner = (
        "FLOW=$(ls -d /OpenROAD-flow-scripts/flow); cd $FLOW; "
        "[ -f ../env.sh ] && source ../env.sh >/dev/null 2>&1 || true; "
        "make DESIGN_CONFIG=/work/config.mk LEC_CHECK=0 > /work/make.log 2>&1; rc=$?; "
        "mkdir -p /work/orfs && cp -r reports logs /work/orfs/ 2>/dev/null || true; exit $rc")
    name = f"chr-{trial_id}"
    cmd = ["docker", "run", "--rm", "--name", name, "--platform", config.ORFS_PLATFORM,
           "-v", f"{run_dir}:/work", config.ORFS_IMAGE, "bash", "-c", inner]
    rc, out = _run(cmd, run_dir, timeout)
    if rc == -1:
        subprocess.run(["docker", "kill", name], capture_output=True)
    res = parse_orfs(run_dir / "orfs", clock_period_ns)
    res["duration_s"] = round(time.time() - t0, 1)
    if res["status"] != "pass":
        mk = run_dir / "make.log"
        res["log_tail"] = _tail(mk.read_text(errors="replace") if mk.exists() else out)
        if rc < 0:
            res["status"] = "error"
    return res
