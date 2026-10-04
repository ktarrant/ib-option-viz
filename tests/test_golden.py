"""Checks against the real capture snapshot.

Synthetic tests prove the maths is self-consistent; these prove it is pointed at
the right data. Values were confirmed by hand against the snapshot. All skip
cleanly when no capture is present.
"""

from datetime import date

import polars as pl
import pytest

from optionviz import frames
from optionviz.events import DerivedEventSource, LabelledEventSource, combine
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
    # A lower bound, not an exact count: the capture accretes more strikes over
    # successive runs, so this only needs to confirm the ladder is substantial.
    assert r["n_pairs"] >= 25
    assert r["atm_iv"] == pytest.approx(0.10, abs=0.01)


def test_forward_sits_just_above_spot_for_every_symbol(catalog, chains):
    """The parity forward must carry, not drift: over spot by about the risk-free
    rate times the time to expiry. Every chain on 2026-08-12 annualises to 3-5%
    (dividend payers - XOM, GS - near zero), so the band is quote noise plus at
    most 6% a year: a few basis points on August's chain, ~1% on November's."""
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
        years = (date.fromisoformat(expiry) - date(2026, 8, 12)).days / 365
        assert -0.001 < basis < 0.002 + 0.06 * years, (
            f"{symbol} {expiry} basis {basis:.4%} is not carry"
        )


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


def test_real_spot_is_captured_and_close_to_the_parity_forward(chains):
    """Schema-v6 stock 30-minute bars, joined onto the option-bar grid.

    Coverage is strong but not total (spot is denser than the option ladder,
    which is what makes it worth joining rather than a strict superset check),
    and where both exist they should agree to within the carry basis - a few
    tenths of a percent for a chain weeks from expiry, not points.
    """
    chain = chains[("SPY", EXPIRY)]
    atm = chain.atm
    assert "spot_mid" in atm.columns
    coverage = atm["spot_mid"].is_not_null().sum() / atm.height
    assert coverage > 0.85

    both = atm.drop_nulls(["forward", "spot_mid"])
    basis = (both["forward"] / both["spot_mid"] - 1.0).abs()
    assert basis.max() < 0.01  # generous: near expiry this runs in the tens of bps


def test_basis_decays_toward_expiry(chains):
    """The forward-vs-spot gap is carry, and carry shrinks to nothing at expiry."""
    atm = chains[("SPY", EXPIRY)].atm.drop_nulls(["forward", "spot_mid"]).sort("ts")
    basis = (atm["forward"] / atm["spot_mid"] - 1.0).to_numpy()
    early, late = basis[: len(basis) // 10], basis[-len(basis) // 10 :]
    assert abs(early.mean()) > abs(late.mean())
    assert abs(late.mean()) < 0.001


def test_labelled_source_finds_amd_and_mstr_earnings(chains, catalog):
    """AMD and MSTR really reported in-window, but their vol barely crushes -
    the derived detector's crush threshold misses both. The labelled source,
    reading the confirmed calendar instead of inferring from vol, must not.
    """
    found = LabelledEventSource(catalog).events(chains)
    earnings = found.filter(pl.col("kind") == "earnings")
    got = set(earnings["symbol"].to_list())
    assert {"AMD", "MSTR"} <= got

    derived = DerivedEventSource().events(chains)
    assert "AMD" not in set(derived.filter(pl.col("kind") == "earnings")["symbol"].to_list())


def test_labelled_earnings_ignore_ancient_calendar_history(chains, catalog):
    """Each symbol's calendar runs back to ~2004-2020; only in-window entries
    should ever produce a match. If the proximity guard in match_boundary ever
    regresses, this is where it would show up as a flood of matches.

    Counted as distinct earnings (symbol, day): one release matches once per
    chain, and the 2026-10 snapshots carry three expiries (NVDA's 08-27 print
    is three rows). The table now also holds other 8-K kinds (~40 legitimate
    in-window matches) and IB headlines, which must never be matched at all —
    they once made this 1 611.
    """
    found = LabelledEventSource(catalog).events(chains)
    releases = found.filter(pl.col("kind") == "earnings").select("symbol", pl.col("ts_after").dt.date()).unique()
    assert releases.height < 20
    assert "headline" not in set(found["kind"].to_list())
    assert (found["ts_after"].dt.year() >= 2026).all()


def test_combined_events_prefer_labelled_kind_at_shared_dates(chains, catalog):
    """AMD's confirmed earnings must win over whatever the derived detector
    would have called that same boundary (its own crush threshold calls it a
    shock, not earnings - combine() must not let that survive the merge).
    """
    labelled = LabelledEventSource(catalog).events(chains)
    derived = DerivedEventSource(min_surprise=0.5).events(chains)
    merged = combine(labelled, derived)
    amd = merged.filter((pl.col("symbol") == "AMD") & (pl.col("kind") == "earnings"))
    assert amd.height == 1


def test_scoring_prefers_real_spot_over_the_forward(chains):
    """AMZN's straddle verdict should be built on real captured spot, not the
    parity reconstruction, whenever the snapshot has it on both sides.
    """
    s = straddle_verdict(
        chains[("AMZN", EXPIRY)],
        pl.datetime(2026, 7, 30, 19, 30, time_zone="UTC"),
        pl.datetime(2026, 7, 31, 13, 30, time_zone="UTC"),
    )
    assert s is not None
    assert s.uses_real_spot
    assert s.realized_move == pytest.approx(abs(s.spot_after / s.spot_before - 1.0))
