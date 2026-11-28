// Alontito 3D Desktop Pet — рендерер (Three.js) для QWebEngineView.
//
// Python (desktop_pet.py) <-> JS обмен сообщениями:
//   JS -> Python : console.log('@@PET@@' + JSON.stringify({ev: ...}))
//   Python -> JS : PetApi.recv({cmd: ...})
//
// Команды (Python -> JS):
//   {cmd:'play', clip, loop?, fade?, speed?}      сыграть клип (idle, walk, sit_idle, drag_panic, ...)
//   {cmd:'face', deg, fast?}                       поворот персонажа: 0 = к пользователю, 90 = вправо, -90 = влево, 180 = спиной
//   {cmd:'portal', action:'run'|'cancel', item?}   последовательность «достаёт предмет из портала и ест/пьёт»
//   {cmd:'dissolve', dir:'out'|'in'}               испарение (скрытие) / материализация (показ)
//   {cmd:'fps', value}                             ограничение кадров
// События (JS -> Python): ready, layout, rects, grab, release, context, dblclick,
//                         clip_end, portal_done, dissolve_done, warn, error

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const Q = new URLSearchParams(location.search);
const DEBUG = Q.has('debug');
if (Q.get('bg')) document.body.style.background = '#' + Q.get('bg');
// база страницы: '/' на Qt-сервере, '/pet/' в приложении — все ресурсы рядом
const BASE = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
const CHAR_URL = Q.get('char') || (BASE + 'asset/character.glb');
const PORTAL_URL = Q.get('portal') || '';
let FPS = Math.max(10, Math.min(60, +(Q.get('fps') || 45)));
const MAX_DT = Math.max(0.05, Math.min(1, +(Q.get('maxdt') || 0.1)));   // потолок шага времени (тесты на медленном софтверном GL поднимают его)

const post = (msg) => console.log('@@PET@@' + JSON.stringify(msg));
window.addEventListener('error', (e) => post({ ev: 'error', msg: String(e.message || e) }));
window.addEventListener('unhandledrejection', (e) => post({ ev: 'error', msg: String(e.reason && e.reason.stack || e.reason) }));

// ------------------------------------------------------------------ сцена
const STAGE_W = 1.75;        // сколько метров сцены помещается в ширину окна
const FEET_V = 0.90;         // на какой доле высоты окна находятся ступни
const CHAR_U = 0.36;         // на какой доле ширины окна стоит персонаж
const CAM_DIST = 5.0;
const CHAR_FIT_K = 1.45;     // персонаж занимает ~70% высоты окна: по размеру,
                             // а не во весь экран; уточняется ростом модели
const PORTAL_Z = -0.50;      // портал справа на экране (= -Z), открыт к персонажу
const PORTAL_BASE = 0.17;    // портал висит над полом, слот на уровне руки
const PORTAL_H = 0.66;
const PORTAL_YAW = THREE.MathUtils.degToRad(-72);
const D2R = Math.PI / 180;

const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: Q.get('aa') === '1', alpha: true, premultipliedAlpha: true, powerPreference: 'low-power' });
renderer.setClearColor(0x000000, 0);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(20, 1, 0.1, 50);
let viewW = 1, viewH = 1, pxScale = 1000;
let charH = 1.7;                        // рост модели в метрах, уточняется в init()

function fit() {
  const w = Math.max(1, canvas.clientWidth), h = Math.max(1, canvas.clientHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.setSize(w, h, false);
  viewW = w; viewH = h;
  // На портретных окнах сцена ограничена шириной (STAGE_W), на широких-коротких
  // (веб-пульт на ПК) — высотой: персонаж по размеру, с воздухом вокруг.
  const visH = Math.max(STAGE_W / (w / h), charH * CHAR_FIT_K);
  camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan((visH / 2) / CAM_DIST));
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  const ty = (FEET_V - 0.5) * visH;
  const tz = (CHAR_U - 0.5) * STAGE_W;
  camera.position.set(CAM_DIST, ty, tz);
  camera.lookAt(0, ty, tz);
  camera.updateMatrixWorld(true);
  pxScale = h / (2 * Math.tan(camera.fov * D2R / 2));
  layoutDirty = true;
}
let layoutDirty = true;
new ResizeObserver(fit).observe(canvas);

// Процедурное окружение для PBR (без внешних HDR-файлов)
function buildEnvironment() {
  const pm = new THREE.PMREMGenerator(renderer);
  const env = new THREE.Scene();
  env.add(new THREE.Mesh(new THREE.SphereGeometry(10, 32, 16), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false,
    vertexShader: 'varying vec3 vP; void main(){ vP = normalize(position); gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
    fragmentShader: 'varying vec3 vP; void main(){ float h = vP.y*0.5+0.5; vec3 c = mix(vec3(0.10,0.06,0.22), vec3(0.50,0.62,0.95), pow(h,1.4)); gl_FragColor = vec4(c,1.0); }',
  })));
  const panel = (color, k, x, y, z, sx, sy) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(sx, sy), new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(k), side: THREE.DoubleSide }));
    m.position.set(x, y, z); m.lookAt(0, 0, 0); env.add(m);
  };
  panel(0xffffff, 5, 5, 6, 4, 5, 3);
  panel(0x38e8ff, 6, -6, 2, -4, 4, 6);
  panel(0xff4fd8, 4, -4, 1, 6, 3, 5);
  const tex = pm.fromScene(env, 0.03).texture;
  pm.dispose();
  return tex;
}
scene.environment = buildEnvironment();
scene.add(new THREE.HemisphereLight(0xbfd8ff, 0x3a2a5a, 1.1));
const key = new THREE.DirectionalLight(0xffffff, 2.4); key.position.set(3, 4, 2.5); scene.add(key);
const rimC = new THREE.DirectionalLight(0x38e8ff, 2.6); rimC.position.set(-2, 2, -3); scene.add(rimC);
const rimM = new THREE.DirectionalLight(0xff4fd8, 1.6); rimM.position.set(-2, 1, 3); scene.add(rimM);
const portalLight = new THREE.PointLight(0xa070ff, 0, 1.6, 2); scene.add(portalLight);

