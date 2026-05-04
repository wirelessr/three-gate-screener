"""Verify FinMind data access on the Register (free) tier.

Goal: figure out whether the bulk parquet signed-URL path (get_object)
can replace the tier-gated "no stock_id" full-market mode.

Probes:
  1. single-stock sanity check — taiwan_stock_daily(stock_id='2330')
  2. bulk parquet for broker-branch data — get_object('TaiwanStockTradingDailyReport', T)
  3. bulk parquet for daily prices — get_object('TaiwanStockPrice', T)
  4. bulk parquet for shareholder dist. — get_object('TaiwanStockHoldingSharesPer', T)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from FinMind.data import DataLoader


def load_token() -> str:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    token = os.getenv("FINMIND_TOKEN", "")
    if not token:
        print("ERROR: FINMIND_TOKEN not set in .env", file=sys.stderr)
        sys.exit(1)
    return token


def summarize(df, label: str) -> None:
    print(f"\n=== {label} ===")
    if df is None or len(df) == 0:
        print("  (empty)")
        return
    print(f"  rows: {len(df):,}")
    print(f"  columns: {list(df.columns)}")
    if "stock_id" in df.columns:
        print(f"  unique stock_id: {df['stock_id'].nunique():,}")
    if "securities_trader_id" in df.columns:
        print(f"  unique securities_trader_id: {df['securities_trader_id'].nunique():,}")
    if "date" in df.columns:
        print(f"  date range: {df['date'].min()} ~ {df['date'].max()}")
    print("  head:")
    print(df.head(3).to_string(index=False))


def probe_single_stock(dl: DataLoader, date: str) -> None:
    t0 = time.time()
    try:
        df = dl.taiwan_stock_daily(stock_id="2330", start_date=date, end_date=date)
    except Exception as exc:
        print(f"\n=== PROBE 1: single stock taiwan_stock_daily(2330, {date}) ===")
        print(f"  ERROR: {exc}")
        return
    dt = time.time() - t0
    summarize(df, f"PROBE 1: single stock taiwan_stock_daily(2330, {date})")
    print(f"  elapsed: {dt:.1f}s")


def probe_bulk_parquet(dl: DataLoader, dataset: str, date: str) -> None:
    label = f"get_object({dataset}, {date})"
    t0 = time.time()
    try:
        df = dl.get_object(dataset=dataset, date=date, timeout=60)
    except Exception as exc:
        print(f"\n=== {label} ===")
        print(f"  ERROR: {exc}")
        print("  VERDICT: bulk parquet NOT available for this dataset/date")
        return
    dt = time.time() - t0
    summarize(df, label)
    print(f"  elapsed: {dt:.1f}s")
    if df is not None and len(df) > 0:
        print("  VERDICT: bulk parquet WORKS — free-tier bypass likely")
    else:
        print("  VERDICT: bulk parquet returned empty — likely blocked or no file")


def main() -> None:
    token = load_token()
    dl = DataLoader(token=token)

    probe_date = sys.argv[1] if len(sys.argv) > 1 else "2026-04-24"
    print(f"Probe date: {probe_date}")

    probe_single_stock(dl, probe_date)

    # Route C2 feasibility on FinMind free tier.
    # Three datasets we need for the degraded strategy:
    #   - TaiwanStockInstitutionalInvestorsBuySell (Gate 1 proxy)
    #   - TaiwanStockPrice                         (Gate 2)
    #   - TaiwanStockHoldingSharesPer              (Gate 3)
    print("\n\n--- Route C2 feasibility on Register tier ---")

    print("\n=== C2-A: institutional investors (2330, last 10 days) ===")
    try:
        df = dl.taiwan_stock_institutional_investors(
            stock_id="2330", start_date="2026-04-15", end_date=probe_date
        )
        summarize(df, "inst. investors single-stock")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    print("\n=== C2-B: institutional investors full market (date only) ===")
    try:
        df = dl.taiwan_stock_institutional_investors(
            start_date=probe_date, end_date=probe_date
        )
        summarize(df, "inst. investors whole market one day")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    print("\n=== C2-C: holding shares per (2330, last 30 days) ===")
    try:
        df = dl.taiwan_stock_holding_shares_per(
            stock_id="2330", start_date="2026-04-01", end_date=probe_date
        )
        summarize(df, "holding_shares_per single-stock")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    print("\n=== C2-D: holding shares per full market (date only) ===")
    try:
        df = dl.taiwan_stock_holding_shares_per(
            start_date=probe_date, end_date=probe_date
        )
        summarize(df, "holding_shares_per whole market one day")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    print("\n=== C2-E: daily price full market (date only) ===")
    try:
        df = dl.taiwan_stock_daily(start_date=probe_date, end_date=probe_date)
        summarize(df, "taiwan_stock_daily whole market one day")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # can register-tier query single-stock broker-branch data?
    print("\n=== PROBE 5: single-stock broker branch (2330) ===")
    t0 = time.time()
    try:
        df = dl.taiwan_stock_trading_daily_report(stock_id="2330", date=probe_date)
        dt = time.time() - t0
        summarize(df, f"taiwan_stock_trading_daily_report(stock_id=2330, date={probe_date})")
        print(f"  elapsed: {dt:.1f}s")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # and the aggregated variant over a date range?
    print("\n=== PROBE 6: single-stock secid_agg (2330, last 10 days) ===")
    t0 = time.time()
    try:
        df = dl.taiwan_stock_trading_daily_report_secid_agg(
            stock_id="2330", start_date="2026-04-15", end_date=probe_date
        )
        dt = time.time() - t0
        summarize(df, "secid_agg(2330, 2026-04-15 ~ 2026-04-24)")
        print(f"  elapsed: {dt:.1f}s")
    except Exception as exc:
        print(f"  ERROR: {exc}")


if __name__ == "__main__":
    main()
