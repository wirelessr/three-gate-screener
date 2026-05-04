"""First-pass backtest: for each signal, compute forward returns over
5/10/20 trading days, then summarize.

Intentionally NAIVE. No position sizing, no stop-loss, no commission.
The goal is to see whether signals point at price up-moves on average,
or whether they're noise.

Entry price assumption: close of signal_date (could also be next-day open;
close is simpler and strict — no look-ahead).
"""
from __future__ import annotations

import pandas as pd

from three_gate_screener import cache
from three_gate_screener.gates import (
    gate1_institutional,
    gate2_cost_spread,
    gate3_derived,
    gate3_holders,
)


FORWARD_DAYS = [5, 10, 20]
BENCHMARK_ID = "0050"

# Stop-loss: close below weighted_cost * (1 - STOP_LOSS_PCT) triggers exit.
# Per design doc section 3 exit rules, "主力棄守線" = 2~3% below cost.
STOP_LOSS_PCT = 0.025


def forward_returns(
    signals: pd.DataFrame, prices: pd.DataFrame, horizons: list[int] = FORWARD_DAYS
) -> pd.DataFrame:
    """Attach forward-return columns to each signal row."""
    if signals.empty:
        return signals

    # Per-stock sorted price series for trading-day offsets.
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
        row = sig.to_dict()
        row["entry_price"] = entry
        for h in horizons:
            j = i + h
            if j < len(hist):
                exit_price = hist.loc[j, "close"]
                row[f"ret_{h}d"] = (exit_price - entry) / entry
                row[f"exit_{h}d_date"] = hist.loc[j, "date"]
            else:
                row[f"ret_{h}d"] = None
                row[f"exit_{h}d_date"] = None
        rows.append(row)
    return pd.DataFrame(rows)


def walk_forward_exits(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    stop_loss_pct: float = STOP_LOSS_PCT,
) -> pd.DataFrame:
    """Walk each signal day-by-day, exit on stop-loss OR end-of-data.

    Entry: close of signal_date (as forward_returns does).
    Exit:  first day where close < weighted_cost * (1 - stop_loss_pct),
           else the last available price (exit_reason = 'end_of_data').

    Requires signals to already carry a 'weighted_cost' column (from gate2).
    """
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
        if hist is None or "weighted_cost" not in sig:
            continue
        idx = hist.index[hist["date"] == sig["signal_date"]]
        if len(idx) == 0:
            continue
        i = int(idx[0])
        entry = hist.loc[i, "close"]
        stop_level = sig["weighted_cost"] * (1 - stop_loss_pct)

        exit_i = None
        exit_reason = "end_of_data"
        # Start simulating from the day AFTER entry.
        for j in range(i + 1, len(hist)):
            if hist.loc[j, "close"] < stop_level:
                exit_i = j
                exit_reason = "stop_loss"
                break
        if exit_i is None:
            exit_i = len(hist) - 1

        exit_price = hist.loc[exit_i, "close"]
        hold_days = exit_i - i
        realized_ret = (exit_price - entry) / entry

        rows.append(
            {
                **sig.to_dict(),
                "entry_price": entry,
                "stop_level": stop_level,
                "exit_price": exit_price,
                "exit_date": hist.loc[exit_i, "date"],
                "exit_reason": exit_reason,
                "hold_days": hold_days,
                "realized_ret": realized_ret,
            }
        )
    return pd.DataFrame(rows)


def summarize_walk_forward(
    result: pd.DataFrame, benchmark: pd.DataFrame | None = None
) -> None:
    if result.empty:
        print("  (no signals)")
        return

    n = len(result)
    by_reason = result["exit_reason"].value_counts()
    print(f"  total signals: {n}")
    for reason, count in by_reason.items():
        print(f"    {reason}: {count} ({count / n:.0%})")

    ret = result["realized_ret"].dropna()
    if not ret.empty:
        win = (ret > 0).mean()
        print(
            f"  realized return: mean={ret.mean():+.2%} "
            f"median={ret.median():+.2%} "
            f"std={ret.std():.2%} "
            f"win={win:.0%}"
        )

    hold = result["hold_days"]
    print(
        f"  hold days: mean={hold.mean():.1f} "
        f"median={hold.median():.0f} "
        f"min={hold.min()} max={hold.max()}"
    )

    # Conditional stats by exit reason
    for reason in by_reason.index:
        subset = result[result["exit_reason"] == reason]
        r = subset["realized_ret"]
        print(
            f"  [{reason}] n={len(subset)} "
            f"mean_ret={r.mean():+.2%} "
            f"mean_hold={subset['hold_days'].mean():.1f}d"
        )

    # Alpha vs benchmark, if provided.
    if benchmark is not None and not benchmark.empty:
        bench_by_date = dict(zip(benchmark["date"], benchmark["close"]))
        alphas = []
        for _, row in result.iterrows():
            b0 = bench_by_date.get(row["signal_date"])
            b1 = bench_by_date.get(row["exit_date"])
            if b0 is None or b1 is None:
                alphas.append(None)
                continue
            alphas.append(row["realized_ret"] - (b1 - b0) / b0)
        a = pd.Series(alphas).dropna()
        if not a.empty:
            awin = (a > 0).mean()
            print(
                f"  alpha vs {BENCHMARK_ID}: mean={a.mean():+.2%} "
                f"median={a.median():+.2%} win={awin:.0%}"
            )


