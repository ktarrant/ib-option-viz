"""Backtest Viewer: render trade-by-trade results from ib-spread-trader."""

from datetime import timedelta
from pathlib import Path

import polars as pl
import streamlit as st

from optionviz.backtest import BacktestResult, position_marks, underlying_bars
from optionviz.ui import charts, data, theme

st.set_page_config(page_title="Backtest", page_icon="🎯", layout="wide")


def _exit_conditions(ext: dict) -> list[str]:
    """The exit rules a run configured, as short human-readable strings."""
    conditions = []
    if ext.get("profit_target_pct") is not None:
        conditions.append(f"Profit target: {ext['profit_target_pct']:.0f}% of max")
    if ext.get("stop_loss_pct") is not None:
        conditions.append(f"Stop loss: {ext['stop_loss_pct']:.0f}% of debit")
    if ext.get("underlying_past_short_strike"):
        conditions.append("Underlying past short strike")
    if ext.get("max_time_pct") is not None:
        conditions.append(f"Time decay: {ext['max_time_pct']:.0f}% of life elapsed")
    if ext.get("close_before_earnings"):
        conditions.append("Before earnings")
    if ext.get("close_after_earnings"):
        conditions.append("After a held release")
    if ext.get("max_hold_days") is not None:
        conditions.append(f"Held {ext['max_hold_days']:g} days")
    if ext.get("close_after_forecast_horizon"):
        conditions.append("Forecast horizon elapsed")
    if ext.get("forecast_target_quantile") is not None:
        conditions.append(f"Forecast target q{ext['forecast_target_quantile']:.2f}")
    if ext.get("forecast_stop_quantile") is not None:
        conditions.append(f"Adverse-forecast stop q{ext['forecast_stop_quantile']:.2f}")
    if ext.get("live_pop_floor") is not None:
        conditions.append(f"Live POP < {ext['live_pop_floor']:.0%}")
    if ext.get("naked_trailing_stop_pct") is not None:
        conditions.append(f"Trailing stop {ext['naked_trailing_stop_pct']:.0f}% of peak")
    if ext.get("naked_momentum_exit"):
        conditions.append("Momentum flip")
    if ext.get("stock_bar_exits"):
        conditions.append("Price exits watched on the 5-min tape")
    if ext.get("min_days_before_expiry") is not None:
        conditions.append(f"Before expiry: ≥{ext['min_days_before_expiry']}d before exp")
    if not conditions:
        conditions.append("None (hold to expiry)")
    return conditions


def _render_staged_config(cfg: dict) -> None:
    """The signal → structure → selector pipeline's configuration."""
    sig = cfg.get("signal", {})
    struct = cfg.get("structure", {})
    exe = cfg.get("execution", {})
    ext = cfg.get("exit", {})

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
        for c in _exit_conditions(ext):
            st.markdown(f"- {c}")


def _render_ranked_config(cfg: dict) -> None:
    """The price-target engine's configuration: model, candidate universe, and
    ranking. The staged `signal`/`structure` fields still carry defaults in a
    ranked config but are unused, so they are deliberately not shown."""
    model = cfg.get("model", {})
    feats = cfg.get("features", {})
    cand = cfg.get("candidates", {})
    rank = cfg.get("ranking", {})
    exe = cfg.get("execution", {})
    ext = cfg.get("exit", {})

    col_model, col_cand, col_rank = st.columns(3)

    with col_model:
        st.markdown("**Price model** — lognormal")
        rvw = model.get("realized_vol_weight", 0.5)
        st.markdown(f"Vol: {rvw:.0%} realized / {1 - rvw:.0%} ATM IV")
        scale, earn = model.get("vol_multiplier", 1.0), model.get("earnings_vol_multiplier", 1.0)
        if scale != 1.0 or earn != 1.0:
            st.markdown(f"Vol × {scale:g}, × {earn:g} with earnings inside the horizon")
        if model.get("crush_to_median"):
            st.markdown("Legs revalued at median IV across a release (crush)")
        st.markdown(f"Horizon: {model.get('horizon_days', 7):g}d (capped at expiry)")
        drift = [
            f"{name} ×{model.get(key, 0.0):.2f}"
            for key, name in (("trend_weight", "trend"), ("vwap_reversion", "VWAP"), ("skew_weight", "skew"))
            if model.get(key, 0.0)
        ]
        st.markdown("Drift: " + (", ".join(drift) if drift else "none — width only"))
        st.markdown(
            f"Grid: {model.get('grid_points', 101)} pts × "
            f"±{model.get('grid_sigma', 4.0):.0f}σ"
        )
        st.markdown(
            f"Features every {feats.get('cadence_minutes', 30)} min: "
            f"SMA {feats.get('trend_fast', 10)}/{feats.get('trend_slow', 30)}, "
            f"ATR {feats.get('atr_period', 14)}, RV {feats.get('realized_vol_period', 20)}, "
            f"skew ±{feats.get('skew_moneyness', 0.05):.0%}"
        )

    with col_cand:
        st.markdown("**Candidates**")
        rights = ", ".join(r.title() for r in cand.get("rights", ["call", "put"]))
        structures = [
            name for key, name in (
                ("allow_debit", "debit verticals"), ("allow_credit", "credit verticals"),
                ("allow_naked_long", "naked longs"), ("allow_straddle", "straddles"),
                ("allow_strangle", "strangles"), ("allow_iron_condor", "iron condors"),
                ("allow_iron_butterfly", "iron butterflies"),
            ) if cand.get(key, key in ("allow_debit", "allow_credit", "allow_naked_long"))
        ]
        st.markdown(f"{rights}: {', '.join(structures) or 'none'}")
        width = f"≥{cand.get('min_width', 0):g}"
        if cand.get("max_width") is not None:
            width += f", ≤{cand['max_width']:g}"
        st.markdown(f"Width: {width}")
        st.markdown(f"Max book spread: {cand.get('max_relative_spread', 0.5):.0%} of mid")
        st.markdown(
            f"Max loss: ${cand.get('max_loss', 500):,.0f} × {cand.get('quantity', 1)}"
        )

    with col_rank:
        metric = rank.get("metric", "expected_value")
        st.markdown(
            "**Ranking** — "
            + ("probability of profit" if metric == "prob_profit" else "E[P/L] ÷ max loss")
        )
        st.markdown(f"Min score: {rank.get('min_score', 0.10):.2f}")
        if rank.get("min_prob_profit") is not None:
            st.markdown(f"Min prob. of profit: {rank['min_prob_profit']:.0%}")
        st.markdown(f"Min E[P/L]: ${rank.get('min_expected_pl', 0):,.0f}")
        gates = [
            f"timing {exe.get('timing', 'next_bar').replace('_', ' ')}",
            f"max open {exe.get('max_open_positions', 1)}",
        ]
        if exe.get("min_entry_days_before_expiry"):
            gates.append(f"no entry <{exe['min_entry_days_before_expiry']}d to expiry")
        if exe.get("earnings_blackout_days"):
            gates.append(f"earnings blackout {exe['earnings_blackout_days']}d")
        st.markdown("Gates: " + ", ".join(gates))

    st.markdown(
        "**Exit conditions** (any triggers close): "
        + " · ".join(_exit_conditions(ext))
    )

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
st.caption(
    f"Expir{'ies' if len(result.expiries) > 1 else 'y'} {', '.join(result.expiries)}  ·  "
    f"{result.bar_minutes}-minute bars  ·  {len(result.symbols)} symbols  ·  "
    f"{len(result.trades)} trades"
)
if result.labels:
    st.caption("Composed of " + " · ".join(f"{k} **{v}**" for k, v in sorted(result.labels.items())))

