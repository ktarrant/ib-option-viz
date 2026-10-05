"""News Study: what the LLM headline scores and the macro calendar say about
moves, volatility and the forecaster.

The report of ib-spread-trader's `scripts/news_study.py`, rendered from its
`out/news_study.json`. The prose was written against one run
(`WRITTEN_AGAINST`); every chart, table and verdict is live, so a newer JSON
redraws the evidence and the page says when the prose has fallen behind it.
"""

from pathlib import Path

import polars as pl
import streamlit as st

from optionviz import news_study as ns
from optionviz.ui import charts

WRITTEN_AGAINST = "ib-option-calc-2026-10-04.sqlite3"

path = Path(st.sidebar.text_input("Study file", value=str(ns.DEFAULT)))
report = ns.load(path)
st.title("News study")
if report is None:
    st.info(f"No study at `{path}`. In ib-spread-trader: `python3 scripts/news_study.py`.")
    st.stop()

prov, S = report["provenance"], report["sections"]
cov, kinds, macro, sent, fcst, day = (S[k] for k in ("coverage", "kinds", "macro", "sentiment", "forecaster", "day_type"))
st.caption(
    f"Snapshot `{Path(prov['snapshot']).name}` · news cache {cov['scored_from'][:10]} → {cov['scored_to'][:10]} · "
    f"generated {prov['generated_at'][:16]} UTC at `{prov['commit']}` · {prov['draws']} placebo draws per search"
)
if Path(prov["snapshot"]).name != WRITTEN_AGAINST:
    st.warning(
        f"The text on this page was written against `{WRITTEN_AGAINST}`; the charts and verdicts come from "
        f"`{Path(prov['snapshot']).name}`. Where they disagree, believe the charts."
    )


def table(rows: list[dict], columns: dict) -> None:
    """A plain table: {header: function of row}."""
    st.dataframe(pl.DataFrame([{h: f(r) for h, f in columns.items()} for r in rows]),
                 hide_index=True, width="stretch")


def verdict_col(c: dict) -> str:
    return ns.label(c.get("verdict", ""))


singles = [s for s in cov["symbols"] if not s["etf"]]
usable, total = sum(s["usable_sessions"] for s in singles), sum(s["sessions"] for s in singles)
best_d = max((abs(c["t"]) for c in sent["cells"] if c["target"] in ("d1", "d5", "v1") and c["t"] is not None), default=0)

# ------------------------------------------------------------------ summary
st.subheader("The short answer")
st.markdown(f"""
- **The headline record has holes, and they shaped every earlier result.** IB returns only the newest 255
  headlines per provider and symbol, so a heavily covered name is complete only from late August or
  September. Inside the scored window, **{usable} of {total}** single-name sessions are usable; NVDA has
  {next(s['usable_sessions'] for s in singles if s['symbol'] == 'NVDA')}, META
  {next(s['usable_sessions'] for s in singles if s['symbol'] == 'META')}. Before this study, "no news" partly
  meant "headline never captured" — in `news_audit.py`, in the engine's sentiment gate, and in the quick tests
  that prompted this page.
- **Headlines describe the move already made, and nothing about their kind forecasts the next one.** News in
  the LLM's direction moves {ns.interval(ns.cells(kinds, axis='all', target='pre')[0])} sd inside its own window;
  the next close and the next five show nothing. No split by type, materiality, vol flag, horizon, source,
  arrival, attention or "priced in" survives the search. The five sessions after fresh news are *calmer*
  ({ns.interval(ns.cells(kinds, axis='all', target='d5_v')[0])}× a quiet bar), and the option market does not
  reprice on headlines at all.
- **The macro calendar has the effects the headlines lack.** Payroll days carry about 4× normal ETF variance,
  FOMC afternoons {macro['profile']['fomc_afternoon']['ratio']:.0f}×, and ETF ATM IV *falls*
  {-100 * macro['iv']['08:30 releases']['release_day']['mean']:.1f} % on 08:30 release days — an event premium
  the market prices and then releases, beyond every random-day draw.
- **The sentiment indicator, rebuilt {len({c['level'] for c in sent['cells'] if c['axis'] == 'grid'}) + 33} ways,
  forecasts nothing as it stands — because the LLM is optimistic, raw tone is almost always positive and its sign
  carries nothing.** Balanced against the symbol's own history or its event type's, short-memory tone leans toward
  next-day *continuation* (+0.1 to +0.2 sd, 6 of 7 weeks), and price momentum does not explain it. It does not
  clear the bar a search this wide sets on {sent['weeks']} weeks (best |t| {best_d:.1f} against
  {sent['placebo']['t95']:.1f}), so two variants are now **pre-registered** and every new snapshot tests them
  out of sample.
- **Offline, no news term improves the forecaster.** A tone-change drift *hurts* out of sample: its fitted sign
  flips between the two halves of the sample.
""")

