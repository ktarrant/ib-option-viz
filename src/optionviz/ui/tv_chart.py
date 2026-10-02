"""The Chart page's price chart, on TradingView's Lightweight Charts.

The chart lives in the browser (`tv_chart.js`); this module builds what it
draws and is the only place that knows how the two sides line up:

- **Time.** Lightweight Charts has no time zones: it draws the seconds it is
  given as UTC. Bars are shifted to New York wall clock here, so the axis reads
  in exchange time and a DST change needs no handling at all. Daily bars go as
  business-day strings. Clicks come back as the bar's *real* UTC start (`keys`),
  never as a shifted time to undo.
- **Position.** The time scale is the bars themselves, one slot each — nights,
  weekends and holidays simply aren't slots. Anything placed between bars (a
  trade opened at 14:15 on an hourly chart) goes as a fractional bar index
  (`logical_x`), never as a time, which would add a phantom bar.
- **No lookahead.** A backtester row stamped `ts` is the bar *starting* then
  (ib-spread-trader's CLAUDE.md), so what it saw is known at `ts + bar_minutes`.
  Panes and the forecast fan are joined on that instant.
"""

from __future__ import annotations

import bisect
import html
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import streamlit as st

from . import charts, theme

TZ = "America/New_York"
_NY = ZoneInfo(TZ)

_HTML = """
<div class="tv">
  <div class="bar">
    <span class="ranges"></span>
    <button class="latest" title="Scroll to the latest bar">Latest ⇥</button>
  </div>
  <div class="wrap" tabindex="0">
    <div class="legend"></div>
    <div class="chart"></div>
  </div>
</div>
"""
_CSS = """
.tv { font-family: var(--st-font); color: var(--st-text-color); }
.bar { display: flex; gap: 4px; align-items: center; margin-bottom: 4px; }
.bar button { font: 12px var(--st-font); color: var(--st-text-color); cursor: pointer;
  background: transparent; border: 1px solid var(--st-border-color-light);
  border-radius: 4px; padding: 2px 8px; }
.bar button:hover, .bar button.on { background: var(--st-secondary-background-color); }
.bar button.on { border-color: var(--st-border-color); }
.bar .latest { margin-left: auto; visibility: hidden; }
.wrap { position: relative; outline: none; }
.wrap:focus-visible { box-shadow: 0 0 0 1px var(--st-border-color); }
.legend { position: absolute; top: 4px; left: 8px; z-index: 3; pointer-events: none;
  font: 12px/1.5 var(--st-font); font-variant-numeric: tabular-nums; }
.legend .m { opacity: .65; }
"""

_component = st.components.v2.component(
    "tv_chart", html=_HTML, css=_CSS, js=Path(__file__).with_name("tv_chart.js").read_text()
)

#: Model inputs a run logs per bar, as (label, scale, unit, decimals). `forecasts.csv`
#: writes an absent reading as an empty cell, so a column can exist and be all null.
PANES = {
    "rsi": ("RSI", 1.0, "", 1),
    "atm_iv": ("ATM IV", 100.0, "%", 1),
    "trend": ("Trend", 100.0, "%", 2),
    "prevailing_trend": ("Prevailing", 100.0, "%", 2),
    "realized_vol": ("Realized vol", 100.0, "%", 3),
}
#: Panes drawn as a histogram about zero, green above and red below. The trend
#: is the run's own reading, shown as logged: its definition is the
#: backtester's to change, and the chart shouldn't need to follow.
HISTOGRAMS = {"trend", "prevailing_trend"}
#: Inputs that are a distance from a price-level line, drawn as that line on
#: the price itself rather than as a ratio in a pane.
OVERLAYS = {"vwap_distance": "VWAP"}
#: Every model input the chart offers, in the order offered.
INPUTS = {"rsi": "RSI", "atm_iv": "ATM IV", **OVERLAYS, "trend": "Trend",
          "prevailing_trend": "Prevailing", "realized_vol": "Realized vol"}


def bar_times(ts: pl.Series, daily: bool) -> list:
    """Each bar's time as Lightweight Charts is given it: New York wall clock in
    epoch seconds, or for daily bars (stamped 00:00 UTC) the date itself."""
    if daily:
        return ts.dt.strftime("%Y-%m-%d").to_list()
    return (
        ts.dt.convert_time_zone(TZ).dt.replace_time_zone("UTC").dt.epoch("s").to_list()
    )


def bar_index(ts: list[datetime], instant: datetime) -> int:
    """The bar holding `instant`: the last to start at or before it (-1 if none)."""
    return bisect.bisect_right(ts, instant) - 1


def bar_containing_close(ts: list[datetime], as_of: datetime) -> int:
    """The bar whose span ends at or holds `as_of` — the bar a picked close
    belongs to, on whatever timeframe is now showing."""
    return bisect.bisect_left(ts, as_of) - 1


