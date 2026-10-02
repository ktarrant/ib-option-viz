"""Cached loaders shared by the pages."""

from __future__ import annotations

import polars as pl
import streamlit as st

from optionviz.backtest import BacktestResult, forecasts_frame, underlying_bars
from optionviz.cache import load_all
from optionviz.catalog import Catalog
from optionviz.events import DerivedEventSource, LabelledEventSource, combine
from optionviz.frames import events_for_symbol, option_quotes


@st.cache_resource(show_spinner="Scanning snapshots…")
def get_catalog(directory: str | None = None) -> Catalog:
    return Catalog.discover(directory)


@st.cache_resource(show_spinner="Rebuilding forwards and implied vols…")
def get_chains(fingerprint: str, _catalog: Catalog):
    # fingerprint is the cache key; a new snapshot file invalidates it.
    return load_all(_catalog)


@st.cache_data(show_spinner="Scoring boundaries…")
def get_events(fingerprint: str, _catalog: Catalog, _chains, min_surprise: float) -> pl.DataFrame:
    # Labelled goes first: a confirmed release from the events table wins over
    # a detected one at the same symbol/day, and appears even when its surprise
    # score sits under the slider - it's a fact, not a threshold crossing.
    labelled = LabelledEventSource(_catalog).events(_chains)
    derived = DerivedEventSource(min_surprise=min_surprise).events(_chains)
    return combine(labelled, derived)


@st.cache_data(show_spinner=False)
def get_stock_symbols(fingerprint: str, _catalog: Catalog) -> list[str]:
    return _catalog.stock_symbols()


@st.cache_data(show_spinner="Reading the tape…")
def get_bars(fingerprint: str, _catalog: Catalog, symbol: str, minutes: int) -> pl.DataFrame:
    return underlying_bars(_catalog, symbol, minutes)


@st.cache_data(show_spinner=False)
def get_earnings(fingerprint: str, _catalog: Catalog, symbol: str) -> pl.DataFrame:
    """Confirmed releases for a symbol across every snapshot, each once."""
    return pl.concat(
        [events_for_symbol(s.path, symbol) for s in _catalog.snapshots]
    ).unique(["event_date", "source_id"], keep="first", maintain_order=True)


@st.cache_data(show_spinner="Reading the option chain…")
def get_option_quotes(
    fingerprint: str, _catalog: Catalog, symbol: str, start: str, end: str
) -> pl.DataFrame:
    """Option quotes in [start, end) across every snapshot, newest winning on a shared bar."""
    return pl.concat(
        [option_quotes(s.path, symbol, start, end) for s in _catalog.snapshots]
    ).unique(["ts", "bar_size", "expiry", "strike", "right"], keep="first", maintain_order=True)


@st.cache_data(show_spinner=False)
def load_run(path: str, mtime: float) -> BacktestResult:
    # mtime is the cache key: a re-run of the backtest overwrites the file.
    return BacktestResult.load(path)


@st.cache_data(show_spinner=False)
def get_forecasts(path: str, mtime: float, symbol: str, run_minutes: int) -> pl.DataFrame | None:
    """A run's per-bar forecasts for one symbol, stamped with when each was
    known; None for a run that logged none. mtime is the cache key."""
    from . import tv_chart

    frame = forecasts_frame(path)
    if frame is None:
        return None
    return tv_chart.known(frame.filter(pl.col("symbol") == symbol), run_minutes)


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
