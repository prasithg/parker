/*
 * Parker's Reachy Mini — CAD shell and state-driven articulation.
 *
 * Downstream of ParkerExpression (expression.js): this module only READS
 * the semantic state and draws it. Every motion here derives from a real
 * controller phase, overlay, or energy — no timer-invented busy-ness.
 * Idle life (breathing and gaze) runs only in awake states;
 * offline/stopped/error are visibly inert.
 *
 * Published Pollen Robotics CAD shells are assembled by reachy-model.js,
 * with circular optical lenses, articulated rods, and wire antennae.
 * Assets ship locally with their source/license metadata. Existing semantic
 * poses, speech-reactive movement, cancellation, and reduced motion remain
 * downstream of the controller; no hardware or provider access lives here.
 *
 * Degradation contract (the page owns the fallback):
 * - createReachyScene returns null when WebGL is unavailable;
 * - reducedMotion renders one truthful static pose per state change;
 * - hidden page pauses the loop; dispose() releases every GL resource.
 */

import * as THREE from '../vendor/three/three.module.min.js';
import { buildReachyModel, HEAD_HEIGHT } from './reachy-model.js';

// ---------------------------------------------------------------------------
// Deterministic PRNG for idle life (blinks, saccades) so a pose sequence is
// reproducible when seeded in tests.
// ---------------------------------------------------------------------------

function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------------------
// Palette — premium matte white body, dark glass face, cool signal glow.
// ---------------------------------------------------------------------------

const EYE_GLOW = 0x8fd2ff;   // calm sky blue
const AMBER = 0xffd166;      // action staged / waiting on the screen
const CONCERN = 0xffb38a;    // guard / error warmth (never alarm-red)
const SUCCESS = 0xa5f0bb;    // action executed

// ---------------------------------------------------------------------------
// Pose targets per semantic state — one readable table so a reviewer can
// check "motion tells the truth" against expression.js.
//
// poseFor MUTATES the shared POSE object (no per-frame allocation).
// life flags gate the idle-life systems: only awake states blink/saccade/
// breathe; asleep states are inert.
// ---------------------------------------------------------------------------

const POSE = {
  headYaw: 0, headPitch: 0, headRoll: 0, bodyYaw: 0, headDrop: 0,
  eyeOpen: 0.8, eyeGlow: 0.5, eyeColor: EYE_GLOW,
  antennaL: -0.16, antennaR: 0.16,   // resting splay (z-rotation)
  breathRate: 0.26, breathAmp: 0.014,
  voice: 0,
  // Scene-level light: 1 = the room lit for company; asleep states dim
  // the whole room, not just the eyes, so powered-on-resting can never be
  // mistaken for engaged listening at a glance (Pras, session 3).
  sceneLight: 1,
  blinks: true, saccades: false, gazeCamera: false,
};

function resetPose() {
  POSE.headDrop = 0;
  POSE.headYaw = 0; POSE.headPitch = 0; POSE.headRoll = 0; POSE.bodyYaw = 0;
  POSE.eyeOpen = 0.8; POSE.eyeGlow = 0.5; POSE.eyeColor = EYE_GLOW;
  POSE.antennaL = -0.16; POSE.antennaR = 0.16;
  POSE.breathRate = 0.26; POSE.breathAmp = 0.014;
  POSE.voice = 0;
  POSE.sceneLight = 1;
  POSE.blinks = true; POSE.saccades = false; POSE.gazeCamera = false;
}

