"""TA Study: what classic and modern technical indicators say about direction,
move size and entry timing, on ten years of 5-minute stock bars.

The report of ib-spread-trader's `scripts/ta_study.py`, rendered from its
`out/ta_study.json`. The prose was written against one run
(`WRITTEN_AGAINST`); every chart, table and verdict is live, so a rebuilt JSON
(the backfill is still landing) redraws the evidence and the page says when
the prose has fallen behind it.
"""

from pathlib import Path

import polars as pl
import streamlit as st

from optionviz import ta_study as ta
from optionviz.ui import charts

WRITTEN_AGAINST = "ib-option-calc-2026-10-05.sqlite3"

path = Path(st.sidebar.text_input("Study file", value=str(ta.DEFAULT)))
report = ta.load(path)
st.title("TA study")
if report is None:
    st.info(f"No study at `{path}`. In ib-spread-trader: `uv run scripts/ta_study.py`.")
    st.stop()

prov, S = report["provenance"], report["sections"]
cov, cat, dirn, mv, ee, comb, leads = (S[k] for k in (
    "coverage", "catalog", "direction", "move_size", "entry_exit", "combinations", "leads"))
lv = S.get("levels")
st.caption(
    f"Snapshot `{Path(prov['snapshot']).name}` · {cov['first']} → {cov['last']}, {cov['sessions']:,} sessions, "
    f"{len(cov['universe'])} symbols · split {prov['split']} · catalog v{prov['catalog_version']} · generated "
    f"{prov['generated_at'][:16]} UTC at `{prov['commit']}` · {prov['draws']} placebo draws per search")
if Path(prov["snapshot"]).name != WRITTEN_AGAINST:
    st.warning(
        f"The text on this page was written against `{WRITTEN_AGAINST}`; the charts and verdicts come from "
        f"`{Path(prov['snapshot']).name}`. Where they disagree, believe the charts.")
waiting = [s["symbol"] for s in cov["symbols"] if s["role"] in ("no bars", "too short")]
if waiting:
    st.warning(f"**Provisional.** {len(waiting)} registered stocks have no usable 5-minute history yet "
               f"({', '.join(waiting)}). Re-run the study when the backfill lands; nothing else changes.")


def table(rows: list[dict], columns: dict) -> None:
    st.dataframe(pl.DataFrame([{h: f(r) for h, f in columns.items()} for r in rows]), hide_index=True, width="stretch")


def verdict_col(c: dict | None) -> str:
    return ta.label(c.get("verdict", "")) if c else "—"


def iv(c: dict | None, digits: int = 2) -> str:
    return ta.interval(c, digits) if c else "—"


def paired(pairs: list[tuple[dict, dict]], name) -> tuple[list[str], list[dict]]:
    """Forest rows: each cell above its holdout twin."""
    labels, cells = [], []
    for d, h in pairs:
        for period, c in (("discovery", d), ("holdout", h)):
            if c is not None and c.get("point") is not None:
                labels.append(f"{name(d)} · {period}")
                cells.append(c)
    return labels, cells


family_of = {s["name"]: i["family"] for i in cat["indicators"] for s in i["signals"]}
dd, di = dirn["daily"], dirn["intraday"]
held = lambda fam: [r for r in leads["rows"] if r["family"] == fam]
kept = lambda rows: [r for r in rows if r["holdout"].get("verdict") == "finding"
                     and (r["holdout"]["point"] > 0) == (r["discovery"]["point"] > 0)]
est = {(c["estimator"], c["horizon"], c["period"]): c for c in mv["estimators"]}
vrp = {(c["model"], c["period"]): c for c in mv["vrp"]["cells"]}

