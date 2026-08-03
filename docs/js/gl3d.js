/* Interactive 3D streamline viewer, hand-rolled WebGL.
 *
 * No library is vendored: lines, a shaded box and an orbit camera are little
 * enough code that pulling in a 600 KB dependency would cost more than it saves,
 * and the page stays genuinely self-contained.
 *
 * The "flowing" look is done entirely on the GPU. Each vertex carries its arc
 * length along its own streamline; the vertex shader turns
 *     fract(arcLength / spacing - time)
 * into a travelling brightness pulse. Nothing moves on the CPU, so a few
 * hundred thousand vertices animate for free.
 *
 * Coordinates are the project's own: x streamwise, y spanwise, z up. The camera
 * up-vector is set to +z rather than remapping the data.
 */
'use strict';

/* ---------- minimal mat4 ---------- */
const M4 = {
  mul(a, b) {
    const o = new Float32Array(16);
    for (let i = 0; i < 4; i++)
      for (let j = 0; j < 4; j++) {
        let s = 0;
        for (let k = 0; k < 4; k++) s += a[k * 4 + j] * b[i * 4 + k];
        o[i * 4 + j] = s;
      }
    return o;
  },
  perspective(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return new Float32Array([
      f / aspect, 0, 0, 0,
      0, f, 0, 0,
      0, 0, (far + near) * nf, -1,
      0, 0, 2 * far * near * nf, 0,
    ]);
  },
  lookAt(eye, target, up) {
    const z = norm(sub(eye, target));
    const x = norm(cross(up, z));
    const y = cross(z, x);
    return new Float32Array([
      x[0], y[0], z[0], 0,
      x[1], y[1], z[1], 0,
      x[2], y[2], z[2], 0,
      -dot(x, eye), -dot(y, eye), -dot(z, eye), 1,
    ]);
  },
};
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => { const l = Math.hypot(...a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

/* ---------- shared colour map (matches flow.js / the 2D view) ---------- */
const GLSL_RAMP = `
vec3 ramp(float t){
  t = clamp(t, 0.0, 1.0);
  vec3 c0=vec3(0.031,0.086,0.235), c1=vec3(0.086,0.337,0.667);
  vec3 c2=vec3(0.078,0.659,0.659), c3=vec3(0.471,0.784,0.353);
  vec3 c4=vec3(0.965,0.745,0.235), c5=vec3(0.910,0.408,0.157);
  vec3 c6=vec3(0.784,0.141,0.157);
  float s = t*6.0; int i = int(floor(s)); float f = fract(s);
  if(i==0) return mix(c0,c1,f);
  if(i==1) return mix(c1,c2,f);
  if(i==2) return mix(c2,c3,f);
  if(i==3) return mix(c3,c4,f);
  if(i==4) return mix(c4,c5,f);
  return mix(c5,c6,f);
}`;

const LINE_VS = `
attribute vec3 aPos;
attribute float aSpeed;
attribute float aArc;
uniform mat4 uMVP;
uniform float uTime;
uniform float uSpeedMax;
uniform float uSpacing;
uniform float uPulse;
varying vec3 vColor;
varying float vGlow;
${GLSL_RAMP}
void main(){
  gl_Position = uMVP * vec4(aPos, 1.0);
  vColor = ramp(aSpeed / uSpeedMax);
  // Travelling pulse: sharpened so it reads as a discrete packet moving along
  // the line rather than a soft gradient.
  float p = fract(aArc / uSpacing - uTime);
  vGlow = pow(1.0 - p, 6.0) * uPulse;
}`;

const LINE_FS = `
precision mediump float;
varying vec3 vColor;
varying float vGlow;
uniform float uBase;
void main(){
  vec3 c = vColor * (uBase + vGlow * 2.4);
  gl_FragColor = vec4(c, uBase * 0.55 + vGlow);
}`;

const SOLID_VS = `
attribute vec3 aPos;
attribute vec3 aNormal;
uniform mat4 uMVP;
varying float vShade;
void main(){
  gl_Position = uMVP * vec4(aPos, 1.0);
  vec3 l = normalize(vec3(-0.4, -0.7, 0.85));
  vShade = 0.42 + 0.58 * max(dot(normalize(aNormal), l), 0.0);
}`;

const SOLID_FS = `
precision mediump float;
varying float vShade;
uniform vec3 uColor;
uniform float uAlpha;
void main(){ gl_FragColor = vec4(uColor * vShade, uAlpha); }`;

function compile(gl, vsSrc, fsSrc) {
  const mk = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
      throw new Error('shader: ' + gl.getShaderInfoLog(s));
    return s;
  };
  const p = gl.createProgram();
  gl.attachShader(p, mk(gl.VERTEX_SHADER, vsSrc));
  gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fsSrc));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS))
    throw new Error('link: ' + gl.getProgramInfoLog(p));
  return p;
}

