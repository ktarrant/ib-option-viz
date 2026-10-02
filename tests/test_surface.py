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


def test_chain_at_reads_one_closed_bar_and_never_looks_ahead():
    """A bar counts once it has closed, stamped at its close. Bars open at
    13:30 (F=100), 14:00 (F=105) and 14:30 (F=110): as of 14:30 the 14:00 bar
    has just closed and the 14:30 one has not, so the chain is F=105."""
    from datetime import datetime, timezone

    chain = synthetic_chain(n_bars=3, forwards=[100.0, 105.0, 110.0], vols=np.full(3, 0.3))
    quotes = chain.surface.select(
        "ts", "strike", "right", "bid", "ask", "mid", "spread", "rel_spread"
    ).with_columns(pl.lit("30min").alias("bar_size"), pl.lit("2026-08-21").alias("expiry"))

    got = surface.chain_at(quotes, datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc))
    assert got["forward"].unique().to_list() == pytest.approx([105.0], abs=0.01)
    assert got["ts"].unique().to_list() == [datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)]
    assert got["iv"].to_numpy() == pytest.approx(0.3, abs=0.005)
    # One vol per strike, so the two deltas are one number apart.
    assert (got["call_delta"] - got["put_delta"]).to_numpy() == pytest.approx(1.0)


def test_expiry_slots_follow_the_calendar_and_skip_dailies():
    """SPY lists Mon-Thu dailies; the weekly is the next non-monthly Friday, and
    a slot the capture lacks keeps its calendar date with no expiry behind it."""
    from datetime import date

    have = ["2026-09-18", "2026-09-21", "2026-09-22", "2026-09-25", "2026-10-16"]
    # Monday after the September monthly: Sep 18 has passed.
    assert surface.expiry_slots(date(2026, 9, 21), have) == {
        "weekly": (date(2026, 9, 25), "2026-09-25"),
        "monthly": (date(2026, 10, 16), "2026-10-16"),
        "next monthly": (date(2026, 11, 20), None),
    }
    # The week of a monthly: that Friday is the monthly, so the weekly skips it.
    slots = surface.expiry_slots(date(2026, 9, 14), have)
    assert slots["weekly"][0] == date(2026, 9, 25)
    assert slots["monthly"] == (date(2026, 9, 18), "2026-09-18")
    # An expiry still trades on its own day.
    assert surface.expiry_slots(date(2026, 9, 18), have)["monthly"][1] == "2026-09-18"
