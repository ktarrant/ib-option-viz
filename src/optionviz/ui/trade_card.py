"""One trade's card — header, numbers, what the model saw, and its charts.

Shared by the Backtest page (every trade) and the Chart page (the selected one).
"""

from __future__ import annotations

from datetime import timedelta

import polars as pl
import streamlit as st

from optionviz.backtest import Trade

from . import charts, theme

PADDING = timedelta(days=5)


def render(trade: Trade, bars: pl.DataFrame, marks: pl.DataFrame, key: str,
           chart: bool = True) -> None:
    """`bars` are the symbol's bars on the run's own grid; `marks` the run's
    `position_marks`, where an empty frame means "not recorded". `chart=False`
    leaves out the price chart, for a page that already draws the trade."""
    p = theme.palette()
    pnl_color = p.good if trade.is_winner else p.critical

    synthetic_badge = (
        f' &nbsp; {theme.badge("⚠ Intrinsic", p.warning)}'
        if trade.synthetic_price
        else ""
    )
    earnings_badge = (
        f' &nbsp; {theme.badge("Held through earnings", p.warning, subtle=True)}'
        if trade.held_through_earnings
        else ""
    )
    expiry = f" &nbsp;<small>exp {trade.expiry}</small>" if trade.expiry else ""
    st.markdown(
        f"**#{trade.id} {trade.description}**{expiry} &nbsp; "
        f'{theme.badge(trade.exit_label, pnl_color)} &nbsp; '
        f'{theme.badge(f"${trade.pnl:+,.2f}", pnl_color, subtle=True)}'
        f"{synthetic_badge}{earnings_badge}",
        unsafe_allow_html=True,
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Entry", f"${trade.entry_debit:,.2f}")
    m2.metric("Exit", f"${trade.exit_credit:,.2f}")
    m3.metric("Commission", f"${trade.commission:,.2f}")
    m4.metric("Bars held", f"{trade.bars_held}")

    # What the ranker concluded at entry. `score` is the ranker's own metric —
    # expected P/L per unit of risk — so it is shown as such rather than as a
    # bare number. Absent for a staged run, which makes no forecast.
    if trade.prediction:
        pred = trade.prediction
        q1, q2, q3 = st.columns(3)
        q1.metric("E[P/L] per risk", f"{pred['score']:+.3f}")
        q2.metric("Prob. of profit", f"{pred['prob_profit']:.0%}")
        q3.metric("Expected P/L", f"${pred['expected_pl']:+,.0f}")

    # What the model *saw* on the bar it decided on. A missing reading is shown
    # as "—": the writer omits absent features rather than sending zero, and a
    # zero here would read as a measurement.
    if trade.entry_features:
        f = trade.entry_features

        def _fmt(key: str, spec: str, scale: float = 1.0) -> str:
            v = f.get(key)
            return "—" if v is None else format(v * scale, spec)

        st.caption(
            "At entry &nbsp;·&nbsp; "
            f"spot {_fmt('spot', ',.2f')} &nbsp;·&nbsp; "
            f"RSI {_fmt('rsi', '.0f')} &nbsp;·&nbsp; "
            f"trend {_fmt('trend', '+.2f', 100)}% &nbsp;·&nbsp; "
            f"prevailing {_fmt('prevailing_trend', '+.2f', 100)}% &nbsp;·&nbsp; "
            f"VWAP dist {_fmt('vwap_distance', '+.2f', 100)}% &nbsp;·&nbsp; "
            f"ATM IV {_fmt('atm_iv', '.0f', 100)}% &nbsp;·&nbsp; "
            f"skew {_fmt('skew', '+.1f', 100)} &nbsp;·&nbsp; "
            f"ATR {_fmt('atr', ',.2f')} &nbsp;·&nbsp; "
            + (
                f"earnings in {f['days_to_earnings']:.0f}d"
                if f.get("days_to_earnings") is not None
                else "no earnings ahead"
            ),
            unsafe_allow_html=True,
        )

    window = bars.filter(
        (pl.col("ts") >= trade.opened_at - PADDING)
        & (pl.col("ts") <= trade.closed_at + PADDING)
    )
    if chart and window.height > 0:
        st.plotly_chart(
            charts.trade_box(
                window,
                trade.opened_at, trade.closed_at,
                trade.long_strike, trade.short_strike,
                trade.pnl,
                f"{trade.symbol}  {trade.opened_at.strftime('%b %d')} → {trade.closed_at.strftime('%b %d')}",
                forecast_target=trade.forecast_target,
                forecast_stop=trade.forecast_stop,
                legs=trade.legs,
            ),
            width="stretch", key=f"trade_{key}",
        )

    # The dynamic exits cannot be drawn on the price chart: a trailing stop is a
    # P&L level riding the position's own peak, not a price. Its own panel is
    # where it belongs, and it appears only when the rule was actually armed.
    if marks.height:
        track = marks.filter(pl.col("position_id") == trade.id)
        if track.height and track["trailing_stop"].null_count() < track.height:
            st.plotly_chart(
                charts.position_track(track, trade.pnl, "Gross P&L and the trailing stop"),
                width="stretch", key=f"track_{key}",
            )
