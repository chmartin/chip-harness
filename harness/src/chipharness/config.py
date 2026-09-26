"""Paths and environment. Everything overridable by env var."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # parser tests run without deps
    load_dotenv = None

REPO_ROOT = Path(os.environ.get("CHIPHARNESS_ROOT", Path(__file__).resolve().parents[3]))
if load_dotenv:
    load_dotenv(REPO_ROOT / ".env")

HARNESS_DIR = REPO_ROOT / "harness"
DESIGNS_DIR = HARNESS_DIR / "designs"
ASSETS_DIR = HARNESS_DIR / "eda_assets"
RUNS_DIR = Path(os.environ.get("CHIPHARNESS_RUNS", REPO_ROOT / "runs"))
CONTRACT_DIR = REPO_ROOT / "contract"

OSS_CAD_SUITE = Path(os.environ.get("OSS_CAD_SUITE", REPO_ROOT / "eda" / "eda-test" / "oss-cad-suite"))
ORFS_IMAGE = os.environ.get("ORFS_IMAGE", "openroad/orfs:latest")
ORFS_PLATFORM = os.environ.get("ORFS_PLATFORM", "linux/amd64")
TIER2_TIMEOUT_S = int(os.environ.get("TIER2_TIMEOUT_S", "1200"))

MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGODB_DB = os.environ.get("MONGODB_DB", "chipharness")

DEFAULT_TOP = "mac_array"
DEFAULT_CLOCK_MHZ = 1000
