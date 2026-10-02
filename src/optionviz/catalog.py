"""Discovery across capture snapshots.

The catalog accretes: each capture run harvests one soon-to-expire chain and
drops another `*.sqlite3` in the snapshot directory. A newer snapshot is usually
a superset of an older one, but older ones may be pruned and a run can fail
partway, so we resolve *per chain* — for each (symbol, expiry) we use whichever
snapshot actually holds the most bars for it, rather than assuming one file wins.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from . import db

ENV_DIR = "OPTIONVIZ_DB_DIR"
DEFAULT_DIR = Path.home() / "Documents" / "Options Database"

# Tables a usable snapshot must have. A capture DB missing these is a dev stub.
REQUIRED_TABLES = {"contracts", "bars", "capture_coverage"}


def default_dir() -> Path:
    return Path(os.environ.get(ENV_DIR) or DEFAULT_DIR).expanduser()


@dataclass(frozen=True)
class Snapshot:
    """One capture snapshot file plus the metadata needed to route queries."""

    path: Path
    size: int
    mtime: float
    user_version: int
    n_bars: int

    @property
    def fingerprint(self) -> str:
        """Cache key. Size+mtime is enough — snapshots are written once, never edited."""
        return f"{self.path.stem}-{self.size}-{int(self.mtime)}-v{self.user_version}"

    @property
    def label(self) -> str:
        return f"{self.path.name} ({self.n_bars:,} bars)"


def _chain_index(snap: Snapshot) -> pl.DataFrame:
    """(symbol, expiry) -> bar count, for chains that actually have bars."""
    return db.query(
        snap.path,
        """
        SELECT c.symbol, c.expiry, COUNT(*) AS n_bars,
               MIN(b.ts) AS first_ts, MAX(b.ts) AS last_ts,
               COUNT(DISTINCT c.id) AS n_contracts
        FROM bars b JOIN contracts c ON c.id = b.contract_id
        WHERE c.kind = 'option' AND b.bar_size = '30min'
        GROUP BY c.symbol, c.expiry
        """,
    )


def _read_snapshot(path: Path) -> Snapshot | None:
    try:
        if not REQUIRED_TABLES.issubset(db.table_names(path)):
            return None
        n_bars = int(db.scalar(path, "SELECT COUNT(*) FROM bars") or 0)
        if n_bars == 0:
            return None
        stat = path.stat()
        return Snapshot(
            path=path,
            size=stat.st_size,
            mtime=stat.st_mtime,
            user_version=db.user_version(path),
            n_bars=n_bars,
        )
    except Exception:
        # A partially written or non-capture .sqlite3 in the folder is not an error.
        return None


class Catalog:
    """All usable snapshots in a directory, with per-chain routing."""

    def __init__(self, snapshots: list[Snapshot]):
        self.snapshots = sorted(snapshots, key=lambda s: s.mtime, reverse=True)
        frames = []
        for snap in self.snapshots:
            idx = _chain_index(snap)
            if idx.height:
                frames.append(idx.with_columns(pl.lit(str(snap.path)).alias("path")))
        self.chains = (
            pl.concat(frames, how="vertical") if frames else pl.DataFrame(schema=_CHAIN_SCHEMA)
        )

    @classmethod
    def discover(cls, directory: str | Path | None = None) -> "Catalog":
        root = Path(directory) if directory else default_dir()
        if not root.is_dir():
            return cls([])
        found = [s for p in sorted(root.glob("*.sqlite3")) if (s := _read_snapshot(p))]
        return cls(found)

    @property
    def is_empty(self) -> bool:
        return not self.snapshots

    @property
    def fingerprint(self) -> str:
        """Cache key for the catalog as a whole."""
        return "+".join(s.fingerprint for s in self.snapshots) or "empty"

    def best_chains(self) -> pl.DataFrame:
        """One row per (symbol, expiry): the snapshot holding the most bars for it."""
        if not self.chains.height:
            return self.chains
        return (
            self.chains.sort("n_bars", descending=True)
                       .unique(subset=["symbol", "expiry"], keep="first")
                       .sort(["symbol", "expiry"])
        )

    def resolve(self, symbol: str, expiry: str) -> Path | None:
        """Which snapshot file to read this chain's bars from."""
        hit = self.best_chains().filter(
            (pl.col("symbol") == symbol) & (pl.col("expiry") == expiry)
        )
        return Path(hit["path"][0]) if hit.height else None

    def symbols(self) -> list[str]:
        best = self.best_chains()
        return sorted(best["symbol"].unique().to_list()) if best.height else []

    def stock_symbols(self) -> list[str]:
        """Every symbol with a stock contract in any snapshot. Wider than
        `symbols()`, which only sees chains with 30-minute option bars."""
        return sorted({
            s
            for snap in self.snapshots
            for s in db.query(
                snap.path, "SELECT DISTINCT symbol FROM contracts WHERE kind = 'stock'"
            )["symbol"].to_list()
        })

    def expiries(self, symbol: str | None = None) -> list[str]:
        best = self.best_chains()
        if not best.height:
            return []
        if symbol:
            best = best.filter(pl.col("symbol") == symbol)
        return sorted(best["expiry"].unique().to_list())


_CHAIN_SCHEMA = {
    "symbol": pl.Utf8,
    "expiry": pl.Utf8,
    "n_bars": pl.Int64,
    "first_ts": pl.Utf8,
    "last_ts": pl.Utf8,
    "n_contracts": pl.Int64,
    "path": pl.Utf8,
}
