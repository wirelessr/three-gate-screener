"""Gate 1: institutional momentum (free-tier proxy for broker-branch analysis).

Original spec: a specific broker branch shows 3+ consecutive net-buy days with
monotonically-increasing volume.

Free-tier substitute: combined Foreign_Investor + Investment_Trust net-buy.
These two are the "smart money" proxies on TW market. Dealers are excluded
because their hedging positions add noise.

User-confirmed decision: monotonically-increasing (strict, no tolerance).

Output: for each (stock_id, signal_date) where the gate fires,
  - streak_length: N of consecutive days qualifying (>=3)
  - avg_daily_net_buy: in shares, not lots
  - streak_start / streak_end dates
"""
from __future__ import annotations

import pandas as pd


SMART_MONEY_NAMES = {"Foreign_Investor", "Investment_Trust"}
MIN_STREAK = 3


def compute_daily_smart_money(inst: pd.DataFrame) -> pd.DataFrame:
    """Aggregate foreign + trust net-buy per (stock_id, date)."""
    smart = inst[inst["name"].isin(SMART_MONEY_NAMES)].copy()
    smart["net"] = smart["buy"] - smart["sell"]
    daily = (
        smart.groupby(["stock_id", "date"], as_index=False)["net"]
        .sum()
        .sort_values(["stock_id", "date"])
        .reset_index(drop=True)
    )
    return daily


def find_signals(daily: pd.DataFrame) -> pd.DataFrame:
    """Walk each stock's daily net series; emit a row at each streak end."""
    signals: list[dict] = []
    for stock_id, grp in daily.groupby("stock_id"):
        grp = grp.reset_index(drop=True)
        streak_start_i = None
        for i, row in grp.iterrows():
            net = row["net"]
            if net > 0:
                if streak_start_i is None:
                    streak_start_i = i
                elif grp.loc[i, "net"] <= grp.loc[i - 1, "net"]:
                    # Broke monotonic-increasing rule; streak restarts HERE
                    # (today is still a buy day, just a new streak).
                    streak_start_i = i
            else:
                streak_start_i = None

            if streak_start_i is not None:
                streak_len = i - streak_start_i + 1
                if streak_len >= MIN_STREAK:
                    window = grp.loc[streak_start_i:i]
                    signals.append(
                        {
                            "stock_id": stock_id,
                            "signal_date": row["date"],
                            "streak_length": streak_len,
                            "streak_start": window["date"].iloc[0],
                            "streak_end": row["date"],
                            "avg_daily_net_buy": window["net"].mean(),
                            "total_net_buy": window["net"].sum(),
                        }
                    )
    return pd.DataFrame(signals)


def run(inst: pd.DataFrame) -> pd.DataFrame:
    daily = compute_daily_smart_money(inst)
    return find_signals(daily)
