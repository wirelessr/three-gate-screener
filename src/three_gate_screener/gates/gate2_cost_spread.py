"""Gate 2: cost-spread filter.

Definition per design doc:
  Weighted avg cost over the consecutive-buy window = sum(buy * price) / sum(buy)
  where buy = daily net-buy (shares), price = daily close.
  Then (current_price - cost) / cost must be <= 5%.

The "price" in the weighted-avg formula is approximated by the daily close,
because per-trade prices aren't available on the free tier (that's broker-
branch data). Close is the standard proxy the original spec uses for Step 3.

User-confirmed decision: the window is strictly the current streak (not a
rolling N-day lookback).

Input: gate1 signals + price data.
Output: same columns as gate1 + [weighted_cost, current_price, spread_pct]
        where spread_pct is (current - cost) / cost.
"""
from __future__ import annotations

import pandas as pd


MAX_SPREAD_PCT = 0.05


def enrich_with_cost(
    gate1_signals: pd.DataFrame,
    daily_net: pd.DataFrame,
    prices: pd.DataFrame,
) -> pd.DataFrame:
    """Compute the weighted avg cost for each signal's streak window."""
    # Merge price into daily_net so we have (date, stock_id, net, close).
    merged = daily_net.merge(
        prices[["date", "stock_id", "close"]], on=["date", "stock_id"], how="left"
    )

    results = []
    for _, sig in gate1_signals.iterrows():
        window = merged[
            (merged["stock_id"] == sig["stock_id"])
            & (merged["date"] >= sig["streak_start"])
            & (merged["date"] <= sig["streak_end"])
        ].copy()
        if window.empty or window["close"].isna().any():
            continue
        # Weights are daily net-buy; prices are close.
        total_buy = window["net"].sum()
        if total_buy <= 0:
            continue
        weighted_cost = (window["net"] * window["close"]).sum() / total_buy
        current_price = window["close"].iloc[-1]
        spread_pct = (current_price - weighted_cost) / weighted_cost

        results.append(
            {
                **sig.to_dict(),
                "weighted_cost": weighted_cost,
                "current_price": current_price,
                "spread_pct": spread_pct,
            }
        )
    return pd.DataFrame(results)


def filter_by_spread(
    enriched: pd.DataFrame, max_spread: float = MAX_SPREAD_PCT
) -> pd.DataFrame:
    return enriched[enriched["spread_pct"] <= max_spread].reset_index(drop=True)


def run(
    gate1_signals: pd.DataFrame,
    daily_net: pd.DataFrame,
    prices: pd.DataFrame,
    max_spread: float = MAX_SPREAD_PCT,
) -> pd.DataFrame:
    enriched = enrich_with_cost(gate1_signals, daily_net, prices)
    return filter_by_spread(enriched, max_spread)
