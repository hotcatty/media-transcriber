/* React Bits Galaxy, vanilla WebGL.
   https://www.reactbits.dev/backgrounds/galaxy
   starSpeed 0.3, density 0.8, hueShift 0, speed 0.4, glow 0.1,
   saturation 0.1, mouseRepulsion, repulsion 0.5, twinkle 0.3, rotation 0.1, transparent */

const VERT = `
attribute vec2 uv;
attribute vec2 position;
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position, 0, 1);
}
`;

const FRAG = `
precision highp float;

uniform float uTime;
uniform vec3 uResolution;
uniform vec2 uFocal;
uniform vec2 uRotation;
uniform float uStarSpeed;
uniform float uDensity;
uniform float uHueShift;
uniform float uSpeed;
uniform vec2 uMouse;
uniform float uGlowIntensity;
uniform float uSaturation;
uniform bool uMouseRepulsion;
uniform float uTwinkleIntensity;
uniform float uRotationSpeed;
uniform float uRepulsionStrength;
uniform float uMouseActiveFactor;
uniform float uAutoCenterRepulsion;
uniform bool uTransparent;
uniform float uLightMode;

varying vec2 vUv;

#define NUM_LAYER 4.0
#define STAR_COLOR_CUTOFF 0.2
#define MAT45 mat2(0.7071, -0.7071, 0.7071, 0.7071)
#define PERIOD 3.0

float Hash21(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float tri(float x) {
  return abs(fract(x) * 2.0 - 1.0);
}

float tris(float x) {
  float t = fract(x);
  return 1.0 - smoothstep(0.0, 1.0, abs(2.0 * t - 1.0));
}

float trisn(float x) {
  float t = fract(x);
  return 2.0 * (1.0 - smoothstep(0.0, 1.0, abs(2.0 * t - 1.0))) - 1.0;
}

vec3 hsv2rgb(vec3 c) {
  vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

float Star(vec2 uv, float flare) {
  float d = length(uv);
  float m = (0.05 * uGlowIntensity) / d;
  float rays = smoothstep(0.0, 1.0, 1.0 - abs(uv.x * uv.y * 1000.0));
  m += rays * flare * uGlowIntensity;
  uv *= MAT45;
  rays = smoothstep(0.0, 1.0, 1.0 - abs(uv.x * uv.y * 1000.0));
  m += rays * 0.3 * flare * uGlowIntensity;
  m *= smoothstep(1.0, 0.2, d);
  return m;
}

vec3 StarLayer(vec2 uv) {
  vec3 col = vec3(0.0);
  vec2 gv = fract(uv) - 0.5;
  vec2 id = floor(uv);

  for (int y = -1; y <= 1; y++) {
    for (int x = -1; x <= 1; x++) {
      vec2 offset = vec2(float(x), float(y));
      vec2 si = id + vec2(float(x), float(y));
      float seed = Hash21(si);
      float size = fract(seed * 345.32);
      float glossLocal = tri(uStarSpeed / (PERIOD * seed + 1.0));
      float flareSize = smoothstep(0.9, 1.0, size) * glossLocal;

      float red = smoothstep(STAR_COLOR_CUTOFF, 1.0, Hash21(si + 1.0)) + STAR_COLOR_CUTOFF;
      float blu = smoothstep(STAR_COLOR_CUTOFF, 1.0, Hash21(si + 3.0)) + STAR_COLOR_CUTOFF;
      float grn = min(red, blu) * seed;
      vec3 base = vec3(red, grn, blu);

      float hue = atan(base.g - base.r, base.b - base.r) / (2.0 * 3.14159) + 0.5;
      hue = fract(hue + uHueShift / 360.0);
      float sat = length(base - vec3(dot(base, vec3(0.299, 0.587, 0.114)))) * uSaturation;
      float val = max(max(base.r, base.g), base.b);
      base = hsv2rgb(vec3(hue, sat, val));

      vec2 pad = vec2(tris(seed * 34.0 + uTime * uSpeed / 10.0), tris(seed * 38.0 + uTime * uSpeed / 30.0)) - 0.5;

      float star = Star(gv - offset - pad, flareSize);
      float twinkle = trisn(uTime * uSpeed + seed * 6.2831) * 0.5 + 1.0;
      twinkle = mix(1.0, twinkle, uTwinkleIntensity);
      star *= twinkle;
      col += star * size * base;
    }
  }
  return col;
}

void main() {
  vec2 focalPx = uFocal * uResolution.xy;
  vec2 uv = (vUv * uResolution.xy - focalPx) / uResolution.y;
  vec2 mouseNorm = uMouse - vec2(0.5);

  if (uAutoCenterRepulsion > 0.0) {
    vec2 centerUV = vec2(0.0, 0.0);
    float centerDist = length(uv - centerUV);
    vec2 repulsion = normalize(uv - centerUV) * (uAutoCenterRepulsion / (centerDist + 0.1));
    uv += repulsion * 0.05;
  } else if (uMouseRepulsion) {
    vec2 mousePosUV = (uMouse * uResolution.xy - focalPx) / uResolution.y;
    float mouseDist = length(uv - mousePosUV);
    vec2 repulsion = normalize(uv - mousePosUV) * (uRepulsionStrength / (mouseDist + 0.1));
    uv += repulsion * 0.05 * uMouseActiveFactor;
  } else {
    vec2 mouseOffset = mouseNorm * 0.1 * uMouseActiveFactor;
    uv += mouseOffset;
  }

  float autoRotAngle = uTime * uRotationSpeed;
  mat2 autoRot = mat2(cos(autoRotAngle), -sin(autoRotAngle), sin(autoRotAngle), cos(autoRotAngle));
  uv = autoRot * uv;
  uv = mat2(uRotation.x, -uRotation.y, uRotation.y, uRotation.x) * uv;

  vec3 col = vec3(0.0);
  for (float i = 0.0; i < 1.0; i += 1.0 / NUM_LAYER) {
    float depth = fract(i + uStarSpeed * uSpeed);
    float scale = mix(20.0 * uDensity, 0.5 * uDensity, depth);
    float fade = depth * smoothstep(1.0, 0.9, depth);
    col += StarLayer(uv * scale + i * 453.32) * fade;
  }

  if (uLightMode > 0.5) {
    float energy = max(max(col.r, col.g), col.b);
    float coverage = clamp(smoothstep(0.0, 0.42, energy) * 0.92, 0.0, 0.92);
    vec3 ink = clamp(col * 0.48, 0.0, 0.82);
    gl_FragColor = vec4(mix(vec3(1.0), ink, coverage), 1.0);
  } else if (uTransparent) {
    float alpha = length(col);
    alpha = smoothstep(0.0, 0.3, alpha);
    alpha = min(alpha, 1.0);
    gl_FragColor = vec4(col, alpha);
  } else {
    gl_FragColor = vec4(col, 1.0);
  }
}
`;