# ------------------------------------------------------------------ summary
st.subheader("The short answer")
st.markdown(f"""
- **No indicator calls the next day, week or fortnight.** {dd['placebo']['cells']:,} daily cells (every
  directional signal × 1, 5 and 10 sessions, raw and SPY-hedged × all / ETFs / single names): the strongest
  has |t| {dd['placebo']['best']:.1f}, where a placebo search of the same size tops out near
  {dd['placebo']['t50']:.1f} by chance and the bar is {dd['placebo']['t95']:.1f}. Most of the top cells
  shrink toward zero after 2022. On 15-minute bars, {len(held('direction_intraday'))} cells cleared their bar
  before 2023 — all of them momentum into the close: above VWAP, above the upper band, RSI above 70, a rising MACD
  histogram — and **{len(kept(held('direction_intraday')))} survived the holdout**.
- **Move size is where the tape speaks, and it repeats out of period.** An earnings release ahead, a Bollinger
  squeeze, a TTM squeeze and VIX9D above VIX forecast a bigger move than trailing vol says, and a narrow (NR7, NR4)
  day a *smaller* one — against Crabel — and all keep their sign and size after 2022; intraday, heavy volume, a
  wide first hour and a big gap forecast a busier rest of the session. {len(kept(held('move_daily')) + kept(held('move_intraday')))} of
  {len(held('move_daily')) + len(held('move_intraday'))} move leads survive.
- **But a good vol model already knows most of it.** Against HAR-RV (daily, weekly and monthly realized vol from
  the 5-minute tape), only the earnings calendar and the overnight share of variance cut CRPS out of period by
  more than a hair; the squeezes and narrow ranges are vol clustering HAR already sees. HAR and the range
  estimators are the best trailing vols — {est[('har', 1, 'holdout')]['point']:+.1%} and
  {est[('garman_klass20', 1, 'holdout')]['point']:+.1%} CRPS against 20-day close-to-close for the next session
  after the split, {est[('har', 5, 'holdout')]['point']:+.1%} and {est[('garman_klass20', 5, 'holdout')]['point']:+.1%}
  for the next five.
- **SPY's variance premium is a decade-long fact, and it has a number.** VIX9D, scaled by
  {vrp[('vix9d', 'discovery')]['scale']:.2f} (fit before 2023; {mv['vrp']['holdout_scale_vix9d']:.2f} fit after),
  forecasts the next five SPY sessions better than trailing vol in both periods, and edges HAR; unscaled, it is
  too wide.
- **Combining signals adds nothing that survives.** {comb['contexts']['placebo']['cells']} signal × context
  interactions and {comb['pairs']['placebo']['cells']} confirmation pairs produce
  {len(held('context')) + len(held('pairs'))} discovery finding(s) and no holdout survivor; trend and oscillator
  consensus counts are flat.
- **Support and resistance levels mostly do not hold, and the classic confirmation makes it worse.** Against
  random levels at the same distance from the open, only the floor pivot point rejects a little more often in both
  periods; the prior session's low and close, tested from above, *break* more often, and so does the pool of every
  level. An oversold touch of support (or overbought touch of resistance) breaks far more often at a real level
  than at a random one — worst at the prior close, the pivot point, the POC and the value area — and no
  confirmer makes a level hold.
""")

with st.expander("How to read this page — method and verdicts"):
    st.markdown(f"""
**Data.** Regular-hours 5-minute TRADES bars from the snapshot, folded to daily and 15-minute bars by New York
clock; every symbol with at least {cov['min_sessions']} whole sessions is a target ({len(cov['universe'])} today),
and {', '.join(cov['context_only'])} and the VIX family are inputs only. **Timing.** A signal is read at a bar's
close and the trade enters at the next bar's open — decide on N, fill on N+1.

**Units.** Returns are log returns over the symbol's *trailing* 20-session daily sd, per √horizon. Move size is
log(realized vol on the 5-minute path ÷ trailing vol). Every statistic is a signal's **excess over its symbol's own
mean in the same period** (and, for 15-minute bars, the same time slot), signed the way the textbook reads the
signal: the universe is today's winners, and this keeps a decade of drift from posing as edge. A continuous
indicator is read as its top fifth minus its bottom fifth, both against its own trailing year.

**Intervals** are 95 % block bootstraps — weeks for one-session and intraday targets, months for five sessions
and up (the unit is in each chart's hover). **Searching.** Every family carries a **placebo**: the same
statistics with each signal circularly shifted in time by a random year or more per symbol, {prov['draws']}
times, which keeps its base rate and persistence and breaks its timing. A cell is a **finding** only when its
interval excludes zero *and* its |t| beats the shuffles' 95th-percentile best.

**Holdout.** Discovery runs to {prov['split']}; the holdout after it. Every discovery finding is re-judged once
on the holdout as a single pre-registered test (interval only), and daily ones also get an out-of-period CRPS
test: a drift or vol multiplier on the signal, fit before {prov['split'][:4]}, scored after against HAR-RV
without it. The catalog and every grid were fixed before the first run (catalog v{cat['version']}).

| verdict | means |
|---|---|
| finding / improves | interval excludes zero, and beats the search's placebo bar where there was a search |
| hurts | an out-of-period CRPS test that made the forecast worse |
| search luck | interval excludes zero, but shuffled signals do that well by chance |
| unguarded | a holdout cell whose discovery twin was not a finding: no pre-registration guards it |
| within noise / too few | interval includes zero / under 30 rows or 5 blocks |
| descriptive | shown for reading, not tested |
""")

