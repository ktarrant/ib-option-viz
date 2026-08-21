"""Option maths, vectorised over numpy arrays.

The capture stores no historical greeks and no intraday underlying — IB has
neither. Both are reconstructed here from the one thing we do have: dense
bid/ask bars on a whole strike ladder.

The key move is put-call parity: at each timestamp, every strike quoted on both
sides gives an independent read on the forward,

    F = K + C - P

and the median across the ladder is the estimate. That makes implied vol free of
any rate or dividend assumption — the carry is already inside F. Nothing
external is needed, which matters because the capture stores no underlying
intraday series to compare against.

**Why the median and not a regression.** For European options `C - P = DF*(F-K)`
is a straight line in K, so least squares would recover the forward and the
discount factor together from slope and intercept. These are American equity
options, and it does not work: fitted on the real snapshot, 93% of timestamps
return DF > 1, which is unphysical, and the fit residual is worse than the
median's (MAD 0.065 vs 0.027 on SPY). The early-exercise premium bends the line
by far more than the ~0.1% of carry the slope is trying to measure. The median
is both more robust and, checked against each symbol's stock close, correct to
the +0.05-0.15% the true carry basis should be.
"""

from __future__ import annotations

import numpy as np

SQRT_2PI = 2.5066282746310002
YEAR_SECONDS = 365.25 * 24 * 3600

# Vol search bracket for the inverter. 500% covers post-crush meme names.
VOL_LO, VOL_HI = 1e-6, 5.0
BISECT_ITERS = 64

# Forward estimation prefers strikes this close to the money, where the
# American early-exercise premium is smallest.
ATM_BAND = 0.10
MIN_BAND_STRIKES = 4


def norm_cdf(x: np.ndarray) -> np.ndarray:
    """Cumulative normal, Hart's double-precision rational approximation.

    Accurate to ~1e-15, which is far tighter than the bid/ask granularity we
    feed it. Avoids a scipy dependency for the one function we need from it.
    """
    x = np.asarray(x, dtype=np.float64)
    xa = np.abs(x)
    e = np.exp(-0.5 * np.square(np.minimum(xa, 40.0)))

    # Inner branch: |x| < 7.07
    num = 3.52624965998911e-02 * xa + 0.700383064443688
    num = num * xa + 6.37396220353165
    num = num * xa + 33.912866078383
    num = num * xa + 112.079291497871
    num = num * xa + 221.213596169931
    num = num * xa + 220.206867912376
    den = 8.83883476483184e-02 * xa + 1.75566716318264
    den = den * xa + 16.064177579207
    den = den * xa + 86.7807322029461
    den = den * xa + 296.564248779674
    den = den * xa + 637.333633378831
    den = den * xa + 793.826512519948
    den = den * xa + 440.413735824752
    inner = e * num / den

    # Outer branch: continued fraction for the far tail
    cf = xa + 0.65
    for k in (4.0, 3.0, 2.0, 1.0):
        cf = xa + k / cf
    outer = e / (cf * SQRT_2PI)

    tail = np.where(xa < 7.07106781186547, inner, outer)
    tail = np.where(xa > 37.0, 0.0, tail)
    return np.where(x <= 0, tail, 1.0 - tail)


def black76(F, K, T, vol, is_call, df=1.0):
    """Black-76 price on a forward. `df` discounts to present value."""
    F, K, T, vol = (np.asarray(v, dtype=np.float64) for v in (F, K, T, vol))
    is_call = np.asarray(is_call, dtype=bool)

    sqrt_t = np.sqrt(np.maximum(T, 0.0))
    denom = vol * sqrt_t
    safe = denom > 0

    # np.where evaluates both branches, so keep the log/divide finite everywhere.
    d_scale = np.where(safe, denom, 1.0)
    d1 = (np.log(np.maximum(F, 1e-300) / np.maximum(K, 1e-300)) + 0.5 * np.square(vol) * T) / d_scale
    d2 = d1 - d_scale

    call = F * norm_cdf(d1) - K * norm_cdf(d2)
    put = K * norm_cdf(-d2) - F * norm_cdf(-d1)
    priced = np.where(is_call, call, put)

    intrinsic = np.where(is_call, np.maximum(F - K, 0.0), np.maximum(K - F, 0.0))
    return df * np.where(safe, priced, intrinsic)


def implied_vol(price, F, K, T, is_call, df=1.0) -> np.ndarray:
    """Invert Black-76 by vectorised bisection.

    64 halvings of [1e-6, 5] resolve vol far below quote granularity, and cost a
    few dozen array ops regardless of row count. Returns NaN where no solution
    exists — price at or below intrinsic, or above the forward for a call.
    """
    price, F, K, T = (np.asarray(v, dtype=np.float64) for v in (price, F, K, T))
    is_call = np.asarray(is_call, dtype=bool)
    fwd_price = price / df  # invert on forward value; carry already lives in F

    intrinsic = np.where(is_call, np.maximum(F - K, 0.0), np.maximum(K - F, 0.0))
    ceiling = np.where(is_call, F, K)
    solvable = (T > 0) & (fwd_price > intrinsic + 1e-9) & (fwd_price < ceiling - 1e-9)

    lo = np.full(fwd_price.shape, VOL_LO)
    hi = np.full(fwd_price.shape, VOL_HI)
    for _ in range(BISECT_ITERS):
        mid = 0.5 * (lo + hi)
        too_cheap = black76(F, K, T, mid, is_call) < fwd_price
        lo = np.where(too_cheap, mid, lo)
        hi = np.where(too_cheap, hi, mid)

    return np.where(solvable, 0.5 * (lo + hi), np.nan)


def parity_fit(strikes: np.ndarray, call_mid: np.ndarray, put_mid: np.ndarray):
    """Recover (forward, discount_factor, residual_mad) from one strike ladder.

    Two-pass median of `K + C - P`: the first pass over every paired strike gives
    a provisional forward, the second restricts to strikes within `ATM_BAND` of
    it. Near-the-money options carry the least early-exercise premium, so the
    second pass is the cleaner read; it falls back to the first when the band is
    too thin to be worth trusting.

    `discount_factor` is always 1.0 — see the module docstring for why it is not
    estimated. It stays in the signature so a European-style chain could supply
    a real one without changing callers.
    """
    k = np.asarray(strikes, dtype=np.float64)
    y = np.asarray(call_mid, dtype=np.float64) - np.asarray(put_mid, dtype=np.float64)
    if k.size == 0:
        return np.nan, np.nan, np.nan

    implied = k + y
    forward = float(np.median(implied))

    near = np.abs(k / forward - 1.0) <= ATM_BAND
    if near.sum() >= MIN_BAND_STRIKES:
        forward = float(np.median(implied[near]))

    return forward, 1.0, float(np.median(np.abs(implied - forward)))


def year_fraction(ts_seconds: np.ndarray, expiry_seconds: float) -> np.ndarray:
    """Calendar year fraction to expiry. Equity options stop trading at the 16:00 ET close."""
    return np.maximum((expiry_seconds - np.asarray(ts_seconds, dtype=np.float64)) / YEAR_SECONDS, 0.0)
