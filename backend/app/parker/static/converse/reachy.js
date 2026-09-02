/*
 * Parker's Reachy Mini v2 — "Faithful & alive".
 *
 * Candidate replacement renderer for the Converse companion page.
 * Downstream of ParkerExpression (expression.js): this module only READS
 * the semantic state and draws it. Every motion here derives from a real
 * controller phase, overlay, or energy — no timer-invented busy-ness.
 * Idle life (breathing, blinks, gaze saccades) runs only in awake states;
 * offline/stopped/error are visibly inert.
 *
 * Faithful to the real Reachy Mini silhouette — white rounded body, big
 * head, two large lens-eyes, two wire antennae with tip beads — built
 * entirely from Three.js primitives and procedural textures. No external
 * models, textures, fonts, or HDRI. The effort budget goes to MOTION:
 * spring-damped everything, volume-preserving breathing, a blink/saccade
 * system, audio-reactive talking with an attack/decay envelope,
 * anticipation impulses on state changes, and antenna secondary physics.
 *
 * Degradation contract (the page owns the fallback):
 * - createReachyScene returns null when WebGL is unavailable;
 * - reducedMotion renders one truthful static pose per state change;
 * - hidden page pauses the loop; dispose() releases every GL resource.
 */

import * as THREE from '../vendor/three/three.module.min.js';

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

const SHELL = 0xf5f2ea;      // warm off-white plastic
const TRIM = 0xd9d4c8;       // bezel/antenna metal-ish white
const GLASS = 0x0a0e15;      // dark glass face
const EYE_GLOW = 0x8fd2ff;   // calm sky blue
const AMBER = 0xffd166;      // action staged / waiting on the screen
const CONCERN = 0xffb38a;    // guard / error warmth (never alarm-red)
const SUCCESS = 0xa5f0bb;    // action executed
const FLOOR_GLOW = 0x1b2434;

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
  blinks: true, saccades: false, gazeCamera: false,
};

function resetPose() {
  POSE.headDrop = 0;
  POSE.headYaw = 0; POSE.headPitch = 0; POSE.headRoll = 0; POSE.bodyYaw = 0;
  POSE.eyeOpen = 0.8; POSE.eyeGlow = 0.5; POSE.eyeColor = EYE_GLOW;
  POSE.antennaL = -0.16; POSE.antennaR = 0.16;
  POSE.breathRate = 0.26; POSE.breathAmp = 0.014;
  POSE.voice = 0;
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
      break;
    case 'dormant':   // powered but resting: same sunken sleep — with the
      // faintest eye ember as the honest "wake listening is armed" cue,
      // and the slowest whisper of breath. Lifeless until "Hey Parker."
      POSE.headPitch = 0.3; POSE.headDrop = 1; POSE.eyeOpen = 0.06; POSE.eyeGlow = 0.14;
      POSE.antennaL = -1.0; POSE.antennaR = 1.0;
      POSE.breathRate = 0.1; POSE.breathAmp = 0.005; POSE.blinks = false;
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
  envScene.background = new THREE.Color(0x04060a);
  const geoms = [];
  const mats = [];
  function panel(color, intensity, w, h, x, y, z, ry, rx) {
    const geom = new THREE.PlaneGeometry(w, h);
    const mat = new THREE.MeshBasicMaterial({ color: color });
    mat.color.multiplyScalar(intensity);
    const mesh = new THREE.Mesh(geom, mat);
    mesh.position.set(x, y, z);
    mesh.rotation.y = ry || 0;
    mesh.rotation.x = rx || 0;
    envScene.add(mesh);
    geoms.push(geom); mats.push(mat);
  }
  // Warm key window (lamp-side), cool TV spill, dim ceiling bounce.
  panel(0xffe0b8, 5.0, 3, 4, 4, 2.5, 2, -Math.PI / 3, 0);
  panel(0x7fb2ff, 2.4, 4, 2.4, -4.5, 1.6, -1, Math.PI / 2.6, 0);
  panel(0x36415a, 1.2, 8, 8, 0, 6, 0, 0, Math.PI / 2);
  panel(0x1a1410, 0.8, 8, 8, 0, -3, 0, 0, -Math.PI / 2);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const target = pmrem.fromScene(envScene, 0.08);
  pmrem.dispose();
  for (let i = 0; i < geoms.length; i++) geoms[i].dispose();
  for (let i = 0; i < mats.length; i++) mats[i].dispose();
  return target;
}