with st.expander("How to read this page — method and verdicts"):
    st.markdown(f"""
**Coverage.** A row counts only when the record for its symbol is complete over its whole look-back
(`news_profile.complete_from`) and inside a scored stretch of the cache; a score is read only once its
30-minute window has closed. **Units.** Single-name moves are net of SPY, in the symbol's daily standard
deviation (sd); variance is a ratio to a comparable quiet bar or to the symbol's own average; realized ÷
implied uses the vol-timing run's ATM IV. Earnings releases are left out (−3 to +7 days) except where they
are the question.

**Intervals** are 95 % weekly-block bootstraps: stocks move together within a day and a 7-day horizon spans a
week, so ~9 weeks, not thousands of rows, is the sample. A statistic with fewer than 30 rows or 5 weeks has no
interval ("too few").

**Searching.** Testing hundreds of cells finds some by luck, so every searched family carries a **placebo**:
the same statistics with the news shuffled across symbols within each day (or moved to random non-release
days, for the calendar), {prov['draws']} times. A cell is a **finding** only when its interval excludes the
null *and* its |t| beats the placebo's 95th-percentile best. The bars this run set: kinds of news
{kinds['placebo']['directional_t95']:.1f} (directional) and {kinds['placebo']['vs_all_news_t95']:.1f}
(vs all news), sentiment variants {sent['placebo']['t95']:.1f}, day type {day['placebo']['t95']:.1f}. They are
high because a cluster t on 7–9 weeks has heavy tails.

| verdict | means |
|---|---|
| finding / improves | interval excludes the null, and beats the search's placebo bar where there was a search |
| hurts | a forecaster change that makes it worse, beyond noise |
| describes the move | a same-day link — news describing the move it sits on, not forecasting |
| search luck | interval excludes the null, but a placebo search turns up cells this strong by chance |
| unguarded | interval excludes the null, but no placebo here can null it (a row's own variance) |
| within noise / too few | interval includes the null / not enough rows or weeks |

Every hypothesis and grid was fixed before the run; the two sentiment **leads** were picked after it and are
judged from now on only on data after {sent['leads_from']}.
""")

# ------------------------------------------------------------------ A
st.divider()
st.header("A · Coverage, and the earlier findings inside it")
weeks = sorted({w for s in cov["symbols"] for w in s["headlines_by_week"]})
syms = [s["symbol"] for s in cov["symbols"]]
st.plotly_chart(charts.count_heatmap(
    [w[5:] for w in weeks], syms,
    [[s["headlines_by_week"].get(w, 0) for w in weeks] for s in cov["symbols"]],
    "Headlines sent to the LLM scorer, per week (traded symbols)"), width="stretch", key="cov")
st.caption(
    "NVDA's first three weeks of August are empty, and META, AAPL and AMZN run 5–10× busier once their record "
    "is complete. That is the cap, not a quiet market. The 08-28 snapshot holds no headlines, so nothing can "
    "fill the holes; daily syncs keep new ones from opening.")
table(cov["symbols"], {"symbol": lambda s: s["symbol"], "complete from": lambda s: (s["complete_from"] or "")[:10],
                       "usable sessions": lambda s: s["usable_sessions"], "in scored window": lambda s: s["sessions"]})

