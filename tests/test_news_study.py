"""The News Study page's reader, its chart builders, and the page itself run
against ib-spread-trader's real study when one is present (skipped otherwise)."""

from pathlib import Path

import pytest

from optionviz import news_study as ns
from optionviz.ui import charts


def test_a_missing_study_reads_as_none(tmp_path):
    assert ns.load(tmp_path / "nope.json") is None


def test_an_interval_is_shown_only_when_the_writer_had_one():
    assert ns.interval(dict(point=0.123, lo=0.02, hi=0.22)) == "+0.12 [+0.02, +0.22]"
    assert ns.interval(dict(point=0.123, lo=None, hi=None)) == "+0.12"  # too few: a point, not a fake interval
    assert ns.interval(dict(point=None)) == "—"


def test_verdicts_keep_their_label_and_status_colour_together():
    assert ns.label("chance (search)") == "search luck" and ns.slot("chance (search)") == "warning"
    assert ns.slot("finding") == "good" and ns.slot("hurts") == "critical"
    assert ns.slot("within noise") == "muted"
    assert ns.label("something new") == "something new"  # an unknown verdict still renders


def test_a_forest_draws_one_trace_per_verdict_and_no_bar_for_a_point_alone():
    cells = [dict(point=0.1, lo=-0.1, hi=0.3, n=40, weeks=6, verdict="within noise"),
             dict(point=0.5, lo=None, hi=None, n=5, weeks=2, verdict="too few"),
             dict(point=0.4, lo=0.2, hi=0.6, n=50, weeks=7, verdict="finding")]
    fig = charts.forest(["a", "b", "c"], cells, "t", 0.0, "x")
    assert sorted(t.name for t in fig.data) == ["finding", "too few", "within noise"]
    too_few = next(t for t in fig.data if t.name == "too few")
    assert list(too_few.error_x.array) == [0] and list(too_few.error_x.arrayminus) == [0]


def test_cells_filter_on_every_field_given():
    section = dict(cells=[dict(axis="all", target="pre"), dict(axis="type", target="pre"), dict(axis="all", target="d1")])
    assert ns.cells(section, axis="all", target="pre") == [dict(axis="all", target="pre")]


def test_the_page_renders_the_real_study():
    if not Path(ns.DEFAULT).exists():
        pytest.skip("no news_study.json from ib-spread-trader")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("../app/views/6_News_Study.py", default_timeout=60).run()
    assert not at.exception, at.exception
    assert any("News study" in t.value for t in at.title)