const CFG = {
  starSpeed: 0.3,
  density: 0.8,
  hueShift: 0,
  speed: 0.4,
  glowIntensity: 0.1,
  saturation: 0.1,
  mouseRepulsion: true,
  repulsionStrength: 0.5,
  twinkleIntensity: 0.3,
  rotationSpeed: 0.1,
};

function compile(gl, type, src) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, src);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    console.warn("[galaxy]", gl.getShaderInfoLog(shader));
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

function startGalaxy(canvas) {
  if (!canvas || !canvas.getContext) return;
  const gl = canvas.getContext("webgl", { alpha: true, antialias: true, premultipliedAlpha: false });
  if (!gl) return;

  const vs = compile(gl, gl.VERTEX_SHADER, VERT);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return;
  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.warn("[galaxy]", gl.getProgramInfoLog(program));
    return;
  }

  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
    -1, -1, 0, 0,
    3, -1, 2, 0,
    -1, 3, 0, 2,
  ]), gl.STATIC_DRAW);
  const pos = gl.getAttribLocation(program, "position");
  const uv = gl.getAttribLocation(program, "uv");
  gl.enableVertexAttribArray(pos);
  gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 16, 0);
  gl.enableVertexAttribArray(uv);
  gl.vertexAttribPointer(uv, 2, gl.FLOAT, false, 16, 8);

  const loc = (name) => gl.getUniformLocation(program, name);
  gl.useProgram(program);
  gl.uniform2f(loc("uFocal"), 0.5, 0.5);
  gl.uniform2f(loc("uRotation"), 1, 0);
  gl.uniform1f(loc("uDensity"), CFG.density);
  gl.uniform1f(loc("uHueShift"), CFG.hueShift);
  gl.uniform1f(loc("uSpeed"), CFG.speed);
  gl.uniform1f(loc("uGlowIntensity"), CFG.glowIntensity);
  gl.uniform1f(loc("uSaturation"), CFG.saturation);
  gl.uniform1i(loc("uMouseRepulsion"), CFG.mouseRepulsion ? 1 : 0);
  gl.uniform1f(loc("uTwinkleIntensity"), CFG.twinkleIntensity);
  gl.uniform1f(loc("uRotationSpeed"), CFG.rotationSpeed);
  gl.uniform1f(loc("uRepulsionStrength"), CFG.repulsionStrength);
  gl.uniform1f(loc("uAutoCenterRepulsion"), 0);
  gl.uniform1i(loc("uTransparent"), 1);
  gl.uniform1f(loc("uLightMode"), 0);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.clearColor(0, 0, 0, 0);

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  let width = 0;
  let height = 0;
  let frame = 0;
  const mouse = { x: 0.5, y: 0.5, tx: 0.5, ty: 0.5, active: 0, tactive: 0 };

  function resize() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const nextW = Math.max(1, Math.floor(window.innerWidth * dpr));
    const nextH = Math.max(1, Math.floor(window.innerHeight * dpr));
    if (nextW === width && nextH === height) return;
    width = nextW;
    height = nextH;
    canvas.width = width;
    canvas.height = height;
    gl.useProgram(program);
    gl.viewport(0, 0, width, height);
    gl.uniform3f(loc("uResolution"), width, height, width / height);
  }

  function draw(t) {
    const sec = t * 0.001;
    gl.useProgram(program);
    gl.uniform1f(loc("uTime"), sec);
    gl.uniform1f(loc("uStarSpeed"), (sec * CFG.starSpeed) / 10);
    mouse.x += (mouse.tx - mouse.x) * 0.05;
    mouse.y += (mouse.ty - mouse.y) * 0.05;
    mouse.active += (mouse.tactive - mouse.active) * 0.05;
    gl.uniform2f(loc("uMouse"), mouse.x, mouse.y);
    gl.uniform1f(loc("uMouseActiveFactor"), mouse.active);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  function tick(t) {
    if (document.hidden || reduced.matches) return;
    resize();
    draw(t);
    frame = requestAnimationFrame(tick);
  }

  function play() {
    if (reduced.matches || document.hidden) {
      resize();
      draw(0);
      return;
    }
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(tick);
  }

  window.addEventListener("mousemove", (e) => {
    mouse.tx = e.clientX / window.innerWidth;
    mouse.ty = 1 - e.clientY / window.innerHeight;
    mouse.tactive = 1;
  });
  window.addEventListener("mouseleave", () => {
    mouse.tactive = 0;
  });
  document.addEventListener("mouseleave", () => {
    mouse.tactive = 0;
  });
  window.addEventListener("resize", () => {
    resize();
    if (reduced.matches || document.hidden) draw(0);
  });
  document.addEventListener("visibilitychange", play);
  reduced.addEventListener("change", play);
  play();
}

