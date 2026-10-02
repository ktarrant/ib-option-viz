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
        exit_reason="profit_target", long_strike=105.0, short_strike=110.0,
        synthetic_price=False,
    )


def _break_kinds(fig) -> list:
    return [b.pattern or ("values" if b.values else "bounds") for b in fig.layout.xaxis.rangebreaks]


def test_daily_bars_are_not_hidden_by_the_overnight_break():
    """Daily bars are stamped 00:00 UTC, inside the overnight break: keeping it
    would hide every bar on a daily chart."""
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    bars = _bars([1.0, 2.0]).with_columns(pl.Series("ts", [start, start + timedelta(days=1)]))
    assert "hour" not in _break_kinds(charts.symbol_chart(bars, "t", daily=True))
    assert "hour" in _break_kinds(charts.symbol_chart(bars, "t"))


def test_an_intraday_holiday_breaks_only_its_session():
    """A whole-day holiday break overlaps the overnight one, and Plotly then
    draws the neighbouring day twice, side by side."""
    thu = datetime(2026, 6, 18, 13, 30, tzinfo=timezone.utc)
    bars = _bars([1.0, 2.0]).with_columns(pl.Series("ts", [thu, thu + timedelta(days=4)]))
    (holiday,) = [b for b in charts.symbol_chart(bars, "t").layout.xaxis.rangebreaks if b.values]
    assert list(holiday.values) == ["2026-06-19 13:30"]
    assert holiday.dvalue == 7 * 3600 * 1000


def test_a_bar_lists_exactly_the_trades_open_during_it():
    """Hover text comes from the trades' own times. Left to Plotly, a leg
    (a two-point line) answered from its nearest endpoint: just outside the
    trade, and never in the middle of it."""
    bars = _bars([1.0] * 6)  # 13:30 ... 16:00, every 30 minutes
    ts = bars["ts"].to_list()
    first = _trade(bars.slice(1, 2), 1, 50.0)   # held 14:00-14:30
    second = _trade(bars.slice(3, 3), 2, -20.0)  # held 15:00-16:00
    fig = charts.symbol_chart(bars, "t", trades=(first, second))
    (candles,) = [t for t in fig.data if t.type == "candlestick"]
    held = [[line.split()[0] for line in text.split("<br>") if line] for text in candles.text]
    assert held == [[], ["#1"], ["#1"], ["#2"], ["#2"], ["#2"]]
    assert "opened" in candles.text[1] and "closed +$50.00" in candles.text[2]
    assert all(t.hoverinfo == "skip" for t in fig.data if t.name.startswith(("long", "short")))
    assert ts[0] < first.opened_at
