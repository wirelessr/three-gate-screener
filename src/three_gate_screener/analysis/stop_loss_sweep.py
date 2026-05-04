"""Stop-loss sensitivity analysis.

The 2.5% stop-loss in backtest.py was picked as the midpoint of the design
doc's "2% to 3%" range, without empirical justification. If it's too tight,
it would kill winners prematurely and mask whatever real edge exists.

Test stop-loss values: none / 1.5% / 2.5% / 3% / 5% / 10%.

Same consistency rule as variant_sweep: a stop-loss value is "working" only
if it delivers POSITIVE ALPHA IN EVERY YEAR, not just good totals.
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


def walk_forward_no_stop(
    signals: pd.DataFrame, prices: pd.DataFrame
) -> pd.DataFrame:
    """Walk forward with NO stop-loss — hold until end of data."""
    if signals.empty:
        return signals
    by_stock = {
        sid: grp.sort_values("date").reset_index(drop=True)
        for sid, grp in prices.groupby("stock_id")
    }
    rows = []
    for _, sig in signals.iterrows():
        sid = sig["stock_id"]
        hist = by_stock.get(sid)
        if hist is None:
            continue
        idx = hist.index[hist["date"] == sig["signal_date"]]
        if len(idx) == 0:
            continue
        i = int(idx[0])
        entry = hist.loc[i, "close"]
        exit_i = len(hist) - 1
        exit_price = hist.loc[exit_i, "close"]
        rows.append(
            {
                **sig.to_dict(),
                "entry_price": entry,
                "exit_price": exit_price,
                "exit_date": hist.loc[exit_i, "date"],
                "exit_reason": "end_of_data",
                "hold_days": exit_i - i,
                "realized_ret": (exit_price - entry) / entry,
            }
        )
    return pd.DataFrame(rows)


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
            "mean_ret": subset["realized_ret"].mean(),
            "stop_rate": (
                (subset["exit_reason"] == "stop_loss").mean()
                if "exit_reason" in subset.columns
                else 0.0
            ),
        }
    return out


def run() -> None:
    inst, prices, benchmark = load_data()
    print(f"inst rows: {len(inst):,}   prices rows: {len(prices):,}")

    daily_net = gate1_institutional.compute_daily_smart_money(inst)
    g1 = gate1_institutional.find_signals(daily_net)
    g1_with_cost = gate2_cost_spread.enrich_with_cost(g1, daily_net, prices)
    print(f"gate1 signals: {len(g1_with_cost):,}\n")

    stop_values = [None, 0.015, 0.025, 0.03, 0.05, 0.10]
    labels = ["no-stop", "1.5%", "2.5%", "3%", "5%", "10%"]

    results: dict[str, dict] = {}
    for pct, label in zip(stop_values, labels):
        if pct is None:
            wf = walk_forward_no_stop(g1_with_cost, prices)
        else:
            wf = walk_forward_exits(g1_with_cost, prices, stop_loss_pct=pct)
        results[label] = year_alpha(wf, benchmark)

    years = ["2023", "2024", "2025", "2026", "ALL"]
    print(f"\n=== ALPHA BY YEAR, across stop-loss values ===")
    print(f"{'stop':10s} " + "  ".join(f"{y:>14s}" for y in years))
    print(f"{'':10s} " + "  ".join("-" * 14 for _ in years))
    for label, stats in results.items():
        row = [label]
        for y in years:
            if y in stats and stats[y]["alpha_mean"] is not None:
                row.append(
                    f"{stats[y]['alpha_mean']:+.2%} n={stats[y]['n']:>4d}"
                )
            else:
                row.append("n/a".rjust(14))
        print(f"{row[0]:10s} " + "  ".join(f"{c:>14s}" for c in row[1:]))

    print(f"\n=== STOP-LOSS TRIGGER RATE (full sample) ===")
    for label, stats in results.items():
        if "ALL" in stats:
            print(f"  {label:10s}  stop_rate={stats['ALL']['stop_rate']:.0%}  "
                  f"mean_ret={stats['ALL']['mean_ret']:+.2%}")

    print(f"\n=== CONSISTENCY CHECK ===")
    for label, stats in results.items():
        yearly = [
            stats[y]["alpha_mean"]
            for y in ["2023", "2024", "2025", "2026"]
            if y in stats and stats[y]["alpha_mean"] is not None
        ]
        all_positive = all(a > 0 for a in yearly) if yearly else False
        worst = min(yearly) if yearly else None
        print(
            f"  stop={label:8s}  all-positive={all_positive}  "
            f"worst={worst:+.2%}" if worst is not None else f"  stop={label}: no data"
        )


if __name__ == "__main__":
    run()
