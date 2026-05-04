"""Gate 3 tests — monotonic holder-count decrease over 4 weekly snapshots."""
from __future__ import annotations

import pandas as pd

from three_gate_screener.gates import gate3_holders as g3


def make_signal(stock_id: str, signal_date: str) -> pd.DataFrame:
    return pd.DataFrame([{"stock_id": stock_id, "signal_date": signal_date}])


def make_totals(rows: list[tuple[str, str, int]]) -> pd.DataFrame:
    """rows: (date, stock_id, total_holders)"""
    return pd.DataFrame(
        [{"date": d, "stock_id": sid, "total_holders": h} for d, sid, h in rows]
    )


def test_monotonic_decrease_passes():
    totals = make_totals(
        [
            ("2025-01-03", "1111", 1000),
            ("2025-01-10", "1111", 990),
            ("2025-01-17", "1111", 980),
            ("2025-01-24", "1111", 970),
        ]
    )
    sig = make_signal("1111", "2025-01-25")
    out = g3.check_monotonic_decrease(sig, totals)
    assert len(out) == 1
    assert bool(out.iloc[0]["holders_monotonic"])
    assert out.iloc[0]["holder_t_minus_3"] == 1000
    assert out.iloc[0]["holder_t0"] == 970


def test_flat_week_breaks_monotonic():
    # 1000 -> 990 -> 990 -> 970: NOT strictly decreasing (week 2->3 is flat).
    totals = make_totals(
        [
            ("2025-01-03", "1111", 1000),
            ("2025-01-10", "1111", 990),
            ("2025-01-17", "1111", 990),
            ("2025-01-24", "1111", 970),
        ]
    )
    sig = make_signal("1111", "2025-01-25")
    out = g3.check_monotonic_decrease(sig, totals)
    assert len(out) == 1
    assert not bool(out.iloc[0]["holders_monotonic"])


def test_insufficient_history_yields_no_row():
    # Only 3 snapshots before the signal_date, need 4.
    totals = make_totals(
        [
            ("2025-01-10", "1111", 1000),
            ("2025-01-17", "1111", 990),
            ("2025-01-24", "1111", 980),
        ]
    )
    sig = make_signal("1111", "2025-01-25")
    out = g3.check_monotonic_decrease(sig, totals)
    assert out.empty


def test_picks_4_most_recent_snapshots_before_signal():
    # 6 snapshots; only the last 4 before signal_date should be used.
    # Dates chosen so the "monotonic" check over last 4 is TRUE,
    # but including older data would break it.
    totals = make_totals(
        [
            ("2024-12-20", "1111", 500),   # oldest — should be ignored
            ("2024-12-27", "1111", 400),   # should be ignored
            ("2025-01-03", "1111", 1000),  # t-3
            ("2025-01-10", "1111", 990),   # t-2
            ("2025-01-17", "1111", 980),   # t-1
            ("2025-01-24", "1111", 970),   # t0
        ]
    )
    sig = make_signal("1111", "2025-01-25")
    out = g3.check_monotonic_decrease(sig, totals)
    assert len(out) == 1
    assert bool(out.iloc[0]["holders_monotonic"])
    assert out.iloc[0]["holder_t_minus_3"] == 1000


def test_run_filters_to_monotonic_only(monkeypatch):
    # Two signals, only one has a monotonic decrease history.
    totals = make_totals(
        [
            ("2025-01-03", "1111", 1000),
            ("2025-01-10", "1111", 990),
            ("2025-01-17", "1111", 980),
            ("2025-01-24", "1111", 970),
            ("2025-01-03", "2222", 1000),
            ("2025-01-10", "2222", 1100),  # going UP
            ("2025-01-17", "2222", 1200),
            ("2025-01-24", "2222", 1300),
        ]
    )
    sigs = pd.DataFrame(
        [
            {"stock_id": "1111", "signal_date": "2025-01-25"},
            {"stock_id": "2222", "signal_date": "2025-01-25"},
        ]
    )

    # Stub fetch_holder_totals to return our in-memory frame.
    monkeypatch.setattr(g3, "fetch_holder_totals", lambda conn: totals)
    out = g3.run(sigs, conn=None)
    assert len(out) == 1
    assert out.iloc[0]["stock_id"] == "1111"