def compute_alpha(
    result: pd.DataFrame,
    benchmark_prices: pd.DataFrame,
    horizons: list[int] = FORWARD_DAYS,
) -> pd.DataFrame:
    """Attach alpha_{h}d columns = stock return - benchmark return, same window."""
    if result.empty:
        return result
    bench = benchmark_prices.sort_values("date").reset_index(drop=True)
    bench_by_date = dict(zip(bench["date"], bench["close"]))
    bench_idx = {d: i for i, d in enumerate(bench["date"])}

    out = result.copy()
    for h in horizons:
        alphas = []
        for _, row in out.iterrows():
            entry_dt = row["signal_date"]
            exit_dt = row.get(f"exit_{h}d_date")
            stock_ret = row.get(f"ret_{h}d")
            if (
                pd.isna(exit_dt)
                or pd.isna(stock_ret)
                or entry_dt not in bench_idx
                or exit_dt not in bench_idx
            ):
                alphas.append(None)
                continue
            b0 = bench_by_date[entry_dt]
            b1 = bench_by_date[exit_dt]
            bench_ret = (b1 - b0) / b0
            alphas.append(stock_ret - bench_ret)
        out[f"alpha_{h}d"] = alphas
    return out


def signal_density_over_time(
    g3: pd.DataFrame,
    benchmark_prices: pd.DataFrame,
) -> pd.DataFrame:
    """Weekly count of gate3 signals vs benchmark close."""
    if g3.empty:
        return pd.DataFrame()
    sigs = g3.copy()
    sigs["week"] = pd.to_datetime(sigs["signal_date"]).dt.to_period("W").dt.start_time
    weekly_signals = sigs.groupby("week").size().rename("n_signals")

    bench = benchmark_prices.copy()
    bench["week"] = pd.to_datetime(bench["date"]).dt.to_period("W").dt.start_time
    weekly_bench = bench.groupby("week")["close"].last().rename("benchmark_close")

    merged = (
        pd.concat([weekly_signals, weekly_bench], axis=1)
        .fillna({"n_signals": 0})
        .reset_index()
    )
    return merged