class Viewer3D {
  constructor(canvas, tracks) {
    this.canvas = canvas;
    this.tracks = tracks;
    const gl = canvas.getContext('webgl', { antialias: true, alpha: false });
    if (!gl) throw new Error('WebGL is not available in this browser');
    this.gl = gl;

    this.lineProg = compile(gl, LINE_VS, LINE_FS);
    this.solidProg = compile(gl, SOLID_VS, SOLID_FS);

    this.buildStreamlines();
    this.buildBody();
    this.buildGround();

    const b = tracks.bounds;
    this.target = [
      (b.x[0] + b.x[1]) / 2 * 0.55,
      0,
      (b.z[0] + b.z[1]) / 2,
    ];
    const span = Math.max(b.x[1] - b.x[0], b.z[1] - b.z[0]);
    this.radius = span * 0.95;
    // Open side-on, matching the 2D mid-plane view exactly: camera on -y,
    // horizontal, so x runs left-to-right and z is up on screen. Toggling
    // between 2D and 3D then reads as the same scene rather than two
    // unrelated pictures. The user can orbit away from here immediately.
    this.theta = -Math.PI / 2;   // camera on the -y side
    this.phi = Math.PI / 2;      // horizontal (eye at the target's height)
    this.pulse = 1.0;
    this.spacing = 0.22;   // metres between travelling pulses
    this.speed = 0.35;     // pulses per second
    this.time = 0;
    this.running = false;

    this.bindControls();
    this.resize();
  }

  /* Flat GL buffers. Arc length is accumulated per line so pulses are evenly
   * spaced in physical distance regardless of how the integrator sampled. */
  buildStreamlines() {
    const gl = this.gl, t = this.tracks;
    const pos = new Float32Array(t.positions);
    const spd = new Float32Array(t.speeds);
    const arc = new Float32Array(t.n_vertices);
    const idx = [];

    for (let l = 0; l < t.n_lines; l++) {
      const o = t.offsets[l], n = t.counts[l];
      let acc = 0;
      arc[o] = 0;
      for (let k = 1; k < n; k++) {
        const a = (o + k - 1) * 3, b = (o + k) * 3;
        acc += Math.hypot(pos[b] - pos[a], pos[b + 1] - pos[a + 1], pos[b + 2] - pos[a + 2]);
        arc[o + k] = acc;
        idx.push(o + k - 1, o + k);
      }
    }
    this.lineIndexCount = idx.length;
    // 12k vertices fits Uint16 comfortably; assert rather than silently wrap.
    if (t.n_vertices > 65535) throw new Error('too many vertices for Uint16 indices');

    this.posBuf = this.buf(gl.ARRAY_BUFFER, pos);
    this.spdBuf = this.buf(gl.ARRAY_BUFFER, spd);
    this.arcBuf = this.buf(gl.ARRAY_BUFFER, arc);
    this.idxBuf = this.buf(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(idx));
  }

