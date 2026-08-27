"""Who won the event.

Ranking asks whether a moment is interesting; this asks the trading question,
and answers it without a model. Take the at-the-money straddle on a **real
listed strike**, held fixed across the boundary — the same contract before and
after, because that is what someone actually owned — and compare what it cost
with what it became.

Two prices are reported, and the gap between them is the point:

- **mid to mid** is the headline, and what most tools stop at.
- **executable** is what the trade was really worth. A buyer lifts both offers
  going in and hits both bids coming out; a seller does the reverse. Because the
  capture stores `bid` and `ask` as separate series rather than a blended
  `bid_ask`, this is directly measurable instead of assumed. (Requesting BID and
  ASK separately also costs the same pacing budget as one BID_ASK request, so
  the capture gets this for free.)

Buyer and seller P&L are not mirror images once the spread is charged to both.
Both sides can lose, and on a wide book they often do — `spread_cost_pct` says
how much the market maker took out of the middle.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import polars as pl

from .cache import Chain


@dataclass
class Straddle:
    """One ATM straddle priced across an event boundary."""

    symbol: str
    expiry: str
    strike: float
    ts_before: datetime
    ts_after: datetime

    mid_before: float
    mid_after: float
    ask_before: float   # what a buyer pays to open
    bid_after: float    # what a buyer receives to close
    bid_before: float   # what a seller receives to open
    ask_after: float    # what a seller pays to close

    forward_before: float
    forward_after: float
    spot_before: float | None = None   # real captured underlying, where the snapshot has it
    spot_after: float | None = None

    @property
    def pnl_mid(self) -> float:
        return self.mid_after - self.mid_before

    @property
    def pnl_mid_pct(self) -> float:
        return self.pnl_mid / self.mid_before if self.mid_before else float("nan")

    @property
    def buyer_pnl(self) -> float:
        return self.bid_after - self.ask_before

    @property
    def buyer_pnl_pct(self) -> float:
        return self.buyer_pnl / self.ask_before if self.ask_before else float("nan")

    @property
    def seller_pnl(self) -> float:
        return self.bid_before - self.ask_after

    @property
    def seller_pnl_pct(self) -> float:
        return self.seller_pnl / self.bid_before if self.bid_before else float("nan")

    @property
    def round_trip_cost(self) -> float:
        """Premium the round-trip spread costs a buyer versus trading at mid.

        Charged as a fraction of the premium rather than of the P&L: dividing by
        P&L blows up whenever the mid barely moved, which is exactly the wide-book
        case the number exists to describe.
        """
        return (self.ask_before - self.mid_before) + (self.mid_after - self.bid_after)

    @property
    def round_trip_cost_pct(self) -> float:
        return self.round_trip_cost / self.mid_before if self.mid_before else float("nan")

    @property
    def uses_real_spot(self) -> bool:
        return self.spot_before is not None and self.spot_after is not None

    @property
    def realized_move(self) -> float:
        """How far the underlying actually moved.

        Real captured spot when the snapshot has it on both sides of the
        boundary; the put-call-parity forward otherwise, which is within
        basis points of spot for a chain this short-dated anyway.
        """
        if self.uses_real_spot:
            return abs(self.spot_after / self.spot_before - 1.0)
        return abs(self.forward_after / self.forward_before - 1.0)

    @property
    def breakeven_move(self) -> float:
        """Move the forward had to make for the buyer to break even at expiry."""
        return self.ask_before / self.forward_before if self.forward_before else float("nan")

    @property
    def verdict(self) -> str:
        """Who came out ahead, judged on executable prices."""
        if self.buyer_pnl > 0 and self.buyer_pnl >= self.seller_pnl:
            return "BUYER"
        if self.seller_pnl > 0:
            return "SELLER"
        return "BUYER" if self.buyer_pnl > self.seller_pnl else "SELLER"

    def as_dict(self) -> dict:
        d = asdict(self)
        d.update(
            pnl_mid=self.pnl_mid, pnl_mid_pct=self.pnl_mid_pct,
            buyer_pnl=self.buyer_pnl, buyer_pnl_pct=self.buyer_pnl_pct,
            seller_pnl=self.seller_pnl, seller_pnl_pct=self.seller_pnl_pct,
            round_trip_cost=self.round_trip_cost, round_trip_cost_pct=self.round_trip_cost_pct,
            realized_move=self.realized_move, uses_real_spot=self.uses_real_spot,
            breakeven_move=self.breakeven_move, verdict=self.verdict,
        )
        return d


def _legs(surface: pl.DataFrame, ts, strike: float) -> dict[str, float] | None:
    """Both legs of one strike at one timestamp."""
    rows = surface.filter((pl.col("ts") == ts) & (pl.col("strike") == strike))
    by_right = {r["right"]: r for r in rows.iter_rows(named=True)}
    if "C" not in by_right or "P" not in by_right:
        return None
    c, p = by_right["C"], by_right["P"]
    return {
        "mid": c["mid"] + p["mid"],
        "ask": c["ask"] + p["ask"],
        "bid": c["bid"] + p["bid"],
    }


def straddle_verdict(chain: Chain, ts_before, ts_after, strike: float | None = None) -> Straddle | None:
    """Price the ATM straddle across one boundary. None if either side is unquoted."""
    atm = chain.atm
    before = atm.filter(pl.col("ts") == ts_before)
    after = atm.filter(pl.col("ts") == ts_after)
    if not before.height or not after.height:
        return None

    # The strike is chosen at t0 and held: you cannot retroactively pick a different contract.
    k = strike if strike is not None else before["atm_strike"][0]
    if k is None:
        return None

    lo = _legs(chain.surface, ts_before, k)
    hi = _legs(chain.surface, ts_after, k)
    if lo is None or hi is None:
        return None

    return Straddle(
        symbol=chain.symbol, expiry=chain.expiry, strike=float(k),
        ts_before=ts_before, ts_after=ts_after,
        mid_before=lo["mid"], mid_after=hi["mid"],
        ask_before=lo["ask"], bid_after=hi["bid"],
        bid_before=lo["bid"], ask_after=hi["ask"],
        forward_before=before["forward"][0], forward_after=after["forward"][0],
        spot_before=before["spot_mid"][0] if "spot_mid" in before.columns else None,
        spot_after=after["spot_mid"][0] if "spot_mid" in after.columns else None,
    )


def verdict_table(chains: dict[tuple[str, str], Chain], events: pl.DataFrame) -> pl.DataFrame:
    """Straddle verdict for every event. Events with an unquoted leg are dropped."""
    rows = []
    for e in events.iter_rows(named=True):
        chain = chains.get((e["symbol"], e["expiry"]))
        if chain is None:
            continue
        s = straddle_verdict(chain, e["ts_before"], e["ts_after"])
        if s is None:
            continue
        row = s.as_dict()
        row.update(kind=e["kind"], score=e["score"], surprise=e["surprise"], boundary=e["boundary"])
        rows.append(row)
    return pl.DataFrame(rows) if rows else pl.DataFrame()
