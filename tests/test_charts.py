"""Unit coverage for the chart builders - no snapshot required.

Only the decisions that carry a claim about the data are pinned here. Styling is
not: a chart that merely looks different is not a defect, but a chart that draws
a zero as if it were a measurement is.
"""

from datetime import datetime, timedelta, timezone

import polars as pl

from optionviz.ui import charts


def _bars(volume: list[float]) -> pl.DataFrame:
    start = datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)
    n = len(volume)
    return pl.DataFrame({
        "ts": [start + timedelta(minutes=30 * i) for i in range(n)],
        "open": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0] * n,
        "close": [100.0 + i for i in range(n)],
        "volume": volume,
    })


def _names(fig) -> list[str]:
    return [t.name for t in fig.data]


def test_legs_parse_from_a_description():
    from optionviz.backtest import parse_legs
    assert parse_legs("SPY +1C 756 +1P 718 -1C 749 -1P 721") == (
        (1, "C", 756.0), (1, "P", 718.0), (-1, "C", 749.0), (-1, "P", 721.0)
    )
    assert parse_legs("TSLA +5C 445") == ((5, "C", 445.0),)


def test_the_fade_holds_only_when_the_prevailing_trend_agrees():
    """Mean-reversion-prevailing fades the 10/30 trend (weight -0.5), but only
    when the fade points the way the prevailing trend does. TSLA's trade #1
    entered on a +3.0% spike in a falling market: faded, and bearish."""
    from optionviz.backtest import model_drift
    model = {"trend_weight": -0.5, "trend_with_prevailing_only": True}
    drift, fade, kept = model_drift(model, {"trend": 0.03, "prevailing_trend": -0.0054})
    assert (round(drift, 4), round(fade, 4), kept) == (-0.015, -0.015, True)
    # The same spike in a rising market gets no view at all.
    assert model_drift(model, {"trend": 0.03, "prevailing_trend": 0.002}) == (0.0, -0.015, False)
    # Without a prevailing reading there is nothing to agree with.
    assert model_drift(model, {"trend": 0.03})[2] is False
    # Ungated, the fade always stands; VWAP reversion pulls toward the line.
    drift, _, kept = model_drift({"trend_weight": -0.5, "vwap_reversion": 1.0},
                                 {"trend": 0.02, "vwap_distance": 0.01})
    assert kept and round(drift, 4) == -0.02


def test_five_minute_bars_fold_into_the_run_grid():
    from optionviz.backtest import fold_bars
    start = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)
    five = pl.DataFrame({
        "ts": [start + timedelta(minutes=5 * i) for i in range(6)],
        "open": [1.0, 2, 3, 4, 5, 6], "high": [2.0, 9, 3, 4, 5, 6],
        "low": [0.5, 1, 1, 4, 0.1, 6], "close": [1.5, 2, 3.5, 4, 5, 6.5],
        "volume": [10.0] * 6,
    })
    got = fold_bars(five, 15)
    assert got["ts"].to_list() == [start, start + timedelta(minutes=15)]
    assert got.row(0) == (start, 1.0, 9.0, 0.5, 3.5, 30.0)
    assert got.row(1)[1:] == (4.0, 6.0, 0.1, 6.5, 30.0)


def _marks(gross: list[float], peak: list[float], stop: list[float | None]) -> pl.DataFrame:
    start = datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)
    return pl.DataFrame({
        "ts": [start + timedelta(minutes=30 * i) for i in range(len(gross))],
        "gross_pnl": gross,
        "peak_value": peak,
        "trailing_stop": stop,
    })


def test_the_position_track_draws_pnl_peak_and_the_trailing_stop():
    fig = charts.position_track(
        _marks([0.0, 100.0, 60.0], [0.0, 100.0, 100.0], [None, 70.0, 70.0]), 60.0, "t"
    )
    assert _names(fig) == ["Peak", "Trailing stop", "Gross P&L"]


def test_a_position_that_never_profited_has_no_trailing_stop_line():
    """The stop rides the peak, so a position never in profit has nothing to
    trail. The writer records that as absent; drawing 0 would invent a level."""
    fig = charts.position_track(
        _marks([-50.0, -80.0], [-50.0, -50.0], [None, None]), -80.0, "t"
    )
    assert "Trailing stop" not in _names(fig)
    assert "Gross P&L" in _names(fig)


def test_an_hourly_fold_opens_on_the_session_not_the_hour():
    """Aligned to :00, the first hourly bar would be labelled 13:00 — inside the
    overnight rangebreak, and so never drawn."""
    from optionviz.backtest import fold_bars
    got = fold_bars(_bars([1.0] * 4), 60)
    assert got["ts"][0].strftime("%H:%M") == "13:30"


def _trade(bars: pl.DataFrame, id: int, pnl: float):
    from optionviz.backtest import Trade
    return Trade(
        id=id, symbol="T", description="T +1C 105 -1C 110",
        opened_at=bars["ts"][0], closed_at=bars["ts"][-1], bars_held=bars.height,
        entry_debit=1.0, exit_credit=1.0, commission=0.0, pnl=pnl,
        exit_reason="profit_target",
        synthetic_price=False,
    )


def test_a_bar_lists_exactly_the_trades_open_during_it():
    """The chart's legend lists what is open at a bar from the trades' own
    times — the Plotly version, left to find the legs itself, matched them by
    nearest point: just outside the trade, and never in the middle of it."""
    bars = _bars([1.0] * 6)  # 13:30 ... 16:00, every 30 minutes
    ts = bars["ts"].to_list()
    first = _trade(bars.slice(1, 2), 1, 50.0)   # held 14:00-14:30
    second = _trade(bars.slice(3, 3), 2, -20.0)  # held 15:00-16:00
    text = charts._open_at(bars, (first, second))
    held = [[line.split()[0] for line in t.split("<br>") if line] for t in text]
    assert held == [[], ["#1"], ["#1"], ["#2"], ["#2"], ["#2"]]
    assert "opened" in text[1] and "closed +$50.00" in text[2]
    assert ts[0] < first.opened_at
