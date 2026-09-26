"""Run with pytest, or plain `python3 harness/tests/test_parser.py`."""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from chipharness.parser import parse_orfs  # noqa: E402

FIX = HERE / "fixtures"


def test_mac1_met_timing():
    r = parse_orfs(FIX / "mac1", 2.0)
    assert r["status"] == "pass"
    assert abs(r["wns_ns"] - 0.478) < 1e-3
    assert r["tns_ns"] == 0
    assert r["drc_count"] == 0
    assert abs(r["area_um2"] - 9201.21) < 0.01
    assert abs(r["power_mw"] - 18.0) < 0.1
    assert abs(r["fmax_mhz"] - 657.2) < 0.5


def test_gcd_negative_slack():
    r = parse_orfs(FIX / "gcd", 0.46)
    assert r["status"] == "pass"
    assert r["wns_ns"] < 0 and r["tns_ns"] < 0
    assert r["drc_count"] == 0
    assert abs(r["fmax_mhz"] - 1000 / (0.46 + 0.161583)) < 0.5


def test_failed_run_does_not_raise():
    with tempfile.TemporaryDirectory() as d:
        r = parse_orfs(d, 1.0)
    assert r["status"] == "fail" and r["fmax_mhz"] is None and r["wns_ns"] is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
