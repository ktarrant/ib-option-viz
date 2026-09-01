"""Load and process backtest results from ib-spread-trader.

Reads the JSON output written by `cargo run -- backtest --out <dir>` and queries
the snapshot for underlying price bars to overlay on trade charts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import polars as pl

from . import db
from .catalog import Catalog


@dataclass(frozen=True)
class Trade:
    id: int
    symbol: str
    description: str
    opened_at: datetime
    closed_at: datetime
    bars_held: int
    entry_debit: float
    exit_credit: float
    commission: float
    pnl: float
    exit_reason: str
    long_strike: float
    short_strike: float
    synthetic_price: bool
    forecast_target: float | None = None

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    @property
    def exit_label(self) -> str:
        # Keys match the Rust report's reason_key().
        return {
            "profit_target": "Profit Target",
            "stop_loss": "Stop Loss",
            "forecast_target": "Forecast Target",
            "underlying_past_short": "Past Short Strike",
            "forecast_horizon": "Forecast Horizon",
            "time_decay": "Time Decay",
            "before_earnings": "Before Earnings",
            "before_expiry": "Before Expiry",
            "expiry_settlement": "Expiry Settlement",
        }.get(self.exit_reason, self.exit_reason)


def _parse_strikes(description: str) -> tuple[float, float]:
    """Extract (long_strike, short_strike) from e.g. 'AAPL +1C 300 -1C 320'."""
    parts = re.findall(r'([+-])(\d+)([CP])\s+([\d.]+)', description)
    long = short = 0.0
    for sign, _qty, _right, strike in parts:
        if sign == '+':
            long = float(strike)
        else:
            short = float(strike)
    return long, short


@dataclass
class BacktestResult:
    strategy: str
    expiry: str
    totals: dict
    symbols: list[dict]
    trades: list[Trade]
    caveats: list[str]
    config: dict
    # Forecast-quality metrics for a ranked run; None for a staged one.
    model_eval: dict | None = None
    # Scorer-discrimination metrics; None unless the run priced candidates forward.
    scorer_eval: dict | None = None

    @classmethod
    def load(cls, path: str | Path) -> "BacktestResult":
        path = Path(path)
        with open(path) as f:
            data = json.load(f)

        trades = []
        for t in data.get("trades", []):
            long_strike, short_strike = _parse_strikes(t["description"])
            trades.append(Trade(
                id=t["id"],
                symbol=t["symbol"],
                description=t["description"],
                opened_at=datetime.fromisoformat(t["opened_at"]),
                closed_at=datetime.fromisoformat(t["closed_at"]),
                bars_held=t["bars_held"],
                entry_debit=t["entry_debit"],
                exit_credit=t["exit_credit"],
                commission=t["commission"],
                pnl=t["pnl"],
                exit_reason=t["exit_reason"],
                long_strike=long_strike,
                short_strike=short_strike,
                synthetic_price=t.get("synthetic_price", False),
                forecast_target=t.get("forecast_target"),
            ))

        return cls(
            strategy=data["strategy"],
            expiry=data["expiry"],
            totals=data["totals"],
            symbols=data["symbols"],
            trades=trades,
            caveats=data.get("caveats", []),
            config=data.get("config", {}),
            model_eval=data.get("model_eval"),
            scorer_eval=data.get("scorer_eval"),
        )


def forecasts_frame(backtest_json: str | Path) -> pl.DataFrame | None:
    """Load the per-bar forecast log written beside a ranked `backtest.json`.
    `None` when the run wrote none (a staged run, or an older ranked run)."""
    path = Path(str(backtest_json).replace("backtest.json", "forecasts.csv"))
    if not path.exists():
        return None
    return pl.read_csv(path).with_columns(
        pl.col("ts").str.to_datetime("%Y-%m-%dT%H:%M:%S%z")
    )


def underlying_bars(catalog: Catalog, symbol: str) -> pl.DataFrame:
    """30-minute stock bars for a symbol from the newest snapshot."""
    snap = catalog.snapshots[0]
    return db.query(
        snap.path,
        """
        SELECT b.ts, b.open, b.high, b.low, b.close
        FROM bars b
        JOIN contracts c ON c.id = b.contract_id
        WHERE c.symbol = ? AND c.kind = 'stock'
          AND b.bar_size = '30min' AND b.data_type = 'trades'
        ORDER BY b.ts
        """,
        (symbol,),
    ).with_columns(pl.col("ts").str.to_datetime("%Y-%m-%dT%H:%M:%S%z"))
