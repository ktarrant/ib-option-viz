"""The chart vocabulary for the event views.

Each function returns a styled Plotly figure. Two measures on different scales
(price and vol) are always two stacked figures sharing an x-axis — never one
plot with two y-axes, which invents a correlation the data does not contain.
"""

from __future__ import annotations

from datetime import timedelta

import pandas  # noqa: F401  -- see the forced attribute access below
import plotly.graph_objects as go
import polars as pl

from . import theme

# pandas 3.0 materialises `Series` lazily on first attribute access. Plotly probes
# `pd.Series` when it validates array data, and the very first chart built after a
# fresh server start triggers that materialisation *inside* plotly's import
# machinery, where it re-enters a partially-initialised pandas ("partially
# initialized module 'pandas' ... has no attribute 'Series'"). Touching it here,
# at import time and outside that re-entrancy, forces a clean materialisation
# before any figure is built.
_ = pandas.Series


def _boundary_marker(fig: go.Figure, ts_after, p, label: str | None = None) -> None:
    """Mark the bar the event crossed.

    A line, not a shaded band: with non-trading time collapsed off the axis the
    boundary is a seam, not a duration.
    """
    # Without a label, no annotation at all: given None, Plotly writes "new text".
    fig.add_vline(
        x=ts_after, line=dict(color=p.text_secondary, width=1),
        **(dict(annotation_text=label, annotation_position="top left",
                annotation_font=dict(size=10, color=p.text_secondary)) if label else {}),
    )


def price_series(window: pl.DataFrame, ts_before, ts_after, title: str) -> go.Figure:
    """Underlying price across the event window.

    Real captured spot is the primary line where the snapshot has it. The
    put-call-parity forward draws alongside as a thin reference - the two
    normally track within basis points, so the interesting case is where they
    visibly part ways for a bar or two right at the event, which is the
    parity fit going briefly noisy while a handful of strikes reprice, not a
    real move spot doesn't share. Falls back to forward alone (solid) when a
    chain has no spot capture at all.
    """
    p = theme.palette()
    has_spot = window["spot_mid"].drop_nulls().len() > 0
    fig = go.Figure()
    if has_spot:
        fig.add_trace(go.Scatter(
            x=window["ts"].to_list(), y=window["spot_mid"].to_list(),
            mode="lines", line=dict(color=p.series_1, width=2), connectgaps=False,
            name="Stock price", hovertemplate="%{y:.2f}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=window["ts"].to_list(), y=window["forward"].to_list(),
            mode="lines", line=dict(color=p.muted, width=1, dash="dot"),
            name="Forward (parity)", hovertemplate="%{y:.2f}<extra></extra>",
        ))
    else:
        fig.add_trace(go.Scatter(
            x=window["ts"].to_list(), y=window["forward"].to_list(),
            mode="lines", line=dict(color=p.series_1, width=2),
            name="Forward (parity)", hovertemplate="%{y:.2f}<extra></extra>",
        ))
    _boundary_marker(fig, ts_after, p, "event")
    fig.update_yaxes(tickformat=",.2f")
    return theme.trading_clock(theme.style(fig, height=240, showlegend=has_spot)).update_layout(title=title)


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


def _legs_text(legs: tuple) -> str:
    return " ".join(f"{q:+d}{r} {k:g}" for q, r, k in legs)


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}${abs(x):,.2f}"


def _open_at(bars: pl.DataFrame, trades) -> list[str]:
    """Per bar, the trades open during it — marked "opened" or "closed" on the
    bar that happened in — as hover text. Computed from the trades' own times, so a bar
    lists exactly what was held then: Plotly's unified hover, left to find the
    legs itself, matched them by nearest point instead."""
    ts = bars["ts"].to_list()
    if not ts:
        return []
    width = ts[-1] - ts[-2] if len(ts) > 1 else timedelta(minutes=30)
    out = []
    for start, end in zip(ts, ts[1:] + [ts[-1] + width]):
        lines = []
        for t in trades:
            if t.opened_at < end and t.closed_at >= start:
                event = " · opened" if start <= t.opened_at < end else ""
                if start <= t.closed_at < end:
                    event += f" · closed {_money(t.pnl)} {t.exit_label}"
                lines.append(f"#{t.id} {_legs_text(t.legs)}{event}")
        out.append("<br>".join(lines))
    return out