function startStars2d(canvas) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d", { alpha: false });
  if (!ctx) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let w = 0;
  let h = 0;
  let stars = [];
  let frame = 0;

  function resize() {
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.max(1, Math.floor(w * dpr));
    canvas.height = Math.max(1, Math.floor(h * dpr));
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.min(420, Math.max(80, Math.floor((w * h) / 4200)));
    stars = Array.from({ length: n }, () => ({
      x: Math.random() * w,
      y: Math.random() * h,
      r: Math.random() * 1.15 + 0.15,
      a: Math.random(),
      tw: 0.5 + Math.random() * 1.4,
    }));
  }

  function tick(t) {
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, w, h);
    for (let i = 0; i < stars.length; i += 1) {
      const s = stars[i];
      const tw = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(t * 0.001 * s.tw + s.a * 6.2));
      ctx.fillStyle = `rgba(255,255,255,${(0.2 + s.a * 0.8) * tw})`;
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fill();
    }
    frame = requestAnimationFrame(tick);
  }

  window.addEventListener("resize", resize);
  resize();
  cancelAnimationFrame(frame);
  frame = requestAnimationFrame(tick);
}

document.addEventListener("DOMContentLoaded", () => {
  const canvas = document.getElementById("sky");
  try {
    if (window.pywebview || new URLSearchParams(location.search).get("desktop") === "1") {
      document.documentElement.classList.add("mt-desktop");
      startStars2d(canvas);
      return;
    }
  } catch (_) {}
  startGalaxy(canvas);
});
