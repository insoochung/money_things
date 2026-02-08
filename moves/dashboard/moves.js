// Money Moves Dashboard — vanilla JS
(function () {
  'use strict';

  // ── State ──
  const state = {
    lastFetch: null,
    positions: [],
    prices: {},
    sortCol: 'ticker',
    sortAsc: true,
    perfRange: '1M',
    perfData: null,
    benchData: null,
    ddData: null,
  };

  // ── Helpers ──
  const $ = (s, p) => (p || document).querySelector(s);
  const $$ = (s, p) => [...(p || document).querySelectorAll(s)];

  function fmt(n, dec = 2) {
    if (n == null) return '—';
    return Number(n).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }
  function fmtCur(n) { return n == null ? '—' : '$' + fmt(n); }
  function fmtPct(n) { return n == null ? '—' : fmt(n) + '%'; }
  function cls(n) { return n == null ? '' : n >= 0 ? 'positive' : 'negative'; }

  function relTime(d) {
    if (!d) return '';
    const s = Math.floor((Date.now() - new Date(d).getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  async function api(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }

  function errorHTML(msg, retryFn) {
    const id = 'r' + Math.random().toString(36).slice(2, 8);
    window[id] = retryFn;
    return `<div class="state-msg">${msg}<br><button class="retry-btn" onclick="${id}()">Retry</button></div>`;
  }

  function emptyHTML(msg) { return `<div class="state-msg">${msg}</div>`; }

  // ── Theme ──
  function initTheme() {
    const saved = localStorage.getItem('mm-theme') || 'light';
    document.documentElement.setAttribute('data-theme', saved);
    $('#theme-toggle').textContent = saved === 'dark' ? '🌙' : '☀️';
    $('#theme-toggle').addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme');
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('mm-theme', next);
      $('#theme-toggle').textContent = next === 'dark' ? '🌙' : '☀️';
      // Redraw canvases
      if (state.perfData) drawPerfChart();
      if (state.ddData) drawDDChart();
    });
  }

  function updateTimestamp() {
    state.lastFetch = new Date();
    const t = relTime(state.lastFetch);
    $('#last-updated').textContent = t;
    $('#footer-updated').textContent = 'Updated ' + t;
  }

  // ── Stale Banner ──
  function checkStale() {
    const el = $('#stale-banner');
    if (!state.lastFetch) { el.className = 'stale-banner'; return; }
    const diff = Date.now() - state.lastFetch.getTime();
    if (diff > 3600000) { el.className = 'stale-banner error'; el.textContent = 'Data is over 1 hour old'; }
    else if (diff > 300000) { el.className = 'stale-banner warn'; el.textContent = 'Data may be stale (>5 min)'; }
    else { el.className = 'stale-banner'; }
  }
  setInterval(checkStale, 30000);

  // ── 2. Summary Cards ──
  async function loadSummary() {
    try {
      const d = await api('/api/fund/status');
      const items = [
        { label: 'NAV', val: fmtCur(d.nav), sub: 'Net Asset Value', c: '' },
        { label: 'Return', val: fmtPct(d.return_pct), sub: 'Total return', c: cls(d.return_pct) },
        { label: 'Unrealized P/L', val: fmtCur(d.unrealized), sub: 'Open positions', c: cls(d.unrealized) },
        { label: 'Realized P/L', val: fmtCur(d.realized), sub: 'Closed trades', c: cls(d.realized) },
        { label: 'Cash', val: fmtCur(d.cash), sub: 'Available', c: '' },
        { label: 'Sharpe', val: fmt(d.sharpe), sub: 'Risk-adj return', c: d.sharpe >= 1 ? 'positive' : d.sharpe < 0 ? 'negative' : '' },
      ];
      const mode = d.mode || 'mock';
      const badge = $('#mode-badge');
      badge.textContent = mode === 'live' ? 'Live' : 'Mock';
      badge.className = 'badge ' + (mode === 'live' ? 'badge-live' : 'badge-mock');
      $('#footer-mode').innerHTML = `<span class="badge ${mode === 'live' ? 'badge-live' : 'badge-mock'}">${mode === 'live' ? 'Live' : 'Mock'}</span>`;
      $('#summary-grid').innerHTML = items.map(i =>
        `<div class="card"><div class="card-label">${i.label}</div><div class="card-value ${i.c}">${i.val}</div><div class="card-sub">${i.sub}</div></div>`
      ).join('');
    } catch (e) {
      $('#summary-grid').innerHTML = errorHTML('Failed to load summary', loadSummary);
    }
  }

  // ── 3. Macro ──
  async function loadMacro() {
    try {
      const d = await api('/api/fund/macro-indicators');
      const indicators = d.indicators || d;
      const arr = Array.isArray(indicators) ? indicators : Object.entries(indicators).map(([k, v]) => ({ name: k, ...v }));
      if (!arr.length) { $('#macro-strip').innerHTML = emptyHTML('No macro data'); return; }
      $('#macro-strip').innerHTML = arr.map(m => {
        const chg = m.change ?? m.change_1d ?? 0;
        return `<div class="macro-item"><span class="macro-label">${m.name || m.label}</span><span class="macro-value">${fmt(m.value)}</span><span class="macro-change ${cls(chg)}">${chg >= 0 ? '+' : ''}${fmt(chg)}</span></div>`;
      }).join('');
    } catch (e) {
      $('#macro-strip').innerHTML = errorHTML('Failed to load macro', loadMacro);
    }
  }

  // macro scroll
  $('#macro-left').addEventListener('click', () => { $('#macro-strip').scrollBy({ left: -150, behavior: 'smooth' }); });
  $('#macro-right').addEventListener('click', () => { $('#macro-strip').scrollBy({ left: 150, behavior: 'smooth' }); });

  // ── 4. Risk ──
  async function loadRisk() {
    try {
      const d = await api('/api/fund/risk');
      const metrics = [
        { label: 'Worst-Case Loss', val: d.worst_case_loss, fmt: fmtCur, thresholds: [-50000, -20000] },
        { label: 'Crash Impact (-20%)', val: d.crash_impact, fmt: fmtCur, thresholds: [-30000, -10000] },
        { label: 'Concentration', val: d.concentration, fmt: fmtPct, thresholds: [60, 40] },
        { label: 'VaR (95%)', val: d.var_95, fmt: fmtCur, thresholds: [-10000, -5000] },
      ];
      $('#risk-grid').innerHTML = metrics.map(m => {
        const v = m.val ?? 0;
        const sev = v <= m.thresholds[0] ? 'risk-red' : v <= m.thresholds[1] ? 'risk-yellow' : 'risk-green';
        return `<div class="risk-metric ${sev}"><div class="risk-val">${m.fmt(v)}</div><div class="risk-label">${m.label}</div></div>`;
      }).join('');
    } catch (e) {
      $('#risk-grid').innerHTML = errorHTML('Failed to load risk', loadRisk);
    }
  }

  // ── 5. Theses ──
  async function loadTheses() {
    try {
      const d = await api('/api/fund/theses');
      const arr = Array.isArray(d) ? d : d.theses || [];
      if (!arr.length) { $('#thesis-list').innerHTML = emptyHTML('No active theses'); return; }
      const statusBadge = s => {
        const map = { strengthening: 'badge-green', confirmed: 'badge-blue', weakening: 'badge-yellow', invalidated: 'badge-red' };
        return `<span class="badge ${map[s] || 'badge-muted'}">${s || 'unknown'}</span>`;
      };
      $('#thesis-list').innerHTML = arr.map(t =>
        `<div class="thesis-card" onclick="this.classList.toggle('expanded')">
          <div class="thesis-header">
            <span class="thesis-title">${t.title || t.name}</span>
            ${statusBadge(t.status)}
            ${t.strategy ? `<span class="badge badge-muted">${t.strategy}</span>` : ''}
            <span class="thesis-symbols">${(t.symbols || t.tickers || []).join(', ')}</span>
          </div>
          <div class="thesis-body">
            <p>${t.description || t.text || ''}</p>
            ${t.criteria ? `<p><strong>Criteria:</strong> ${t.criteria}</p>` : ''}
            ${t.news_matches ? `<p><strong>News:</strong> ${t.news_matches}</p>` : ''}
          </div>
        </div>`
      ).join('');
    } catch (e) {
      $('#thesis-list').innerHTML = errorHTML('Failed to load theses', loadTheses);
    }
  }

  // ── 6. Exposure ──
  async function loadExposure() {
    try {
      const d = await api('/api/fund/exposure');
      const long = d.long_pct ?? d.long ?? 0;
      const short = Math.abs(d.short_pct ?? d.short ?? 0);
      const cash = d.cash_pct ?? d.cash ?? (100 - long - short);
      const net = d.net_exposure ?? (long - short);

      const container = $('#exposure-bar-container');
      container.innerHTML = `<div class="exposure-bar"><div class="exposure-long" style="width:${long}%"></div><div class="exposure-short" style="width:${short}%"></div><div class="exposure-cash-seg" style="width:${cash}%"></div></div>`;
      $('#exposure-labels').innerHTML = `<span class="positive">Long ${fmt(long, 1)}%</span><span>Cash ${fmt(cash, 1)}%</span><span class="negative">Short ${fmt(short, 1)}%</span>`;

      // SVG gauge
      drawGauge(net);
    } catch (e) {
      $('#exposure-bar-container').innerHTML = errorHTML('Failed to load exposure', loadExposure);
    }
  }

  function drawGauge(pct) {
    const svg = $('#exposure-gauge');
    const cx = 110, cy = 110, r = 90;
    const startA = Math.PI, endA = 0;
    const arc = (a1, a2, color) => {
      const x1 = cx + r * Math.cos(a1), y1 = cy - r * Math.sin(a1);
      const x2 = cx + r * Math.cos(a2), y2 = cy - r * Math.sin(a2);
      const large = (a1 - a2) > Math.PI ? 1 : 0;
      return `<path d="M${x1},${y1} A${r},${r} 0 ${large} 0 ${x2},${y2}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round"/>`;
    };
    // zones: red(-100 to -50), yellow(-50 to 0), green(0 to 50), yellow(50 to 100)... simplified
    const needleAngle = Math.PI - ((pct + 100) / 200) * Math.PI;
    const nx = cx + (r - 15) * Math.cos(needleAngle);
    const ny = cy - (r - 15) * Math.sin(needleAngle);
    svg.innerHTML = `
      ${arc(Math.PI, Math.PI * 0.75, '#e03e3e')}
      ${arc(Math.PI * 0.75, Math.PI * 0.5, '#cb912f')}
      ${arc(Math.PI * 0.5, Math.PI * 0.25, '#448361')}
      ${arc(Math.PI * 0.25, 0, '#cb912f')}
      <line x1="${cx}" y1="${cy}" x2="${nx}" y2="${ny}" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round"/>
      <circle cx="${cx}" cy="${cy}" r="4" fill="var(--accent)"/>
      <text x="${cx}" y="${cy + 20}" text-anchor="middle" font-family="IBM Plex Mono" font-size="14" font-weight="700" fill="var(--text)">${fmt(pct, 1)}%</text>
      <text x="15" y="${cy + 5}" font-size="10" fill="var(--muted)">-100%</text>
      <text x="185" y="${cy + 5}" font-size="10" fill="var(--muted)">+100%</text>
    `;
  }

  // ── 7. Correlation ──
  async function loadCorrelation() {
    try {
      const d = await api('/api/fund/correlation');
      const labels = d.labels || d.tickers || [];
      const matrix = d.matrix || d.correlations || [];
      if (!labels.length) { $('#correlation-map').innerHTML = emptyHTML('No correlation data'); return; }
      const n = labels.length;
      const el = $('#correlation-map');
      el.className = '';
      el.style.cssText = `display:grid;grid-template-columns:60px repeat(${n},1fr);gap:2px;`;
      let html = '<div></div>';
      labels.forEach(l => { html += `<div class="heatmap-label">${l}</div>`; });
      matrix.forEach((row, i) => {
        html += `<div class="heatmap-label">${labels[i]}</div>`;
        row.forEach((v, j) => {
          const r = v > 0 ? Math.round(v * 200) : 0;
          const b = v < 0 ? Math.round(-v * 200) : 0;
          const bg = `rgb(${200 + Math.round(v < 0 ? -v * 55 : 0)},${200 + Math.round((1 - Math.abs(v)) * 55)},${200 + Math.round(v > 0 ? v * 55 : 0)})`;
          html += `<div class="heatmap-cell" style="background:rgba(${v > 0 ? '47,128,237' : '224,62,62'},${Math.abs(v) * 0.5})">${fmt(v, 2)}<span class="tooltip">${labels[i]} × ${labels[j]}: ${fmt(v, 2)}</span></div>`;
        });
      });
      el.innerHTML = html;
    } catch (e) {
      const el = $('#correlation-map'); el.className = ''; el.innerHTML = errorHTML('Failed to load correlation', loadCorrelation);
    }
  }

  // ── 8. Treemap ──
  async function loadTreemap() {
    try {
      const d = await api('/api/fund/heatmap');
      const items = (d.positions || d || []).filter(p => p.market_value || p.value);
      if (!items.length) { $('#treemap').innerHTML = emptyHTML('No positions'); return; }

      const total = items.reduce((s, p) => s + Math.abs(p.market_value || p.value || 0), 0);
      const container = $('#treemap');
      const W = container.offsetWidth || 800;
      const H = 260;

      // simple squarified-ish layout: single row
      let x = 0;
      container.innerHTML = items.map(p => {
        const mv = Math.abs(p.market_value || p.value || 1);
        const w = (mv / total) * W;
        const pnl = p.pnl_pct ?? p.pl_pct ?? 0;
        const bg = pnl >= 0 ? `rgba(68,131,97,${Math.min(0.9, 0.3 + Math.abs(pnl) / 30)})` : `rgba(224,62,62,${Math.min(0.9, 0.3 + Math.abs(pnl) / 30)})`;
        const left = x;
        x += w;
        return `<div class="treemap-rect" style="left:${left}px;top:0;width:${w}px;height:${H}px;background:${bg}" title="${p.ticker}: ${fmtCur(p.market_value || p.value)} (${fmtPct(pnl)})"><span class="tm-ticker">${p.ticker}</span><span class="tm-pct">${fmtPct(pnl)}</span></div>`;
      }).join('');
    } catch (e) {
      $('#treemap').innerHTML = errorHTML('Failed to load heatmap', loadTreemap);
    }
  }

  // ── 9. Positions Table ──
  async function loadPositions() {
    try {
      const d = await api('/api/fund/positions');
      state.positions = Array.isArray(d) ? d : d.positions || [];
      renderPositions();
    } catch (e) {
      $('#positions-body').innerHTML = `<tr><td colspan="10">${errorHTML('Failed to load positions', loadPositions)}</td></tr>`;
    }
  }

  function renderPositions() {
    const arr = [...state.positions];
    if (!arr.length) { $('#positions-body').innerHTML = `<tr><td colspan="10">${emptyHTML('No open positions')}</td></tr>`; return; }
    arr.sort((a, b) => {
      let av = a[state.sortCol], bv = b[state.sortCol];
      if (typeof av === 'string') { av = av.toLowerCase(); bv = (bv || '').toLowerCase(); }
      return (av < bv ? -1 : av > bv ? 1 : 0) * (state.sortAsc ? 1 : -1);
    });

    $('#positions-body').innerHTML = arr.map((p, i) => {
      const cur = state.prices[p.ticker] ?? p.current ?? p.price;
      const value = (cur || 0) * (p.shares || 0);
      const pnl = p.pnl ?? (value - (p.entry || 0) * (p.shares || 0));
      const pnlPct = p.pnl_pct ?? (p.entry ? ((cur - p.entry) / p.entry) * 100 : 0);
      const rangeBar = buildRangeBar(p.stop, cur, p.target);
      const review = p.review_days != null ? `${p.review_days}d` : '—';
      return `<tr class="pos-row" data-idx="${i}" style="cursor:pointer">
        <td><strong>${p.ticker}</strong></td>
        <td><span class="badge ${p.side === 'short' ? 'badge-red' : 'badge-green'}">${p.side || 'long'}</span></td>
        <td>${p.shares}</td>
        <td>${fmtCur(p.entry)}</td>
        <td id="price-${p.ticker}">${fmtCur(cur)}</td>
        <td class="hide-tablet">${fmtCur(value)}</td>
        <td class="${cls(pnl)}">${fmtCur(pnl)}</td>
        <td class="${cls(pnlPct)}">${fmtPct(pnlPct)}</td>
        <td class="hide-tablet">${rangeBar}</td>
        <td class="hide-tablet">${review}</td>
      </tr>
      <tr class="expand-row" id="expand-${i}"><td colspan="10"><div class="expand-content"><canvas class="sparkline-canvas" width="200" height="50" id="spark-${i}"></canvas><div id="lots-${i}"></div></div></td></tr>`;
    }).join('');

    // click to expand
    $$('.pos-row').forEach(row => {
      row.addEventListener('click', () => {
        const idx = row.dataset.idx;
        const exp = $(`#expand-${idx}`);
        exp.classList.toggle('open');
        if (exp.classList.contains('open')) loadPositionDetail(idx, arr[idx].ticker);
      });
    });
  }

  function buildRangeBar(stop, cur, target) {
    if (stop == null && target == null) return '—';
    const lo = stop || cur * 0.9;
    const hi = target || cur * 1.1;
    const range = hi - lo || 1;
    const needlePos = Math.max(0, Math.min(100, ((cur - lo) / range) * 100));
    return `<div class="range-bar"><div class="rb-stop" style="width:${needlePos}%"></div><div class="rb-target" style="width:${100 - needlePos}%"></div><div class="rb-needle" style="left:${needlePos}%"></div></div>`;
  }

  async function loadPositionDetail(idx, ticker) {
    try {
      const d = await api(`/api/fund/position/${ticker}`);
      const lotsEl = $(`#lots-${idx}`);
      if (d.lots && d.lots.length) {
        lotsEl.innerHTML = '<strong>Lots:</strong><br>' + d.lots.map(l => `${l.shares}sh @ ${fmtCur(l.entry)} (${l.date || ''})`).join('<br>');
      }
      if (d.thesis) lotsEl.innerHTML += `<br><em>Thesis: ${d.thesis}</em>`;
      // sparkline
      if (d.sparkline && d.sparkline.length) drawSparkline(`spark-${idx}`, d.sparkline);
    } catch (e) { /* silent */ }
  }

  function drawSparkline(canvasId, data) {
    const c = document.getElementById(canvasId);
    if (!c) return;
    const ctx = c.getContext('2d');
    const w = c.width, h = c.height;
    const min = Math.min(...data), max = Math.max(...data);
    const range = max - min || 1;
    ctx.clearRect(0, 0, w, h);
    ctx.beginPath();
    ctx.strokeStyle = data[data.length - 1] >= data[0] ? '#448361' : '#e03e3e';
    ctx.lineWidth = 1.5;
    data.forEach((v, i) => {
      const x = (i / (data.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  // sorting
  $$('#positions-table th').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (!col) return;
      if (state.sortCol === col) state.sortAsc = !state.sortAsc;
      else { state.sortCol = col; state.sortAsc = true; }
      renderPositions();
    });
  });

  // ── 10. Performance Chart ──
  async function loadPerformance() {
    try {
      const [perf, bench] = await Promise.all([
        api('/api/fund/performance'),
        api('/api/fund/benchmark'),
      ]);
      state.perfData = perf;
      state.benchData = bench;
      drawPerfChart();
      // badges
      const badges = $('#perf-badges');
      badges.innerHTML = `<span class="badge badge-muted">α ${fmt(perf.alpha ?? bench.alpha ?? 0)}</span><span class="badge badge-muted">β ${fmt(perf.beta ?? bench.beta ?? 0)}</span>`;
    } catch (e) {
      $('#perf-canvas').parentElement.innerHTML = errorHTML('Failed to load performance', loadPerformance);
    }
  }

  function drawPerfChart() {
    const c = document.getElementById('perf-canvas');
    if (!c) return;
    const ctx = c.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const w = c.parentElement.offsetWidth;
    const h = 280;
    c.width = w * dpr; c.height = h * dpr;
    c.style.width = w + 'px'; c.style.height = h + 'px';
    ctx.scale(dpr, dpr);

    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    const textC = isDark ? '#e0e0e0' : '#37352f';
    const mutedC = isDark ? '#7a7a7a' : '#9b9a97';
    const borderC = isDark ? '#333' : '#e8e8e4';

    const perf = state.perfData;
    const nav = perf.nav_series || perf.series || perf.nav_history || [];
    if (!nav.length) { ctx.fillStyle = mutedC; ctx.fillText('No data', w / 2 - 20, h / 2); return; }

    const vals = nav.map(p => p.value ?? p.nav ?? p);
    const min = Math.min(...vals) * 0.98, max = Math.max(...vals) * 1.02;
    const range = max - min || 1;

    // grid
    ctx.strokeStyle = borderC; ctx.lineWidth = 0.5;
    for (let i = 0; i < 5; i++) {
      const y = 20 + (i / 4) * (h - 40);
      ctx.beginPath(); ctx.moveTo(40, y); ctx.lineTo(w - 10, y); ctx.stroke();
      ctx.fillStyle = mutedC; ctx.font = '10px IBM Plex Mono';
      ctx.fillText(fmt(max - (i / 4) * range, 0), 0, y + 3);
    }

    const plotX = i => 40 + (i / (vals.length - 1)) * (w - 50);
    const plotY = v => 20 + ((max - v) / range) * (h - 40);

    // gradient fill
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, isDark ? 'rgba(68,131,97,0.3)' : 'rgba(68,131,97,0.15)');
    grad.addColorStop(1, 'transparent');
    ctx.beginPath();
    vals.forEach((v, i) => { i === 0 ? ctx.moveTo(plotX(i), plotY(v)) : ctx.lineTo(plotX(i), plotY(v)); });
    ctx.lineTo(plotX(vals.length - 1), h - 20);
    ctx.lineTo(plotX(0), h - 20);
    ctx.fillStyle = grad; ctx.fill();

    // NAV line
    ctx.beginPath();
    ctx.strokeStyle = '#448361'; ctx.lineWidth = 2;
    vals.forEach((v, i) => { i === 0 ? ctx.moveTo(plotX(i), plotY(v)) : ctx.lineTo(plotX(i), plotY(v)); });
    ctx.stroke();

    // Benchmarks
    const benchColors = { SPY: '#2f80ed', QQQ: '#cb912f', IWM: '#9b9a97' };
    ['spy', 'qqq', 'iwm'].forEach(sym => {
      if (!document.getElementById(`bench-${sym}`).checked) return;
      const bd = state.benchData;
      const series = bd[sym] || bd[sym.toUpperCase()] || [];
      if (!series.length) return;
      const bVals = series.map(p => p.value ?? p);
      ctx.beginPath(); ctx.strokeStyle = benchColors[sym.toUpperCase()]; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
      bVals.forEach((v, i) => { const x = plotX(i * (vals.length - 1) / (bVals.length - 1)); i === 0 ? ctx.moveTo(x, plotY(v)) : ctx.lineTo(x, plotY(v)); });
      ctx.stroke(); ctx.setLineDash([]);
    });
  }

  // perf controls
  $$('#perf-controls .chart-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      $$('#perf-controls .chart-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.perfRange = btn.dataset.range;
      loadPerformance();
    });
  });
  ['spy', 'qqq', 'iwm'].forEach(s => {
    document.getElementById(`bench-${s}`).addEventListener('change', drawPerfChart);
  });

  // ── 11. Drawdown ──
  async function loadDrawdown() {
    try {
      const d = await api('/api/fund/drawdown');
      state.ddData = d;
      const metrics = $('#dd-metrics');
      metrics.innerHTML = [
        { label: 'Max Drawdown', val: fmtPct(d.max_drawdown) },
        { label: 'Current DD', val: fmtPct(d.current_drawdown) },
        { label: 'Days Underwater', val: d.days_underwater ?? '—' },
      ].map(m => `<div class="dd-metric"><div class="dd-val negative">${m.val}</div><div class="dd-label">${m.label}</div></div>`).join('');
      drawDDChart();
    } catch (e) {
      $('#dd-metrics').innerHTML = errorHTML('Failed to load drawdown', loadDrawdown);
    }
  }

  function drawDDChart() {
    const c = document.getElementById('dd-canvas');
    if (!c || !state.ddData) return;
    const ctx = c.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const w = c.parentElement.offsetWidth;
    const h = 180;
    c.width = w * dpr; c.height = h * dpr;
    c.style.width = w + 'px'; c.style.height = h + 'px';
    ctx.scale(dpr, dpr);

    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    const series = state.ddData.series || state.ddData.underwater || [];
    if (!series.length) return;

    const vals = series.map(p => p.value ?? p);
    const min = Math.min(...vals, 0);

    ctx.beginPath();
    ctx.strokeStyle = '#e03e3e'; ctx.lineWidth = 1.5;
    ctx.fillStyle = isDark ? 'rgba(224,62,62,0.2)' : 'rgba(224,62,62,0.1)';
    const plotX = i => (i / (vals.length - 1)) * w;
    const plotY = v => (1 - v / (min || -1)) * (h - 10) + 5;
    vals.forEach((v, i) => { i === 0 ? ctx.moveTo(plotX(i), plotY(v)) : ctx.lineTo(plotX(i), plotY(v)); });
    ctx.stroke();
    ctx.lineTo(plotX(vals.length - 1), plotY(0));
    ctx.lineTo(plotX(0), plotY(0));
    ctx.fill();

    // zero line
    ctx.beginPath(); ctx.strokeStyle = isDark ? '#333' : '#e8e8e4'; ctx.lineWidth = 1;
    ctx.moveTo(0, plotY(0)); ctx.lineTo(w, plotY(0)); ctx.stroke();
  }

  // ── 12. Trades ──
  async function loadTrades() {
    try {
      const d = await api('/api/fund/trades');
      const trades = (Array.isArray(d) ? d : d.trades || []).slice(0, 10);
      if (!trades.length) { $('#trades-body').innerHTML = `<tr><td colspan="6">${emptyHTML('No trades yet')}</td></tr>`; return; }
      $('#trades-body').innerHTML = trades.map(t => {
        const isBuy = (t.action || t.side || '').toLowerCase().includes('buy');
        return `<tr>
          <td><span class="badge ${isBuy ? 'badge-green' : 'badge-red'}">${(t.action || t.side || '').toUpperCase()}</span></td>
          <td><strong>${t.ticker}</strong></td>
          <td>${t.shares}</td>
          <td>${fmtCur(t.price)}</td>
          <td class="hide-tablet">${fmtCur(t.total ?? (t.shares * t.price))}</td>
          <td class="${cls(t.realized_pnl)}">${fmtCur(t.realized_pnl ?? 0)}</td>
        </tr>`;
      }).join('');
    } catch (e) {
      $('#trades-body').innerHTML = `<tr><td colspan="6">${errorHTML('Failed to load trades', loadTrades)}</td></tr>`;
    }
  }

  // ── 13. Congress ──
  async function loadCongress() {
    try {
      const d = await api('/api/fund/congress-trades');
      const trades = Array.isArray(d) ? d : d.trades || [];
      if (!trades.length) { $('#congress-body').innerHTML = `<tr><td colspan="6">${emptyHTML('No congress trades')}</td></tr>`; return; }
      const myTickers = new Set(state.positions.map(p => p.ticker));
      $('#congress-body').innerHTML = trades.map(t => {
        const overlap = myTickers.has(t.ticker);
        return `<tr class="${overlap ? 'overlap' : ''}">
          <td>${t.member || t.representative}</td>
          <td><strong>${t.ticker}</strong></td>
          <td>${t.action || t.type}</td>
          <td class="hide-tablet">${t.amount || '—'}</td>
          <td>${t.date || t.filed_date || '—'}</td>
          <td>${overlap ? '⚠️' : ''}</td>
        </tr>`;
      }).join('');
    } catch (e) {
      $('#congress-body').innerHTML = `<tr><td colspan="6">${errorHTML('Failed to load congress', loadCongress)}</td></tr>`;
    }
  }

  // ── 14. Principles ──
  async function loadPrinciples() {
    try {
      const d = await api('/api/fund/principles');
      const arr = Array.isArray(d) ? d : d.principles || [];
      if (!arr.length) { $('#principles-list').innerHTML = emptyHTML('No principles defined'); return; }
      $('#principles-list').innerHTML = arr.map(p => {
        const val = p.validated ?? 0;
        const inv = p.invalidated ?? 0;
        const total = val + inv || 1;
        const pct = (val / total) * 100;
        return `<div class="principle-item">
          <div class="principle-text">${p.text || p.principle}</div>
          <div class="principle-meta">
            ${p.category ? `<span class="badge badge-muted">${p.category}</span>` : ''}
            <span>✓ ${val}</span><span>✗ ${inv}</span>
            <div class="validation-bar"><div class="vb-green" style="width:${pct}%"></div></div>
            ${p.last_applied ? `<span>Last: ${relTime(p.last_applied)}</span>` : ''}
          </div>
        </div>`;
      }).join('');
    } catch (e) {
      $('#principles-list').innerHTML = errorHTML('Failed to load principles', loadPrinciples);
    }
  }

  // ── WebSocket ──
  function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let ws;
    let retryDelay = 1000;

    function connect() {
      ws = new WebSocket(`${proto}//${location.host}/ws/prices`);
      ws.onopen = () => { retryDelay = 1000; };
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          const updates = data.prices || data;
          if (typeof updates === 'object') {
            Object.entries(updates).forEach(([ticker, price]) => {
              const old = state.prices[ticker];
              state.prices[ticker] = price;
              const el = document.getElementById(`price-${ticker}`);
              if (el) {
                el.textContent = fmtCur(price);
                if (old != null && price !== old) {
                  el.classList.remove('flash-up', 'flash-down');
                  void el.offsetWidth; // reflow
                  el.classList.add(price > old ? 'flash-up' : 'flash-down');
                }
              }
            });
            // recalc
            updateTimestamp();
          }
        } catch (err) { /* ignore */ }
      };
      ws.onclose = () => {
        setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 30000);
      };
      ws.onerror = () => { ws.close(); };
    }
    connect();
  }

  // ── Init ──
  async function init() {
    initTheme();
    updateTimestamp();

    // Load all sections in parallel
    await Promise.allSettled([
      loadSummary(),
      loadMacro(),
      loadRisk(),
      loadTheses(),
      loadExposure(),
      loadCorrelation(),
      loadTreemap(),
      loadPositions(),
      loadPerformance(),
      loadDrawdown(),
      loadTrades(),
      loadCongress(),
      loadPrinciples(),
    ]);

    updateTimestamp();
    connectWS();

    // Refresh every 60s
    setInterval(() => {
      Promise.allSettled([loadSummary(), loadPositions(), loadExposure()]);
      updateTimestamp();
    }, 60000);
  }

  init();
})();
