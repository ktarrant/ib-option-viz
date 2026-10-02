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


def _box(bars: pl.DataFrame, **kwargs):
    start = bars["ts"][0]
    return charts.trade_box(
        bars, start, bars["ts"][-1], 105.0, 110.0, 250.0, "test", **kwargs
    )


def _names(fig) -> list[str]:
    return [t.name for t in fig.data]


def test_volume_is_a_stacked_panel_not_a_second_price_axis():
    """CLAUDE.md: two measures on different scales are two stacked figures
    sharing an x-axis. Price and share count have no common scale."""
    fig = _box(_bars([1000.0, 2000.0, 1500.0]))
    assert "Volume" in _names(fig)
    # Separate vertical domains, volume beneath price, no overlaying axis.
    assert fig.layout.yaxis2.overlaying is None
    assert max(fig.layout.yaxis2.domain) <= min(fig.layout.yaxis.domain)
    # One shared x-axis: the volume panel does not carry its own tick labels.
    assert fig.layout.xaxis2.matches == "x" or fig.layout.xaxis.rangebreaks


def test_an_all_zero_volume_column_is_not_plotted():
    """The capture writes 0 on every bid/ask row because IB sends -1 into a NOT
    NULL column. Drawing that would render a missing measurement as a real one."""
    fig = _box(_bars([0.0, 0.0, 0.0]))
    assert "Volume" not in _names(fig)
    # The secondary axis is never created, rather than created and left empty.
    assert "yaxis2" not in fig.layout


def test_a_frame_without_a_volume_column_still_charts():
    fig = _box(_bars([1000.0, 2000.0]).drop("volume"))
    assert "Stock price" in _names(fig)
    assert "Volume" not in _names(fig)


def test_the_forecast_bounds_are_drawn_when_supplied():
    plain = _box(_bars([1000.0, 1200.0]))
    both = _box(_bars([1000.0, 1200.0]), forecast_target=112.0, forecast_stop=96.0)
    # Each bound adds one horizontal line beyond the strike lines.
    assert len(both.layout.shapes) == len(plain.layout.shapes) + 2


def test_every_leg_shows_its_right_and_side():
    """At a glance, what the trade is: calls slot 1, puts slot 2, long solid,
    short dashed, each labelled in the trade's own notation. P&L doesn't color
    the legs — a losing condor is drawn exactly like a winning one."""
    from optionviz.ui import theme
    p = theme.palette()
    bars = _bars([1000.0, 1200.0, 1100.0])
    legs = ((1, "P", 95.0), (-1, "P", 98.0), (-1, "C", 104.0), (1, "C", 107.0))
    for pnl in (80.0, -80.0):
        fig = charts.trade_box(bars, bars["ts"][0], bars["ts"][-1], 107.0, 104.0, pnl, "t", legs=legs)
        drawn = {t.name: (t.line.color, t.line.dash) for t in fig.data if t.name.startswith(("long", "short"))}
        assert drawn == {
            "long 95P": (p.series_2, None), "short 98P": (p.series_2, "dash"),
            "short 104C": (p.series_1, "dash"), "long 107C": (p.series_1, None),
        }
        assert {a.text for a in fig.layout.annotations} >= {"+1P 95", "-1P 98", "-1C 104", "+1C 107"}


def test_a_spread_side_is_filled_and_a_straddle_is_not():
    """A right holding a long and a short leg is a spread: its zone fills in
    that right's color. A condor is two spreads; a straddle is none."""
    bars = _bars([1000.0, 1200.0])
    t0, t1 = bars["ts"][0], bars["ts"][-1]

    def fills(legs):
        fig = charts.trade_box(bars, t0, t1, 0.0, 0.0, 1.0, "t", legs=legs)
        return sorted((s.y0, s.y1) for s in fig.layout.shapes if s.type == "rect")

    condor = ((1, "P", 95.0), (-1, "P", 98.0), (-1, "C", 104.0), (1, "C", 107.0))
    assert fills(condor) == [(95.0, 98.0), (104.0, 107.0)]
    assert fills(((1, "C", 100.0), (1, "P", 100.0))) == []


def test_legs_parse_from_a_description():
    from optionviz.backtest import parse_legs
    assert parse_legs("SPY +1C 756 +1P 718 -1C 749 -1P 721") == (
        (1, "C", 756.0), (1, "P", 718.0), (-1, "C", 749.0), (-1, "P", 721.0)
    )
    assert parse_legs("TSLA +5C 445") == ((5, "C", 445.0),)


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
