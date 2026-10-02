"""Chart: a symbol's price on TradingView's Lightweight Charts, with one
backtest run drawn on it and the option chain at any bar you pick."""

from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import polars as pl
import streamlit as st

from optionviz import surface
from optionviz.backtest import DAILY, position_marks
from optionviz.ui import chain_table, data, trade_card, tv_chart


TIMEFRAMES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "1D": DAILY}
# Calendar days on screen when a timeframe opens: enough bars to read, few
# enough that a candle is wider than a hairline. None opens on everything.
OPENING_SPAN = {"5m": 5, "15m": 14, "30m": 30, "1h": 60, "1D": None}
EPS = timedelta(microseconds=1)


def _open(t, run: str, minutes: int, frame: bool = True) -> None:
    """Open a trade: its symbol, the chain it entered into (its entry bar's
    close) and, with `frame`, the chart scrolled to it. Sets the symbol widget,
    so only from a callback or before the widgets render."""
    st.session_state["chart_symbol"] = t.symbol
    st.session_state["chart_trade"] = (run, t.id)
    st.session_state["chart_as_of"] = (t.symbol, t.opened_at + timedelta(minutes=minutes))
    if frame:
        st.session_state["chart_frame"] = (t.opened_at, t.closed_at)


# ------------------------------------------------------------------ inputs
catalog = data.require_catalog()
symbols = data.get_stock_symbols(catalog.fingerprint, catalog)
# A trade picked on the Backtest page: (trade, run path, bar minutes). Applied
# here, before any widget renders, since it sets the symbol and the run.
if link := st.session_state.pop("chart_open", None):
    st.session_state["chart_run"] = Path(link[1])
    _open(*link)
if st.session_state.get("chart_symbol") not in symbols:
    st.session_state["chart_symbol"] = "SPY" if "SPY" in symbols else symbols[0]
symbol = st.sidebar.selectbox("Symbol", symbols, key="chart_symbol")

results_root = Path(st.sidebar.text_input("Backtest results", value="../ib-spread-trader/out"))
runs = {}
for f in sorted(results_root.rglob("backtest.json")) if results_root.is_dir() else []:
    try:
        run = data.load_run(str(f), f.stat().st_mtime)
    except Exception:
        continue  # a run mid-write, or one from a format this viewer predates
    runs[f] = run
if st.session_state.get("chart_run") not in runs:
    st.session_state["chart_run"] = None  # a run since deleted, or from another root
# The run stays picked as the symbol changes, so one run can be read across
# several: every run is listed, traded here or not, and the widget is keyed.
# Labels must not change with the symbol — a selectbox remembers its value by
# label, so a per-symbol trade count in it dropped the pick on every change
# (and an unkeyed widget fed `index` from state is a new widget whenever that
# index moves, which threw away every other pick). The count is in the caption.
chosen = st.sidebar.selectbox(
    "Overlay a run",
    [None, *runs],
    key="chart_run",
    format_func=lambda f: "None" if f is None else str(f.parent.relative_to(results_root)),
    help="Stays selected as you change symbol, to see one run across several.",
)
run = runs.get(chosen)
trades = [t for t in run.trades if t.symbol == symbol] if run else []

show_earnings = st.sidebar.toggle("Earnings", value=True)
show_volume = st.sidebar.toggle("Volume", value=True)

# What the run's model saw and forecast, bar by bar — present only for a ranked run.
forecasts = data.get_forecasts(str(chosen), chosen.stat().st_mtime, symbol, run.bar_minutes) if run else None
features = (run.config.get("features") or {}) if run else {}
cadence = features.get("cadence_minutes", 30)
pane_keys, show_fan = [], False
if options := tv_chart.available_inputs(forecasts):
    # A run opens on the inputs its model leans on; after that the choice is
    # the viewer's, kept to what this symbol's readings can draw.
    if st.session_state.get("chart_panes_run") != str(chosen) or "chart_panes" not in st.session_state:
        st.session_state["chart_panes_run"] = str(chosen)
        st.session_state["chart_panes"] = tv_chart.weighted_inputs(run.config.get("model") or {})
    st.session_state["chart_panes"] = [k for k in st.session_state["chart_panes"] if k in options]
    pane_keys = st.sidebar.multiselect(
        "Model inputs", options, format_func=tv_chart.INPUTS.get, key="chart_panes",
        help="The run's own per-bar readings — what the model saw, not recomputed. "
             "VWAP is drawn on the price as the line it measures distance from; "
             "the rest each get a pane.",
    )
    show_fan = st.sidebar.toggle(
        "Forecast at picked bar", value=True,
        help="The model's 50% and 90% ranges from the picked bar to its horizon, "
             "and where price actually landed.",
    )