function poseFor(state) {
  const user = state.userEnergy;
  const parker = state.parkerEnergy;
  resetPose();
  switch (state.phase) {
    case 'offline':   // asleep: head settled onto the body, eyes shut,
      // antennae hanging like floppy ears, inert.
      POSE.headPitch = 0.3; POSE.headDrop = 1; POSE.eyeOpen = 0.04; POSE.eyeGlow = 0.04;
      POSE.antennaL = -1.0; POSE.antennaR = 1.0;
      POSE.breathAmp = 0; POSE.blinks = false;
      POSE.sceneLight = 0.48;
      break;
    case 'dormant':   // powered but resting: same sunken sleep — with the
      // faintest eye ember as the honest "wake listening is armed" cue,
      // and the slowest whisper of breath. Lifeless until "Hey Parker."
      POSE.headPitch = 0.3; POSE.headDrop = 1; POSE.eyeOpen = 0.06; POSE.eyeGlow = 0.14;
      POSE.antennaL = -1.0; POSE.antennaR = 1.0;
      POSE.breathRate = 0.1; POSE.breathAmp = 0.005; POSE.blinks = false;
      POSE.sceneLight = 0.56; // readable hardware at rest; brighter on actual wake
      break;
    case 'idle':      // present, softly alive, gaze wandering the room
      POSE.headPitch = 0.08; POSE.eyeOpen = 0.66; POSE.eyeGlow = 0.35;
      POSE.saccades = true;
      break;
    case 'connecting': // the POP: head springs up, eyes snap open bright,
      // antennae perk to attention — "I heard you."
      POSE.headPitch = -0.1; POSE.eyeOpen = 1.0; POSE.eyeGlow = 0.95;
      POSE.antennaL = -0.02; POSE.antennaR = 0.02;
      POSE.breathRate = 0.6; POSE.breathAmp = 0.02; POSE.gazeCamera = true;
      break;
    case 'listening': // open, attentive, eyes on him, tiny gaze life
      POSE.headPitch = -0.05; POSE.eyeOpen = 0.92; POSE.eyeGlow = 0.7;
      POSE.antennaL = -0.05; POSE.antennaR = 0.05;
      POSE.breathRate = 0.3; POSE.breathAmp = 0.015 + user * 0.01;
      // Waiting reads as alive: a slow weight shift (~8 s), never a fidget.
      POSE.bodyYaw = Math.sin(state.sincePhaseMs / 1300) * 0.03;
      POSE.saccades = true; POSE.gazeCamera = true;
      break;
    case 'hearing':   // leaning in — head sway rides HIS real energy
      POSE.headPitch = -0.12; POSE.headRoll = 0.06 + user * 0.06;
      POSE.headYaw = Math.sin(state.sincePhaseMs / 900) * 0.045 * (0.4 + user);
      POSE.eyeOpen = 1.0; POSE.eyeGlow = 0.85;
      POSE.antennaL = -0.03 - user * 0.14; POSE.antennaR = 0.03 + user * 0.14;
      POSE.breathRate = 0.36; POSE.breathAmp = 0.014 + user * 0.014;
      POSE.gazeCamera = true;
      break;
    case 'thinking':  // classic look-up-and-away, one antenna curled in
      POSE.headYaw = 0.34; POSE.headPitch = -0.18; POSE.headRoll = 0.15;
      POSE.eyeOpen = 0.58; POSE.eyeGlow = 0.55;
      POSE.antennaL = 0.04; POSE.antennaR = -0.1;
      POSE.breathRate = 0.44; POSE.saccades = true;
      break;
    case 'talking':   // facing him, mouth/eyes riding Parker's REAL voice
      POSE.headPitch = -0.03 + parker * 0.05;
      POSE.headYaw = Math.sin(state.sincePhaseMs / 750) * 0.03 * (0.3 + parker);
      POSE.eyeOpen = 0.9; POSE.eyeGlow = 0.7 + parker * 0.3;
      POSE.antennaL = -0.06 - parker * 0.22; POSE.antennaR = 0.06 + parker * 0.22;
      POSE.breathRate = 0.4; POSE.voice = parker; POSE.gazeCamera = true;
      break;
    case 'interrupted': // visible yield: small bow, softened eyes
      POSE.headPitch = 0.16; POSE.headYaw = -0.08;
      POSE.eyeOpen = 0.4; POSE.eyeGlow = 0.42;
      POSE.antennaL = -0.42; POSE.antennaR = 0.42;
      break;
    case 'closing':   // winding down gently
      POSE.headPitch = 0.22; POSE.headDrop = 0.45; POSE.eyeOpen = 0.42; POSE.eyeGlow = 0.3;
      POSE.antennaL = -0.7; POSE.antennaR = 0.7;
      POSE.breathRate = 0.2;
      break;
    case 'stopped':   // properly asleep — inert, honest rest
      POSE.headPitch = 0.3; POSE.headDrop = 0.9; POSE.eyeOpen = 0.08; POSE.eyeGlow = 0.08;
      POSE.antennaL = -0.95; POSE.antennaR = 0.95;
      POSE.breathAmp = 0; POSE.blinks = false;
      break;
    case 'error':     // static concern — no motion pretending recovery
      POSE.headPitch = 0.24; POSE.headRoll = -0.1;
      POSE.eyeOpen = 0.45; POSE.eyeGlow = 0.35; POSE.eyeColor = CONCERN;
      POSE.antennaL = -0.85; POSE.antennaR = 0.85;
      POSE.breathAmp = 0; POSE.blinks = false;
      break;
    default: break;
  }

  // Overlays are secondary cues layered on the phase, never a second
  // character.
  if (state.guard === 'redirect') {
    // Gentle apology: chin down and away, warm eyes.
    POSE.headPitch += 0.2; POSE.headRoll = -0.16;
    POSE.eyeColor = CONCERN;
    POSE.eyeGlow = Math.max(POSE.eyeGlow, 0.55);
  } else if (state.guard === 'repair') {
    // Curious asking tilt, eyes wide.
    POSE.headRoll += 0.18;
    POSE.eyeOpen = Math.max(POSE.eyeOpen, 0.9);
  }

  if (state.attention === 'choice') {
    // Choices wait on the screen below: attentive, slightly toward it.
    POSE.headPitch += 0.1;
    POSE.eyeOpen = Math.max(POSE.eyeOpen, 0.85);
  }

  switch (state.action) {
    case 'staged':
      // A deliberate glance down at the confirmation card — amber eyes.
      POSE.headPitch += 0.18; POSE.eyeColor = AMBER;
      POSE.eyeGlow = Math.max(POSE.eyeGlow, 0.7);
      break;
    case 'executed':
      // Quiet delight from the REAL action_result frame: head up, bright
      // green eyes, antennae perked high. Truthful — this pose has no
      // other entry path.
      POSE.headPitch -= 0.1;
      POSE.eyeColor = SUCCESS; POSE.eyeGlow = Math.max(POSE.eyeGlow, 0.9);
      POSE.eyeOpen = Math.max(POSE.eyeOpen, 0.95);
      POSE.antennaL = -0.02; POSE.antennaR = 0.02;
      break;
    case 'failed':
      // Honest apologetic droop — warm concern, never panic.
      POSE.headPitch += 0.22; POSE.headRoll = -0.12;
      POSE.eyeColor = CONCERN; POSE.eyeOpen = Math.min(POSE.eyeOpen, 0.5);
      POSE.antennaL = -0.5; POSE.antennaR = 0.5;
      break;
    default: break;
  }
  return POSE;
}

