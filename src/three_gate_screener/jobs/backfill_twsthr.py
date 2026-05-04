"""Backfill 3+ years of weekly derived holder metrics from norway.twsthr.info.

Universe: all 4-digit numeric stock_ids present in the latest TDCC snapshot
(~2934 stocks). Runs at 1 req/sec by default, so expect ~50 minutes end-to-end.

Safe to re-run: already-scraped stocks are skipped via fetch_log.
"""
from __future__ import annotations

import re
import sys

from three_gate_screener import cache
from three_gate_screener.sources import twsthr


def load_universe() -> list[str]:
    """4-digit numeric stock_ids from the most recent TDCC snapshot."""
    with cache.connect() as conn:
        ids = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT stock_id
                FROM holders_snapshot
                WHERE date = (SELECT MAX(date) FROM holders_snapshot)
                ORDER BY stock_id
                """
            ).fetchall()
        ]
    return [s for s in ids if re.fullmatch(r"[0-9]{4}", s)]


def run(sleep_s: float = 1.0, limit: int | None = None) -> None:
    universe = load_universe()
    if limit:
        universe = universe[:limit]
    print(f"universe size: {len(universe)}")
    print(f"pacing: {sleep_s}s between requests")
    print(f"ETA: ~{len(universe) * sleep_s / 60:.1f} minutes")

    results = twsthr.scrape_many(universe, sleep_s=sleep_s)

    # summary
    ok = [r for r in results if r.rows > 0]
    empty = [r for r in results if r.rows == 0 and not r.error]
    errors = [r for r in results if r.error]
    print("\n=== summary ===")
    print(f"  succeeded: {len(ok)}")
    print(f"  empty:     {len(empty)}")
    print(f"  errors:    {len(errors)}")
    if errors:
        print("  sample errors:")
        for r in errors[:5]:
            print(f"    {r.stock_id}: {r.error}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    sleep_s = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    run(sleep_s=sleep_s, limit=limit)
