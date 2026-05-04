"""Pilot FinMind backfill for 2021 Q3 window.

Goal: pull prices + institutional-investors data for the top 50 stocks (by
2021-07-02 holder count, a rough proxy for liquidity/market-cap) across the
window 2021-06-01 to 2021-10-15. The date range brackets the 13-week lisa
TDCC window with enough pre-run history for gate1/gate2 lookbacks.
"""
from __future__ import annotations

import re
import time

from three_gate_screener import cache
from three_gate_screener.sources import finmind


START_DATE = "2021-06-01"
END_DATE = "2022-03-31"
N_STOCKS = 50


def pick_universe() -> list[str]:
    """Top-N stocks by holder count in the first lisa week (proxy for liquidity)."""
    with cache.connect() as conn:
        rows = conn.execute(
            """
            SELECT stock_id, people
              FROM holders_snapshot
             WHERE date = '2021-07-02' AND level = 17
             ORDER BY people DESC
            """
        ).fetchall()
    four_digit_only = [(s, p) for s, p in rows if re.fullmatch(r"[0-9]{4}", s)]
    return [s for s, _ in four_digit_only[:N_STOCKS]]


def run() -> None:
    universe = pick_universe()
    print(f"universe: {len(universe)} stocks")
    print(f"window: {START_DATE} ~ {END_DATE}")
    t0 = time.time()

    for n, sid in enumerate(universe, start=1):
        stats = finmind.load_stock(sid, START_DATE, END_DATE)
        print(
            f"[{n:>2d}/{len(universe)}] {sid}: "
            f"prices={stats.prices_rows:>4d} inst={stats.institutional_rows:>4d} "
            f"elapsed={stats.elapsed_s:.1f}s"
        )

    print(f"\ntotal elapsed: {time.time() - t0:.1f}s")

    # quick counts
    with cache.connect() as conn:
        p = conn.execute(
            f"""
            SELECT COUNT(*) FROM prices
            WHERE stock_id IN ({','.join('?' * len(universe))})
              AND date BETWEEN ? AND ?
            """,
            (*universe, START_DATE, END_DATE),
        ).fetchone()[0]
        i = conn.execute(
            f"""
            SELECT COUNT(*) FROM institutional
            WHERE stock_id IN ({','.join('?' * len(universe))})
              AND date BETWEEN ? AND ?
            """,
            (*universe, START_DATE, END_DATE),
        ).fetchone()[0]
    print(f"cache: prices={p:,} inst={i:,}")


if __name__ == "__main__":
    run()
