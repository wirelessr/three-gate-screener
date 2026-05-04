"""Gate 2 tests — weighted-cost calculation and 5% spread filter."""
from __future__ import annotations

import pandas as pd

from three_gate_screener.gates import gate2_cost_spread as g2


def make_daily_net(stock_id: str, rows: list[tuple[str, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"stock_id": stock_id, "date": d, "net": n} for d, n in rows]
    )


def make_prices(stock_id: str, rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"stock_id": stock_id, "date": d, "close": c} for d, c in rows]
    )


def make_gate1_signal(stock_id: str, start: str, end: str, streak_len: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "stock_id": stock_id,
                "signal_date": end,
                "streak_length": streak_len,
                "streak_start": start,
                "streak_end": end,
                "avg_daily_net_buy": 0,
                "total_net_buy": 0,
            }
        ]
    )


def test_weighted_cost_is_volume_weighted_average():
    # Day 1: net=100 @ price=10 (weight 1000)
    # Day 2: net=200 @ price=12 (weight 2400)
    # Day 3: net=300 @ price=15 (weight 4500)
    # Cost = (1000 + 2400 + 4500) / (100+200+300) = 7900/600 = 13.1666...
    daily = make_daily_net("1111", [("2024-01-02", 100), ("2024-01-03", 200), ("2024-01-04", 300)])
    prices = make_prices("1111", [("2024-01-02", 10), ("2024-01-03", 12), ("2024-01-04", 15)])
    sig = make_gate1_signal("1111", "2024-01-02", "2024-01-04", 3)

    enriched = g2.enrich_with_cost(sig, daily, prices)
    assert len(enriched) == 1
    cost = enriched.iloc[0]["weighted_cost"]
    assert abs(cost - 13.1666667) < 1e-4


def test_spread_filter_passes_when_current_close_to_cost():
    daily = make_daily_net("1111", [("2024-01-02", 100), ("2024-01-03", 100), ("2024-01-04", 100)])
    # All at price 100 -> cost = 100, current = 100, spread = 0%
    prices = make_prices("1111", [("2024-01-02", 100), ("2024-01-03", 100), ("2024-01-04", 100)])
    sig = make_gate1_signal("1111", "2024-01-02", "2024-01-04", 3)

    out = g2.run(sig, daily, prices, max_spread=0.05)
    assert len(out) == 1
    assert abs(out.iloc[0]["spread_pct"]) < 1e-9


def test_spread_filter_rejects_when_price_jumped_over_5_percent():
    # Weighted cost = 100; but we force current price to jump by only inflating the last day.
    # Day 1, 2: net 100 each @ 100. Day 3 net=1 @ 120 -> cost ~= (20000 + 120) / 201 ≈ 100.1
    # Current price = 120; spread = (120 - 100.1) / 100.1 ≈ 19.9% -> REJECT
    daily = make_daily_net("1111", [("2024-01-02", 100), ("2024-01-03", 100), ("2024-01-04", 1)])
    prices = make_prices("1111", [("2024-01-02", 100), ("2024-01-03", 100), ("2024-01-04", 120)])
    sig = make_gate1_signal("1111", "2024-01-02", "2024-01-04", 3)

    out = g2.run(sig, daily, prices, max_spread=0.05)
    assert out.empty


def test_enrich_returns_empty_for_missing_prices():
    daily = make_daily_net("1111", [("2024-01-02", 100), ("2024-01-03", 100), ("2024-01-04", 100)])
    # Price missing for 2024-01-03 -> skip this signal.
    prices = make_prices("1111", [("2024-01-02", 100), ("2024-01-04", 100)])
    sig = make_gate1_signal("1111", "2024-01-02", "2024-01-04", 3)

    out = g2.enrich_with_cost(sig, daily, prices)
    assert out.empty


def test_negative_spread_still_passes():
    # Current price below cost -> spread_pct is negative -> passes 5% ceiling.
    daily = make_daily_net("1111", [("2024-01-02", 100), ("2024-01-03", 100), ("2024-01-04", 100)])
    prices = make_prices("1111", [("2024-01-02", 100), ("2024-01-03", 110), ("2024-01-04", 95)])
    sig = make_gate1_signal("1111", "2024-01-02", "2024-01-04", 3)

    out = g2.run(sig, daily, prices, max_spread=0.05)
    assert len(out) == 1
    assert out.iloc[0]["spread_pct"] < 0
