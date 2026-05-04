"""TDCC shareholder-dispersion loader.

Source: https://opendata.tdcc.com.tw/getOD.ashx?id=1-5
Format: UTF-8 BOM CSV
Update cadence: weekly (Fridays). Only the LATEST snapshot is served — we
MUST pull and archive before it gets overwritten.

Columns (raw, Chinese):
  資料日期          e.g. 20260430 (YYYYMMDD)
  證券代號          e.g. 2330
  持股分級          1..15, integer level
  人數              people count
  股數              shares held
  占集保庫存數比例% percent of total
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

from three_gate_screener import cache
from three_gate_screener.config import TDCC_OPENDATA_URL, TDCC_SNAPSHOT_DIR


TDCC_COLUMN_MAP = {
    "資料日期": "date",
    "證券代號": "stock_id",
    "持股分級": "level",
    "人數": "people",
    "股數": "shares",
    "占集保庫存數比例%": "percent",
}


@dataclass(frozen=True)
class TDCCSnapshot:
    snapshot_date: str  # YYYY-MM-DD
    df: pd.DataFrame
    raw_csv_path: Path


def download(timeout: int = 60) -> bytes:
    # TDCC returns a redirect loop without a browser-like UA.
    headers = {"User-Agent": "Mozilla/5.0 (three-gate-screener)"}
    resp = requests.get(TDCC_OPENDATA_URL, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def parse(csv_bytes: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(csv_bytes), encoding="utf-8-sig", dtype=str)
    df = df.rename(columns=TDCC_COLUMN_MAP)
    # TDCC pads stock_id to 6 chars with trailing spaces; strip so "2330  " -> "2330".
    df["stock_id"] = df["stock_id"].str.strip()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    df["level"] = df["level"].astype(int)
    df["people"] = df["people"].astype(int)
    df["shares"] = df["shares"].astype("int64")
    df["percent"] = df["percent"].astype(float)
    return df[["date", "stock_id", "level", "people", "shares", "percent"]]


def snapshot(
    out_dir: Path = TDCC_SNAPSHOT_DIR,
    write_cache: bool = True,
) -> TDCCSnapshot:
    """Download TDCC CSV, parse, archive raw file, upsert into SQLite."""
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = download()
    df = parse(raw)

    # Every row in a weekly CSV shares the same 資料日期; use it as filename.
    snapshot_date = df["date"].iloc[0]
    raw_path = out_dir / f"tdcc_{snapshot_date}.csv"
    raw_path.write_bytes(raw)

    if write_cache:
        cache.init_db()
        with cache.connect() as conn:
            n = cache.upsert_df(conn, "holders_snapshot", df)
            cache.log_fetch(
                conn,
                dataset="TDCC_HOLDERS",
                key="*",
                start_date=snapshot_date,
                end_date=snapshot_date,
                rows=n,
            )
    return TDCCSnapshot(snapshot_date=snapshot_date, df=df, raw_csv_path=raw_path)


def archive_from_disk(raw_path: Path) -> TDCCSnapshot:
    """Re-parse a previously archived CSV and upsert into cache.

    Useful when the raw file exists but the cache hasn't been populated yet.
    """
    raw = raw_path.read_bytes()
    df = parse(raw)
    snapshot_date = df["date"].iloc[0]
    cache.init_db()
    with cache.connect() as conn:
        n = cache.upsert_df(conn, "holders_snapshot", df)
        cache.log_fetch(
            conn,
            dataset="TDCC_HOLDERS",
            key="*",
            start_date=snapshot_date,
            end_date=snapshot_date,
            rows=n,
        )
    return TDCCSnapshot(snapshot_date=snapshot_date, df=df, raw_csv_path=raw_path)


def main() -> None:
    snap = snapshot()
    df = snap.df
    print(f"snapshot date: {snap.snapshot_date}")
    print(f"raw archived:  {snap.raw_csv_path}")
    print(f"rows:          {len(df):,}")
    print(f"unique stocks: {df['stock_id'].nunique():,}")
    print(f"levels:        {sorted(df['level'].unique())}")
    total_row = df[(df["stock_id"] == "2330") & (df["level"] == 17)]
    if not total_row.empty:
        total = int(total_row["people"].iloc[0])
        print(f"sanity: 2330 total holders = {total:,}")


if __name__ == "__main__":
    main()