# ------------------------------------------------------------------ A
st.divider()
st.header("A · Coverage")
table(cov["symbols"], {
    "symbol": lambda s: s["symbol"], "role": lambda s: s["role"], "first": lambda s: s.get("first", ""),
    "last": lambda s: s.get("last", ""), "sessions": lambda s: s["sessions"],
    "missing inside span": lambda s: s.get("missing"), "behind the calendar": lambda s: s.get("lag"),
    "discovery / holdout": lambda s: f"{s.get('discovery', 0)} / {s.get('holdout', 0)}" if "discovery" in s else "",
    "earnings releases": lambda s: s.get("releases")})
chk = cov["daily_check"]
st.markdown(f"""
{cov['sessions']:,} sessions ({cov['discovery_sessions']:,} discovery, {cov['holdout_sessions']:,} holdout). A
session counts only if it opens at 09:30 and runs at least a half day, so a listing day or a capture that began
mid-session cannot misstate an open. **The fold matches IB's own daily bars** on {chk['open']['n']:,} days where
both exist: open {chk['open']['within_cent']:.0%}, high {chk['high']['within_cent']:.1%} and low
{chk['low']['within_cent']:.1%} to the cent. The close differs (median {chk['close']['median_bp']:.1f} bp): IB
prints the closing auction, the fold the last 5-minute trade.
""")

# ------------------------------------------------------------------ B
st.divider()
st.header("B · The catalog")
table(cat["indicators"], {"indicator": lambda i: i["name"], "family": lambda i: i["family"], "era": lambda i: i["era"],
                          "source": lambda i: i["source"], "parameters": lambda i: i["params"],
                          "bars": lambda i: {"D": "daily", "I": "15-min"}[i["timeframe"]],
                          "signals": lambda i: ", ".join(s["name"] for s in i["signals"])})
with st.expander("Every signal, its textbook reading and how often it is active"):
    table([dict(s, indicator=i["name"]) for i in cat["indicators"] for s in i["signals"]], {
        "signal": lambda s: s["name"], "indicator": lambda s: s["indicator"], "kind": lambda s: s["kind"],
        "target": lambda s: s["target"], "textbook sign": lambda s: f"{s['sign']:+d}", "reading": lambda s: s["reading"],
        "active share": lambda s: f"{s['rate']:.1%}" if s["kind"] != "continuous" else "quintiles"})

# ------------------------------------------------------------------ C
st.divider()
st.header("C · Direction")
st.markdown("""
Each cell: the signal's forward return in sd, in the direction the textbook reads it, less the symbol's own mean.
Positive means the textbook was right; negative means the opposite worked. Columns are horizons; `hedged` is net
of SPY.
""")
col1, col2 = st.columns([2, 1])
fam = col1.selectbox("Family", ["trend", "oscillator", "volume", "volatility", "market", "calendar"])
grp = col2.radio("Group", ["all", "etf", "single"], horizontal=True)
cols = [("raw", 1), ("raw", 5), ("raw", 10), ("hedged", 5)]
cell = {(c["signal"], c["target"], c["horizon"], c["period"]): c for c in dd["cells"] if c["group"] == grp}
sigs = [s for s in dict.fromkeys(c["signal"] for c in dd["cells"]) if family_of.get(s) == fam]
if sigs:
    z = [[cell.get((s, t, h, "discovery"), {}).get("point") for t, h in cols] for s in sigs]
    txt = [[(lambda d, ho: f"{d['point']:+.2f} → {ho['point']:+.2f}" if d.get("point") is not None
             and ho.get("point") is not None else "—")(cell.get((s, t, h, "discovery"), {}), cell.get((s, t, h, "holdout"), {}))
            for t, h in cols] for s in sigs]
    st.plotly_chart(charts.grid_heatmap([f"{t} {h}d" for t, h in cols], sigs, z, txt,
                                        f"{fam.title()} signals, {grp}: discovery colour, text discovery → holdout",
                                        "sd"), width="stretch", key="dir_heat")
top = sorted([p for p in ta.twins(dd, group=grp) if p[0].get("t") is not None], key=lambda p: -abs(p[0]["t"]))[:12]
labels, cs = paired(top, lambda c: f"{c['signal']} {c['target']} {c['horizon']}d")
st.plotly_chart(charts.forest(labels, cs, f"The twelve strongest daily cells before {prov['split'][:4]}, and after ({grp})",
                              0.0, "excess return in the textbook direction (sd)"), width="stretch", key="dir_top")
