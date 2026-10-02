"""The option chain board: calls | strike, IV | puts, three expiries a side.

Each cell is a contract's mid, tinted by its move since the previous bar —
green up, red down, stronger the bigger the move — with a hover card giving
the contract's quote, vol and both changes (previous bar, prior session close).

Plain HTML rather than `st.dataframe`: the data grid has no per-cell tooltips,
which are the point here, and a chain needs no sorting.
"""

from __future__ import annotations

import polars as pl

from . import theme

#: A move this size (either way) in one bar paints at full strength.
FULL_AT = 0.5
SLOTS = ("weekly", "monthly", "next monthly")


def _index(chain: pl.DataFrame) -> dict:
    """(expiry, strike) -> row, for one `surface.chain_at` frame."""
    if not chain.height:
        return {}
    return {(r["expiry"], r["strike"]): r for r in chain.iter_rows(named=True)}


def _tint(pct: float | None) -> str:
    if pct is None or abs(pct) < 0.005:
        return ""
    p = theme.palette()
    # Square root, so a 5% move still shows while a 50% one is unmistakable.
    alpha = 0.6 * min(abs(pct) / FULL_AT, 1.0) ** 0.5
    return f"background:{theme.rgba(p.good if pct > 0 else p.critical, round(alpha, 3))};"


def _change_line(label: str, mid: float, then: dict | None, side: str) -> str:
    ref = then and then.get(f"{side}_mid")
    if not ref:
        return f"<div><b>{label}</b> &nbsp;—</div>"
    d = mid - ref
    return (
        f"<div><b>{label}</b> &nbsp;{d / ref:+.1%} &nbsp;"
        f"{'+' if d >= 0 else '−'}${abs(d) * 100:,.0f} per contract"
        f"<span class=m> &nbsp;from {ref:,.2f} at {then['ts']:%b %d %H:%M}</span></div>"
    )


