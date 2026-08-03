/* Wires the case metadata to the panels and starts the flow animation. */
'use strict';

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 4) => (v === null || v === undefined || !isFinite(v)) ? '—' : v.toFixed(d);

let anim = null;    // 2D canvas particle animation
let v3d = null;     // 3D WebGL streamline viewer
let mode = '3d';
let cases = [];

async function boot() {
  let payload;
  try {
    payload = await (await fetch('data/cases.json')).json();
  } catch (e) {
    document.querySelector('main').innerHTML =
      '<div class="panel"><h2>데이터 없음</h2><p style="color:var(--dim)">' +
      'data/cases.json 을 읽지 못했습니다. 로컬에서 열었다면 file:// 이 아니라 ' +
      '<code>python3 -m http.server</code> 로 서빙해야 합니다.</p></div>';
    return;
  }
  cases = payload.cases || [];
  const sel = $('caseSel');
  cases.forEach((c, i) => {
    const o = document.createElement('option');
    o.value = String(i); o.textContent = c.label || c.id;
    sel.appendChild(o);
  });
  sel.addEventListener('change', () => loadCase(cases[+sel.value]));
  if (cases.length) loadCase(cases[0]);
}

async function loadCase(c) {
  renderForces(c);
  renderSetup(c);
  renderConvergence(c);
  renderChart(c);
  renderShots(c);

  if (anim) { anim.stop(); anim = null; }
  try {
    // 3D streamlines first - it is the default view.
    if (v3d) { v3d.stop(); v3d = null; }
    if (c.tracks) {
      try {
        const tk = await (await fetch(c.tracks)).json();
        v3d = new Viewer3D($('gl3d'), tk);
        window.v3d = v3d;
        if (mode === '3d') v3d.start();
      } catch (err) {
        $('gl3d').hidden = true;
        console.warn('3D 뷰어를 초기화하지 못했습니다:', err);
        setMode('2d');
      }
    }

    const data = await (await fetch(c.field)).json();
    const field = new FlowField(data);
    $('cmax').textContent = field.speedMax.toFixed(1) + ' m/s';
    paintColorbar();

    // Match the canvas to the field's aspect ratio so the aspect-preserving fit
    // does not leave large empty letterbox bands. Clamped so an extremely long,
    // shallow domain still gets a usable amount of height.
    const cv = $('flow');
    const aspect = (field.x1 - field.x0) / (field.z1 - field.z0);
    const w = cv.getBoundingClientRect().width;
    cv.style.height = Math.round(Math.max(260, Math.min(560, w / aspect))) + 'px';
    anim = new FlowAnimation($('flow'), field, {
      count: +$('countRng').value,
      // Field units are m/s and m, so a particle moves u * dt metres per second
      // of wall clock. That is real time, which is far too fast to read at this
      // scale, so slow it down for legibility.
      speedScale: 0.22 * (+$('speedRng').value),
    });
    window.anim = anim;
    if (mode === '2d') anim.start(); else anim.stop();
    $('playBtn').textContent = '일시정지';
    applyMode();
  } catch (e) {
    const ctx = $('flow').getContext('2d');
    ctx.fillStyle = '#05070c';
    ctx.fillRect(0, 0, $('flow').width, $('flow').height);
    ctx.fillStyle = '#93a0b4'; ctx.font = '14px sans-serif';
    ctx.fillText('속도장 데이터를 불러오지 못했습니다: ' + e.message, 20, 40);
  }
}

function renderForces(c) {
  const f = c.forces || {};
  const down = f.Cl_downforce;
  $('forces').innerHTML = `
    <div class="k">C<sub>d</sub> (항력)</div><div class="v big">${fmt(f.Cd)}</div>
    <div class="k">C<sub>l</sub> (원시)</div><div class="v">${fmt(f.Cl)}</div>
    <div class="k">다운포스</div>
      <div class="v big ${down > 0 ? 'pos' : 'neg'}">${fmt(down)}</div>
    <div class="k">L/D</div><div class="v">${fmt(f.LD, 3)}</div>
    <div class="k">압력중심 x</div><div class="v">${fmt(f.CoP_x, 3)} m</div>
    <div class="k">평균 구간</div>
      <div class="v">마지막 ${f.window} / ${f.n_iterations}</div>`;
}

function renderSetup(c) {
  const b = c.body || {}, m = c.mesh || {}, s = c.solver || {}, fl = c.flow || {};
  $('setup').innerHTML = `
    <div class="k">자유류</div><div class="v">${fl.u_inf} m/s</div>
    <div class="k">요각</div><div class="v">${fl.yaw_deg}°</div>
    <div class="k">물체 (L×W×H)</div>
      <div class="v">${b.length}×${b.width}×${b.height} m</div>
    <div class="k">지상고</div><div class="v">${b.ground_clearance} m</div>
    <div class="k">셀 수</div><div class="v">${(m.cells || 0).toLocaleString()}</div>
    <div class="k">표면 레벨</div><div class="v">${(m.surface_level || []).join('–')}</div>
    <div class="k">레이어</div><div class="v">${m.n_layers}</div>
    <div class="k">난류 모델</div><div class="v">${s.turbulence || '—'}</div>
    <div class="k">지면</div><div class="v">${s.ground || '—'}</div>`;
}

