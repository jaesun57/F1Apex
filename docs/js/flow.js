/* Particle advection through a solved CFD velocity field.
 *
 * Particles are integrated through the real mid-plane velocity data, not a
 * decorative animation. Trails come from fading the canvas each frame rather
 * than clearing it.
 *
 * ARRAY ORDERING - must match src/f1apex/post/field.py exactly:
 *     index = iz * nx + ix        (x fast axis, z slow axis)
 * A transposed read animates plausibly and is completely wrong, and no visual
 * inspection will catch it.
 */
'use strict';

const PALETTE = [
  [  8,  22,  60],
  [ 22,  86, 170],
  [ 20, 168, 168],
  [120, 200,  90],
  [246, 190,  60],
  [232, 104,  40],
  [200,  36,  40],
];

function speedColor(t) {
  t = Math.max(0, Math.min(0.999, t));
  const s = t * (PALETTE.length - 1);
  const i = Math.floor(s);
  const f = s - i;
  const a = PALETTE[i], b = PALETTE[i + 1];
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * f)},${Math.round(a[1] + (b[1] - a[1]) * f)},${Math.round(a[2] + (b[2] - a[2]) * f)})`;
}

class FlowField {
  constructor(data) {
    this.nx = data.nx;
    this.nz = data.nz;
    this.x0 = data.x0; this.x1 = data.x1;
    this.z0 = data.z0; this.z1 = data.z1;
    this.u = Float32Array.from(data.u);
    this.w = Float32Array.from(data.w);
    this.mask = Uint8Array.from(data.mask);
    this.speedMax = data.speed_max;
    this.body = data.body;
    this.dx = (this.x1 - this.x0) / (this.nx - 1);
    this.dz = (this.z1 - this.z0) / (this.nz - 1);
  }

  /* Bilinear sample. Returns null outside the domain or inside the body, which
   * is the signal to respawn the particle. */
  sample(x, z) {
    const fx = (x - this.x0) / this.dx;
    const fz = (z - this.z0) / this.dz;
    if (fx < 0 || fz < 0 || fx > this.nx - 1.001 || fz > this.nz - 1.001) return null;

    const ix = fx | 0, iz = fz | 0;
    const tx = fx - ix, tz = fz - iz;
    const i00 = iz * this.nx + ix;
    const i10 = i00 + 1;
    const i01 = i00 + this.nx;
    const i11 = i01 + 1;

    // Any corner inside the solid: treat the sample as invalid rather than
    // blending wall values, which would drag particles through the body.
    if (this.mask[i00] || this.mask[i10] || this.mask[i01] || this.mask[i11]) return null;

    const w00 = (1 - tx) * (1 - tz), w10 = tx * (1 - tz);
    const w01 = (1 - tx) * tz,       w11 = tx * tz;
    return [
      this.u[i00] * w00 + this.u[i10] * w10 + this.u[i01] * w01 + this.u[i11] * w11,
      this.w[i00] * w00 + this.w[i10] * w10 + this.w[i01] * w01 + this.w[i11] * w11,
    ];
  }

  isSolid(x, z) {
    const fx = Math.round((x - this.x0) / this.dx);
    const fz = Math.round((z - this.z0) / this.dz);
    if (fx < 0 || fz < 0 || fx >= this.nx || fz >= this.nz) return true;
    return this.mask[fz * this.nx + fx] === 1;
  }
}

class FlowAnimation {
  constructor(canvas, field, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false });
    this.field = field;
    this.count = opts.count || 2600;
    this.speedScale = opts.speedScale || 1.0;
    this.maxAge = opts.maxAge || 320;
    this.running = false;
    this.particles = [];
    this._raf = null;
    this.resize();
    for (let i = 0; i < this.count; i++) this.particles.push(this.spawn());
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = this.canvas.getBoundingClientRect();
    this.canvas.width = Math.max(320, Math.round(rect.width * dpr));
    this.canvas.height = Math.max(200, Math.round(rect.height * dpr));
    this.dpr = dpr;
    const f = this.field;
    // Fit the field region to the canvas, preserving aspect ratio so the body
    // is not stretched.
    const fw = f.x1 - f.x0, fh = f.z1 - f.z0;
    const s = Math.min(this.canvas.width / fw, this.canvas.height / fh);
    this.scale = s;
    this.offX = (this.canvas.width - fw * s) / 2;
    this.offY = (this.canvas.height - fh * s) / 2;
    this.ctx.fillStyle = '#05070c';
    this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
  }

  toPx(x, z) {
    return [
      this.offX + (x - this.field.x0) * this.scale,
      this.canvas.height - this.offY - (z - this.field.z0) * this.scale,
    ];
  }

  spawn(seedAnywhere = true) {
    const f = this.field;
    for (let attempt = 0; attempt < 24; attempt++) {
      // Bias new particles toward the inlet so the field stays populated
      // upstream, but seed some throughout so the wake is never empty.
      const x = seedAnywhere && Math.random() < 0.55
        ? f.x0 + Math.random() * (f.x1 - f.x0)
        : f.x0 + Math.random() * (f.x1 - f.x0) * 0.12;
      const z = f.z0 + Math.random() * (f.z1 - f.z0);
      if (!f.isSolid(x, z) && f.sample(x, z)) {
        return { x, z, px: x, pz: z, age: Math.random() * this.maxAge };
      }
    }
    return { x: f.x0, z: f.z0 + (f.z1 - f.z0) * 0.5, px: f.x0, pz: f.z0, age: 0 };
  }

  step(dt) {
    const f = this.field;
    const ctx = this.ctx;

    // Fade rather than clear: this is what leaves streak trails.
    ctx.fillStyle = 'rgba(5,7,12,0.13)';
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

    // Aspect-fit letterboxes the field, so there is canvas area outside the
    // data. Clip to the field rectangle so nothing can ever be drawn there,
    // regardless of what the integrator does.
    const [lx, ly] = this.toPx(f.x0, f.z1);
    const [rx, ry] = this.toPx(f.x1, f.z0);
    ctx.save();
    ctx.beginPath();
    ctx.rect(lx, ly, rx - lx, ry - ly);
    ctx.clip();

    ctx.lineWidth = Math.max(1, 1.15 * this.dpr);
    ctx.lineCap = 'round';

    const h = dt * this.speedScale;
    for (let i = 0; i < this.particles.length; i++) {
      const p = this.particles[i];
      const v = f.sample(p.x, p.z);
      if (!v) { this.particles[i] = this.spawn(); continue; }

      const spd = Math.hypot(v[0], v[1]);
      p.px = p.x; p.pz = p.z;
      p.x += v[0] * h;
      p.z += v[1] * h;
      p.age += 1;

      // Retire stalled or old particles. Without this, recirculation zones
      // accumulate particles and the rest of the field slowly empties.
      if (p.age > this.maxAge || spd < 0.02 * f.speedMax) {
        this.particles[i] = this.spawn();
        continue;
      }

      // Reject the step if it left the domain or entered the solid, BEFORE
      // drawing it. Respawning on the next frame instead is too late: the
      // offending segment has already been drawn, and because the canvas only
      // fades, one stray segment per particle per frame accumulates into a
      // visible fan of lines below the ground plane.
      if (!f.sample(p.x, p.z)) {
        this.particles[i] = this.spawn();
        continue;
      }

      const [ax, ay] = this.toPx(p.px, p.pz);
      const [bx, by] = this.toPx(p.x, p.z);
      ctx.strokeStyle = speedColor(spd / f.speedMax);
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
    }
    ctx.restore();
    this.drawBody();
  }

  drawBody() {
    const ctx = this.ctx;
    const b = this.field.body;
    const [x0, z1] = this.toPx(b.x0, b.z1);
    const [x1, z0] = this.toPx(b.x1, b.z0);

    // Ground line
    const [, gy] = this.toPx(this.field.x0, 0);
    ctx.strokeStyle = 'rgba(150,165,190,0.55)';
    ctx.lineWidth = 2 * this.dpr;
    ctx.beginPath();
    ctx.moveTo(0, gy); ctx.lineTo(this.canvas.width, gy); ctx.stroke();

    ctx.fillStyle = '#e9edf4';
    ctx.fillRect(x0, z1, x1 - x0, z0 - z1);
    ctx.strokeStyle = 'rgba(255,255,255,0.85)';
    ctx.lineWidth = 1.5 * this.dpr;
    ctx.strokeRect(x0, z1, x1 - x0, z0 - z1);
  }

  start() {
    if (this.running) return;
    this.running = true;
    let last = performance.now();
    const loop = (now) => {
      if (!this.running) return;
      // Clamp dt so a backgrounded tab does not teleport every particle
      // across the domain on the first frame after refocus.
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      this.step(dt);
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
  }

  stop() {
    this.running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
  }
}

window.FlowField = FlowField;
window.FlowAnimation = FlowAnimation;
window.speedColor = speedColor;