def logical_x(ts: list[datetime], instant: datetime, width: timedelta) -> float:
    """`instant` as a fractional bar index. Bar i spans i-0.5 .. i+0.5; an instant
    in a gap (overnight) sits at the end of the bar before it.

    Past the last bar the bars run out, so the position is extrapolated at the
    tape's average bars per calendar day. # ponytail: average density, a
    trading calendar if a fan's tip must land on the exact future bar.
    """
    i = bar_index(ts, instant)
    if i < 0:
        return -0.5 - (ts[0] - instant) / width
    end = ts[-1] + width
    if i == len(ts) - 1 and instant > end:
        per_day = len(ts) / max((end - ts[0]) / timedelta(days=1), 1.0)
        return len(ts) - 0.5 + (instant - end) / timedelta(days=1) * per_day
    return i - 0.5 + min((instant - ts[i]) / width, 1.0)


def _et(when: datetime) -> str:
    return f"{when.astimezone(_NY):%b %d %H:%M}"


def trades_payload(trades, ts: list[datetime], width: timedelta, selected: int | None) -> list[dict]:
    """Every trade overlapping the bars, as the Trades primitive draws it: legs,
    the zone of any right holding both a long and a short leg (a spread), and
    the forecast bounds — all of which widen the price axis while in view."""
    end = ts[-1] + width
    out = []
    for t in trades:
        if t.closed_at < ts[0] or t.opened_at >= end:
            continue
        zones = []
        for right in ("C", "P"):
            side = [(q, k) for q, r, k in t.legs if r == right]
            if any(q > 0 for q, _ in side) and any(q < 0 for q, _ in side):
                ks = [k for _, k in side]
                zones.append({"right": right, "lo": min(ks), "hi": max(ks)})
        out.append({
            "id": t.id,
            "x0": logical_x(ts, t.opened_at, width),
            "x1": logical_x(ts, t.closed_at, width),
            "legs": [{"qty": q, "right": r, "k": k, "text": f"{q:+d}{r} {k:g}"} for q, r, k in t.legs],
            "zones": zones,
            "target": t.forecast_target,
            "stop": t.forecast_stop,
            "selected": t.id == selected,
            "label": html.escape(
                f"#{t.id} {charts._legs_text(t.legs)} · {charts._money(t.pnl)} {t.exit_label}"
            ),
        })
    return out


def markers(trades, ts: list[datetime], times: list, width: timedelta,
            earnings: pl.DataFrame | None) -> tuple[list[dict], dict]:
    """Entry ▲ and exit ▼ for each trade (ids `t{id}.in` / `t{id}.out`, so a
    click on one selects the trade), and an "E" per confirmed earnings release.
    Colors are palette names; the browser resolves them for its theme.

    Returns the markers, time-sorted as the library requires, and the hover
    note for each earnings marker by id."""
    end = ts[-1] + width
    out, notes = [], {}
    for t in trades:
        if ts[0] <= t.opened_at < end:
            out.append({"time": times[bar_index(ts, t.opened_at)], "position": "belowBar",
                        "shape": "arrowUp", "color": "muted", "id": f"t{t.id}.in",
                        "text": f"#{t.id}"})
        if ts[0] <= t.closed_at < end:
            out.append({"time": times[bar_index(ts, t.closed_at)], "position": "aboveBar",
                        "shape": "arrowDown", "color": "good" if t.is_winner else "critical",
                        "id": f"t{t.id}.out", "text": f"{t.pnl:+,.0f}"})
    if earnings is not None and earnings.height:
        days = [t.date() for t in ts]
        for n, e in enumerate(earnings.iter_rows(named=True)):
            on_day = [i for i, d in enumerate(days) if d == e["event_date"]]
            if not on_day:
                continue
            # Before the open, the session's first bar reacts; otherwise its last.
            i = on_day[0] if e["session_timing"] == "before_open" else on_day[-1]
            out.append({"time": times[i], "position": "belowBar", "shape": "circle",
                        "color": "text_secondary", "id": f"e{n}", "text": "E"})
            notes[f"e{n}"] = html.escape(f"Earnings · {e['title']}")
    out.sort(key=lambda m: (m["time"], m["id"]))
    return out, notes


def known(forecasts: pl.DataFrame, run_minutes: int) -> pl.DataFrame:
    """`forecasts.csv` rows with `known_at`: the close of the bar each was made on."""
    return forecasts.with_columns(
        pl.col("ts").dt.cast_time_unit("us"),
        (pl.col("ts") + pl.duration(minutes=run_minutes)).dt.cast_time_unit("us").alias("known_at"),
    ).sort("known_at")


