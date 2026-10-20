"""A research DB (ib-option-calc's docs/research-db.md) must read exactly as
the capture snapshot it was built from. The conversion below is the one
`scripts/research.py build` does for an IB-only build."""

import sqlite3

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from optionviz import backtest, frames
from optionviz.catalog import Catalog

TS = ["2026-07-01T13:30:00+00:00", "2026-07-01T14:00:00+00:00", "2026-07-02T13:30:00+00:00"]


def capture(path):
    db = sqlite3.connect(path)
    db.executescript(
        """
        PRAGMA user_version = 9;
        CREATE TABLE contracts (id INTEGER PRIMARY KEY, symbol TEXT, kind TEXT, exchange TEXT,
            currency TEXT, ibkr_con_id INTEGER, expiry TEXT, strike REAL, right TEXT,
            multiplier INTEGER, trading_class TEXT, local_symbol TEXT);
        CREATE TABLE bars (contract_id INTEGER, ts TEXT, bar_size TEXT, data_type TEXT,
            open REAL, high REAL, low REAL, close REAL, volume REAL, wap REAL, trade_count INTEGER,
            PRIMARY KEY (contract_id, ts, bar_size, data_type));  -- as the capture's
        CREATE TABLE capture_coverage (contract_id INTEGER, bar_size TEXT, what_to_show TEXT,
            bar_count INTEGER, window_start TEXT, window_end TEXT, first_ts TEXT, last_ts TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY, symbol TEXT, event_date TEXT NOT NULL,
            event_time TEXT, session_timing TEXT, kind TEXT NOT NULL, source TEXT NOT NULL,
            source_id TEXT, title TEXT, detail_json TEXT);
        INSERT INTO contracts (id, symbol, kind, exchange, currency, expiry, strike, right) VALUES
            (1, 'TEST', 'stock', 'SMART', 'USD', NULL, NULL, NULL),
            (2, 'TEST', 'option', 'SMART', 'USD', '2026-08-21', 100.0, 'C'),
            (3, 'TEST', 'option', 'SMART', 'USD', '2026-08-21', 100.0, 'P');
        INSERT INTO events (symbol, event_date, kind, source, title)
            VALUES ('TEST', '2026-07-02', 'earnings', 'edgar', 'Q2');
        """
    )
    rows = []
    for i, ts in enumerate(TS):
        for cid, mid in ((2, 5.0 + i / 10), (3, 4.5 - i / 10)):
            for size in ("30min", "15min"):
                rows += [(cid, ts, size, "bid", mid - 0.05), (cid, ts, size, "ask", mid + 0.05)]
        for size in ("30min", "5min", "1d"):
            rows += [(1, ts if size != "1d" else ts[:10] + "T00:00:00+00:00", size, kind, px)
                     for kind, px in (("trades", 100 + i), ("bid", 99.99 + i), ("ask", 100.01 + i))]
    db.executemany(
        "INSERT OR IGNORE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, 10, NULL, NULL)",
        [(c, t, s, k, round(p, 2), round(p, 2) + 0.5, round(p, 2) - 0.5, round(p, 2)) for c, t, s, k, p in rows],
    )
    db.commit()
    db.close()


