"""Backtest Viewer: render trade-by-trade results from ib-spread-trader."""

from datetime import timedelta
from pathlib import Path

import polars as pl
import streamlit as st

from optionviz.backtest import BacktestResult, underlying_bars
from optionviz.ui import charts, data, theme

st.set_page_config(page_title="Backtest", page_icon="🎯", layout="wide")

# ------------------------------------------------------------------ inputs
catalog = data.require_catalog()

DEFAULT_DIR = "../ib-spread-trader/out"
results_dir = st.sidebar.text_input("Results directory", value=DEFAULT_DIR)

# Discover all backtest.json files under the directory.
results_root = Path(results_dir)
backtest_files: list[Path] = sorted(results_root.rglob("backtest.json")) if results_root.is_dir() else []

if not backtest_files:
    st.info(f"No `backtest.json` files found under `{results_dir}`.")
    st.stop()

labels = {str(f): f.parent.name if f.parent != results_root else "default" for f in backtest_files}
selected = st.sidebar.selectbox(
    "Backtest run",
    backtest_files,
    format_func=lambda f: labels[str(f)],
)

try:
    result = BacktestResult.load(selected)
except Exception as e:
    st.error(f"Failed to load: {e}")
    st.stop()

# ------------------------------------------------------------------ header
st.title(result.strategy)
st.caption(f"Expiry {result.expiry}  ·  {len(result.symbols)} symbols  ·  {len(result.trades)} trades")

# ------------------------------------------------------------------ config
with st.expander("Strategy configuration", expanded=True):
    cfg = result.config
    sig = cfg.get("signal", {})
    struct = cfg.get("structure", {})
    exe = cfg.get("execution", {})
    ext = cfg.get("exit", {})
    costs = cfg.get("costs", {})

    col_sig, col_struct, col_exit = st.columns(3)

    with col_sig:
        st.markdown("**Signal**")
        kind = sig.get("kind", "—")
        if kind == "rsi_cross":
            st.markdown(f"RSI({sig.get('period', 14)}) crosses {sig.get('level', 30)}")
        elif kind == "sma_cross":
            st.markdown(f"SMA({sig.get('fast', 5)}) / SMA({sig.get('slow', 20)})")
        elif kind == "volatility_contraction_breakout":
            measure = sig.get("measure", "bollinger_width").replace("_", " ").title()
            st.markdown(f"{measure} squeeze → breakout")
            st.markdown(f"Vol period: {sig.get('volatility_period', 20)}, "
                        f"lookback: {sig.get('contraction_lookback', 120)}")
            st.markdown(f"Percentile: {sig.get('contraction_percentile', 0.10):.0%}, "
                        f"breakout: {sig.get('breakout_period', 20)} bars")
            st.markdown(f"Volume: ≥{sig.get('min_volume_ratio', 1.5):.2f}× slot median")
            st.markdown(f"Max ATM IV rise: {sig.get('max_atm_iv_increase', 0.10):.0%}")
        else:
            st.markdown(kind)

    with col_struct:
        st.markdown("**Structure**")
        right = struct.get("right", "call")
        long_d = struct.get("long_delta", 0.5)
        short_d = struct.get("short_delta", 0.3)
        qty = struct.get("quantity", 1)
        st.markdown(
            f"{right.title()} vertical: {long_d:.2f}δ / {short_d:.2f}δ × {qty}"
        )
        if exe.get("max_debit"):
            st.markdown(f"Max debit: ${exe['max_debit']:,.0f}")
        st.markdown(f"Tolerance: ±{exe.get('delta_tolerance', 0.07):.2f}δ")
        if exe.get("min_entry_days_before_expiry"):
            st.markdown(f"No entries within {exe['min_entry_days_before_expiry']}d of expiry")

    with col_exit:
        st.markdown("**Exit conditions** (any triggers close)")
        conditions = []
        if ext.get("profit_target_pct") is not None:
            conditions.append(f"Profit target: {ext['profit_target_pct']:.0f}% of max")
        if ext.get("stop_loss_pct") is not None:
            conditions.append(f"Stop loss: {ext['stop_loss_pct']:.0f}% of debit")
        if ext.get("underlying_past_short_strike"):
            conditions.append("Underlying past short strike")
        if ext.get("max_time_pct") is not None:
            conditions.append(f"Time decay: {ext['max_time_pct']:.0f}% of life elapsed")
        if ext.get("min_days_before_expiry") is not None:
            conditions.append(f"Before expiry: ≥{ext['min_days_before_expiry']}d before exp")
        if not conditions:
            conditions.append("None (hold to expiry)")
        for c in conditions:
            st.markdown(f"- {c}")

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

# Exit breakdown
exits = totals.get("exits_by_reason", {})
if exits:
    st.caption("Exits: " + " · ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(exits.items())))

# Signal diagnostics (gate funnel)
diagnostics = totals.get("census", {}).get("signal_diagnostics", {})
if diagnostics:
    gate_order = ["bars_seen", "bars_ready", "contraction_pass", "breakout_pass",
                  "volume_pass", "iv_pass"]
    parts = []
    for gate in gate_order:
        if gate in diagnostics:
            label = gate.replace("_", " ").title()
            parts.append(f"{diagnostics[gate]:,} {label}")
    extra = {k: v for k, v in diagnostics.items()
             if k not in gate_order and k != "missing_underlying"}
    for k, v in sorted(extra.items()):
        parts.append(f"{v:,} {k.replace('_', ' ').title()}")
    if parts:
        st.caption("Signal funnel: " + " → ".join(parts))
    missing = diagnostics.get("missing_underlying", 0)
    if missing:
        st.caption(f"({missing:,} bars had no underlying data)")

synthetic_count = totals.get("census", {}).get("synthetic_exits", 0)
if synthetic_count:
    st.warning(
        f"⚠ {synthetic_count} exit{'s' if synthetic_count > 1 else ''} "
        f"priced at intrinsic — option quotes were unavailable. "
        f"P&L on {'those' if synthetic_count > 1 else 'that'} "
        f"trade{'s is' if synthetic_count > 1 else ' is'} estimated."
    )

# ------------------------------------------------------------------ equity
equity_path = str(selected).replace("backtest.json", "equity.csv")
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

    synthetic_badge = (
        f' &nbsp; {theme.badge("⚠ Intrinsic", p.warning)}'
        if trade.synthetic_price
        else ""
    )
    st.markdown(
        f"**#{trade.id} {trade.description}** &nbsp; "
        f'{theme.badge(exit_label, pnl_color)} &nbsp; '
        f'{theme.badge(f"${trade.pnl:+,.2f}", pnl_color, subtle=True)}'
        f"{synthetic_badge}",
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
