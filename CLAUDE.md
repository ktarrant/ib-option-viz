# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

The read side for [`ib-option-calc`](../ib-option-calc), which captures IB
options history into SQLite and drops `VACUUM INTO` snapshots into
`~/Documents/Options Database`. This project never writes to a snapshot — for
expired options the data is irreplaceable, so `db.connect()` opens
`file:...?mode=ro` and there is no code path that does otherwise.

Read the capture repo's `CLAUDE.md` and the snapshot's `SCHEMA.md` before
touching anything that reads the database.

## Commands

```sh
uv sync
uv run streamlit run app/Home.py
uv sync --extra dev                 # pytest, kaleido - not installed by plain `uv sync`
uv run pytest                       # 73 tests
uv run pytest tests/test_pricing.py
```

Golden tests in `tests/test_golden.py` run against the real snapshot and skip
cleanly when none is present. They pin values confirmed by hand — SPY's forward
at a known bar, PLTR's earnings crush, the straddle verdict — so a refactor that
quietly changes the maths fails loudly.

## What is derived, and why it has to be

Schema v6 added real 30-minute stock bars, so the underlying is no longer
purely reconstructed — but the reconstruction stayed, and turned out to still
matter. `cache.build_chain` joins real spot onto `atm` as `spot_mid` /
`spot_bid` / `spot_ask` (left join on `ts`; coverage is strong, ~93% on SPY, not
total — the stock tape is denser than the option bars it's joined onto, and
null on any snapshot older than v6).

| Quantity | Where it comes from | Still needed because |
|---|---|---|
| Spot price | Real, schema v6 `bars` (`kind='stock'`, `30min`) | — |
| Forward price | Put-call parity across the strike ladder | Black-76 needs a *forward*, and this one is rate/dividend-free by construction — spot alone is not |
| Implied vol | Black-76 on the parity forward | IB has no historical greeks, ever |
| Delta | Falls out once vol is known | same |

**Prefer spot, keep forward.** `scoring.Straddle.realized_move` and the price
chart (`ui/charts.price_series`) use real spot when both boundary endpoints
have it, falling back to the forward otherwise. Detection (`events.boundaries`'s
`ret`/`surprise`/`crush`) deliberately stays on the forward — switching would
mean re-tuning every threshold below against a different series for no real
gain, since the two are within basis points bar-to-bar. `spot_ret` is carried
alongside purely for display. The one place they visibly diverge is *right at*
an event: AMD's `parity_mad` roughly triples in the bar its earnings print
lands in, because a handful of strikes quote wide or stale for a moment while
market makers reprice. Spot, one instrument instead of thirty reconciled
together, isn't exposed to that wobble — which is exactly why it's the more
honest number where it matters most.

`pricing.parity_fit` uses the **median** of `K + C - P`, not a least-squares fit.
A regression would recover the forward and the discount factor together from
slope and intercept, and it was tried: on the real snapshot 93% of timestamps
return DF > 1, which is unphysical, and the residual is worse than the median's
(MAD 0.065 vs 0.027 on SPY). These are American options and the early-exercise
premium bends the parity line far more than the ~0.1% of carry the slope is
trying to measure. **Do not reintroduce the regression** without checking those
numbers again.

Consequently `discount_factor` is always 1.0. It stays in the signature so a
European-style chain could supply a real one.

## Event detection is empirically normalised

`events.py` scores each boundary against that symbol's own distribution of
same-kind boundaries (median/MAD robust z), not against a diffusive expectation.
`em = atm_iv * sqrt(dt)` was tried and ranks badly: `dt` in calendar time is
wrong for a 30-minute bar inside a 6.5-hour session, so intraday moves score
enormous and drown everything else.

Two criteria were tried for earnings and rejected — both are documented in the
module, and both look plausible until you check them:

- **IV rank going in.** COIN's real print went in at rank 0.15; SPY's macro
  false positive went in at 1.00.
- **Cohort sharing.** Three companies reporting the same night is a calendar
  cluster, not a macro event.

What works is `move_vs_implied`: the six real earnings run 0.86–1.62, SPY's
post-FOMC crush sits at 0.40.

## LabelledEventSource: confirmed earnings from schema v6's `events` table