def gate3_attribution(
    g2: pd.DataFrame, g3: pd.DataFrame, prices: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Compare forward returns of signals that passed gate3 vs those rejected."""
    if g2.empty:
        return {"passed": pd.DataFrame(), "rejected": pd.DataFrame()}

    pass_keys = set(zip(g3["stock_id"], g3["signal_date"])) if not g3.empty else set()
    g2_keys = list(zip(g2["stock_id"], g2["signal_date"]))

    g2_with_status = g2.copy()
    g2_with_status["gate3_passed"] = [k in pass_keys for k in g2_keys]

    fwd = forward_returns(g2_with_status, prices)
    return {
        "passed": fwd[fwd["gate3_passed"]],
        "rejected": fwd[~fwd["gate3_passed"]],
    }


def summarize(result: pd.DataFrame, horizons: list[int] = FORWARD_DAYS) -> None:
    print(f"\nsignals with forward-return data: {len(result)}")
    if result.empty:
        return
    for h in horizons:
        col = f"ret_{h}d"
        s = result[col].dropna()
        if s.empty:
            continue
        win_rate = (s > 0).mean()
        alpha_col = f"alpha_{h}d"
        alpha_part = ""
        if alpha_col in result.columns:
            a = result[alpha_col].dropna()
            if not a.empty:
                alpha_win = (a > 0).mean()
                alpha_part = f"  alpha_mean={a.mean():+.2%} alpha_win={alpha_win:.0%}"
        print(
            f"  {col}: n={len(s):>3d} "
            f"mean={s.mean():+.2%} "
            f"median={s.median():+.2%} "
            f"std={s.std():.2%} "
            f"win={win_rate:.0%} "
            f"min={s.min():+.2%} max={s.max():+.2%}{alpha_part}"
        )


def run(
    start_date: str = "2021-06-01",
    signal_end_date: str = "2021-10-15",
    price_end_date: str = "2022-03-31",
    max_spread: float = 0.05,
) -> dict[str, pd.DataFrame]:
    with cache.connect() as conn:
        # Inst data window ends at signal_end_date (we don't generate signals beyond).
        inst = pd.read_sql_query(
            "SELECT * FROM institutional WHERE date BETWEEN ? AND ?",
            conn,
            params=(start_date, signal_end_date),
        )
        # Price data extends to price_end_date so stop-loss can resolve.
        prices = pd.read_sql_query(
            "SELECT * FROM prices WHERE date BETWEEN ? AND ?",
            conn,
            params=(start_date, price_end_date),
        )

    daily_net = gate1_institutional.compute_daily_smart_money(inst)
    g1 = gate1_institutional.find_signals(daily_net)
    g2 = gate2_cost_spread.run(g1, daily_net, prices, max_spread=max_spread)
    with cache.connect() as conn:
        g3 = gate3_holders.run(g2, conn)

    print(f"gate1: {len(g1):>4d} signals")
    print(f"gate2: {len(g2):>4d} signals (spread <= {max_spread:.0%})")
    print(f"gate3: {len(g3):>4d} signals (monotonic holder decrease)")

    # Benchmark for alpha calc.
    with cache.connect() as conn:
        benchmark = pd.read_sql_query(
            "SELECT date, close FROM prices WHERE stock_id = ? "
            "AND date BETWEEN ? AND ? ORDER BY date",
            conn,
            params=(BENCHMARK_ID, start_date, price_end_date),
        )
    print(f"\nbenchmark {BENCHMARK_ID}: {len(benchmark)} trading days")

    # Forward returns at each gate for comparison.
    print("\n--- forward returns: gate1 signals (baseline) ---")
    g1_fwd = compute_alpha(forward_returns(g1, prices), benchmark)
    summarize(g1_fwd)

    print("\n--- forward returns: gate2 signals ---")
    g2_fwd = compute_alpha(forward_returns(g2, prices), benchmark)
    summarize(g2_fwd)

    print("\n--- forward returns: gate3 signals (all three gates) ---")
    g3_fwd = compute_alpha(forward_returns(g3, prices), benchmark)
    summarize(g3_fwd)

    # Diagnostic 1: does gate3 actually help?
    print("\n--- gate3 attribution: passed vs rejected ---")
    attrib = gate3_attribution(g2, g3, prices)
    attrib["passed"] = compute_alpha(attrib["passed"], benchmark)
    attrib["rejected"] = compute_alpha(attrib["rejected"], benchmark)
    print(f"[passed gate3]   n={len(attrib['passed'])}")
    summarize(attrib["passed"])
    print(f"[rejected gate3] n={len(attrib['rejected'])}")
    summarize(attrib["rejected"])

    # Diagnostic 2: signal density vs benchmark direction.
    print("\n--- signal density (weekly) vs benchmark ---")
    density = signal_density_over_time(g3, benchmark)
    if not density.empty:
        print(density.to_string(index=False))

    # Walk-forward backtest with stop-loss.
    print(f"\n=== WALK-FORWARD BACKTEST (stop_loss={STOP_LOSS_PCT:.1%} below cost) ===")
    print("\n--- gate1 signals (baseline) ---")
    g1_wf = walk_forward_exits(
        gate2_cost_spread.enrich_with_cost(g1, daily_net, prices),
        prices,
    )
    summarize_walk_forward(g1_wf, benchmark)

    print("\n--- gate2 signals ---")
    g2_wf = walk_forward_exits(g2, prices)
    summarize_walk_forward(g2_wf, benchmark)

    print("\n--- gate3 signals (all three gates) ---")
    g3_wf = walk_forward_exits(g3, prices)
    summarize_walk_forward(g3_wf, benchmark)

    # Gate3 attribution for walk-forward.
    if not g3.empty and not g2.empty:
        pass_keys = set(zip(g3["stock_id"], g3["signal_date"]))
        g2_wf_with_status = g2_wf.copy()
        g2_wf_with_status["gate3_passed"] = [
            (r["stock_id"], r["signal_date"]) in pass_keys
            for _, r in g2_wf_with_status.iterrows()
        ]
        print("\n--- walk-forward gate3 attribution ---")
        print("[gate3 passed]")
        summarize_walk_forward(
            g2_wf_with_status[g2_wf_with_status["gate3_passed"]], benchmark
        )
        print("[gate3 rejected]")
        summarize_walk_forward(
            g2_wf_with_status[~g2_wf_with_status["gate3_passed"]], benchmark
        )

    if not g3_fwd.empty:
        print("\n--- all-gates signals in detail ---")
        cols = [
            "stock_id",
            "signal_date",
            "streak_length",
            "spread_pct",
            "holder_change_pct",
            "entry_price",
        ] + [f"ret_{h}d" for h in FORWARD_DAYS]
        disp = g3_fwd[cols].copy()
        for c in ["spread_pct", "holder_change_pct"] + [f"ret_{h}d" for h in FORWARD_DAYS]:
            disp[c] = disp[c].apply(lambda x: f"{x:+.2%}" if pd.notna(x) else "n/a")
        print(disp.to_string(index=False))

    return {"g1": g1_fwd, "g2": g2_fwd, "g3": g3_fwd}


def run_derived(
    start_date: str = "2023-01-01",
    signal_end_date: str = "2026-04-01",
    price_end_date: str = "2026-04-30",
    max_spread: float = 0.05,
    variant: str = "both",
) -> dict[str, pd.DataFrame]:
    """Backtest using twsthr derived metrics for gate3 (variant A or B)."""
    with cache.connect() as conn:
        inst = pd.read_sql_query(
            "SELECT * FROM institutional WHERE date BETWEEN ? AND ?",
            conn,
            params=(start_date, signal_end_date),
        )
        prices = pd.read_sql_query(
            "SELECT * FROM prices WHERE date BETWEEN ? AND ?",
            conn,
            params=(start_date, price_end_date),
        )
        benchmark = pd.read_sql_query(
            "SELECT date, close FROM prices WHERE stock_id = ? "
            "AND date BETWEEN ? AND ? ORDER BY date",
            conn,
            params=(BENCHMARK_ID, start_date, price_end_date),
        )
    print(f"inst rows: {len(inst):,}   prices rows: {len(prices):,}")
    print(f"benchmark {BENCHMARK_ID}: {len(benchmark)} trading days")

    daily_net = gate1_institutional.compute_daily_smart_money(inst)
    g1 = gate1_institutional.find_signals(daily_net)
    g2 = gate2_cost_spread.run(g1, daily_net, prices, max_spread=max_spread)
    print(f"gate1: {len(g1):,} signals")
    print(f"gate2: {len(g2):,} signals (spread <= {max_spread:.0%})")

    variants: dict[str, pd.DataFrame] = {}
    with cache.connect() as conn:
        if variant in ("A", "both"):
            variants["A"] = gate3_derived.variant_a(g2, conn)
            print(f"gate3-A (holders dec):             {len(variants['A']):,} signals")
        if variant in ("B", "both"):
            variants["B"] = gate3_derived.variant_b(g2, conn)
            print(
                f"gate3-B (holders dec + whale up):  {len(variants['B']):,} signals"
            )

    print(f"\n=== WALK-FORWARD (stop_loss={STOP_LOSS_PCT:.1%} below cost) ===")
    print("\n--- gate1 baseline ---")
    g1_wf = walk_forward_exits(
        gate2_cost_spread.enrich_with_cost(g1, daily_net, prices), prices
    )
    summarize_walk_forward(g1_wf, benchmark)

    print("\n--- gate2 ---")
    g2_wf = walk_forward_exits(g2, prices)
    summarize_walk_forward(g2_wf, benchmark)

    results = {"g1": g1_wf, "g2": g2_wf}
    for name, gX in variants.items():
        print(f"\n--- gate3-{name} ---")
        gX_wf = walk_forward_exits(gX, prices)
        summarize_walk_forward(gX_wf, benchmark)
        results[f"g3_{name}"] = gX_wf
    return results


if __name__ == "__main__":
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "classic"
    if mode == "derived":
        run_derived()
    else:
        run()
