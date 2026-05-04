"""Gate 1 tests — consecutive net-buy streak with monotonic increase."""
from __future__ import annotations

import pandas as pd
import pytest

from three_gate_screener.gates import gate1_institutional as g1


def make_inst(stock_id: str, rows: list[tuple[str, str, int, int]]) -> pd.DataFrame:
    """rows: (date, name, buy, sell). 'name' must be a smart-money label."""
    return pd.DataFrame(
        [
            {"stock_id": stock_id, "date": d, "name": n, "buy": b, "sell": s}
            for d, n, b, s in rows
        ]
    )


def test_compute_daily_smart_money_sums_foreign_and_trust():
    inst = make_inst(
        "2330",
        [
            ("2024-01-02", "Foreign_Investor", 1000, 0),
            ("2024-01-02", "Investment_Trust", 500, 0),
            ("2024-01-02", "Dealer_self", 9999, 0),  # excluded
        ],
    )
    daily = g1.compute_daily_smart_money(inst)
    assert len(daily) == 1
    assert daily.iloc[0]["net"] == 1500


def test_streak_must_be_at_least_3_days():
    # 2 consecutive positive days: no signal.
    daily = pd.DataFrame(
        [
            {"stock_id": "1111", "date": "2024-01-02", "net": 100},
            {"stock_id": "1111", "date": "2024-01-03", "net": 200},
        ]
    )
    assert g1.find_signals(daily).empty


def test_monotonic_increasing_3_day_fires():
    daily = pd.DataFrame(
        [
            {"stock_id": "1111", "date": "2024-01-02", "net": 100},
            {"stock_id": "1111", "date": "2024-01-03", "net": 200},
            {"stock_id": "1111", "date": "2024-01-04", "net": 300},
        ]
    )
    sig = g1.find_signals(daily)
    assert len(sig) == 1
    assert sig.iloc[0]["streak_length"] == 3
    assert sig.iloc[0]["signal_date"] == "2024-01-04"


def test_non_monotonic_resets_streak():
    # Day 3 breaks the monotonic rule (200 <= 200), so streak restarts at day 3.
    # That means by day 4 only length 2; by day 5 length 3 — fires on day 5.
    daily = pd.DataFrame(
        [
            {"stock_id": "1111", "date": "2024-01-02", "net": 100},
            {"stock_id": "1111", "date": "2024-01-03", "net": 200},
            {"stock_id": "1111", "date": "2024-01-04", "net": 200},
            {"stock_id": "1111", "date": "2024-01-05", "net": 300},
            {"stock_id": "1111", "date": "2024-01-06", "net": 400},
        ]
    )
    sig = g1.find_signals(daily)
    assert len(sig) == 1
    assert sig.iloc[0]["signal_date"] == "2024-01-06"
    assert sig.iloc[0]["streak_length"] == 3  # restarted at day 3


def test_net_sell_breaks_streak_entirely():
    daily = pd.DataFrame(
        [
            {"stock_id": "1111", "date": "2024-01-02", "net": 100},
            {"stock_id": "1111", "date": "2024-01-03", "net": 200},
            {"stock_id": "1111", "date": "2024-01-04", "net": -50},
            {"stock_id": "1111", "date": "2024-01-05", "net": 300},
            {"stock_id": "1111", "date": "2024-01-06", "net": 400},
        ]
    )
    # After -50 the streak fully resets; then 300, 400 is only 2 days.
    assert g1.find_signals(daily).empty


def test_longer_streak_also_emits_longer_signal_row():
    # 6 monotonic days: day 2, 3, 4, 5, 6, 7.
    # A signal is emitted every day streak >= 3, so days 4, 5, 6, 7 = 4 rows.
    daily = pd.DataFrame(
        [
            {"stock_id": "1111", "date": f"2024-01-0{i}", "net": 100 * i}
            for i in range(2, 8)
        ]
    )
    sig = g1.find_signals(daily)
    assert len(sig) == 4
    assert sig.iloc[-1]["streak_length"] == 6
    assert sig.iloc[0]["streak_length"] == 3


def test_per_stock_isolation():
    daily = pd.DataFrame(
        [
            {"stock_id": "1111", "date": "2024-01-02", "net": 100},
            {"stock_id": "1111", "date": "2024-01-03", "net": 200},
            {"stock_id": "2222", "date": "2024-01-04", "net": 300},
        ]
    )
    # 1111 has 2 days (no signal), 2222 has 1 day (no signal).
    assert g1.find_signals(daily).empty
