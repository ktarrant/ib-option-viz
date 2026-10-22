from pathlib import Path

import pytest

from optionviz import db
from optionviz.catalog import Catalog, Snapshot


def test_discover_finds_the_snapshot(catalog):
    assert not catalog.is_empty
    assert catalog.fingerprint != "empty"
    assert all(s.n_bars > 0 for s in catalog.snapshots)


def test_missing_directory_is_empty_not_an_error(tmp_path):
    cat = Catalog.discover(tmp_path / "nope")
    assert cat.is_empty
    assert cat.symbols() == []
    assert cat.resolve("SPY", "2026-08-21") is None


def test_non_capture_sqlite_is_ignored(tmp_path):
    """A stray .sqlite3 in the folder must not break discovery."""
    import sqlite3

    junk = tmp_path / "notes.sqlite3"
    sqlite3.connect(junk).execute("CREATE TABLE t (a INT)")
    assert Catalog.discover(tmp_path).is_empty


def test_resolve_routes_to_a_snapshot_holding_bars(catalog):
    for row in catalog.best_chains().iter_rows(named=True):
        path = catalog.resolve(row["symbol"], row["expiry"])
        assert path is not None and path.exists()


def test_chains_without_bars_are_not_offered(catalog):
    """The 2026-09-18 chain is catalogued but uncaptured, so it must not appear."""
    best = catalog.best_chains()
    assert (best["n_bars"] > 0).all()


def test_fingerprint_changes_with_content(tmp_path):
    a = Snapshot(tmp_path / "s.sqlite3", 100, 1.0, 5, 10)
    b = Snapshot(tmp_path / "s.sqlite3", 200, 1.0, 5, 10)
    assert a.fingerprint != b.fingerprint


def test_a_catalog_of_many_dbs_has_a_short_fingerprint(tmp_path):
    """It names a cache directory, and the server unions a DB per published bundle."""
    snaps = [Snapshot(tmp_path / f"bundle{i}.sqlite3", 10**9, 1.8e9, 9, 10) for i in range(40)]
    cat = Catalog([], [tmp_path])
    cat.snapshots = snaps
    fp = cat.fingerprint
    assert len(fp) < 64
    cat.snapshots = snaps[:-1]
    assert cat.fingerprint != fp


def test_a_publish_changes_the_catalog_key(tmp_path):
    """The server swaps a `current` symlink per publish; the cached catalog must notice."""
    from optionviz.ui.data import files_state

    run = tmp_path / "run"
    run.mkdir()
    (run / "bundle.sqlite3").write_bytes(b"old")
    (tmp_path / "news-llm.sqlite3").write_bytes(b"not a snapshot")
    before = files_state(str(tmp_path))
    assert [Path(p).name for p, *_ in before] == ["bundle.sqlite3"]
    (run / "bundle.sqlite3").write_bytes(b"newer, and longer")
    assert files_state(str(tmp_path)) != before


def test_connections_are_read_only(catalog):
    path = catalog.snapshots[0].path
    with pytest.raises(Exception):
        with db.connect(path) as conn:
            conn.execute("CREATE TABLE should_not_exist (a INT)")
