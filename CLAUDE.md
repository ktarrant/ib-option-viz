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
uv run pytest                       # 87 tests
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
  `tv_chart.payload` guards the same way from the other end: it sends volume only
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

- **Stock bars are folded from the 5-minute tape** to the timeframe asked for
  (`backtest.fold_bars`): the 15-minute captures took no 15- or
  30-minute stock bars, so a 30-minute query shows nothing after 2026-08-25.
- **The Backtest page judges a run; the Chart page reads its trades.** The
  Backtest page is the summary (config, metrics, funnels, equity) and one table
  of every trade; a row opens it on the Chart page (`chart_open` in session
  state, applied there before any widget renders, since it sets the symbol and
  the run). Each trade used to be a Plotly card with its own price chart
  (`charts.trade_box`, removed; the Plotly version is on the `chart-plotly`
  branch) — far harder to read than candles.
- **A trade is drawn as what it is, not how it went**: every leg a line across
  the holding window, calls slot 1 and puts slot 2, long solid and heavier,
  short dashed, labelled in the trade's own notation (`+1C 756`). A right
  holding both a long and a short leg is a spread and its zone fills faintly in
  that right's color — a vertical is one fill, an iron condor two, a straddle
  none. P&L lives in the card's badge and the entry/exit markers; it used to
  color the legs, which said how the trade went but not what it was. The
  forecast target is green (paired with the red stop) because amber would
  read as VWAP and magenta as a put.
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

