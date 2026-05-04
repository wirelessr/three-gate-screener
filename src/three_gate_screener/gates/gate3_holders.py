"""Gate 3: holder-count monotonic decrease over past 4 weeks.

Spec: level-17 (total) people count must strictly decrease over 4 consecutive
weekly TDCC snapshots. Interpretation: "chips concentrating from retail to
whales" — if total holder count goes down AND share count stays the same,
by pigeonhole more shares per holder = concentration.

Input: a signal date (business day) + stock_id. We match it to the 4 most
recent TDCC snapshots with date <= signal_date, then check monotonicity.

Output: adds [holder_t0, holder_t_minus_3, holder_change_pct, holders_monotonic]
to each input signal; callers filter by holders_monotonic == True.
"""
from __future__ import annotations

import pandas as pd


LOOKBACK_WEEKS = 4


def fetch_holder_totals(conn) -> pd.DataFrame:
    """All level-17 (total) rows across time, sorted by date."""
    return pd.read_sql_query(
        """
        SELECT date, stock_id, people AS total_holders
          FROM holders_snapshot
         WHERE level = 17
         ORDER BY stock_id, date
        """,
        conn,
    )


def check_monotonic_decrease(
    signals: pd.DataFrame, holder_totals: pd.DataFrame
) -> pd.DataFrame:
    """For each signal, verify holder count strictly decreased over past 4 snapshots."""
    enriched_rows = []
    # Pre-index for fast per-stock slicing.
    by_stock = {sid: grp.sort_values("date") for sid, grp in holder_totals.groupby("stock_id")}

    for _, sig in signals.iterrows():
        sid = sig["stock_id"]
        sig_date = sig["signal_date"]
        hist = by_stock.get(sid)
        if hist is None:
            continue
        past = hist[hist["date"] <= sig_date].tail(LOOKBACK_WEEKS)
        if len(past) < LOOKBACK_WEEKS:
            continue
        series = past["total_holders"].to_numpy()
        monotonic = bool((series[1:] < series[:-1]).all())
        enriched_rows.append(
            {
                **sig.to_dict(),
                "holder_t_minus_3": int(series[0]),
                "holder_t0": int(series[-1]),
                "holder_change_pct": (series[-1] - series[0]) / series[0],
                "holders_monotonic": monotonic,
            }
        )
    return pd.DataFrame(enriched_rows)


def run(signals: pd.DataFrame, conn) -> pd.DataFrame:
    totals = fetch_holder_totals(conn)
    enriched = check_monotonic_decrease(signals, totals)
    if enriched.empty:
        return enriched
    return enriched[enriched["holders_monotonic"]].reset_index(drop=True)
