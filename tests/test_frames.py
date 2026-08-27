"""Unit coverage for the pure parts of frames.py - no snapshot required.

Golden checks against a real snapshot (stock_intraday coverage, events_for_symbol
against actual EDGAR rows) live in test_golden.py instead, since they need real
data to mean anything.
"""

import polars as pl
import pytest

from optionviz.frames import Quality, _clean_bid_ask


def _df(rows):
    return pl.DataFrame(rows, schema={"ts": pl.Int64, "bid": pl.Float64, "ask": pl.Float64}, orient="row")


def test_clean_bid_ask_keeps_a_well_formed_quote():
    df = _df([(1, 10.0, 10.2)])
    out, q = _clean_bid_ask(df, ["ts"])
    assert out.height == 1
    assert out["mid"][0] == 10.1
    assert out["spread"][0] == pytest.approx(0.2)
    assert q == Quality(rows_read=1, dropped_unpaired=0, dropped_crossed=0, dropped_nonpositive=0)


def test_clean_bid_ask_drops_unpaired_quotes():
    df = _df([(1, 10.0, None), (2, None, 10.0), (3, 10.0, 10.2)])
    out, q = _clean_bid_ask(df, ["ts"])
    assert out.height == 1
    assert q.dropped_unpaired == 2
    assert q.rows_kept == 1


def test_clean_bid_ask_drops_crossed_quotes():
    df = _df([(1, 10.2, 10.0), (2, 10.0, 10.2)])  # first row: bid > ask
    out, q = _clean_bid_ask(df, ["ts"])
    assert out.height == 1
    assert out["ts"][0] == 2
    assert q.dropped_crossed == 1


def test_clean_bid_ask_drops_nonpositive_mid():
    df = _df([(1, 0.0, 0.0), (2, 10.0, 10.2)])  # first row: mid == 0
    out, q = _clean_bid_ask(df, ["ts"])
    assert out.height == 1
    assert q.dropped_nonpositive == 1


def test_clean_bid_ask_empty_input():
    df = pl.DataFrame(schema={"ts": pl.Int64, "bid": pl.Float64, "ask": pl.Float64})
    out, q = _clean_bid_ask(df, ["ts"])
    assert out.height == 0
    assert q.rows_read == 0


def test_quality_rows_kept_matches_manual_arithmetic():
    q = Quality(rows_read=100, dropped_unpaired=5, dropped_crossed=2, dropped_nonpositive=1)
    assert q.rows_kept == 92