// ------------------------------------------------------------------ растворение (disintegrate)
const GLSL_NOISE = `
float hash31(vec3 p){ p = fract(p*0.3183099+vec3(.1,.2,.3)); p*=17.0; return fract(p.x*p.y*p.z*(p.x+p.y+p.z)); }
float vnoise(vec3 x){ vec3 i=floor(x), f=fract(x); f=f*f*(3.0-2.0*f);
  return mix(mix(mix(hash31(i+vec3(0,0,0)),hash31(i+vec3(1,0,0)),f.x), mix(hash31(i+vec3(0,1,0)),hash31(i+vec3(1,1,0)),f.x),f.y),
              mix(mix(hash31(i+vec3(0,0,1)),hash31(i+vec3(1,0,1)),f.x), mix(hash31(i+vec3(0,1,1)),hash31(i+vec3(1,1,1)),f.x),f.y),f.z); }`;

/** Делает материал «растворяемым»: uniform uDissolve 0..1 (0 — целый, 1 — испарился), светящаяся кромка. */
function makeDissolvable(material, { minY, height, edge }) {
  const u = { uDissolve: { value: 0 }, uMinY: { value: minY }, uHeight: { value: height }, uEdge: { value: new THREE.Color(edge) } };
  material.userData.dissolve = u;
  material.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, u);
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vDPos;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvDPos = position;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vDPos; uniform float uDissolve; uniform float uMinY; uniform float uHeight; uniform vec3 uEdge;\n' + GLSL_NOISE)
      .replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>
        float dKey = 1.0; float dThr = -1.0;
        if (uDissolve > 0.0005) {
          float h = clamp((vDPos.y - uMinY) / uHeight, 0.0, 1.0);
          float n = vnoise(vDPos * (22.0 / uHeight)) * 0.65 + vnoise(vDPos * (55.0 / uHeight)) * 0.35;
          dKey = n * 0.55 + (1.0 - h) * 0.45;
          dThr = uDissolve * 1.12 - 0.06;
          if (dKey < dThr) discard;
        }`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
        if (uDissolve > 0.0005) { float eg = 1.0 - smoothstep(0.0, 0.09, dKey - dThr); totalEmissiveRadiance += uEdge * eg * 2.0; }`);
  };
  material.customProgramCacheKey = () => 'alontito-dissolve';
  material.needsUpdate = true;
  return u;
}
const setDissolve = (list, v) => list.forEach((u) => { u.uDissolve.value = v; });

// ------------------------------------------------------------------ твины / частицы
const tweens = [];
function tween(dur, fn, { delay = 0, ease = (x) => x } = {}) {
  return new Promise((resolve) => tweens.push({ t: -delay, dur, fn, ease, resolve }));
}
function updateTweens(dt) {
  for (let i = tweens.length - 1; i >= 0; i--) {
    const tw = tweens[i];
    tw.t += dt;
    if (tw.t < 0) continue;
    const k = Math.min(1, tw.t / tw.dur);
    tw.fn(tw.ease(k), k);
    if (k >= 1) { tweens.splice(i, 1); tw.resolve(); }
  }
}
const sleep = (s) => tween(s, () => {});
const easeInOut = (x) => x * x * (3 - 2 * x);

const P_N = 220;
const pPos = new Float32Array(P_N * 3), pVel = new Float32Array(P_N * 3), pAge = new Float32Array(P_N).fill(1), pLife = new Float32Array(P_N).fill(1), pSize = new Float32Array(P_N);
const pGeo = new THREE.BufferGeometry();
pGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3));
pGeo.setAttribute('aAge', new THREE.BufferAttribute(pAge, 1));
pGeo.setAttribute('aSize', new THREE.BufferAttribute(pSize, 1));
const pMat = new THREE.ShaderMaterial({
  transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  uniforms: { uScale: { value: 1000 }, uColA: { value: new THREE.Color(0x4cf0ff) }, uColB: { value: new THREE.Color(0xc060ff) } },
  vertexShader: `attribute float aAge; attribute float aSize; uniform float uScale; varying float vA; varying float vM;
    void main(){ vA = 1.0 - aAge; vM = aAge; vec4 mv = modelViewMatrix * vec4(position, 1.0);
      gl_PointSize = max(1.0, aSize * uScale / -mv.z); gl_Position = projectionMatrix * mv; }`,
  fragmentShader: `uniform vec3 uColA; uniform vec3 uColB; varying float vA; varying float vM;
    void main(){ float d = length(gl_PointCoord - 0.5); if (d > 0.5 || vA <= 0.0) discard;
      float a = smoothstep(0.5, 0.05, d) * vA; gl_FragColor = vec4(mix(uColA, uColB, vM) * 1.6, a); }`,
});
const particles = new THREE.Points(pGeo, pMat);
particles.frustumCulled = false;
scene.add(particles);
let pCursor = 0;
function emit(n, cx, cy, cz, spread = [0.18, 0.1, 0.2], up = 0.35) {
  for (let k = 0; k < n; k++) {
    const i = pCursor; pCursor = (pCursor + 1) % P_N;
    pPos[i * 3] = cx + (Math.random() - 0.5) * 2 * spread[0];
    pPos[i * 3 + 1] = cy + (Math.random() - 0.5) * 2 * spread[1];
    pPos[i * 3 + 2] = cz + (Math.random() - 0.5) * 2 * spread[2];
    pVel[i * 3] = (Math.random() - 0.5) * 0.12;
    pVel[i * 3 + 1] = up * (0.4 + Math.random());
    pVel[i * 3 + 2] = (Math.random() - 0.5) * 0.12;
    pAge[i] = 0; pLife[i] = 0.7 + Math.random() * 0.9; pSize[i] = 0.012 + Math.random() * 0.02;
  }
}
function updateParticles(dt) {
  for (let i = 0; i < P_N; i++) {
    if (pAge[i] >= 1) continue;
    pAge[i] = Math.min(1, pAge[i] + dt / pLife[i]);
    pPos[i * 3] += pVel[i * 3] * dt; pPos[i * 3 + 1] += pVel[i * 3 + 1] * dt; pPos[i * 3 + 2] += pVel[i * 3 + 2] * dt;
    pVel[i * 3 + 1] *= (1 - 0.6 * dt);
  }
  pGeo.attributes.position.needsUpdate = true; pGeo.attributes.aAge.needsUpdate = true; pGeo.attributes.aSize.needsUpdate = true;
  pMat.uniforms.uScale.value = pxScale;
}

// ------------------------------------------------------------------ персонаж
const world = new THREE.Group(); scene.add(world);
const charPivot = new THREE.Group(); world.add(charPivot);          // позиция/поворот персонажа
const charOffset = new THREE.Group(); charPivot.add(charOffset);    // центрирование по бёдрам (x ≈ 0.07)
charOffset.position.x = -0.07;

