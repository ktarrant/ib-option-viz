"""Cached loaders shared by the pages."""

from __future__ import annotations

import polars as pl
import streamlit as st

from optionviz.cache import load_all
from optionviz.catalog import Catalog
from optionviz.events import DerivedEventSource, combine


@st.cache_resource(show_spinner="Scanning snapshots…")
def get_catalog(directory: str | None = None) -> Catalog:
    return Catalog.discover(directory)


@st.cache_resource(show_spinner="Rebuilding forwards and implied vols…")
def get_chains(fingerprint: str, _catalog: Catalog):
    # fingerprint is the cache key; a new snapshot file invalidates it.
    return load_all(_catalog)


@st.cache_data(show_spinner="Scoring boundaries…")
def get_events(fingerprint: str, _chains, min_surprise: float) -> pl.DataFrame:
    derived = DerivedEventSource(min_surprise=min_surprise).events(_chains)
    # When the capture side grows a labelled `events` table, its frame goes
    # first here and its labels win at the same symbol/day.
    return combine(derived)


def require_catalog():
    cat = get_catalog()
    if cat.is_empty:
        st.error("No capture snapshots found.")
        st.markdown(
            "Point `OPTIONVIZ_DB_DIR` at the folder holding your "
            "`*.sqlite3` captures, or drop one into "
            f"`{__import__('optionviz.catalog', fromlist=['default_dir']).default_dir()}`."
        )
        st.stop()
    return cat
