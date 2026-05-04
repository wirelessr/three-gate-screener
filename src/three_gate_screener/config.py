"""Project-wide paths and constants."""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
CACHE_DB_PATH = DATA_DIR / "cache.db"
TDCC_SNAPSHOT_DIR = DATA_DIR / "tdcc_snapshots"

TDCC_OPENDATA_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