def board_html(
    symbol: str,
    now: pl.DataFrame,
    prev_bar: pl.DataFrame,
    prev_close: pl.DataFrame,
    slots: dict,
    strikes_each_side: int | None = 10,
) -> str:
    """`now`, `prev_bar` and `prev_close` are `surface.chain_at` at the clicked
    bar's close, its open and the start of its session; `slots` is
    `surface.expiry_slots`. `strikes_each_side=None` shows every strike."""
    p = theme.palette()
    cur, bar0, sess0 = _index(now), _index(prev_bar), _index(prev_close)
    expiries = {name: slots[name][1] for name in SLOTS}
    shown = [e for e in expiries.values() if e]

    strikes = sorted({k for (e, k) in cur if e in shown})
    # The money line and the middle IV follow the front monthly, else whatever is there.
    ref_expiry = expiries["monthly"] if expiries["monthly"] in shown else (shown or [None])[0]
    ref_rows = [r for (e, _), r in cur.items() if e == ref_expiry]
    forward = ref_rows[0]["forward"] if ref_rows else None
    if strikes_each_side and forward is not None:
        at = sum(k < forward for k in strikes)
        strikes = strikes[max(0, at - strikes_each_side): at + strikes_each_side]

    def cell(name: str, strike: float, side: str, upper: bool) -> str:
        expiry = expiries[name]
        row = cur.get((expiry, strike))
        mid = row and row.get(f"{side}_mid")
        if not mid:
            return "<td class=x>—</td>"
        then = bar0.get((expiry, strike))
        ref = then and then.get(f"{side}_mid")
        pct = mid / ref - 1 if ref else None
        delta, iv = row.get(f"{side}_delta"), row.get("iv")
        card = (
            f"<div class=pop style='{'top' if upper else 'bottom'}:100%;"
            f"{'left' if side == 'call' else 'right'}:0'>"
            f"<div class=t>{symbol} {expiry} {strike:g} {'Call' if side == 'call' else 'Put'}</div>"
            f"<div>Mid <b>{mid:,.2f}</b><span class=m> &nbsp;bid {row[f'{side}_bid']:,.2f}"
            f" / ask {row[f'{side}_ask']:,.2f}</span></div>"
            f"<div>IV {'—' if iv is None else f'{iv:.1%}'} &nbsp;Δ "
            f"{'—' if delta is None else f'{delta:+.2f}'}"
            f"<span class=m> &nbsp;bar closing {row['ts']:%b %d %H:%M} UTC</span></div>"
            + _change_line("vs previous bar", mid, then, side)
            + _change_line("vs prior close", mid, sess0.get((expiry, strike)), side)
            + "</div>"
        )
        return f"<td class=c style='{_tint(pct)}'>{mid:,.2f}{card}</td>"

    def head(name: str) -> str:
        day, expiry = slots[name]
        tag = {"weekly": "wk", "monthly": "mo", "next monthly": "mo+1"}[name]
        if expiry:
            return f"<th>{day:%b %d} <span class=m>{tag}</span></th>"
        return f"<th class=x title='Not in the capture'>{day:%b %d} <span class=m>{tag}</span></th>"

    calls_order = list(reversed(SLOTS))  # mirrored: the weekly sits beside the strike
    rows = []
    for i, k in enumerate(strikes):
        upper = i < len(strikes) / 2  # cards open downward up top, upward below
        money = forward is not None and k >= forward and (i == 0 or strikes[i - 1] < forward)
        iv = (cur.get((ref_expiry, k)) or {}).get("iv")
        rows.append(
            f"<tr{' class=fwd' if money else ''}>"
            + "".join(cell(n, k, "call", upper) for n in calls_order)
            + f"<td class=k>{k:g}</td><td class=iv>{'—' if iv is None else f'{iv:.1%}'}</td>"
            + "".join(cell(n, k, "put", upper) for n in SLOTS)
            + "</tr>"
        )

    style = f"""
    <style>
      .chain {{ border-collapse: collapse; width: 100%; font-size: 13px;
               font-variant-numeric: tabular-nums; color: {p.text_primary}; }}
      .chain th, .chain td {{ padding: 4px 10px; text-align: right;
               border-bottom: 1px solid {p.grid}; white-space: nowrap; }}
      .chain th {{ font-weight: 600; color: {p.text_secondary}; }}
      .chain .side {{ text-align: center; color: {p.text_primary}; }}
      .chain .k {{ text-align: center; font-weight: 600; }}
      .chain .k, .chain .iv {{ background: {theme.rgba(p.muted, 0.08)}; }}
      .chain .iv {{ color: {p.text_secondary}; }}
      .chain .x {{ color: {p.muted}; }}
      .chain .m {{ color: {p.muted}; font-weight: 400; }}
      .chain tr.fwd td {{ border-top: 2px solid {p.series_1}; }}
      .chain td.c {{ position: relative; cursor: default; }}
      .chain td.c:hover {{ outline: 1px solid {p.text_secondary}; }}
      .chain .pop {{ display: none; position: absolute; z-index: 20; text-align: left;
               background: {p.surface}; border: 1px solid {p.axis}; border-radius: 6px;
               padding: 8px 10px; font-size: 12px; line-height: 1.6;
               box-shadow: 0 4px 14px rgba(0,0,0,.18); }}
      .chain td.c:hover .pop {{ display: block; }}
      .chain .pop .t {{ font-weight: 600; margin-bottom: 2px; }}
    </style>"""
    return (
        style
        + "<table class=chain><thead>"
        + "<tr><th colspan=3 class=side>Calls</th><th class=k>Strike</th>"
        + "<th class=iv>IV <span class=m>"
        + (f"{ref_expiry[5:].replace('-', '/')}" if ref_expiry else "") + "</span></th>"
        + "<th colspan=3 class=side>Puts</th></tr>"
        + "<tr>" + "".join(head(n) for n in calls_order) + "<th class=k></th><th class=iv></th>"
        + "".join(head(n) for n in SLOTS) + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
