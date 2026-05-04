"""Smoke test: load 10 representative stocks and confirm all three data
sources land in SQLite consistently.

Stocks span TSMC, large-cap banks/telcos, ETF, and a few smaller caps.
"""
from __future__ import annotations

import time

import pandas as pd

from three_gate_screener import cache
from three_gate_screener.sources import finmind


SMOKE_STOCKS = [
    "2330",  # TSMC
    "2317",  # Hon Hai
    "2454",  # MediaTek
    "2412",  # Chunghwa Telecom
    "2882",  # Cathay Financial
    "0050",  # Yuanta Taiwan 50 ETF
    "2603",  # Evergreen Marine
    "3008",  # Largan
    "1101",  # Taiwan Cement
    "2609",  # Yang Ming
]

START_DATE = "2026-04-01"
END_DATE = "2026-04-30"


def run() -> None:
    t0 = time.time()
    results = []
    for sid in SMOKE_STOCKS:
        stats = finmind.load_stock(sid, START_DATE, END_DATE)
        results.append(stats)
        print(
            f"{sid}: prices={stats.prices_rows:3d} inst={stats.institutional_rows:3d} "
            f"elapsed={stats.elapsed_s:.2f}s"
        )

    total_elapsed = time.time() - t0
    print(f"\ntotal elapsed: {total_elapsed:.1f}s")

    # Aggregate verification from SQLite
    with cache.connect() as conn:
        price_count = pd.read_sql_query(
            "SELECT stock_id, COUNT(*) AS n FROM prices GROUP BY stock_id",
            conn,
        )
        inst_count = pd.read_sql_query(
            "SELECT stock_id, COUNT(*) AS n FROM institutional GROUP BY stock_id",
            conn,
        )
        holder_count = pd.read_sql_query(
            f"""
            SELECT stock_id, people
              FROM holders_snapshot
             WHERE level = 17
               AND stock_id IN ({','.join('?' * len(SMOKE_STOCKS))})
            """,
            conn,
            params=SMOKE_STOCKS,
        )

    print("\n=== price rows per stock ===")
    print(price_count.to_string(index=False))
    print("\n=== inst rows per stock ===")
    print(inst_count.to_string(index=False))
    print("\n=== total holders from this week's TDCC snapshot ===")
    print(holder_count.to_string(index=False))


if __name__ == "__main__":
    run()
