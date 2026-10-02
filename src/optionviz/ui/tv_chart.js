// The Chart page's price chart. Built by tv_chart.py, which documents the
// time and position conventions; this file only draws what it is given.
//
// Streamlit calls the default export again whenever `data` or the theme
// changes, with the same parentElement and no cleanup in between (cleanup runs
// only on unmount). So one chart lives per element and is updated in place,
// which is what keeps the viewer's pan and zoom across reruns.

// ponytail: pinned CDN build; vendor the .mjs if the app must run offline.
const LWC = import('https://cdn.jsdelivr.net/npm/lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.mjs');

const RIGHT = 6;  // empty bars kept right of the last one, as TradingView does
const PANE_HEIGHT = 90;
const PRICE_HEIGHT = 460;
const states = new WeakMap();

const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const num = (v, d = 2) => v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });

function rgba(hex, a) {
  const h = hex.replace('#', '');
  return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`;
}

// ------------------------------------------------------------------ primitives
// Each is attached to the candle series and draws in media (CSS pixel)
// coordinates. x positions are fractional bar indices from tv_chart.py.

class Primitive {
  constructor() { this._views = [{ zOrder: () => this.zOrder, renderer: () => ({ draw: t => t.useMediaCoordinateSpace(s => this.draw(s.context, s.mediaSize)) }) }]; }
  attached({ chart, series, requestUpdate }) { this.chart = chart; this.series = series; this.requestUpdate = requestUpdate; }
  detached() { this.requestUpdate = null; }
  paneViews() { return this._views; }
  updateAllViews() {}
  set(v, pal) { this.v = v; this.pal = pal; this.requestUpdate?.(); }
  // logicalToCoordinate is only right on whole bar indices — a fractional one
  // lands far off — so between bars the position is interpolated here.
  x(logical) {
    const ts = this.chart.timeScale(), i = Math.floor(logical);
    const a = ts.logicalToCoordinate(i), b = ts.logicalToCoordinate(i + 1);
    return a === null || b === null ? null : a + (logical - i) * (b - a);
  }
  spacing() {
    const ts = this.chart.timeScale();
    return ts.logicalToCoordinate(1) - ts.logicalToCoordinate(0);
  }
  y(price) { return this.series.priceToCoordinate(price); }
}

class Trades extends Primitive {
  zOrder = 'normal';
  hovered = null;
  hover(id) { if (id !== this.hovered) { this.hovered = id; this.requestUpdate?.(); } }
  autoscaleInfo(from, to) {
    // Every trade in view widens the axis to its strikes and forecast bounds.
    let lo = Infinity, hi = -Infinity;
    for (const t of this.v || []) {
      if (t.x1 < from || t.x0 > to) continue;
      for (const p of [...t.legs.map(l => l.k), t.target, t.stop]) {
        if (p == null) continue;
        lo = Math.min(lo, p); hi = Math.max(hi, p);
      }
    }
    return lo <= hi ? { priceRange: { minValue: lo, maxValue: hi } } : null;
  }
  hitTest(x, y) {
    for (const t of [...(this.v || [])].reverse()) {
      const x0 = this.x(t.x0), x1 = this.x(t.x1);
      if (x0 === null || x1 === null || x < x0 - 3 || x > x1 + 3) continue;
      const near = t.legs.some(l => { const py = this.y(l.k); return py !== null && Math.abs(py - y) <= 5; });
      const inside = t.zones.some(z => {
        const a = this.y(z.lo), b = this.y(z.hi);
        return a !== null && b !== null && y >= Math.min(a, b) && y <= Math.max(a, b);
      });
      if (near || inside) return { externalId: `t${t.id}`, zOrder: 'normal', cursorStyle: 'pointer' };
    }
    return null;
  }
  draw(ctx) {
    const p = this.pal, color = { C: p.series_1, P: p.series_2 };
    const focus = this.hovered ?? (this.v || []).find(t => t.selected)?.id;
    for (const t of this.v || []) {
      const x0 = this.x(t.x0), x1 = this.x(t.x1);
      if (x0 === null || x1 === null) continue;
      const strong = focus == null || t.id === focus;
      const alpha = strong ? 1 : 0.3;
      for (const z of t.zones) {
        const a = this.y(z.lo), b = this.y(z.hi);
        if (a === null || b === null) continue;
        ctx.fillStyle = rgba(color[z.right], strong ? 0.14 : 0.05);
        ctx.fillRect(x0, Math.min(a, b), x1 - x0, Math.abs(b - a));
      }
      for (const l of t.legs) {
        const py = this.y(l.k);
        if (py === null) continue;
        ctx.strokeStyle = rgba(color[l.right] || p.muted, alpha);
        ctx.lineWidth = l.qty > 0 ? 2.5 : 1.5;
        ctx.setLineDash(l.qty > 0 ? [] : [6, 4]);
        ctx.beginPath(); ctx.moveTo(x0, py); ctx.lineTo(x1, py); ctx.stroke();
      }
      ctx.setLineDash([]);
      if (!t.selected) continue;
      // The selected trade says what it is: each leg in its own notation, calls
      // above the line and puts below so a shared strike doesn't collide, and
      // the forecast's target (green) and stop (red) across the holding window.
      ctx.font = `11px ${this.chart.options().layout.fontFamily}`;
      ctx.textAlign = 'left';
      for (const l of t.legs) {
        const py = this.y(l.k);
        if (py === null) continue;
        ctx.fillStyle = color[l.right] || p.muted;
        ctx.textBaseline = l.right === 'P' ? 'top' : 'bottom';
        ctx.fillText(l.text, x1 + 4, py + (l.right === 'P' ? 2 : -2));
      }
      for (const [price, c, name] of [[t.target, p.good, 'target'], [t.stop, p.critical, 'stop']]) {
        const py = price == null ? null : this.y(price);
        if (py === null) continue;
        ctx.strokeStyle = c; ctx.lineWidth = 1.5; ctx.setLineDash([2, 3]);
        ctx.beginPath(); ctx.moveTo(x0, py); ctx.lineTo(x1, py); ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle = c; ctx.textBaseline = 'middle';
        ctx.fillText(`${name} ${num(price)}`, x1 + 4, py);
      }
    }
  }
}

// The picked bar: a full-height column, so the bar the option chain was read
// at stays marked while the chart scrolls.
class Selection extends Primitive {
  zOrder = 'bottom';
  draw(ctx, size) {
    if (this.v == null) return;
    const x = this.x(this.v);
    if (x === null) return;
    const w = Math.max(this.spacing(), 3);
    ctx.fillStyle = rgba(this.pal.accent, 0.14);
    ctx.fillRect(x - w / 2, 0, w, size.height);
  }
}

// The model's forecast at the picked bar: q05-q95 and q25-q75 fans from spot to
// the horizon, the median dashed, and where price actually landed as a dot.
class Fan extends Primitive {
  zOrder = 'normal';
  autoscaleInfo(from, to) {
    const f = this.v;
    if (!f || f.x1 < from || f.x0 > to) return null;
    return { priceRange: { minValue: Math.min(f.q05, f.realized ?? f.q05), maxValue: Math.max(f.q95, f.realized ?? f.q95) } };
  }
  draw(ctx) {
    const f = this.v;
    if (!f) return;
    const x0 = this.x(f.x0), x1 = this.x(f.x1), y0 = this.y(f.spot);
    if (x0 === null || x1 === null || y0 === null) return;
    const ink = this.pal.text_secondary;
    for (const [lo, hi, a] of [[f.q05, f.q95, 0.10], [f.q25, f.q75, 0.16]]) {
      ctx.fillStyle = rgba(ink, a);
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, this.y(hi)); ctx.lineTo(x1, this.y(lo)); ctx.closePath(); ctx.fill();
    }
    ctx.strokeStyle = rgba(ink, 0.8); ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, this.y(f.q50)); ctx.stroke();
    ctx.setLineDash([]);
    if (f.realized != null) {
      ctx.fillStyle = this.pal.text_primary;
      ctx.beginPath(); ctx.arc(x1, this.y(f.realized), 3.5, 0, 2 * Math.PI); ctx.fill();
    }
  }
}

// ------------------------------------------------------------------ mount
function mount(L, root, component) {
  const wrap = root.querySelector('.wrap');
  const el = root.querySelector('.chart');
  const s = {
    L, root, wrap, el, component, legend: root.querySelector('.legend'),
    latest: root.querySelector('.latest'), panes: [], panesKey: '', range: null,
  };
  const chart = s.chart = L.createChart(el, {
    autoSize: true,
    layout: { background: { color: 'transparent' }, fontSize: 11 },
    timeScale: { rightOffset: RIGHT, secondsVisible: false },
    rightPriceScale: { scaleMargins: { top: 0.08, bottom: 0.22 } },
    crosshair: { mode: L.CrosshairMode.Normal },
  });
  s.candles = chart.addSeries(L.CandlestickSeries, { priceFormat: { type: 'price', precision: 2, minMove: 0.01 } });
  // Volume sits in the bottom fifth of the price pane on its own hidden
  // scale, as TradingView draws it.
  s.volume = chart.addSeries(L.HistogramSeries, {
    priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false,
  });
  chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });
  s.markers = L.createSeriesMarkers(s.candles, []);
  for (const [k, P] of [['selection', Selection], ['trades', Trades], ['fan', Fan]]) {
    s[k] = new P(); s.candles.attachPrimitive(s[k]);
  }

  const trigger = (name, value) => s.component.setTriggerValue(name, value);
  const hoveredId = p => p.hoveredInfo?.objectId ?? p.hoveredObjectId;

  chart.subscribeCrosshairMove(p => {
    const id = hoveredId(p);
    s.trades.hover(typeof id === 'string' && id.startsWith('t') ? parseInt(id.slice(1)) : null);
    legend(s, p.logical === undefined ? null : Math.round(p.logical), id);
  });
  chart.subscribeClick(p => {
    const id = hoveredId(p);
    if (typeof id === 'string' && id.startsWith('t')) return trigger('trade', parseInt(id.slice(1)));
    const i = p.logical === undefined ? -1 : Math.round(p.logical);
    if (i >= 0 && i < s.data.keys.length) trigger('pick', s.data.keys[i]);
    wrap.focus({ preventScroll: true });
  });
  chart.timeScale().subscribeVisibleLogicalRangeChange(r => {
    if (!r || !s.data) return;
    const keys = s.data.keys, n = keys.length;
    const at = v => keys[Math.min(n - 1, Math.max(0, Math.round(v)))];
    s.range = { from: at(r.from), to: r.to >= n - 1 ? null : at(r.to) };  // null: pinned to the latest bar
    s.latest.style.visibility = r.to < n - 2 ? 'visible' : 'hidden';
  });

  // ←/→ step the picked bar, Esc clears it. Shift+←/→ is the page's: it
  // steps the run's trades.
  wrap.addEventListener('keydown', e => {
    const sel = s.data?.selected;
    if (e.key === 'Escape') { trigger('clear', true); return; }
    if (sel == null || e.shiftKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    const i = sel + (e.key === 'ArrowRight' ? 1 : -1);
    if (i >= 0 && i < s.data.keys.length) { trigger('pick', s.data.keys[i]); e.preventDefault(); }
  });

  // A range button's choice is kept until the viewer scrolls or zooms by hand:
  // a new symbol or timeframe then gets the same choice (All stays All), where
  // a hand-picked window carries over as the same dates.
  s.buttons = [];
  for (const [label, sessions, days] of [['1D', 1], ['5D', 5], ['1M', null, 30], ['3M', null, 91], ['All']]) {
    const b = document.createElement('button');
    b.textContent = label;
    b.onclick = () => { setPreset(s, { sessions, days, button: b }); showRange(s, s.preset); };
    root.querySelector('.ranges').appendChild(b);
    s.buttons.push(b);
  }
  s.latest.onclick = () => { setPreset(s, null); chart.timeScale().scrollToRealTime(); };
  wrap.addEventListener('wheel', () => setPreset(s, null), { passive: true });
  wrap.addEventListener('pointerdown', e => { s.down = [e.clientX, e.clientY]; });
  wrap.addEventListener('pointermove', e => {
    // A drag pans or stretches an axis; a click (no movement) only picks a bar.
    if (e.buttons && s.down && Math.hypot(e.clientX - s.down[0], e.clientY - s.down[1]) > 3) setPreset(s, null);
  });
  return s;
}

function setPreset(s, preset) {
  s.preset = preset;
  for (const b of s.buttons) b.classList.toggle('on', b === preset?.button);
}

// The last `sessions` trading days, or the last `days` calendar days, or all.
function showRange(s, { sessions, days }) {
  const keys = s.data.keys, n = keys.length, ts = s.chart.timeScale();
  if (!sessions && !days) return ts.fitContent();
  let i = n - 1;
  if (sessions) {
    let seen = 1;
    while (i > 0 && (keys[i - 1].slice(0, 10) === keys[i].slice(0, 10) || ++seen <= sessions)) i--;
  } else {
    const cutoff = Date.parse(keys[n - 1]) - days * 864e5;
    i = Math.max(0, keys.findIndex(k => Date.parse(k) >= cutoff));
  }
  ts.setVisibleLogicalRange({ from: i - 0.5, to: n - 1 + RIGHT });
}

function legend(s, i, hoverId) {
  const d = s.data;
  if (i === null || i < 0 || i >= d.bars.length) i = d.selected ?? d.bars.length - 1;
  const b = d.bars[i], prev = d.bars[i - 1], p = s.pal;
  const lines = [];
  let top = `<b>${esc(d.title)}</b> &nbsp;<span class=m>O</span> ${num(b.open)} <span class=m>H</span> ${num(b.high)} `
    + `<span class=m>L</span> ${num(b.low)} <span class=m>C</span> ${num(b.close)}`;
  if (prev) {
    const ch = b.close / prev.close - 1;
    top += ` <span style="color:${ch >= 0 ? p.good : p.critical}">${ch >= 0 ? '+' : ''}${(ch * 100).toFixed(2)}%</span>`;
  }
  if (d.volume) top += ` &nbsp;<span class=m>Vol</span> ${compact.format(d.volume[i])}`;
  lines.push(top);
  const id = typeof hoverId === 'string' ? hoverId : null;
  const trade = id?.startsWith('t') && d.trades.find(t => t.id === parseInt(id.slice(1)));
  if (trade) lines.push(trade.label);
  else if (id && d.notes[id]) lines.push(d.notes[id]);
  else if (d.open_at?.[i]) lines.push(d.open_at[i]);
  // Overlay values in text ink, each keyed by a swatch of its line's color.
  const vals = [
    ...d.overlays.map(o => o.values[i] == null ? null : `<span style="color:${p[o.color]}">━</span> <span class=m>${esc(o.label)}</span> ${num(o.values[i])}`),
    ...d.panes.map(pn => pn.values[i] == null ? null : `<span class=m>${esc(pn.label)}</span> ${num(pn.values[i], pn.decimals)}${pn.unit}`),
  ].filter(Boolean);
  if (vals.length) lines.push(vals.join(' &nbsp;·&nbsp; '));
  if (d.fan) lines.push(`<span class=m>${d.fan.legend}</span>`);
  s.legend.innerHTML = lines.join('<br>');
}

// Model-input panes, rebuilt only when the set of panes or the bars change.
// A line's points, one per bar. A line joins across empty points, which would
// draw a gap in the readings as a smooth move; a point's color paints the
// segment leaving it, so the last reading before a gap — or, with `sessions`,
// before a session opens — is made transparent.
function lineData(d, values, sessions) {
  return d.bars.map((b, i) => {
    const v = values[i];
    if (v == null) return { time: b.time };
    const next = d.keys[i + 1];
    const end = values[i + 1] == null || (sessions && next.slice(0, 10) !== d.keys[i].slice(0, 10));
    return end ? { time: b.time, value: v, color: 'rgba(0,0,0,0)' } : { time: b.time, value: v };
  });
}

// Lines on the price itself (VWAP), each named on the price axis — a direct
// label, since slot 3 sits under 3:1 contrast on the light surface.
function setOverlays(s, d) {
  const key = d.view + '|' + d.overlays.map(o => o.label).join(',');
  if (key === s.overlaysKey) return;
  s.overlaysKey = key;
  for (const series of s.overlays || []) s.chart.removeSeries(series);
  s.overlays = d.overlays.map(o => {
    const series = s.chart.addSeries(s.L.LineSeries, {
      title: o.label, lineWidth: 2, priceLineVisible: false, crosshairMarkerVisible: false,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });
    series.setData(lineData(d, o.values, o.sessions));
    return series;
  });
}

function setPanes(s, d) {
  // The theme is in the key: a histogram's bar colors are set per point.
  const key = d.view + '|' + d.panes.map(p => p.key).join(',') + '|' + s.pal.surface;
  if (key === s.panesKey) return;
  s.panesKey = key;
  for (const series of s.panes) s.chart.removeSeries(series);
  s.panes = [];
  while (s.chart.panes().length > 1) s.chart.removePane(s.chart.panes().length - 1);
  s.el.style.height = `${PRICE_HEIGHT + PANE_HEIGHT * d.panes.length}px`;
  d.panes.forEach((pn, n) => {
    const priceFormat = { type: 'custom', minMove: 10 ** -pn.decimals, formatter: v => num(v, pn.decimals) + pn.unit };
    let series;
    if (pn.histogram) {
      // About zero, green above and red below; a bar with no reading has no bar.
      series = s.chart.addSeries(s.L.HistogramSeries, { title: pn.label, priceLineVisible: false, priceFormat }, n + 1);
      series.setData(d.bars.map((b, i) => pn.values[i] == null ? { time: b.time }
        : { time: b.time, value: pn.values[i], color: pn.values[i] >= 0 ? s.pal.good : s.pal.critical }));
    } else {
      series = s.chart.addSeries(s.L.LineSeries, {
        title: pn.label, lineWidth: 1, priceLineVisible: false, crosshairMarkerRadius: 3, priceFormat,
      }, n + 1);
      series.setData(lineData(d, pn.values, false));
    }
    for (const g of pn.guides) series.createPriceLine({ price: g, lineWidth: 1, lineStyle: s.L.LineStyle.Dashed, axisLabelVisible: false, color: s.pal.axis });
    s.panes.push(series);
  });
  // Stretch factors share the height in proportion; fixed heights don't hold —
  // each pane added took its share from whichever had been sized before it.
  s.chart.panes().forEach((pane, i) => pane.setStretchFactor(i ? PANE_HEIGHT : PRICE_HEIGHT));
}

function theme(s) {
  const p = s.pal, L = s.L;
  s.chart.applyOptions({
    layout: {
      textColor: p.text_secondary,
      fontFamily: getComputedStyle(s.root.host || s.root).getPropertyValue('--st-font').trim() || undefined,
      panes: { separatorColor: p.grid, separatorHoverColor: rgba(p.muted, 0.25) },
    },
    grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
    rightPriceScale: { borderColor: p.axis },
    timeScale: { borderColor: p.axis, timeVisible: !s.data.daily },
    crosshair: {
      vertLine: { color: p.muted, labelBackgroundColor: p.accent, style: L.LineStyle.Dashed },
      horzLine: { color: p.muted, labelBackgroundColor: p.accent, style: L.LineStyle.Dashed },
    },
  });
  // Neutral candles — up hollow, down filled grey: calls and puts own the colors.
  s.candles.applyOptions({
    upColor: 'rgba(0,0,0,0)', borderUpColor: p.text_secondary, wickUpColor: p.text_secondary,
    downColor: p.muted, borderDownColor: p.muted, wickDownColor: p.muted, priceLineColor: p.muted,
  });
  s.volume.applyOptions({ color: rgba(p.muted, 0.35) });
  s.panes.forEach((series, n) => s.data.panes[n].histogram || series.applyOptions({ color: p.text_secondary }));
  (s.overlays || []).forEach((series, n) => series.applyOptions({ color: p[s.data.overlays[n].color] }));
}

function update(s, d) {
  const prev = s.data, kept = s.range;
  const viewChanged = !prev || prev.view !== d.view;
  s.data = d;
  s.pal = d.palette;
  if (viewChanged) {
    s.candles.setData(d.bars);
    s.volume.setData(d.volume ? d.bars.map((b, i) => ({ time: b.time, value: d.volume[i] })) : []);
  }
  setPanes(s, d);
  setOverlays(s, d);
  theme(s);
  s.markers.setMarkers(d.markers.map(m => ({ ...m, color: s.pal[m.color] || m.color })));
  s.trades.set(d.trades, s.pal);
  s.selection.set(d.selected, s.pal);
  s.fan.set(d.fan, s.pal);

  const ts = s.chart.timeScale(), n = d.keys.length;
  if (viewChanged) {
    // A new symbol or timeframe keeps the range: the button's choice if one
    // is in force, else the same dates. The opening span only on first draw,
    // or when the kept dates lie past this tape's end.
    const from = kept ? d.keys.findIndex(k => k >= kept.from) : -1;
    if (prev && s.preset) {
      showRange(s, s.preset);
    } else if (prev && from >= 0) {
      const to = kept.to === null ? -1 : d.keys.findIndex(k => k >= kept.to);
      ts.setVisibleLogicalRange({ from: from - 0.5, to: to < 0 ? n - 1 + RIGHT : to + 0.5 });
    } else {
      ts.setVisibleLogicalRange({ from: d.opening - 0.5, to: n - 1 + RIGHT });
    }
  }
  if (d.focus && d.focus.nonce !== s.focusNonce) {
    s.focusNonce = d.focus.nonce;
    setPreset(s, null);
    ts.setVisibleLogicalRange({ from: d.focus.from, to: d.focus.to });
  }
  legend(s, null, null);
}

export default async function (component) {
  const L = await LWC;
  const { parentElement } = component;
  let s = states.get(parentElement);
  if (!s) {
    s = mount(L, parentElement, component);
    states.set(parentElement, s);
  }
  s.component = component;
  update(s, component.data);
  return () => { s.chart.remove(); states.delete(parentElement); };
}