let charRoot = null, bones = {}, REST = {}, DRIVEN = [], ANIM = null;
let mixer = null, current = null, currentName = '';
const clips = {};                 // name -> AnimationClip
const clipMeta = {};              // name -> {loop, props, meta}
const charDissolve = [];          // uniform-ы всех материалов персонажа
let portalPivot = null, portalGlow = null; const portalDissolve = [];
const target = { yaw: 0 };
let curYaw = 0;
let frozen = false;

const AX = { x: new THREE.Vector3(1, 0, 0), y: new THREE.Vector3(0, 1, 0), z: new THREE.Vector3(0, 0, 1) };
const axQ = (a, d) => new THREE.Quaternion().setFromAxisAngle(AX[a], d * D2R);
function opsQuat(ops) { const q = new THREE.Quaternion(); for (const [a, d] of ops) q.premultiply(axQ(a, d)); return q; }

const AIM_CHILD = {
  Waist: 'Spine01', Spine01: 'Spine02', Spine02: 'Head',
  R_Clavicle: 'R_Upperarm', L_Clavicle: 'L_Upperarm',
  R_Upperarm: 'R_Forearm', L_Upperarm: 'L_Forearm',
  R_Forearm: 'R_Hand', L_Forearm: 'L_Hand',
  R_Hand: '^R_Forearm', L_Hand: '^L_Forearm',          // '^' — направление «предплечье → кисть»
  R_Thigh: 'R_Calf', L_Thigh: 'L_Calf', R_Calf: 'R_Foot', L_Calf: 'L_Foot', R_Foot: 'R_ToeBase', L_Foot: 'L_ToeBase',
};

function captureRest(root) {
  root.updateMatrixWorld(true);
  root.traverse((o) => { if (o.isBone) bones[o.name] = o; });
  const v = new THREE.Vector3();
  for (const [n, b] of Object.entries(bones)) {
    REST[n] = {
      localQ: b.quaternion.clone(), localP: b.position.clone(),
      worldQ: b.getWorldQuaternion(new THREE.Quaternion()), worldP: b.getWorldPosition(new THREE.Vector3()),
      parentWorldQ: b.parent ? b.parent.getWorldQuaternion(new THREE.Quaternion()) : new THREE.Quaternion(),
    };
  }
  for (const [n, c] of Object.entries(AIM_CHILD)) {
    if (!REST[n]) continue;
    let dir;
    if (c.startsWith('^')) { const f = c.slice(1); dir = v.clone().subVectors(REST[n].worldP, REST[f].worldP); }
    else if (REST[c]) dir = v.clone().subVectors(REST[c].worldP, REST[n].worldP);
    if (dir && dir.lengthSq() > 1e-10) REST[n].dir = dir.normalize().clone();
  }
  if (REST.Head) REST.Head.dir = new THREE.Vector3(0, 1, 0);
}

function nearestDrivenAncestor(name) {
  let o = bones[name] && bones[name].parent;
  while (o) { if (o.isBone && DRIVEN.includes(o.name)) return o.name; o = o.parent; }
  return null;
}

/** Ключ -> эффективные мировые повороты (относительно позы покоя) всех управляемых костей. */
function resolveKey(k) {
  const eff = {};
  for (const bn of DRIVEN) {
    const spec = k.b[bn];
    const pa = nearestDrivenAncestor(bn);
    const pe = pa ? eff[pa] : new THREE.Quaternion();
    let W;
    if (!spec) W = pe.clone();
    else if (spec.aim) {
      if (!REST[bn].dir) throw new Error('нет rest-направления для ' + bn);
      W = new THREE.Quaternion().setFromUnitVectors(REST[bn].dir, new THREE.Vector3(...spec.aim).normalize());
    } else if (spec.rot) W = pe.clone().multiply(opsQuat(spec.rot));
    else if (spec.abs) W = opsQuat(spec.abs);
    else W = pe.clone();
    eff[bn] = W;
  }
  return { eff, hip: k.hip || [0, 0, 0] };
}

/** «Запекает» описание клипа из animations.json в THREE.AnimationClip под скелет модели. */
function bakeClip(name, def) {
  const fps = ANIM.fps || 30;
  const N = Math.max(2, Math.round(def.duration * fps) + 1);
  const keys = def.keys, rk = keys.map(resolveKey);
  const times = new Float32Array(N);
  const qv = {}; DRIVEN.forEach((n) => { qv[n] = new Float32Array(N * 4); });
  const hv = new Float32Array(N * 3);
  const q = new THREE.Quaternion(), prev = {}, effS = {};
  const inv = new THREE.Quaternion(), loc = new THREE.Quaternion(), hipV = new THREE.Vector3();
  let ki = 0;
  for (let i = 0; i < N; i++) {
    const t = (i / (N - 1)) * def.duration; times[i] = t;
    while (ki < keys.length - 2 && t > keys[ki + 1].t) ki++;
    const k0 = keys[ki], k1 = keys[Math.min(ki + 1, keys.length - 1)];
    let u = k1.t > k0.t ? (t - k0.t) / (k1.t - k0.t) : 0; u = Math.max(0, Math.min(1, u));
    if (def.ease !== 'linear') u = u * u * (3 - 2 * u);
    for (const bn of DRIVEN) effS[bn] = rk[ki].eff[bn].clone().slerp(rk[Math.min(ki + 1, keys.length - 1)].eff[bn], u);
    for (const bn of DRIVEN) {
      const pa = nearestDrivenAncestor(bn);
      const pe = pa ? effS[pa] : new THREE.Quaternion();
      // local = Rp_imm^-1 · eff(parent)^-1 · eff(b) · R_b
      loc.copy(REST[bn].parentWorldQ).invert();
      inv.copy(pe).invert();
      loc.multiply(inv).multiply(effS[bn]).multiply(REST[bn].worldQ);
      if (prev[bn] && prev[bn].dot(loc) < 0) { loc.x *= -1; loc.y *= -1; loc.z *= -1; loc.w *= -1; }
      prev[bn] = (prev[bn] || new THREE.Quaternion()).copy(loc);
      qv[bn].set([loc.x, loc.y, loc.z, loc.w], i * 4);
    }
    const a = rk[ki].hip, b = rk[Math.min(ki + 1, keys.length - 1)].hip;
    hipV.set(a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u);
    hipV.applyQuaternion(q.copy(REST.Hip.parentWorldQ).invert()).add(REST.Hip.localP);
    hv.set([hipV.x, hipV.y, hipV.z], i * 3);
  }
  const tracks = DRIVEN.map((bn) => new THREE.QuaternionKeyframeTrack(bn + '.quaternion', times, qv[bn]));
  tracks.push(new THREE.VectorKeyframeTrack('Hip.position', times, hv));
  return new THREE.AnimationClip(name, def.duration, tracks);
}

