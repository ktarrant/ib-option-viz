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
