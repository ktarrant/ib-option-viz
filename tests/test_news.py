"""The news cache reader and where a headline lands on the chart. No snapshot needed."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from optionviz import news
from optionviz.ui import tv_chart

UTC = timezone.utc


def _cache(path, version, created, answer_ids):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS calls (window_end TEXT, chunk INTEGER, model TEXT,
        prompt_version TEXT, effort TEXT, items_json TEXT, prompt TEXT, raw_json TEXT, seconds REAL, created_at TEXT)""")
    items = [{"key": "a1", "at": "2026-09-14T22:00:00+00:00", "title": "AMD wins order",
              "symbols": ["AMD"], "providers": ["DJ-N"]}]
    effect = {"symbol": "NVDA", "materiality": 2, "direction": -0.2, "sentiment": -0.1,
              "vol": "elevated", "horizon": "days"}
    raw = {"items": [{"id": i, "event_type": "product", "is_repeat": False, "effects": [effect],
                      "future_events": [], "rationale": "r"} for i in answer_ids],
           "symbols": [{"symbol": "NVDA", "sentiment": -0.3}]}
    conn.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("2026-09-15T13:30:00+00:00", 0, "m", version, "low", json.dumps(items), "",
                  json.dumps(raw), 1.0, created))
    conn.commit()
    conn.close()


def test_the_newest_prompt_version_is_read_and_invented_ids_are_dropped(tmp_path):
    path = tmp_path / news.CACHE_NAME
    _cache(path, "v1", "2026-10-01T00:00:00", ["0"])
    _cache(path, "v2", "2026-10-02T00:00:00", ["0", "7", "x"])
    items = news.load(path)
    assert len(items) == 1  # v2 only; ids 7 and x match no headline
    assert items[0]["tone"] == {"NVDA": -0.3}
    nvda = news.for_symbol(items, "NVDA")
    assert nvda[0]["cross"] and nvda[0]["effect"]["vol"] == "elevated"
    assert news.for_symbol(items, "AMD") == []  # no effect on AMD of materiality 1+


def test_a_headline_between_sessions_lands_on_the_next_bar():
    day = datetime(2026, 9, 14, 13, 30, tzinfo=UTC)
    ts = [day + timedelta(minutes=30 * k) for k in range(13)] + \
         [day + timedelta(days=1, minutes=30 * k) for k in range(13)]
    item = {"at": datetime(2026, 9, 14, 22, 0, tzinfo=UTC), "known_at": datetime(2026, 9, 15, 13, 30, tzinfo=UTC),
            "title": "t", "type": "product", "cross": False, "symbols": ["AMD"], "providers": [],
            "rationale": "", "effect": {"materiality": 1, "direction": 0, "sentiment": 0, "vol": "none"},
            "effects": [], "future": [], "tone": {}}
    during = dict(item, at=day + timedelta(minutes=45))
    groups = tv_chart.news_payload([item, during], ts, timedelta(minutes=30))
    assert [g["i"] for g in groups] == [1, 13]  # inside bar 1; overnight -> the next open


def test_sentiment_decays_from_when_a_score_is_known_and_is_blank_outside_scored_news():
    t0 = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    day = timedelta(days=1)

    def item(known, m, s):
        return {"known_at": known, "effect": {"materiality": m, "sentiment": s}}

    # Scored every day for ten days, then a month unscored, then two more days.
    scored = [item(t0 + k * day, 1, 0.0) for k in range(10)] + [item(t0 + 40 * day, 1, 0.0),
                                                                 item(t0 + 41 * day, 1, 0.0)]
    segs = news.segments(scored)
    assert segs == [(t0, t0 + 9 * day), (t0 + 40 * day, t0 + 41 * day)]
    mine = [item(t0 + 6 * day, 2, 0.5), item(t0 + 8 * day, 1, -1.0)]
    half = timedelta(days=2)  # warm from t0 + 6 days
    closes = [t0 + 5 * day, t0 + 8 * day - timedelta(minutes=1), t0 + 8 * day, t0 + 9 * day, t0 + 20 * day]
    v = news.sentiment(mine, closes, segs, half)
    assert v[0] is None  # not yet warm
    assert abs(v[1] - 2 * 0.5 * 0.5 ** ((2 * day - timedelta(minutes=1)) / half)) < 1e-12  # not yet known: -1 absent
    assert abs(v[2] - (2 * 0.5 * 0.5 - 1.0)) < 1e-12
    assert abs(v[3] - (0.5 * 0.5 ** 0.5 - 1.0 * 0.5 ** 0.5)) < 1e-12
    assert v[4] is None  # inside the unscored month: unknown, not zero