// ------------------------------------------------------------------ пропсы (ноутбук, кубик Рубика, предметы)
const props = {};
function stdMat(color, o = {}) { return new THREE.MeshStandardMaterial({ color, roughness: 0.45, metalness: 0.5, ...o }); }
function buildLaptop() {
  const g = new THREE.Group(); g.position.set(0.20, 0, 0);
  const base = new THREE.Mesh(new THREE.BoxGeometry(0.13, 0.010, 0.19), stdMat(0x1c1c2a));
  base.position.y = 0.006; g.add(base);
  const kb = new THREE.Mesh(new THREE.PlaneGeometry(0.10, 0.15), new THREE.MeshBasicMaterial({ color: 0x5a3cff, transparent: true, opacity: 0.55 }));
  kb.rotation.x = -Math.PI / 2; kb.position.set(-0.004, 0.0121, 0); g.add(kb);
  const hinge = new THREE.Group(); hinge.position.set(0.065, 0.011, 0); hinge.rotation.z = -12 * D2R; g.add(hinge);
  const lid = new THREE.Mesh(new THREE.BoxGeometry(0.007, 0.12, 0.19), stdMat(0x22223a));
  lid.position.y = 0.06; hinge.add(lid);
  const logo = new THREE.Mesh(new THREE.CircleGeometry(0.018, 24), new THREE.MeshBasicMaterial({ color: 0x35f2ff }));
  logo.rotation.y = Math.PI / 2; logo.position.set(0.0042, 0.06, 0); hinge.add(logo);
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(0.17, 0.10), new THREE.MeshBasicMaterial({ color: 0x78c8ff }));
  screen.rotation.y = -Math.PI / 2; screen.position.set(-0.0042, 0.06, 0); hinge.add(screen);
  return g;
}
const RUBIK_COL = { px: 0xd62828, nx: 0xf77f00, py: 0xffffff, ny: 0xffd60a, pz: 0x2a9d3f, nz: 0x1d5fd6 };
function buildRubik() {
  const g = new THREE.Group(); g.position.set(0.25, 0.26, 0);
  const cube = new THREE.Group(); g.add(cube); g.userData.cube = cube; g.userData.cubies = [];
  const s = 0.030, gap = 0.0015;
  const geo = new THREE.BoxGeometry(s, s, s);
  for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++) {
    const faces = ['px', 'nx', 'py', 'ny', 'pz', 'nz'];
    const mats = faces.map((f) => {
      const outward = (f === 'px' && x === 1) || (f === 'nx' && x === -1) || (f === 'py' && y === 1) || (f === 'ny' && y === -1) || (f === 'pz' && z === 1) || (f === 'nz' && z === -1);
      return new THREE.MeshStandardMaterial({ color: outward ? RUBIK_COL[f] : 0x101010, roughness: 0.35, metalness: 0.0 });
    });
    const m = new THREE.Mesh(geo, mats);
    m.position.set(x * (s + gap), y * (s + gap), z * (s + gap));
    cube.add(m); g.userData.cubies.push(m);
  }
  g.userData.timer = 0.3; g.userData.busy = false;
  return g;
}
function updateRubik(dt) {
  const g = props.rubik; if (!g || !g.visible) return;
  g.userData.cube.rotation.y += dt * 0.5; g.userData.cube.rotation.x = Math.sin(performance.now() / 1300) * 0.3;
  g.userData.timer -= dt;
  if (g.userData.timer > 0 || g.userData.busy) return;
  g.userData.busy = true; g.userData.timer = 0.25 + Math.random() * 0.4;
  const axis = ['x', 'y', 'z'][Math.floor(Math.random() * 3)], layer = Math.floor(Math.random() * 3) - 1, sgn = Math.random() < 0.5 ? 1 : -1;
  const cube = g.userData.cube, step = 0.0315;
  const pivot = new THREE.Group(); cube.add(pivot);
  const sel = g.userData.cubies.filter((c) => Math.abs(c.position[axis] - layer * step) < 0.01);
  sel.forEach((c) => pivot.attach(c));
  tween(0.28, (k) => { pivot.rotation[axis] = sgn * k * Math.PI / 2; }, { ease: easeInOut }).then(() => {
    sel.forEach((c) => { cube.attach(c); c.position.set(Math.round(c.position.x / step) * step, Math.round(c.position.y / step) * step, Math.round(c.position.z / step) * step); });
    cube.remove(pivot); g.userData.busy = false;
  });
}
function buildItem(kind) {
  const g = new THREE.Group();
  if (kind === 'cup') {
    const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.026, 0.020, 0.058, 20), stdMat(0xff4fd8, { roughness: 0.3, metalness: 0.1, emissive: 0x551040 }));
    const lid = new THREE.Mesh(new THREE.CylinderGeometry(0.027, 0.027, 0.006, 20), stdMat(0xffffff, { roughness: 0.3, metalness: 0 }));
    lid.position.y = 0.032; const straw = new THREE.Mesh(new THREE.CylinderGeometry(0.003, 0.003, 0.05, 8), stdMat(0x35f2ff, { emissive: 0x0a5a66 }));
    straw.position.set(0.004, 0.058, 0); straw.rotation.z = -0.15; g.add(cup, lid, straw);
  } else if (kind === 'cookie') {
    const c = new THREE.Mesh(new THREE.CylinderGeometry(0.032, 0.032, 0.012, 24), stdMat(0xc88a3a, { roughness: 0.9, metalness: 0 }));
    for (let i = 0; i < 6; i++) { const ch = new THREE.Mesh(new THREE.SphereGeometry(0.0045, 8, 8), stdMat(0x3a2210, { roughness: 1, metalness: 0 })); ch.position.set((Math.random() - 0.5) * 0.04, 0.006, (Math.random() - 0.5) * 0.04); g.add(ch); }
    g.add(c);
  } else {
    const orb = new THREE.Mesh(new THREE.SphereGeometry(0.03, 20, 20), new THREE.MeshStandardMaterial({ color: 0x35f2ff, emissive: 0x35f2ff, emissiveIntensity: 1.6, roughness: 0.2, metalness: 0 }));
    g.add(orb);
  }
  return g;
}