rows = S["recheck"]["rows"]
st.plotly_chart(charts.forest([f"{r['label']} · {r['population']}" for r in rows], rows,
                              "The earlier headline findings, without and with the coverage mask",
                              None, "estimate (null 0 for moves, 1 for ratios)"), width="stretch", key="recheck")
st.markdown("""
Every earlier result keeps its sign inside complete coverage; what changes is how sure it is.
News describes its own window's move (+0.10 → +0.12 sd) and the fifth close stays calm (0.71 → 0.76×).
**The one news result that had looked like a volatility signal — the rest of the session after vol-flagged news,
1.31× a quiet bar — falls to 1.21 [0.98, 1.42] once the missing headlines stop counting as quiet.** The 7-day
realized ÷ implied split by the vol flag widens (flagged 1.38, unflagged 0.73), but every interval still spans 1.
""")

# ------------------------------------------------------------------ B
st.divider()
st.header("B · Kinds of news")
st.markdown(f"""
{kinds['events']} fresh, non-repeat, material events over {kinds['weeks']} weeks, each labelled by its most
material effect. The targets, signed by the LLM's direction where they are moves: the window's own move, the rest
of the session, the next close, the fifth; variance against a quiet bar at the same time of day; next-session
realized ÷ implied; the change in ATM IV; and whether the next close continues the window's move.
""")
col1, col2 = st.columns([3, 1])
target = col1.selectbox("Target", list(kinds["targets"]), format_func=lambda k: kinds["targets"][k]["description"])
mode = col2.radio("Read as", ["absolute", "vs all news"], horizontal=True)
cs = [c for c in kinds["cells"] if c["target"] == target and (c["kind"] == mode or c["axis"] == "all")
      and c["point"] is not None]
null = kinds["targets"][target]["null"] if mode == "absolute" else 0.0
st.plotly_chart(charts.forest([f"{c['axis']}: {c['level']}" for c in cs], cs,
                              kinds["targets"][target]["description"], null, "estimate"), width="stretch", key="kinds")
st.markdown(f"""
**No split survives its search.** The strongest-looking cells — overnight news describing its window's move
(+0.22 sd), product news followed by the calmest week (0.46× a quiet bar at the fifth close), news the LLM
expects to last weeks *reversing* at the next close (−0.10 sd) — are what a shuffled label set produces by
luck; the bars were {kinds['placebo']['directional_t95']:.1f} and {kinds['placebo']['vs_all_news_t95']:.1f}.

The pre-registered hypotheses, one line each:
- **Weeks-horizon news keeps variance up to the fifth close** — no: it is followed by a *calmer* week (0.66×).
- **Rest-of-session variance rises with the vol flag** — elevated runs above all news, but only 11 `high`
  events exist; not testable yet.
- **Analyst actions continue** — next close +0.08 [−0.09, +0.47]: no.
- **M&A, legal/regulatory and guidance carry the most variance** — 13, 52 and 3 events; legal/regulatory runs
  *below* a quiet bar for the session (0.83×). No.
- **`market_recap` carries nothing** — it carries nothing, as a built-in placebo should.
- **"Priced in" news (tone and direction disagree) reverses its window's move** — +0.08 [−0.07, +0.22]: no.

Pooled over all news, two things hold as single tests: news describes its window's move, and the **next close
continues that move by {ns.interval(ns.cells(kinds, axis='all', target='rev')[0])} sd** — small, and the
opposite of the fade the mean-reversion model makes.
""")

# ------------------------------------------------------------------ C
st.divider()
st.header("C · Scheduled macro releases")
counts = {}
for e in macro["calendar"]:
    counts[e["kind"]] = counts.get(e["kind"], 0) + 1
st.markdown(
    f"{len(macro['calendar'])} releases on {len({e['day'] for e in macro['calendar']})} sessions from the "
    "snapshot's own calendar (" + ", ".join(f"{k} {v}" for k, v in counts.items()) + "). No headlines needed, "
    "so this section reads the whole stock tape, not just the scored weeks.")
