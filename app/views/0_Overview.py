"""Overview: what the catalog holds, and how healthy it is."""

import polars as pl
import streamlit as st

from optionviz.ui import data


st.title("Options history")
st.caption(
    "Reconstructed from IB capture snapshots. The underlying series, the implied "
    "vols and the greeks are all derived from option bid/ask ladders by put-call "
    "parity — the capture stores none of them."
)

catalog = data.require_catalog()
chains = data.get_chains(catalog.fingerprint, catalog)

if not chains:
    st.warning("Snapshots found, but no chain has 30-minute bars yet.")
    st.stop()

events = data.get_events(catalog.fingerprint, catalog, chains, 3.0)

# ---------------------------------------------------------------- headline
n_earnings = events.filter(pl.col("kind") == "earnings").height
total_bars = sum(s.n_bars for s in catalog.snapshots)
span = catalog.best_chains()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Chains", len(chains))
c2.metric("Bars", f"{total_bars:,}")
c3.metric("Events found", events.height)
c4.metric("Earnings reactions", n_earnings)

st.page_link("views/1_Events.py", label="Browse the events", icon=":material/arrow_forward:")
st.divider()

# ---------------------------------------------------------------- cohort
st.subheader("Cohort")
rows = []
for (symbol, expiry), chain in sorted(chains.items()):
    last = chain.atm.drop_nulls("atm_iv").sort("ts").tail(1)
    if not last.height:
        continue
    r = last.row(0, named=True)
    ev = events.filter((pl.col("symbol") == symbol) & (pl.col("expiry") == expiry))
    rows.append({
        "Symbol": symbol,
        "Expiry": expiry,
        "Bars": chain.surface.height,
        "Strikes": chain.surface["strike"].n_unique(),
        "From": chain.atm["ts"].min().strftime("%Y-%m-%d"),
        "To": chain.atm["ts"].max().strftime("%Y-%m-%d"),
        "Forward": r["forward"],
        "ATM IV": r["atm_iv"] * 100,
        "Expected move": r["expected_move"] * 100,
        "Events": ev.height,
        "Earnings": ev.filter(pl.col("kind") == "earnings").height,
    })

st.dataframe(
    pl.DataFrame(rows).sort("ATM IV", descending=True),
    hide_index=True, width="stretch",
    column_config={
        "Bars": st.column_config.NumberColumn(format="%d"),
        "Forward": st.column_config.NumberColumn(format="%.2f"),
        "ATM IV": st.column_config.NumberColumn(
            format="%.1f%%", help="Interpolated at the forward from the OTM smile"
        ),
        "Expected move": st.column_config.NumberColumn(
            format="%.1f%%", help="ATM straddle as a fraction of the forward"
        ),
    },
)
st.caption("ATM IV and expected move are as of each chain's last captured bar.")

# ---------------------------------------------------------------- snapshots
st.subheader("Snapshots")
for snap in catalog.snapshots:
    st.markdown(
        f"**{snap.path.name}** — {snap.n_bars:,} bars · schema v{snap.user_version} · "
        f"{snap.size / 1e6:.0f} MB"
    )
st.caption(
    "Each capture harvests one soon-to-expire chain, because expired options have "
    "no retrievable history. Where snapshots overlap, each chain is read from "
    "whichever file holds the most bars for it."
)
