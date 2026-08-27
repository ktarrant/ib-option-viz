"""Detection is calibration-sensitive, so these pin the behaviour that matters:
a planted event is found, and a quiet tape yields nothing."""

from datetime import date

import numpy as np
import polars as pl
import pytest

from optionviz import events
from optionviz.events import DerivedEventSource, match_boundary
from tests.conftest import synthetic_chain


def _earnings_chain(n=440, gap=0.12, iv_before=0.35, iv_after=0.20, at=325):
    """Flat tape, then one overnight gap with the vol collapsing behind it.

    The vol level matters: at 80% with seven weeks left the chain prices a ~24%
    move, so a 12% gap is a *seller's* win and rightly fails the earnings test.
    These defaults price roughly a 10% move and deliver 12%.

    Length matters too: `robust_z` needs `MIN_HISTORY` same-kind boundaries
    before it will score anything, and a session is 13 bars, so a chain has to
    run past ~31 sessions for its overnight distribution to exist at all.
    """
    rng = np.random.default_rng(7)
    fwd = 100.0 * np.cumprod(1 + rng.normal(0, 0.0008, n))
    # Real vol wanders; a perfectly flat series is a degenerate case, not a tape.
    vols = iv_before + rng.normal(0, 0.01, n)
    fwd[at:] *= 1 + gap
    vols[at:] = iv_after + rng.normal(0, 0.01, n - at)
    return synthetic_chain(n_bars=n, forwards=fwd, vols=vols)


def test_finds_a_planted_earnings_event():
    chain = _earnings_chain()
    found = DerivedEventSource().events({("TEST", "2026-08-21"): chain})
    assert found.height >= 1
    top = found.row(0, named=True)
    assert top["kind"] == "earnings"
    assert top["boundary"] == "overnight"
    assert top["ret"] == pytest.approx(np.log(1.12), abs=0.02)
    assert top["crush"] < -0.3
    assert top["crush_z"] <= events.EARNINGS_CRUSH_Z


def test_quiet_tape_produces_no_events():
    rng = np.random.default_rng(3)
    fwd = 100.0 * np.cumprod(1 + rng.normal(0, 0.0005, 440))
    chain = synthetic_chain(n_bars=440, forwards=fwd)
    assert DerivedEventSource().events({("TEST", "2026-08-21"): chain}).height == 0


def test_too_little_history_scores_nothing():
    """A distribution needs samples; nine overnight boundaries is not a distribution."""
    rng = np.random.default_rng(3)
    fwd = 100.0 * np.cumprod(1 + rng.normal(0, 0.0008, 130))
    fwd[104:] *= 1.15
    chain = synthetic_chain(n_bars=130, forwards=fwd)
    assert DerivedEventSource().events({("TEST", "2026-08-21"): chain}).height == 0


def test_move_without_a_crush_is_not_earnings():
    """Same gap, vol left alone: a shock, not a scheduled event resolving."""
    rng = np.random.default_rng(11)
    fwd = 100.0 * np.cumprod(1 + rng.normal(0, 0.0008, 440))
    fwd[325:] *= 1.12
    chain = synthetic_chain(n_bars=440, forwards=fwd, vols=0.5 + rng.normal(0, 0.01, 440))
    found = DerivedEventSource().events({("TEST", "2026-08-21"): chain})
    assert found.height >= 1
    assert "earnings" not in found["kind"].to_list()


def test_robust_z_ignores_the_outlier_it_measures():
    x = np.concatenate([np.full(60, 1.0), np.full(60, 1.1), [50.0]])
    z = events.robust_z(x)
    assert z[-1] > 20          # the outlier is flagged
    assert abs(z[0]) < 5       # without inflating the baseline

    assert np.isnan(events.robust_z(np.arange(5.0))).all()  # too little history


def test_trailing_rank_is_causal():
    """Rank may only look backwards: appending a spike cannot change earlier ranks."""
    rng = np.random.default_rng(5)
    base = rng.uniform(0.2, 0.5, 60)
    spiked = base.copy()
    spiked[-1] = 99.0

    r_base = events.trailing_rank(base, window=20)
    r_spiked = events.trailing_rank(spiked, window=20)
    np.testing.assert_allclose(r_base[:-1], r_spiked[:-1], equal_nan=True)

    assert events.trailing_rank(np.concatenate([np.full(30, 0.2), [0.9]]), window=20)[30] == 1.0
    assert np.isnan(r_base[0])  # no history yet


def test_one_event_per_symbol_day():
    chain = _earnings_chain()
    found = DerivedEventSource().events({("TEST", "2026-08-21"): chain})
    days = found["ts_after"].dt.date().to_list()
    assert len(days) == len(set(days))


def test_combine_prefers_the_earlier_source():
    chain = _earnings_chain()
    derived = DerivedEventSource().events({("TEST", "2026-08-21"): chain})
    labelled = derived.head(1).with_columns(
        __import__("polars").lit("Q2 FY26").alias("label"),
        __import__("polars").lit("db").alias("source"),
    )
    merged = events.combine(labelled, derived)
    assert merged.height == derived.height
    assert merged.filter(__import__("polars").col("source") == "db").height == 1


def _boundaries_for_match():
    """A real boundaries() frame from a synthetic chain, for match_boundary tests."""
    chain = _earnings_chain()
    return DerivedEventSource().boundaries(chain)


def test_match_boundary_before_open_lands_same_day():
    b = _boundaries_for_match()
    target_date = b["ts_after"][200].date()
    m = match_boundary(b, target_date, "before_open")
    assert m is not None
    assert m["ts_after"].date() == target_date


def test_match_boundary_after_close_lands_next_available_day():
    b = _boundaries_for_match()
    day_before = b["ts_before"][200].date()
    m = match_boundary(b, day_before, "after_close")
    assert m is not None
    assert m["ts_after"].date() > day_before


def test_match_boundary_rejects_an_event_far_outside_the_window():
    """A 2004 earnings date must not match "any boundary after it" -

    that is, chronologically, the very first bar of the whole chain. This
    pins the proximity guard that exists specifically to prevent that.
    """
    b = _boundaries_for_match()
    ancient = date(2004, 1, 1)
    assert match_boundary(b, ancient, "after_close") is None
    assert match_boundary(b, ancient, "before_open") is None


def test_match_boundary_rejects_a_future_event_past_the_capture():
    b = _boundaries_for_match()
    last_date = b["ts_after"].max().date()
    far_future = date(last_date.year + 1, 1, 1)
    assert match_boundary(b, far_future, "after_close") is None


def test_match_boundary_empty_boundaries_returns_none():
    assert match_boundary(pl.DataFrame(schema=events.SCHEMA), date(2026, 1, 1), "before_open") is None


def test_match_boundary_default_timing_behaves_like_after_close():
    # Dict equality would compare the NaN spot_ret field to itself and fail
    # (nan != nan), so compare the one field that actually distinguishes a match.
    b = _boundaries_for_match()
    day_before = b["ts_before"][200].date()
    assert (
        match_boundary(b, day_before, None)["ts_after"]
        == match_boundary(b, day_before, "after_close")["ts_after"]
    )
