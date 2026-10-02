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

from datetime import date, datetime, time, timedelta
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


def chain_at(quotes: pl.DataFrame, as_of: datetime) -> pl.DataFrame:
    """Every expiry's chain as it stood at `as_of`, one row per (expiry, strike):
    call and put bid/ask, the smile's IV, both deltas, and the parity forward.

    `quotes` is `frames.option_quotes` output. Each expiry is read off a single
    bar — the latest to have *closed* by `as_of`, stamped at its close — never
    each contract's own last quote: a thin strike's last print can be an hour
    stale, and mixing times would skew the forward every vol is solved against.
    Requiring the bar to have closed keeps a 15-minute option bar from leaking
    quotes from after a 5-minute candle.

    IV is the out-of-the-money side's, one per strike — the smile, as `build`
    solves it; an in-the-money price is mostly intrinsic plus early-exercise
    premium. Both deltas come from that one vol, so put = call - 1.
    """
    if not quotes.height:
        return pl.DataFrame()
    minutes = pl.col("bar_size").str.extract(r"^(\d+)min$").cast(pl.Int64)
    closed = (
        quotes.with_columns((pl.col("ts") + pl.duration(minutes=minutes)).alias("ts"))
        .filter(pl.col("ts") <= as_of)
    )
    latest = (
        closed.filter(pl.col("ts") == pl.col("ts").max().over("expiry"))
        # Two bar sizes can close at the same instant; their closes are one quote.
        .unique(["expiry", "strike", "right"], keep="first", maintain_order=True)
    )
    frames = []
    for (expiry,), chain in latest.group_by("expiry"):
        surf, _ = build(chain.drop("bar_size", "expiry"), expiry)
        if not surf.height:
            continue
        side = lambda right, tag: surf.filter(pl.col("right") == right).select(  # noqa: E731
            "strike", pl.col("bid").alias(f"{tag}_bid"), pl.col("ask").alias(f"{tag}_ask"),
            pl.col("mid").alias(f"{tag}_mid"),
        )
        smile = surf.filter("is_otm").select(
            "strike", "iv",
            pl.when("is_call").then("delta").otherwise(pl.col("delta") + 1).alias("call_delta"),
        )
        frames.append(
            side("C", "call").join(side("P", "put"), on="strike", how="full", coalesce=True)
            .join(smile, on="strike", how="left")
            .with_columns(
                (pl.col("call_delta") - 1).alias("put_delta"),
                pl.lit(expiry).alias("expiry"),
                pl.lit(surf["ts"][0]).alias("ts"),
                pl.lit(surf["forward"][0]).alias("forward"),
                pl.lit(surf["tau"][0]).alias("tau"),
            )
        )
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames).select(
        "expiry", "ts", "forward", "tau", "strike", "call_bid", "call_ask", "call_mid",
        "call_delta", "iv", "put_bid", "put_ask", "put_mid", "put_delta",
    ).sort(["expiry", "strike"])


def third_friday(year: int, month: int) -> date:
    """The standard monthly expiry: the Friday falling on the 15th-21st."""
    d = date(year, month, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def expiry_slots(day: date, available: list[str]) -> dict[str, tuple[date, str | None]]:
    """The chain board's three columns as of `day`: the next weekly, the next
    monthly and the monthly after it, each as (calendar date, captured expiry
    or None).

    The dates come from the calendar, not the capture, so a column the capture
    lacks still says which expiry it would be. "Weekly" is the next Friday that
    isn't a monthly — Monday-Thursday dailies (SPY, QQQ, IWM) are skipped so the
    column means the same for every symbol. An expiry counts from its own day
    on: it trades until the close.

    A captured expiry matches a slot up to a day early, which is where an
    exchange holiday on the Friday moves it.
    """
    monthly = third_friday(day.year, day.month)
    if monthly < day:
        nxt = (day.replace(day=1) + timedelta(days=32))
        monthly = third_friday(nxt.year, nxt.month)
    after = monthly.replace(day=1) + timedelta(days=32)
    weekly = day + timedelta(days=(4 - day.weekday()) % 7)
    while weekly.day in range(15, 22):  # a Friday on the 15th-21st is the monthly
        weekly += timedelta(days=7)

    def captured(slot: date) -> str | None:
        hits = [e for e in available
                if slot - timedelta(days=1) <= date.fromisoformat(e) <= slot]
        return max(hits) if hits else None

    return {
        name: (slot, captured(slot))
        for name, slot in (("weekly", weekly), ("monthly", monthly),
                           ("next monthly", third_friday(after.year, after.month)))
    }
