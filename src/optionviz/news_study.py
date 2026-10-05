"""Read ib-spread-trader's news study (`out/news_study.json`, written by its
`scripts/news_study.py`) for the News Study page.

The study is one JSON document: provenance, then one section per question,
each a list of *cells* — a statistic with its weekly-block 95 % interval and a
verdict the writer has already judged against that section's placebo. The page
renders verdicts; it never re-derives them, so a threshold lives in one place.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT = Path("../ib-spread-trader/out/news_study.json")

#: verdict -> (label, palette slot). A status colour always travels with its label.
VERDICTS = {
    "finding": ("finding", "good"),
    "improves": ("improves", "good"),
    "describes": ("describes the move", "series_1"),
    "hurts": ("hurts", "critical"),
    "chance (search)": ("search luck", "warning"),
    "unguarded": ("unguarded", "warning"),
    "within noise": ("within noise", "muted"),
    "too few": ("too few", "muted"),
    "descriptive": ("descriptive", "muted"),
}


def load(path: str | Path = DEFAULT) -> dict | None:
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else None


def slot(verdict: str) -> str:
    return VERDICTS.get(verdict, (verdict, "muted"))[1]


def label(verdict: str) -> str:
    return VERDICTS.get(verdict, (verdict, "muted"))[0]


def cells(section: dict, **match) -> list[dict]:
    """The section's cells whose fields equal every `match` value."""
    return [c for c in section.get("cells", []) if all(c.get(k) == v for k, v in match.items())]


def interval(c: dict, digits: int = 2) -> str:
    """'+0.12 [+0.02, +0.22]', or the point alone when too thin for one."""
    fmt = f"{{:+.{digits}f}}"
    if c.get("point") is None:
        return "—"
    if c.get("lo") is None:
        return fmt.format(c["point"])
    return f"{fmt.format(c['point'])} [{fmt.format(c['lo'])}, {fmt.format(c['hi'])}]"