function setProps(list) {
  for (const [name, obj] of Object.entries(props)) {
    const want = list.includes(name);
    if (want && !obj.visible) { obj.visible = true; obj.scale.setScalar(0.01); tween(0.35, (k) => obj.scale.setScalar(Math.max(0.01, k)), { ease: easeInOut }); }
    if (!want && obj.visible) {
      tween(0.2, (k) => obj.scale.setScalar(Math.max(0.01, 1 - k))).then(() => {
        const still = clipMeta[currentName] && clipMeta[currentName].props.includes(name);
        if (!still) obj.visible = false; else obj.scale.setScalar(1);
      });
    }
  }
}

// ------------------------------------------------------------------ воспроизведение клипов
const waiters = {};              // clipName -> [resolve]
function play(name, { loop, fade = 0.25, speed = 1 } = {}) {
  const clip = clips[name];
  if (!clip) { post({ ev: 'warn', msg: 'нет клипа ' + name }); return null; }
  const isLoop = loop === undefined ? clipMeta[name].loop : loop;
  const next = mixer.clipAction(clip);
  if (!current || current !== next || !isLoop) next.reset();          // reset() сбрасывает weight/timeScale — делаем до их установки
  next.enabled = true; next.setEffectiveWeight(1); next.setEffectiveTimeScale(speed);
  next.setLoop(isLoop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
  next.clampWhenFinished = !isLoop;
  if (current && current !== next) next.crossFadeFrom(current, Math.max(0.001, fade), false);
  next.play();
  current = next; currentName = name;
  setProps(clipMeta[name].props || []);
  return next;
}
function playOnce(name, { fade = 0.3, onTime = [] } = {}) {
  return new Promise((resolve) => {
    const act = play(name, { loop: false, fade });
    if (!act) { resolve(); return; }
    const pending = onTime.slice().sort((a, b) => a.t - b.t);
    const unwatch = tween(clips[name].duration + 0.5, () => {
      while (pending.length && act.time >= pending[0].t) pending.shift().fn();
    });
    let done = false;
    const fin = () => { if (done) return; done = true; resolve(); };
    (waiters[name] = waiters[name] || []).push(fin);
    unwatch.then(fin);
  });
}

// ------------------------------------------------------------------ портал + взаимодействие
let seqToken = 0, seqOpen = false;
async function openPortal() {
  if (!portalPivot) return;
  portalPivot.visible = true; seqOpen = true;
  await tween(0.9, (k) => { setDissolve(portalDissolve, 1 - k); portalGlow.material.opacity = k * 0.6; portalLight.intensity = k * 2.2; }, { ease: easeInOut });
  const b = new THREE.Vector3(0, PORTAL_BASE + PORTAL_H / 2, PORTAL_Z); emit(40, b.x, b.y, b.z, [0.04, 0.25, 0.1], 0.2);
}
async function closePortal(fast = false) {
  if (!portalPivot || !seqOpen) return;
  seqOpen = false;
  await tween(fast ? 0.35 : 0.8, (k) => { setDissolve(portalDissolve, k); portalGlow.material.opacity = (1 - k) * 0.6; portalLight.intensity = (1 - k) * 2.2; }, { ease: easeInOut });
  if (!seqOpen) portalPivot.visible = false;
}
let heldItem = null;
function spawnItem(kind, boneName) {
  dropItem(true);
  const it = buildItem(kind); const bone = bones[boneName];
  it.userData.base = 1.45;
  const ws = bone.getWorldScale(new THREE.Vector3());
  it.scale.setScalar(0.01 / Math.max(1e-6, ws.x));            // стартуем с «поп»-масштаба
  it.position.set(0, 0.05 / Math.max(1e-6, ws.x), 0);          // от запястья к пальцам (ось Y кости)
  bone.add(it); heldItem = { obj: it, ws: ws.x };
  tween(0.25, (k) => it.scale.setScalar(1.45 * Math.max(0.01, k) / Math.max(1e-6, ws.x)), { ease: easeInOut });
  const p = it.getWorldPosition(new THREE.Vector3()); emit(26, p.x, p.y, p.z, [0.03, 0.03, 0.03], 0.25);
}
function dropItem(instant = false) {
  if (!heldItem) return; const h = heldItem; heldItem = null;
  const p = h.obj.getWorldPosition(new THREE.Vector3());
  if (instant) { h.obj.removeFromParent(); return; }
  emit(34, p.x, p.y, p.z, [0.03, 0.03, 0.03], 0.3);
  tween(0.3, (k) => h.obj.scale.setScalar(1.45 * Math.max(0.01, 1 - k) / Math.max(1e-6, h.ws))).then(() => h.obj.removeFromParent());
}

async function portalSequence(kind) {
  if (!portalPivot) { post({ ev: 'portal_done', ok: false, reason: 'портал не загружен' }); return; }
  const my = ++seqToken; const alive = () => my === seqToken;
  const prevYaw = target.yaw;
  const item = kind || ['cup', 'orb', 'cookie'][Math.floor(Math.random() * 3)];
  const rm = clipMeta.portal_reach_in.meta || {}, em = clipMeta.portal_eat_drink.meta || {};
  await openPortal(); if (!alive()) return;
  target.yaw = 38 * D2R;                       // чуть разворачивается к порталу
  await sleep(0.45); if (!alive()) return;
  await playOnce('portal_reach_in', { onTime: [{ t: rm.itemAt || 1.5, fn: () => { spawnItem(item, rm.handBone || 'L_Hand'); } }] });
  if (!alive()) return;
  target.yaw = 0;                              // разворачивается обратно к пользователю
  const closing = closePortal();
  await playOnce('portal_eat_drink', { onTime: [{ t: em.vanishAt || 2.5, fn: () => dropItem() }] });
  await closing;
  if (!alive()) return;
  target.yaw = prevYaw;
  post({ ev: 'portal_done', ok: true });
}
function cancelPortal() {
  seqToken++;
  dropItem();
  closePortal(true);
  (waiters.portal_reach_in || []).splice(0).forEach((f) => f());
  (waiters.portal_eat_drink || []).splice(0).forEach((f) => f());
}

// ------------------------------------------------------------------ испарение / материализация
let hidden = true;
async function dissolveOut() {
  cancelPortal();
  play('disintegrate', { fade: 0.2 });
  const t0 = performance.now();
  await tween(1.5, (k) => {
    setDissolve(charDissolve, k);
    emit(Math.round(10 + 18 * k), 0, (1 - k) * 0.95 + 0.03, 0, [0.20, 0.07, 0.22], 0.4);
  }, { delay: 0.25, ease: (x) => x * x * (3 - 2 * x) });
  charPivot.visible = false; hidden = true;
  post({ ev: 'dissolve_done', dir: 'out', ms: Math.round(performance.now() - t0) });
}
async function dissolveIn() {
  charPivot.visible = true; hidden = false;
  play('idle', { fade: 0.01 });
  await tween(1.3, (k) => {
    setDissolve(charDissolve, 1 - k);
    emit(Math.round(26 * (1 - k)), 0, (1 - k) * 0.95 + 0.03, 0, [0.20, 0.07, 0.22], -0.15);
  }, { ease: (x) => x * x * (3 - 2 * x) });
  setDissolve(charDissolve, 0);
  post({ ev: 'dissolve_done', dir: 'in' });
}

// ------------------------------------------------------------------ хит-боксы и раскладка для Python
const _v = new THREE.Vector3();
function project(x, y, z) { _v.set(x, y, z).project(camera); return [(_v.x * 0.5 + 0.5) * viewW, (-_v.y * 0.5 + 0.5) * viewH]; }
let lastRects = '';
function computeRects(force = false) {
  const rects = [];
  if (charPivot.visible && charRoot) {
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    const add = (v) => { const p = project(v.x, v.y, v.z); x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]); };
    const wp = new THREE.Vector3();
    for (const b of Object.values(bones)) add(b.getWorldPosition(wp));
    const hp = bones.Head.getWorldPosition(wp.clone()); add(hp.clone().add(new THREE.Vector3(0, 0.46, 0)));
    const gp = charPivot.getWorldPosition(wp.clone()); add(gp); add(gp.clone().add(new THREE.Vector3(0, 0, 0.2))); add(gp.clone().add(new THREE.Vector3(0, 0, -0.2)));
    const m = 0.09 * pxScale / CAM_DIST;
    rects.push([x0 - m, y0 - m, x1 - x0 + 2 * m, y1 - y0 + 2 * m]);
  }
  if (portalPivot && portalPivot.visible && seqOpen) {
    const p0 = project(0, PORTAL_BASE - 0.03, PORTAL_Z - 0.16), p1 = project(0, PORTAL_BASE + PORTAL_H + 0.05, PORTAL_Z + 0.16);
    rects.push([Math.min(p0[0], p1[0]), Math.min(p0[1], p1[1]), Math.abs(p1[0] - p0[0]), Math.abs(p1[1] - p0[1])]);
  }
  const s = JSON.stringify(rects.map((r) => r.map((n) => Math.round(n / 3) * 3)));
  if (force || s !== lastRects) { lastRects = s; post({ ev: 'rects', rects: rects.map((r) => r.map((n) => Math.round(n))) }); }
}
function postLayout() {
  const f = project(0, 0, 0), l = project(0, 0, 0.0);
  post({ ev: 'layout', w: viewW, h: viewH, feet_y: Math.round(f[1]), center_x: Math.round(l[0]), ppm: pxScale / CAM_DIST });
  layoutDirty = false;
}