`events.py`'s opening design note said "when the capture side grows an `events`
table, a second source drops in beside it." It did, and it does:
`LabelledEventSource` reads SEC EDGAR 8-K rows and matches each in-window
release onto the same per-boundary metrics `DerivedEventSource.boundaries()`
already computes, via `match_boundary`. `combine()` (labelled passed first)
lets it win at the same symbol/day regardless of score, and it bypasses the
Events page's surprise-threshold slider entirely — a confirmed release is a
fact, not a statistical crossing.

This is not redundant with the detector. **AMD and MSTR really reported
in-window and the crush-based detector misses both** — AMD's ATM IV sits so
high (~80%+) that an earnings-sized move barely registers as a *relative*
crush, and MSTR's vol is dominated by BTC-linked factors the print doesn't
resolve. The calendar doesn't need vol to move to know a release happened;
that's the whole reason to keep both sources rather than retire the detector.

**`match_boundary` needs a proximity guard, and this was a real bug caught by
rendering the data, not by review.** The events table's earliest rows run back
to ~2004. Matching `after_close` on day D against "the first boundary with
`ts_after` date > D" is correct in-window — but a 2004 date is *before every
boundary the chain has*, so without a cap the match becomes "the first bar of
the entire chain," a nonsense result that printed as a plausible-looking tiny
intraday move. `MAX_REACTION_GAP_DAYS = 4` rejects any match further from the
calendar date than a long weekend could explain.
`test_labelled_earnings_ignore_ancient_calendar_history` in `test_golden.py`
pins this.

## Gotchas

- **`bar_count = 0` in `capture_coverage` is a real answer**, not a gap — IB was
  asked and had nothing. The Coverage page exists to keep that distinction
  visible; do not render it as missing data.
- **`volume` is 0 on every non-trades bar.** `frames.py` drops the column for
  bid/ask series rather than let a caller read it as "no trading".
  `charts.trade_box` guards the same way from the other end: it plots volume only
  when the column sums above zero, so an all-zero series is drawn as absent
  rather than as a flat floor of real measurements.
- **`trades` is sparse, `bid`/`ask` dense.** Continuous series come from bid/ask;
  trades is an activity overlay only.
- **Polars: `NaN >= x` keeps the row, and `is_not_null()` is True for NaN.**
  Detection guards NaN explicitly. A symbol without `MIN_HISTORY` boundaries
  scores NaN by design, and without the guard it produced spurious events.
- Timestamps are RFC3339 text with a literal `+00:00`; `frames.TS_FORMAT` parses
  that exact shape. Range queries elsewhere rely on it too.
- The reference snapshot's option bars stop the day *before* expiry (2026-08-20
  for the 2026-08-21 chain) — closer than earlier captures, but still not
  through expiry itself. Anything expiry-day waits on a later capture. The
  stock tape, captured separately, runs well past it (through 2026-08-25).
- **A `.join()` re-appends its new column(s) at the end, regardless of where
  that column sits in `SCHEMA`.** `DerivedEventSource._label_macro` rebuilds
  `n_cohort` via a join and this silently reordered it relative to every other
  `EventSource`'s output — invisible as long as `combine()` only ever
  concatenated one frame, and a `ShapeError` the moment a second real source
  (`LabelledEventSource`) showed up. Fixed in two places on purpose, not one:
  `_label_macro` re-selects `list(SCHEMA.keys())` before returning, and
  `combine()` now does the same to every input frame before concatenating, so
  no future `EventSource` can reintroduce this by a join anywhere in its own
  pipeline.
- Two calendar earnings sometimes land on the *same* trading day for different
  companies (a "calendar cluster") — that's expected and is exactly what keeps
  `_label_macro` from mislabelling shared company earnings as a macro event
  (only boundaries not already tagged `earnings` get relabelled).

## Backtests from ib-spread-trader

`backtest.BacktestResult` reads `out/*/backtest.json`. Fields added upstream are
read with a default, so older runs still load: `expiries` (several chains per
run), `bar_minutes` (the execution grid — 15 on captures from 2026-09, 30
before), `labels`, and per trade `expiry` and `held_through_earnings`.

