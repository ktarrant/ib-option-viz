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