table(macro["variance"], {
    "release": lambda c: c["group"], "scope": lambda c: c["scope"], "days": lambda c: c["days"],
    "variance × non-release day": lambda c: ns.interval(c),
    "random-day percentile": lambda c: f"{c['placebo_pct']:.2f}" if c["placebo_pct"] is not None else "—"})
st.caption("Close-to-close squared move against the symbol's own non-release mean. The percentile places the "
           "release days among 200 random sets of non-release days of the same size: 1.00 = above every one.")

prof = macro["profile"]
st.plotly_chart(charts.study_lines(
    prof["slots"], [("FOMC days", prof["fomc"], "series_2", None), ("08:30 release days", prof["early"], "series_1", None),
                    ("non-release days", prof["normal"], "muted", "dot")],
    "Mean |5-minute move| through the session, SPY / QQQ / IWM (bp)", "bar start (New York)", "bp"),
    width="stretch", key="profile")
st.caption(f"Opening gap: {prof['gap']['early']:.0f} bp on 08:30 release days, {prof['gap']['normal']:.0f} bp "
           f"otherwise, {prof['gap']['fomc']:.0f} bp on FOMC mornings. FOMC afternoons carry "
           f"{prof['fomc_afternoon']['ratio']:.1f}× the variance of a normal afternoon.")

iv = macro["iv"]
st.plotly_chart(charts.study_lines(
    iv["fomc"]["k"], [("FOMC", iv["fomc"]["mean"], "series_2", None), ("08:30 releases", iv["08:30 releases"]["mean"], "series_1", None),
                      ("non-release days", iv["non-release"]["mean"], "muted", "dot")],
    "ETF ATM IV around a release, log change from the prior close", "sessions from the release (0 = release day)",
    "log change", ".1%"), width="stretch", key="iv")
rd = iv["08:30 releases"]["release_day"]
st.caption(f"On 08:30 release days IV changes {rd['mean']:+.1%} over the session ({rd['n']} ETF-days; random-day "
           f"percentile {rd['placebo_pct']:.2f}). The FOMC line is two events — read it as a picture, not a result. "
           "Same chain only; IV drifts down through the whole sample, hence the slope of every line.")

cs = macro["implied"]
st.plotly_chart(charts.forest(
    [f"{c['scope']}: {c['release']} {'inside' if c['inside'] else 'not inside'} the next {c['horizon']}" for c in cs], cs,
    "Realized ÷ implied when a release falls inside the horizon (× the symbol's own)", 1.0, "ratio"),
    width="stretch", key="implied")

trades = macro["condor_trades"]
if trades:
    fomc_t = [t for t in trades if t["fomc"]]
    rest = [t for t in trades if not t["fomc"]]
    st.markdown(f"**The skewed condor's {len(trades)} trades:** {len(fomc_t)} held across an FOMC made "
                f"${sum(t['pnl'] for t in fomc_t):,.0f} (${sum(t['pnl'] for t in fomc_t) / max(1, len(fomc_t)):,.0f} each); "
                f"{len(rest)} that were not made ${sum(t['pnl'] for t in rest):,.0f} "
                f"(${sum(t['pnl'] for t in rest) / max(1, len(rest)):,.0f} each). Descriptive only.")

mn = macro["market_news"]
table(mn["cells"], {
    "market-wide news": lambda c: "LLM intensity (materiality per covered symbol)" if c["feature"] == "intensity" else "macro headlines",
    "target": lambda c: {"spy_same": "SPY variance, same session", "spy_next": "SPY variance, next session",
                         "disp_next": "cross-sectional dispersion, next session"}[c["target"]],
    "top third − bottom third": lambda c: f"{c['diff']:+.2f}",
    "shuffle percentile": lambda c: f"{c['placebo_pct']:.2f}"})
