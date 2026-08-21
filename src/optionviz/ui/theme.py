"""Chart palette and shared Plotly layout.

Values come from a validated categorical palette. Only slots 1 and 2 (blue,
orange) carry series identity here — both clear 3:1 contrast on either surface,
so no chart needs the relief rule. Status colors are reserved for the verdict and
never used for a series.

Rules this module exists to enforce:
- Two measures of different scale are two stacked charts sharing an x-axis,
  never one plot with two y-axes.
- Color follows the entity: calls are always slot 1, puts always slot 2,
  whatever is filtered.
"""

from __future__ import annotations

from dataclasses import dataclass

import plotly.graph_objects as go
import streamlit as st

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


@dataclass(frozen=True)
class Palette:
    surface: str
    text_primary: str
    text_secondary: str
    muted: str
    grid: str
    axis: str
    series_1: str   # calls / price / "before"
    series_2: str   # puts / vol / "after"
    good: str = "#0ca30c"
    critical: str = "#d03b3b"
    warning: str = "#fab219"


LIGHT = Palette(
    surface="#fcfcfb", text_primary="#0b0b0b", text_secondary="#52514e",
    muted="#898781", grid="#e1e0d9", axis="#c3c2b7",
    series_1="#2a78d6", series_2="#eb6834",
)
DARK = Palette(
    surface="#1a1a19", text_primary="#ffffff", text_secondary="#c3c2b7",
    muted="#898781", grid="#2c2c2a", axis="#383835",
    series_1="#3987e5", series_2="#d95926",
)


def palette() -> Palette:
    """Follow the viewer's Streamlit theme; fall back to light."""
    try:
        return DARK if st.context.theme.type == "dark" else LIGHT
    except Exception:
        return LIGHT


def style(
    fig: go.Figure,
    height: int = 300,
    showlegend: bool = False,
    left: int = 58,
    bottom: int = 44,
) -> go.Figure:
    """Recessive chrome, hairline grid, crosshair-friendly hover.

    Margins default to room for tick labels; tight margins clip them, which no
    amount of palette validation catches.
    """
    p = palette()
    fig.update_layout(
        height=height,
        showlegend=showlegend,
        margin=dict(l=left, r=18, t=38, b=bottom),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=12, color=p.text_secondary),
        title=dict(font=dict(size=13, color=p.text_primary), x=0, xanchor="left"),
        hovermode="x unified",
        hoverlabel=dict(font_family=FONT, font_size=12),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            font=dict(size=11), bgcolor="rgba(0,0,0,0)",
        ),
    )
    fig.update_xaxes(
        showgrid=False, zeroline=False, linecolor=p.axis, linewidth=1,
        tickfont=dict(color=p.muted, size=11), automargin=True,
    )
    fig.update_yaxes(
        showgrid=True, gridcolor=p.grid, gridwidth=1, zeroline=False,
        linecolor="rgba(0,0,0,0)", tickfont=dict(color=p.muted, size=11),
        automargin=True,
    )
    return fig


# The tape runs 13:30-20:00 UTC. Without these the axis renders empty nights and
# weekends as real elapsed time, and an overnight gap is drawn as a gentle
# diagonal - a move that never happened. Collapsing dead time turns it back into
# the jump it actually was.
SESSION_BREAKS = [
    dict(bounds=[20.5, 13.5], pattern="hour"),
    dict(bounds=["sat", "mon"]),
]


def trading_clock(fig: go.Figure) -> go.Figure:
    fig.update_xaxes(rangebreaks=SESSION_BREAKS)
    return fig


def verdict_color(verdict: str) -> str:
    p = palette()
    return p.good if verdict == "BUYER" else p.critical


def badge(text: str, color: str, subtle: bool = False) -> str:
    """Inline pill. Always paired with a text label, never color alone."""
    bg = f"{color}22" if subtle else color
    fg = color if subtle else "#ffffff"
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:10px;'
        f'font-size:11px;font-weight:600;letter-spacing:.02em;white-space:nowrap">{text}</span>'
    )


KIND_LABEL = {
    "earnings": ("Earnings", "#2a78d6"),
    "macro": ("Macro", "#eb6834"),
    "shock": ("Shock", "#898781"),
    "move": ("Move", "#898781"),
}


def kind_badge(kind: str) -> str:
    label, color = KIND_LABEL.get(kind, (kind.title(), "#898781"))
    return badge(label, color, subtle=True)