# ------------------------------------------------------------------ config
with st.expander("Strategy configuration", expanded=True):
    cfg = result.config
    if cfg.get("mode") == "ranked":
        _render_ranked_config(cfg)
    else:
        _render_staged_config(cfg)

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

c6, c7, c7b, c8, c9 = st.columns(5)
c6.metric("Traded P&L", f"${totals['traded_pnl']:+,.2f}")
c7.metric("Settled P&L", f"${totals['settled_pnl']:+,.2f}")
c7b.metric(
    "Held through earnings",
    f"${totals['earnings_pnl']:+,.2f}" if "earnings_pnl" in totals else "—",
    help=f"{totals.get('earnings_trades', 0)} trade(s) open across a release — a few binary "
    "events, reported apart so they neither hide in nor swamp the rest",
)
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

# Candidate funnel (ranked mode): every vertical the ladder could supply,
# cascading down to the ones the model actually took.
ranking = totals.get("census", {}).get("ranking", {})
if ranking.get("enumerated", 0) > 0:
    funnel_order = [
        ("enumerated", "Enumerated"),
        ("priceable", "Priceable"),
        ("liquid", "Liquid"),
        ("within_loss", "Within loss cap"),
        ("balanced", "In reward/risk band"),
        ("positive_ev", "Positive EV"),
        ("above_min_score", "Above min score"),
        ("above_min_pop", "Above min POP"),
        ("chosen", "Chosen"),
    ]
    parts = [f"{ranking[k]:,} {label}" for k, label in funnel_order if k in ranking]
    st.caption("Candidate funnel: " + " → ".join(parts))

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
    # `invested` (capital at risk in open positions) is written by newer runs;
    # older equity.csv files have only ts,equity.
    invested = eq["invested"].to_list() if "invested" in eq.columns else None
    st.plotly_chart(
        charts.equity_curve(
            eq["ts"].to_list(), eq["equity"].to_list(),
            totals["capital_deployed"], "Equity curve (all symbols combined)",
            invested=invested,
        ),
        width="stretch", key="equity",
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
        sym_df, hide_index=True, width="stretch",
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
# Per-bar position state, when the run recorded it. Older runs have no
# positions.csv, and an empty frame means "not recorded", not "nothing happened".
marks_frame = position_marks(selected)


def get_bars(symbol: str) -> pl.DataFrame:
    if symbol not in symbols_with_bars:
        symbols_with_bars[symbol] = underlying_bars(catalog, symbol, result.bar_minutes)
    return symbols_with_bars[symbol]


for i, trade in enumerate(result.trades):
    pnl_color = p.good if trade.is_winner else p.critical
    exit_label = trade.exit_label

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
        f'{theme.badge(exit_label, pnl_color)} &nbsp; '
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
                forecast_target=trade.forecast_target,
                forecast_stop=trade.forecast_stop,
                legs=trade.legs,
            ),
            width="stretch", key=f"trade_{i}",
        )

    # The dynamic exits cannot be drawn on the price chart: a trailing stop is a
    # P&L level riding the position's own peak, not a price. Its own panel is
    # where it belongs, and it appears only when the rule was actually armed.
    if marks_frame.height:
        track = marks_frame.filter(pl.col("position_id") == trade.id)
        if track.height and track["trailing_stop"].null_count() < track.height:
            st.plotly_chart(
                charts.position_track(track, trade.pnl, "Gross P&L and the trailing stop"),
                width="stretch", key=f"track_{i}",
            )
    st.divider()
