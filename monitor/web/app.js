'use strict';

/* ---------- formatting ---------- */

const pct  = v => v == null ? '—' : v.toFixed(v < 10 ? 1 : 0) + '%';
const temp = v => v == null ? '—' : v.toFixed(0) + '°C';

function bps(v) {
  if (v == null) return '—';
  const units = ['B/s', 'kB/s', 'MB/s', 'GB/s'];
  let i = 0;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return v.toFixed(v < 10 && i > 0 ? 1 : 0) + ' ' + units[i];
}

function bytes(v) {
  if (v == null) return '—';
  const units = ['B', 'kB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(i > 1 ? 1 : 0) + ' ' + units[i];
}

function duration(seconds) {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor(seconds % 86400 / 3600);
  const m = Math.floor(seconds % 3600 / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// Dates follow the viewer's locale, on a 24-hour clock.
const clockTime = ts => new Date(ts * 1000)
  .toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });

const fullTime = ts => new Date(ts * 1000)
  .toLocaleString(undefined, {
    day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });

const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/* ---------- panel definitions ---------- */

const RANGES = ['15m', '1h', '6h', '24h', '7d', '30d'];

const CHART_DEFS = [
  { id: 'cpu', title: 'CPU usage', fmt: pct, fixed: [0, 100],
    lines: [{ key: 'cpu_pct', name: 'CPU', color: '--cpu', area: true }] },
  { id: 'ram', title: 'RAM usage', fmt: pct, fixed: [0, 100],
    lines: [{ key: 'ram_pct', name: 'RAM', color: '--ram', area: true }] },
  { id: 'cputemp', title: 'CPU core temperatures', fmt: temp, cores: true, lines: [] },
  { id: 'gpu', title: 'GPU and VRAM', fmt: pct, fixed: [0, 100],
    lines: [{ key: 'gpu_pct', name: 'GPU', color: '--gpu' },
            { key: 'vram_pct', name: 'VRAM', color: '--vram' }] },
  { id: 'gputemp', title: 'GPU temperature', fmt: temp,
    lines: [{ key: 'gpu_temp_c', name: 'GPU', color: '--temp', area: true }] },
  { id: 'net', title: 'Network throughput', fmt: bps, floor: 0,
    lines: [{ key: 'net_rx_bps', name: 'Download', color: '--rx' },
            { key: 'net_tx_bps', name: 'Upload', color: '--tx' }] },
];

/* Per-core line colours, derived from a hue rotation. */
const coreColor = i => `hsl(${(18 + i * 47) % 360} 78% 62%)`;

const state = { range: '1h', series: null, current: null };

/* ---------- scale and drawing ---------- */

const PAD = { l: 48, r: 12, t: 12, b: 22 };

/** Round-ish bounds plus the values the gridlines sit on. */
function scaleFor(values, def) {
  if (def.fixed) {
    const [lo, hi] = def.fixed;
    return { lo, hi, ticks: [0, 25, 50, 75, 100].filter(v => v >= lo && v <= hi) };
  }
  const finite = values.filter(v => v != null && isFinite(v));
  let lo = finite.length ? Math.min(...finite) : 0;
  let hi = finite.length ? Math.max(...finite) : 1;
  if (def.floor != null) lo = def.floor;
  if (hi - lo < 1e-9) hi = lo + 1;

  const step = Math.pow(10, Math.floor(Math.log10((hi - lo) / 4)));
  const nice = [1, 2, 2.5, 5, 10].map(m => m * step).find(s => (hi - lo) / s <= 4.5) || step * 10;
  lo = Math.floor(lo / nice) * nice;
  hi = Math.ceil(hi / nice) * nice;

  const ticks = [];
  for (let v = lo; v <= hi + nice / 2; v += nice) ticks.push(Number(v.toFixed(6)));
  return { lo, hi, ticks };
}

const svgEl = (tag, attrs) => {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};

/** Draw a line chart into `plot` (a .plot element). */
function drawChart(plot, def, data, lines) {
  plot.textContent = '';
  const t = data && Array.isArray(data.t) ? data.t : [];

  if (!t.length || !lines.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'no data in this window';
    plot.appendChild(empty);
    return;
  }

  const w = plot.clientWidth || 600;
  const h = plot.clientHeight || 170;
  const iw = Math.max(10, w - PAD.l - PAD.r);
  const ih = Math.max(10, h - PAD.t - PAD.b);

  const all = lines.flatMap(l => l.data);
  const { lo, hi, ticks } = scaleFor(all, def);
  const x = i => PAD.l + (t.length === 1 ? iw / 2 : i / (t.length - 1) * iw);
  const y = v => PAD.t + ih - (v - lo) / (hi - lo) * ih;

  const svg = svgEl('svg', { viewBox: `0 0 ${w} ${h}`, width: w, height: h });

  // horizontal gridlines and their y-axis labels
  for (const tick of ticks) {
    const ty = y(tick);
    if (ty < PAD.t - 1 || ty > PAD.t + ih + 1) continue;
    svg.appendChild(svgEl('line', {
      x1: PAD.l, x2: PAD.l + iw, y1: ty, y2: ty,
      stroke: css('--grid'), 'stroke-width': 1,
    }));
    const label = svgEl('text', {
      x: PAD.l - 7, y: ty + 3.5, 'text-anchor': 'end',
      fill: css('--muted'), 'font-size': 10.5,
    });
    label.textContent = def.fmt(tick);
    svg.appendChild(label);
  }

  // x-axis labels
  const xCount = Math.min(5, t.length);
  for (let k = 0; k < xCount; k++) {
    const i = Math.round(k / Math.max(1, xCount - 1) * (t.length - 1));
    const label = svgEl('text', {
      x: x(i), y: h - 6,
      'text-anchor': k === 0 ? 'start' : k === xCount - 1 ? 'end' : 'middle',
      fill: css('--muted'), 'font-size': 10.5,
    });
    label.textContent = clockTime(t[i]);
    svg.appendChild(label);
  }

  // the series themselves
  for (const line of lines) {
    const color = line.color.startsWith('--') ? css(line.color) : line.color;
    const segments = [];
    let current = [];
    line.data.forEach((v, i) => {
      if (v == null || !isFinite(v)) { if (current.length) segments.push(current); current = []; }
      else current.push([x(i), y(v)]);
    });
    if (current.length) segments.push(current);

    for (const seg of segments) {
      const d = seg.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join('');
      if (line.area && seg.length > 1) {
        const base = PAD.t + ih;
        svg.appendChild(svgEl('path', {
          d: `${d}L${seg.at(-1)[0].toFixed(1)},${base}L${seg[0][0].toFixed(1)},${base}Z`,
          fill: color, opacity: 0.13, stroke: 'none',
        }));
      }
      svg.appendChild(svgEl('path', {
        d, fill: 'none', stroke: color,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round',
        // A lone point would be invisible as a stroke, so widen it into a dot.
        'stroke-width': seg.length === 1 ? 3.5 : 1.8,
      }));
    }
  }

  // interaction layer: guide line and markers follow the cursor
  const guide = svgEl('line', {
    y1: PAD.t, y2: PAD.t + ih, stroke: css('--muted'),
    'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0,
  });
  svg.appendChild(guide);
  const markers = lines.map(line => {
    const color = line.color.startsWith('--') ? css(line.color) : line.color;
    const dot = svgEl('circle', { r: 3.5, fill: color, stroke: css('--panel'), 'stroke-width': 1.5, opacity: 0 });
    svg.appendChild(dot);
    return dot;
  });

  const hit = svgEl('rect', { x: PAD.l, y: PAD.t, width: iw, height: ih, fill: 'transparent' });
  svg.appendChild(hit);
  plot.appendChild(svg);

  const tooltip = document.getElementById('tooltip');

  const move = ev => {
    const rect = svg.getBoundingClientRect();
    const rel = (ev.clientX - rect.left - PAD.l) / iw;
    const i = Math.max(0, Math.min(t.length - 1, Math.round(rel * (t.length - 1))));

    guide.setAttribute('x1', x(i));
    guide.setAttribute('x2', x(i));
    guide.setAttribute('opacity', 0.65);

    const rows = [];
    lines.forEach((line, k) => {
      const v = line.data[i];
      const dot = markers[k];
      if (v == null || !isFinite(v)) { dot.setAttribute('opacity', 0); return; }
      dot.setAttribute('cx', x(i));
      dot.setAttribute('cy', y(v));
      dot.setAttribute('opacity', 1);
      const color = line.color.startsWith('--') ? css(line.color) : line.color;
      rows.push(`<div class="tt-row"><i style="background:${color}"></i>${line.name}<b>${def.fmt(v)}</b></div>`);
    });

    tooltip.innerHTML = `<div class="tt-time">${fullTime(t[i])}</div>${rows.join('')}`;
    tooltip.hidden = false;
    const box = tooltip.getBoundingClientRect();
    let left = ev.clientX + 14;
    if (left + box.width > window.innerWidth - 8) left = ev.clientX - box.width - 14;
    let top = ev.clientY - box.height - 12;
    if (top < 8) top = ev.clientY + 16;
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
  };

  const leave = () => {
    guide.setAttribute('opacity', 0);
    markers.forEach(m => m.setAttribute('opacity', 0));
    tooltip.hidden = true;
  };

  hit.addEventListener('mousemove', move);
  hit.addEventListener('mouseleave', leave);
}

/** One line per core, built from data.cores. */
function coreLines(data) {
  if (!data || !data.cores) return [];
  return Object.keys(data.cores).sort().map((name, i) => ({
    key: name, name, color: coreColor(i), data: data.cores[name],
  }));
}

/* ---------- indicators ---------- */

function meterColor(value, warn, bad) {
  return value == null ? '--muted' : value >= bad ? '--bad' : value >= warn ? '--warn' : null;
}

/** Turn a temperature into a bar width, mapping 20 °C–100 °C onto 0–100%. */
const tempMeter = c => c == null ? 0 : (c - 20) / 80 * 100;

function kpiCard({ label, color, value, sub, meter }) {
  const meterHtml = meter == null ? '' :
    `<div class="meter"><span style="width:${Math.max(0, Math.min(100, meter.pct || 0))}%;background:${css(meter.color || color)}"></span></div>`;
  return `<div class="kpi">
    <div class="label"><span class="dot" style="background:${css(color)}"></span>${label}</div>
    <div class="value">${value}</div>
    <div class="sub">${sub || ''}</div>
    ${meterHtml}
  </div>`;
}

function renderKPIs(current) {
  const box = document.getElementById('kpis');
  const s = current && current.sample;
  if (!s) { box.innerHTML = '<div class="kpi"><div class="sub">waiting for the first sample…</div></div>'; return; }

  const cards = [
    kpiCard({
      label: 'CPU', color: '--cpu', value: pct(s.cpu_pct),
      sub: `${current.host.cpu_count} threads`,
      meter: { pct: s.cpu_pct, color: meterColor(s.cpu_pct, 75, 90) || '--cpu' },
    }),
    kpiCard({
      label: 'RAM', color: '--ram', value: pct(s.ram_pct),
      sub: `${bytes(s.ram_used_bytes)} of ${bytes(s.ram_total_bytes)}`,
      meter: { pct: s.ram_pct, color: meterColor(s.ram_pct, 80, 92) || '--ram' },
    }),
    kpiCard({
      label: 'CPU temp', color: '--temp', value: temp(s.cpu_temp_c),
      sub: Object.entries(s.cpu_core_temps || {}).map(([k, v]) => `${k.replace('Core ', 'C')}: ${temp(v)}`).join('  ') || 'no sensors',
      meter: { pct: tempMeter(s.cpu_temp_c), color: meterColor(s.cpu_temp_c, 70, 85) || '--temp' },
    }),
    kpiCard({
      label: 'GPU', color: '--gpu', value: pct(s.gpu_pct),
      sub: current.host.gpu_name || 'GPU unavailable',
      meter: { pct: s.gpu_pct, color: meterColor(s.gpu_pct, 80, 95) || '--gpu' },
    }),
    kpiCard({
      label: 'GPU temp', color: '--temp', value: temp(s.gpu_temp_c),
      sub: s.gpu_temp_c == null ? 'no reading' : 'graphics core',
      meter: { pct: tempMeter(s.gpu_temp_c), color: meterColor(s.gpu_temp_c, 75, 87) || '--temp' },
    }),
    kpiCard({
      label: 'VRAM', color: '--vram', value: pct(s.vram_pct),
      sub: s.vram_total_mb ? `${s.vram_used_mb.toFixed(0)} of ${s.vram_total_mb.toFixed(0)} MB` : 'no reading',
      meter: { pct: s.vram_pct, color: meterColor(s.vram_pct, 80, 92) || '--vram' },
    }),
    kpiCard({
      label: 'Network', color: '--rx',
      value: `<span style="color:${css('--rx')}">↓</span> ${bps(s.net_rx_bps)}`,
      sub: `<span style="color:${css('--tx')}">↑</span> ${bps(s.net_tx_bps)} &nbsp;·&nbsp; total ${bytes(s.net_rx_total)} / ${bytes(s.net_tx_total)}`,
    }),
  ];
  box.innerHTML = cards.join('');
}

/* ---------- chart assembly ---------- */

function buildChartShells() {
  const box = document.getElementById('charts');
  box.innerHTML = CHART_DEFS.map(def => `
    <div class="card">
      <header><h2>${def.title}</h2><div class="legend" id="legend-${def.id}"></div></header>
      <div class="plot" id="plot-${def.id}"></div>
    </div>`).join('');
}

function renderCharts() {
  const data = state.series;
  for (const def of CHART_DEFS) {
    const lines = def.cores ? coreLines(data) : def.lines.map(l => ({
      ...l, data: (data && data.series && data.series[l.key]) || [],
    }));

    document.getElementById('legend-' + def.id).innerHTML = lines.map(l => {
      const color = l.color.startsWith('--') ? css(l.color) : l.color;
      return `<span><i style="background:${color}"></i>${l.name}</span>`;
    }).join('');

    drawChart(document.getElementById('plot-' + def.id), def, data, lines);
  }
}

/* ---------- state and refresh ---------- */

function setStatus(kind, text) {
  const el = document.getElementById('status');
  el.className = 'status ' + kind;
  document.getElementById('status-text').textContent = text;
}

function renderHeader(current) {
  document.getElementById('hostname').textContent = current.host.hostname;
  const parts = [
    `${current.host.cpu_count} threads`,
    current.host.gpu_name,
    `up ${duration(current.host.uptime)}`,
    `sampling every ${current.host.interval}s`,
  ].filter(Boolean);
  document.getElementById('subtitle').textContent = parts.join(' · ');
}

async function refresh() {
  try {
    const [current, series] = await Promise.all([
      fetch('/api/current').then(r => r.json()),
      fetch('/api/series?range=' + encodeURIComponent(state.range)).then(r => r.json()),
    ]);
    // An error payload ({"error": ...}) must not be mistaken for a series,
    // or the renderers below would throw on its missing fields.
    state.current = current && current.host ? current : null;
    state.series = series && Array.isArray(series.t) ? series : null;
    if (!state.current) { setStatus('down', 'unexpected response'); return; }

    renderHeader(current);
    renderKPIs(current);
    renderCharts();

    if (!current.sample) setStatus('stale', 'waiting for a sample');
    else if (current.stale) setStatus('stale', 'data is behind · ' + clockTime(current.sample.ts));
    else setStatus('live', 'live · ' + clockTime(current.sample.ts));
  } catch (err) {
    setStatus('down', 'disconnected');
  }
}

function buildRangeButtons() {
  const box = document.getElementById('ranges');
  box.innerHTML = RANGES.map(r =>
    `<button type="button" data-range="${r}" aria-pressed="${r === state.range}">${r}</button>`).join('');
  box.addEventListener('click', ev => {
    const btn = ev.target.closest('button[data-range]');
    if (!btn) return;
    state.range = btn.dataset.range;
    for (const b of box.querySelectorAll('button')) {
      b.setAttribute('aria-pressed', String(b === btn));
    }
    refresh();
  });
}

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(renderCharts, 150);
});

buildRangeButtons();
buildChartShells();
refresh();
setInterval(refresh, 10000);