// ------------------------------------------------------------------ веб-взаимодействия (?mode=web)
// Повадки домашнего питомца: тычок, поглаживание, поворот за пальцем и
// собственная жизнь в паузах. На рабочем столе хостом командует Python,
// поэтому всё это включается только в веб-iframe (?mode=web).
// Все хелперы — константы уровня модуля: в ES-модуле function-декларации
// внутри if-блоков не видны снаружи.
const WEB_MODE = Q.get('mode') === 'web';
let lastHostCmd = performance.now();
let asleep = false;

function hostTouch() {                  // любая команда хоста / касание
  lastHostCmd = performance.now();
  if (asleep) { asleep = false; play('idle', { fade: 0.4 }); }
}

const HEAD_REACTIONS = ['happy', 'laugh', 'excited'];     // гладишь по голове
const BODY_REACTIONS = ['surprised', 'shy', 'annoyed'];   // трогаешь за тело
const IDLE_VARIATIONS = ['stretch', 'yawn', 'peek'];      // чем заняться в тишине
const DREAM_MOVES = ['backflip', 'spin_360'];             // двойной клик
let _lastReact = 0, _lastIdleAct = 0;
let _downX = 0, _downY = 0, _downT = 0, _dragTurned = false, _strokeAcc = 0, _lastStrokeX = 0;

const charRectHit = WEB_MODE ? function () {
  if (!charPivot.visible || !charRoot) return null;
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  const wp = new THREE.Vector3();
  const add = (v) => { const p = project(v.x, v.y, v.z); x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]); };
  for (const b of Object.values(bones)) add(b.getWorldPosition(wp));
  const m = 0.05 * pxScale / CAM_DIST;
  return { x: x0 - m, y: y0 - m, w: x1 - x0 + 2 * m, h: y1 - y0 + 2 * m };
} : function () { return null; };

const busyReact = function () {
  return hidden || seqOpen || (!!currentName && currentName !== 'idle' && currentName !== 'sleepy');
};

const oneShot = function (list, cooldownMs) {
  const now = performance.now();
  if (busyReact() || now - _lastReact < cooldownMs) return Promise.resolve();
  _lastReact = now;
  const clip = list[Math.floor(Math.random() * list.length)];
  return playOnce(clip, { fade: 0.2 }).then(() => {
    if (!hidden && !seqOpen && currentName === clip) play('idle', { fade: 0.35 });
  });
};

const webDown = WEB_MODE ? function (e) {
  hostTouch();
  _downX = e.clientX; _downY = e.clientY; _downT = performance.now();
  _dragTurned = false; _strokeAcc = 0; _lastStrokeX = e.clientX;
} : function () {};

const webMove = WEB_MODE ? function (e) {
  if (!dragging) return;
  const dx = e.clientX - _downX;
  if (Math.abs(dx) > 8) _dragTurned = true;
  if (_dragTurned) target.yaw = THREE.MathUtils.clamp(dx / 220, -1.2, 1.2);   // смотрит за пальцем
  const r = charRectHit();
  if (r && e.clientX >= r.x && e.clientX <= r.x + r.w && e.clientY >= r.y && e.clientY <= r.y + r.h) {
    _strokeAcc += Math.abs(e.clientX - _lastStrokeX);
    if (_strokeAcc > 260) { _strokeAcc = 0; oneShot(HEAD_REACTIONS, 6000); }  // погладил
  }
  _lastStrokeX = e.clientX;
} : function () {};

const webUp = WEB_MODE ? function (e) {
  const quick = performance.now() - _downT < 450 && !_dragTurned &&
                Math.abs(e.clientX - _downX) < 8 && Math.abs(e.clientY - _downY) < 8;
  const r = charRectHit();
  const inside = !!r && e.clientX >= r.x && e.clientX <= r.x + r.w &&
                 e.clientY >= r.y && e.clientY <= r.y + r.h;
  if (quick && inside) {
    const headish = (e.clientY - r.y) < r.h * 0.45;
    oneShot(headish ? HEAD_REACTIONS : BODY_REACTIONS, 2200);                 // тычок
  }
  if (_dragTurned) target.yaw = 0;                     // вернуться лицом к зрителю
  _strokeAcc = 0;
} : function () {};

