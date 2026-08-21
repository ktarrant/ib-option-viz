"""The chart vocabulary for the event views.

Each function returns a styled Plotly figure. Two measures on different scales
(price and vol) are always two stacked figures sharing an x-axis — never one
plot with two y-axes, which invents a correlation the data does not contain.
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import polars as pl

from . import theme


def _boundary_marker(fig: go.Figure, ts_after, p, label: str | None = None) -> None:
    """Mark the bar the event crossed.

    A line, not a shaded band: with non-trading time collapsed off the axis the
    boundary is a seam, not a duration.
    """
    fig.add_vline(
        x=ts_after, line=dict(color=p.text_secondary, width=1),
        annotation_text=label, annotation_position="top left",
        annotation_font=dict(size=10, color=p.text_secondary),
    )


def forward_series(window: pl.DataFrame, ts_before, ts_after, title: str) -> go.Figure:
    """Reconstructed forward price across the event window."""
    p = theme.palette()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=window["ts"].to_list(), y=window["forward"].to_list(),
        mode="lines", line=dict(color=p.series_1, width=2),
        name="Forward", hovertemplate="%{y:.2f}<extra></extra>",
    ))
    _boundary_marker(fig, ts_after, p, "event")
    fig.update_yaxes(tickformat=",.2f")
    return theme.trading_clock(theme.style(fig, height=240)).update_layout(title=title)


def atm_iv_series(window: pl.DataFrame, ts_before, ts_after, title: str) -> go.Figure:
    """ATM implied vol on its own axis, stacked under the price."""
    p = theme.palette()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=window["ts"].to_list(), y=(window["atm_iv"] * 100).to_list(),
        mode="lines", line=dict(color=p.series_2, width=2),
        name="ATM IV", hovertemplate="%{y:.1f}%<extra></extra>",
    ))
    _boundary_marker(fig, ts_after, p)
    fig.update_yaxes(ticksuffix="%", tickformat=".0f")
    return theme.trading_clock(theme.style(fig, height=210)).update_layout(title=title)


def strike_ladder(ladder: pl.DataFrame, title: str) -> go.Figure:
    """Percent change of every strike's call and put across the boundary.

    The strike axis is categorical, not linear: listed strikes are unevenly
    spaced (2.5 apart near the money, 5 out in the wings), so a linear axis
    crowds rows together and cannot label them all.

    Calls are always slot 1 and puts always slot 2 regardless of what is on
    screen, so the reader's colour learning survives a filter change.
    """
    p = theme.palette()
    order = [f"{k:g}" for k in sorted(ladder["strike"].unique().to_list())]
    ladder = ladder.with_columns(
        pl.col("strike").map_elements(lambda k: f"{k:g}", return_dtype=pl.Utf8).alias("k")
    )

    fig = go.Figure()
    for right, name, color in (("C", "Calls", p.series_1), ("P", "Puts", p.series_2)):
        side = ladder.filter(pl.col("right") == right).sort("strike")
        if not side.height:
            continue
        fig.add_trace(go.Bar(
            y=side["k"].to_list(), x=(side["pct_change"] * 100).to_list(),
            name=name, orientation="h",
            marker=dict(color=color, line=dict(width=0)),
            hovertemplate=f"{name} %{{y}}<br>%{{x:+.0f}}%<extra></extra>",
        ))
    fig.add_vline(x=0, line=dict(color=p.axis, width=1))
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.1)
    fig.update_xaxes(showgrid=True, gridcolor=p.grid, ticksuffix="%", zeroline=False)
    fig.update_yaxes(
        type="category", categoryorder="array", categoryarray=order,
        showgrid=False, title=dict(text="Strike", font=dict(size=11)),
    )
    height = max(300, 19 * len(order) + 96)
    return theme.style(fig, height=height, showlegend=True, left=64).update_layout(title=title)


def smile(before: pl.DataFrame, after: pl.DataFrame, title: str) -> go.Figure:
    """Implied vol against log-moneyness, before and after."""
    p = theme.palette()
    fig = go.Figure()
    for frame, name, color in ((before, "Before", p.series_1), (after, "After", p.series_2)):
        f = frame.drop_nulls("iv").sort("log_moneyness")
        if not f.height:
            continue
        fig.add_trace(go.Scatter(
            x=(f["log_moneyness"] * 100).to_list(), y=(f["iv"] * 100).to_list(),
            mode="lines+markers", name=name,
            line=dict(color=color, width=2), marker=dict(size=5, color=color),
            hovertemplate=f"{name}<br>%{{x:+.1f}}%: %{{y:.0f}}%<extra></extra>",
        ))
    fig.add_vline(x=0, line=dict(color=p.axis, width=1))
    fig.update_xaxes(
        title=dict(text="Log-moneyness", font=dict(size=11), standoff=8), ticksuffix="%"
    )
    fig.update_yaxes(ticksuffix="%", tickformat=".0f")
    return theme.style(fig, height=280, showlegend=True, bottom=58).update_layout(title=title)
