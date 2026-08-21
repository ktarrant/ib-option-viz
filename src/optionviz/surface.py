"""Per-timestamp forward, implied vol surface and ATM metrics for one chain.

Everything downstream — event detection, the crush charts, the verdict — reads
the two frames this module produces:

- `surface`: one row per (ts, strike, right) with an implied vol attached.
- `atm`: one row per ts with the forward, the ATM straddle and its cost to trade.

Vols are solved on the **out-of-the-money side only**. The OTM option carries all
the vol information at a strike, has a tighter relative spread, and avoids
inverting a price that is almost entirely intrinsic value.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from . import pricing

MARKET_TZ = ZoneInfo("America/New_York")
# Equity options stop trading at the 16:00 ET close on the expiry date.
EXPIRY_CLOSE = time(16, 0)


def expiry_instant(expiry: str) -> datetime:
    d = datetime.strptime(expiry, "%Y-%m-%d").date()
    return datetime.combine(d, EXPIRY_CLOSE, tzinfo=MARKET_TZ)


def _paired(mids: pl.DataFrame) -> pl.DataFrame:
    """Strikes quoted on both sides, calls and puts side by side."""
    wide = mids.pivot(
        on="right", index=["ts", "strike"], values=["bid", "ask", "mid"], aggregate_function="first"
    )
    rename = {}
    for base in ("bid", "ask", "mid"):
        for r, tag in (("C", "call"), ("P", "put")):
            for cand in (f"{base}_right_{r}", f"{base}_{r}"):
                if cand in wide.columns:
                    rename[cand] = f"{tag}_{base}"
    wide = wide.rename(rename)
    needed = ["call_mid", "put_mid", "call_bid", "call_ask", "put_bid", "put_ask"]
    missing = [c for c in needed if c not in wide.columns]
    if missing:
        return pl.DataFrame()
    return wide.drop_nulls(["call_mid", "put_mid"]).sort(["ts", "strike"])


def build(mids: pl.DataFrame, expiry: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return (surface, atm) for one chain's mid frame."""
    if not mids.height:
        return pl.DataFrame(), pl.DataFrame()

    pairs = _paired(mids)
    if not pairs.height:
        return pl.DataFrame(), pl.DataFrame()

    exp_s = expiry_instant(expiry).timestamp()

    # --- per-timestamp parity fit -------------------------------------------
    rows = []
    for (ts,), grp in pairs.group_by(["ts"], maintain_order=True):
        k = grp["strike"].to_numpy()
        f, df, mad = pricing.parity_fit(k, grp["call_mid"].to_numpy(), grp["put_mid"].to_numpy())
        rows.append((ts, f, df, mad, len(k)))

    fwd = pl.DataFrame(
        rows, schema={"ts": pairs["ts"].dtype, "forward": pl.Float64, "df": pl.Float64,
                      "parity_mad": pl.Float64, "n_pairs": pl.Int64}, orient="row"
    ).with_columns(
        (pl.lit(exp_s) - pl.col("ts").dt.epoch("s")).truediv(pricing.YEAR_SECONDS)
        .clip(lower_bound=0.0).alias("tau")
    )

    # --- implied vols on the OTM side ---------------------------------------
    surf = mids.join(fwd, on="ts", how="inner").with_columns(
        (pl.col("strike") / pl.col("forward")).log().alias("log_moneyness"),
        (pl.col("right") == "C").alias("is_call"),
    )
    surf = surf.with_columns(
        ((pl.col("is_call") & (pl.col("strike") >= pl.col("forward")))
         | (~pl.col("is_call") & (pl.col("strike") < pl.col("forward")))).alias("is_otm")
    )

    iv = pricing.implied_vol(
        surf["mid"].to_numpy(), surf["forward"].to_numpy(), surf["strike"].to_numpy(),
        surf["tau"].to_numpy(), surf["is_call"].to_numpy(), surf["df"].to_numpy(),
    )
    surf = surf.with_columns(pl.Series("iv", iv))

    # Delta comes free once vol is known, and makes strike ladders sortable by risk.
    tau = surf["tau"].to_numpy()
    vol = np.nan_to_num(surf["iv"].to_numpy(), nan=0.0)
    denom = vol * np.sqrt(np.maximum(tau, 0.0))
    d1 = np.where(denom > 0, (surf["log_moneyness"].to_numpy() * -1 + 0.5 * vol**2 * tau) / np.where(denom > 0, denom, 1.0), np.nan)
    call_delta = pricing.norm_cdf(d1)
    delta = np.where(surf["is_call"].to_numpy(), call_delta, call_delta - 1.0)
    surf = surf.with_columns(pl.Series("delta", np.where(np.isnan(surf["iv"].to_numpy()), np.nan, delta)))

    atm = _atm_metrics(pairs, surf, fwd)
    return surf.sort(["ts", "strike", "right"]), atm


def _atm_metrics(pairs: pl.DataFrame, surf: pl.DataFrame, fwd: pl.DataFrame) -> pl.DataFrame:
    """ATM straddle on a real listed strike, plus the interpolated ATM vol.

    The straddle uses the listed strike nearest the forward rather than a
    synthetic K=F, because the verdict has to price something that could
    actually have been traded. Buying costs the ask on both legs; selling
    receives the bid on both.
    """
    near = (
        pairs.join(fwd.select("ts", "forward"), on="ts", how="inner")
        .with_columns((pl.col("strike") - pl.col("forward")).abs().alias("dist"))
        .sort(["ts", "dist"])
        .unique(subset=["ts"], keep="first")
        .with_columns(
            pl.col("strike").alias("atm_strike"),
            (pl.col("call_mid") + pl.col("put_mid")).alias("straddle_mid"),
            (pl.col("call_ask") + pl.col("put_ask")).alias("straddle_ask"),
            (pl.col("call_bid") + pl.col("put_bid")).alias("straddle_bid"),
        )
        .select("ts", "atm_strike", "straddle_mid", "straddle_ask", "straddle_bid")
    )

    # ATM vol by interpolating the OTM smile at log-moneyness 0.
    otm = surf.filter(pl.col("is_otm") & pl.col("iv").is_not_null()).sort(["ts", "log_moneyness"])
    iv_rows = []
    for (ts,), g in otm.group_by(["ts"], maintain_order=True):
        x, y = g["log_moneyness"].to_numpy(), g["iv"].to_numpy()
        if x.size == 0:
            continue
        atm_iv = float(np.interp(0.0, x, y))
        lo = float(np.interp(-0.10, x, y))
        hi = float(np.interp(0.10, x, y))
        iv_rows.append((ts, atm_iv, lo - hi))

    ivf = pl.DataFrame(
        iv_rows, schema={"ts": otm["ts"].dtype, "atm_iv": pl.Float64, "skew_10pct": pl.Float64},
        orient="row",
    ) if iv_rows else pl.DataFrame(schema={"ts": fwd["ts"].dtype, "atm_iv": pl.Float64, "skew_10pct": pl.Float64})

    return (
        fwd.join(near, on="ts", how="left")
           .join(ivf, on="ts", how="left")
           .with_columns((pl.col("straddle_mid") / pl.col("forward")).alias("expected_move"))
           .sort("ts")
    )