  buildBody() {
    const b = this.tracks.body;
    const v = [], nrm = [];
    const face = (p, n) => { for (const q of p) { v.push(...q); nrm.push(...n); } };
    const [x0, x1, y0, y1, z0, z1] = [b.x0, b.x1, b.y0, b.y1, b.z0, b.z1];
    const quad = (a, c, d, e, n) => face([a, c, d, a, d, e], n);
    quad([x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1], [0,0,1]);
    quad([x0,y0,z0],[x0,y1,z0],[x1,y1,z0],[x1,y0,z0], [0,0,-1]);
    quad([x0,y0,z0],[x1,y0,z0],[x1,y0,z1],[x0,y0,z1], [0,-1,0]);
    quad([x0,y1,z0],[x0,y1,z1],[x1,y1,z1],[x1,y1,z0], [0,1,0]);
    quad([x0,y0,z0],[x0,y0,z1],[x0,y1,z1],[x0,y1,z0], [-1,0,0]);
    quad([x1,y0,z0],[x1,y1,z0],[x1,y1,z1],[x1,y0,z1], [1,0,0]);
    this.bodyCount = v.length / 3;
    this.bodyPos = this.buf(this.gl.ARRAY_BUFFER, new Float32Array(v));
    this.bodyNrm = this.buf(this.gl.ARRAY_BUFFER, new Float32Array(nrm));
  }

  buildGround() {
    const b = this.tracks.bounds;
    const step = 0.1, v = [];
    const x0 = Math.floor(b.x[0] / step) * step, x1 = Math.ceil(b.x[1] / step) * step;
    const y0 = -0.6, y1 = 0.6;
    for (let x = x0; x <= x1 + 1e-9; x += step) v.push(x, y0, 0, x, y1, 0);
    for (let y = y0; y <= y1 + 1e-9; y += step) v.push(x0, y, 0, x1, y, 0);
    this.groundCount = v.length / 3;
    this.groundPos = this.buf(this.gl.ARRAY_BUFFER, new Float32Array(v));
  }

  buf(target, data) {
    const gl = this.gl, b = gl.createBuffer();
    gl.bindBuffer(target, b); gl.bufferData(target, data, gl.STATIC_DRAW);
    return b;
  }

  /* ---------- camera ---------- */
  eye() {
    const sp = Math.sin(this.phi), cp = Math.cos(this.phi);
    return [
      this.target[0] + this.radius * sp * Math.cos(this.theta),
      this.target[1] + this.radius * sp * Math.sin(this.theta),
      this.target[2] + this.radius * cp,
    ];
  }

  mvp() {
    const aspect = this.canvas.width / this.canvas.height;
    const proj = M4.perspective(Math.PI / 4, aspect, 0.01, 100);
    const view = M4.lookAt(this.eye(), this.target, [0, 0, 1]);
    return M4.mul(proj, view);
  }