st.markdown(f"""
**Payrolls are the volatile day in this sample, not CPI.** Payroll sessions run about 4× an ETF's normal variance
and 1.9× a single name's, both beyond nearly every random draw; CPI days were ordinary (about 1×), and retail-sales
days were calm for the ETFs. An 08:30 release barely widens the opening gap on average. **FOMC is an afternoon event** — the 14:00
bar alone moves ten times a normal one — on three meetings.

**The option market prices the release and lets it go.** ETF ATM IV falls on 08:30 release days by more than on
any of 200 random sets of days: a small event premium, released when the number prints. That is the same
mechanism as the earnings crush, at a smaller scale and on a schedule known months ahead.

**The LLM's market-wide news volume only describes the day it sits on** (SPY variance, top third of news days
against the bottom: high percentile same session, nothing next session) — the same pattern as single names.
""")

# ------------------------------------------------------------------ D
st.divider()
st.header("D · The sentiment indicator, rebuilt")
b = sent["base"]
st.markdown(f"""
The base is the indicator on the Chart page: every fresh effect's materiality × sentiment, decayed with a
{b['half']} h half-life on the calendar clock, read as a tone (that sum over the summed materiality). Each sweep
changes one thing; the grid crosses the two named first — the period and the optimism balancing. Rows are
{sent['rows']} symbol-days of {sent['symbols']} single names over {sent['weeks']} weeks, decided at the close
with news cut just before it. Targets: the next close and the next five in the indicator's direction, next-day
variance when the indicator is extreme, and — for contrast — the same day's move.
""")
tg = st.radio("Grid target", ["d1", "d5", "same"], horizontal=True,
              format_func=lambda k: sent["targets"][k]["description"].split(" (")[0])
grid = {c["level"]: c for c in sent["cells"] if c["axis"] == "grid" and c["target"] == tg}
halves, bals = sent["grid"]["half"], sent["grid"]["balance"]
z = [[grid[f"{h}h × {bl}"]["point"] for bl in bals] for h in halves]
txt = [[(lambda c: f"{c['point']:+.2f}" + (f"<br>t {c['t']:+.1f}" if c["t"] is not None else ""))(grid[f"{h}h × {bl}"])
        for bl in bals] for h in halves]
st.plotly_chart(charts.grid_heatmap(bals, [f"{h} h" for h in halves], z, txt,
                                    f"Half-life × optimism balancing — {sent['targets'][tg]['description']}", "sd"),
                width="stretch", key="grid")

tg2 = st.selectbox("Sweep target", list(sent["targets"]), format_func=lambda k: sent["targets"][k]["description"])
cs = [c for c in sent["cells"] if c["axis"] != "grid" and c["target"] == tg2]
st.plotly_chart(charts.forest([f"{c['axis']}: {c['level']}" for c in cs], cs,
                              f"One axis at a time from the base — {sent['targets'][tg2]['description']}",
                              sent["targets"][tg2]["null"], "estimate"), width="stretch", key="sweeps")

st.subheader("Leads, pre-registered for the next snapshots")
table(sent["leads"], {"variant": lambda c: c["lead"], "rows": lambda c: c["label"],
                      "next close in its direction (sd)": lambda c: ns.interval(c), "n": lambda c: c["n"],
                      "verdict": verdict_col})
st.markdown(f"""
**Short memory describes; long memory, if anything, fades.** The same-day link is strongest at 2–24 h
(+0.16 to +0.25 sd) and gone by a week; nothing forecasts at any period, clock, weighting, floor, signal or
transform. A week-long memory leans slightly *against* the next five closes — the contrarian lean the tone level
showed before — and the trading clock the same; both are search luck.

**Optimism balancing is where the one pattern lives.** Raw tone is positive on nearly every row (the LLM reads
most news as good), so its sign is uninformative and the raw row of the grid is flat. Subtract the symbol's own
recent tone, or its event type's running mean, and short-memory tone leans toward next-day continuation —
+0.11 sd at 12 h by event type (6 of 7 weeks, 7 of 11 symbols), +0.20 sd at 6 h by symbol z-score (6 of 6
weeks, 6 of 8 symbols). Two checks that it is news and not price: the day's own move on the same rows does next
to nothing (−0.04 and +0.05 sd); and the event-type variant is strongest when the tone **opposes** the day's
move — a stock that fell on news the LLM rates better than usual for its kind bounces.

It is still one corner of a {len(grid) + 33}-variant search on {sent['weeks']} weeks, and the search bar was
{sent['placebo']['t95']:.1f}. So the two variants are fixed now and judged only on data after
{sent['leads_from']} — the "out of sample" rows above, empty until the next snapshot lands. A single
pre-registered test needs only its own interval, so a few weeks can settle it either way.
""")

