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
uv run pytest                       # 42 tests
uv run pytest tests/test_pricing.py
```

Golden tests in `tests/test_golden.py` run against the real snapshot and skip
cleanly when none is present. They pin values confirmed by hand — SPY's forward
at a known bar, PLTR's earnings crush, the straddle verdict — so a refactor that
quietly changes the maths fails loudly.

## What is derived, and why it has to be

The capture stores none of these; IB does not provide them.

| Quantity | Where it comes from |
|---|---|
| Intraday underlying | Put-call parity across the strike ladder |
| Implied vol | Black-76 on the parity forward |
| Delta | Falls out once vol is known |

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

## Gotchas

- **`bar_count = 0` in `capture_coverage` is a real answer**, not a gap — IB was
  asked and had nothing. The Coverage page exists to keep that distinction
  visible; do not render it as missing data.
- **`volume` is 0 on every non-trades bar.** `frames.py` drops the column for
  bid/ask series rather than let a caller read it as "no trading".
- **`trades` is sparse, `bid`/`ask` dense.** Continuous series come from bid/ask;
  trades is an activity overlay only.
- **Polars: `NaN >= x` keeps the row, and `is_not_null()` is True for NaN.**
  Detection guards NaN explicitly. A symbol without `MIN_HISTORY` boundaries
  scores NaN by design, and without the guard it produced spurious events.
- Timestamps are RFC3339 text with a literal `+00:00`; `frames.TS_FORMAT` parses
  that exact shape. Range queries elsewhere rely on it too.
- The reference snapshot was captured a week *before* its expiry, so the final
  week into expiry is absent. Anything expiry-day waits on a later capture.

## Charts

`ui/theme.py` holds a validated palette; series identity is fixed (calls always
slot 1, puts always slot 2) so filtering never repaints a series. Two measures on
different scales are two stacked figures sharing an x-axis — never one plot with
two y-axes.

Time axes use `theme.trading_clock()`, which collapses nights and weekends off
the axis. Without it an overnight gap renders as a gentle diagonal — a move that
never happened.

Render a chart before believing it. `plotly.write_image` needs a Chrome that
kaleido fetches once via `kaleido.get_chrome_sync()`; the palette validator
checks colour, not whether tick labels fit.