def position_track(marks: pl.DataFrame, pnl: float, title: str) -> go.Figure:
    """A position's gross P&L over its life, with the trailing stop riding beneath.

    The trailing stop is a *P&L* level, not a price — it sits a fixed fraction
    below the running peak, so it moves every bar and cannot be drawn as a line
    on a price chart. Its own panel is the honest place for it, and the gap
    between the two lines is the giveback the rule allows.

    The peak is drawn as well, dotted: without it the stop looks arbitrary, and
    with it the mechanism reads directly — stop tracks peak up, never down.
    """
    p = theme.palette()
    color = p.good if pnl > 0 else p.critical
    ts = marks["ts"].to_list()

    fig = go.Figure()
    if "peak_value" in marks.columns:
        fig.add_trace(go.Scatter(
            x=ts, y=marks["peak_value"].to_list(),
            mode="lines", line=dict(color=p.muted, width=1, dash="dot"),
            name="Peak", hovertemplate="peak $%{y:,.0f}<extra></extra>",
        ))
    if "trailing_stop" in marks.columns and marks["trailing_stop"].null_count() < marks.height:
        fig.add_trace(go.Scatter(
            x=ts, y=marks["trailing_stop"].to_list(),
            mode="lines", line=dict(color=p.critical, width=1.5, dash="dash"),
            name="Trailing stop", hovertemplate="stop $%{y:,.0f}<extra></extra>",
        ))
    fig.add_trace(go.Scatter(
        x=ts, y=marks["gross_pnl"].to_list(),
        mode="lines", line=dict(color=color, width=2),
        name="Gross P&L", hovertemplate="$%{y:,.0f}<extra></extra>",
    ))
    fig.add_hline(y=0, line=dict(color=p.axis, width=1))
    fig.update_yaxes(tickformat="$,.0f")
    return theme.trading_clock(
        theme.style(fig, height=200, showlegend=True)
    ).update_layout(title=title)


def equity_curve(
    ts: list,
    equity: list,
    initial: float,
    title: str,
    invested: list | None = None,
) -> go.Figure:
    """Account equity over time with a reference line at starting capital, and —
    when supplied — a filled second line showing capital at risk in open
    positions (what is 'invested')."""
    p = theme.palette()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ts, y=equity,
        mode="lines", line=dict(color=p.series_1, width=2),
        name="Equity", hovertemplate="$%{y:,.0f}<extra></extra>",
    ))
    fig.add_hline(
        y=initial, line=dict(color=p.muted, width=1, dash="dot"),
        annotation_text="initial", annotation_position="bottom right",
        annotation_font=dict(size=10, color=p.text_secondary),
    )
    # Capital invested rides a *secondary* axis (right, from zero): it is orders
    # of magnitude smaller than equity, so sharing the equity axis — worse, with
    # a fill to zero — would collapse the equity line to a flat stripe.
    if invested is not None:
        fig.add_trace(go.Scatter(
            x=ts, y=invested, yaxis="y2",
            mode="lines", line=dict(color=p.series_2, width=1.5),
            fill="tozeroy", fillcolor=theme.rgba(p.series_2, 0.10),
            name="Invested", hovertemplate="invested $%{y:,.0f}<extra></extra>",
        ))
    fig.update_yaxes(tickformat="$,.0f")
    fig = theme.trading_clock(theme.style(fig, height=240, showlegend=invested is not None))
    if invested is not None:
        # Independent right axis so equity keeps its own tight auto-scale.
        fig.update_layout(yaxis2=dict(
            overlaying="y", side="right", rangemode="tozero",
            showgrid=False, tickformat="$,.0s",
            tickfont=dict(size=9, color=p.series_2),
        ))
    return fig.update_layout(title=title)


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


# --------------------------------------------------------------- model evaluation


def crps_skill_bars(labels: list[str], skills: list[float], title: str) -> go.Figure:
    """CRPS skill vs a random-walk null, one bar per symbol. Positive (green)
    beats the null; negative (red) is worse than a random walk."""
    p = theme.palette()
    colors = [p.good if s > 0 else p.critical for s in skills]
    fig = go.Figure(go.Bar(
        x=skills, y=labels, orientation="h", marker_color=colors,
        hovertemplate="%{y}: skill %{x:+.3f}<extra></extra>",
    ))
    fig.add_vline(x=0, line=dict(color=p.axis, width=1))
    fig.update_xaxes(title="CRPS skill vs null  ( > 0 beats a random walk )", tickformat="+.2f")
    fig = theme.style(fig, height=max(220, 26 * len(labels) + 90))
    return fig.update_layout(title=title, hovermode="closest")


