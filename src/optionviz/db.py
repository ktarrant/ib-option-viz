"""Read-only SQLite access to capture snapshots.

Snapshots are produced by `VACUUM INTO` and are self-contained: no `-wal`
sidecar, `journal_mode=delete`. We open them in true read-only mode so a viewer
can never corrupt a capture that, for expired options, is irreplaceable.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import polars as pl


@contextmanager
def connect(path: str | Path):
    """Open a snapshot read-only. Never use a plain sqlite3.connect() here."""
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        yield conn
    finally:
        conn.close()


def query(
    path: str | Path, sql: str, params: Sequence[Any] = (), schema: dict[str, Any] | None = None
) -> pl.DataFrame:
    """Run a query and return a polars frame, preserving column names."""
    with connect(path) as conn:
        cur = conn.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    if not rows:
        return pl.DataFrame(schema=schema or {c: pl.Utf8 for c in cols})
    return pl.DataFrame(rows, schema=cols, orient="row")


def scalar(path: str | Path, sql: str, params: Sequence[Any] = ()) -> Any:
    with connect(path) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return row[0] if row else None


def user_version(path: str | Path) -> int:
    return int(scalar(path, "PRAGMA user_version") or 0)


def table_names(path: str | Path) -> set[str]:
    with connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# A research DB (ib-option-calc's docs/research-db.md) holds the same chains in
# a compact layout: option quote closes as integers on integer bar starts, and
# the underlying in `stock_bars`. Readers branch on this, and every research
# query returns the capture query's own columns, so what follows is shared.
RESEARCH_FORMAT_VERSION = "1"

#: SQL for a research row's `ts`, in the capture's stored text form.
RESEARCH_TS = "strftime('%Y-%m-%dT%H:%M:%S+00:00', {t} * 60, 'unixepoch')"


def is_research(path: str | Path) -> bool:
    """True for a research DB, False for a capture snapshot. A research DB of
    a format this code does not know is refused."""
    if "research_meta" not in table_names(path):
        return False
    version = scalar(path, "SELECT value FROM research_meta WHERE key = 'format_version'")
    if version != RESEARCH_FORMAT_VERSION:
        raise ValueError(f"{path} is research format {version}; this reads {RESEARCH_FORMAT_VERSION}")
    return True


def minutes(bar_size: str) -> int:
    """'15min' -> 15, '1d' -> 1440: a capture bar size as a research DB's `minutes`."""
    return 1440 if bar_size == "1d" else int(bar_size.removesuffix("min"))


def ts_minutes(ts: str) -> int:
    """Capture-form RFC3339 text -> unix minutes, a research DB's `t`."""
    from datetime import datetime

    return int(datetime.fromisoformat(ts).timestamp()) // 60