const idleBrain = WEB_MODE ? function (now) {
  // своя жизнь: в тишине агент потягивается, зевает, выглядывает; задремлет,
  // если тишина затянулась; любое касание или команда хоста будит
  if (hidden || seqOpen || asleep) return;
  if (currentName !== 'idle' && currentName !== 'sleepy') return;
  const quiet = now - lastHostCmd;
  if (quiet > 150000 && now - _lastIdleAct > 150000) {
    asleep = true; play('sleepy', { fade: 0.6 }); _lastIdleAct = now; return;
  }
  if (quiet > 35000 && now - _lastIdleAct > 35000 && currentName === 'idle') {
    _lastIdleAct = now;
    const clip = IDLE_VARIATIONS[Math.floor(Math.random() * IDLE_VARIATIONS.length)];
    playOnce(clip, { fade: 0.5 }).then(() => {
      if (!hidden && !seqOpen && currentName === clip) play('idle', { fade: 0.4 });
    });
  }
} : function () {};

if (WEB_MODE) {
  canvas.addEventListener('dblclick', () => { hostTouch(); if (!busyReact()) oneShot(DREAM_MOVES, 1500); });
}

// ------------------------------------------------------------------ ввод мыши
let dragging = false;
canvas.addEventListener('pointerdown', (e) => {
  if (e.button !== 0) return;
  webDown(e);
  dragging = true; canvas.setPointerCapture(e.pointerId);
  post({ ev: 'grab', x: e.clientX, y: e.clientY });
});
canvas.addEventListener('pointermove', webMove);
const endDrag = () => { if (dragging) { dragging = false; post({ ev: 'release' }); } };
canvas.addEventListener('pointerup', (e) => { webUp(e); endDrag(); });
canvas.addEventListener('pointercancel', endDrag);
canvas.addEventListener('contextmenu', (e) => { e.preventDefault(); post({ ev: 'context', x: e.clientX, y: e.clientY }); });
canvas.addEventListener('dblclick', (e) => post({ ev: 'dblclick', x: e.clientX, y: e.clientY }));

// ------------------------------------------------------------------ API для Python
function lerpAngle(a, b, k) { let d = ((b - a + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI; return a + d * k; }
const PetApi = {
  recv(m) {
    try {
      hostTouch();
      switch (m.cmd) {
        case 'play': if (!hidden || m.clip === 'disintegrate') play(m.clip, { loop: m.loop, fade: m.fade ?? 0.25, speed: m.speed ?? 1 }); break;
        case 'face': target.yaw = (m.deg || 0) * D2R; if (m.fast) curYaw = target.yaw; break;
        case 'portal': if (m.action === 'cancel') cancelPortal(); else if (!hidden) portalSequence(m.item); break;
        case 'dissolve': if (m.dir === 'out') dissolveOut(); else dissolveIn(); break;
        case 'fps': FPS = Math.max(10, Math.min(144, +m.value || 60)); break;
        case 'ping': post({ ev: 'pong', t: m.t }); layoutDirty = true; computeRects(true); break;
        default: post({ ev: 'warn', msg: 'неизвестная команда ' + m.cmd });
      }
    } catch (err) { post({ ev: 'error', msg: String(err && err.stack || err) }); }
  },
};
window.PetApi = PetApi;

// ------------------------------------------------------------------ загрузка
async function init() {
  fit();
  const loader = new GLTFLoader();
  const [animRes, chGltf] = await Promise.all([
    fetch(BASE + 'animations.json').then((r) => r.json()),
    loader.loadAsync(CHAR_URL),
  ]);
  ANIM = animRes; DRIVEN = ANIM.driven;
  charRoot = chGltf.scene;
  captureRest(charRoot);
  for (const n of DRIVEN) if (!bones[n]) throw new Error('В модели нет кости ' + n + ' — риг отличается от ожидаемого (Tripo).');

  // рост модели в метрах — кадр подгоняется под него (персонаж по размеру)
  const chBox = new THREE.Box3().setFromObject(charRoot);
  charH = Math.max(0.5, chBox.getSize(new THREE.Vector3()).y);
  layoutDirty = true;

  charRoot.traverse((o) => {
    if (!o.isMesh) return;
    o.frustumCulled = false;
    o.geometry.computeBoundingBox();
    const bb = o.geometry.boundingBox;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    mats.forEach((m) => { m.envMapIntensity = 0.9; charDissolve.push(makeDissolvable(m, { minY: bb.min.y, height: Math.max(1e-3, bb.max.y - bb.min.y), edge: 0x35f2ff })); });
  });
  charOffset.add(charRoot);

  // клипы: настоящие из .glb имеют приоритет, остальные — процедурные.
  // Статичные позы из старого экспорта (1 канал, ~1с, без ключей) игнорируем:
  // они убивают живую анимацию — персонаж навсегда застывает в T-позе.
  // Настоящая запечённая анимация = много каналов (≥3) и не единственный кадр.
  mixer = new THREE.AnimationMixer(charRoot);
  const baked = {}; (chGltf.animations || []).forEach((a) => { baked[a.name.toLowerCase()] = a; });
  const bakedStats = (anim) => {
    let d = 0;
    for (const s of (anim.samplers || [])) {
      const acc = chGltf.accessors[s.input];
      d = Math.max(d, (acc && acc.max ? acc.max[0] : 0));
    }
    return { dur: d, channels: (anim.channels || []).length };
  };
  const source = {};
  for (const [name, def] of Object.entries(ANIM.clips)) {
    const b = baked[name];
    const st = b ? bakedStats(b) : null;
    if (b && st.channels >= 3 && st.dur > 0.3) { clips[name] = b; source[name] = 'glb'; }
    else { clips[name] = bakeClip(name, def); source[name] = 'procedural'; }
    clipMeta[name] = { loop: def.loop, props: def.props || [], meta: def.meta || {} };
  }
  mixer.addEventListener('finished', (e) => {
    const nm = e.action.getClip().name;
    post({ ev: 'clip_end', name: nm });
    (waiters[nm] || []).splice(0).forEach((f) => f());
  });

  // пропсы
  props.laptop = buildLaptop(); props.rubik = buildRubik();
  for (const p of Object.values(props)) { p.visible = false; charPivot.add(p); }

  // портал: мгновенный процедурный + асинхронная подгрузка .glb в фоне (не блокирует старт)
  try {
    const portalGroup = new THREE.Group();
    const torusGeo = new THREE.TorusGeometry(0.24, 0.022, 16, 48);
    const torusMat = new THREE.MeshStandardMaterial({ color: 0x8a2be2, emissive: 0xa020f0, emissiveIntensity: 1.5, roughness: 0.2, metalness: 0.8 });
    portalDissolve.push(makeDissolvable(torusMat, { minY: -0.3, height: 0.6, edge: 0xb070ff }));
    const ring = new THREE.Mesh(torusGeo, torusMat);
    ring.scale.set(0.65, 1.25, 1);
    portalGroup.add(ring);

    portalPivot = new THREE.Group();
    portalPivot.add(portalGroup);
    portalPivot.position.set(0, PORTAL_BASE + PORTAL_H / 2, PORTAL_Z);
    portalPivot.rotation.y = PORTAL_YAW;
    portalPivot.visible = false;
    world.add(portalPivot);

    const c = document.createElement('canvas'); c.width = c.height = 128;
    const g = c.getContext('2d'), grd = g.createRadialGradient(64, 64, 4, 64, 64, 62);
    grd.addColorStop(0, 'rgba(190,140,255,0.95)'); grd.addColorStop(0.4, 'rgba(120,80,255,0.45)'); grd.addColorStop(1, 'rgba(60,20,160,0)');
    g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
    portalGlow = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending }));
    portalGlow.scale.set(0.55, 0.85, 1);
    portalPivot.add(portalGlow);
    portalLight.position.set(0.15, PORTAL_BASE + PORTAL_H / 2, PORTAL_Z + 0.1);
    portalDissolve.forEach((u) => { u.uDissolve.value = 1; });
  } catch (err) { post({ ev: 'warn', msg: 'процедурный портал: ' + err }); }

  if (PORTAL_URL) {
    loader.loadAsync(PORTAL_URL).then((po) => {
      const box = new THREE.Box3().setFromObject(po.scene), size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
      po.scene.position.sub(center);
      po.scene.traverse((o) => {
        if (!o.isMesh) return;
        o.frustumCulled = false; o.geometry.computeBoundingBox();
        const bb = o.geometry.boundingBox;
        (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => { m.envMapIntensity = 1.1; portalDissolve.push(makeDissolvable(m, { minY: bb.min.y, height: Math.max(1e-3, bb.max.y - bb.min.y), edge: 0xb070ff })); });
      });
      if (portalPivot) {
        portalPivot.clear();
        portalPivot.add(po.scene);
        if (size.y > 0.001) portalPivot.scale.setScalar(PORTAL_H / size.y);
        portalPivot.add(portalGlow);
        portalDissolve.forEach((u) => { u.uDissolve.value = 1; });
      }
    }).catch((err) => { post({ ev: 'warn', msg: 'фоновый портал: ' + err }); });
  }

  setDissolve(charDissolve, 1); charPivot.visible = false; hidden = true;
  play('idle', { fade: 0 });
  if (Q.has('autoshow') || DEBUG) { charPivot.visible = true; hidden = false; setDissolve(charDissolve, 0); }
  fit(); postLayout(); computeRects(true);
  post({ ev: 'ready', clips: source, portal: !!portalPivot, renderer: renderer.capabilities.isWebGL2 ? 'webgl2' : 'webgl1' });
  window.__petReady = true;
  requestAnimationFrame(loop);
}

