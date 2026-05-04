"""FinMind loader with SQLite write-through cache.

Free tier constraints:
  - Single-stock queries only (stock_id REQUIRED, no full-market mode).
  - 600 requests/hour with token.

Strategy: fetch a (stock_id, date_range), upsert into SQLite, log the fetch.
Subsequent reads come from SQLite; we refetch only when asked to extend.

Inst. investors `name` values observed (2026-04-24):
  Foreign_Investor, Foreign_Dealer_Self, Investment_Trust,
  Dealer_self, Dealer_Hedging
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd
from dotenv import load_dotenv
from FinMind.data import DataLoader

from three_gate_screener import cache


DATASET_PRICE = "TaiwanStockPrice"
DATASET_INST = "TaiwanStockInstitutionalInvestorsBuySell"


@lru_cache(maxsize=1)
def _loader() -> DataLoader:
    load_dotenv()
    token = os.getenv("FINMIND_TOKEN", "")
    if not token:
        raise RuntimeError("FINMIND_TOKEN not set")
    return DataLoader(token=token)


# ---------------------------------------------------------------------------
# raw fetch (goes over network)
# ---------------------------------------------------------------------------

def fetch_prices(stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
    dl = _loader()
    df = dl.taiwan_stock_daily(
        stock_id=stock_id, start_date=start_date, end_date=end_date
    )
    if df is None or df.empty:
        return pd.DataFrame()
    # Normalize column names to our schema.
    return df.rename(
        columns={
            "Trading_Volume": "trading_volume",
            "Trading_money": "trading_money",
            "Trading_turnover": "trading_turnover",
        }
    )[
        [
            "date",
            "stock_id",
            "trading_volume",
            "trading_money",
            "open",
            "max",
            "min",
            "close",
            "spread",
            "trading_turnover",
        ]
    ]


def fetch_institutional(
    stock_id: str, start_date: str, end_date: str
) -> pd.DataFrame:
    dl = _loader()
    df = dl.taiwan_stock_institutional_investors(
        stock_id=stock_id, start_date=start_date, end_date=end_date
    )
    if df is None or df.empty:
        return pd.DataFrame()
    return df[["date", "stock_id", "name", "buy", "sell"]]


# ---------------------------------------------------------------------------
# cache-aware load (single entry point for callers)
# ---------------------------------------------------------------------------

@dataclass
class LoadStats:
    stock_id: str
    prices_rows: int
    institutional_rows: int
    elapsed_s: float


def load_stock(
    stock_id: str,
    start_date: str,
    end_date: str,
    force_refresh: bool = False,
) -> LoadStats:
    """Fetch prices + institutional for a stock, upsert into cache, return stats."""
    cache.init_db()
    t0 = time.time()
    prices_rows = 0
    inst_rows = 0

    with cache.connect() as conn:
        if force_refresh or not cache.is_fetched(
            conn, DATASET_PRICE, stock_id, start_date, end_date
        ):
            price_df = fetch_prices(stock_id, start_date, end_date)
            prices_rows = cache.upsert_df(conn, "prices", price_df)
            cache.log_fetch(
                conn, DATASET_PRICE, stock_id, start_date, end_date, prices_rows
            )

        if force_refresh or not cache.is_fetched(
            conn, DATASET_INST, stock_id, start_date, end_date
        ):
            inst_df = fetch_institutional(stock_id, start_date, end_date)
            inst_rows = cache.upsert_df(conn, "institutional", inst_df)
            cache.log_fetch(
                conn, DATASET_INST, stock_id, start_date, end_date, inst_rows
            )

    return LoadStats(
        stock_id=stock_id,
        prices_rows=prices_rows,
        institutional_rows=inst_rows,
        elapsed_s=time.time() - t0,
    )


def read_prices(stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
    with cache.connect() as conn:
        return pd.read_sql_query(
            """
            SELECT * FROM prices
            WHERE stock_id = ? AND date BETWEEN ? AND ?
            ORDER BY date
            """,
            conn,
            params=(stock_id, start_date, end_date),
        )


def read_institutional(
    stock_id: str, start_date: str, end_date: str
) -> pd.DataFrame:
    with cache.connect() as conn:
        return pd.read_sql_query(
            """
            SELECT * FROM institutional
            WHERE stock_id = ? AND date BETWEEN ? AND ?
            ORDER BY date, name
            """,
            conn,
            params=(stock_id, start_date, end_date),
        )