def available_inputs(forecasts: pl.DataFrame | None) -> list[str]:
    if forecasts is None or not forecasts.height:
        return []
    return [k for k in INPUTS if k in forecasts.columns
            and forecasts[k].null_count() < forecasts.height]


def _at_closes(bars: pl.DataFrame, width: timedelta, frame: pl.DataFrame,
               tolerance: timedelta | None) -> pl.DataFrame:
    """`frame` (keyed by `known_at`) valued at each bar's close: the latest row
    known by then, or null when none is within `tolerance`."""
    closes = bars.select((pl.col("ts").dt.cast_time_unit("us") + width).alias("close"))
    return closes.join_asof(frame, left_on="close", right_on="known_at",
                            strategy="backward", tolerance=tolerance)


def vwap_line(bars: pl.DataFrame, width: timedelta, forecasts: pl.DataFrame,
              tolerance: timedelta) -> list[float | None]:
    """The run's own session VWAP, in dollars, at each bar. Exact, not
    recomputed: the backtester defines `vwap_distance` as (spot − vwap) / vwap
    and logs `spot` beside it, so vwap = spot / (1 + distance)."""
    frame = forecasts.select(
        "known_at", (pl.col("spot") / (1 + pl.col("vwap_distance"))).alias("vwap")
    )
    return _at_closes(bars, width, frame, tolerance)["vwap"].to_list()


def overlays(bars: pl.DataFrame, width: timedelta, keys, forecasts: pl.DataFrame,
             tolerance: timedelta, daily: bool) -> list[dict]:
    """The price-level lines for the chosen overlay inputs, in palette slot 3 —
    the next after calls and puts. `sessions` breaks a line at each session
    open (VWAP resets daily) — not on a daily chart, where every bar is one."""
    if "vwap_distance" not in keys:
        return []
    return [{"label": "VWAP", "color": "series_3", "sessions": not daily,
             "values": vwap_line(bars, width, forecasts, tolerance)}]


def model_panes(bars: pl.DataFrame, width: timedelta, forecasts: pl.DataFrame,
                keys: list[str], tolerance: timedelta, rsi_level: float | None) -> list[dict]:
    """One pane per model input, valued at each bar's close with the latest
    reading known by then. Forecasts are sparser than bars, so a bar with no
    reading within `tolerance` is a gap — never the last value carried forward."""
    joined = _at_closes(bars, width, forecasts.select("known_at", *keys), tolerance)
    out = []
    for k in keys:
        label, scale, unit, decimals = PANES[k]
        guides = []
        if k == "rsi" and rsi_level is not None:
            guides = [rsi_level, 100 - rsi_level]
        out.append({
            "key": k, "label": label, "unit": unit, "decimals": decimals, "guides": guides,
            "histogram": k in HISTOGRAMS,
            "values": [None if v is None else v * scale for v in joined[k].to_list()],
        })
    return out


def weighted_inputs(model: dict) -> list[str]:
    """The inputs a ranked run's model actually leans on, from its config: what
    a run opens with on screen. The prevailing trend counts only as the gate on
    the trend's fade (`trend_with_prevailing_only`)."""
    on = {
        "trend": model.get("trend_weight"),
        "prevailing_trend": model.get("trend_weight") and model.get("trend_with_prevailing_only"),
        "vwap_distance": model.get("vwap_reversion"),
    }
    return [k for k in INPUTS if on.get(k)]


def pnl_pane(bars: pl.DataFrame, width: timedelta, marks: pl.DataFrame, run_minutes: int,
             tolerance: timedelta, trade_id: int) -> dict:
    """One position's gross P&L at each bar's close, from `positions.csv` marks
    (bar starts, known at their close). Nothing after its last mark: a closed
    position has no P&L to carry forward."""
    frame = known(marks.select("ts", "gross_pnl"), run_minutes).select("known_at", "gross_pnl")
    last = frame["known_at"].max()
    joined = _at_closes(bars, width, frame, tolerance)
    values = [v if c <= last else None for c, v in zip(joined["close"], joined["gross_pnl"])]
    # The trade id is in the key so the pane is rebuilt as the selection moves.
    return {"key": f"pnl{trade_id}", "label": "P&L $", "unit": "", "decimals": 0,
            "guides": [0], "histogram": False, "values": values}


