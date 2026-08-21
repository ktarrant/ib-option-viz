"""The maths has no reference implementation to check against, so it is pinned
by identities that must hold regardless of implementation."""

import math

import numpy as np
import pytest

from optionviz import pricing


def test_norm_cdf_matches_stdlib():
    x = np.linspace(-8, 8, 4001)
    ref = np.array([0.5 * (1 + math.erf(v / math.sqrt(2))) for v in x])
    assert np.abs(pricing.norm_cdf(x) - ref).max() < 1e-14


def test_norm_cdf_tails_and_symmetry():
    assert pricing.norm_cdf(np.array([0.0]))[0] == pytest.approx(0.5)
    assert pricing.norm_cdf(np.array([-50.0]))[0] == pytest.approx(0.0, abs=1e-300)
    assert pricing.norm_cdf(np.array([50.0]))[0] == pytest.approx(1.0)
    x = np.array([0.3, 1.7, 4.2])
    assert np.allclose(pricing.norm_cdf(x) + pricing.norm_cdf(-x), 1.0)


def test_put_call_parity_identity():
    """C - P = F - K is the relation the forward reconstruction depends on."""
    F, K, T, v = 100.0, 95.0, 0.25, 0.3
    c = pricing.black76(F, K, T, v, True)
    p = pricing.black76(F, K, T, v, False)
    assert float(c - p) == pytest.approx(F - K, abs=1e-9)


def test_zero_vol_and_zero_time_give_intrinsic():
    assert float(pricing.black76(110.0, 100.0, 0.5, 0.0, True)) == pytest.approx(10.0)
    assert float(pricing.black76(110.0, 100.0, 0.0, 0.4, True)) == pytest.approx(10.0)
    assert float(pricing.black76(90.0, 100.0, 0.5, 0.0, True)) == pytest.approx(0.0)
    assert float(pricing.black76(90.0, 100.0, 0.5, 0.0, False)) == pytest.approx(10.0)


def test_implied_vol_round_trips():
    rng = np.random.default_rng(0)
    n = 20_000
    F = rng.uniform(50, 800, n)
    K = F * rng.uniform(0.7, 1.3, n)
    T = rng.uniform(1 / 365, 0.2, n)
    vol = rng.uniform(0.05, 2.0, n)
    is_call = rng.random(n) < 0.5

    px = pricing.black76(F, K, T, vol, is_call)
    back = pricing.implied_vol(px, F, K, T, is_call)

    solved = ~np.isnan(back)
    assert solved.mean() > 0.9
    assert np.abs(back[solved] - vol[solved]).max() < 1e-5


def test_implied_vol_respects_discount_factor():
    F, K, T, vol = 500.0, 505.0, 0.05, 0.35
    df = 0.997
    px = pricing.black76(F, K, T, vol, True, df=df)
    assert float(pricing.implied_vol(px, F, K, T, True, df=df)) == pytest.approx(vol, abs=1e-6)


def test_implied_vol_returns_nan_below_intrinsic():
    # A price at or under intrinsic has no vol that explains it.
    out = pricing.implied_vol(np.array([5.0, 0.0]), 110.0, 100.0, 0.25, True)
    assert np.isnan(out).all()


def test_parity_fit_recovers_forward_exactly():
    F_true = 773.4
    strikes = np.arange(700.0, 800.0, 2.5)
    calls = pricing.black76(F_true, strikes, 0.025, 0.15, True)
    puts = pricing.black76(F_true, strikes, 0.025, 0.15, False)

    forward, df, mad = pricing.parity_fit(strikes, calls, puts)
    assert forward == pytest.approx(F_true, abs=1e-6)
    assert df == 1.0
    assert mad < 1e-9


def test_parity_fit_is_robust_to_a_bad_strike():
    """One mangled quote must not drag the forward — the median is why."""
    F_true = 100.0
    strikes = np.arange(90.0, 110.0, 1.0)
    calls = pricing.black76(F_true, strikes, 0.05, 0.25, True)
    puts = pricing.black76(F_true, strikes, 0.05, 0.25, False)
    calls[3] += 25.0  # a stale or crossed print

    forward, _, mad = pricing.parity_fit(strikes, calls, puts)
    assert forward == pytest.approx(F_true, abs=0.01)
    assert mad < 0.01


def test_parity_fit_handles_thin_ladders():
    forward, df, mad = pricing.parity_fit(np.array([100.0]), np.array([5.0]), np.array([3.0]))
    assert forward == pytest.approx(102.0)
    assert df == 1.0
    assert pricing.parity_fit(np.array([]), np.array([]), np.array([]))[0] != pricing.parity_fit(
        np.array([]), np.array([]), np.array([])
    )[0]  # NaN
