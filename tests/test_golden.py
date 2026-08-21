"""Checks against the real capture snapshot.

Synthetic tests prove the maths is self-consistent; these prove it is pointed at
the right data. Values were confirmed by hand against the snapshot. All skip
cleanly when no capture is present.
"""

import polars as pl
import pytest

from optionviz import frames
from optionviz.cache import load_chain
from optionviz.events import DerivedEventSource
from optionviz.scoring import straddle_verdict, verdict_table

EXPIRY = "2026-08-21"
CALM_TS = pl.datetime(2026, 8, 12, 19, 30, time_zone="UTC")


@pytest.fixture(scope="module")
def chains(catalog):
    from optionviz.cache import load_all

    out = load_all(catalog)
    if not out:
        pytest.skip("snapshot has no usable chains")
    return out


def _at(chain, ts):
    row = chain.atm.filter(pl.col("ts") == ts)
    if not row.height:
        pytest.skip("expected bar not in this snapshot")
    return row.row(0, named=True)


def test_spy_forward_and_vol(chains):
    """SPY on 2026-08-12 19:30: forward 773.31, ATM IV ~10%, tight parity fit."""
    r = _at(chains[("SPY", EXPIRY)], CALM_TS)
    assert r["forward"] == pytest.approx(773.31, abs=0.5)
    assert r["parity_mad"] < 0.10
    assert r["n_pairs"] == 30
    assert r["atm_iv"] == pytest.approx(0.10, abs=0.01)


def test_forward_sits_just_above_spot_for_every_symbol(catalog, chains):
    """The parity forward must carry, not drift: a few basis points over spot."""
    for (symbol, expiry), chain in chains.items():
        row = chain.atm.filter(pl.col("ts") == CALM_TS)
        if not row.height:
            continue
        stock = frames.stock_daily(catalog.resolve(symbol, expiry), symbol).filter(
            pl.col("date") == "2026-08-12"
        )
        if not stock.height:
            continue
        basis = row["forward"][0] / stock["close"][0] - 1.0
        assert -0.001 < basis < 0.004, f"{symbol} basis {basis:.4%} is not carry"


def test_index_vol_ranks_below_single_names(chains):
    """A sanity check no amount of arithmetic error survives."""
    def iv(sym):
        return _at(chains[(sym, EXPIRY)], CALM_TS)["atm_iv"]

    assert iv("SPY") < iv("QQQ") < iv("AAPL") < iv("TSLA") < iv("MSTR")


def test_pltr_earnings_crush(chains):
    """PLTR 08-03 -> 08-04: ATM vol collapses from ~80% to ~55%."""
    chain = chains[("PLTR", EXPIRY)]
    before = _at(chain, pl.datetime(2026, 8, 3, 19, 30, time_zone="UTC"))
    after = _at(chain, pl.datetime(2026, 8, 4, 13, 30, time_zone="UTC"))
    assert before["atm_iv"] == pytest.approx(0.80, abs=0.04)
    assert after["atm_iv"] == pytest.approx(0.55, abs=0.04)
    assert after["forward"] / before["forward"] - 1 == pytest.approx(0.224, abs=0.02)


def test_pltr_straddle_verdict(chains):
    """The 126 straddle went 17.85 -> 29.20 over the print. Buyers won."""
    s = straddle_verdict(
        chains[("PLTR", EXPIRY)],
        pl.datetime(2026, 8, 3, 19, 30, time_zone="UTC"),
        pl.datetime(2026, 8, 4, 13, 30, time_zone="UTC"),
    )
    assert s is not None
    assert s.strike == 126.0
    assert s.mid_before == pytest.approx(17.85, abs=0.05)
    assert s.mid_after == pytest.approx(29.20, abs=0.05)
    assert s.verdict == "BUYER"
    assert s.buyer_pnl > 0 > s.seller_pnl
    # Executable is worse than mid-to-mid, always.
    assert s.buyer_pnl < s.pnl_mid


def test_known_earnings_are_detected(chains):
    found = DerivedEventSource().events(chains)
    earnings = found.filter(pl.col("kind") == "earnings")
    got = set(earnings["symbol"].to_list())
    assert {"PLTR", "AMZN", "META", "AAPL", "TSLA", "COIN"} <= got
    # SPY has no earnings; its post-FOMC crush must not be labelled as one.
    assert "SPY" not in got


def test_detected_earnings_land_on_the_right_dates(chains):
    found = DerivedEventSource().events(chains)
    dates = {
        (r["symbol"], r["ts_after"].strftime("%Y-%m-%d"))
        for r in found.filter(pl.col("kind") == "earnings").iter_rows(named=True)
    }
    assert {
        ("PLTR", "2026-08-04"), ("AMZN", "2026-07-31"), ("META", "2026-07-30"),
        ("AAPL", "2026-07-31"), ("TSLA", "2026-07-23"), ("COIN", "2026-07-31"),
    } <= dates


def test_fomc_shows_up_across_the_index_etfs(chains):
    """07-29 18:30 UTC is 14:30 ET. All three index ETFs move on the same bar."""
    found = DerivedEventSource().events(chains)
    at_fomc = found.filter(pl.col("ts_after") == pl.datetime(2026, 7, 29, 18, 30, time_zone="UTC"))
    assert {"SPY", "QQQ", "IWM"} <= set(at_fomc["symbol"].to_list())
    assert (at_fomc["kind"] == "macro").all()


def test_verdicts_are_computable_for_every_earnings_event(chains):
    found = DerivedEventSource().events(chains)
    earnings = found.filter(pl.col("kind") == "earnings")
    table = verdict_table(chains, earnings)
    assert table.height == earnings.height
    assert table["verdict"].is_in(["BUYER", "SELLER"]).all()


def test_coverage_reports_asked_but_empty_strikes(catalog):
    """bar_count = 0 is an answer. If it vanished, the coverage view is lying."""
    cov = frames.coverage(catalog.snapshots[0].path)
    assert cov.height > 0
    assert (cov["bar_count"] == 0).sum() > 0
