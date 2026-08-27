"""Backtest Viewer: render trade-by-trade results from ib-spread-trader."""

from datetime import timedelta

import polars as pl
import streamlit as st

from optionviz.backtest import BacktestResult, underlying_bars
from optionviz.ui import charts, data, theme

st.set_page_config(page_title="Backtest", page_icon="🎯", layout="wide")

# ------------------------------------------------------------------ inputs
catalog = data.require_catalog()

DEFAULT_PATH = "../ib-spread-trader/out/backtest.json"
path = st.sidebar.text_input("Backtest JSON", value=DEFAULT_PATH)
if not path:
    st.info("Enter the path to a `backtest.json` produced by `ib-spread-trader`.")
    st.stop()

try:
    result = BacktestResult.load(path)
except FileNotFoundError:
    st.error(f"File not found: `{path}`")
    st.stop()
except Exception as e:
    st.error(f"Failed to load backtest: {e}")
    st.stop()

# ------------------------------------------------------------------ header
st.title(result.strategy)
st.caption(f"Expiry {result.expiry}  ·  {len(result.symbols)} symbols  ·  {len(result.trades)} trades")

# ------------------------------------------------------------------ summary
totals = result.totals
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total P&L", f"${totals['total_pnl']:+,.2f}")
c2.metric("Return", f"{totals['return_pct']:+.3f}%")
c3.metric("Trades", f"{totals['trades']}")
c4.metric(
    "Win Rate",
    f"{totals['win_rate']:.0f}%" if totals.get("win_rate") is not None else "—",
)
c5.metric("Max Drawdown", f"{totals['max_drawdown'] * 100:.2f}%")

c6, c7, c8, c9 = st.columns(4)
c6.metric("Traded P&L", f"${totals['traded_pnl']:+,.2f}")
c7.metric("Settled P&L", f"${totals['settled_pnl']:+,.2f}")
c8.metric("Commission", f"${totals['commission_paid']:,.2f}")
c9.metric(
    "Avg Trade",
    f"${totals['average_trade_pnl']:+,.2f}" if totals.get("average_trade_pnl") is not None else "—",
)

# ------------------------------------------------------------------ equity
equity_path = path.replace("backtest.json", "equity.csv")
try:
    eq = pl.read_csv(equity_path)
    eq = eq.with_columns(pl.col("ts").str.to_datetime("%Y-%m-%dT%H:%M:%S%z"))
    st.plotly_chart(
        charts.equity_curve(
            eq["ts"].to_list(), eq["equity"].to_list(),
            totals["capital_deployed"], "Equity curve (all symbols combined)",
        ),
        use_container_width=True, key="equity",
    )
except Exception:
    pass

# ------------------------------------------------------------------ caveats
if result.caveats:
    with st.expander("Caveats", expanded=False):
        for caveat in result.caveats:
            st.markdown(f"- {caveat}")

# ------------------------------------------------------------------ per-symbol
with st.expander("Per-symbol summary", expanded=False):
    sym_df = pl.DataFrame(result.symbols).select(
        pl.col("symbol").alias("Symbol"),
        pl.col("trades").alias("Trades"),
        pl.col("pnl").alias("P&L"),
        pl.col("traded_pnl").alias("Traded"),
        pl.col("settled_pnl").alias("Settled"),
        (pl.col("max_drawdown") * 100).alias("Max DD %"),
        pl.col("triggers").alias("Triggers"),
        pl.col("rejections").alias("Rejections"),
    )
    st.dataframe(
        sym_df, hide_index=True, use_container_width=True,
        column_config={
            "P&L": st.column_config.NumberColumn(format="$%+,.2f"),
            "Traded": st.column_config.NumberColumn(format="$%+,.2f"),
            "Settled": st.column_config.NumberColumn(format="$%+,.2f"),
            "Max DD %": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )

# ------------------------------------------------------------------ trades
st.divider()
st.subheader("Trades")

p = theme.palette()

PADDING = timedelta(days=5)

symbols_with_bars: dict[str, pl.DataFrame] = {}


def get_bars(symbol: str) -> pl.DataFrame:
    if symbol not in symbols_with_bars:
        symbols_with_bars[symbol] = underlying_bars(catalog, symbol)
    return symbols_with_bars[symbol]


for i, trade in enumerate(result.trades):
    pnl_color = p.good if trade.is_winner else p.critical
    exit_label = trade.exit_label

    st.markdown(
        f"**#{trade.id} {trade.description}** &nbsp; "
        f'{theme.badge(exit_label, pnl_color)} &nbsp; '
        f'{theme.badge(f"${trade.pnl:+,.2f}", pnl_color, subtle=True)}',
        unsafe_allow_html=True,
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Entry", f"${trade.entry_debit:,.2f}")
    m2.metric("Exit", f"${trade.exit_credit:,.2f}")
    m3.metric("Commission", f"${trade.commission:,.2f}")
    m4.metric("Bars held", f"{trade.bars_held}")

    bars = get_bars(trade.symbol)
    window = bars.filter(
        (pl.col("ts") >= trade.opened_at - PADDING)
        & (pl.col("ts") <= trade.closed_at + PADDING)
    )

    if window.height > 0:
        st.plotly_chart(
            charts.trade_box(
                window,
                trade.opened_at, trade.closed_at,
                trade.long_strike, trade.short_strike,
                trade.pnl,
                f"{trade.symbol}  {trade.opened_at.strftime('%b %d')} → {trade.closed_at.strftime('%b %d')}",
            ),
            use_container_width=True, key=f"trade_{i}",
        )
    st.divider()
