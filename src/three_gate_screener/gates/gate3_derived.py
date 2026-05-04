"""Gate 3 (twsthr derived data variant).

Data source: holders_derived table, populated from norway.twsthr.info.
Coverage: 2023-01-13 to now, ~2400 stocks, weekly.

Two variants implemented so we can A/B them against the original gate3:

  VARIANT_A — 1:1 port of the raw TDCC gate3
      total_holders strictly decreasing over the past 4 weekly snapshots.

  VARIANT_B — concentration-direct
      total_holders decreasing
      AND whale_1000_percent increasing by at least 0.1 percentage point
      over the same 4-week window.
      This directly confirms "retail leaving AND big holders accumulating",
      which is what the original design doc is trying to infer indirectly.
"""
from __future__ import annotations

import pandas as pd


LOOKBACK_WEEKS = 4
MIN_WHALE_PCT_RISE = 0.1  # percentage points, not fraction


def fetch_derived(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT date, stock_id, total_holders, whale_1000_percent
          FROM holders_derived
         ORDER BY stock_id, date
        """,
        conn,
    )


def _check_window(signals: pd.DataFrame, derived: pd.DataFrame) -> pd.DataFrame:
    """Attach window stats to each signal; no filtering yet."""
    by_stock = {
        sid: grp.sort_values("date").reset_index(drop=True)
        for sid, grp in derived.groupby("stock_id")
    }
    rows = []
    for _, sig in signals.iterrows():
        sid = sig["stock_id"]
        sig_date = sig["signal_date"]
        hist = by_stock.get(sid)
        if hist is None:
            continue
        past = hist[hist["date"] <= sig_date].tail(LOOKBACK_WEEKS)
        if len(past) < LOOKBACK_WEEKS:
            continue
        holders = past["total_holders"].to_numpy()
        whale_pct = past["whale_1000_percent"].to_numpy()
        rows.append(
            {
                **sig.to_dict(),
                "derived_holder_t0": int(holders[-1]),
                "derived_holder_t_minus_3": int(holders[0]),
                "derived_holder_monotonic_dec": bool(
                    (holders[1:] < holders[:-1]).all()
                ),
                "whale_1000_pct_t0": float(whale_pct[-1]),
                "whale_1000_pct_t_minus_3": float(whale_pct[0]),
                "whale_1000_pct_rise": float(whale_pct[-1] - whale_pct[0]),
            }
        )
    return pd.DataFrame(rows)


def variant_a(signals: pd.DataFrame, conn) -> pd.DataFrame:
    """Monotonic decrease in total_holders — direct port of raw gate3."""
    enriched = _check_window(signals, fetch_derived(conn))
    if enriched.empty:
        return enriched
    return enriched[enriched["derived_holder_monotonic_dec"]].reset_index(drop=True)


def variant_b(
    signals: pd.DataFrame,
    conn,
    min_whale_rise: float = MIN_WHALE_PCT_RISE,
) -> pd.DataFrame:
    """Holders down AND whale concentration up — direct concentration test."""
    enriched = _check_window(signals, fetch_derived(conn))
    if enriched.empty:
        return enriched
    mask = enriched["derived_holder_monotonic_dec"] & (
        enriched["whale_1000_pct_rise"] >= min_whale_rise
    )
    return enriched[mask].reset_index(drop=True)


def variant_c_leading(signals: pd.DataFrame, conn) -> pd.DataFrame:
    """Leading-edge variant: holders just STARTED decreasing.

    Hypothesis: the original gate3 is a lagging indicator — by the time holders
    have decreased for 4 consecutive weeks, the big money has already been
    accumulating. The inflection point (flat/rising -> falling) might be the
    real signal.

    Rule: at t0, total_holders < t-1, AND t-1 >= t-2 >= t-3 (i.e. prior 3
    weeks were non-decreasing). First week of a downtrend.
    """
    derived = fetch_derived(conn)
    by_stock = {
        sid: grp.sort_values("date").reset_index(drop=True)
        for sid, grp in derived.groupby("stock_id")
    }
    rows = []
    for _, sig in signals.iterrows():
        sid = sig["stock_id"]
        sig_date = sig["signal_date"]
        hist = by_stock.get(sid)
        if hist is None:
            continue
        past = hist[hist["date"] <= sig_date].tail(4)
        if len(past) < 4:
            continue
        h = past["total_holders"].to_numpy()
        # h is [t-3, t-2, t-1, t0]. We want:
        #   h[-1] < h[-2]            (just started decreasing)
        #   h[-2] >= h[-3] >= h[-4]  (prior trend was flat/rising)
        if h[-1] < h[-2] and h[-2] >= h[-3] and h[-3] >= h[-4]:
            rows.append(
                {
                    **sig.to_dict(),
                    "derived_holder_t0": int(h[-1]),
                    "derived_holder_t_minus_3": int(h[0]),
                    "derived_holder_change_pct": (h[-1] - h[0]) / h[0],
                }
            )
    return pd.DataFrame(rows)
