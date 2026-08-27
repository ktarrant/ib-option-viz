"""Parquet cache for derived frames.

The IV solve is cheap (~0.2s a chain) but the app rebuilds on every rerun, and
that adds up across twelve symbols. Snapshots are written once and never edited,
so a fingerprint of (name, size, mtime, schema version) is a sound cache key — a
new capture drops a new file and invalidates naturally.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from . import frames, surface
from .catalog import Catalog

CACHE_ROOT = Path(__file__).resolve().parents[2] / "data" / "cache"


@dataclass
class Chain:
    """Everything the views need about one (symbol, expiry).

    `atm` carries `spot_mid`/`spot_bid`/`spot_ask` alongside the parity-derived
    `forward`: real captured underlying quotes, joined in on `ts`. Coverage is
    typically strong but not total (the stock tape is denser than the option
    bars it's joined onto), and null on any snapshot older than schema v6.
    `forward` remains what the IV solve uses - it is rate/dividend-free by
    construction, which spot alone is not - but `spot_mid` is the more honest
    number for realized-move and price-chart purposes wherever it exists.
    """

    symbol: str
    expiry: str
    surface: pl.DataFrame
    atm: pl.DataFrame
    quality: dict[str, int]

    @property
    def is_empty(self) -> bool:
        return not self.atm.height


def _chain_dir(fingerprint: str, symbol: str, expiry: str) -> Path:
    return CACHE_ROOT / fingerprint / f"{symbol}_{expiry}"


def _prefixed(d: dict, prefix: str) -> dict:
    return {f"{prefix}_{k}": v for k, v in d.items()}


def build_chain(path: Path, symbol: str, expiry: str) -> Chain:
    mids, q = frames.option_mids(path, symbol, expiry)
    surf, atm = surface.build(mids, expiry)

    # Real spot, where the capture has it - a schema-v6 addition, so an older
    # snapshot in the catalog simply returns an empty (but typed) frame here and
    # the join leaves spot_mid/spot_bid/spot_ask null throughout.
    spot, spot_q = frames.stock_intraday(path, symbol)
    if atm.height:
        atm = atm.join(
            spot.select(
                "ts", pl.col("mid").alias("spot_mid"),
                pl.col("bid").alias("spot_bid"), pl.col("ask").alias("spot_ask"),
            ),
            on="ts", how="left",
        )

    quality = {**_prefixed(q.as_dict(), "option"), **_prefixed(spot_q.as_dict(), "spot")}
    return Chain(symbol, expiry, surf, atm, quality)


def load_chain(catalog: Catalog, symbol: str, expiry: str, use_cache: bool = True) -> Chain:
    src = catalog.resolve(symbol, expiry)
    if src is None:
        return Chain(symbol, expiry, pl.DataFrame(), pl.DataFrame(), {})

    d = _chain_dir(catalog.fingerprint, symbol, expiry)
    surf_p, atm_p = d / "surface.parquet", d / "atm.parquet"
    if use_cache and surf_p.exists() and atm_p.exists():
        return Chain(symbol, expiry, pl.read_parquet(surf_p), pl.read_parquet(atm_p), {})

    chain = build_chain(src, symbol, expiry)
    if use_cache and chain.surface.height:
        d.mkdir(parents=True, exist_ok=True)
        chain.surface.write_parquet(surf_p)
        chain.atm.write_parquet(atm_p)
    return chain


def load_all(catalog: Catalog, use_cache: bool = True) -> dict[tuple[str, str], Chain]:
    out = {}
    for row in catalog.best_chains().iter_rows(named=True):
        key = (row["symbol"], row["expiry"])
        chain = load_chain(catalog, *key, use_cache=use_cache)
        if not chain.is_empty:
            out[key] = chain
    return out