# ------------------------------------------------------------------ E
st.divider()
st.header("E · Does any of it improve the forecaster?")
st.markdown(f"""
Offline, on the vol-timing run's {fcst['rows']:,} single-name forecasts inside complete coverage: rescale or
shift its log-normal by a news term, fit on one half of the weeks, score the other, swap. Skill is the CRPS saved
against the unmodified model, every row out of sample.
""")
table(fcst["tests"], {"test": lambda c: c["test"] + (" (post hoc)" if c.get("post_hoc") else ""),
                      "news term": lambda c: c["label"], "CRPS saved": lambda c: ns.interval(c, 4),
                      "verdict": verdict_col, "fitted (first half → second half)": lambda c: str(c["params"])})
table(fcst["skill"], {"model": lambda c: c["run"].replace("_all", ""), "bars": lambda c: c["bucket"],
                      "CRPS skill vs null": lambda c: ns.interval(c, 4), "n": lambda c: c["n"], "verdict": verdict_col})
st.markdown("""
**Nothing improves it.** A vol multiplier by news state fits 0.81 / 1.07 / 1.42 (unflagged / none / flagged) on
the first half and 1.04 / 1.13 / 1.11 on the second — the "flagged news means bigger moves" shape does not
survive the turn of the sample. The tone-change drift is worse: its β is +0.50 on one half and −0.74 on the other,
so it **hurts** out of sample. The payrolls multiplier for ETFs (post hoc, from section C) is within noise: the
7-day monthly-IV horizon dilutes a one-day event.

The directional models' own skill, split by news: the mean-reversion fade does better after a big move with
**no** news (+0.031) than with it (+0.004) — the direction Chan (2003) predicts, news moves drift and no-news
moves revert — but every interval spans zero.
""")

# ------------------------------------------------------------------ F
st.divider()
st.header("F · Does morning news make a trend day?")
cs = [c for c in day["cells"] if c["kind"] == "vs no news"]
st.plotly_chart(charts.forest([f"{c['outcome']} day, {c['label']} news" for c in cs], cs,
                              "Share of trend / inside days, against days with no news before 10:30", 0.0,
                              "difference in share"), width="stretch", key="daytype")
share = {(c["outcome"], c["label"]): c for c in day["cells"] if c["kind"] == "share"}
st.markdown(f"""
{day['rows']} single-name sessions. Trend days — closing a full initial-balance width beyond it — are rare
({share['trend', 'none']['point']:.0%} with no morning news), and vol-flagged news before 10:30 does not make them
commoner ({share['trend', 'flagged']['point']:.0%}); it makes a close back inside the range slightly rarer
({share['inside', 'flagged']['point']:.0%} against {share['inside', 'none']['point']:.0%}), within noise. The
same-session variance the vol flag once promised was itself not robust to the coverage mask (section A).
""")

# ------------------------------------------------------------------ conclusions
st.divider()
st.header("What to do with it")
st.markdown("""
1. **Keep headline news out of the forecaster for now.** No news term improved it out of sample, and the
   directional ones flipped sign between halves of the sample.
2. **Use the macro calendar instead.** It is already in the snapshot and needs no LLM: scheduled releases are
   where ETF variance and an IV event premium actually show up. A release term in the vol-timing model —
   payroll-day variance, the 08:30 premium released at the print — is the concrete next step, and its own
   model label.
3. **Make every news reader respect coverage.** The engine's sentiment gate and `news_audit.py` read a time
   before a symbol's complete record as quiet.
4. **Track the two leads.** Re-run `scripts/news_study.py` on each weekly snapshot; the out-of-sample rows in
   section D fill in, and a single pre-registered test needs no search penalty.
5. **Let the sample grow.** Each week adds about one week to every interval here; daily syncs keep the
   record complete from now on.
""")
