/* Vantage price charts. One series per chart (line + light area wash), one global range control that scopes
   every open chart, crosshair + keyboard hover, a data-table fallback, and a neutral marker where the desk
   first picked the name. All text goes in via textContent; nothing here uses innerHTML. */
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const RANGES = [['1d', '1D', 'today'], ['1w', '1W', '1 week'], ['1m', '1M', '1 month'], ['1y', '1Y', '1 year'], ['5y', '5Y', '5 years']];
  const SERIES = '#3987e5', SURFACE = '#0e141b', GRID = '#1f2a36', REF = '#3b4756', NEUTRAL = '#9aa7b4';
  const TZ = 'America/New_York';
  const VZ = { range: '1m', mounted: new Set() };
  try { const r = localStorage.getItem('vz-range'); if (r && RANGES.some(x => x[0] === r)) VZ.range = r; } catch (e) { /* storage unavailable */ }

  const svg = (name, attrs, parent) => { const e = document.createElementNS(NS, name); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
  const dom = (tag, cls, text, parent) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; };
  const money = v => '$' + (v >= 1000 ? v.toLocaleString('en-US', { maximumFractionDigits: 0 }) : v.toFixed(2));
  const pct = v => (v > 0 ? '+' : '') + v.toFixed(2) + '%';
  const TICK_FMT = { '1d': { hour: 'numeric' }, '1w': { weekday: 'short' }, '1m': { month: 'short', day: 'numeric' }, '1y': { month: 'short' }, '5y': { year: 'numeric' } };
  const LONG_FMT = { '1d': { hour: 'numeric', minute: '2-digit' }, '1w': { weekday: 'short', hour: 'numeric', minute: '2-digit' }, '1m': { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' },
                     '1y': { month: 'short', day: 'numeric', year: 'numeric' }, '5y': { month: 'short', day: 'numeric', year: 'numeric' } };
  const fmt = (ms, o) => new Date(ms).toLocaleString('en-US', Object.assign({ timeZone: TZ }, o));
  const etDate = ms => new Date(ms).toLocaleDateString('en-CA', { timeZone: TZ });   // YYYY-MM-DD in market time

  function niceTicks(lo, hi, n) {
    const raw = (hi - lo || 1) / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
    const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag, out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }

  function render(box, d) {
    box._vz = d;
    while (box.firstChild) box.removeChild(box.firstChild);
    const rangeLabel = (RANGES.find(r => r[0] === d.range) || [])[2] || d.range;
    if (d.error) { dom('div', 'vz-msg', 'Chart unavailable: ' + d.error, box); return; }
    const P = d.points, n = P.length, vals = P.map(p => p[1]);

    // header: the numbers, readable without hovering
    const head = dom('div', 'vz-head', null, box);
    dom('span', 'vz-price', money(d.last), head);
    const chg = dom('span', 'vz-chg', null, head);
    const arrow = dom('span', d.change_pct >= 0 ? 'vz-up' : 'vz-down', d.change_pct >= 0 ? '▲' : '▼', chg); arrow.setAttribute('aria-hidden', 'true');
    dom('span', null, ' ' + pct(d.change_pct) + (d.range === '1d' ? ' today vs prior close' : ' over ' + rangeLabel), chg);
    if (d.pick) {
      const since = (d.last / d.pick.price - 1) * 100;
      dom('span', 'vz-note', 'Desk first picked it ' + fmt(Date.parse(d.pick.date + 'T12:00:00Z'), { month: 'short', day: 'numeric' }) + ' at ' + money(d.pick.price) + ' (' + pct(since) + ' since)', head);
    }

    const plot = dom('div', 'vz-plot', null, box);
    const W = Math.max(320, box.clientWidth || 720), H = 236, m = { l: 50, r: 64, t: 14, b: 26 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    const s = svg('svg', { width: W, height: H, viewBox: '0 0 ' + W + ' ' + H, tabindex: 0, role: 'img',
      'aria-label': d.ticker + ' price, ' + rangeLabel + ': ' + money(d.first) + ' to ' + money(d.last) + ', ' + pct(d.change_pct) + '. Use arrow keys to read values.' }, plot);

    // pick marker index (only when the pick date falls inside the range and is not on top of the end dot)
    let pi = -1;
    if (d.pick) { pi = P.findIndex(p => etDate(p[0]) >= d.pick.date); if (pi < 0 || etDate(P[0][0]) > d.pick.date || pi >= n - 2) pi = -1; }
    let lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (d.prev_close) { lo = Math.min(lo, d.prev_close); hi = Math.max(hi, d.prev_close); }
    const pad = (hi - lo || hi * 0.01) * 0.07; lo -= pad; hi += pad;
    const X = i => m.l + (n === 1 ? 0 : i * pw / (n - 1)), Y = v => m.t + (hi - v) / (hi - lo) * ph;

    // recessive grid + y ticks (clean numbers)
    niceTicks(lo, hi, 4).forEach(t => {
      svg('line', { x1: m.l, x2: m.l + pw, y1: Y(t), y2: Y(t), stroke: GRID, 'stroke-width': 1 }, s);
      svg('text', { x: m.l - 8, y: Y(t) + 4, 'text-anchor': 'end', class: 'vz-axis' }, s).textContent = t >= 1000 ? t.toLocaleString('en-US') : (hi - lo < 5 ? t.toFixed(2) : String(+t.toFixed(2)));
    });
    // x ticks: evenly spaced bars (index scale skips nights/weekends), duplicates dropped
    let lastLab = null;
    [0, 0.25, 0.5, 0.75, 1].forEach((f, k) => {
      const i = Math.round(f * (n - 1)), lab = fmt(P[i][0], TICK_FMT[d.range]);
      if (lab === lastLab) return; lastLab = lab;
      svg('text', { x: X(i), y: H - 8, 'text-anchor': k === 0 ? 'start' : k === 4 ? 'end' : 'middle', class: 'vz-axis' }, s).textContent = lab;
    });
    if (d.prev_close) {
      svg('line', { x1: m.l, x2: m.l + pw, y1: Y(d.prev_close), y2: Y(d.prev_close), stroke: REF, 'stroke-width': 1 }, s);
      svg('text', { x: m.l + 4, y: Y(d.prev_close) - 5, class: 'vz-axis' }, s).textContent = 'prior close ' + money(d.prev_close);
    }
    // the series: 10% area wash under a 2px line
    const line = P.map((p, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' ');
    svg('path', { d: line + ' L' + X(n - 1).toFixed(1) + ' ' + (m.t + ph) + ' L' + m.l + ' ' + (m.t + ph) + ' Z', fill: SERIES, 'fill-opacity': 0.10 }, s);
    svg('path', { d: line, fill: 'none', stroke: SERIES, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, s);
    if (pi >= 0) {
      svg('circle', { cx: X(pi), cy: Y(P[pi][1]), r: 4, fill: NEUTRAL, stroke: SURFACE, 'stroke-width': 2 }, s);
      const above = Y(P[pi][1]) > m.t + 22;
      svg('text', { x: X(pi), y: Y(P[pi][1]) + (above ? -10 : 18), 'text-anchor': X(pi) > m.l + pw * 0.8 ? 'end' : X(pi) < m.l + 40 ? 'start' : 'middle', class: 'vz-axis' }, s).textContent = 'picked';
    }
    // end dot with surface ring + the one direct label
    svg('circle', { cx: X(n - 1), cy: Y(d.last), r: 4, fill: SERIES, stroke: SURFACE, 'stroke-width': 2 }, s);
    svg('text', { x: X(n - 1) + 9, y: Y(d.last) + 4, class: 'vz-end' }, s).textContent = money(d.last);

    // hover layer: crosshair snaps to the nearest bar; same readout on keyboard focus
    const hair = svg('line', { y1: m.t, y2: m.t + ph, stroke: '#5b6b7c', 'stroke-width': 1, visibility: 'hidden' }, s);
    const dot = svg('circle', { r: 4, fill: SERIES, stroke: SURFACE, 'stroke-width': 2, visibility: 'hidden' }, s);
    const tip = dom('div', 'vz-tip', null, plot);
    const tipVal = dom('b', null, '', tip), tipTime = dom('span', null, '', tip), tipChg = dom('span', 'vz-tipsub', '', tip);
    const base = d.prev_close || d.first;
    let cur = -1;
    const show = i => {
      i = Math.max(0, Math.min(n - 1, i)); cur = i;
      const x = X(i), y = Y(P[i][1]);
      hair.setAttribute('x1', x); hair.setAttribute('x2', x); hair.setAttribute('visibility', 'visible');
      dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.setAttribute('visibility', 'visible');
      tipVal.textContent = money(P[i][1]);
      tipTime.textContent = fmt(P[i][0], LONG_FMT[d.range]) + (d.range === '1d' || d.range === '1w' || d.range === '1m' ? ' ET' : '');
      tipChg.textContent = pct((P[i][1] / base - 1) * 100) + (d.prev_close ? ' vs prior close' : ' vs start of range');
      tip.style.display = 'block';
      const tw = tip.offsetWidth; tip.style.left = (x + 12 + tw > W ? x - 12 - tw : x + 12) + 'px'; tip.style.top = Math.max(0, Math.min(H - 70, y - 30)) + 'px';
    };
    const hide = () => { cur = -1; hair.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); tip.style.display = 'none'; };
    const hit = svg('rect', { x: m.l, y: m.t, width: pw, height: ph, fill: 'transparent' }, s);
    hit.addEventListener('pointermove', e => { const r = s.getBoundingClientRect(); show(Math.round((e.clientX - r.left - m.l) / pw * (n - 1))); });
    hit.addEventListener('pointerleave', hide);
    s.addEventListener('focus', () => show(n - 1)); s.addEventListener('blur', hide);
    s.addEventListener('keydown', e => {
      const step = { ArrowLeft: -1, ArrowRight: 1, PageUp: -Math.ceil(n / 10), PageDown: Math.ceil(n / 10) }[e.key];
      if (step) { show((cur < 0 ? n - 1 : cur) + step); e.preventDefault(); }
      else if (e.key === 'Home') { show(0); e.preventDefault(); } else if (e.key === 'End') { show(n - 1); e.preventDefault(); }
    });

    // table view: every value reachable without the chart
    const det = dom('details', 'vz-table', null, box);
    dom('summary', null, 'Data table (' + n + ' rows)', det);
    const wrap = dom('div', 'vz-scroll', null, det), tb = dom('table', null, null, wrap), hr = dom('tr', null, null, dom('thead', null, null, tb));
    dom('th', null, d.range === '1y' || d.range === '5y' ? 'Date' : 'Time (ET)', hr); dom('th', null, 'Price', hr);
    const body = dom('tbody', null, null, tb);
    for (let i = n - 1; i >= 0; i--) { const tr = dom('tr', null, null, body); dom('td', null, fmt(P[i][0], LONG_FMT[d.range]), tr); dom('td', null, money(P[i][1]), tr); }
  }

  async function load(box) {
    const t = box.dataset.ticker;
    box.classList.add('vz-loading');                       // hold the previous render while refetching
    try { const r = await fetch('/api/chart?ticker=' + encodeURIComponent(t) + '&range=' + VZ.range); render(box, await r.json()); }
    catch (e) { render(box, { error: 'could not load ' + t }); }
    box.classList.remove('vz-loading');
  }

  VZ.mount = box => { if (!box || VZ.mounted.has(box)) return; VZ.mounted.add(box); dom('div', 'vz-msg', 'Loading chart…', box); load(box); };
  VZ.setRange = r => {
    VZ.range = r; try { localStorage.setItem('vz-range', r); } catch (e) { /* ignore */ }
    document.querySelectorAll('.rangebar button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.range === r)));
    VZ.mounted.forEach(box => { if (document.contains(box)) load(box); else VZ.mounted.delete(box); });
  };
  VZ.rangeBar = host => {
    if (!host) return;
    dom('span', 'rangebar-label', 'Chart range', host);
    const g = dom('span', 'rangebar-group', null, host); g.setAttribute('role', 'group'); g.setAttribute('aria-label', 'Chart range');
    RANGES.forEach(r => { const b = dom('button', null, r[1], g); b.type = 'button'; b.dataset.range = r[0]; b.title = r[2]; b.setAttribute('aria-pressed', String(r[0] === VZ.range)); b.addEventListener('click', () => VZ.setRange(r[0])); });
    dom('span', 'rangebar-hint', 'Click any pick below to open its chart. The range applies to every open chart.', host);
  };
  let rt; window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => VZ.mounted.forEach(b => { if (b._vz && document.contains(b)) render(b, b._vz); }), 150); });
  window.VZ = VZ;
})();
