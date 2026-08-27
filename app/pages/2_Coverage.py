"""What was asked for, what came back, and what the derivation had to discard.

`bar_count = 0` is a real answer, not a gap: IB was asked and had nothing, which
for a deep-OTM strike is the normal case. The whole point of this page is to
show that distinction rather than render it as missing data.
"""

import polars as pl
import streamlit as st

from optionviz import frames
from optionviz.ui import data

st.set_page_config(page_title="Coverage", page_icon="🗂️", layout="wide")

catalog = data.require_catalog()
st.title("Coverage")

snap_names = [s.path.name for s in catalog.snapshots]
chosen = st.selectbox("Snapshot", snap_names)
snap = next(s for s in catalog.snapshots if s.path.name == chosen)

cov = frames.coverage(snap.path)
if not cov.height:
    st.info("This snapshot has no capture_coverage rows.")
    st.stop()

asked = cov.height
empty = cov.filter(pl.col("bar_count") == 0).height
c1, c2, c3 = st.columns(3)
c1.metric("Requests recorded", f"{asked:,}")
c2.metric("Returned data", f"{asked - empty:,}")
c3.metric("Asked, genuinely empty", f"{empty:,}",
          help="IB was asked and had nothing — most often a strike that never traded")

st.caption(
    "The requested window is deliberately wider than the data returned: IB retains "
    "roughly 52 days of 30-minute option history and a larger duration costs the "
    "same single request. Use the first/last bar timestamps, not the window, to see "
    "the real extent."
)
st.divider()

st.subheader("By symbol and expiry")
by_chain = (
    cov.group_by("symbol", "expiry", "what_to_show")
       .agg(
           pl.len().alias("requests"),
           pl.col("bar_count").sum().alias("bars"),
           (pl.col("bar_count") == 0).sum().alias("empty"),
           pl.col("first_ts").min().alias("first"),
           pl.col("last_ts").max().alias("last"),
       )
       .sort("symbol", "expiry", "what_to_show")
)
st.dataframe(
    by_chain.with_columns(
        pl.col("first").str.slice(0, 10), pl.col("last").str.slice(0, 10)
    ),
    hide_index=True, width="stretch", height=420,
)

st.subheader("Derivation losses")
st.caption(
    "Rows dropped turning raw bars into a clean mid series — for the option "
    "ladder and, where the snapshot has it, the real stock tape. Unpaired means "
    "only one side of the market was quoted; crossed means bid above ask."
)
chains = data.get_chains(catalog.fingerprint, catalog)
q_rows = []
for (symbol, expiry) in sorted(chains):
    path = catalog.resolve(symbol, expiry)
    _, oq = frames.option_mids(path, symbol, expiry)
    _, sq = frames.stock_intraday(path, symbol)
    q_rows.append({
        "Symbol": symbol, "Expiry": expiry,
        **{f"option_{k}": v for k, v in oq.as_dict().items()},
        **{f"spot_{k}": v for k, v in sq.as_dict().items()},
    })
st.dataframe(pl.DataFrame(q_rows), hide_index=True, width="stretch")

st.subheader("Events calendar")
snap_symbols = cov["symbol"].unique().sort().to_list()
all_events = pl.concat(
    [frames.events_for_symbol(snap.path, s) for s in snap_symbols], how="vertical"
) if snap_symbols else pl.DataFrame()
if not all_events.height:
    st.info(
        "This snapshot has no `events` table (schema v6+) or no rows in it — "
        "the Events page falls back to statistical detection alone."
    )
else:
    events_df = data.get_events(catalog.fingerprint, catalog, chains, 3.0)
    in_window = events_df.filter(pl.col("source") != "derived").height
    e1, e2, e3 = st.columns(3)
    e1.metric("Calendar rows", f"{all_events.height:,}", help="Across all symbols, full history")
    e2.metric("Symbols covered", f"{all_events['symbol'].n_unique()}")
    e3.metric("Matched to a captured boundary", f"{in_window}",
              help="Calendar entries landing inside this snapshot's bar window — "
                   "these are the ones the Events page can show as Confirmed")
    st.caption(
        "The calendar runs back to each symbol's earliest SEC filing; only entries "
        "falling inside the captured bar window can be matched to a real boundary "
        "and shown as Confirmed on the Events page."
    )
    st.dataframe(
        all_events.group_by("symbol").agg(
            pl.len().alias("rows"), pl.col("event_date").min().alias("earliest"),
            pl.col("event_date").max().alias("latest"),
        ).sort("symbol"),
        hide_index=True, width="stretch",
    )
st.divider()

st.subheader("Catalogued but uncaptured")
st.caption(
    "Contracts the catalog knows about that have no bars in this snapshot. A later "
    "capture of the same expiry fills these in."
)
chain_bars = catalog.chains.filter(pl.col("path") == str(snap.path))
all_chains = data.get_catalog().best_chains()
uncaptured = frames.coverage(snap.path).filter(pl.col("bar_count") == 0).group_by(
    "symbol", "expiry"
).agg(pl.len().alias("empty_requests")).sort("empty_requests", descending=True)
st.dataframe(uncaptured, hide_index=True, width="stretch")