# ------------------------------------------------------------------ header
c_title, c_tf = st.columns([3, 2], vertical_alignment="bottom")
c_title.title(symbol)
tf = c_tf.segmented_control(
    "Timeframe", list(TIMEFRAMES), default="30m", label_visibility="collapsed"
) or "30m"
daily = tf == "1D"
width = timedelta(days=1) if daily else timedelta(minutes=TIMEFRAMES[tf])

bars = data.get_bars(catalog.fingerprint, catalog, symbol, TIMEFRAMES[tf])
if bars.height < 2:
    st.info(f"No stock bars for {symbol} at {tf}.")
    st.stop()
if not show_volume:
    bars = bars.drop("volume", strict=False)

if run and not trades:
    st.caption(f"**{chosen.parent.name}** made no {symbol} trades.")
elif run:
    won = sum(t.is_winner for t in trades)
    st.caption(
        f"**{chosen.parent.name}**: {len(trades)} {symbol} trades · "
        f"${sum(t.pnl for t in trades):+,.2f} · {won / len(trades):.0%} won · "
        f"{run.bar_minutes}-minute grid  \n"
        "Legs: calls blue, puts orange; long solid, short dashed; a spread's zone is "
        "filled. Click a leg or a ▲▼ marker to open that trade."
    )

# ------------------------------------------------------------------ selection
# The pick is an instant — the close of the bar picked — not a bar, so it means
# the same thing on every timeframe and is exactly the no-lookahead `as_of` the
# option chain is read at. Both picks are tied to the symbol (and the trade to
# its run), so neither outlives what it was picked on.
picked = st.session_state.get("chart_as_of")
as_of = picked[1] if picked and picked[0] == symbol else None
picked_trade = st.session_state.get("chart_trade")
trade = next(
    (t for t in trades if picked_trade == (str(chosen), t.id)), None
) if run else None


def _grid() -> timedelta:
    return timedelta(minutes=run.bar_minutes)


def _select_trade(t, frame: bool) -> None:
    _open(t, str(chosen), run.bar_minutes, frame)


# A run's trades can lie well outside the opening window (an older chain's), so
# picking one frames its trades once — unless a trade was just opened, which is
# framed instead. Only on picking the run: a change of symbol keeps the range
# the viewer chose.
overlay = str(chosen)
if trades and st.session_state.get("chart_overlay") != overlay:
    st.session_state.setdefault(
        "chart_frame", (min(t.opened_at for t in trades), max(t.closed_at for t in trades))
    )
st.session_state["chart_overlay"] = overlay
# A frame is asked for as instants and resolved here, against the bars now
# showing — a trade opened on another symbol had none until this rerun.
if span := st.session_state.pop("chart_frame", None):
    ts = bars["ts"].to_list()
    x0, x1 = (tv_chart.logical_x(ts, x, width) for x in span)
    pad = max(10.0, (x1 - x0) * 0.4)
    nonce = st.session_state.get("chart_focus_nonce", 0) + 1
    st.session_state["chart_focus_nonce"] = nonce
    st.session_state["chart_focus"] = {"from": x0 - pad, "to": x1 + pad, "nonce": nonce}


def _on_pick() -> None:
    if key := st.session_state["tv"]["pick"]:
        st.session_state["chart_as_of"] = (symbol, datetime.fromisoformat(key) + width)


def _on_trade() -> None:
    picked_id = st.session_state["tv"]["trade"]
    if t := next((t for t in trades if t.id == picked_id), None):
        _select_trade(t, frame=False)


def _on_clear() -> None:
    st.session_state["chart_as_of"] = None


def _pick_from_table() -> None:
    """A row may be off screen, so the chart scrolls to frame it."""
    rows = st.session_state["trades"].selection["rows"]
    if rows:
        _select_trade(run.trades[rows[0]], frame=True)


# ------------------------------------------------------------------ chart
# Per-bar position state; an empty frame means "not recorded" (an older run).
all_marks = position_marks(chosen) if run else pl.DataFrame()
marks = all_marks.filter(pl.col("position_id") == trade.id) if trade and all_marks.height else None
earnings = data.get_earnings(catalog.fingerprint, catalog, symbol) if show_earnings else None
tv_chart.render(
    tv_chart.payload(
        symbol=symbol, timeframe=tf, bars=bars, width=width, daily=daily,
        trades=trades, selected_trade=trade.id if trade else None, as_of=as_of,
        earnings=earnings, forecasts=forecasts, pane_keys=pane_keys, fan=show_fan,
        cadence=cadence,
        run_minutes=run.bar_minutes if run else 30,
        rsi_level=features.get("rsi_cross_level"),
        opening_days=OPENING_SPAN[tf], focus=st.session_state.get("chart_focus"),
        marks=marks,
    ),
    key="tv", on_pick=_on_pick, on_trade=_on_trade, on_clear=_on_clear,
)