def pit_histogram(bins: list[int], title: str) -> go.Figure:
    """The probability-integral-transform histogram over ten bins. Flat at the
    dotted line = calibrated; mass shifted right = the forecast is biased low."""
    p = theme.palette()
    n = sum(bins) or 1
    centers = [(i + 0.5) / 10 for i in range(len(bins))]
    fig = go.Figure(go.Bar(
        x=centers, y=bins, width=0.09, marker_color=p.series_1,
        hovertemplate="PIT %{x:.2f}: %{y} bars<extra></extra>",
    ))
    fig.add_hline(
        y=n / 10, line=dict(color=p.muted, width=1, dash="dot"),
        annotation_text="uniform", annotation_position="top right",
        annotation_font=dict(size=10, color=p.text_secondary),
    )
    fig.update_xaxes(title="PIT = F(realized)", range=[0, 1])
    fig.update_yaxes(title="bars")
    return theme.style(fig, height=280).update_layout(title=title, hovermode="closest")


def predicted_vs_realized(pred_move: list[float], real_move: list[float], title: str) -> go.Figure:
    """Predicted move (forecast mean vs spot) against the realized move. Points on
    the dotted diagonal are perfect; a cloud with no slope has no directional edge."""
    p = theme.palette()
    fig = go.Figure(go.Scatter(
        x=pred_move, y=real_move, mode="markers",
        marker=dict(color=p.series_1, size=4, opacity=0.35),
        hovertemplate="pred %{x:+.2%}, real %{y:+.2%}<extra></extra>",
    ))
    span = max((abs(v) for v in pred_move + real_move), default=0.01) or 0.01
    fig.add_trace(go.Scatter(
        x=[-span, span], y=[-span, span], mode="lines",
        line=dict(color=p.muted, width=1, dash="dot"), hoverinfo="skip", showlegend=False,
    ))
    fig.update_xaxes(title="predicted move (mean / spot − 1)", tickformat="+.0%")
    fig.update_yaxes(title="realized move", tickformat="+.0%")
    return theme.style(fig, height=380).update_layout(title=title, hovermode="closest")


def forecast_band(df: pl.DataFrame, title: str) -> go.Figure:
    """One symbol's forecasts over time: the 5–95% band, the median, and where the
    underlying actually landed a horizon later. A calibrated model has ~90% of the
    realized markers inside the band."""
    p = theme.palette()
    ts = df["ts"].to_list()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ts, y=df["q95"].to_list(), mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=ts, y=df["q05"].to_list(), mode="lines", line=dict(width=0),
        fill="tonexty", fillcolor=theme.rgba(p.muted, 0.18), name="5–95%", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=ts, y=df["q50"].to_list(), mode="lines",
        line=dict(color=p.series_1, width=1.5), name="forecast median",
        hovertemplate="median %{y:.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=ts, y=df["realized"].to_list(), mode="markers",
        marker=dict(color=p.series_2, size=4), name="realized",
        hovertemplate="realized %{y:.2f}<extra></extra>",
    ))
    return theme.style(fig, height=340, showlegend=True).update_layout(title=title)


def scorer_deciles(deciles: list[dict], title: str) -> go.Figure:
    """Realized P/L per unit of risk, by the model's score decile (1 = lowest
    score, 10 = highest). A discriminating score climbs left to right; a flat
    ladder means the score is noise."""
    p = theme.palette()
    x = [d["decile"] for d in deciles]
    y = [d["mean_realized_pl_per_risk"] for d in deciles]
    colors = [p.good if v > 0 else p.critical for v in y]
    fig = go.Figure(go.Bar(
        x=x, y=y, marker_color=colors,
        customdata=[d["mean_score"] for d in deciles],
        hovertemplate="decile %{x}: %{y:+.2f} realized P/L per risk"
                      " (mean score %{customdata:+.2f})<extra></extra>",
    ))
    fig.add_hline(y=0, line=dict(color=p.axis, width=1))
    fig.update_xaxes(title="score decile  (1 = lowest, 10 = highest)", dtick=1)
    fig.update_yaxes(title="mean realized P/L ÷ risk")
    return theme.style(fig, height=340).update_layout(title=title, hovermode="closest")