// ---------------------------------------------------------------------------
// Robot construction — faithful Reachy Mini silhouette from primitives.
// ---------------------------------------------------------------------------

function buildRobot(groundTexture) {
  const robot = new THREE.Group();

  const shellMat = new THREE.MeshPhysicalMaterial({
    color: SHELL, roughness: 0.42, metalness: 0.0,
    clearcoat: 0.5, clearcoatRoughness: 0.4,
  });
  const trimMat = new THREE.MeshStandardMaterial({
    color: TRIM, roughness: 0.35, metalness: 0.55,
  });
  const glassMat = new THREE.MeshPhysicalMaterial({
    color: GLASS, roughness: 0.14, metalness: 0.1,
    clearcoat: 1.0, clearcoatRoughness: 0.08,
  });
  const darkMat = new THREE.MeshStandardMaterial({
    color: 0x161c26, roughness: 0.6, metalness: 0.25,
  });

  // Body: a rounded squat lathe profile — the little white "trunk".
  const profile = [];
  const BODY_STEPS = 22;
  for (let i = 0; i <= BODY_STEPS; i++) {
    const t = i / BODY_STEPS;               // 0 bottom -> 1 top
    const y = t * 0.86;
    // Rounded barrel: wide hips, gentle waist, soft shoulder.
    const r = 0.5 * (1 - 0.28 * t * t) * Math.sqrt(Math.max(0.06, 1 - Math.pow(2 * t - 1, 4) * 0.22));
    profile.push(new THREE.Vector2(Math.max(0.14, r), y));
  }
  const bodyGeom = new THREE.LatheGeometry(profile, 44);
  const torso = new THREE.Mesh(bodyGeom, shellMat);
  torso.position.y = 0.06;
  robot.add(torso);

  const baseRing = new THREE.Mesh(new THREE.TorusGeometry(0.485, 0.035, 12, 44), darkMat);
  baseRing.rotation.x = Math.PI / 2;
  baseRing.position.y = 0.07;
  robot.add(baseRing);

  // Speaker: a small dark grille dot-band low on the front of the body.
  const speaker = new THREE.Mesh(new THREE.CapsuleGeometry(0.03, 0.16, 6, 12), darkMat);
  speaker.rotation.z = Math.PI / 2;
  speaker.position.set(0, 0.28, 0.445);
  speaker.rotation.x = 0.18;
  robot.add(speaker);

  // Neck seam where the head floats over the body.
  const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.19, 0.16, 24), darkMat);
  neck.position.y = 0.94;
  robot.add(neck);

  // Head: the star — big, wide, rounded. Pivot low so nods read naturally.
  const head = new THREE.Group();
  head.position.y = 1.28;
  robot.add(head);

  const skull = new THREE.Mesh(new THREE.SphereGeometry(0.42, 44, 32), shellMat);
  skull.scale.set(1.12, 0.88, 1.0);
  head.add(skull);

  // The face is the same white shell (research correction: the real
  // Reachy Mini's darkness lives only in the lens rings) — each eye gets
  // its own dark glass seat instead of one big dark window.

  // Eyes: two big camera-lens eyes with emissive iris + fixed catchlight.
  function makeEye(x) {
    const group = new THREE.Group();
    group.position.set(x, 0.035, 0.45);
    const seat = new THREE.Mesh(new THREE.CircleGeometry(0.128, 36), glassMat);
    seat.position.z = -0.004;
    group.add(seat);
    const bezel = new THREE.Mesh(new THREE.TorusGeometry(0.108, 0.024, 14, 36), trimMat);
    group.add(bezel);
    const lens = new THREE.Mesh(
      new THREE.CircleGeometry(0.1, 36),
      new THREE.MeshStandardMaterial({ color: 0x04070c, roughness: 0.12, metalness: 0.35 })
    );
    lens.position.z = 0.002;
    group.add(lens);
    // Gaze group: iris + halo + catchlight translate together for saccades.
    const gaze = new THREE.Group();
    const halo = new THREE.Mesh(
      new THREE.CircleGeometry(0.095, 36),
      new THREE.MeshBasicMaterial({
        color: EYE_GLOW, transparent: true, opacity: 0.24,
        blending: THREE.AdditiveBlending, depthWrite: false,
      })
    );
    halo.position.z = 0.004;
    gaze.add(halo);
    const iris = new THREE.Mesh(
      new THREE.CircleGeometry(0.074, 32),
      new THREE.MeshBasicMaterial({ color: EYE_GLOW, transparent: true, opacity: 0.85 })
    );
    iris.position.z = 0.006;
    gaze.add(iris);
    const catchlight = new THREE.Mesh(
      new THREE.CircleGeometry(0.02, 16),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.95, depthWrite: false })
    );
    catchlight.position.set(-0.03, 0.036, 0.008);
    gaze.add(catchlight);
    group.add(gaze);
    return { group: group, gaze: gaze, iris: iris, halo: halo };
  }
  const eyeL = makeEye(-0.165);
  const eyeR = makeEye(0.165);
  head.add(eyeL.group); head.add(eyeR.group);

  // Voice light: the face stays mouthless (that IS the cute); Parker's
  // REAL output energy glows from the body speaker instead, like sound
  // coming from where the sound comes from.
  const voice = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.014, 0.1, 6, 12),
    new THREE.MeshBasicMaterial({
      color: EYE_GLOW, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false,
    })
  );
  voice.rotation.z = Math.PI / 2;
  voice.rotation.x = 0.18;
  voice.position.set(0, 0.285, 0.452);
  robot.add(voice);

  // Antennae: gently curved wire stems with tip beads, on swing pivots so
  // secondary physics can lag them behind head motion.
  function makeAntenna(side) {
    const pivot = new THREE.Group();          // physics swing lives here
    pivot.position.set(side * 0.2, 0.3, -0.06);
    const arm = new THREE.Group();            // pose splay lives here
    pivot.add(arm);
    const curve = new THREE.QuadraticBezierCurve3(
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(side * 0.05, 0.22, -0.02),
      new THREE.Vector3(side * 0.17, 0.4, -0.05)
    );
    const stem = new THREE.Mesh(new THREE.TubeGeometry(curve, 16, 0.011, 8, false), trimMat);
    arm.add(stem);
    const tip = new THREE.Mesh(
      new THREE.SphereGeometry(0.042, 18, 14),
      new THREE.MeshStandardMaterial({
        color: SHELL, roughness: 0.45, metalness: 0.05,
        emissive: new THREE.Color(AMBER), emissiveIntensity: 0,
      })
    );
    tip.position.set(side * 0.17, 0.4, -0.05);
    arm.add(tip);
    return { pivot: pivot, arm: arm, tip: tip };
  }
  const antL = makeAntenna(-1);
  const antR = makeAntenna(1);
  head.add(antL.pivot); head.add(antR.pivot);

  // Soft procedural ground pool — no shadow maps.
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(1.9, 1.9),
    groundTexture
      ? new THREE.MeshBasicMaterial({ map: groundTexture, transparent: true, depthWrite: false })
      : new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.35, depthWrite: false })
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = 0.004;
  robot.add(ground);

  // A faint cool floor glow disc gives the dark room some grounding light.
  const floorGlow = new THREE.Mesh(
    new THREE.CircleGeometry(1.15, 40),
    new THREE.MeshBasicMaterial({
      color: FLOOR_GLOW, transparent: true, opacity: 0.35,
      blending: THREE.AdditiveBlending, depthWrite: false,
    })
  );
  floorGlow.rotation.x = -Math.PI / 2;
  floorGlow.position.y = 0.002;
  robot.add(floorGlow);

  return {
    robot: robot, torso: torso, head: head,
    eyeL: eyeL, eyeR: eyeR, voice: voice, antL: antL, antR: antR,
  };
}

