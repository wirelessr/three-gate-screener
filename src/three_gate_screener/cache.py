"""SQLite cache for the three-gate pipeline.

Tables:
  prices                - daily OHLCV (FinMind: TaiwanStockPrice)
  institutional         - daily inst. investor buy/sell (FinMind)
  holders_snapshot      - weekly TDCC shareholder distribution (all 17 levels)
  fetch_log             - which (dataset, key, date_range) we've already pulled,
                          so cold restarts don't refetch.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from three_gate_screener.config import CACHE_DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    date TEXT NOT NULL,
    stock_id TEXT NOT NULL,
    trading_volume INTEGER,
    trading_money INTEGER,
    open REAL,
    max REAL,
    min REAL,
    close REAL,
    spread REAL,
    trading_turnover INTEGER,
    PRIMARY KEY (date, stock_id)
);

CREATE TABLE IF NOT EXISTS institutional (
    date TEXT NOT NULL,
    stock_id TEXT NOT NULL,
    name TEXT NOT NULL,       -- e.g. Foreign_Investor, Investment_Trust, Dealer_self
    buy INTEGER,
    sell INTEGER,
    PRIMARY KEY (date, stock_id, name)
);

CREATE TABLE IF NOT EXISTS holders_snapshot (
    date TEXT NOT NULL,       -- snapshot date from TDCC (weekly)
    stock_id TEXT NOT NULL,
    level INTEGER NOT NULL,   -- 1..15 dispersion level, 16 adjustment, 17 total
    people INTEGER,
    shares INTEGER,
    percent REAL,
    PRIMARY KEY (date, stock_id, level)
);

-- Weekly derived metrics from norway.twsthr.info (3+ years history).
-- Schema is different from the raw 17-level TDCC data; kept separate.
CREATE TABLE IF NOT EXISTS holders_derived (
    date TEXT NOT NULL,             -- YYYY-MM-DD
    stock_id TEXT NOT NULL,
    total_lots INTEGER,             -- 集保總張數
    total_holders INTEGER,          -- 總股東人數 (matches level-17 people in raw)
    avg_lots_per_holder REAL,       -- 平均張數/人
    whale_400_lots INTEGER,         -- >400張大股東持有張數
    whale_400_percent REAL,         -- >400張大股東持有百分比
    whale_400_count INTEGER,        -- >400張大股東人數
    holders_400_600 INTEGER,        -- 400~600張人數
    holders_600_800 INTEGER,        -- 600~800張人數
    holders_800_1000 INTEGER,       -- 800~1000張人數
    holders_over_1000 INTEGER,      -- >1000張人數
    whale_1000_percent REAL,        -- >1000張大股東持有百分比
    close_price REAL,               -- 收盤價
    PRIMARY KEY (date, stock_id)
);

CREATE INDEX IF NOT EXISTS idx_holders_derived_stock
    ON holders_derived(stock_id, date);

CREATE TABLE IF NOT EXISTS fetch_log (
    dataset TEXT NOT NULL,
    key TEXT NOT NULL,        -- e.g. stock_id; "*" for whole-market pulls
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    rows INTEGER,
    PRIMARY KEY (dataset, key, start_date, end_date)
);

CREATE INDEX IF NOT EXISTS idx_prices_stock
    ON prices(stock_id, date);
CREATE INDEX IF NOT EXISTS idx_inst_stock
    ON institutional(stock_id, date);
CREATE INDEX IF NOT EXISTS idx_holders_stock
    ON holders_snapshot(stock_id, date);
"""


@contextmanager
def connect(db_path: Path = CACHE_DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path = CACHE_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_df(
    conn: sqlite3.Connection,
    table: str,
    df: pd.DataFrame,
) -> int:
    """INSERT OR REPLACE into table from df. Returns row count written."""
    if df is None or df.empty:
        return 0
    cols = list(df.columns)
    placeholders = ",".join(["?"] * len(cols))
    col_list = ",".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
    conn.executemany(sql, df.itertuples(index=False, name=None))
    return len(df)


def log_fetch(
    conn: sqlite3.Connection,
    dataset: str,
    key: str,
    start_date: str,
    end_date: str,
    rows: int,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO fetch_log
            (dataset, key, start_date, end_date, fetched_at, rows)
        VALUES (?, ?, ?, ?, datetime('now'), ?)
        """,
        (dataset, key, start_date, end_date, rows),
    )


def is_fetched(
    conn: sqlite3.Connection,
    dataset: str,
    key: str,
    start_date: str,
    end_date: str,
) -> bool:
    cur = conn.execute(
        """
        SELECT 1 FROM fetch_log
        WHERE dataset = ? AND key = ?
          AND start_date <= ? AND end_date >= ?
        LIMIT 1
        """,
        (dataset, key, start_date, end_date),
    )
    return cur.fetchone() is not None


if __name__ == "__main__":
    init_db()
    print(f"initialized cache at {CACHE_DB_PATH}")
    with connect() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        print("tables:", [t[0] for t in tables])