// ------------------------------------------------------------------ главный цикл
let last = performance.now(), rectTimer = 0, acc = 0;
function loop(now) {
  requestAnimationFrame(loop);
  const minDt = 1000 / FPS - 1.5;
  if (now - last < minDt) return;
  const dt = Math.min(MAX_DT, (now - last) / 1000); last = now;
  if (document.hidden) return;
  if (!frozen) {
    updateTweens(dt);
    if (mixer) mixer.update(dt);
    curYaw = lerpAngle(curYaw, target.yaw, 1 - Math.exp(-dt * 9));
    charPivot.rotation.y = curYaw;
    updateRubik(dt); updateParticles(dt);
    idleBrain(now);
    if (portalPivot && portalPivot.visible) {
      const t = now / 1000;
      portalPivot.position.y = PORTAL_BASE + PORTAL_H / 2 + Math.sin(t * 1.6) * 0.012;
      portalGlow.scale.set(0.55 + Math.sin(t * 3.1) * 0.03, 0.85 + Math.sin(t * 2.3) * 0.04, 1);
    }
    rectTimer += dt;
    if (rectTimer > 0.1) { rectTimer = 0; computeRects(); }
  }
  if (layoutDirty) { postLayout(); computeRects(true); }
  renderer.render(scene, camera);
}

// отладка / тесты
if (DEBUG) {
  window.__pet = {
    THREE, bones, clips, REST, play, charPivot, camera, scene, renderer, props, target, particles,
    freeze(v = true) { frozen = v; },
    pose(name, t, yawDeg) {                      // выставить клип на момент t (для скриншотов)
      frozen = true; mixer.stopAllAction(); current = null; play(name, { fade: 0 }); current.paused = false; current.time = t; mixer.update(0);
      if (yawDeg !== undefined) { target.yaw = curYaw = yawDeg * D2R; charPivot.rotation.y = curYaw; }
      for (const [n, o] of Object.entries(props)) { o.visible = clipMeta[name].props.includes(n); o.scale.setScalar(1); }
      charRoot.updateMatrixWorld(true);
      renderer.render(scene, camera);
    },
    boneWorld(n) { return bones[n].getWorldPosition(new THREE.Vector3()).toArray(); },
    render() { renderer.render(scene, camera); },
    setDissolve(v) { setDissolve(charDissolve, v); },
    openPortalNow() { portalPivot.visible = true; seqOpen = true; setDissolve(portalDissolve, 0); portalGlow.material.opacity = 0.6; portalLight.intensity = 2.2; },
    showItem(kind, boneName) { spawnItem(kind, boneName); heldItem.obj.scale.setScalar(1.45 / heldItem.ws); },
    spawnItem, tween, dissolveOut, dissolveIn, portalSequence,
    mode: () => ({ currentName, hidden, seqOpen, asleep, dragging,
                   quietMs: Math.round(performance.now() - lastHostCmd),
                   rect: charRectHit() }),
  };
}

init().catch((err) => post({ ev: 'error', msg: 'init: ' + String(err && err.stack || err) }));
