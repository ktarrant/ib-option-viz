"""Model Evaluation: is the price-target engine's forecast any good?

Judges the ranked engine as a *forecaster*, on the dense per-bar forecast log
rather than the sparse trades — CRPS skill against a random-walk null,
calibration (PIT + interval coverage), and predicted-vs-realized moves.
"""

from pathlib import Path

import polars as pl
import streamlit as st

from optionviz.backtest import BacktestResult, forecasts_frame
from optionviz.ui import charts, theme

st.set_page_config(page_title="Model Evaluation", page_icon="🎯", layout="wide")

# ------------------------------------------------------------------ inputs
DEFAULT_DIR = "../ib-spread-trader/out"
results_dir = st.sidebar.text_input("Results directory", value=DEFAULT_DIR)
results_root = Path(results_dir)

# Only ranked runs carry a model_eval section.
ranked: list[tuple[Path, BacktestResult]] = []
if results_root.is_dir():
    for f in sorted(results_root.rglob("backtest.json")):
        try:
            r = BacktestResult.load(f)
        except Exception:
            continue
        if r.model_eval:
            ranked.append((f, r))

if not ranked:
    st.info(
        f"No ranked backtests with a `model_eval` section found under `{results_dir}`. "
        "Run `cargo run -- backtest --config strategies/ranked_verticals.toml --out <dir>`."
    )
    st.stop()

labels = {str(f): (f.parent.name if f.parent != results_root else "default") for f, _ in ranked}
selected, result = st.sidebar.selectbox(
    "Backtest run", ranked, format_func=lambda pair: labels[str(pair[0])]
)

evaluation = result.model_eval
pooled = evaluation["pooled"]
per_symbol = evaluation["per_symbol"]

# ------------------------------------------------------------------ header
st.title(f"{result.strategy} — forecast quality")
st.caption(
    f"Expir{'ies' if len(result.expiries) > 1 else 'y'} {', '.join(result.expiries)}  ·  "
    f"{pooled['forecasts']:,} scored forecasts  ·  "
    f"judged against a random-walk null (driftless, vol = ATM IV)"
)

skill = pooled["crps_skill"]
# The verdict reads the weekly-block interval, not the point estimate: forecasts
# inside one week share most of a seven-day horizon and are nearly one bet.
# Runs written before the backtester carried the interval fall back to the
# point estimate and say so.
interval = pooled.get("crps_skill_interval")
if interval:
    lo, hi = interval
    span = f"**CRPS skill {skill:+.4f}**, 95% interval [{lo:+.4f}, {hi:+.4f}] over {pooled['weeks']} weeks"
    if lo > 0:
        st.success(f"{span} — the model beats a random walk, beyond noise.")
    elif hi < 0:
        st.error(f"{span} — the model forecasts *worse* than a random walk.")
    else:
        st.info(f"{span} — within noise of a random walk: not shown to beat it.")
else:
    st.warning(
        f"**CRPS skill {skill:+.4f}** — this run predates the weekly-block interval, "
        "so there is no way to tell it from noise here. Re-run it."
    )

# ------------------------------------------------------------------ headline
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("CRPS skill vs null", f"{skill:+.4f}")
c2.metric("PIT mean", f"{pooled['pit_mean']:.2f}", help="0.50 is unbiased; >0.5 forecasts low")
c3.metric("Coverage 50%", f"{pooled['coverage_50']:.2f}", help="nominal 0.50")
c4.metric("Coverage 90%", f"{pooled['coverage_90']:.2f}", help="nominal 0.90")
c5.metric(
    "Directional hit-rate",
    f"{pooled['directional_hit_rate']:.2f}" if pooled.get("directional_forecasts", 1) else "—",
    help="0.50 is a coin flip. \"—\": the model forecasts no direction (width only).",
)

c6, c7, c8 = st.columns(3)
c6.metric("Mean CRPS (model)", f"{pooled['crps_model']:.3f}")
c7.metric("Mean CRPS (null)", f"{pooled['crps_null']:.3f}")
c8.metric("Mean forecast σ (sharpness)", f"{pooled['mean_stddev']:.2f}")

# ------------------------------------------------------------------ per-symbol skill
st.divider()
st.subheader("CRPS skill by symbol")
st.caption("Does the model beat a random walk for this name? Green beats it, red loses to it.")
rows = sorted(per_symbol, key=lambda s: s["crps_skill"])
st.plotly_chart(
    charts.crps_skill_bars(
        ["Pooled"] + [s["symbol"] for s in rows],
        [skill] + [s["crps_skill"] for s in rows],
        "CRPS skill vs a random-walk null",
    ),
    width="stretch", key="skill",
)

# ------------------------------------------------------------------ calibration
st.divider()
st.subheader("Calibration")
col_pit, col_cov = st.columns([3, 2])
with col_pit:
    st.plotly_chart(
        charts.pit_histogram(pooled["pit_histogram"], "PIT histogram (flat = calibrated)"),
        width="stretch", key="pit",
    )
    st.caption(
        f"Total-variation distance from uniform: **{pooled['pit_calibration_error']:.3f}** "
        "(0 = perfectly calibrated). Mass shifted right means the forecast sits below "
        "what actually happens."
    )
