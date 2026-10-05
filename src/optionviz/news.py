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
from datetime import datetime, timedelta
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


#: Scored windows further apart than this split the cache into segments. A
#: weekend is one overnight window, so it never splits one; a backlog drained
#: oldest-first and then newest-first leaves its middle unscored, and does.
SEGMENT_GAP = timedelta(days=4)
#: The sentiment indicator's half-life, and how many of them a segment must
#: cover before a reading is shown — before that, news older than the segment
#: (unscored, not absent) would still carry weight.
HALF_LIFE = timedelta(hours=48)
WARM_HALF_LIVES = 3


def segments(items: list[dict]) -> list[tuple[datetime, datetime]]:
    """Runs of scored windows (`known_at`) with no gap over SEGMENT_GAP. A time
    outside every segment has no news *known*, which is not the same as none."""
    ends = sorted({it["known_at"] for it in items})
    out, start = [], 0
    for i in range(1, len(ends) + 1):
        if i == len(ends) or ends[i] - ends[i - 1] > SEGMENT_GAP:
            out.append((ends[start], ends[i - 1]))
            start = i
    return out


def sentiment(items: list[dict], closes: list[datetime], scored: list[tuple[datetime, datetime]],
              half_life: timedelta = HALF_LIFE) -> list[float | None]:
    """The decayed sentiment at each close (ascending): every headline's
    materiality × sentiment on the symbol (`for_symbol` items), counted from
    when its score became known and halving every `half_life` — so it sums
    tone *and* volume of news. None outside a scored segment, or before it has
    run WARM_HALF_LIVES half-lives. Matches ib-spread-trader's
    `news_scores.sentiment_index`. ponytail: decays in calendar time, so a
    weekend fades news as much as two sessions do."""
    effects = sorted((it["known_at"], it["effect"]["materiality"] * it["effect"]["sentiment"])
                     for it in items)
    warm = WARM_HALF_LIVES * half_life
    out, total, last, j = [], 0.0, None, 0
    for close in closes:
        if last is not None:
            total *= 0.5 ** ((close - last) / half_life)
        while j < len(effects) and effects[j][0] <= close:
            total += effects[j][1] * 0.5 ** ((close - effects[j][0]) / half_life)
            j += 1
        last = close
        out.append(total if any(a + warm <= close <= b for a, b in scored) else None)
    return out
