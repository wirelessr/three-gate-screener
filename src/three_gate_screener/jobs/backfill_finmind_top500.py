"""Backfill FinMind prices + institutional for top-500 stocks over 2023-2026.

Universe: top 500 by total_lots (集保總張數) in the most recent twsthr snapshot.
total_lots is a stock-size proxy (before we have daily volume for everyone).

Window: 2023-01-01 to 2026-04-30 — matches the twsthr holders_derived window.
Benchmark 0050 is force-included.
"""
from __future__ import annotations

import time

import pandas as pd

from three_gate_screener import cache
from three_gate_screener.sources import finmind


START_DATE = "2023-01-01"
END_DATE = "2026-04-30"
N_STOCKS = 500
BENCHMARK = "0050"


def pick_universe() -> list[str]:
    """Top-N by total_lots on latest twsthr snapshot, plus benchmark."""
    with cache.connect() as conn:
        latest = conn.execute(
            "SELECT MAX(date) FROM holders_derived"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT stock_id, total_lots
              FROM holders_derived
             WHERE date = ? AND total_lots IS NOT NULL
             ORDER BY total_lots DESC
             LIMIT ?
            """,
            (latest, N_STOCKS),
        ).fetchall()
    ids = [r[0] for r in rows]
    if BENCHMARK not in ids:
        ids.append(BENCHMARK)
    return ids


def run() -> None:
    universe = pick_universe()
    print(f"universe: {len(universe)} stocks (top-{N_STOCKS} + benchmark {BENCHMARK})")
    print(f"window: {START_DATE} ~ {END_DATE}")
    t0 = time.time()
    total_new_prices = 0
    total_new_inst = 0

    for n, sid in enumerate(universe, start=1):
        stats = finmind.load_stock(sid, START_DATE, END_DATE)
        total_new_prices += stats.prices_rows
        total_new_inst += stats.institutional_rows
        # Light pacing to stay below 600/hr.
        time.sleep(0.1)
        if n % 20 == 0 or n == len(universe):
            elapsed = time.time() - t0
            eta = elapsed / n * (len(universe) - n)
            print(
                f"[{n:>3d}/{len(universe)}] last={sid} "
                f"elapsed={elapsed:.0f}s ETA={eta:.0f}s "
                f"new_prices={total_new_prices:,} new_inst={total_new_inst:,}"
            )

    print(f"\ntotal elapsed: {time.time() - t0:.1f}s")

    with cache.connect() as conn:
        p = conn.execute(
            "SELECT COUNT(*) FROM prices WHERE date BETWEEN ? AND ?",
            (START_DATE, END_DATE),
        ).fetchone()[0]
        i = conn.execute(
            "SELECT COUNT(*) FROM institutional WHERE date BETWEEN ? AND ?",
            (START_DATE, END_DATE),
        ).fetchone()[0]
        ps = conn.execute(
            "SELECT COUNT(DISTINCT stock_id) FROM prices WHERE date BETWEEN ? AND ?",
            (START_DATE, END_DATE),
        ).fetchone()[0]
    print(f"cache in window: prices={p:,} ({ps} stocks) inst={i:,}")


if __name__ == "__main__":
    run()