- **The trade chart's stock bars are folded from the 5-minute tape** to the run's
  `bar_minutes` (`backtest.fold_bars`): the 15-minute captures took no 15- or
  30-minute stock bars, so a 30-minute query shows nothing after 2026-08-25.
- **A trade is drawn as what it is, not how it went** (`charts._draw_position`):
  every leg a line across the holding window, calls slot 1 and puts slot 2,
  long solid and heavier, short dashed, labelled in the trade's own notation
  (`+1C 756`) in `trade_box`. A right holding both a long and a short leg is a
  spread and its zone fills faintly in that right's color — a vertical is one
  fill, an iron condor two, a straddle none. P&L lives in the card's badge; it
  used to color the legs, which said how the trade went but not what it was.
  The stock price is neutral grey so it never passes for a call leg, and the
  forecast target is green (paired with the red stop) because orange would
  read as a put.
- **The Model Evaluation verdict reads `crps_skill_interval`**, the weekly-block
  bootstrap interval, not the point estimate — a skill whose interval spans zero
  is "within noise", however positive. Runs without the interval say they
  cannot be judged. A width-only model has `directional_forecasts = 0` and its
  hit-rate renders "—".
- **The chain pages (Events, Coverage) still read `30min` only**, so the
  15-minute October chain is invisible there; their detection thresholds were
  tuned on 30-minute boundaries and would need re-checking, not just a query
  change.

## Charts

`ui/theme.py` holds a validated palette; series identity is fixed (calls always
slot 1, puts always slot 2) so filtering never repaints a series. Two measures on
different scales are two stacked figures sharing an x-axis — never one plot with
two y-axes. `ui/charts.price_series` is the one exception to "one series, one
color, always" worth knowing about: it draws real spot *and* the parity forward
together, both in dollars (same scale, so a single y-axis is legitimate) — spot
solid and colored as the primary read, forward as a thin muted dashed
reference, so the two staying close is reassurance and the rare divergence
(right at an event) is visible rather than papered over.

`charts.trade_box` is the worked example of the stacked rule: price and share
count share an x-axis in two panels (76/24), never one plot with two y-axes —
overlaying them invites reading a tall volume bar as a big price move. The
holding-window lines span both panels so volume during the trade reads against
the same boundaries as price.

The trade card also renders what the backtester *decided* and what it *saw*:
`prediction` (expected P/L, probability of profit, and `score`, which is expected
P/L per unit of risk) beside `entry_features` (trend, VWAP distance, ATR,
realized vol, ATM IV, skew, RSI, days to earnings). A missing feature renders as
"—", never 0: the writer omits absent readings deliberately, and a zero would
read as a measurement.

`charts.position_track` is the other half of a trade's story: gross P&L over the
position's life with the trailing stop riding beneath it. It is a separate figure
rather than an overlay for the same reason as volume — the trailing stop is a
*P&L* level sitting a fixed fraction under the running peak, so it moves every
bar and has no meaning on a price axis. The peak is drawn dotted alongside,
because without it the stop looks arbitrary and with it the mechanism reads
directly: the stop tracks the peak up and never down. It appears only when the
rule was armed; a position that was never in profit has nothing to trail, and the
backtester records that as absent rather than as zero.

Its data is `positions.csv` (one row per open position per bar), read by
`backtest.position_marks`. Runs written before the backtester recorded it have no
such file and get an empty frame — which means "not recorded", not "nothing
happened", and callers should not render it as a flat line.

**The crosshair's halo is hidden with page CSS** (`theme.page_css()`, called by
every page). Plotly draws each hover spike over a solid halo 2px wider, colored
from the plot background; ours is transparent, which composites to white, so in
dark mode the 1px dashed crosshair became thick white bars. Painting the plot
background in the page's color was tried and is wrong: `st.context.theme`
reports "light" on a session's first run even in dark mode, which drew white
plot areas. (`theme.palette()` has the same first-run blind spot for colors.)

Time axes use `theme.trading_clock()`, which collapses nights and weekends off
the axis. Without it an overnight gap renders as a gentle diagonal — a move that
never happened.

Render a chart before believing it. `plotly.write_image` needs a Chrome that
kaleido fetches once via `kaleido.get_chrome_sync()`; the palette validator
checks colour, not whether tick labels fit.