def forecast_fan(forecasts: pl.DataFrame, as_of: datetime, ts: list[datetime],
                 width: timedelta) -> dict | None:
    """The model's forecast as it stood at `as_of`: the latest row known by
    then, drawn from that bar to its horizon. Straight edges between spot and
    the horizon's quantiles — the run logs only the endpoint, so the path
    between is not drawn as if it were known.

    None when the latest forecast is from an earlier session: that is not the
    forecast "at this bar", however recent it looks on the chart."""
    rows = forecasts.filter(pl.col("known_at") <= as_of)
    if not rows.height:
        return None
    r = rows.row(-1, named=True)
    if r["known_at"].date() != (as_of - timedelta(microseconds=1)).date():
        return None
    made = r["ts"]
    target = made + timedelta(days=r["horizon_years"] * 365.25)
    realized = r.get("realized")
    pit = r.get("pit")
    legend = (
        f"Forecast {_et(made)} → {_et(target)[:6]} · median {r['q50']:,.2f} · "
        f"90% {r['q05']:,.2f}–{r['q95']:,.2f}"
        + ("" if realized is None else f" · realized {realized:,.2f}")
        + ("" if pit is None else f" (PIT {pit:.2f})")
    )
    return {
        "x0": logical_x(ts, made, width), "x1": logical_x(ts, target, width),
        "spot": r["spot"], "q05": r["q05"], "q25": r["q25"], "q50": r["q50"],
        "q75": r["q75"], "q95": r["q95"], "realized": realized, "legend": legend,
    }


def open_at(bars: pl.DataFrame, trades) -> list[str]:
    """Per bar, the trades open in it, as legend lines."""
    return ["<br>".join(html.escape(line) for line in text.split("<br>") if line)
            for text in charts._open_at(bars, trades)]


def payload(
    *,
    symbol: str,
    timeframe: str,
    bars: pl.DataFrame,
    width: timedelta,
    daily: bool,
    trades=(),
    selected_trade: int | None = None,
    as_of: datetime | None = None,
    earnings: pl.DataFrame | None = None,
    forecasts: pl.DataFrame | None = None,
    pane_keys=(),
    fan: bool = False,
    run_minutes: int = 30,
    cadence: int = 30,
    rsi_level: float | None = None,
    opening_days: int | None = None,
    focus: dict | None = None,
    marks: pl.DataFrame | None = None,
) -> dict:
    """Everything the chart draws, as plain JSON. `forecasts` is `known()` output
    for this symbol; `pane_keys` the chosen model inputs, each drawn as a pane
    or, for `OVERLAYS`, as a line on the price; `focus` a one-shot
    {from, to, nonce} scroll in bar indices; `marks` the selected trade's
    `position_marks`, drawn as a P&L pane beneath the inputs."""
    ts = bars["ts"].to_list()
    times = bar_times(bars["ts"], daily)
    keys = [t.isoformat() for t in ts]
    has_volume = "volume" in bars.columns and (bars["volume"].sum() or 0) > 0
    mks, notes = markers(trades, ts, times, width, earnings)
    selected = None
    if as_of is not None:
        i = bar_containing_close(ts, as_of)
        selected = i if 0 <= i < len(ts) else None
    # The model reads once per `cadence` minutes, however fine the option grid:
    # a bar is a gap only when no reading lands within a step of it.
    tolerance = max(width, timedelta(minutes=max(run_minutes, cadence)))
    opening = 0
    if opening_days:
        opening = bisect.bisect_left(ts, ts[-1] - timedelta(days=opening_days))
    return {
        "view": f"{symbol}|{timeframe}|{len(ts)}|{keys[-1]}",
        "symbol": symbol,
        "title": f"{symbol} · {timeframe}",
        "daily": daily,
        "keys": keys,
        "bars": [
            {"time": t, "open": o, "high": h, "low": lo, "close": c}
            for t, o, h, lo, c in zip(times, bars["open"], bars["high"], bars["low"], bars["close"])
        ],
        "volume": bars["volume"].to_list() if has_volume else None,
        "opening": opening,
        "trades": trades_payload(trades, ts, width, selected_trade),
        "markers": mks,
        "notes": notes,
        "open_at": open_at(bars, trades) if trades else None,
        "selected": selected,
        "panes": (model_panes(bars, width, forecasts, [k for k in pane_keys if k in PANES],
                              tolerance, rsi_level)
                  if forecasts is not None and pane_keys else [])
        + ([pnl_pane(bars, width, marks, run_minutes, tolerance, selected_trade)]
           if marks is not None and marks.height else []),
        "overlays": overlays(bars, width, pane_keys, forecasts, tolerance, daily)
        if forecasts is not None and pane_keys else [],
        "fan": forecast_fan(forecasts, as_of, ts, width)
        if fan and forecasts is not None and as_of is not None else None,
        "focus": focus,
        "palettes": {"light": asdict(theme.LIGHT), "dark": asdict(theme.DARK)},
    }


def render(data: dict, key: str, on_pick, on_trade, on_clear):
    """Mount the chart. Its events arrive through the callbacks, which read the
    value from `st.session_state[key]`: `pick` (a bar's UTC start, ISO),
    `trade` (an id) and `clear`."""
    return _component(
        data=data, key=key,
        on_pick_change=on_pick, on_trade_change=on_trade, on_clear_change=on_clear,
    )
