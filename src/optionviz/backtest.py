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
    forecast_stop: float | None = None
    #: What the ranker concluded at entry: expected_pl, score (E[P/L] per unit of
    #: risk) and prob_profit. Empty for a staged run, which makes no forecast.
    prediction: dict | None = None
    #: What the model *saw* on the bar it decided on — trend, VWAP distance, ATR,
    #: realized vol, ATM IV, skew, RSI, days to earnings. Absent readings are
    #: omitted by the writer rather than sent as zero, so `.get` returning None
    #: means "not measured", not "measured at nothing".
    entry_features: dict | None = None
    #: The chain the position traded in. Absent from runs written before the
    #: backtester traded several expiries at once.
    expiry: str | None = None
    #: Open across an earnings release — a binary event's P&L, reported apart.
    held_through_earnings: bool = False
    #: Every leg as (signed quantity, right, strike), from the description.
    legs: tuple = ()

    @property
    def neutral(self) -> bool:
        """Holds calls and puts both — a straddle, strangle, iron condor or
        butterfly: a bet on the size of the move, with no direction."""
        rights = {right for _, right, _ in self.legs}
        return rights == {"C", "P"}

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
            "forecast_stop": "Forecast Stop",
            "pop_floor": "POP Give-up",
            "trailing_stop": "Trailing Stop",
            "momentum_exit": "Momentum Exit",
            "time_decay": "Time Decay",
            "before_earnings": "Before Earnings",
            "after_earnings": "After Earnings",
            "hold_period": "Hold Period",
            "before_expiry": "Before Expiry",
            "expiry_settlement": "Expiry Settlement",
        }.get(self.exit_reason, self.exit_reason)


LEG = re.compile(r'([+-])(\d+)([CP])\s+([\d.]+)')


def parse_legs(description: str) -> tuple[tuple[int, str, float], ...]:
    """Every leg of e.g. 'SPY +1C 756 +1P 718 -1C 749 -1P 721', as
    (signed quantity, right, strike)."""
    return tuple(
        (int(qty) if sign == '+' else -int(qty), right, float(strike))
        for sign, qty, right, strike in LEG.findall(description)
    )


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
    # Every chain the run traded; older runs carry only `expiry`.
    expiries: list[str] | None = None
    # Minutes per option bar — the execution grid. Older runs were all 30.
    bar_minutes: int = 30
    # The block labels the run composed (model, ranker, portfolio, exit, dataset).
    labels: dict | None = None

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
                forecast_stop=t.get("forecast_stop"),
                prediction=t.get("prediction"),
                entry_features=t.get("entry_features"),
                expiry=t.get("expiry"),
                held_through_earnings=t.get("held_through_earnings", False),
                legs=parse_legs(t["description"]),
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
            expiries=data.get("expiries") or [data["expiry"]],
            bar_minutes=data.get("bar_minutes", 30),
            labels=data.get("labels"),
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


def model_drift(model: dict, features: dict) -> tuple[float, float, bool]:
    """The ranked model's horizon log-drift from a trade's entry features —
    `E[S_h] = spot · exp(drift)` — as `(drift, fade, kept)`: `fade` is the
    trend term and `kept` whether `trend_with_prevailing_only` let it through.

    Mirrors `LognormalDriftModel::drift` in ib-spread-trader's
    `src/model/price_model.rs`; an absent reading counts as zero there too."""
    fade = model.get("trend_weight", 0.0) * (features.get("trend") or 0.0)
    prevailing = features.get("prevailing_trend")
    kept = not model.get("trend_with_prevailing_only") or (
        prevailing is not None and prevailing * fade > 0
    )
    drift = (
        (fade if kept else 0.0)
        - model.get("vwap_reversion", 0.0) * (features.get("vwap_distance") or 0.0)
        - model.get("skew_weight", 0.0) * (features.get("skew") or 0.0)
    )
    return drift, fade, kept


def position_marks(results_path: Path | str) -> pl.DataFrame:
    """Per-bar state of each open position, from a run's `positions.csv`.

    Empty when the file is absent — runs written before the backtester recorded
    it, which is every run older than the trailing-stop work. Callers should
    treat an empty frame as "not recorded", not as "nothing happened".
    """
    path = Path(results_path).parent / "positions.csv"
    if not path.exists():
        return pl.DataFrame()
    return pl.read_csv(path).with_columns(
        pl.col("ts").str.to_datetime("%Y-%m-%dT%H:%M:%S%z")
    )


#: `minutes` value asking `underlying_bars` for the captured daily bars.
DAILY = 1440

_BARS_SCHEMA = {
    "ts": pl.Utf8, "open": pl.Float64, "high": pl.Float64,
    "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64,
}


def underlying_bars(catalog: Catalog, symbol: str, minutes: int = 30) -> pl.DataFrame:
    """Stock bars for a symbol across every snapshot, on a run's own grid.

    Built from the 5-minute tape — the one stock series every capture since
    2026-08 carries through its whole window (the 15-minute captures took no
    15- or 30-minute stock bars) — folded to `minutes`: first open, highest
    high, lowest low, last close, summed volume. Without 5-minute bars it falls
    back to bars of `minutes` itself. `minutes=DAILY` reads the captured daily
    bars instead, which start a couple of weeks before the 5-minute tape.

    Every snapshot is read and the newest wins on a shared `ts`: a fresh capture
    is not guaranteed to carry the tape an older snapshot holds.

    `data_type = 'trades'` is the only series where volume means anything: the
    capture writes 0 on every bid/ask row because IB sends -1 into a NOT NULL
    column, so volume read off those would be a silent zero rather than a
    measurement.
    """
    def read(bar_size: str) -> pl.DataFrame:
        frames = [
            db.query(
                snap.path,
                """
                SELECT b.ts, b.open, b.high, b.low, b.close, b.volume
                FROM bars b
                JOIN contracts c ON c.id = b.contract_id
                WHERE c.symbol = ? AND c.kind = 'stock'
                  AND b.bar_size = ? AND b.data_type = 'trades'
                """,
                (symbol, bar_size),
                schema=_BARS_SCHEMA,
            )
            for snap in catalog.snapshots  # newest first
        ]
        return (
            pl.concat(frames, how="vertical_relaxed")
            .unique("ts", keep="first", maintain_order=True)
            .with_columns(pl.col("ts").str.to_datetime("%Y-%m-%dT%H:%M:%S%z"))
            .sort("ts")
        )

    if minutes == DAILY:
        return read("1d")
    five = read("5min")
    if not five.height:
        return read(f"{minutes}min")
    return fold_bars(five, minutes)


def fold_bars(bars: pl.DataFrame, minutes: int) -> pl.DataFrame:
    """Fold a sorted bar frame into `minutes`-wide bars keyed by their start.

    Windows are anchored to the half hour the session opens on: an hourly fold
    aligned to :00 would label the first bar 13:00, inside the overnight break.
    """
    return (
        bars.sort("ts")
        .group_by_dynamic(
            "ts", every=f"{minutes}m", offset=f"{30 % minutes}m", closed="left", label="left"
        )
        .agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
        )
    )