st.caption(f"Placebo for the daily family: the bar is |t| {dd['placebo']['t95']:.2f}; a shuffled search's best is "
           f"typically {dd['placebo']['t50']:.2f}; the real best is {dd['placebo']['best']:.2f}.")
st.markdown("""
**Nothing survives the search, and the near-misses mostly fade.** The top of the list is what a search of a
thousand cells throws up: small groups (ETF crosses, ~100–300 events) and hedged OBV slope, each a fraction of an
sd, most of them nearer zero after 2022. Two shapes are worth a second look rather than a conclusion. Overbought
single names (RSI above 70, MFI above 80) *beat* SPY over the next week in 7 of 7 discovery years — momentum,
against the textbook's fade — and lean the same way after 2022, inside noise. And the Monday session (decide on
Friday's close, hold Monday's session) is positive in both periods and for every symbol after 2022; it was not a
discovery finding, so its holdout interval is unguarded.
""")

st.subheader("15-minute bars")
pairs_i = [p for p in ta.twins(di, group="all") if p[0].get("point") is not None]
labels, cs = paired(pairs_i, lambda c: f"{c['signal']} → {c['horizon']}")
st.plotly_chart(charts.forest(labels, cs, "Every intraday direction signal, all symbols", 0.0, "excess return (daily sd)"),
                width="stretch", key="dir_intra")
st.markdown(f"""
**Every intraday finding was a 2016–22 effect, and all were momentum into the close.** A bar above session
VWAP, above its upper Bollinger band, with RSI above 70 or a rising MACD histogram carried on to the close by
0.04–0.06 of a daily sd — against the mean-reversion reading three of those signals carry — and cleared a bar of
|t| {di['placebo']['t95']:.1f} before 2023. Every one is within noise after it. That is the signature of an effect that was arbitraged or was never
there, and it is why a short-horizon signal needs a holdout and not just a placebo.
""")

# ------------------------------------------------------------------ D
st.divider()
st.header("D · Move size")
h_mv = st.radio("Horizon", [1, 5], horizontal=True, format_func=lambda h: f"{h} session{'s' if h > 1 else ''}")
pairs_m = [p for p in ta.twins(mv["daily"], group="all", horizon=h_mv) if p[0].get("point") is not None]
labels, cs = paired(pairs_m, lambda c: c["signal"])
st.plotly_chart(charts.forest(labels, cs, "Realized vol after each signal, against trailing vol (all symbols)", 0.0,
                              "excess log(realized ÷ trailing vol), textbook direction"), width="stretch", key="mv_daily")
st.markdown("""
Signs follow each signal's textbook reading, so **NR7 and NR4 plot negative: a narrow day is followed by a
smaller move, not the expansion Crabel promised** — vol clustering beats the coil, in both periods (the inside day
leans the same way, inside noise).
The Bollinger and TTM squeezes do forecast expansion against trailing vol, more strongly after 2022 than before.
**The overnight share is partly built in**: the target starts at the next open, so a symbol whose variance lives
in its gaps shows less of it on the path; read it as "intraday paths are quieter than close-to-close vol says",
not as a forecast.
""")
mrows = held("move_daily")
table(mrows, {"signal": lambda r: r["signal"], "horizon": lambda r: r["horizon"], "group": lambda r: r["group"],
              "before": lambda r: iv(r["discovery"]), "after": lambda r: iv(r["holdout"]),
              "after: verdict": lambda r: verdict_col(r["holdout"]),
              "CRPS vs HAR, after": lambda r: iv(r.get("crps"), 4), "CRPS verdict": lambda r: verdict_col(r.get("crps")),
              "fitted": lambda r: f"{r['crps']['param']:+.3f}" if r.get("crps") else "—"})
st.caption("The CRPS column adds the signal to HAR-RV as a vol multiplier exp(γ·signal), γ fit before the split, "
           "and scores the forecast of the next sessions after it. Positive = the signal adds what HAR does not know.")

st.subheader("Which trailing vol forecasts best")
h_e = st.radio("Horizon ", [1, 5], horizontal=True, key="est_h", format_func=lambda h: f"{h} session{'s' if h > 1 else ''}")
ecs = [c for c in mv["estimators"] if c["horizon"] == h_e and c["estimator"] != "cc20"]
labels = [f"{c['estimator']} (×{c['scale']:.2f}) · {c['period']}" for c in ecs]
st.plotly_chart(charts.forest(labels, ecs, "CRPS skill against 20-day close-to-close, each scaled once before the split",
                              0.0, "CRPS saved"), width="stretch", key="est")
