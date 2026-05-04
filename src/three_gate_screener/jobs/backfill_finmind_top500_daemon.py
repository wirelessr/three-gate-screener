"""Resumable backfill daemon: sleep through rate limits, keep going.

Calls finmind.load_stock in a loop. On 'reach the upper limit' error,
sleeps for SLEEP_MINUTES and retries. Progress is safe because load_stock
uses the fetch_log cache — already-loaded stocks skip the network.

Run in background; output lands in data/finmind_backfill.log for
offline inspection.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from three_gate_screener import cache
from three_gate_screener.config import DATA_DIR
from three_gate_screener.sources import finmind


START_DATE = "2023-01-01"
END_DATE = "2026-04-30"
N_STOCKS = 500
BENCHMARK = "0050"
SLEEP_MINUTES = 30
MAX_RETRIES_PER_STOCK = 20

LOG_PATH = DATA_DIR / "finmind_backfill.log"
RATE_LIMIT_RE = re.compile(r"Requests reach the upper limit", re.IGNORECASE)


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def pick_universe() -> list[str]:
    with cache.connect() as conn:
        latest = conn.execute(
            "SELECT MAX(date) FROM holders_derived"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT stock_id FROM holders_derived
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


def already_complete(stock_id: str) -> bool:
    """A stock is 'done' when both datasets are logged for the full window."""
    with cache.connect() as conn:
        price = cache.is_fetched(
            conn, finmind.DATASET_PRICE, stock_id, START_DATE, END_DATE
        )
        inst = cache.is_fetched(
            conn, finmind.DATASET_INST, stock_id, START_DATE, END_DATE
        )
    return price and inst


def run() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    universe = pick_universe()
    log(f"universe: {len(universe)} stocks, window {START_DATE} ~ {END_DATE}")

    pending = [s for s in universe if not already_complete(s)]
    done = len(universe) - len(pending)
    log(f"initial state: {done} done, {len(pending)} pending")

    consecutive_rate_limits = 0
    t0 = time.time()

    for sid in pending:
        retries = 0
        while True:
            try:
                stats = finmind.load_stock(sid, START_DATE, END_DATE)
                done += 1
                consecutive_rate_limits = 0
                if done % 25 == 0 or done == len(universe):
                    elapsed = time.time() - t0
                    log(
                        f"[{done}/{len(universe)}] last={sid} "
                        f"p={stats.prices_rows} i={stats.institutional_rows} "
                        f"elapsed={elapsed:.0f}s"
                    )
                break
            except Exception as exc:
                msg = str(exc)
                if RATE_LIMIT_RE.search(msg):
                    consecutive_rate_limits += 1
                    log(
                        f"rate limit on {sid} (retry #{retries + 1}, "
                        f"streak {consecutive_rate_limits}); sleeping {SLEEP_MINUTES}m"
                    )
                    time.sleep(SLEEP_MINUTES * 60)
                    retries += 1
                    if retries >= MAX_RETRIES_PER_STOCK:
                        log(f"GIVE UP on {sid} after {retries} retries")
                        break
                else:
                    log(f"non-rate-limit error on {sid}: {msg[:200]}")
                    break
        else:
            continue

    total_done = sum(1 for s in universe if already_complete(s))
    log(f"=== DONE. {total_done}/{len(universe)} complete ===")


if __name__ == "__main__":
    run()
