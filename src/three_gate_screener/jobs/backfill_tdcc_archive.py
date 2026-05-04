"""Ingest historical TDCC snapshots from the local archive submodule.

The archive lives at `tdcc-archive/` as a git submodule pointing to
`github.com/wirelessr/tdcc-opendata-archive`. That repo contains:
  snapshots/<YYYY>/<YYYY-MM-DD>.csv  (raw TDCC opendata CSV)

This job walks the archive, parses each CSV via sources.tdcc.parse(), and
upserts into the holders_snapshot table. Fully offline — no network calls.

Prerequisite: you've cloned the three-gate-screener repo with `--recurse-submodules`
(or run `git submodule update --init`).

To pull newer snapshots committed to the archive repo:
  git submodule update --remote tdcc-archive
"""
from __future__ import annotations

import sys
from pathlib import Path

from three_gate_screener.config import TDCC_ARCHIVE_DIR
from three_gate_screener.sources import tdcc


def list_archive_csvs() -> list[Path]:
    if not TDCC_ARCHIVE_DIR.exists():
        print(
            f"ERROR: archive not found at {TDCC_ARCHIVE_DIR}.\n"
            "Run `git submodule update --init --recursive` to fetch it.",
            file=sys.stderr,
        )
        sys.exit(1)
    return sorted(TDCC_ARCHIVE_DIR.rglob("*.csv"))


def run() -> None:
    paths = list_archive_csvs()
    print(f"found {len(paths)} snapshots in archive")

    for path in paths:
        snap = tdcc.archive_from_disk(path)
        print(
            f"  {snap.snapshot_date}: "
            f"{len(snap.df):,} rows, "
            f"{snap.df['stock_id'].nunique():,} stocks"
        )


if __name__ == "__main__":
    run()
