"""Read ib-spread-trader's technical-indicator study (`out/ta_study.json`,
written by its `scripts/ta_study.py`) for the TA Study page.

Same shape as the news study: provenance, then one section per question, each
holding *cells* — a statistic with its block-bootstrap 95 % interval and a
verdict the writer already judged. Verdict labels and colours come from
`news_study`, so the two pages read alike and a threshold lives upstream.
"""

from __future__ import annotations

from pathlib import Path

from .news_study import VERDICTS, cells, interval, label, load as _load, slot  # noqa: F401  (re-exported)

DEFAULT = Path("../ib-spread-trader/out/ta_study.json")


def load(path: str | Path = DEFAULT) -> dict | None:
    return _load(path)


def twins(section: dict, **match) -> list[tuple[dict, dict]]:
    """(discovery, holdout) pairs of the matching cells, in discovery order."""
    held = {}
    for c in cells(section, period="holdout", **match):
        held[key(c)] = c
    return [(c, held.get(key(c))) for c in cells(section, period="discovery", **match)]


def key(c: dict) -> tuple:
    return tuple((k, str(v)) for k, v in sorted(c.items())
                 if k in ("signal", "target", "horizon", "group", "context", "a", "b", "estimator", "model", "index"))