# ------------------------------------------------------------------ stepper
# The run's trades in its own order, across symbols: ◀/▶ open the neighbour on
# its symbol, framed, so a run reads one trade at a time on the candles.
if run and run.trades:
    ids = [t.id for t in run.trades]
    n, last = (ids.index(trade.id) if trade else -1), len(ids) - 1
    c_prev, c_at, c_next = st.columns([1, 6, 1], vertical_alignment="center")
    c_prev.button("◀ Prev", disabled=n <= 0, width="stretch",
                  on_click=_select_trade, args=(run.trades[max(n - 1, 0)], True))
    c_next.button("Next ▶", disabled=n == last, width="stretch",
                  on_click=_select_trade, args=(run.trades[min(n + 1, last)], True))
    c_at.caption(
        f"Trade {n + 1} of {len(ids)} in **{chosen.parent.name}**" if trade else
        f"Step through **{chosen.parent.name}**'s {len(ids)} trades, across every symbol."
    )
    if trade:
        ranked = run.config.get("mode") == "ranked"
        trade_card.render(trade, all_marks, key="selected",
                          model=run.config.get("model") if ranked else None)
        c_in, c_out, _ = st.columns([1, 1, 4])
        c_in.button("Chain at entry", on_click=lambda: st.session_state.update(
            chart_as_of=(symbol, trade.opened_at + _grid())))
        c_out.button("Chain at exit", on_click=lambda: st.session_state.update(
            chart_as_of=(symbol, trade.closed_at + _grid())))
    st.divider()


# ------------------------------------------------------------------ option chain
def _option_chain(as_of: datetime) -> None:
    """The chain board as it stood at `as_of`, the close of the picked bar."""
    day = (as_of - EPS).date()
    session = datetime.combine(day, time.min, timezone.utc)
    # Five days before the session reaches the prior one across a long weekend.
    stamp = "%Y-%m-%dT%H:%M:%S+00:00"
    quotes = data.get_option_quotes(
        catalog.fingerprint, catalog, symbol,
        (session - timedelta(days=5)).strftime(stamp), as_of.strftime(stamp),
    )
    now = surface.chain_at(quotes, as_of)
    c_head, c_clear = st.columns([6, 1], vertical_alignment="bottom")
    c_head.subheader(f"Option chain · {tv_chart._et(as_of)} ET")
    c_clear.button("Clear pick", on_click=_on_clear, help="Esc on the chart does the same")
    if not now.height:
        st.info(f"No {symbol} option quotes in the five days to {tv_chart._et(as_of)} ET.")
        return
    slots = surface.expiry_slots(day, now["expiry"].unique().to_list())
    if not any(e for _, e in slots.values()):
        st.info(
            f"None of this bar's expiries is captured. "
            f"Quoted: {', '.join(sorted(now['expiry'].unique()))}."
        )
        return
    c_note, c_all = st.columns([5, 1], vertical_alignment="bottom")
    show_all = c_all.toggle("All strikes")
    held = trade and trade.opened_at <= as_of <= trade.closed_at + _grid()
    c_note.caption(
        "Mid of each contract, tinted by its move since the previous bar: green up, "
        f"red down, full strength at ±{chain_table.FULL_AT:.0%}. Hover a cell for its "
        "quote, IV and both changes. IV is the front monthly's smile (the nearest "
        "captured expiry when it isn't), and the blue rule marks its forward."
        + (f" Outlined: trade #{trade.id}'s legs." if held else "")
    )
    st.html(chain_table.board_html(
        symbol, now,
        surface.chain_at(quotes, as_of - width),  # the previous bar closed as this one opened
        surface.chain_at(quotes, session),        # the last bar before this session
        slots, strikes_each_side=None if show_all else 10,
        legs={(trade.expiry or run.expiry, k, "call" if r == "C" else "put")
              for _, r, k in trade.legs} if held else None,
    ))


if as_of:
    _option_chain(as_of)
else:
    st.caption(
        "Click a bar to see its option chain; ←/→ then step it, Esc clears. "
        "Scroll to zoom, drag to pan, drag either axis to stretch it, "
        "double-click the price axis to refit."
    )

if not run:
    st.caption("Overlay a backtest run from the sidebar to see its trades on the tape.")
    st.stop()
if not run.trades:
    st.stop()

# ------------------------------------------------------------------ trades
# Every trade in the run, across symbols: a row opens it on its own symbol.
st.dataframe(
    pl.DataFrame([{
        "#": t.id,
        "Symbol": t.symbol,
        "Opened": t.opened_at,
        "Closed": t.closed_at,
        "Trade": t.description,
        "Exit": t.exit_label,
        "P&L": t.pnl,
    } for t in run.trades]),
    hide_index=True, width="stretch", height=min(36 * len(run.trades) + 38, 400),
    column_config={
        "Opened": st.column_config.DatetimeColumn(format="MMM D HH:mm"),
        "Closed": st.column_config.DatetimeColumn(format="MMM D HH:mm"),
        "P&L": st.column_config.NumberColumn(format="$%+,.2f"),
    },
    key="trades", on_select=_pick_from_table, selection_mode="single-row",
)