hb = mv["har"]["5"]["beta"]
st.markdown(f"""
**HAR-RV and the range estimators (Parkinson, Garman–Klass, Rogers–Satchell) lead**: HAR is best for the next
session in both periods and for the next five before the split; after it the range estimators edge it at five.
EWMA, 20-day realized and Yang–Zhang follow; all beat close-to-close. Its five-session fit puts weight
{hb[1]:.2f} / {hb[2]:.2f} / {hb[3]:.2f} on yesterday / last week / last month (R² {mv['har']['5']['r2']:.2f} in log
variance). This is the stock-only vol a model can use where no option chain exists — and the benchmark any
indicator has to beat before it earns a place in the vol forecast.
""")

st.subheader("SPY's variance premium, 2016 →")
yr = mv["vrp"]["yearly"]
st.plotly_chart(charts.study_lines(
    [y["year"] for y in yr],
    [("VIX: next 21 sessions", [y["vix_ratio"] for y in yr], "series_1", None),
     ("VIX9D: next 5 sessions", [y["vix9d_ratio"] for y in yr], "series_2", None),
     ("realized = implied", [1.0] * len(yr), "muted", "dot")],
    "Realized ÷ implied variance, SPY, by year", "year", "ratio"), width="stretch", key="vrp_years")
vcs = [c for c in mv["vrp"]["cells"] if c["model"] != "cc20"]
st.plotly_chart(charts.forest([f"{c['model']} (×{c['scale']:.2f}) · {c['period']}" for c in vcs], vcs,
                              f"Five-session SPY forecasts against trailing vol (from {mv['vrp']['first']})", 0.0,
                              "CRPS saved"), width="stretch", key="vrp")
st.markdown(f"""
Realized variance ran below implied in every year but 2018 and 2020, and implied sat above the realized that
followed on most days of every year. The best scale for VIX9D as a five-session vol is
{vrp[('vix9d', 'discovery')]['scale']:.2f} before the split and {mv['vrp']['holdout_scale_vix9d']:.2f} after — the
same number twice — and a scaled VIX9D edges HAR in both periods, inside overlapping intervals. The vol-timing model's 0.85 sits close; but it
multiplies ATM IV, which runs below VIX (VIX prices the whole strike strip, skew included), so the two are not the
same knob.
""")

st.subheader("15-minute bars: the rest of the session")
pairs_mi = [p for p in ta.twins(mv["intraday"], group="all") if p[0].get("point") is not None]
labels, cs = paired(pairs_mi, lambda c: c["signal"])
st.plotly_chart(charts.forest(labels, cs, "Rest-of-session realized vol after each reading (all symbols)", 0.0,
                              "excess log(realized ÷ expected)"), width="stretch", key="mv_intra")
st.caption("Heavy volume for the slot, a wide first hour and a big opening gap each forecast a busier rest of "
           "the session, before and after the split. Useful for when to watch a position, not for direction.")

# ------------------------------------------------------------------ E
st.divider()
st.header("E · Entry and exit")
base = {b["period"]: b for b in ee["baseline"]}
st.markdown(f"""
After each event fires, enter at the next open with a target and a stop one trailing *weekly* sd away and five
sessions to reach either, first touch read off the 5-minute path. Unconditionally the up barrier comes first
{base['discovery']['p_up_first']:.0%} of the time, the down {base['discovery']['p_down_first']:.0%}, neither
{base['discovery']['p_timeout']:.0%} (before the split). The statistic is the trade's result in barrier units
(+1 target, −1 stop, the fraction reached at a timeout), less the same for every entry.
""")
pairs_b = sorted([p for p in ta.twins(ee["barrier"], group="all") if p[0].get("point") is not None],
                 key=lambda p: -abs(p[0].get("t") or 0))
labels, cs = paired(pairs_b, lambda c: c["signal"])
st.plotly_chart(charts.forest(labels, cs, "Barrier trades after each event, against entering anywhere", 0.0,
                              "excess result (barrier units)"), width="stretch", key="barrier")
