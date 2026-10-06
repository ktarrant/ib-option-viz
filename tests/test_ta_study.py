"""The TA Study page's reader and the page itself, run against ib-spread-trader's
real study when one is present (skipped otherwise)."""

from pathlib import Path

import pytest

from optionviz import ta_study as ta


def test_twins_pair_each_discovery_cell_with_its_holdout():
    section = dict(cells=[
        dict(signal="a", horizon=5, group="all", period="discovery", point=0.1),
        dict(signal="a", horizon=5, group="all", period="holdout", point=0.0),
        dict(signal="b", horizon=5, group="all", period="discovery", point=0.2),
    ])
    pairs = ta.twins(section, group="all")
    assert [(d["signal"], h and h["point"]) for d, h in pairs] == [("a", 0.0), ("b", None)]


def test_the_page_renders_the_real_study():
    if not Path(ta.DEFAULT).exists():
        pytest.skip("no ta_study.json from ib-spread-trader")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("../app/views/7_TA_Study.py", default_timeout=120).run()
    assert not at.exception, at.exception
    assert any("TA study" in t.value for t in at.title)