**The look is a dark terminal, and dark only.** Streamlit's colours, fonts
(JetBrains Mono, from Google Fonts), radius and heading sizes are set in
`.streamlit/config.toml`; `theme.DARK` mirrors those colours for everything
the theme doesn't reach (Plotly, the chart canvas, the chain board) — change
one, change the other. Amber `accent` is chrome only (headings, the picked
bar, the chain's forward rule, crosshair tags), never a series. What config
can't express lives in `theme.page_css()`: uppercase headings, the amber h1,
metric tiles. `app/Home.py` is only the entrypoint — page config, that CSS
and `st.navigation(position="top")`. The pages live in `app/views/`, **not**
`pages/`: Streamlit still auto-discovers a `pages/` folder, and a cold session
opened on a page's URL then ran that page alone — no nav, no layout, no CSS.

`ui/theme.py` holds a validated palette (calls cyan, puts magenta, VWAP amber,
checked against the dark surface — re-run the dataviz validator on any change); series identity is fixed (calls always
slot 1, puts always slot 2) so filtering never repaints a series. Two measures on
different scales are two stacked figures sharing an x-axis — never one plot with
two y-axes. (One deliberate exception: the Chart page draws volume TradingView's
way, in the bottom fifth of the price pane on a hidden scale — chosen for the
look, and only there.) `ui/charts.price_series` is the one exception to "one series, one
color, always" worth knowing about: it draws real spot *and* the parity forward
together, both in dollars (same scale, so a single y-axis is legitimate) — spot
solid and colored as the primary read, forward as a thin muted dashed
reference, so the two staying close is reassurance and the rare divergence
(right at an event) is visible rather than papered over.

The trade card (`ui/trade_card.py`, under the Chart page's chart) renders what the backtester *decided* and what it *saw*:
`prediction` (expected P/L, probability of profit, and `score`, which is expected
P/L per unit of risk) beside `entry_features` (trend, VWAP distance, ATR,
realized vol, ATM IV, skew, RSI, days to earnings). A missing feature renders as
"—", never 0: the writer omits absent readings deliberately, and a zero would
read as a measurement. For a ranked run it adds the **View**: the model's
drift term by term (`backtest.model_drift`, a mirror of ib-spread-trader's
`LognormalDriftModel::drift` — keep the two in step), so a mean-reversion trade
says which spike it faded and whether the prevailing trend let it.

`charts.position_track`, on the card, shows gross P&L over the position's life
with the trailing stop riding beneath it — only for runs where that rule was
armed; the Chart page's P&L pane (`tv_chart.pnl_pane`) draws the gross P&L
itself for every selected trade. It is a separate figure
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

**The crosshair's halo is hidden with page CSS** (`theme.page_css()`, called
once by the entrypoint). Plotly draws each hover spike over a solid halo 2px
wider, colored from the plot background; ours is transparent, which composites
to white, so in dark mode the 1px dashed crosshair became thick white bars.

Time axes use `theme.trading_clock()`, which collapses nights and weekends off
the axis. Without it an overnight gap renders as a gentle diagonal — a move that
never happened.

### The Chart page (`5_Chart.py`, `ui/tv_chart.py` + `tv_chart.js`)

TradingView's Lightweight Charts 5.2, mounted with `st.components.v2` — the
only page not on Plotly. The Plotly version is kept on the `chart-plotly`
branch. Python builds a plain-JSON payload (`tv_chart.payload`); the JS only
draws it. Lean on what the library does natively — wheel zoom, drag pan,
dragging either axis to stretch it, double-click the price axis to refit,
autoscale to whatever is visible, session gaps that simply aren't slots on the
time scale — rather than rebuilding any of it. Box zoom, the navigator strip,
the window slider and rangebreaks all existed only to work around Plotly.

- **Lifecycle.** Streamlit re-invokes the JS on every change of `data` or theme,
  on the same `parentElement`, and runs cleanup only on unmount; a rerun with
  identical data doesn't invoke it at all. So one chart lives per element and
  is updated in place — that is what keeps the viewer's pan and zoom across
  reruns. Bars are only re-set when `data.view` (symbol, timeframe, extent)
  changes. A change of symbol or timeframe keeps the range: a range button's
  choice while it is in force (All stays All, 5D stays five sessions), else the
  same dates. A wheel, a drag or a programmatic `focus` ends a button's choice;
  a click without movement only picks a bar and doesn't. The JS file is
  read at import: restart the server after editing it.
- **Time.** The library has no time zones and draws seconds as UTC, so bars go
  shifted to New York wall clock (`bar_times`) — the axis reads in exchange time
  and DST needs no handling. Daily bars go as business-day strings. Clicks never
  come back as a shifted time: the JS sends `keys[i]`, the bar's real UTC start.
- **Position.** Anything between bars (a 15-minute trade on an hourly chart, a
  forecast's horizon past the tape) is a fractional bar index (`logical_x`),
  never a time — a time not in the bars becomes a phantom bar.
  `timeScale().logicalToCoordinate` is only right on *whole* indices; a
  fractional one lands far off-screen, so the primitives interpolate between
  neighbours (`Primitive.x`). Caught by rendering: every trade's leg started at
  the left edge.
- **The pick is an instant, not a bar**: `chart_as_of` is the UTC close of the
  bar clicked, so it means the same thing on every timeframe and is exactly the
  no-lookahead `as_of` the chain is read at. The highlighted column is whichever
  bar holds that close on the current timeframe. ←/→ step it, Esc clears.
  Events arrive through `on_*_change` callbacks, which can read the value from
  `st.session_state["tv"]` — so state is set before the rerun renders, with no
  second rerun.
- **Trades are one primitive, not a series per leg** (`Trades` in the JS): legs,
  spread zones, and for the selected trade its leg labels and forecast
  target/stop. Its `hitTest` makes a leg or zone hoverable and clickable (the id
  comes back as `hoveredInfo.objectId`); markers carry ids `t{id}.in/.out` so a
  click on them selects the trade too. Its `autoscaleInfo` widens the price axis
  to the strikes of every trade in view — a user decision: nothing hidden, at
  the cost of flatter candles when a far strike is on screen. Selecting a trade
  reads the chain at its entry bar's close (`opened_at + bar_minutes`) and
  outlines its legs on the board. A table row also scrolls the chart to it, via
  a one-shot `focus` that applies only when its nonce changes. Picking a run
  frames all its trades once, since an older chain's can lie wholly outside the
  opening window — on picking the run only, never on a change of symbol, which
  keeps the range the viewer chose.
- **A run is read one trade at a time, across symbols.** The table lists every
  trade in the run and ◀/▶ step through them in run order — in a toolbar
  *above* the chart, so stepping never needs a scroll, and on Shift+←/→ via
  `st.button(shortcut=)` (the chart's own ←/→ ignore Shift, so the bar pick and
  the trade step don't both fire); either opens the
  trade on its own symbol (`_open` sets the symbol widget, so it runs only from
  a callback or before the widgets render). A frame is asked for as instants
  (`chart_frame`) and turned into bar indices after the bars load, because a
  trade on another symbol has no bars until that rerun. A run opens on the
  model inputs its config weights (`tv_chart.weighted_inputs`) — for
  mean-reversion-prevailing, Trend and Prevailing — and the viewer's changes
  hold until another run is picked. With no run overlaid, none of this
  appears: the page is the plain price reader.
- **The backtester's `ts` is the bar START** (ib-spread-trader CLAUDE.md), so a
  forecast row is known at `ts + bar_minutes`. Model inputs (`forecasts.csv`
  columns) are joined on that, backward with a tolerance of one model step
  (`cadence_minutes`, not the finer option grid), so a bar without a reading is
  a gap, not the last value repeated. A line series joins across empty points,
  and a point's colour paints the segment *leaving* it — so the last reading
  before a gap is drawn transparent. Pane sizes are stretch factors; fixed
  `setHeight`s don't hold as panes are added.
- **What goes on the price and what gets a pane.** VWAP is a line on the price,
  rebuilt *exactly* from the run's own numbers: the backtester defines
  `vwap_distance` as `(spot − vwap) / vwap` and logs `spot`, so
  `vwap = spot / (1 + distance)`. It breaks at each session open (VWAP resets
  daily) and takes palette slot 3, the next after calls and puts. The trend
  stays a pane, a histogram about zero, green above and red below, showing the
  logged ratio as is. Drawing it as its SMA lines was tried and backed out: the
  SMAs had to come from the tape, which reproduces the model's only where it
  saw every bar (its indicators skip bars with no option quote), and the trend
  calculation is the backtester's to change — the chart shouldn't depend on it.
- **The forecast fan** is the latest row known by the pick, and only if from the
  same session — an earlier session's forecast isn't the one "at this bar".
  Straight edges from spot to the horizon's quantiles: the run logs only the
  endpoint, so no path between is drawn as if known.
- **Clicking a bar opens its option chain** (`surface.chain_at` over
  `frames.option_quotes`). Each expiry is read off *one* option bar — the
  latest to have closed by `as_of`, stamped at its close — never each
  contract's own last quote, which would mix stale strikes into the parity
  forward. "Closed by" is the no-lookahead rule: a 15-minute option bar can
  outlast a 5-minute candle. Quotes read from all bar sizes and expiries, since
  captures differ (August 30-minute, October 15-minute). IV is the OTM side's,
  one per strike, so put Δ = call Δ − 1.
- **The chain board** (`ui/chain_table.py`) is plain HTML, not `st.dataframe`:
  the data grid has no per-cell tooltips. Columns are `surface.expiry_slots` —
  next weekly (next non-monthly Friday; dailies skipped), next monthly, the one
  after — dated from the calendar, so an uncaptured column still says which
  expiry it would be. Cells tint by the move since the previous bar
  (`chain_at` one bar before `as_of`); the card adds the change since the prior
  session's close (`chain_at` at the session's 00:00). Cards open downward in
  the top half and upward below so they stay inside the table.
- **Theme** is dark only: the payload carries `theme.palette()`, and marker
  colours are palette names. The canvas's own labels take the chart's font.
- `underlying_bars` unions every snapshot (newest wins per `ts`) — a fresh
  capture isn't guaranteed to carry the older tape — and `fold_bars` anchors
  windows on :30 so an hourly bar opens with the session, not inside the break.
- The Plotly pages' break bounds (`theme.trading_clock`) are UTC and summer-time
  (13:30–20:00). When the tape crosses the November DST change, the session
  shifts to 14:30–21:00 UTC and the hour break will clip the last half hour.

Not built yet, roughly in order — each is a pane, a primitive or a payload
field, not a new mechanism:
- Peak and trailing stop in the P&L pane (it is one series per pane today),
  retiring `position_track`.
- An opportunity pane from `scorer.csv`: best candidate score per bar (137k rows
  on a recent run, so aggregate per symbol and cache).
- The forecast band "made one horizon earlier" on the real chart — Model
  Evaluation's `forecast_band`, showing whether price stayed inside.
- Compare another symbol in `PriceScaleMode.Percentage`; derived event
  boundaries as markers; an ATM IV pane from `cache.build_chain`, run-independent.
- Deep links (`st.query_params`: symbol/tf/run/trade/as_of); several runs at once; a live tail via `series.update()`.

Render a chart before believing it. `plotly.write_image` needs a Chrome that
kaleido fetches once via `kaleido.get_chrome_sync()`; the palette validator
checks colour, not whether tick labels fit. The Chart page draws in the browser,
so it can only be checked there: run the app on a free port and drive it with
Playwright (`uvx --with playwright python …`, pointed at a cached Chromium
headless shell via `executable_path`). Its canvases sit in the component's
shadow root, which Playwright locators pierce; the legend's text is the easiest
probe of what is hovered.