st.markdown(f"""
**No event is a better entry than a random one** (bar |t| {ee['barrier']['placebo']['t95']:.1f}; best
{ee['barrier']['placebo']['best']:.1f}). The exit reading is the same table turned round: a bearish event that
forecast nothing for a short forecasts nothing for a long either, so no indicator here earns a place as an exit.
""")
names = [p[0]["signal"] for p in pairs_b]
pick = st.selectbox("Path after", names)
path_rows = {p["period"]: p["mean"] for p in ee["paths"] if p["signal"] == pick}
st.plotly_chart(charts.study_lines(
    list(range(1, 11)), [("before the split", path_rows.get("discovery", []), "series_1", None),
                         ("after", path_rows.get("holdout", []), "series_2", None)],
    f"Cumulative excess return after {pick}, textbook direction", "sessions after the fire", "sd"),
    width="stretch", key="path")
table([o for o in ee["outcomes"] if o["signal"] == pick], {
    "period": lambda o: o["period"], "events": lambda o: o["n"], "target first": lambda o: f"{o['p_target']:.0%}",
    "stop first": lambda o: f"{o['p_stop']:.0%}", "timeout": lambda o: f"{o['p_timeout']:.0%}",
    "median best (barriers)": lambda o: o["mfe_median"], "median worst": lambda o: o["mae_median"],
    "median bars to a touch": lambda o: o["bars_median"]})

# ------------------------------------------------------------------ F
st.divider()
st.header("F · Combinations")
ctx = comb["contexts"]
fam2 = st.selectbox("Family ", ["trend", "oscillator", "volume", "volatility", "market", "calendar"], key="ctx_fam")
contexts = cat["contexts"]
cc = {(c["signal"], c["context"]): c for c in ctx["cells"] if c["period"] == "discovery"}
hc = {(c["signal"], c["context"]): c for c in ctx["cells"] if c["period"] == "holdout"}
sigs = [s for s in dict.fromkeys(c["signal"] for c in ctx["cells"]) if family_of.get(s) == fam2]
if sigs:
    st.plotly_chart(charts.grid_heatmap(
        contexts, sigs, [[cc.get((s, k), {}).get("point") for k in contexts] for s in sigs],
        [[(lambda d, h: f"{d['point']:+.2f}<br>{h['point']:+.2f}" if d.get("point") is not None and h.get("point") is not None
           else "—")(cc.get((s, k), {}), hc.get((s, k), {})) for k in contexts] for s in sigs],
        f"Inside minus outside the context, five sessions ({fam2}): discovery colour, text discovery / holdout", "sd"),
        width="stretch", key="ctx_heat")
st.markdown(f"""
Each cell asks whether a signal works better inside a context than outside it — the classic filters (trade the
oversold dip only above the 200-day; ignore breakouts in a flat, low-ADX tape; respect VIX backwardation). The
placebo here shifts the *context* in time, so it asks whether the context's timing adds anything. Bar |t|
{ctx['placebo']['t95']:.1f}, best {ctx['placebo']['best']:.1f}: **no filter survives**, Connors' RSI(2)-above-the-200-day
included.
""")
pr = comb["pairs"]
pairs_p = sorted([p for p in ta.twins(pr) if p[0].get("point") is not None], key=lambda p: -abs(p[0].get("t") or 0))[:15]
labels, cs = paired(pairs_p, lambda c: f"{c['a']} + {c['b']}")
st.plotly_chart(charts.forest(labels, cs, f"Two families agreeing within {cat['recent']} sessions, against either alone",
                              0.0, "lift (sd, five sessions)"), width="stretch", key="pairs")
st.caption(f"Placebo shifts the second signal: bar |t| {pr['placebo']['t95']:.1f}. The one discovery finding "
           "(DI cross confirmed by an OBV divergence) is within noise after the split.")
cons = comb["consensus"]
for name, title in (("trend", "Trend states agreeing bullish (of 10)"), ("oscillator", "Oscillators: oversold minus overbought (of 9)")):
    rows = cons[name]["bins"]
    bins = list(dict.fromkeys(r["bin"] for r in rows))
    get = lambda p: [next(r["point"] for r in rows if r["bin"] == b and r["period"] == p) for b in bins]
    st.plotly_chart(charts.study_lines(bins, [("before the split", get("discovery"), "series_1", None),
                                              ("after", get("holdout"), "series_2", None)],
                                       f"{title}: excess five-session return", "count", "sd"),
                    width="stretch", key=f"cons_{name}")
tests = [t for n in cons for t in cons[n]["tests"]]
table(tests, {"index": lambda t: t["index"], "period": lambda t: t["period"], "top minus bottom bin": lambda t: iv(t),
              "verdict": verdict_col})