  bindControls() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener('mousedown', (e) => {
      drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button === 2 };
      // The drag continues over the rest of the page, so without suppressing
      // selection the pointer highlights every panel it crosses.
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });
    c.addEventListener('contextmenu', (e) => e.preventDefault());
    window.addEventListener('mouseup', () => { drag = null; document.body.style.userSelect = ''; });
    window.addEventListener('mousemove', (e) => {
      if (!drag) return;
      e.preventDefault();
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.x = e.clientX; drag.y = e.clientY;
      if (drag.pan) {
        const s = this.radius * 0.0016;
        // Pan along the screen axes, not world axes, so dragging always moves
        // the model the way the cursor goes.
        const f = norm(sub(this.target, this.eye()));
        const r = norm(cross(f, [0, 0, 1]));
        const u = cross(r, f);
        for (let i = 0; i < 3; i++) this.target[i] += -r[i] * dx * s + u[i] * dy * s;
      } else {
        this.theta -= dx * 0.008;
        this.phi = Math.max(0.05, Math.min(Math.PI - 0.05, this.phi - dy * 0.008));
      }
    });
    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      this.radius = Math.max(0.15, Math.min(30, this.radius * Math.exp(e.deltaY * 0.0012)));
    }, { passive: false });

    // Touch: one finger orbits, two fingers pinch-zoom.
    let touch = null;
    c.addEventListener('touchstart', (e) => {
      touch = e.touches.length === 2
        ? { d: Math.hypot(e.touches[0].clientX - e.touches[1].clientX,
                          e.touches[0].clientY - e.touches[1].clientY) }
        : { x: e.touches[0].clientX, y: e.touches[0].clientY };
    }, { passive: true });
    c.addEventListener('touchmove', (e) => {
      if (!touch) return;
      if (e.touches.length === 2 && touch.d) {
        const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX,
                             e.touches[0].clientY - e.touches[1].clientY);
        this.radius = Math.max(0.15, Math.min(30, this.radius * (touch.d / d)));
        touch.d = d;
      } else if (e.touches.length === 1 && touch.x !== undefined) {
        this.theta -= (e.touches[0].clientX - touch.x) * 0.008;
        this.phi = Math.max(0.05, Math.min(Math.PI - 0.05,
                    this.phi - (e.touches[0].clientY - touch.y) * 0.008));
        touch.x = e.touches[0].clientX; touch.y = e.touches[0].clientY;
      }
      e.preventDefault();
    }, { passive: false });
    c.addEventListener('touchend', () => { touch = null; }, { passive: true });
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const r = this.canvas.getBoundingClientRect();
    this.canvas.width = Math.max(320, Math.round(r.width * dpr));
    this.canvas.height = Math.max(220, Math.round(r.height * dpr));
    this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
  }

  setAttrib(prog, name, buffer, size) {
    const gl = this.gl;
    const loc = gl.getAttribLocation(prog, name);
    if (loc < 0) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
  }

  draw(dt) {
    const gl = this.gl;
    this.time += dt * this.speed;
    const mvp = this.mvp();

    gl.clearColor(0.02, 0.027, 0.047, 1);
    gl.enable(gl.DEPTH_TEST);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    // Solid body first, with depth writes on, so streamlines behind it are
    // correctly hidden.
    gl.useProgram(this.solidProg);
    gl.uniformMatrix4fv(gl.getUniformLocation(this.solidProg, 'uMVP'), false, mvp);
    gl.uniform3f(gl.getUniformLocation(this.solidProg, 'uColor'), 0.91, 0.93, 0.96);
    gl.uniform1f(gl.getUniformLocation(this.solidProg, 'uAlpha'), 1.0);
    this.setAttrib(this.solidProg, 'aPos', this.bodyPos, 3);
    this.setAttrib(this.solidProg, 'aNormal', this.bodyNrm, 3);
    gl.drawArrays(gl.TRIANGLES, 0, this.bodyCount);

    // Additive blending for the lines: overlapping streamlines build up
    // brightness, which is what makes dense regions read as dense.
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
    gl.depthMask(false);

    gl.useProgram(this.lineProg);
    const u = (n) => gl.getUniformLocation(this.lineProg, n);
    gl.uniformMatrix4fv(u('uMVP'), false, mvp);
    gl.uniform1f(u('uTime'), this.time);
    gl.uniform1f(u('uSpeedMax'), this.tracks.speed_max);
    gl.uniform1f(u('uSpacing'), this.spacing);
    gl.uniform1f(u('uPulse'), this.pulse);
    gl.uniform1f(u('uBase'), 0.30);
    this.setAttrib(this.lineProg, 'aPos', this.posBuf, 1 * 3);
    this.setAttrib(this.lineProg, 'aSpeed', this.spdBuf, 1);
    this.setAttrib(this.lineProg, 'aArc', this.arcBuf, 1);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.idxBuf);
    gl.drawElements(gl.LINES, this.lineIndexCount, gl.UNSIGNED_SHORT, 0);

    // Ground grid, drawn last and dim so it reads as context, not data.
    gl.useProgram(this.solidProg);
    gl.uniformMatrix4fv(gl.getUniformLocation(this.solidProg, 'uMVP'), false, mvp);
    gl.uniform3f(gl.getUniformLocation(this.solidProg, 'uColor'), 0.16, 0.20, 0.28);
    gl.uniform1f(gl.getUniformLocation(this.solidProg, 'uAlpha'), 0.5);
    this.setAttrib(this.solidProg, 'aPos', this.groundPos, 3);
    this.setAttrib(this.solidProg, 'aNormal', this.groundPos, 3);
    gl.drawArrays(gl.LINES, 0, this.groundCount);

    gl.depthMask(true);
    gl.disable(gl.BLEND);
  }

  start() {
    if (this.running) return;
    this.running = true;
    let last = performance.now();
    const loop = (now) => {
      if (!this.running) return;
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      this.draw(dt);
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
  }

  stop() {
    this.running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
  }
}

window.Viewer3D = Viewer3D;
