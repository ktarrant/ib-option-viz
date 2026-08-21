"""Round-trip: price a chain forward from known inputs, then check the surface
recovers them."""

import numpy as np
import polars as pl
import pytest

from optionviz import surface
from tests.conftest import synthetic_chain


def test_recovers_known_forward_and_vol():
    chain = synthetic_chain(n_bars=26, forwards=np.full(26, 137.5), vols=np.full(26, 0.42))
    atm = chain.atm
    assert atm.height == 26
    assert atm["forward"].to_numpy() == pytest.approx(137.5, abs=0.01)
    assert atm["atm_iv"].to_numpy() == pytest.approx(0.42, abs=0.005)
    assert (atm["parity_mad"].to_numpy() < 0.01).all()


def test_atm_strike_is_a_listed_strike_near_the_forward():
    chain = synthetic_chain(n_bars=13, forwards=np.full(13, 103.0))
    listed = set(chain.surface["strike"].unique().to_list())
    for k in chain.atm["atm_strike"].to_list():
        assert k in listed
        assert abs(k - 103.0) <= 2.0


def test_straddle_bid_below_mid_below_ask():
    chain = synthetic_chain(n_bars=13)
    a = chain.atm.drop_nulls("straddle_mid")
    assert (a["straddle_bid"] <= a["straddle_mid"]).all()
    assert (a["straddle_mid"] <= a["straddle_ask"]).all()


def test_otm_flag_splits_at_the_forward():
    chain = synthetic_chain(n_bars=13, forwards=np.full(13, 100.0))
    s = chain.surface
    calls = s.filter((pl.col("right") == "C") & pl.col("is_otm"))
    puts = s.filter((pl.col("right") == "P") & pl.col("is_otm"))
    assert (calls["strike"] >= calls["forward"]).all()
    assert (puts["strike"] < puts["forward"]).all()


def test_delta_is_bounded_and_signed():
    s = synthetic_chain(n_bars=13).surface.drop_nulls("delta")
    calls = s.filter(pl.col("right") == "C")["delta"].to_numpy()
    puts = s.filter(pl.col("right") == "P")["delta"].to_numpy()
    assert ((calls >= 0) & (calls <= 1)).all()
    assert ((puts >= -1) & (puts <= 0)).all()


def test_expiry_instant_uses_the_market_close():
    inst = surface.expiry_instant("2026-08-21")
    assert inst.hour == 16
    assert inst.utcoffset().total_seconds() == -4 * 3600  # EDT in August


def test_empty_input_returns_empty_frames():
    surf, atm = surface.build(pl.DataFrame(), "2026-08-21")
    assert surf.is_empty() and atm.is_empty()