# ------------------------------------------------------------------ H
if lv:
    st.divider()
    st.header("H · Support and resistance levels")
    prm = lv["params"]
    st.markdown(f"""
Do the levels traders draw — floor pivots, the prior session's high, low and close, Fibonacci retracements of the
last 20 sessions, the prior session's volume profile (POC and 70 % value area), and the first hour's range — turn
price more often than an arbitrary price would? Each level is scored at its **first touch** in a session: the
first 5-minute bar whose range holds it (from 10:30 for the initial balance). Price coming from above makes it
support, from below resistance. Over the next {prm['window_bars'] * prm['bar_minutes']} minutes, reaching
{prm['band_sd']} daily sd back on the near side first is a **rejection** (+1), that far through first a **break**
(−1), and anything else scores the fraction of the way it ended.

The comparison is the point. A **placebo level** sits at the distance from the open (in daily sd) that the same
level had in another session a year or more away, so it is touched as often and approached the same way, but has
no history behind it — {prm['draws']} such sets make the baseline and the search bar. The statistic is the touch
score at real levels minus the score at placebo levels in the same weeks. Added in catalog v2, after section C–G's
results were read (`post_hoc` in the catalog); the definitions below were fixed before any level was scored.
""")
    table(cat.get("levels", []), {"level": lambda x: x["name"], "family": lambda x: x["group"], "era": lambda x: x["era"],
                                  "definition": lambda x: x["params"], "tested from": lambda x: x["start"]})
    grp_l = st.radio("Group  ", ["all", "etf", "single"], horizontal=True, key="lv_grp")
    lvc = {(c["signal"], c["target"], c["period"]): c for c in lv["cells"] if c["group"] == grp_l}
    names_l = list(dict.fromkeys(c["signal"] for c in lv["cells"]))
    sides_l = ["support", "resistance", "both"]
    st.plotly_chart(charts.grid_heatmap(
        sides_l, names_l, [[lvc.get((n, sd, "discovery"), {}).get("point") for sd in sides_l] for n in names_l],
        [[(lambda d, h: f"{d['point']:+.3f} → {h['point']:+.3f}" if d.get("point") is not None
           and h.get("point") is not None else "—")(lvc.get((n, sd, "discovery"), {}), lvc.get((n, sd, "holdout"), {}))
          for sd in sides_l] for n in names_l],
        f"Real minus placebo touch score ({grp_l}): discovery colour, text discovery → holdout", "barrier units"),
        width="stretch", key="lv_heat")
    pairs_l = [p for p in ta.twins(lv, group=grp_l, target="both") if p[0].get("point") is not None]
    labels, cs = paired(pairs_l, lambda c: c["signal"])
    st.plotly_chart(charts.forest(labels, cs, f"Does the level turn price more than a random one? Both sides, {grp_l}",
                                  0.0, "real minus placebo (barrier units)"), width="stretch", key="lv_forest")
    st.caption(f"Bar |t| {lv['placebo']['t95']:.2f} (a shuffled search's best is typically {lv['placebo']['t50']:.2f}). "
               "Positive: the level holds more often than a random price would.")
    table([o for o in lv["outcomes"] if o["period"] == "discovery"], {
        "level": lambda o: o["signal"],
        "touched (real / placebo)": lambda o: f"{o['real']['touch_rate']:.0%} / {o['placebo']['touch_rate']:.0%}",
        "rejects": lambda o: f"{o['real']['p_reject']:.1%} / {o['placebo']['p_reject']:.1%}",
        "breaks": lambda o: f"{o['real']['p_break']:.1%} / {o['placebo']['p_break']:.1%}",
        "neither": lambda o: f"{o['real']['p_timeout']:.1%} / {o['placebo']['p_timeout']:.1%}"})
    st.caption("Before the split, real / placebo. Touch rates match by construction; the outcome mix is the question.")
    st.markdown("""
**Most levels are indistinguishable from an arbitrary price, and the ones that differ mostly differ the wrong way.**
The floor pivot point is the one level that turns price a little more often than chance in both periods — through
fewer breaks rather than more rejections: price stalls there. The prior session's low and its close, tested from
above, *break* more often than a random level does, before and after 2023, and pooling every level as support
says the same. A level everyone can see is where stops sit, and a run through it is as likely as a bounce. Fibonacci
retracements and the volume profile are noise; the first hour's range looked like a break level before 2023 and
mostly is not after it.
""")

    st.subheader("Confirmed touches")
    co = lv["combos"]
    conf_names = [c["name"] for c in cat.get("confirmers", [])]
    cc_l = {(c["signal"], c["context"], c["period"]): c for c in co["cells"]}
    st.plotly_chart(charts.grid_heatmap(
        conf_names, names_l, [[cc_l.get((n, k, "discovery"), {}).get("point") for k in conf_names] for n in names_l],
        [[(lambda d, h: f"{d['point']:+.2f} → {h['point']:+.2f}" if d.get("point") is not None
           and h.get("point") is not None else "—")(cc_l.get((n, k, "discovery"), {}), cc_l.get((n, k, "holdout"), {}))
          for k in conf_names] for n in names_l],
        "Confirmed real level minus confirmed placebo level: discovery colour, text discovery → holdout",
        "barrier units"), width="stretch", key="lv_combo_heat")
    table([c for c in co["cells"] if c["signal"] == "all levels"], {
        "confirmer": lambda c: c["context"], "period": lambda c: c["period"],
        "touches confirmed": lambda c: f"{c['confirmed_share']:.0%}",
        "confirmer alone (random levels)": lambda c: f"{c['confirmer_alone']:+.3f}",
        "level adds, once confirmed": lambda c: iv(c, 3), "verdict": verdict_col})
    st.caption(f"Confirmers: " + "; ".join(f"**{c['name']}** — {c['reading']}" for c in cat.get("confirmers", []))
               + f". Bar |t| {co['placebo']['t95']:.2f}.")
    st.markdown("""
The test is a confirmed real level against a confirmed *random* level, because a confirmer can flatter any price:
a touch bar that already closed back on its side is a rejection half made (+0.37 at random levels), whatever the
level. Asked that way:

- **RSI confirmation is backwards.** An oversold touch of support (or overbought touch of resistance) is
  followed by more breaks at *any* price — short-term momentum — and by far more at a real level than a random one:
  worst at the prior close, the pivot point, the POC and the value area, every one of them in both periods. Price that arrives
  stretched at a level inside yesterday's range goes through it.
- **The rejection bar works everywhere and slightly less at real levels** than at random ones.
- **Volume, the daily trend and confluence add nothing** once the comparison is fair. (Confluence first looked
  like the strongest combination of all; it was counting the first hour's high and low as confluence before they
  existed — a look-ahead, removed.)
""")