// ---------------------------------------------------------------------------
// Scene lifecycle
// ---------------------------------------------------------------------------

const SPRING_KEYS = ['headYaw', 'headPitch', 'headRoll', 'headDrop', 'eyeOpen', 'eyeGlow', 'antennaL', 'antennaR', 'voice', 'gazeX', 'gazeY'];
const SPRING_OMEGA = {
  headYaw: 5.5, headPitch: 5.5, headRoll: 5.5, headDrop: 3.2,
  eyeOpen: 12, eyeGlow: 8, antennaL: 8, antennaR: 8,
  voice: 20, gazeX: 22, gazeY: 22, // saccades snap, they don't drift
};
// Anticipation: a brief counter-impulse before the head commits to a new
// pose (the classic animation beat). Applied to head springs on phase change.
const ANTICIPATION_KEYS = ['headYaw', 'headPitch', 'headRoll'];
const ANTICIPATION_GAIN = 3.2;

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
  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 20);
  camera.position.set(0, 1.26, 3.05);
  camera.lookAt(0, 1.04, 0);

  // Dark-living-room lighting: dim cool ambience, one warm lamp key, one
  // cool rim so the white shell separates from the dark at 3 meters.
  scene.add(new THREE.HemisphereLight(0x51719e, 0x1a1512, 0.85));
  const key = new THREE.DirectionalLight(0xfff1de, 1.9);
  key.position.set(2.2, 2.8, 2.2);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0x6fb0ff, 1.1);
  rim.position.set(-2.6, 1.6, -2.2);
  scene.add(rim);

  const envTarget = buildEnvironment(renderer);
  scene.environment = envTarget.texture;

  const groundTexture = makeGroundTexture();
  const parts = buildRobot(groundTexture);
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
    // Head: springs + talking micro-nod that rides the REAL voice envelope.
    const nod = -voiceEnv * 0.05;
    parts.head.rotation.set(dof.headPitch + nod, dof.headYaw, dof.headRoll);
    parts.robot.rotation.y = dof.headYaw * 0.22 + pose.bodyYaw;

    // Volume-preserving breath: chest swells out, height compensates.
    const breathe = 1 + Math.sin(breathPhase) * pose.breathAmp;
    parts.torso.scale.set(breathe, 1 / Math.max(breathe, 0.0001), breathe);
    parts.head.position.y = 1.28 - dof.headDrop * 0.16
      + Math.sin(breathPhase) * pose.breathAmp * 0.6;

    // Eyes: openness x blink, glow, gaze saccade offsets.
    const open = Math.max(0.04, dof.eyeOpen * blink);
    parts.eyeL.group.scale.y = open;
    parts.eyeR.group.scale.y = open;
    const glowOpacity = 0.12 + dof.eyeGlow * 0.88;
    parts.eyeL.iris.material.opacity = glowOpacity;
    parts.eyeR.iris.material.opacity = glowOpacity;
    parts.eyeL.halo.material.opacity = dof.eyeGlow * 0.3;
    parts.eyeR.halo.material.opacity = dof.eyeGlow * 0.3;
    parts.eyeL.iris.material.color.copy(eyeColor);
    parts.eyeR.iris.material.color.copy(eyeColor);
    parts.eyeL.halo.material.color.copy(eyeColor);
    parts.eyeR.halo.material.color.copy(eyeColor);
    parts.eyeL.gaze.position.set(dof.gazeX, dof.gazeY, 0);
    parts.eyeR.gaze.position.set(dof.gazeX, dof.gazeY, 0);

    // Antennae: pose splay on the arm, physics swing on the pivot.
    // Sign flip: the pose table is authored as negative-L / positive-R
    // = OUTWARD splay; the pivots' z-axis runs the other way.
    parts.antL.arm.rotation.z = -dof.antennaL;
    parts.antR.arm.rotation.z = -dof.antennaR;
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
    const dt = Math.min(0.05, last === null ? 0.016 : (timeMs - last) / 1000);
    last = timeMs;
    controller.tick();
    const state = controller.getState();
    const pose = poseFor(state);

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
    springStep('eyeOpen', pose.eyeOpen, dt);
    springStep('eyeGlow', pose.eyeGlow, dt);
    springStep('antennaL', pose.antennaL, dt);
    springStep('antennaR', pose.antennaR, dt);
    springStep('voice', pose.voice, dt);
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
    unsubscribe = controller.subscribe(renderStatic);
    renderStatic();
    reducedTimer = setInterval(function () {
      controller.tick();
    }, 400);
  } else {
    renderer.setAnimationLoop(frame);
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
