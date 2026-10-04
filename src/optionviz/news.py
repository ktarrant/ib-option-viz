"""LLM-scored headlines, as ib-option-calc's `news-score` caches them.

The capture server scores each day's headlines after its sync and keeps the
cache beside its database; ib-spread-trader's `scripts/fetch_news_cache.sh`
copies it here as `news-llm.sqlite3`, beside the snapshots — never part of one. Its
`calls` table holds one row per (decision window, chunk, model, prompt
version): `items_json` lists the headlines the call scored (index = the id the
model answered with) and `raw_json` the model's answer, shaped by ib-option-calc's
`src/events/news_schema.json`. Removing or renaming a field there breaks this.

**No lookahead.** A score exists only once its window has closed:
`known_at` is the window end, which can be up to a night after the headline.
The chart places a marker at the headline's own time, as TradingView does, and
the card says when the score became known.

Only the newest prompt version is read (the one most recently written), so a
re-score never shows beside the run it replaces.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import db

CACHE_NAME = "news-llm.sqlite3"


def load(path: str | Path) -> list[dict]:
    """Every scored headline in the newest prompt version, oldest first, each
    with `tone`: the model's read of the overall tone around each symbol named
    in that window (chunks of one window averaged)."""
    path = Path(path)
    if not path.exists():
        return []
    with db.connect(path) as conn:
        latest = conn.execute(
            "SELECT model, prompt_version FROM calls ORDER BY created_at DESC LIMIT 1").fetchone()
        if latest is None:
            return []
        rows = conn.execute(
            "SELECT window_end, items_json, raw_json FROM calls WHERE model = ? AND prompt_version = ?",
            latest).fetchall()
    out, tone = [], {}
    for end, items_json, raw_json in rows:
        items, raw = json.loads(items_json), json.loads(raw_json)
        for t in raw.get("symbols", []):
            tone.setdefault(end, {}).setdefault(t["symbol"], []).append(t["sentiment"])
        for s in raw.get("items", []):
            try:
                it = items[int(s["id"])]
            except (ValueError, IndexError, KeyError):
                continue  # an id the model invented
            out.append({
                "at": datetime.fromisoformat(it["at"]), "known_at": datetime.fromisoformat(end),
                "title": it["title"], "symbols": it["symbols"], "providers": it["providers"],
                "type": s["event_type"], "repeat": s["is_repeat"], "rationale": s.get("rationale", ""),
                "effects": s["effects"], "future": s["future_events"], "_end": end,
            })
    for it in out:
        it["tone"] = {sym: sum(v) / len(v) for sym, v in tone.get(it.pop("_end"), {}).items()}
    out.sort(key=lambda it: it["at"])
    return out


def for_symbol(items: list[dict], symbol: str) -> list[dict]:
    """The headlines that bear on `symbol`: an effect on it of materiality 1+,
    whether tagged with it or read through from another symbol's news
    (`cross`). Repeats of a story already told are left out."""
    out = []
    for it in items:
        eff = next((e for e in it["effects"] if e["symbol"] == symbol), None)
        if eff is None or eff["materiality"] < 1 or it["repeat"]:
            continue
        out.append({**it, "effect": eff, "cross": symbol not in it["symbols"]})
    return out
