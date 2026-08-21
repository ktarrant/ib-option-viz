"""Snapshot tables -> tidy frames.

`bars` is long-format: one row per (contract, ts, bar_size, data_type). Every
view we build wants bid and ask side by side, so the pivot happens here once.

Two conventions from the capture side that this module enforces so callers never
have to remember them:

- **bid/ask is dense, trades is sparse.** A thin strike prints hundreds of bid
  bars and a handful of trades bars. Continuous series come from bid/ask;
  `trades` is only ever an activity overlay.
- **`volume` is 0 on every non-trades row.** IB reports -1 for bid/ask series and
  the column is NOT NULL, so it gets normalised to 0. Reading that as "no
  trading" is wrong — the field is meaningless there, so we don't carry it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from . import db

BAR_SIZE = "30min"


@dataclass
class Quality:
    """What we dropped getting to a clean mid series. Surfaced, never hidden."""

    rows_read: int = 0
    dropped_unpaired: int = 0  # only one side of the market quoted
    dropped_crossed: int = 0  # bid > ask
    dropped_nonpositive: int = 0  # mid <= 0

    @property
    def rows_kept(self) -> int:
        return self.rows_read - self.dropped_unpaired - self.dropped_crossed - self.dropped_nonpositive

    def as_dict(self) -> dict[str, int]:
        return {
            "rows_read": self.rows_read,
            "rows_kept": self.rows_kept,
            "dropped_unpaired": self.dropped_unpaired,
            "dropped_crossed": self.dropped_crossed,
            "dropped_nonpositive": self.dropped_nonpositive,
        }


# Every writer emits RFC3339 UTC with a literal `+00:00` offset. Range queries
# on the raw text rely on that exact form, and so does this parse.
TS_FORMAT = "%Y-%m-%dT%H:%M:%S%:z"


def _to_utc(col: str = "ts") -> pl.Expr:
    return pl.col(col).str.to_datetime(format=TS_FORMAT, time_unit="us", time_zone="UTC")


def option_mids(path: str | Path, symbol: str, expiry: str) -> tuple[pl.DataFrame, Quality]:
    """Tidy bid/ask/mid per (ts, strike, right) for one chain.

    Returns the clean frame plus a Quality record of what was discarded.
    """
    raw = db.query(
        path,
        """
        SELECT b.ts AS ts, c.strike AS strike, c.right AS right,
               MAX(CASE WHEN b.data_type = 'bid' THEN b.close END) AS bid,
               MAX(CASE WHEN b.data_type = 'ask' THEN b.close END) AS ask
        FROM bars b JOIN contracts c ON c.id = b.contract_id
        WHERE c.symbol = ? AND c.expiry = ? AND c.kind = 'option'
          AND b.bar_size = ? AND b.data_type IN ('bid', 'ask')
        GROUP BY b.ts, c.strike, c.right
        """,
        (symbol, expiry, BAR_SIZE),
        schema=_MIDS_RAW_SCHEMA,
    )
    q = Quality(rows_read=raw.height)
    if not raw.height:
        return pl.DataFrame(schema=_MIDS_SCHEMA), q

    df = raw.with_columns(
        _to_utc(),
        pl.col("strike").cast(pl.Float64),
        pl.col("bid").cast(pl.Float64),
        pl.col("ask").cast(pl.Float64),
    )

    paired = df.filter(pl.col("bid").is_not_null() & pl.col("ask").is_not_null())
    q.dropped_unpaired = df.height - paired.height

    uncrossed = paired.filter(pl.col("bid") <= pl.col("ask"))
    q.dropped_crossed = paired.height - uncrossed.height

    out = uncrossed.with_columns(
        ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
        (pl.col("ask") - pl.col("bid")).alias("spread"),
    ).filter(pl.col("mid") > 0)
    q.dropped_nonpositive = uncrossed.height - out.height

    out = out.with_columns(
        (pl.col("spread") / pl.col("mid")).alias("rel_spread")
    ).sort(["ts", "strike", "right"])
    return out, q


def trade_activity(path: str | Path, symbol: str, expiry: str) -> pl.DataFrame:
    """Sparse traded volume per (ts, strike, right). Overlay only."""
    raw = db.query(
        path,
        """
        SELECT b.ts AS ts, c.strike AS strike, c.right AS right,
               b.volume AS volume, b.trade_count AS trade_count, b.close AS last
        FROM bars b JOIN contracts c ON c.id = b.contract_id
        WHERE c.symbol = ? AND c.expiry = ? AND c.kind = 'option'
          AND b.bar_size = ? AND b.data_type = 'trades'
        """,
        (symbol, expiry, BAR_SIZE),
        schema=_TRADES_SCHEMA,
    )
    if not raw.height:
        return pl.DataFrame(schema=_TRADES_SCHEMA)
    return raw.with_columns(
        _to_utc(), pl.col("strike").cast(pl.Float64), pl.col("volume").cast(pl.Float64)
    ).sort(["ts", "strike", "right"])


def stock_daily(path: str | Path, symbol: str) -> pl.DataFrame:
    """Daily underlying bars. The only real underlying series in the DB."""
    raw = db.query(
        path,
        """
        SELECT b.ts AS ts, b.open AS open, b.high AS high, b.low AS low,
               b.close AS close, b.volume AS volume
        FROM bars b JOIN contracts c ON c.id = b.contract_id
        WHERE c.symbol = ? AND c.kind = 'stock' AND b.bar_size = '1d'
          AND b.data_type = 'trades'
        ORDER BY b.ts
        """,
        (symbol,),
        schema=_STOCK_SCHEMA,
    )
    if not raw.height:
        return pl.DataFrame(schema=_STOCK_SCHEMA)
    return raw.with_columns(_to_utc(), pl.col("ts").str.slice(0, 10).alias("date"))


def coverage(path: str | Path) -> pl.DataFrame:
    """What was asked for vs what came back.

    `bar_count = 0` is a real answer — IB was asked and had nothing, which for a
    deep-OTM strike is the norm. `bars` alone cannot tell that apart from "never
    asked", which is the whole reason this table exists.
    """
    return db.query(
        path,
        """
        SELECT c.symbol, c.expiry, c.strike, c.right, c.kind,
               v.bar_size, v.what_to_show, v.bar_count,
               v.window_start, v.window_end, v.first_ts, v.last_ts
        FROM capture_coverage v JOIN contracts c ON c.id = v.contract_id
        """,
    )


def chain_strikes(path: str | Path, symbol: str, expiry: str) -> pl.DataFrame:
    """Every catalogued strike, whether or not it has bars."""
    return db.query(
        path,
        """
        SELECT strike, right, ibkr_con_id, trading_class, local_symbol
        FROM contracts
        WHERE symbol = ? AND expiry = ? AND kind = 'option'
        ORDER BY strike, right
        """,
        (symbol, expiry),
    )


def latest_open_interest(path: str | Path, symbol: str, expiry: str) -> pl.DataFrame:
    """Latest OI/greeks snapshot per contract.

    Point-in-time only: open interest cannot be re-obtained after expiration, so
    the capture stores it append-only and there may be just one cross-section.
    """
    return db.query(
        path,
        """
        SELECT c.strike, c.right, s.open_interest, s.implied_volatility,
               s.delta, s.gamma, s.theta, s.vega, s.underlying_price,
               s.captured_at, s.snapshot_kind
        FROM option_snapshots s JOIN contracts c ON c.id = s.contract_id
        WHERE c.symbol = ? AND c.expiry = ?
          AND s.id IN (SELECT MAX(id) FROM option_snapshots GROUP BY contract_id)
        ORDER BY c.strike, c.right
        """,
        (symbol, expiry),
    )


_MIDS_RAW_SCHEMA = {
    "ts": pl.Utf8,
    "strike": pl.Float64,
    "right": pl.Utf8,
    "bid": pl.Float64,
    "ask": pl.Float64,
}
_MIDS_SCHEMA = {
    "ts": pl.Datetime("us", "UTC"),
    "strike": pl.Float64,
    "right": pl.Utf8,
    "bid": pl.Float64,
    "ask": pl.Float64,
    "mid": pl.Float64,
    "spread": pl.Float64,
    "rel_spread": pl.Float64,
}
_TRADES_SCHEMA = {
    "ts": pl.Utf8,
    "strike": pl.Float64,
    "right": pl.Utf8,
    "volume": pl.Float64,
    "trade_count": pl.Int64,
    "last": pl.Float64,
}
_STOCK_SCHEMA = {
    "ts": pl.Utf8,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
}
