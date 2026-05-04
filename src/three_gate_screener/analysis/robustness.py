"""Gate1 alpha robustness checks — slice results by time period and
(if available) industry.

Purpose: is the +4.2% alpha from the full-sample backtest consistent across
sub-periods, or is it concentrated in a single "lucky year"?
"""
from __future__ import annotations

import pandas as pd

from three_gate_screener import cache
from three_gate_screener.backtest import (
    BENCHMARK_ID,
    STOP_LOSS_PCT,
    walk_forward_exits,
)
from three_gate_screener.gates import gate1_institutional, gate2_cost_spread


def load_data(start: str, signal_end: str, price_end: str) -> dict[str, pd.DataFrame]:
    with cache.connect() as conn:
        inst = pd.read_sql_query(
            "SELECT * FROM institutional WHERE date BETWEEN ? AND ?",
            conn,
            params=(start, signal_end),
        )
        prices = pd.read_sql_query(
            "SELECT * FROM prices WHERE date BETWEEN ? AND ?",
            conn,
            params=(start, price_end),
        )
        benchmark = pd.read_sql_query(
            "SELECT date, close FROM prices WHERE stock_id = ? "
            "AND date BETWEEN ? AND ? ORDER BY date",
            conn,
            params=(BENCHMARK_ID, start, price_end),
        )
    return {"inst": inst, "prices": prices, "benchmark": benchmark}


def summarize(result: pd.DataFrame, benchmark: pd.DataFrame, label: str) -> dict:
    if result.empty:
        print(f"  {label}: no signals")
        return {}

    bench_by_date = dict(zip(benchmark["date"], benchmark["close"]))
    alphas = []
    for _, row in result.iterrows():
        b0 = bench_by_date.get(row["signal_date"])
        b1 = bench_by_date.get(row["exit_date"])
        if b0 is None or b1 is None:
            continue
        alphas.append(row["realized_ret"] - (b1 - b0) / b0)

    ret = result["realized_ret"].dropna()
    stop = (result["exit_reason"] == "stop_loss").sum()
    n = len(result)
    alpha_series = pd.Series(alphas).dropna()

    stats = {
        "label": label,
        "n": n,
        "stop_rate": stop / n,
        "mean_ret": ret.mean(),
        "median_ret": ret.median(),
        "win_rate": (ret > 0).mean(),
        "alpha_mean": alpha_series.mean() if not alpha_series.empty else None,
        "alpha_median": alpha_series.median() if not alpha_series.empty else None,
        "alpha_win": (alpha_series > 0).mean() if not alpha_series.empty else None,
    }
    print(
        f"  {label:28s} n={n:>4d}  "
        f"mean_ret={stats['mean_ret']:+.2%}  "
        f"median={stats['median_ret']:+.2%}  "
        f"win={stats['win_rate']:.0%}  "
        f"alpha={stats['alpha_mean']:+.2%}  "
        f"alpha_win={stats['alpha_win']:.0%}  "
        f"stop={stats['stop_rate']:.0%}"
    )
    return stats


def generate_gate1_walkforward(
    inst: pd.DataFrame, prices: pd.DataFrame
) -> pd.DataFrame:
    daily_net = gate1_institutional.compute_daily_smart_money(inst)
    g1 = gate1_institutional.find_signals(daily_net)
    g1_enriched = gate2_cost_spread.enrich_with_cost(g1, daily_net, prices)
    return walk_forward_exits(g1_enriched, prices)


def slice_by_year(wf: pd.DataFrame) -> dict[str, pd.DataFrame]:
    wf = wf.copy()
    wf["year"] = pd.to_datetime(wf["signal_date"]).dt.year
    return {str(y): grp for y, grp in wf.groupby("year")}


def slice_by_signal_half(wf: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """For within-year granularity: split into H1 (Jan-Jun) vs H2 (Jul-Dec)."""
    wf = wf.copy()
    dt = pd.to_datetime(wf["signal_date"])
    wf["bucket"] = dt.dt.year.astype(str) + "-" + dt.dt.month.le(6).map(
        {True: "H1", False: "H2"}
    )
    return {b: grp for b, grp in wf.groupby("bucket")}


def run() -> None:
    start, signal_end, price_end = "2023-01-01", "2026-04-01", "2026-04-30"
    data = load_data(start, signal_end, price_end)
    print(f"inst rows: {len(data['inst']):,}   "
          f"prices rows: {len(data['prices']):,}")

    wf = generate_gate1_walkforward(data["inst"], data["prices"])
    print(f"gate1 walk-forward signals: {len(wf):,}\n")

    print(f"=== FULL SAMPLE ===")
    summarize(wf, data["benchmark"], "ALL")

    print(f"\n=== BY YEAR ===")
    for label, subset in sorted(slice_by_year(wf).items()):
        summarize(subset, data["benchmark"], label)

    print(f"\n=== BY HALF-YEAR ===")
    for label, subset in sorted(slice_by_signal_half(wf).items()):
        summarize(subset, data["benchmark"], label)

    # Concentration check — how much of total signal count comes from top N stocks?
    print(f"\n=== CONCENTRATION BY STOCK ===")
    top_stocks = (
        wf["stock_id"].value_counts().head(10)
    )
    print(f"top 10 stocks account for {top_stocks.sum()} / {len(wf)} "
          f"({top_stocks.sum() / len(wf):.0%}) of signals:")
    print(top_stocks.to_string())


if __name__ == "__main__":
    run()