function renderConvergence(c) {
  const cv = c.convergence || {};
  const el = $('convergence');
  if (cv.converged) {
    el.className = 'conv ok';
    el.innerHTML = '<b>수렴 확인됨.</b> 힘 계수가 정상 상태에 도달했습니다.';
  } else {
    el.className = 'conv';
    // Distinguish "not enough data" from "actually still drifting" - reporting
    // an unknown as a failure would be just as misleading as the reverse.
    const reason = cv.reason
      ? `판정 불가 — ${cv.reason}.`
      : '힘 계수가 아직 표류 중입니다.';
    el.innerHTML = `<b>수렴 미확인.</b> ${reason} 아래 값은 마지막 구간 평균이며, ` +
                   `안정돼 보이지만 <u>증명되지는 않았습니다</u>.`;
  }
}

function renderChart(c) {
  const h = c.history || {};
  const svg = $('chart');
  svg.innerHTML = '';
  if (!h.iter || h.iter.length < 2) return;

  const W = 320, H = 150, P = { l: 34, r: 8, t: 8, b: 20 };
  const xs = h.iter, series = [
    { v: h.Cd, color: 'var(--warm)' },
    { v: h.Cl, color: 'var(--accent)' },
  ];
  const all = series.flatMap(s => s.v).filter(isFinite);
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.12 || 0.1;
  lo -= pad; hi += pad;

  const sx = i => P.l + (xs[i] - xs[0]) / (xs[xs.length - 1] - xs[0]) * (W - P.l - P.r);
  const sy = v => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);

  const ns = 'http://www.w3.org/2000/svg';
  const add = (tag, attrs) => {
    const e = document.createElementNS(ns, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    svg.appendChild(e); return e;
  };
  // Zero line, when it is in range - the Cl sign is the whole story here.
  if (lo < 0 && hi > 0) {
    add('line', { x1: P.l, x2: W - P.r, y1: sy(0), y2: sy(0),
                  stroke: '#3a465c', 'stroke-dasharray': '3 3', 'stroke-width': 1 });
    add('text', { x: 4, y: sy(0) + 3, fill: '#63708a', 'font-size': 9 }).textContent = '0';
  }
  add('text', { x: 4, y: sy(hi) + 8, fill: '#63708a', 'font-size': 9 })
    .textContent = hi.toFixed(2);
  add('text', { x: 4, y: sy(lo) - 1, fill: '#63708a', 'font-size': 9 })
    .textContent = lo.toFixed(2);

  for (const s of series) {
    const d = s.v.map((v, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join('');
    add('path', { d, fill: 'none', stroke: s.color, 'stroke-width': 1.4 });
  }
}

function renderShots(c) {
  const labels = { hero: '3/4 뷰', side: '측면', top: '평면', rear: '후방', front: '전방' };
  $('shots').innerHTML = Object.entries(c.images || {}).map(([k, src]) =>
    `<figure><img src="${src}" alt="${labels[k] || k}" loading="lazy">
     <figcaption>${labels[k] || k}</figcaption></figure>`).join('');
}

function paintColorbar() {
  const stops = [];
  for (let i = 0; i <= 10; i++) stops.push(speedColor(i / 10));
  $('cbar').style.background = `linear-gradient(90deg, ${stops.join(',')})`;
}

/* ---- controls ---- */
function activeView() { return mode === '3d' ? v3d : anim; }

$('playBtn').addEventListener('click', () => {
  const v = activeView();
  if (!v) return;
  if (v.running) { v.stop(); $('playBtn').textContent = '재생'; }
  else { v.start(); $('playBtn').textContent = '일시정지'; }
});

function applyMode() {
  const is3d = mode === '3d';
  $('gl3d').hidden = !is3d || !v3d;
  $('flow').hidden = is3d;
  $('mode3d').classList.toggle('on', is3d);
  $('mode2d').classList.toggle('on', !is3d);
  // Particle count only means anything for the 2D advection view.
  $('ctlB').style.display = is3d ? 'none' : '';
  $('vizTitle').innerHTML = is3d
    ? '3D 유선 <span class="muted">드래그로 회전</span>'
    : '중앙 단면 유동 <span class="muted">y = 0</span>';
  $('vizNote').textContent = is3d
    ? '드래그 = 회전 · 휠 = 확대 · Shift+드래그 = 이동. 색 = 국부 속도, 상한은 99.5 백분위수.'
    : '실제 속도장을 입자가 이류. 색 = 국부 속도, 상한은 99.5 백분위수(이상치 제외).';

  // Only one loop runs at a time; leaving both animating wastes a core.
  if (is3d) { if (anim) anim.stop(); if (v3d) { v3d.resize(); v3d.start(); } }
  else { if (v3d) v3d.stop(); if (anim) { anim.resize(); anim.start(); } }
  $('playBtn').textContent = '일시정지';
}

function setMode(m) { mode = m; applyMode(); }
$('mode3d').addEventListener('click', () => setMode('3d'));
$('mode2d').addEventListener('click', () => setMode('2d'));
$('countRng').addEventListener('input', (e) => {
  $('countVal').textContent = e.target.value;
  if (!anim) return;
  const n = +e.target.value;
  while (anim.particles.length < n) anim.particles.push(anim.spawn());
  anim.particles.length = n;
});
$('speedRng').addEventListener('input', (e) => {
  const v = +e.target.value;
  $('speedVal').textContent = v.toFixed(1) + '×';
  if (anim) anim.speedScale = 0.22 * v;
  if (v3d) v3d.speed = 0.35 * v;
});
let rt;
window.addEventListener('resize', () => {
  clearTimeout(rt);
  rt = setTimeout(() => {
    if (mode === '2d' && anim) anim.resize();
    if (mode === '3d' && v3d) v3d.resize();
  }, 160);
});

boot();
