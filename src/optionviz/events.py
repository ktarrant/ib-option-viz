"""Finding the moments worth looking at.

An event is a bar-to-bar boundary where the forward moved far more than that
symbol normally moves over a boundary of the same kind. Normalisation is the
whole point: a raw "gap > 3%" rule fires fifteen times on AMD and MSTR in two
months while a genuine SPY event never clears it.

**Why the comparison is empirical, not model-based.** The obvious normaliser is
a diffusive expectation, `em = atm_iv * sqrt(dt)`. Tried on this data it ranks
badly, because `dt` in calendar time is wrong twice over: a 30-minute bar spans
half an hour of the 6.5-hour trading day, not half an hour of a 24-hour one, so
intraday moves score enormous; and an overnight gap carries information flow no
calendar fraction describes. Rather than hand-tune a trading-time clock, each
boundary is scored against **that symbol's own distribution of same-kind
boundaries**, via a median/MAD robust z:

    surprise = (|ret| - median|ret|) / (1.4826 * MAD|ret|)

That is model-free and self-calibrating. MSTR's wide distribution raises its own
bar; SPY's narrow one means a 1% gap stands out, as it should.

The same treatment applies to the vol collapse (`crush_z`). Earnings read as a
large `surprise` on an overnight boundary together with a sharply negative
`crush_z` — the event premium priced across every strike vanishing once the news
is out. A macro shock moves the forward with vol flat or rising instead.

Who *won* the event is a separate question and deliberately not answered here:
`scoring.py` prices the actual straddle across the boundary, with no model at
all. Ranking asks "is this interesting"; scoring asks "did buyers or sellers
come out ahead".

Sources are pluggable. `DerivedEventSource` finds candidates from the surface
alone; `LabelledEventSource` reads the capture's `events` table (schema v6,
currently SEC EDGAR 8-K earnings releases) and is authoritative where the
detector is a heuristic — AMD's and MSTR's real 2026-07/08 earnings prints do
not clear the crush threshold (their implied vol barely moves around the
release), so the detector alone misses them. `combine()` lets a labelled row
win over a derived one at the same symbol/day regardless of score.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import numpy as np
import polars as pl

from . import frames
from .cache import Chain
from .catalog import Catalog

YEAR_SECONDS = 365.25 * 24 * 3600
MAD_TO_SIGMA = 1.4826

# Detection thresholds, in robust sigmas of the symbol's own history.
MIN_ABS_RET = 0.004      # below this a move is quote noise regardless of z
MIN_PAIRS = 4            # forward needs a real ladder behind it
MIN_HISTORY = 30         # boundaries needed before a distribution means anything
SURPRISE_MIN = 3.0       # ranking floor
EARNINGS_SURPRISE = 3.0
EARNINGS_CRUSH_Z = -2.0
# One night delivering most of what the option's whole remaining life was priced
# for. This is what separates a company print from an index reacting to a macro
# release: on the real snapshot the six genuine earnings run 0.86-1.62 while
# SPY's post-FOMC crush sits at 0.40. Neither the cohort count nor the IV rank
# separates them - both were tried and both failed.
EARNINGS_VS_IMPLIED = 0.6
IV_RANK_WINDOW = 65        # ~5 trading days of 30-minute bars, for context only
MACRO_MIN_SYMBOLS = 3      # a boundary this widely shared is the market, not a company
CRUSH_WEIGHT = 1.5       # how much a vol collapse lifts an event up the ranking

SCHEMA = {
    "symbol": pl.Utf8, "expiry": pl.Utf8,
    "ts_before": pl.Datetime("us", "UTC"), "ts_after": pl.Datetime("us", "UTC"),
    "boundary": pl.Utf8, "kind": pl.Utf8, "label": pl.Utf8, "source": pl.Utf8,
    "forward_before": pl.Float64, "forward_after": pl.Float64, "ret": pl.Float64,
    "spot_before": pl.Float64, "spot_after": pl.Float64, "spot_ret": pl.Float64,
    "surprise": pl.Float64, "crush_z": pl.Float64, "iv_rank_before": pl.Float64,
    "atm_iv_before": pl.Float64, "atm_iv_after": pl.Float64, "crush": pl.Float64,
    "expected_move": pl.Float64, "move_vs_implied": pl.Float64, "n_cohort": pl.UInt32,
    "atm_strike": pl.Float64, "score": pl.Float64,
}


@runtime_checkable
class EventSource(Protocol):
    def events(self, chains: dict[tuple[str, str], Chain]) -> pl.DataFrame: ...


def robust_z(x: np.ndarray) -> np.ndarray:
    """Median/MAD z-score. Immune to the very outliers we are hunting for."""
    finite = x[np.isfinite(x)]
    if finite.size < MIN_HISTORY:
        return np.full(x.shape, np.nan)
    med = np.median(finite)
    mad = np.median(np.abs(finite - med))
    if mad > 0:
        return (x - med) / (MAD_TO_SIGMA * mad)

    # MAD collapses to zero when more than half the series is identical — a
    # pegged quote, or a vol that only ever moves at one boundary. Standard
    # deviation still has something to say there; a genuinely constant series
    # has no deviation to score.
    sd = float(np.std(finite))
    if sd <= 0:
        return np.zeros_like(x)
    return (x - med) / sd


def trailing_rank(x: np.ndarray, window: int = IV_RANK_WINDOW) -> np.ndarray:
    """Fraction of the trailing `window` values that the current value exceeds.

    Reported as context for the detail view — "IV went in at its 98th
    percentile" — not used to classify. It was tried as an earnings criterion
    and rejected: COIN's real 07-31 print went in at rank 0.15 while SPY's
    macro-driven false positive went in at 1.00, so it separates nothing.
    """
    out = np.full(x.shape, np.nan)
    for i in range(x.size):
        prior = x[max(0, i - window):i]
        prior = prior[np.isfinite(prior)]
        if prior.size >= 10 and np.isfinite(x[i]):
            out[i] = float((prior <= x[i]).mean())
    return out


class DerivedEventSource:
    """Detects events from the reconstructed surface alone. No network, no calendar."""

    name = "derived"

    def __init__(self, min_surprise: float = SURPRISE_MIN):
        self.min_surprise = min_surprise

    def events(self, chains: dict[tuple[str, str], Chain]) -> pl.DataFrame:
        out = [self._for_chain(c) for c in chains.values()]
        out = [f for f in out if f.height]
        if not out:
            return pl.DataFrame(schema=SCHEMA)
        return self._label_macro(pl.concat(out, how="vertical")).sort("score", descending=True)

    @staticmethod
    def _label_macro(events: pl.DataFrame) -> pl.DataFrame:
        """Relabel widely-shared boundaries as macro.

        A move that lands on the same bar across most of the cohort is the
        market repricing, not one company's news — an FOMC statement rather than
        an earnings release. Names already classified `earnings` keep that label:
        several companies reporting the same night is a calendar cluster, not a
        macro event, and their vol crush is what tells them apart.
        """
        if not events.height:
            return events
        counts = events.group_by("ts_after").agg(
            pl.col("symbol").n_unique().cast(pl.UInt32).alias("n_cohort")
        )
        return (
            events.drop("n_cohort", strict=False)
                  .join(counts, on="ts_after", how="left")
                  .with_columns(
                      pl.when((pl.col("n_cohort") >= MACRO_MIN_SYMBOLS) & (pl.col("kind") != "earnings"))
                        .then(pl.lit("macro"))
                        .otherwise(pl.col("kind")).alias("kind")
                  )
                  # The join re-appends n_cohort at the end regardless of its
                  # SCHEMA position; pin the order back so this frame concats
                  # cleanly with any other EventSource's output in combine().
                  .select(list(SCHEMA.keys()))
        )

    def boundaries(self, chain: Chain) -> pl.DataFrame:
        """Every bar-to-bar boundary with its normalised scores. Unfiltered."""
        atm = chain.atm
        if atm.height < 2:
            return pl.DataFrame(schema=SCHEMA)

        a = atm.sort("ts").filter(
            pl.col("forward").is_not_null()
            & pl.col("atm_iv").is_not_null()
            & (pl.col("n_pairs") >= MIN_PAIRS)
        )
        if a.height < 2:
            return pl.DataFrame(schema=SCHEMA)

        has_spot = "spot_mid" in a.columns
        a = a.with_columns(
            pl.Series("iv_rank", trailing_rank(a["atm_iv"].to_numpy()))
        )
        shift_cols = [
            pl.col("ts").shift(1).alias("ts_before"),
            pl.col("iv_rank").shift(1).alias("iv_rank_before"),
            pl.col("forward").shift(1).alias("forward_before"),
            pl.col("atm_iv").shift(1).alias("atm_iv_before"),
            pl.col("expected_move").shift(1).alias("expected_move"),
            pl.col("atm_strike").shift(1).alias("atm_strike"),
        ]
        rename_cols = {"ts": "ts_after", "forward": "forward_after", "atm_iv": "atm_iv_after"}
        if has_spot:
            shift_cols.append(pl.col("spot_mid").shift(1).alias("spot_before"))
            rename_cols["spot_mid"] = "spot_after"
        b = a.with_columns(*shift_cols).drop_nulls(["ts_before"]).rename(rename_cols)
        if not has_spot:
            b = b.with_columns(pl.lit(None, pl.Float64).alias("spot_before"),
                                pl.lit(None, pl.Float64).alias("spot_after"))

        f0, f1 = b["forward_before"].to_numpy(), b["forward_after"].to_numpy()
        iv0, iv1 = b["atm_iv_before"].to_numpy(), b["atm_iv_after"].to_numpy()
        s0, s1 = b["spot_before"].to_numpy(), b["spot_after"].to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            # Detection stays on the forward: switching it now would mean
            # re-tuning every threshold above against a different series, for
            # no real gain (surprise/crush are relative measures; the ranking
            # they produce barely moves). spot_ret is carried alongside purely
            # for display - a real "AMZN moved 13.3%" beats a forward number,
            # and the two can genuinely diverge right at an event: AMD's
            # parity fit gets visibly noisier in the bar the print lands in
            # (its parity_mad roughly triples), because a handful of strikes
            # quote wide or stale for a moment while market makers reprice.
            # Spot, one instrument instead of thirty reconciled together, isn't
            # exposed to that wobble - which is exactly why it is the more
            # honest number right where it matters most.
            ret = np.log(f1 / f0)
            spot_ret = np.log(s1 / s0)
            crush = np.where(iv0 > 0, (iv1 - iv0) / iv0, np.nan)

        overnight = b["ts_after"].dt.date().to_numpy() != b["ts_before"].dt.date().to_numpy()
        b = b.with_columns(
            pl.Series("ret", ret), pl.Series("spot_ret", spot_ret), pl.Series("crush", crush),
            pl.Series("boundary", np.where(overnight, "overnight", "intraday")),
            pl.lit(chain.symbol).alias("symbol"), pl.lit(chain.expiry).alias("expiry"),
            pl.lit(self.name).alias("source"),
        )

        # Normalise within boundary kind: overnight and intraday are different regimes.
        parts = []
        for (kind,), grp in b.group_by(["boundary"], maintain_order=True):
            r = grp["ret"].to_numpy()
            parts.append(
                grp.with_columns(
                    pl.Series("surprise", robust_z(np.abs(r))),
                    pl.Series("crush_z", robust_z(grp["crush"].to_numpy())),
                )
            )
        b = pl.concat(parts, how="vertical") if parts else b

        b = b.with_columns(
            (pl.col("ret").abs() / pl.col("expected_move")).alias("move_vs_implied")
        )
        kinds, scores = [], []
        for r in b.iter_rows(named=True):
            s, cz, rank = r["surprise"], r["crush_z"], r["iv_rank_before"]
            s = s if s is not None and np.isfinite(s) else 0.0
            cz = cz if cz is not None and np.isfinite(cz) else 0.0
            rank = rank if rank is not None and np.isfinite(rank) else 0.0
            vs_impl = r["move_vs_implied"]
            vs_impl = vs_impl if vs_impl is not None and np.isfinite(vs_impl) else 0.0
            if (r["boundary"] == "overnight" and s >= EARNINGS_SURPRISE
                    and cz <= EARNINGS_CRUSH_Z and vs_impl >= EARNINGS_VS_IMPLIED):
                kinds.append("earnings")
            elif s >= SURPRISE_MIN:
                kinds.append("shock")
            else:
                kinds.append("move")
            scores.append(s + CRUSH_WEIGHT * max(0.0, -cz))

        return b.with_columns(
            pl.Series("kind", kinds), pl.Series("score", scores),
            pl.lit(None, pl.Utf8).alias("label"),
            pl.lit(None, pl.UInt32).alias("n_cohort"),
        ).select(list(SCHEMA.keys())).sort("ts_after")

    def _for_chain(self, chain: Chain) -> pl.DataFrame:
        b = self.boundaries(chain)
        if not b.height:
            return b
        # NaN survives both is_not_null() and a >= comparison in polars, and a
        # symbol without MIN_HISTORY same-kind boundaries scores NaN by design.
        # Guard it explicitly rather than relying on comparison semantics.
        out = b.filter(
            (pl.col("ret").abs() >= MIN_ABS_RET)
            & pl.col("surprise").is_not_null()
            & pl.col("surprise").is_not_nan()
            & (pl.col("surprise") >= self.min_surprise)
        )
        if not out.height:
            return pl.DataFrame(schema=SCHEMA)
        # One event per symbol-day: keep the boundary that scored highest.
        return (
            out.with_columns(pl.col("ts_after").dt.date().alias("_d"))
               .sort("score", descending=True)
               .unique(subset=["symbol", "expiry", "_d"], keep="first")
               .drop("_d")
               .sort("score", descending=True)
        )


# A calendar's earnings history runs back decades; only a handful of entries
# fall inside any given capture window. Without a proximity cap, an ancient
# event_date matches "any boundary after it" - which, for a 2004 date, is the
# very first bar of the whole chain. A long weekend plus a holiday is the
# widest realistic gap between a release and the next traded session.
MAX_REACTION_GAP_DAYS = 4


def match_boundary(boundaries: pl.DataFrame, event_date: date, session_timing: str | None) -> dict | None:
    """Find the bar-to-bar boundary a calendar release actually lands on.

    The `events` table gives a date and whether the release was before the
    open or after the close; the surface only has whatever bars the capture
    happened to pull. Rather than assume a fixed grid and risk a holiday or a
    gap landing on the wrong bar, this asks the data directly:

    - `before_open` on day D: the reaction is priced into the first bar the
      capture has *on* D, so the target is `min(ts_after)` where the date is D.
    - `after_close` on day D (or anything else - the conservative default):
      the reaction is priced into the first bar of the *next* trading day the
      capture has, so the target is `min(ts_after)` where the date is after D.

    Either way the paired `ts_before` is whatever `boundaries()` already
    computed as the immediately preceding bar - correct even across a gap,
    since that pairing never assumed a fixed grid either.
    """
    if not boundaries.height:
        return None
    dates = boundaries["ts_after"].dt.date()
    if session_timing == "before_open":
        candidates = boundaries.filter(dates == event_date)
        if not candidates.height:
            candidates = boundaries.filter(dates > event_date)
    else:
        candidates = boundaries.filter(dates > event_date)
    if not candidates.height:
        return None
    row = candidates.sort("ts_after").row(0, named=True)
    if (row["ts_after"].date() - event_date).days > MAX_REACTION_GAP_DAYS:
        return None  # nearest boundary is a different, unrelated capture window
    return row


class LabelledEventSource:
    """Confirmed events from the capture's `events` table.

    Matched onto the same per-boundary metrics `DerivedEventSource` computes
    (surprise, crush, the straddle's later verdict all read the same fields
    regardless of source), so a confirmed release and a detected one render
    identically in the UI - only `kind`, `label` and `source` differ. `kind` is
    passed through from the table rather than hardcoded to "earnings", so a
    future capture that starts tagging guidance updates or splits needs no
    change here.
    """

    name = "labelled"

    def __init__(self, catalog: Catalog):
        self._catalog = catalog
        self._boundaries = DerivedEventSource()

    def events(self, chains: dict[tuple[str, str], Chain]) -> pl.DataFrame:
        rows = []
        for (symbol, expiry), chain in chains.items():
            path = self._catalog.resolve(symbol, expiry)
            if path is None:
                continue
            calendar = frames.events_for_symbol(path, symbol)
            if not calendar.height:
                continue
            boundaries = self._boundaries.boundaries(chain)
            if not boundaries.height:
                continue
            for r in calendar.iter_rows(named=True):
                matched = match_boundary(boundaries, r["event_date"], r["session_timing"])
                if matched is None:
                    continue
                row = dict(matched)
                row["kind"] = r["kind"]
                row["label"] = r["title"]
                row["source"] = self.name
                rows.append(row)
        if not rows:
            return pl.DataFrame(schema=SCHEMA)
        out = pl.DataFrame(rows, schema=SCHEMA)
        # Two calendar rows should never land on the same boundary, but a
        # source_id typo or a re-filed 8-K could - keep the higher-score match.
        return (
            out.with_columns(pl.col("ts_after").dt.date().alias("_d"))
               .sort("score", descending=True)
               .unique(subset=["symbol", "expiry", "_d"], keep="first")
               .drop("_d")
               .sort("score", descending=True)
        )


def combine(*sources_results: pl.DataFrame) -> pl.DataFrame:
    """Merge event frames, letting labelled sources win over detected ones.

    Ordering matters: earlier frames take precedence at the same symbol/day.
    """
    frames_in = [f.select(list(SCHEMA.keys())) for f in sources_results if f is not None and f.height]
    if not frames_in:
        return pl.DataFrame(schema=SCHEMA)
    return (
        pl.concat(frames_in, how="vertical")
          .with_columns(pl.col("ts_after").dt.date().alias("_d"))
          .unique(subset=["symbol", "expiry", "_d"], keep="first")
          .drop("_d")
          .sort("score", descending=True)
    )