// ---------------------------------------------------------------------------
// Procedural helpers — construction-time only.
// ---------------------------------------------------------------------------

// Soft radial ground pool (shadow + faint cool glow) via CanvasTexture.
function makeGroundTexture() {
  if (typeof document === 'undefined') return null;
  const size = 256;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  const grad = ctx.createRadialGradient(size / 2, size / 2, 8, size / 2, size / 2, size / 2);
  grad.addColorStop(0.0, 'rgba(0,0,0,0.62)');
  grad.addColorStop(0.55, 'rgba(0,0,0,0.3)');
  grad.addColorStop(1.0, 'rgba(0,0,0,0)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// Hand-built emissive room -> PMREM environment, so the white shell and the
// dark glass face pick up believable living-room reflections. No HDRI file.
function buildEnvironment(renderer) {
  const envScene = new THREE.Scene();
  envScene.background = new THREE.Color(0x222b35);
  const geoms = [];
  const mats = [];
  function panel(color, intensity, w, h, x, y, z) {
    const geom = new THREE.PlaneGeometry(w, h);
    const mat = new THREE.MeshBasicMaterial({ color: color });
    mat.color.multiplyScalar(intensity);
    const mesh = new THREE.Mesh(geom, mat);
    mesh.position.set(x, y, z);
    mesh.lookAt(0, 1, 0);
    envScene.add(mesh);
    geoms.push(geom); mats.push(mat);
  }
  // Warm key window (lamp-side), cool TV spill, dim ceiling bounce.
  panel(0xfff5e7, 4.0, 3, 5, -3, 3, 4);
  panel(0xb3c9e5, 2.0, 2, 4, 4, 2, 3);
  panel(0x36415a, 1.2, 8, 8, 0, 6, 0);
  panel(0x1a1410, 0.8, 8, 8, 0, -3, 0);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const target = pmrem.fromScene(envScene, 0.08);
  pmrem.dispose();
  for (let i = 0; i < geoms.length; i++) geoms[i].dispose();
  for (let i = 0; i < mats.length; i++) mats[i].dispose();
  return target;
}

// ---------------------------------------------------------------------------
// Scene lifecycle
// ---------------------------------------------------------------------------

const SPRING_KEYS = ['headYaw', 'headPitch', 'headRoll', 'headDrop', 'eyeOpen', 'eyeGlow', 'antennaL', 'antennaR', 'voice', 'gazeX', 'gazeY', 'sceneLight'];
const SPRING_OMEGA = {
  headYaw: 5.5, headPitch: 5.5, headRoll: 5.5, headDrop: 7,
  eyeOpen: 12, eyeGlow: 8, antennaL: 8, antennaR: 8,
  voice: 20, gazeX: 22, gazeY: 22, // saccades snap, they don't drift
  sceneLight: 10, // visible wake response in the first few frames, not a 1.4 s fade
};
// Anticipation: a brief counter-impulse before the head commits to a new
// pose (the classic animation beat). Applied to head springs on phase change.
const ANTICIPATION_KEYS = ['headYaw', 'headPitch', 'headRoll'];
const ANTICIPATION_GAIN = 3.2;

// ---------------------------------------------------------------------------
// Beats: short, bounded, self-decaying offsets layered ABOVE the pose
// springs and below the antenna physics (docs/plans/2026-09-02-reachy-
// motion-vocabulary.md, after the official Reachy Mini motion reference).
// The spring owns each degree of freedom's target; a beat adds an offset
// that returns to zero on its own, so nothing ever snaps and a phase
// change into rest simply clears the list. Each beat is a list of
// [key, amplitude, startMs, durationMs] channels shaped by a curve.
// ---------------------------------------------------------------------------

const BEAT_KEYS = ['headPitch', 'headYaw', 'headRoll', 'headDrop', 'antennaL', 'antennaR'];

// dip-then-rise: anticipation below zero, overshoot above, settle. u in [0,1].
function curveAnticipate(u) {
  if (u < 0.22) return -Math.sin((u / 0.22) * Math.PI) * 0.55;      // the compress
  const v = (u - 0.22) / 0.78;
  return Math.sin(v * Math.PI) * Math.exp(-2.2 * v);                 // overshoot, settle
}
// one soft bump (a nod, an antenna punctuation)
function curveBump(u) { return Math.sin(u * Math.PI); }
// slow ease down and hold, then release (a restrained "oh")
function curveDwell(u) { return u < 0.3 ? Math.sin((u / 0.3) * Math.PI / 2) : Math.cos(((u - 0.3) / 0.7) * Math.PI / 2); }

const BEATS = {
  // The staged wake: compress, rise on the spring, antennae perk ~150 ms
  // later with one overshoot, settle. Total under a second.
  // Signs: headDrop + = lower, headPitch + = chin down, antennaL + = perk
  // (toward 0 from the drooped -1). The anticipation curve goes negative
  // first, so the amplitude sign is chosen for the SETTLE direction:
  // head ends up higher/chin up, antennae end up perked.
  wake: [
    ['headDrop', -0.10, 0, 260, curveAnticipate],
    ['headPitch', -0.06, 0, 300, curveAnticipate],
    ['antennaL', 0.30, 150, 700, curveAnticipate],
    ['antennaR', -0.30, 150, 700, curveAnticipate],
  ],
  // "I heard you": one small nod and an antenna dip.
  acknowledge: [
    ['headPitch', 0.06, 0, 260, curveBump],
    ['antennaL', 0.10, 40, 320, curveBump],
    ['antennaR', -0.10, 40, 320, curveBump],
  ],
  // A sentence ended: a micro-nod and a tiny alternating antenna tick.
  phrase: [
    ['headPitch', 0.035, 0, 200, curveBump],
    ['antennaL', 0.06, 30, 220, curveBump],
  ],
  phraseAlt: [
    ['headPitch', 0.035, 0, 200, curveBump],
    ['antennaR', -0.06, 30, 220, curveBump],
  ],
  // The real result landed: a small head-up bounce (the antenna hop is physics).
  executed: [
    ['headPitch', -0.08, 0, 420, curveBump],
  ],
  // Restrained, distinct, never theatrical.
  failed: [
    ['headPitch', 0.09, 0, 900, curveDwell],
    ['headRoll', -0.05, 0, 900, curveDwell],
  ],
  cancelled: [
    ['antennaL', 0.12, 0, 500, curveDwell],
    ['antennaR', -0.12, 0, 500, curveDwell],
  ],
};

export function createReachyScene(container, controller, options) {
  const opts = options || {};
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
    if (!renderer.getContext()) throw new Error('no WebGL context');
  } catch (err) {
    return null; // the page keeps the orb and the full text experience
  }

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.18;
  const canvas = renderer.domElement;
  canvas.setAttribute('aria-hidden', 'true'); // presentation only — status
  // text and cards carry every essential meaning.
  container.appendChild(canvas);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 20);
  camera.position.set(0.32, 1.8, 6.1);
  camera.lookAt(0, 1.38, 0);

  // Dark-living-room lighting: dim cool ambience, one warm lamp key, one
  // cool rim so the white shell separates from the dark at 3 meters.
  const hemi = new THREE.HemisphereLight(0xd8e2f0, 0x20232a, 1.4);
  scene.add(hemi);
  const key = new THREE.DirectionalLight(0xfff6e9, 3.2);
  key.position.set(-2.5, 4.5, 4);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xc0d8f5, 2.1);
  rim.position.set(3, 3, -2);
  scene.add(rim);
  const LIGHTS = [[hemi, 1.4], [key, 3.2], [rim, 2.1]];
  function applyLights(level) {
    const l = Math.max(0.15, Math.min(1, level));
    for (let i = 0; i < LIGHTS.length; i++) LIGHTS[i][0].intensity = LIGHTS[i][1] * l;
  }

  const envTarget = buildEnvironment(renderer);
  scene.environment = envTarget.texture;

  const groundTexture = makeGroundTexture();
  const parts = buildReachyModel(groundTexture);
  scene.add(parts.robot);

  // ---- Spring-tracked degrees of freedom (no per-frame allocation) ----
  const dof = {};
  const vel = {};
  for (let i = 0; i < SPRING_KEYS.length; i++) { dof[SPRING_KEYS[i]] = 0; vel[SPRING_KEYS[i]] = 0; }
  dof.eyeOpen = 0.66; dof.eyeGlow = 0.35;

  function springStep(k, target, dt) {
    const omega = SPRING_OMEGA[k];
    const x = dof[k] - target;
    vel[k] += (-2 * omega * vel[k] - omega * omega * x) * dt;
    dof[k] += vel[k] * dt;
  }

  const eyeColor = new THREE.Color(EYE_GLOW);
  const targetColor = new THREE.Color(EYE_GLOW);
  const rand = mulberry32(opts.seed || 20260901);

  // Idle-life state.
  let breathPhase = 0;
  let blink = 1;                 // 1 open .. 0 closed (multiplies eyeOpen)
  let blinkAt = 0;               // when the current/next blink starts
  let blinkDouble = false;
  let saccade = { x: 0, y: 0, nextAt: 0 };
  let voiceEnv = 0;              // attack/decay envelope over parkerEnergy
  let workPulse = 0;
  let lastPhase = null;
  let lastAction = 'none';
  // Antenna secondary physics: swing lags behind measured head motion.
  const antPhys = { zL: 0, zR: 0, x: 0, vzL: 0, vzR: 0, vx: 0 };
  let prevHeadYaw = 0;
  let prevHeadPitch = 0;

  function resize() {
    const w = container.clientWidth || 320;
    const h = container.clientHeight || 240;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // Frame the complete robot, including antennae and base, on phones too.
    camera.position.z = Math.max(6.1, 4.5 / Math.max(camera.aspect, 0.3));
    camera.lookAt(0, 1.38, 0);
    camera.updateProjectionMatrix();
    // While the loop is paused (hidden tab, reduced motion) a resize must
    // still leave a correctly framed frame, not a stretched one.
    renderer.render(scene, camera);
  }
  resize();
  let observer = null;
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(resize);
    observer.observe(container);
  }

  // ---- Apply the DOF state to the scene graph ----
  function applyToScene(pose) {
    applyLights(dof.sceneLight);
    // Head: springs + beat offsets + talking micro-nod on the REAL voice envelope.
    const nod = -voiceEnv * 0.05;
    parts.head.rotation.set(
      dof.headPitch + beatOffset.headPitch + nod,
      dof.headYaw + beatOffset.headYaw,
      dof.headRoll + beatOffset.headRoll
    );
    parts.robot.rotation.y = dof.headYaw * 0.22 + pose.bodyYaw;

    // Volume-preserving breath: chest swells out, height compensates.
    const breathe = 1 + Math.sin(breathPhase) * pose.breathAmp;
    parts.torso.scale.set(breathe, 1 / Math.max(breathe, 0.0001), breathe);
    parts.head.position.y = HEAD_HEIGHT - (dof.headDrop + beatOffset.headDrop) * 0.16
      + Math.sin(breathPhase) * pose.breathAmp * 0.6;

    // Eyes: openness x blink, glow, gaze saccade offsets.
    const glowOpacity = dof.eyeGlow * 0.22;
    parts.eyeL.iris.material.opacity = glowOpacity;
    parts.eyeR.iris.material.opacity = glowOpacity;
    parts.eyeL.halo.material.opacity = dof.eyeGlow * 0.12;
    parts.eyeR.halo.material.opacity = dof.eyeGlow * 0.12;
    parts.eyeL.iris.material.color.copy(eyeColor);
    parts.eyeR.iris.material.color.copy(eyeColor);
    parts.eyeL.halo.material.color.copy(eyeColor);
    parts.eyeR.halo.material.color.copy(eyeColor);
    parts.eyeL.gaze.position.set(dof.gazeX * 0.12, dof.gazeY * 0.12, 0);
    parts.eyeR.gaze.position.set(dof.gazeX * 0.12, dof.gazeY * 0.12, 0);
    parts.updateNeck();

    // Antennae: pose splay on the arm, physics swing on the pivot.
    // Sign flip: the pose table is authored as negative-L / positive-R
    // = OUTWARD splay; the pivots' z-axis runs the other way.
    parts.antL.arm.rotation.z = -(dof.antennaL + beatOffset.antennaL);
    parts.antR.arm.rotation.z = -(dof.antennaR + beatOffset.antennaR);
    parts.antL.pivot.rotation.z = antPhys.zL;
    parts.antR.pivot.rotation.z = antPhys.zR;
    parts.antL.pivot.rotation.x = antPhys.x;
    parts.antR.pivot.rotation.x = antPhys.x;

    // Voice light: attack/decay envelope, wide and bright enough for 3 m.
    parts.voice.material.opacity = Math.min(0.85, voiceEnv * 1.4);
    parts.voice.scale.set(1 + voiceEnv * 1.1, 1 + voiceEnv * 0.35, 1);
    parts.voice.material.color.copy(eyeColor);
  }

  let disposed = false;
  let paused = false;
  let last = null;

  function frame(timeMs) {
    if (disposed) return;
    const dt = Math.max(0, Math.min(0.05, last === null ? 0.016 : (timeMs - last) / 1000));
    last = timeMs;
    controller.tick();
    const state = controller.getState();
    const pose = poseFor(state);

    triggerBeats(state, timeMs);
    stepBeats(timeMs);

    // ---- Anticipation beat on phase/action changes ----
    if (lastPhase !== null && state.phase !== lastPhase) {
      for (let i = 0; i < ANTICIPATION_KEYS.length; i++) {
        const k = ANTICIPATION_KEYS[i];
        vel[k] -= (pose[k] - dof[k]) * ANTICIPATION_GAIN;
      }
    }
    if (state.action !== lastAction && state.action === 'executed') {
      // A tiny happy hop of the antennae when the real result lands.
      antPhys.vzL -= 2.2; antPhys.vzR += 2.2;
    }
    lastPhase = state.phase;
    lastAction = state.action;

    // ---- Blink system (awake states only; never during a closed-eye pose) ----
    if (pose.blinks && dof.eyeOpen > 0.25) {
      if (timeMs >= blinkAt) {
        if (blinkAt === 0) {
          blinkAt = timeMs + 1600 + rand() * 3200; // first schedule
        } else {
          // A blink is running: triangle envelope over ~150 ms.
          const e = (timeMs - blinkAt) / 150;
          if (e >= 1) {
            blink = 1;
            if (blinkDouble) {
              blinkDouble = false;
              blinkAt = timeMs + 140;
            } else {
              blinkDouble = rand() < 0.14;
              blinkAt = timeMs + 2200 + rand() * 3600;
            }
          } else {
            blink = e < 0.4 ? 1 - e / 0.4 : (e - 0.4) / 0.6;
          }
        }
      }
    } else {
      blink = 1;
      blinkAt = 0;
      blinkDouble = false;
    }

    // ---- Saccade system: quick bounded gaze jumps, then hold ----
    let gx = 0, gy = 0;
    if (pose.saccades) {
      if (timeMs >= saccade.nextAt) {
        saccade.nextAt = timeMs + 1400 + rand() * 2600;
        saccade.x = (rand() - 0.5) * 0.045;
        saccade.y = (rand() - 0.5) * 0.028;
        if (state.phase === 'thinking') {
          // Thinking scans upward — the classic recall gaze.
          saccade.y = 0.02 + rand() * 0.02;
          saccade.x = (rand() < 0.5 ? -1 : 1) * (0.02 + rand() * 0.025);
        }
      }
      gx = saccade.x; gy = saccade.y;
    } else if (pose.gazeCamera) {
      gx = 0; gy = 0; // locked on him
    }

    // ---- Voice envelope: fast attack, musical decay, from REAL energy ----
    const target = pose.voice;
    if (target > voiceEnv) voiceEnv += (target - voiceEnv) * Math.min(1, dt * 26);
    else voiceEnv += (target - voiceEnv) * Math.min(1, dt * 7);

    breathPhase += dt * Math.PI * 2 * pose.breathRate;

    // ---- Springs toward pose targets ----
    springStep('headYaw', pose.headYaw, dt);
    springStep('headPitch', pose.headPitch, dt);
    springStep('headRoll', pose.headRoll, dt);
    springStep('headDrop', pose.headDrop, dt); // never stepped before: the live loop never sank the head
    springStep('eyeOpen', pose.eyeOpen, dt);
    springStep('eyeGlow', pose.eyeGlow, dt);
    springStep('antennaL', pose.antennaL, dt);
    springStep('antennaR', pose.antennaR, dt);
    springStep('voice', pose.voice, dt);
    springStep('sceneLight', pose.sceneLight, dt);
    springStep('gazeX', gx, dt);
    springStep('gazeY', gy, dt);

    // ---- Antenna secondary physics: lag behind measured head motion ----
    const headYawVel = (dof.headYaw - prevHeadYaw) / Math.max(dt, 0.001);
    const headPitchVel = (dof.headPitch - prevHeadPitch) / Math.max(dt, 0.001);
    prevHeadYaw = dof.headYaw;
    prevHeadPitch = dof.headPitch;
    const K = 70, C = 7;
    antPhys.vzL += (-K * antPhys.zL - C * antPhys.vzL - headYawVel * 1.4) * dt;
    antPhys.vzR += (-K * antPhys.zR - C * antPhys.vzR - headYawVel * 1.4) * dt;
    antPhys.vx += (-K * antPhys.x - C * antPhys.vx + headPitchVel * 1.2) * dt;
    antPhys.zL += antPhys.vzL * dt;
    antPhys.zR += antPhys.vzR * dt;
    antPhys.x += antPhys.vx * dt;

    // ---- Work overlay: right antenna tip pulses ONLY while real work ----
    if (state.work.length > 0) {
      workPulse += dt * 3.2;
      parts.antR.tip.material.emissiveIntensity = 0.7 + Math.sin(workPulse) * 0.5;
      parts.antR.tip.scale.setScalar(1 + Math.max(0, Math.sin(workPulse)) * 0.25);
    } else {
      workPulse = 0;
      parts.antR.tip.scale.setScalar(1);
      parts.antR.tip.material.emissiveIntensity = Math.max(
        0, parts.antR.tip.material.emissiveIntensity - dt * 2
      );
    }

    targetColor.setHex(pose.eyeColor);
    eyeColor.lerp(targetColor, Math.min(1, dt * 5));

    applyToScene(pose);
    renderer.render(scene, camera);
  }

  // ---- Beats (see BEATS above) ----
  const activeBeats = []; // {channels, t0}
  const beatOffset = {};
  for (let i = 0; i < BEAT_KEYS.length; i++) beatOffset[BEAT_KEYS[i]] = 0;
  let phraseTick = 0;
  let lastBeats = 0;
  let lastKnownPhase = null;
  let lastKnownAction = null;

  function startBeat(name, nowMs) {
    if (opts.reducedMotion) return;
    // Replace, never stack: a new beat of the same name restarts it.
    for (let i = activeBeats.length - 1; i >= 0; i--) {
      if (activeBeats[i].name === name) activeBeats.splice(i, 1);
    }
    activeBeats.push({ name: name, channels: BEATS[name], t0: nowMs });
  }

  function clearBeats() { activeBeats.length = 0; }

  function stepBeats(nowMs) {
    for (let i = 0; i < BEAT_KEYS.length; i++) beatOffset[BEAT_KEYS[i]] = 0;
    for (let b = activeBeats.length - 1; b >= 0; b--) {
      const beat = activeBeats[b];
      let alive = false;
      for (let c = 0; c < beat.channels.length; c++) {
        const ch = beat.channels[c];
        const local = nowMs - beat.t0 - ch[2];
        if (local < 0) { alive = true; continue; }
        const u = local / ch[3];
        if (u >= 1) continue;
        alive = true;
        beatOffset[ch[0]] += ch[1] * ch[4](u);
      }
      if (!alive) activeBeats.splice(b, 1);
    }
  }

  const ASLEEP = { offline: true, dormant: true, stopped: true, error: true, closing: true };

  function triggerBeats(state, nowMs) {
    if (lastKnownPhase !== null && state.phase !== lastKnownPhase) {
      if (ASLEEP[state.phase]) clearBeats();
      else if (state.phase === 'connecting' && ASLEEP[lastKnownPhase]) startBeat('wake', nowMs);
      else if (state.phase === 'listening' && lastKnownPhase === 'hearing') startBeat('acknowledge', nowMs);
    }
    if (state.action !== lastKnownAction) {
      if (state.action === 'executed') startBeat('executed', nowMs);
      else if (state.action === 'failed') startBeat('failed', nowMs);
      else if (lastKnownAction === 'staged' && state.action === 'none' && !ASLEEP[state.phase]) {
        startBeat('cancelled', nowMs); // the offer lapsed: his "no", or the window expired
      }
    }
    if (state.beats !== lastBeats) {
      if (state.phase === 'talking' && state.beats > lastBeats) {
        phraseTick += 1;
        startBeat(phraseTick % 2 ? 'phrase' : 'phraseAlt', nowMs);
      }
      lastBeats = state.beats;
    }
    lastKnownPhase = state.phase;
    lastKnownAction = state.action;
  }

  let unsubscribe = null;
  let reducedTimer = null;

  function renderStatic() {
    if (disposed) return;
    const state = controller.getState();
    // Meaning stays, motion goes: real energies still shape the pose and
    // voice light, but nothing oscillates.
    const pose = poseFor(state);
    for (let i = 0; i < SPRING_KEYS.length; i++) {
      const k = SPRING_KEYS[i];
      dof[k] = k in pose ? pose[k] : 0;
      vel[k] = 0;
    }
    dof.gazeX = 0; dof.gazeY = 0;
    blink = 1;
    voiceEnv = pose.voice;
    breathPhase = 0;
    antPhys.zL = antPhys.zR = antPhys.x = 0;
    antPhys.vzL = antPhys.vzR = antPhys.vx = 0;
    clearBeats();
    for (let i = 0; i < BEAT_KEYS.length; i++) beatOffset[BEAT_KEYS[i]] = 0;
    lastKnownPhase = state.phase; lastKnownAction = state.action; lastBeats = state.beats;
    eyeColor.setHex(pose.eyeColor);
    parts.antR.tip.material.emissiveIntensity = state.work.length > 0 ? 0.9 : 0;
    parts.antR.tip.scale.setScalar(state.work.length > 0 ? 1.15 : 1);
    applyToScene(pose);
    renderer.render(scene, camera);
  }

  function onVisibility() {
    if (document.hidden) {
      renderer.setAnimationLoop(null);
    } else if (!paused && !opts.reducedMotion) {
      last = null;
      renderer.setAnimationLoop(frame);
    }
  }

  if (opts.reducedMotion) {
    // One truthful static pose per semantic change; a slow interval lets
    // TTL/dwell housekeeping settle without a render loop.
    unsubscribe = controller.subscribe(function (s, cause) {
      if (cause !== 'phrase_boundary') renderStatic(); // a beat is not a pose change
    });
    renderStatic();
    reducedTimer = setInterval(function () {
      controller.tick();
    }, 400);
  } else {
    if (!document.hidden) renderer.setAnimationLoop(frame);
    document.addEventListener('visibilitychange', onVisibility);
  }

  return {
    canvas: canvas,
    // Objective pose readout for verification and tests — what the scene
    // is ACTUALLY showing, not what a screenshot suggests.
    debug: function () {
      return {
        dof: Object.assign({}, dof),
        eyeColor: '#' + eyeColor.getHexString(),
        workGlow: parts.antR.tip.material.emissiveIntensity,
        sceneLight: dof.sceneLight,
        lightIntensity: { hemi: hemi.intensity, key: key.intensity, rim: rim.intensity },
        beatOffset: Object.assign({}, beatOffset),
        activeBeats: activeBeats.map((b) => b.name),
        bodyYaw: parts.robot.rotation.y,
        headRotation: {
          x: parts.head.rotation.x, y: parts.head.rotation.y, z: parts.head.rotation.z,
        },
        blink: blink,
        voiceEnv: voiceEnv,
        antennaSwing: { zL: antPhys.zL, zR: antPhys.zR, x: antPhys.x },
      };
    },
    setPaused: function (value) {
      paused = !!value;
      if (opts.reducedMotion) return;
      renderer.setAnimationLoop(paused ? null : frame);
      if (!paused) last = null;
    },
    renderOnce: function () { renderStatic(); },
    // Deterministic stepping for verification: advance the live frame
    // loop by a virtual interval (ms) and render — beats become readouts
    // over time instead of eyeballed motion.
    advance: function (ms) {
      // Verification only: takes over the clock. The live rAF loop is
      // stopped first so a later real frame can never see a negative dt
      // (setPaused(false) resumes it with a fresh `last`).
      if (opts.reducedMotion || disposed) return;
      if (!paused) { paused = true; renderer.setAnimationLoop(null); }
      const step = Math.max(1, Math.min(50, ms || 16));
      let t = (last === null ? 0 : last);
      const end = t + (ms || 16);
      while (t < end) { t = Math.min(end, t + step); frame(t); }
    },
    dispose: function () {
      if (disposed) return;
      disposed = true;
      renderer.setAnimationLoop(null);
      document.removeEventListener('visibilitychange', onVisibility);
      if (observer) observer.disconnect();
      if (unsubscribe) unsubscribe();
      if (reducedTimer !== null) clearInterval(reducedTimer);
      scene.traverse(function (obj) {
        if (obj.geometry) obj.geometry.dispose();
        if (obj.material) {
          if (Array.isArray(obj.material)) obj.material.forEach(function (m) { m.dispose(); });
          else obj.material.dispose();
        }
      });
      if (groundTexture) groundTexture.dispose();
      envTarget.dispose();
      renderer.dispose();
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    },
  };
}