with col_cov:
    st.markdown("**Interval coverage** — actual vs nominal")
    coverage = pl.DataFrame({
        "Interval": ["50%", "80%", "90%"],
        "Nominal": [0.50, 0.80, 0.90],
        "Actual": [pooled["coverage_50"], pooled["coverage_80"], pooled["coverage_90"]],
    })
    st.dataframe(
        coverage, hide_index=True, width="stretch",
        column_config={
            "Nominal": st.column_config.NumberColumn(format="%.2f"),
            "Actual": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "Actual below nominal → intervals too narrow or mis-centred; above → too wide."
    )

# ------------------------------------------------------------------ ranker
if result.scorer_eval:
    se = result.scorer_eval
    rho = se["spearman"]
    st.divider()
    st.subheader("Ranker — does the score predict realized P/L?")
    st.caption(
        f"{se['records']:,} candidates sampled across bars, opened and closed a horizon "
        "later on real quotes. This grades the ranker's ordering, not the model's forecast."
    )
    if rho > 0.10:
        st.success(
            f"**Spearman {rho:+.2f}** — the score discriminates: higher-scored candidates "
            "realized more. The ranking premise holds."
        )
    elif rho < -0.10:
        st.error(
            f"**Spearman {rho:+.2f}** — the score is inverted: it ranks the wrong candidates "
            "higher. The ranker is the bottleneck."
        )
    else:
        st.info(
            f"**Spearman {rho:+.2f}** — the score barely tracks realized P/L; the ranked "
            "selection is close to random."
        )

    m1, m2, m3 = st.columns(3)
    m1.metric("Spearman (score vs realized)", f"{rho:+.2f}")
    m2.metric("Sampled candidates", f"{se['records']:,}")
    m3.metric(
        "Mean realized P/L ÷ risk",
        f"{se['mean_realized_pl_per_risk']:+.2f}",
        help="Across all sampled candidates — the average vertical held to the horizon",
    )

    st.plotly_chart(
        charts.scorer_deciles(se["deciles"], "Realized P/L per risk by score decile"),
        width="stretch", key="deciles",
    )
    top = se["deciles"][-1]["mean_realized_pl_per_risk"]
    avg = se["mean_realized_pl_per_risk"]
    st.caption(
        f"The ladder climbs with the score, but the candidate universe is negative on "
        f"average ({avg:+.2f} per risk) — round-tripping a vertical on crossed quotes bleeds "
        f"the spread. The edge is in avoiding the worst: only the top decile ({top:+.2f}) "
        "approaches break-even, and the ranker takes above even that. The score also values "
        "the exit at model mid while the realized close crosses the book, so it reads "
        "optimistically."
    )

# ------------------------------------------------------------------ per-bar detail
forecasts = forecasts_frame(selected)
if forecasts is not None and forecasts.height > 0:
    st.divider()
    st.subheader("Predicted vs realized")

    moves = forecasts.with_columns(
        (pl.col("mean") / pl.col("spot") - 1.0).alias("pred_move"),
        (pl.col("realized") / pl.col("spot") - 1.0).alias("real_move"),
    )
    pred_spread = moves.select(pl.col("pred_move").std()).item() or 0.0
    if pred_spread < 1e-4:
        st.info(
            "This model is **driftless**: its forecast mean equals spot by construction, "
            "so the predicted move is ~0 on every bar and this scatter collapses to a "
            "vertical line. A driftless model makes no directional bet — its quality is a "
            "**calibration** story (the PIT, coverage, and bands), not a directional one. "
            "Turn a drift weight up in the config's `[model]` section and this chart "
            "becomes informative."
        )
    else:
        st.plotly_chart(
            charts.predicted_vs_realized(
                moves["pred_move"].to_list(), moves["real_move"].to_list(),
                "Predicted move vs realized move (each point is one bar)",
            ),
            width="stretch", key="scatter",
        )
        st.caption(
            "Points on the dotted diagonal are perfect directional calls; a cloud with no "
            "slope means the drift carries no directional information."
        )

    st.subheader("Forecast bands over time")
    symbols = forecasts["symbol"].unique().sort().to_list()
    symbol = st.selectbox("Symbol", symbols, key="band_symbol")
    one = forecasts.filter(pl.col("symbol") == symbol).sort("ts")
    st.plotly_chart(
        charts.forecast_band(one, f"{symbol}: 5–95% band, median, and where price landed"),
        width="stretch", key="band",
    )
    inside = one.filter(
        (pl.col("realized") >= pl.col("q05")) & (pl.col("realized") <= pl.col("q95"))
    ).height
    st.caption(
        f"{inside}/{one.height} ({100 * inside / one.height:.0f}%) of realized prints "
        f"landed inside {symbol}'s 5–95% band (nominal 90%)."
    )
else:
    st.divider()
    st.info("No `forecasts.csv` beside this run — re-run with `--out` to get the per-bar detail.")

# ------------------------------------------------------------------ caveats
if result.caveats:
    with st.expander("Caveats", expanded=False):
        for caveat in result.caveats:
            st.markdown(f"- {caveat}")
