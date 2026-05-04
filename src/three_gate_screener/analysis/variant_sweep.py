"""Variant sweep — test 4 configurations, each broken down by year.

Avoid p-hacking: each variant must be motivated by prior evidence, not
"try every combination". We DO NOT sweep Gate2 spread in a grid; we pick
2% because the original doc says 2-3% stop-loss band (symmetric intuition).

Decision rule: only a variant with CONSISTENT positive alpha across all
4 years/periods is worth believing in. Strong total alpha with one bad
year is overfitting.
"""
from __future__ import annotations

import pandas as pd

from three_gate_screener import cache
from three_gate_screener.backtest import BENCHMARK_ID, walk_forward_exits
from three_gate_screener.gates import gate1_institutional, gate2_cost_spread


START = "2023-01-01"
SIGNAL_END = "2026-04-01"
PRICE_END = "2026-04-30"


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


def apply_variant(
    inst: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    min_streak: int,
    max_spread: float | None,
) -> pd.DataFrame:
    """Run gate1 with custom streak, optionally filter by gate2 with custom spread."""
    daily_net = gate1_institutional.compute_daily_smart_money(inst)

    # gate1 with configurable streak length
    orig_min = gate1_institutional.MIN_STREAK
    gate1_institutional.MIN_STREAK = min_streak
    try:
        g1 = gate1_institutional.find_signals(daily_net)
    finally:
        gate1_institutional.MIN_STREAK = orig_min

    if max_spread is None:
        g1_cost = gate2_cost_spread.enrich_with_cost(g1, daily_net, prices)
        return walk_forward_exits(g1_cost, prices)
    else:
        g2 = gate2_cost_spread.run(g1, daily_net, prices, max_spread=max_spread)
        return walk_forward_exits(g2, prices)


def year_stats(wf: pd.DataFrame, benchmark: pd.DataFrame) -> dict[str, dict]:
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
        "V0 Gate1 only (streak>=3)":
            dict(min_streak=3, max_spread=None),
        "V1 Gate1+Gate2@2% (streak>=3)":
            dict(min_streak=3, max_spread=0.02),
        "V2 Gate1 streak>=5":
            dict(min_streak=5, max_spread=None),
        "V3 Gate1 streak>=5 + Gate2@2%":
            dict(min_streak=5, max_spread=0.02),
    }

    results: dict[str, dict[str, dict]] = {}
    for name, params in variants.items():
        wf = apply_variant(inst, prices, **params)
        results[name] = year_stats(wf, benchmark)

    # Print comparison matrix.
    years = ["2023", "2024", "2025", "2026", "ALL"]
    print(f"\n{'':35s} " + "  ".join(f"{y:>12s}" for y in years))
    print(f"{'':35s} " + "  ".join("-" * 12 for _ in years))
    for name, stats in results.items():
        row = [name]
        for y in years:
            if y in stats and stats[y]["alpha_mean"] is not None:
                row.append(f"{stats[y]['alpha_mean']:+.2%} (n={stats[y]['n']:>4d})")
            else:
                row.append("n/a".rjust(12))
        print(f"{row[0]:35s} " + "  ".join(f"{c:>12s}" for c in row[1:]))

    # Which variants are consistently positive across all years?
    print("\n=== CONSISTENCY CHECK ===")
    print("(a 'robust' variant must show positive alpha in ALL 4 yearly buckets)")
    for name, stats in results.items():
        yearly = [
            stats[y]["alpha_mean"]
            for y in ["2023", "2024", "2025", "2026"]
            if y in stats and stats[y]["alpha_mean"] is not None
        ]
        all_positive = all(a > 0 for a in yearly)
        worst = min(yearly) if yearly else None
        print(
            f"  {name:35s}  all-positive={all_positive}  worst={worst:+.2%}"
            if worst is not None
            else f"  {name:35s}  (no data)"
        )


if __name__ == "__main__":
    run()
