# ib-option-viz

Local Streamlit app for exploring the options history captured by
[`ib-option-calc`](../ib-option-calc), and for finding the moments in it worth
looking at — above all earnings reactions, and whether the people who sold
premium into the event or the people who bought it came out ahead.

Read-only: it never writes to a capture snapshot.

## Run it

```sh
uv sync
uv run streamlit run app/Home.py
```

Snapshots are discovered in `~/Documents/Options Database` by default. Point
somewhere else with `OPTIONVIZ_DB_DIR`.

```sh
uv sync --extra dev
uv run pytest          # 71 tests; the golden ones skip if no snapshot is present
```

## Real data vs. reconstructed

Schema v6 added real 30-minute underlying bars and an `events` calendar to the
capture. Historical greeks and historical implied vol still don't exist — IB
doesn't provide them — so those two are still rebuilt, and the reconstruction
turns out to matter even now that real spot exists alongside it.

**Forward price**, via put-call parity: every strike quoted on both sides gives
an independent read, `F = K + C - P`, and the median across the ladder is the
estimate. This is still what the vol solve uses — Black-76 needs a forward, and
this one is rate/dividend-free by construction, which real spot alone is not.
Cross-checked against real spot across the whole window, the two agree to a few
basis points except right at an event: AMD's parity fit visibly wobbles in the
bar its earnings print lands in (`parity_mad` triples) while a handful of
strikes quote wide or stale for a moment. Spot, one instrument instead of
thirty reconciled together, isn't exposed to that — which is why it's now the
primary number in the price chart and the event verdict, with the forward kept
alongside as a thin reference line.

The gap between the two isn't noise, either: it's carry, and it decays
predictably. On SPY it runs from +0.71% at 51 days to expiry down to +0.03% the
day before — the accrued dividend/rate spread annuitizing to zero exactly where
it should.

**Implied vol.** Solved on the out-of-the-money side by vectorised bisection.
SPY prices out at 10% with a textbook downside skew; PLTR goes into its print
at 81% with a flat smile and comes out at 55% with a normal one.

## Finding events

Two sources, merged, with confirmed facts winning over statistical guesses.

**Detected.** A boundary is scored against **that symbol's own distribution of
same-kind boundaries**, using a median/MAD robust z. This matters more than it
sounds: a raw "gap > 3%" rule fires fifteen times on AMD and MSTR in two months
while a genuine SPY event never clears it. Earnings read as a large overnight
surprise together with a sharp vol collapse; on the reference snapshot that
alone finds six real ones and correctly declines to call SPY's post-FOMC crush
an earnings print. Boundaries shared across the cohort are labelled macro
instead — 29 July 18:30 UTC shows up simultaneously on SPY, QQQ and IWM.

**Confirmed.** Schema v6's `events` table holds SEC EDGAR 8-K earnings
releases, one row per real filing, running back to 2004. `LabelledEventSource`
matches each in-window release onto the same bar-boundary metrics the detector
computes — same shape, so a confirmed event and a detected one render
identically, only `kind`/`label`/`source` differ — and wins at that symbol/day
regardless of score. This is where the two sources earn their keep as *two*
sources rather than one: AMD's and MSTR's real earnings barely move their
implied vol (AMD's ATM IV is already so high, ~80%+, that an earnings-sized
move doesn't register as a crush), so the detector alone misses both. The
calendar doesn't need vol to move to know a release happened. Confirmed events
show on the Events page regardless of the surprise-threshold slider, and are
marked with a "Confirmed" badge.

Event sources sit behind one `EventSource` protocol; `combine()` does the
merge, and a third source is exactly this much code to add.

## Who won

Ranking asks whether a moment is interesting. The verdict asks the trading
question, and answers it with no model at all: take the ATM straddle on a real
listed strike, hold it fixed across the boundary, and compare what it cost with
what it became.

Prices are reported two ways. Mid-to-mid is the headline. **Executable** is what
the trade was really worth — a buyer lifts both offers going in and hits both
bids coming out. Because the capture stores `bid` and `ask` as separate series
rather than a blended `bid_ask`, that is measured rather than assumed.

How far the underlying actually moved uses real captured spot wherever the
snapshot has it on both sides of the boundary, falling back to the parity
forward otherwise — the same preference the price chart makes.

## Layout

```
src/optionviz/
  catalog.py    discovery across snapshots; per-chain routing
  db.py         read-only connections
  frames.py     bars -> tidy bid/ask/mid (options + real stock), events, quality
  pricing.py    black76, vectorised implied vol, parity forward
  surface.py    per-timestamp forward, vol surface, ATM metrics
  events.py     EventSource protocol; DerivedEventSource, LabelledEventSource
  scoring.py    straddle P&L across a boundary; the verdict
  cache.py      parquet cache keyed on snapshot fingerprint
  backtest.py   ib-spread-trader results; stock bars on a run's grid
  ui/           palette, chart vocabulary, cached loaders, trade card
app/            Streamlit entry and pages
```

## Backlog

- Buyers-vs-sellers scoreboard aggregated across the cohort, with short-strangle
  and short-put variants beside the straddle
- IV surface heatmap and a cohort-wide vol carpet
- Straddle decay across the whole life of an expiry
- Event-variance decomposition, for a principled per-night implied move
- Open interest and max pain — one cross-section today, a series as snapshots
  accumulate
- Expiry-day pinning — option bars now run to the day before expiry (up from a
  week before), but a capture that runs through expiry itself hasn't landed yet
- Non-earnings calendar kinds (guidance, macro releases, splits) once the
  capture starts tagging them — `LabelledEventSource` already passes `kind`
  through rather than hardcoding "earnings", so this needs no code change here