def research(src, path):
    db = sqlite3.connect(path)
    db.execute("ATTACH ? AS cap", (str(src),))
    db.executescript(
        """
        CREATE TABLE research_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO research_meta VALUES ('format_version', '1'), ('snapshot_schema', '9');
        CREATE TABLE contracts (id INTEGER PRIMARY KEY, symbol TEXT, expiry TEXT, strike REAL, right TEXT);
        INSERT INTO contracts SELECT id, symbol, expiry, strike, right FROM cap.contracts WHERE kind = 'option';
        CREATE TABLE quotes (contract_id INTEGER, minutes INTEGER, t INTEGER, bid INTEGER, ask INTEGER,
            source INTEGER, PRIMARY KEY (contract_id, minutes, t)) WITHOUT ROWID;
        INSERT INTO quotes SELECT b.contract_id, CAST(replace(b.bar_size, 'min', '') AS INTEGER),
            CAST(strftime('%s', b.ts) AS INTEGER) / 60,
            CAST(round(MAX(CASE WHEN b.data_type = 'bid' THEN b.close END) * 10000) AS INTEGER),
            CAST(round(MAX(CASE WHEN b.data_type = 'ask' THEN b.close END) * 10000) AS INTEGER), 0
            FROM contracts c JOIN cap.bars b ON b.contract_id = c.id
            WHERE b.data_type IN ('bid', 'ask') GROUP BY b.contract_id, b.ts, b.bar_size;
        CREATE TABLE stock_bars (symbol TEXT, minutes INTEGER, t INTEGER, open REAL, high REAL, low REAL,
            close REAL, volume REAL, bid REAL, ask REAL, PRIMARY KEY (symbol, minutes, t)) WITHOUT ROWID;
        INSERT INTO stock_bars SELECT c.symbol,
            CASE b.bar_size WHEN '1d' THEN 1440 ELSE CAST(replace(b.bar_size, 'min', '') AS INTEGER) END,
            CAST(strftime('%s', b.ts) AS INTEGER) / 60,
            MAX(CASE WHEN b.data_type = 'trades' THEN b.open END),
            MAX(CASE WHEN b.data_type = 'trades' THEN b.high END),
            MAX(CASE WHEN b.data_type = 'trades' THEN b.low END),
            MAX(CASE WHEN b.data_type = 'trades' THEN b.close END),
            MAX(CASE WHEN b.data_type = 'trades' THEN b.volume END),
            MAX(CASE WHEN b.data_type = 'bid' THEN b.close END),
            MAX(CASE WHEN b.data_type = 'ask' THEN b.close END)
            FROM cap.contracts c JOIN cap.bars b ON b.contract_id = c.id
            WHERE c.kind = 'stock' GROUP BY c.symbol, b.bar_size, b.ts;
        CREATE TABLE events AS SELECT * FROM cap.events;
        CREATE TABLE chains (symbol TEXT, expiry TEXT, minutes INTEGER, strikes INTEGER,
            paired_strikes INTEGER, timestamps INTEGER, sessions INTEGER, first_t INTEGER, last_t INTEGER,
            quote_bars INTEGER, stock_overlap INTEGER, tape_share REAL,
            PRIMARY KEY (symbol, expiry, minutes)) WITHOUT ROWID;
        INSERT INTO chains SELECT c.symbol, c.expiry, q.minutes, COUNT(DISTINCT c.strike), 1,
            COUNT(DISTINCT q.t), COUNT(DISTINCT q.t / 1440), MIN(q.t), MAX(q.t), COUNT(*), 0, 0
            FROM quotes q JOIN contracts c ON c.id = q.contract_id GROUP BY c.symbol, c.expiry, q.minutes;
        """
    )
    db.commit()
    db.close()


@pytest.fixture
def pair(tmp_path):
    (tmp_path / "cap").mkdir()
    (tmp_path / "res").mkdir()
    cap, res = tmp_path / "cap" / "snap.sqlite3", tmp_path / "res" / "research.sqlite3"
    capture(cap)
    research(cap, res)
    return cap, res


def test_frames_read_a_research_db_as_its_snapshot(pair):
    cap, res = pair
    a, qa = frames.option_mids(cap, "TEST", "2026-08-21")
    b, qb = frames.option_mids(res, "TEST", "2026-08-21")
    assert a.height == 6 and qa == qb
    assert_frame_equal(a, b)
    start, end = TS[0], "2026-07-03T00:00:00+00:00"
    assert_frame_equal(frames.option_quotes(cap, "TEST", start, end).sort(pl.all()),
                       frames.option_quotes(res, "TEST", start, end).sort(pl.all()))
    assert_frame_equal(frames.stock_intraday(cap, "TEST")[0], frames.stock_intraday(res, "TEST")[0])
    assert_frame_equal(frames.stock_daily(cap, "TEST"), frames.stock_daily(res, "TEST"))
    assert_frame_equal(frames.events_for_symbol(cap, "TEST"), frames.events_for_symbol(res, "TEST"))
    assert frames.trade_activity(res, "TEST", "2026-08-21").height == 0


def test_the_catalog_takes_research_dbs_and_bundles(pair, tmp_path):
    cap, res = pair
    a, b = Catalog.discover(cap.parent), Catalog.discover(res.parent)
    assert (a.symbols(), a.expiries(), a.stock_symbols()) == (b.symbols(), b.expiries(), b.stock_symbols())
    assert a.best_chains()["n_bars"].to_list() == b.best_chains()["n_bars"].to_list()
    for minutes in (30, backtest.DAILY):
        assert_frame_equal(backtest.underlying_bars(a, "TEST", minutes),
                           backtest.underlying_bars(b, "TEST", minutes))
    # A published run's bundle sits one level down, and several folders combine.
    run = tmp_path / "results" / "vol_timing_all"
    run.mkdir(parents=True)
    research(cap, run / "bundle.sqlite3")
    both = Catalog(
        [s for d in (cap.parent, tmp_path / "results") for s in Catalog.discover(d).snapshots])
    assert len(both.snapshots) == 2
    assert len(Catalog.discover(tmp_path / "results").snapshots) == 1
