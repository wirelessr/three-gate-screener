"""V4/V5 smart-money refinement sweep.

Context: V0 (Foreign + Trust summed) may be polluted by passive ETF flows.
The original strategy's Gate 1 is a SPECIFIC broker branch — we can't match
that on the free tier, but we can try to get closer to "active smart money":

  V4: Investment_Trust only.
      Trust is actively managed, smaller AUM, no passive index flows.
      Taiwanese retail folklore calls trust "投信" = genuine smart money.

  V5: Foreign_Investor AND Investment_Trust both net-buying every day.
      Two independent signals agreeing = stronger.
      Sum-then-test (V0) allows one side to mask the other; AND doesn't.

Baseline V0 (sum of both) is included for comparison.
"""
from __future__ import annotations

import pandas as pd

from three_gate_screener import cache
from three_gate_screener.backtest import (
    BENCHMARK_ID,
    walk_forward_exits,
)
from three_gate_screener.gates import gate1_institutional, gate2_cost_spread


START = "2023-01-01"
SIGNAL_END = "2026-04-01"
PRICE_END = "2026-04-30"
MIN_STREAK = 3


def load_data():
    with cache.connect() as conn:
        inst = pd.read_sql_query(
            "SELECT * FROM institutional WHERE date BETWEEN ? AND ?",
            conn, params=(START, SIGNAL_END))
        prices = pd.read_sql_query(
            "SELECT * FROM prices WHERE date BETWEEN ? AND ?",
            conn, params=(START, PRICE_END))
        benchmark = pd.read_sql_query(
            "SELECT date, close FROM prices WHERE stock_id = ? "
            "AND date BETWEEN ? AND ? ORDER BY date",
            conn, params=(BENCHMARK_ID, START, PRICE_END))
    return inst, prices, benchmark


def find_signals_generic(daily: pd.DataFrame, min_streak: int) -> pd.DataFrame:
    """Same logic as gate1_institutional.find_signals, works on any daily_net df."""
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
                    streak_start_i = i
            else:
                streak_start_i = None

            if streak_start_i is not None:
                streak_len = i - streak_start_i + 1
                if streak_len >= min_streak:
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


def v0_foreign_plus_trust(inst: pd.DataFrame) -> pd.DataFrame:
    return gate1_institutional.compute_daily_smart_money(inst)


def v4_trust_only(inst: pd.DataFrame) -> pd.DataFrame:
    trust = inst[inst["name"] == "Investment_Trust"].copy()
    trust["net"] = trust["buy"] - trust["sell"]
    return (
        trust.groupby(["stock_id", "date"], as_index=False)["net"]
        .sum()
        .sort_values(["stock_id", "date"])
        .reset_index(drop=True)
    )


def v5_foreign_and_trust(inst: pd.DataFrame) -> pd.DataFrame:
    """Require BOTH foreign and trust to be net buyers; signal net = min(foreign, trust).

    Using min() means the streak-monotonic rule on 'net' correctly tracks the
    weaker of the two institutions, which is the conservative interpretation.
    """
    subset = inst[inst["name"].isin({"Foreign_Investor", "Investment_Trust"})].copy()
    subset["net"] = subset["buy"] - subset["sell"]
    pivot = (
        subset.pivot_table(
            index=["stock_id", "date"],
            columns="name",
            values="net",
            aggfunc="sum",
        )
        .fillna(0)
        .reset_index()
    )
    pivot = pivot[
        (pivot["Foreign_Investor"] > 0) & (pivot["Investment_Trust"] > 0)
    ].copy()
    pivot["net"] = pivot[["Foreign_Investor", "Investment_Trust"]].min(axis=1)
    return pivot[["stock_id", "date", "net"]].sort_values(
        ["stock_id", "date"]
    ).reset_index(drop=True)


def year_alpha(wf: pd.DataFrame, benchmark: pd.DataFrame) -> dict:
    if wf.empty:
        return {}
    bench_by_date = dict(zip(benchmark["date"], benchmark["close"]))
    wf = wf.copy()
    wf["year"] = pd.to_datetime(wf["signal_date"]).dt.year

    out = {}
    for label, subset in list(wf.groupby("year")) + [("ALL", wf)]:
        alphas = []
        for _, row in subset.iterrows():
            b0 = bench_by_date.get(row["signal_date"])
            b1 = bench_by_date.get(row["exit_date"])
            if b0 is not None and b1 is not None:
                alphas.append(row["realized_ret"] - (b1 - b0) / b0)
        a = pd.Series(alphas).dropna()
        out[str(label)] = {
            "n": len(subset),
            "alpha_mean": a.mean() if not a.empty else None,
            "alpha_win": (a > 0).mean() if not a.empty else None,
            "mean_ret": subset["realized_ret"].mean(),
        }
    return out


def run() -> None:
    inst, prices, benchmark = load_data()
    print(f"inst rows: {len(inst):,}   prices rows: {len(prices):,}")

    variants = {
        "V0 Foreign+Trust (sum)": v0_foreign_plus_trust,
        "V4 Trust only":          v4_trust_only,
        "V5 Foreign AND Trust":   v5_foreign_and_trust,
    }

    results = {}
    for name, builder in variants.items():
        daily_net = builder(inst)
        g1 = find_signals_generic(daily_net, MIN_STREAK)
        g1_cost = gate2_cost_spread.enrich_with_cost(g1, daily_net, prices)
        wf = walk_forward_exits(g1_cost, prices)
        results[name] = year_alpha(wf, benchmark)
        print(f"  {name}: {len(g1):,} signals")

    years = ["2023", "2024", "2025", "2026", "ALL"]
    print(f"\n=== ALPHA BY YEAR ===")
    print(f"{'variant':25s} " + "  ".join(f"{y:>14s}" for y in years))
    print(f"{'':25s} " + "  ".join("-" * 14 for _ in years))
    for name, stats in results.items():
        row = [name]
        for y in years:
            if y in stats and stats[y]["alpha_mean"] is not None:
                row.append(f"{stats[y]['alpha_mean']:+.2%} n={stats[y]['n']:>4d}")
            else:
                row.append("n/a".rjust(14))
        print(f"{row[0]:25s} " + "  ".join(f"{c:>14s}" for c in row[1:]))

    print(f"\n=== CONSISTENCY CHECK ===")
    for name, stats in results.items():
        yearly = [
            stats[y]["alpha_mean"]
            for y in ["2023", "2024", "2025", "2026"]
            if y in stats and stats[y]["alpha_mean"] is not None
        ]
        if not yearly:
            print(f"  {name}: no data")
            continue
        all_positive = all(a > 0 for a in yearly)
        worst = min(yearly)
        best = max(yearly)
        print(
            f"  {name:25s}  all-positive={all_positive}  "
            f"worst={worst:+.2%}  best={best:+.2%}"
        )


if __name__ == "__main__":
    run()
