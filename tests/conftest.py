from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl
import pytest

from optionviz import pricing
from optionviz.cache import Chain
from optionviz.catalog import Catalog

SNAPSHOT_DIR = None  # resolved lazily so the suite runs without any capture present


@pytest.fixture(scope="session")
def catalog():
    cat = Catalog.discover()
    if cat.is_empty:
        pytest.skip("no capture snapshot available")
    return cat


def synthetic_chain(
    symbol="TEST",
    expiry="2026-08-21",
    n_bars=120,
    forwards=None,
    vols=None,
    strikes=None,
) -> Chain:
    """A chain built forward from Black-76, so every derived value is known.

    Bars are laid on a 30-minute grid, 13 per session, which is the shape the
    real 13:30-20:00 UTC grid has.
    """
    start = datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)
    ts = []
    day, slot = 0, 0
    for _ in range(n_bars):
        ts.append(start + timedelta(days=day, minutes=30 * slot))
        slot += 1
        if slot == 13:
            slot, day = 0, day + 1

    forwards = np.full(n_bars, 100.0) if forwards is None else np.asarray(forwards, float)
    vols = np.full(n_bars, 0.30) if vols is None else np.asarray(vols, float)
    strikes = np.arange(80.0, 121.0, 2.0) if strikes is None else np.asarray(strikes, float)

    exp = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc).timestamp()
    rows = []
    for t, F, v in zip(ts, forwards, vols):
        tau = max((exp - t.timestamp()) / pricing.YEAR_SECONDS, 1e-9)
        for k in strikes:
            for right, is_call in (("C", True), ("P", False)):
                mid = float(pricing.black76(F, k, tau, v, is_call))
                half = max(0.01, 0.005 * mid)
                rows.append((t, float(k), right, mid - half, mid + half))

    mids = pl.DataFrame(
        rows, schema=["ts", "strike", "right", "bid", "ask"], orient="row"
    ).with_columns(
        ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
        (pl.col("ask") - pl.col("bid")).alias("spread"),
    ).with_columns((pl.col("spread") / pl.col("mid")).alias("rel_spread"))

    from optionviz import surface

    surf, atm = surface.build(mids, expiry)
    return Chain(symbol, expiry, surf, atm, {})


@pytest.fixture
def flat_chain():
    return synthetic_chain()
