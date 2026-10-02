"""The Chart page's payload builders — what the browser is told to draw.

The drawing itself lives in `tv_chart.js` and is checked by rendering it; these
pin the claims the Python side makes about time, position and lookahead.
"""

from datetime import datetime, timedelta, timezone

import polars as pl

from optionviz.backtest import Trade
from optionviz.ui import tv_chart

UTC = timezone.utc
HALF = timedelta(minutes=30)
HOUR = timedelta(hours=1)


def _session(day: datetime, step: timedelta) -> list[datetime]:
    """One regular session's bar starts, 13:30-20:00 UTC (summer time)."""
    start = day.replace(hour=13, minute=30, tzinfo=UTC)
    return [start + step * i for i in range(int(6.5 * HOUR / step))]


def _bars(ts: list[datetime]) -> pl.DataFrame:
    n = len(ts)
    return pl.DataFrame({
        "ts": pl.Series(ts).dt.cast_time_unit("us"),
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [10.0] * n,
    })


def _trade(id: int, opened: datetime, closed: datetime, description: str, pnl: float = 10.0):
    from optionviz.backtest import parse_legs
    return Trade(
        id=id, symbol="T", description=description, opened_at=opened, closed_at=closed,
        bars_held=1, entry_debit=1.0, exit_credit=1.0, commission=0.0, pnl=pnl,
        exit_reason="profit_target", long_strike=0.0, short_strike=0.0,
        synthetic_price=False, forecast_target=120.0, legs=parse_legs(description),
    )


def test_the_axis_reads_new_york_wall_clock_on_both_sides_of_dst():
    """The open is 13:30 UTC in summer and 14:30 UTC in winter; both are 09:30
    on the chart, so no session break or DST rule is needed anywhere."""
    ts = pl.Series([datetime(2026, 10, 30, 13, 30, tzinfo=UTC),
                    datetime(2026, 11, 2, 14, 30, tzinfo=UTC)])
    shown = [datetime.fromtimestamp(s, UTC) for s in tv_chart.bar_times(ts, daily=False)]
    assert [s.strftime("%H:%M") for s in shown] == ["09:30", "09:30"]


def test_daily_bars_are_business_days():
    ts = pl.Series([datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 2, tzinfo=UTC)])
    assert tv_chart.bar_times(ts, daily=True) == ["2026-07-01", "2026-07-02"]


def test_a_quarter_hour_lands_inside_an_hourly_bar_not_on_a_new_one():
    """A 15-minute-grid trade on an hourly chart sits a quarter into its bar:
    as a time it would have become a phantom bar of its own."""
    ts = _session(datetime(2026, 8, 3), HOUR)
    assert tv_chart.logical_x(ts, ts[2] + timedelta(minutes=15), HOUR) == 2 - 0.5 + 0.25
    assert tv_chart.logical_x(ts, ts[0], HOUR) == -0.5


def test_an_overnight_instant_sits_at_the_end_of_the_session():
    day1 = _session(datetime(2026, 8, 3), HALF)
    ts = day1 + _session(datetime(2026, 8, 4), HALF)
    night = datetime(2026, 8, 4, 2, 0, tzinfo=UTC)
    assert tv_chart.logical_x(ts, night, HALF) == len(day1) - 1 + 0.5


def test_past_the_last_bar_the_position_keeps_going():
    """A forecast's horizon often lies past the tape: its tip is extrapolated,
    not pinned to the last bar."""
    ts = _session(datetime(2026, 8, 3), HALF) + _session(datetime(2026, 8, 4), HALF)
    later = tv_chart.logical_x(ts, ts[-1] + timedelta(days=3), HALF)
    assert later > len(ts)


def test_a_picked_close_highlights_the_bar_it_closes_on_every_timeframe():
    """The pick is the close of a bar. On a coarser timeframe it is the bar
    holding that instant; on the same one, the bar it closed."""
    halves = _session(datetime(2026, 8, 3), HALF)
    hours = _session(datetime(2026, 8, 3), HOUR)
    as_of = halves[3] + HALF  # 15:30 UTC, the close of the 15:00 half-hour
    assert tv_chart.bar_containing_close(halves, as_of) == 3
    assert hours[tv_chart.bar_containing_close(hours, as_of)] == hours[1]  # 14:30-15:30


def test_a_spread_has_a_zone_and_its_legs_widen_the_axis():
    ts = _session(datetime(2026, 8, 3), HALF)
    spread = _trade(1, ts[1], ts[4], "T +1C 105 -1C 110")
    straddle = _trade(2, ts[2], ts[5], "T +1C 100 +1P 100")
    old = _trade(3, ts[0] - timedelta(days=9), ts[0] - timedelta(days=8), "T +1C 90")
    got = {t["id"]: t for t in tv_chart.trades_payload((spread, straddle, old), ts, HALF, 1)}
    assert set(got) == {1, 2}  # one wholly outside the bars isn't sent
    assert got[1]["zones"] == [{"right": "C", "lo": 105.0, "hi": 110.0}]
    assert got[2]["zones"] == []
    assert got[1]["selected"] and not got[2]["selected"]
    assert got[1]["target"] == 120.0  # forecast bounds travel with the trade


