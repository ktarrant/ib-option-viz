"""Discovery across capture snapshots.

The catalog accretes: each capture run harvests one soon-to-expire chain and
drops another `*.sqlite3` in the snapshot directory. A newer snapshot is usually
a superset of an older one, but older ones may be pruned and a run can fail
partway, so we resolve *per chain* — for each (symbol, expiry) we use whichever
snapshot actually holds the most bars for it, rather than assuming one file wins.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from . import db

ENV_DIR = "OPTIONVIZ_DB_DIR"
DEFAULT_DIR = Path.home() / "Documents" / "Options Database"

# Tables a usable snapshot must have. A capture DB missing these is a dev stub.
REQUIRED_TABLES = {"contracts", "bars", "capture_coverage"}


def default_dirs() -> list[Path]:
    """`OPTIONVIZ_DB_DIR` (several folders joined with `os.pathsep`), else
    `~/Documents/Options Database`."""
    raw = os.environ.get(ENV_DIR)
    if not raw:
        return [DEFAULT_DIR.expanduser()]
    return [Path(p).expanduser() for p in raw.split(os.pathsep) if p]


def default_dir() -> Path:
    return default_dirs()[0]


def _candidates(root: Path) -> list[Path]:
    """The DB files a folder offers: its own `*.sqlite3`, and the bundle each
    published backtest run carries one level down (`<run>/bundle.sqlite3`).
    The headline-score cache is not a snapshot."""
    found = sorted(root.glob("*.sqlite3")) + sorted(root.glob("*/bundle.sqlite3"))
    return [p for p in found if p.name != "news-llm.sqlite3"]


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
    if db.is_research(snap.path):
        # The research DB's own catalog, at the same 30-minute size the chain
        # pages read. Its two-sided bars stand in for the capture's rows (a
        # bid and an ask each), so files of both kinds rank on one scale.
        return db.query(
            snap.path,
            f"""
            SELECT ch.symbol, ch.expiry, ch.quote_bars * 2 AS n_bars,
                   {db.RESEARCH_TS.format(t='ch.first_t')} AS first_ts,
                   {db.RESEARCH_TS.format(t='ch.last_t')} AS last_ts,
                   (SELECT COUNT(*) FROM contracts c
                     WHERE c.symbol = ch.symbol AND c.expiry = ch.expiry) AS n_contracts
            FROM chains ch WHERE ch.minutes = 30
            """,
            schema={k: v for k, v in _CHAIN_SCHEMA.items() if k != "path"},
        )
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
        if db.is_research(path):
            # Counting 280M quote rows would take minutes; the catalog has it.
            n_bars = int(db.scalar(path, "SELECT SUM(quote_bars) * 2 FROM chains") or 0)
            version = int(db.scalar(
                path, "SELECT value FROM research_meta WHERE key = 'snapshot_schema'") or 0)
        else:
            if not REQUIRED_TABLES.issubset(db.table_names(path)):
                return None
            n_bars = int(db.scalar(path, "SELECT COUNT(*) FROM bars") or 0)
            version = db.user_version(path)
        if n_bars == 0:
            return None
        stat = path.stat()
        return Snapshot(
            path=path,
            size=stat.st_size,
            mtime=stat.st_mtime,
            user_version=version,
            n_bars=n_bars,
        )
    except Exception:
        # A partially written or non-capture .sqlite3 in the folder is not an error.
        return None


class Catalog:
    """All usable snapshots in a directory, with per-chain routing."""

    def __init__(self, snapshots: list[Snapshot], directories: list[Path] | None = None):
        self.snapshots = sorted(snapshots, key=lambda s: s.mtime, reverse=True)
        #: The folders searched, in order (where the news cache is looked for).
        self.directories = directories or sorted({s.path.parent for s in self.snapshots})
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
        roots = [Path(directory)] if directory else default_dirs()
        roots = [r for r in roots if r.is_dir()]
        found = [s for r in roots for p in _candidates(r) if (s := _read_snapshot(p))]
        return cls(found, roots)

    @property
    def is_empty(self) -> bool:
        return not self.snapshots

    @property
    def fingerprint(self) -> str:
        """Cache key for the catalog as a whole, and a cache directory's name.

        One snapshot keeps its own readable key. Several are hashed: the server
        unions the recent DB with every published bundle, and their keys joined
        outgrow a 255-byte file name.
        """
        if len(self.snapshots) <= 1:
            return self.snapshots[0].fingerprint if self.snapshots else "empty"
        joined = "+".join(s.fingerprint for s in self.snapshots)
        return f"{len(self.snapshots)}dbs-{hashlib.sha256(joined.encode()).hexdigest()[:16]}"

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
        def stocks(path: Path) -> list[str]:
            sql = ("SELECT DISTINCT symbol FROM stock_bars" if db.is_research(path)
                   else "SELECT DISTINCT symbol FROM contracts WHERE kind = 'stock'")
            return db.query(path, sql)["symbol"].to_list()

        return sorted({s for snap in self.snapshots for s in stocks(snap.path)})

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
