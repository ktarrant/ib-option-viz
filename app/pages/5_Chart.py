"""Chart: a symbol's price history, with one backtest run's trades drawn on it."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import streamlit as st

from optionviz import surface
from optionviz.backtest import DAILY, position_marks
from optionviz.ui import chain_table, charts, data, theme, trade_card

st.set_page_config(page_title="Chart", page_icon="🕯️", layout="wide")
theme.page_css()

TIMEFRAMES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "1D": DAILY}
# Calendar days on screen when a timeframe opens: enough bars to read, few
# enough that a candle is wider than a hairline. None opens on everything.
OPENING_SPAN = {"5m": 5, "15m": 14, "30m": 30, "1h": 60, "1D": None}

# ------------------------------------------------------------------ inputs
catalog = data.require_catalog()
symbols = data.get_stock_symbols(catalog.fingerprint, catalog)
symbol = st.sidebar.selectbox(
    "Symbol", symbols, index=symbols.index("SPY") if "SPY" in symbols else 0
)
tf = st.sidebar.segmented_control("Timeframe", list(TIMEFRAMES), default="30m") or "30m"

results_root = Path(st.sidebar.text_input("Backtest results", value="../ib-spread-trader/out"))
runs = {}
for f in sorted(results_root.rglob("backtest.json")) if results_root.is_dir() else []:
    try:
        run = data.load_run(str(f), f.stat().st_mtime)
    except Exception:
        continue  # a run mid-write, or one from a format this viewer predates
    if n := sum(t.symbol == symbol for t in run.trades):
        runs[f] = (run, n)
chosen = st.sidebar.selectbox(
    "Overlay a run",
    [None, *runs],
    format_func=lambda f: "None" if f is None else f"{f.parent.name} · {runs[f][1]} trades",
    help="Only runs that traded this symbol are listed.",
)
show_earnings = st.sidebar.toggle("Earnings", value=True)
show_volume = st.sidebar.toggle("Volume", value=True)

# ------------------------------------------------------------------ bars
bars = data.get_bars(catalog.fingerprint, catalog, symbol, TIMEFRAMES[tf])
if bars.height < 2:
    st.info(f"No stock bars for {symbol} at {tf}.")
    st.stop()

first, last = bars["ts"][0].date(), bars["ts"][-1].date()
span = OPENING_SPAN[tf]
# The window filters the bars before they reach Plotly, rather than zooming:
# Plotly does not refit the y-axis to a zoomed x-range, so a zoom into four
# months of data would draw flat candles. Keyed per symbol and timeframe so
# each opens on its own span. Its controls sit under the chart, so the value is
# read from state here and the widgets drawn there.
window_key = f"window_{symbol}_{tf}"
opening = (max(first, last - timedelta(days=span)) if span else first, last)
lo, hi = st.session_state.setdefault(window_key, opening)


def _show_all() -> None:
    st.session_state[window_key] = (first, last)
    # Also when the window already is everything: Plotly's own zoom lives in
    # the browser, and only a fresh chart (a new key, below) drops it.
    st.session_state["view_rev"] = st.session_state.get("view_rev", 0) + 1


def _window_controls() -> None:
    c_window, c_all = st.columns([7, 1], vertical_alignment="center")
    c_window.slider(
        "Window", min_value=first, max_value=last,
        format="MMM D, YYYY", key=window_key, label_visibility="collapsed",
    )
    c_all.button(
        "Show all", on_click=_show_all,
        help="Every bar the capture holds for this symbol and timeframe",
    )


view = bars.filter(pl.col("ts").dt.date().is_between(lo, hi))
if view.height < 2:
    st.info("Widen the window — it holds fewer than two bars.")
    _window_controls()
    st.stop()

run, _ = runs.get(chosen, (None, 0))
trades = [t for t in run.trades if t.symbol == symbol] if run else []
on_screen = [t for t in trades if t.closed_at.date() >= lo and t.opened_at.date() <= hi]

# ------------------------------------------------------------------ header
st.title(symbol)
close, start = view["close"][-1], view["open"][0]
c1, c2, c3 = st.columns(3)
c1.metric("Last", f"{close:,.2f}", f"{close / start - 1:+.2%} over the window")
c2.metric("Window range", f"{view['low'].min():,.2f} – {view['high'].max():,.2f}")
c3.metric("Bars", f"{view.height:,}", f"{lo:%b %d} → {hi:%b %d}", delta_color="off")
if run:
    won = sum(t.is_winner for t in trades)
    st.caption(
        f"**{chosen.parent.name}**: {len(trades)} {symbol} trades · "
        f"${sum(t.pnl for t in trades):+,.2f} · {won / len(trades):.0%} won · "
        f"{run.bar_minutes}-minute grid · {len(on_screen)} in this window  \n"
        "Legs: calls blue, puts orange; long solid, short dashed; a spread's zone "
        "is filled. Hover a bar for the trades open in it."
    )

# ------------------------------------------------------------------ chart


# Every change of view gets charts under keys never used before. Keyed on the
# window alone, returning to an earlier window (Show all, twice) handed back
# that chart's browser-side state — its Plotly zoom, its old pick.
shown = (symbol, tf, lo, hi)
if st.session_state.get("shown") != shown:
    st.session_state["shown"] = shown
    st.session_state["view_rev"] = st.session_state.get("view_rev", 0) + 1
chart_key = f"chart_{st.session_state['view_rev']}"
nav_key = f"nav_{st.session_state['view_rev']}"


def _on_navigator_select() -> None:
    """A drag across the navigator sets the window to the dragged days."""
    box = st.session_state[nav_key].selection["box"]
    if box:
        x0, x1 = sorted(date.fromisoformat(str(x)[:10]) for x in box[0]["x"])
        st.session_state[window_key] = (max(first, x0), min(last, x1))


def _on_chart_select() -> None:
    """A click picks the bar under it, which the option chain below reads off.
    A double-click clears the pick."""
    points = st.session_state[chart_key].selection["points"]
    # Tied to this chart's key, so the chain never outlives the highlighted bar.
    st.session_state["bar"] = (chart_key, points[0]["x"]) if points else None


def _pick_from_table() -> None:
    """A row may be off screen, so the window moves to frame it."""
    rows = st.session_state["trades"].selection["rows"]
    if rows:
        t = trades[rows[0]]
        st.session_state["trade"] = (str(chosen), t.id)
        st.session_state[window_key] = (
            max(first, t.opened_at.date() - timedelta(days=3)),
            min(last, t.closed_at.date() + timedelta(days=3)),
        )


earnings = data.get_earnings(catalog.fingerprint, catalog, symbol) if show_earnings else None
st.plotly_chart(
    charts.symbol_chart(
        view, f"{symbol} · {tf}", trades=tuple(on_screen), earnings=earnings,
        show_volume=show_volume, daily=tf == "1D",
    # Plotly's own box zoom stays for a close look (drag a box round the
    # candles; Home returns to the window). Streamlit would default to pan.
    ).update_layout(dragmode="zoom"),
    width="stretch", key=chart_key, on_select=_on_chart_select, selection_mode="points",
    config={"displaylogo": False, "modeBarButtonsToRemove": [
        "pan2d", "zoomIn2d", "zoomOut2d", "autoScale2d",
    ]},
)
st.plotly_chart(
    charts.navigator(bars, lo, hi, daily=tf == "1D"),
    width="stretch", key=nav_key, on_select=_on_navigator_select, selection_mode="box",
    config={"displayModeBar": False},
)
_window_controls()

# ------------------------------------------------------------------ option chain
def _option_chain(clicked: datetime) -> None:
    """The chain board as it stood at the close of the clicked bar."""
    candle = view.filter(pl.col("ts") <= clicked)["ts"]
    start = candle[-1] if candle.len() else view["ts"][0]
    as_of = start + (timedelta(days=1) if tf == "1D" else timedelta(minutes=TIMEFRAMES[tf]))
    session = datetime.combine(start.date(), datetime.min.time(), timezone.utc)
    # Five days before the session reaches the prior one across a long weekend.
    stamp = "%Y-%m-%dT%H:%M:%S+00:00"
    quotes = data.get_option_quotes(
        catalog.fingerprint, catalog, symbol,
        (session - timedelta(days=5)).strftime(stamp), as_of.strftime(stamp),
    )
    now = surface.chain_at(quotes, as_of)
    st.subheader(f"Option chain · {start:%b %d}" + ("" if tf == "1D" else f" {start:%H:%M}"))
    if not now.height:
        st.info(f"No {symbol} option quotes in the five days to {as_of:%b %d %H:%M} UTC.")
        return
    slots = surface.expiry_slots(start.date(), now["expiry"].unique().to_list())
    if not any(e for _, e in slots.values()):
        st.info(
            f"None of this bar's expiries is captured. "
            f"Quoted: {', '.join(sorted(now['expiry'].unique()))}."
        )
        return
    c_note, c_all = st.columns([5, 1], vertical_alignment="bottom")
    show_all = c_all.toggle("All strikes")
    c_note.caption(
        "Mid of each contract, tinted by its move since the previous bar: green up, "
        f"red down, full strength at ±{chain_table.FULL_AT:.0%}. Hover a cell for its "
        "quote, IV and both changes. IV is the front monthly's smile (the nearest "
        "captured expiry when it isn't), and the blue rule marks its forward."
    )
    st.html(chain_table.board_html(
        symbol, now,
        surface.chain_at(quotes, start),    # the previous bar closed as this one opened
        surface.chain_at(quotes, session),  # the last bar before this session
        slots, strikes_each_side=None if show_all else 10,
    ))


picked_bar = st.session_state.get("bar")
if picked_bar and picked_bar[0] == chart_key:
    _option_chain(datetime.fromisoformat(str(picked_bar[1])).replace(tzinfo=timezone.utc))
else:
    st.caption("Click a bar to see its option chain; double-click to clear the pick. "
               "Drag across the strip under the chart to choose the window.")

if not run:
    st.caption("Overlay a backtest run from the sidebar to see its trades on the tape.")
    st.stop()

# ------------------------------------------------------------------ trades
st.dataframe(
    pl.DataFrame([{
        "#": t.id,
        "Opened": t.opened_at,
        "Closed": t.closed_at,
        "Trade": t.description,
        "Exit": t.exit_label,
        "P&L": t.pnl,
    } for t in trades]),
    hide_index=True, width="stretch", height=min(36 * len(trades) + 38, 280),
    column_config={
        "Opened": st.column_config.DatetimeColumn(format="MMM D HH:mm"),
        "Closed": st.column_config.DatetimeColumn(format="MMM D HH:mm"),
        "P&L": st.column_config.NumberColumn(format="$%+,.2f"),
    },
    key="trades", on_select=_pick_from_table, selection_mode="single-row",
)

picked_run, picked_id = st.session_state.get("trade", (None, None))
trade = next((t for t in trades if picked_run == str(chosen) and t.id == picked_id), None)
if trade is None:
    st.caption("Click a row above to open that trade.")
else:
    st.divider()
    trade_card.render(
        trade,
        data.get_bars(catalog.fingerprint, catalog, symbol, run.bar_minutes),
        position_marks(chosen),
        key="selected",
    )
