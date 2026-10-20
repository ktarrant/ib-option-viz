"""Where the app reads from, so one code path serves two deployments.

On the development PC the defaults hold: snapshots and research DBs in
`~/Documents/Options Database` (or `OPTIONVIZ_DB_DIR`), backtests in the
sibling `../ib-spread-trader/out`, and the headline-score cache beside the
snapshots. On the capture server the unit sets:

- `OPTIONVIZ_DB_DIR`: the recent research DB's folder and the published
  results (`~/ib-results/current`), joined with `os.pathsep`;
- `OPTIONVIZ_RESULTS`: `~/ib-results/current`, which holds the frontier
  runs, their bundles and the study reports;
- `OPTIONVIZ_NEWS`: the capture's own `news-llm.sqlite3`.
"""

from __future__ import annotations

import os
from pathlib import Path

RESULTS_ENV = "OPTIONVIZ_RESULTS"
NEWS_ENV = "OPTIONVIZ_NEWS"
DEFAULT_RESULTS = "../ib-spread-trader/out"


def results_dir() -> str:
    """The folder of backtest results the result pages list (and the study
    reports sit in)."""
    return os.environ.get(RESULTS_ENV) or DEFAULT_RESULTS


def study_report(name: str) -> Path:
    """One of ib-spread-trader's study reports (`news_study.json`, ...)."""
    return Path(results_dir()) / name


def news_cache(snapshot_dirs: list[Path], cache_name: str) -> Path:
    """The headline-score cache: `OPTIONVIZ_NEWS`, else the first snapshot
    folder holding one, else beside the newest snapshot (which may not exist)."""
    if os.environ.get(NEWS_ENV):
        return Path(os.environ[NEWS_ENV]).expanduser()
    for directory in snapshot_dirs:
        if (directory / cache_name).exists():
            return directory / cache_name
    return (snapshot_dirs[0] if snapshot_dirs else Path(".")) / cache_name
