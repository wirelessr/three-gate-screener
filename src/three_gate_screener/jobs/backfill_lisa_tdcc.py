"""Backfill historical TDCC data from lisa4930007/ownership_distribution_access_db.

Source: https://github.com/lisa4930007/ownership_distribution_access_db
Coverage: 2021-07-02 to 2021-09-24 (13 weekly snapshots), full market.
Format: UTF-8 BOM CSV, same schema as TDCC official endpoint.

This gives us a small but 17-level-complete historical window that our
existing gate3 logic can consume without any schema adapter.
"""
from __future__ import annotations

import time

import requests

from three_gate_screener.config import TDCC_SNAPSHOT_DIR
from three_gate_screener.sources import tdcc


REPO_API = (
    "https://api.github.com/repos/"
    "lisa4930007/ownership_distribution_access_db/contents/csv"
)
RAW_BASE = (
    "https://raw.githubusercontent.com/"
    "lisa4930007/ownership_distribution_access_db/master/csv"
)


def list_available_csvs() -> list[str]:
    resp = requests.get(REPO_API, headers={"User-Agent": "three-gate"}, timeout=30)
    resp.raise_for_status()
    return sorted(e["name"] for e in resp.json() if e["name"].endswith(".csv"))


def download_csv(name: str) -> bytes:
    resp = requests.get(
        f"{RAW_BASE}/{name}",
        headers={"User-Agent": "three-gate"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def run() -> None:
    TDCC_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    filenames = list_available_csvs()
    print(f"found {len(filenames)} weekly CSVs in lisa repo")

    for fname in filenames:
        # Filename format: YYYYMMDD.csv
        snapshot_date = f"{fname[0:4]}-{fname[4:6]}-{fname[6:8]}"
        archive_path = TDCC_SNAPSHOT_DIR / f"tdcc_{snapshot_date}.csv"

        if archive_path.exists():
            print(f"  {snapshot_date}: already archived, skip")
            continue

        t0 = time.time()
        raw = download_csv(fname)
        archive_path.write_bytes(raw)
        snap = tdcc.archive_from_disk(archive_path)
        print(
            f"  {snap.snapshot_date}: "
            f"{len(snap.df):,} rows, "
            f"{snap.df['stock_id'].nunique():,} stocks, "
            f"{time.time() - t0:.1f}s"
        )


if __name__ == "__main__":
    run()
