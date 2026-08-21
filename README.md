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
uv run pytest          # 42 tests; the golden ones skip if no snapshot is present
```

## What it reconstructs

The capture stores no intraday underlying series, no historical greeks and no
historical implied vol, because IB does not provide them. All three are rebuilt
here from the one thing that *is* dense — bid/ask bars across a whole strike
ladder.

**Forward price.** Every strike quoted on both sides gives an independent read
via put-call parity, `F = K + C - P`, and the median across the ladder is the
estimate. On SPY this returns 773.31 from all 30 strikes with a MAD of 0.03, and
across all twelve symbols the forward sits 0.05–0.15% above that day's stock
close — the carry basis a nine-day forward should show. The result is a
30-minute underlying series where the database holds only daily bars.

**Implied vol.** Because the forward comes from parity, Black-76 needs no rate
and no dividend assumption — the carry is already inside `F`. Solved on the
out-of-the-money side by vectorised bisection. SPY prices out at 10% with a
textbook downside skew; PLTR goes into its print at 81% with a flat smile and
comes out at 55% with a normal one.

## Finding events

A boundary is scored against **that symbol's own distribution of same-kind
boundaries**, using a median/MAD robust z. This matters more than it sounds: a
raw "gap > 3%" rule fires fifteen times on AMD and MSTR in two months while a
genuine SPY event never clears it.

Earnings read as a large overnight surprise together with a sharp vol collapse.
On the reference snapshot that finds six, all real — PLTR, AMZN, META, AAPL,
COIN, TSLA — and correctly declines to call SPY's post-FOMC crush an earnings
print. Boundaries shared across the cohort are labelled macro instead; 29 July
18:30 UTC shows up simultaneously on SPY, QQQ and IWM.

Event sources are pluggable behind one schema. When the capture side grows an
`events` table, a second source drops in and its labels take precedence.

## Who won

Ranking asks whether a moment is interesting. The verdict asks the trading
question, and answers it with no model at all: take the ATM straddle on a real
listed strike, hold it fixed across the boundary, and compare what it cost with
what it became.

Prices are reported two ways. Mid-to-mid is the headline. **Executable** is what
the trade was really worth — a buyer lifts both offers going in and hits both
bids coming out. Because the capture stores `bid` and `ask` as separate series
rather than a blended `bid_ask`, that is measured rather than assumed.

## Layout

```
src/optionviz/
  catalog.py    discovery across snapshots; per-chain routing
  db.py         read-only connections
  frames.py     bars -> tidy bid/ask/mid, with a quality record
  pricing.py    black76, vectorised implied vol, parity forward
  surface.py    per-timestamp forward, vol surface, ATM metrics
  events.py     EventSource protocol; DerivedEventSource
  scoring.py    straddle P&L across a boundary; the verdict
  cache.py      parquet cache keyed on snapshot fingerprint
  ui/           palette, chart vocabulary, cached loaders
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
- Expiry-day pinning, once a capture runs at expiry rather than a week before
