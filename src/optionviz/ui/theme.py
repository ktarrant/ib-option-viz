"""Chart palette, shared Plotly layout and the page's terminal chrome.

Dark only. Streamlit's own colors are set in `.streamlit/config.toml`; this
palette mirrors them for what the theme doesn't reach (Plotly, the Chart page's
canvas, the chain board). The series slots are a validated categorical palette
on the dark surface: calls cyan, puts magenta, VWAP amber. Amber `accent` is
chrome — headings, the picked bar, the forward rule — never a series. Status
colors are reserved for the verdict and never used for a series.

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

FONT = '"JetBrains Mono", ui-monospace, Menlo, monospace'


@dataclass(frozen=True)
class Palette:
    surface: str
    panel: str      # raised surfaces: hover cards, tooltips
    text_primary: str
    text_secondary: str
    muted: str
    grid: str
    axis: str
    accent: str     # chrome, never a series
    series_1: str   # calls / price / "before"
    series_2: str   # puts / vol / "after"
    series_3: str   # the Chart page's VWAP, beside calls and puts
    good: str = "#1fa865"
    critical: str = "#f0522a"
    warning: str = "#e8c547"


DARK = Palette(
    surface="#0b0e14", panel="#121822", text_primary="#e6edf3",
    text_secondary="#a3adbb", muted="#7d8796", grid="#1a212c", axis="#2a3342",
    accent="#ffb000",
    series_1="#0094c4", series_2="#e23d9b",
    series_3="#b98a00",
)


# Plotly draws every hover spike over a solid halo 2px wider, colored from the
# plot background. Ours is transparent, which composites to white, so in dark
# mode the 1px dashed crosshair became a pair of thick white bars. The halo is
# the spike line without a dash pattern; this hides it.
HALO_CSS = ".js-plotly-plot line.spikeline:not([stroke-dasharray]){display:none}"


def page_css() -> None:
    """Page-wide CSS — the Plotly halo fix and the terminal chrome the theme
    config can't express. Called once, by the entrypoint."""
    p = DARK
    st.html(f"""<style>
{HALO_CSS}
[data-testid="stMainBlockContainer"] {{ padding-top: 3.5rem; }}
h1, h2, h3, h4 {{ text-transform: uppercase; letter-spacing: .06em; }}
h1 {{ color: {p.accent}; text-shadow: 0 0 14px {rgba(p.accent, 0.35)};
      border-bottom: 1px solid {rgba(p.accent, 0.35)}; padding-bottom: .3rem !important; }}
[data-testid="stMetric"] {{ background: {p.panel}; border: 1px solid {p.axis};
      border-left: 2px solid {rgba(p.accent, 0.6)}; padding: .45rem .7rem; }}
[data-testid="stMetricLabel"] {{ text-transform: uppercase; letter-spacing: .08em;
      color: {p.text_secondary}; }}
</style>""")


def palette() -> Palette:
    return DARK


def rgba(hex_color: str, alpha: float) -> str:
    """A '#rrggbb' color as an 'rgba(r,g,b,a)' string, for translucent fills."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


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
        hoverlabel=dict(font_family=FONT, font_size=12, bgcolor=p.panel,
                        bordercolor=p.accent, font_color=p.text_primary),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            font=dict(size=11), bgcolor="rgba(0,0,0,0)",
        ),
    )
    fig.update_xaxes(
        showgrid=False, zeroline=False, linecolor=p.axis, linewidth=1,
        tickfont=dict(color=p.muted, size=11), automargin=True,
        showspikes=True, spikemode="across", spikesnap="cursor",
        spikethickness=1, spikedash="dash", spikecolor=p.muted,
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
    bg = rgba(color, 0.14) if subtle else color
    fg = color if subtle else DARK.surface
    edge = f"border:1px solid {rgba(color, 0.5)};" if subtle else ""
    return (
        f'<span style="background:{bg};color:{fg};{edge}padding:1px 7px;border-radius:2px;'
        f'font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
        f'white-space:nowrap">{text}</span>'
    )


KIND_LABEL = {
    "earnings": ("Earnings", "series_1"),
    "macro": ("Macro", "series_2"),
    "shock": ("Shock", "muted"),
    "move": ("Move", "muted"),
}


def kind_badge(kind: str) -> str:
    label, slot = KIND_LABEL.get(kind, (kind.title(), "muted"))
    return badge(label, getattr(palette(), slot), subtle=True)


def source_badge(source: str) -> str:
    """Confirmed (from the capture's events table) vs detected (statistical).

    Shown only for the confirmed case - detected is the implicit default and a
    badge on every row would be noise, not signal. Uses the reserved "good"
    status color with its label, per the rule that a status color never
    appears without one - this is a verified-fact/detected-guess distinction,
    not a data series, so it does not compete with categorical identity.
    """
    if source == "derived":
        return ""
    return " " + badge("Confirmed", palette().good, subtle=False)
