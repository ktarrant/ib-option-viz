"""Event Explorer: rank the moments, then anatomise the selected one."""

import polars as pl
import streamlit as st

from optionviz.scoring import straddle_verdict
from optionviz.ui import charts, data, theme

st.set_page_config(page_title="Events", page_icon="⚡", layout="wide")

catalog = data.require_catalog()
chains = data.get_chains(catalog.fingerprint, catalog)
if not chains:
    st.warning("No chain has 30-minute bars yet.")
    st.stop()

st.title("Events")

# ------------------------------------------------------------------ filters
KINDS = ["earnings", "macro", "shock", "move"]
f1, f2, f3, f4 = st.columns([2, 2, 2, 1.4])
symbols = f1.multiselect("Symbol", sorted({s for s, _ in chains}), default=[])
kinds = f2.multiselect("Kind", KINDS, default=["earnings", "macro", "shock"])
boundary = f3.multiselect("Boundary", ["overnight", "intraday"], default=[])
min_surprise = f4.slider("Min surprise", 2.0, 12.0, 3.0, 0.5, help="Robust sigmas vs the symbol's own history")

events = data.get_events(catalog.fingerprint, chains, min_surprise)
if symbols:
    events = events.filter(pl.col("symbol").is_in(symbols))
if kinds:
    events = events.filter(pl.col("kind").is_in(kinds))
if boundary:
    events = events.filter(pl.col("boundary").is_in(boundary))

if not events.height:
    st.info("No events match these filters. Lower the surprise threshold or widen the kinds.")
    st.stop()

p = theme.palette()
left, right = st.columns([1, 2.1], gap="large")

# ------------------------------------------------------------------ the list
with left:
    st.caption(f"{events.height} events, highest score first")
    listing = events.select(
        pl.col("symbol").alias("Symbol"),
        pl.col("ts_after").dt.strftime("%b %d %H:%M").alias("When"),
        pl.col("kind").alias("Kind"),
        (pl.col("ret") * 100).alias("Move"),
        pl.col("score").alias("Score"),
    )
    picked = st.dataframe(
        listing, hide_index=True, width="stretch", height=620,
        on_select="rerun", selection_mode="single-row",
        column_config={
            "Move": st.column_config.NumberColumn(format="%+.1f%%"),
            "Score": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    idx = picked["selection"]["rows"][0] if picked["selection"]["rows"] else 0

event = events.row(idx, named=True)
chain = chains[(event["symbol"], event["expiry"])]

# ------------------------------------------------------------------ detail
with right:
    label = event["label"] or ""
    st.markdown(
        f"### {event['symbol']} · {event['ts_after'].strftime('%B %d, %Y')} "
        f"{theme.kind_badge(event['kind'])} "
        f"{theme.badge(event['boundary'].title(), p.muted, subtle=True)} {label}",
        unsafe_allow_html=True,
    )

    verdict = straddle_verdict(chain, event["ts_before"], event["ts_after"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Forward", f"{event['forward_after']:,.2f}", f"{event['ret'] * 100:+.2f}%")
    m2.metric("ATM IV", f"{event['atm_iv_after'] * 100:.0f}%",
              f"{event['crush'] * 100:+.0f}%", delta_color="off")
    m3.metric("Surprise", f"{event['surprise']:.1f}σ",
              help="Robust sigmas against this symbol's own same-kind boundaries")
    if event["n_cohort"] and event["n_cohort"] > 1:
        m4.metric("Moved together", f"{event['n_cohort']} symbols",
                  help="Symbols with an event on this same bar")
    else:
        m4.metric("Moved together", "Alone", help="No other symbol moved on this bar")

    # ---- who won -------------------------------------------------------
    if verdict is not None:
        st.markdown(
            f"#### The {verdict.strike:,.0f} straddle "
            f"{theme.badge(verdict.verdict + 'S WIN', theme.verdict_color(verdict.verdict))}",
            unsafe_allow_html=True,
        )
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Cost to buy", f"${verdict.ask_before:,.2f}",
                  help="Lifting both offers going in")
        v2.metric("Worth after", f"${verdict.bid_after:,.2f}",
                  help="Hitting both bids coming out")
        v3.metric("Buyer", f"{verdict.buyer_pnl_pct * 100:+.0f}%",
                  f"${verdict.buyer_pnl:+,.2f}")
        v4.metric("Seller", f"{verdict.seller_pnl_pct * 100:+.0f}%",
                  f"${verdict.seller_pnl:+,.2f}")
        st.caption(
            f"Priced at executable prices, not mid. The move needed "
            f"{verdict.breakeven_move * 100:.1f}% to break even and delivered "
            f"{verdict.realized_move * 100:.1f}%. The round trip cost "
            f"{verdict.round_trip_cost_pct * 100:.0f}% of premium in spread — "
            f"mid-to-mid would have shown {verdict.pnl_mid_pct * 100:+.0f}%."
        )
    else:
        st.info("One leg of the ATM straddle was unquoted at this boundary.")

    # ---- price and vol, stacked on a shared clock -----------------------
    span = pl.duration(days=3)
    window = chain.atm.filter(
        (pl.col("ts") >= event["ts_before"] - span) & (pl.col("ts") <= event["ts_after"] + span)
    ).sort("ts")

    st.plotly_chart(
        charts.forward_series(window, event["ts_before"], event["ts_after"],
                              "Forward price, 30-minute bars"),
        width="stretch", key="fwd",
    )
    st.plotly_chart(
        charts.atm_iv_series(window, event["ts_before"], event["ts_after"],
                             "At-the-money implied vol"),
        width="stretch", key="iv",
    )

    # ---- the whole surface's reaction -----------------------------------
    before = chain.surface.filter(pl.col("ts") == event["ts_before"])
    after = chain.surface.filter(pl.col("ts") == event["ts_after"])
    ladder = (
        before.select("strike", "right", pl.col("mid").alias("mid_before"))
        .join(after.select("strike", "right", pl.col("mid").alias("mid_after")),
              on=["strike", "right"], how="inner")
        .filter(pl.col("mid_before") > 0.05)  # a 2c option's % change is noise
        .with_columns(((pl.col("mid_after") - pl.col("mid_before")) / pl.col("mid_before")).alias("pct_change"))
    )

    tab_ladder, tab_smile, tab_table = st.tabs(["Strike ladder", "Vol smile", "Table"])
    with tab_ladder:
        if ladder.height:
            st.plotly_chart(
                charts.strike_ladder(ladder, "Percent change by strike, across the boundary"),
                width="stretch", key="ladder",
            )
            st.caption("Strikes priced under $0.05 before the event are excluded — their percent change is noise.")
        else:
            st.info("No strike is quoted on both sides of this boundary.")
    with tab_smile:
        st.plotly_chart(
            charts.smile(before.filter(pl.col("is_otm")), after.filter(pl.col("is_otm")),
                         "Implied vol against moneyness"),
            width="stretch", key="smile",
        )
        st.caption("Out-of-the-money side only, which is where the vol information lives.")
    with tab_table:
        st.dataframe(
            ladder.sort(["right", "strike"]).select(
                pl.col("strike").alias("Strike"), pl.col("right").alias("Right"),
                pl.col("mid_before").alias("Before"), pl.col("mid_after").alias("After"),
                (pl.col("pct_change") * 100).alias("Change"),
            ),
            hide_index=True, width="stretch", height=420,
            column_config={
                "Before": st.column_config.NumberColumn(format="$%.2f"),
                "After": st.column_config.NumberColumn(format="$%.2f"),
                "Change": st.column_config.NumberColumn(format="%+.0f%%"),
            },
        )