# ------------------------------------------------------------------ G
st.divider()
st.header("G · Leads: discovery findings, judged once after the split")
st.markdown(f"**{leads['n']} discovery findings; {leads['survived']} keep their sign with an interval clear of zero "
            f"after {prov['split'][:4]}** (about {leads['expected_false']:.1f} would by chance).")
table(leads["rows"], {
    "family": lambda r: r["family"],
    "signal": lambda r: (r.get("signal") or f"{r.get('a')} + {r.get('b')}") + (f" + {r['context']}" if r.get("context") else ""),
    "target": lambda r: f"{r.get('target', '')} {r.get('horizon', '')}", "group": lambda r: r["group"],
    "before": lambda r: iv(r["discovery"]), "after": lambda r: iv(r["holdout"]), "after: verdict": lambda r: verdict_col(r["holdout"]),
    "CRPS vs HAR, after": lambda r: iv(r.get("crps"), 4), "CRPS verdict": lambda r: verdict_col(r.get("crps"))})

# ------------------------------------------------------------------ conclusions
st.divider()
st.header("What to do with it")
st.markdown("""
1. **Stop looking for direction in indicators.** A thousand daily cells and a decade of data found nothing a
   search does not find by chance, and the intraday effects that looked real before 2023 are gone after it. The
   ranker already orders candidates well; the forecast's directional term should stay at or near zero.
2. **Use stock-only vol where it helps: HAR-RV.** It beats trailing close-to-close in both periods, at one
   session and five, and needs no option chain. It is the honest vol for anything the option tape is too short
   to price, and the bar a vol signal must clear.
3. **Keep the earnings term, and size it from the tape.** An earnings release in the next five sessions is the
   one calendar input that adds to HAR out of period — about 1.5× HAR's vol, fit before 2023 and scored after.
   Ten years of releases (~40 a symbol) can now measure each symbol's move rather than the flat ×2.0 the
   vol-timing model uses.
4. **Treat VIX9D × ~0.77 as SPY's five-session vol prior,** and check the ATM-IV equivalent on the capture window
   before it touches `vol_multiplier`.
5. **Do not build entries or exits on drawn levels.** No level holds enough to stand on, and the classic
   confirmation (oversold at support) points the wrong way; if anything, an obvious prior low or close is where a
   test breaks. A level is a place to watch, not a reason to trade.
6. **Re-run when the backfill lands.** `uv run scripts/ta_study.py` rebuilds this page; the catalog is frozen,
   so the new symbols add evidence without adding search.
""")