def test_trade_markers_carry_the_trade_id_and_sit_on_bars():
    ts = _session(datetime(2026, 8, 3), HALF)
    times = tv_chart.bar_times(pl.Series(ts), daily=False)
    t = _trade(7, ts[1] + timedelta(minutes=15), ts[4], "T +1C 105", pnl=-5.0)
    marks, _ = tv_chart.markers((t,), ts, times, HALF, None)
    assert [(m["id"], m["time"]) for m in marks] == [("t7.in", times[1]), ("t7.out", times[4])]
    assert marks[1]["color"] == "critical"


def _forecasts(rows: list[tuple[datetime, float]], minutes: int = 15) -> pl.DataFrame:
    n = len(rows)
    return tv_chart.known(pl.DataFrame({
        "ts": pl.Series([r[0] for r in rows]),
        "rsi": [r[1] for r in rows],
        "spot": [100.0] * n, "horizon_years": [7 / 365.25] * n,
        "q05": [90.0] * n, "q25": [95.0] * n, "q50": [100.0] * n,
        "q75": [105.0] * n, "q95": [110.0] * n, "realized": [103.0] * n, "pit": [0.6] * n,
    }), minutes)


def test_a_pane_reads_only_what_was_known_by_each_close_and_leaves_gaps():
    """A reading stamped 14:00 is the bar *starting* then: known at 14:15. Bars
    with no reading within the tolerance stay empty rather than repeating."""
    ts = _session(datetime(2026, 8, 3), HALF)  # closes 14:00, 14:30, 15:00, ...
    f = _forecasts([(ts[1], 40.0)])            # made on the 14:00 bar, known 14:15
    (pane,) = tv_chart.model_panes(_bars(ts), HALF, f, ["rsi"], HALF, rsi_level=30.0)
    assert pane["values"][:4] == [None, 40.0, None, None]
    assert pane["guides"] == [30.0, 70.0]


def test_the_fan_never_shows_a_forecast_made_after_the_pick():
    ts = _session(datetime(2026, 8, 3), HALF)
    f = _forecasts([(ts[1], 40.0), (ts[5], 60.0)])
    fan = tv_chart.forecast_fan(f, ts[5] + timedelta(minutes=10), ts, HALF)
    assert fan["x0"] == tv_chart.logical_x(ts, ts[1], HALF)  # ts[5]'s isn't known until +15m
    assert fan["realized"] == 103.0 and fan["x1"] > len(ts)


def test_a_forecast_from_an_earlier_session_is_not_the_one_at_this_bar():
    day1 = _session(datetime(2026, 8, 3), HALF)
    ts = day1 + _session(datetime(2026, 8, 4), HALF)
    f = _forecasts([(day1[-2], 40.0)])
    assert tv_chart.forecast_fan(f, ts[len(day1) + 2], ts, HALF) is None


def test_a_run_without_readings_offers_no_panes():
    """No forecasts.csv, or a column the writer left empty throughout, is not a
    pane: an empty one would read as "the model saw nothing"."""
    assert tv_chart.available_inputs(None) == []
    f = pl.DataFrame({"ts": [datetime(2026, 8, 3, tzinfo=UTC)], "rsi": [None], "atm_iv": [0.2]})
    assert tv_chart.available_inputs(f) == ["atm_iv"]


def test_vwap_is_the_runs_own_not_recomputed():
    """The backtester logs vwap_distance = (spot - vwap) / vwap beside spot, so
    its VWAP comes back exactly: here 101 at +1% is a VWAP of 100. Like any
    reading it holds for one step of the model's cadence, then gaps."""
    ts = _session(datetime(2026, 8, 3), HALF)
    f = tv_chart.known(pl.DataFrame({
        "ts": pl.Series([ts[0]]), "spot": [101.0], "vwap_distance": [0.01],
    }), 30)
    assert tv_chart.vwap_line(_bars(ts[:3]), HALF, f, HALF) == [100.0, 100.0, None]


def test_vwap_breaks_at_each_session_but_not_on_a_daily_chart():
    ts = _session(datetime(2026, 8, 3), HALF)
    f = tv_chart.known(pl.DataFrame({"ts": pl.Series(ts), "spot": [101.0] * len(ts),
                                     "vwap_distance": [0.01] * len(ts)}), 30)
    intraday = tv_chart.overlays(_bars(ts), HALF, ["vwap_distance"], f, HALF, False)
    daily = tv_chart.overlays(_bars(ts), HALF, ["vwap_distance"], f, HALF, True)
    assert intraday[0]["sessions"] and not daily[0]["sessions"]


def test_the_trend_is_a_histogram_pane_of_the_runs_own_reading():
    ts = _session(datetime(2026, 8, 3), HALF)
    f = tv_chart.known(pl.DataFrame({"ts": pl.Series(ts[:2]), "trend": [0.01, -0.02]}), 30)
    (pane,) = tv_chart.model_panes(_bars(ts[:2]), HALF, f, ["trend"], HALF, None)
    assert pane["histogram"] and pane["values"] == [1.0, -2.0]
