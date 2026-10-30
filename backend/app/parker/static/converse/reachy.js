/*
 * Parker's Reachy Mini — the 3D presence renderer for the Converse page.
 *
 * Downstream of ParkerExpression (expression.js): this module only READS
 * the semantic state and draws it. It may never invent listening, work,
 * speech, or action completion — every motion here is caused by a state
 * or energy the controller derived from real signals.
 *
 * An original, stylized low-poly interpretation of the Reachy Mini
 * silhouette (head, two lens eyes, two antennas, little trunk body) built
 * from Three.js primitives — no downloaded model, no textures, no CDN.
 * Three.js is vendored and pinned (../vendor/three/, MIT).
 *
 * Degradation contract (the page owns the fallback):
 * - createReachyScene returns null when WebGL is unavailable — the page
 *   keeps the orb and the complete text/status experience;
 * - reducedMotion renders one static pose per state change, no loop;
 * - hidden page pauses the loop; dispose() releases every GL resource.
 */

import * as THREE from '../vendor/three/three.module.min.js';

// ---------------------------------------------------------------------------
// Small deterministic PRNG for idle gaze wander (no Math.random so a pose
// sequence is reproducible when seeded in tests).
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

// Critically-damped spring toward a target; returns [value, velocity].
function spring(value, velocity, target, omega, dt) {
  const x = value - target;
  const v = velocity + (-2 * omega * velocity - omega * omega * x) * dt;
  return [value + v * dt, v];
}

const CREAM = 0xf1ece2;
const DARK = 0x141b26;
const FACE = 0x0d131d;
const EYE_GLOW = 0x9fd8ff;
const AMBER = 0xffd166;
const CONCERN = 0xffb38a;

// ---------------------------------------------------------------------------
// Pose targets per semantic state. One place, readable top to bottom, so a
// reviewer can check "motion tells the truth" against the brief's table.
// ---------------------------------------------------------------------------

function poseFor(state, energies) {
  const user = energies.user;
  const parker = energies.parker;
  const pose = {
    headYaw: 0, headPitch: 0, headRoll: 0, bodyYaw: 0,
    eyeOpen: 0.7, eyeGlow: 0.45, eyeColor: EYE_GLOW,
    antennaL: -0.14, antennaR: 0.14, // resting gentle splay (z-rotation)
    breathRate: 0.28, breathAmp: 0.011,
    voice: 0,
  };
  switch (state.phase) {
    case 'offline':
      pose.headPitch = 0.34; pose.eyeOpen = 0.06; pose.eyeGlow = 0.05;
      pose.antennaL = -0.55; pose.antennaR = 0.55;
      pose.breathAmp = 0; break;
    case 'idle':
      pose.headPitch = 0.1; pose.eyeOpen = 0.55; pose.eyeGlow = 0.3;
      break;
    case 'connecting':
      pose.headPitch = -0.06; pose.eyeOpen = 0.85; pose.eyeGlow = 0.6;
      pose.antennaL = -0.05; pose.antennaR = 0.05;
      pose.breathRate = 0.5; break;
    case 'listening':
      pose.headPitch = -0.05; pose.eyeOpen = 0.9; pose.eyeGlow = 0.7;
      pose.antennaL = -0.04; pose.antennaR = 0.04;
      pose.breathRate = 0.32; pose.breathAmp = 0.012 + user * 0.01;
      break;
    case 'hearing':
      pose.headPitch = -0.1; pose.headRoll = 0.05 + user * 0.05;
      pose.headYaw = Math.sin(state.sincePhaseMs / 900) * 0.04 * (0.4 + user);
      pose.eyeOpen = 1.0; pose.eyeGlow = 0.85;
      pose.antennaL = -0.03 - user * 0.12; pose.antennaR = 0.03 + user * 0.12;
      pose.breathRate = 0.36; pose.breathAmp = 0.012 + user * 0.012;
      break;
    case 'thinking':
      pose.headYaw = 0.38; pose.headPitch = -0.16; pose.headRoll = 0.16;
      pose.eyeOpen = 0.55; pose.eyeGlow = 0.55;
      pose.antennaL = 0.02; pose.antennaR = -0.12; // one antenna curls in
      pose.breathRate = 0.45; break;
    case 'talking':
      pose.headPitch = -0.04 + parker * 0.07;
      pose.headYaw = Math.sin(state.sincePhaseMs / 700) * 0.03;
      pose.eyeOpen = 0.9; pose.eyeGlow = 0.75 + parker * 0.25;
      pose.antennaL = -0.05 - parker * 0.2; pose.antennaR = 0.05 + parker * 0.2;
      pose.breathRate = 0.4; pose.voice = parker;
      break;
    case 'interrupted':
      pose.headPitch = 0.14; pose.headYaw = -0.08;
      pose.eyeOpen = 0.35; pose.eyeGlow = 0.4;
      pose.antennaL = -0.4; pose.antennaR = 0.4;
      break;
    case 'closing':
      pose.headPitch = 0.2; pose.eyeOpen = 0.4; pose.eyeGlow = 0.3;
      pose.antennaL = -0.3; pose.antennaR = 0.3;
      pose.breathRate = 0.22; break;
    case 'stopped':
      pose.headPitch = 0.3; pose.eyeOpen = 0.12; pose.eyeGlow = 0.1;
      pose.antennaL = -0.5; pose.antennaR = 0.5;
      pose.breathAmp = 0.006; break;
    case 'error':
      pose.headPitch = 0.22; pose.headRoll = -0.1;
      pose.eyeOpen = 0.4; pose.eyeGlow = 0.35; pose.eyeColor = CONCERN;
      pose.antennaL = -0.45; pose.antennaR = 0.45;
      break;
    default: break;
  }
  // Overlays are secondary cues, never a second character.
  if (state.guard === 'redirect') {
    pose.headPitch += 0.22; pose.headRoll = -0.18;
    pose.eyeColor = CONCERN; pose.eyeGlow = Math.max(pose.eyeGlow, 0.55);
  } else if (state.guard === 'repair') {
    pose.headRoll += 0.18; pose.eyeOpen = Math.max(pose.eyeOpen, 0.85);
  }
  if (state.action === 'staged') {
    // Waiting on the screen below: a deliberate glance down at the card.
    pose.headPitch += 0.18; pose.eyeColor = AMBER;
    pose.eyeGlow = Math.max(pose.eyeGlow, 0.65);
  }
  return pose;
}

// ---------------------------------------------------------------------------
// Robot construction
// ---------------------------------------------------------------------------

function buildRobot() {
  const robot = new THREE.Group();

  const bodyMat = new THREE.MeshStandardMaterial({ color: CREAM, roughness: 0.55, metalness: 0.05 });
  const darkMat = new THREE.MeshStandardMaterial({ color: DARK, roughness: 0.6, metalness: 0.2 });
  const faceMat = new THREE.MeshStandardMaterial({ color: FACE, roughness: 0.3, metalness: 0.4 });

  const base = new THREE.Mesh(new THREE.CylinderGeometry(0.52, 0.58, 0.16, 40), darkMat);
  base.position.y = 0.08;
  robot.add(base);

  const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.4, 0.34, 8, 24), bodyMat);
  torso.position.y = 0.52;
  robot.add(torso);

  const collar = new THREE.Mesh(new THREE.TorusGeometry(0.22, 0.045, 12, 32), darkMat);
  collar.rotation.x = Math.PI / 2;
  collar.position.y = 0.96;
  robot.add(collar);

  const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.11, 0.13, 0.22, 20), darkMat);
  neck.position.y = 1.04;
  robot.add(neck);

  const head = new THREE.Group();
  head.position.y = 1.34;
  robot.add(head);

  const skull = new THREE.Mesh(new THREE.SphereGeometry(0.36, 36, 28), bodyMat);
  skull.scale.set(1.08, 0.86, 0.98);
  head.add(skull);

  // Face plate: a dark lens window on the front of the head.
  const plate = new THREE.Mesh(new THREE.SphereGeometry(0.33, 36, 28), faceMat);
  plate.scale.set(0.92, 0.66, 0.7);
  plate.position.set(0, -0.01, 0.155);
  head.add(plate);

  function eye(x) {
    const group = new THREE.Group();
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.095, 0.022, 12, 28),
      new THREE.MeshStandardMaterial({ color: 0xcfd6df, roughness: 0.35, metalness: 0.6 })
    );
    const iris = new THREE.Mesh(
      new THREE.CircleGeometry(0.078, 28),
      new THREE.MeshBasicMaterial({ color: EYE_GLOW, transparent: true, opacity: 0.8 })
    );
    iris.position.z = 0.005;
    group.add(ring); group.add(iris);
    group.position.set(x, 0.02, 0.395); // on the visor's face
    return { group, iris };
  }
  const eyeL = eye(-0.145);
  const eyeR = eye(0.145);
  head.add(eyeL.group); head.add(eyeR.group);

  // Voice light: a soft bar under the eyes that carries output energy.
  const voice = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.016, 0.1, 4, 10),
    new THREE.MeshBasicMaterial({ color: EYE_GLOW, transparent: true, opacity: 0 })
  );
  voice.rotation.z = Math.PI / 2;
  voice.position.set(0, -0.14, 0.375);
  head.add(voice);

  function antenna(x) {
    const group = new THREE.Group();
    const stem = new THREE.Mesh(
      new THREE.CylinderGeometry(0.014, 0.02, 0.5, 10),
      new THREE.MeshStandardMaterial({ color: 0xcfd6df, roughness: 0.4, metalness: 0.5 })
    );
    stem.position.y = 0.25;
    const tip = new THREE.Mesh(
      new THREE.SphereGeometry(0.055, 16, 12),
      new THREE.MeshStandardMaterial({
        color: CREAM, roughness: 0.5,
        emissive: new THREE.Color(AMBER), emissiveIntensity: 0,
      })
    );
    tip.position.y = 0.52;
    group.add(stem); group.add(tip);
    group.position.set(x, 0.26, -0.04);
    return { group, tip };
  }
  const antL = antenna(-0.19);
  const antR = antenna(0.19);
  head.add(antL.group); head.add(antR.group);

  // Soft fake ground shadow — cheap, no shadow maps.
  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(0.62, 40),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.4 })
  );
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = 0.005;
  robot.add(shadow);

  return { robot, head, torso, eyeL, eyeR, voice, antL, antR };
}

// ---------------------------------------------------------------------------
// Scene lifecycle
// ---------------------------------------------------------------------------

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
  const canvas = renderer.domElement;
  canvas.setAttribute('aria-hidden', 'true'); // presentation only — the
  // status text and controls carry every essential meaning.
  container.appendChild(canvas);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 20);
  camera.position.set(0, 1.3, 3.0);
  camera.lookAt(0, 0.94, 0);

  scene.add(new THREE.HemisphereLight(0x8fb4d8, 0x1a1410, 0.85));
  const key = new THREE.DirectionalLight(0xffe8c8, 1.5);
  key.position.set(2, 3, 2.4);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0x6db3ff, 0.9);
  rim.position.set(-2.4, 1.4, -2);
  scene.add(rim);

  const parts = buildRobot();
  scene.add(parts.robot);

  // Dynamic state: spring-tracked degrees of freedom.
  const dof = {};
  const vel = {};
  for (const key of ['headYaw', 'headPitch', 'headRoll', 'eyeOpen', 'eyeGlow', 'antennaL', 'antennaR', 'voice']) {
    dof[key] = 0; vel[key] = 0;
  }
  dof.eyeOpen = 0.55; dof.eyeGlow = 0.3;
  const eyeColor = new THREE.Color(EYE_GLOW);
  let breathPhase = 0;
  const rand = mulberry32(opts.seed || 20260831);
  let wander = { yaw: 0, pitch: 0, nextAt: 0 };
  let workPulse = 0;

  function resize() {
    const w = container.clientWidth || 300;
    const h = container.clientHeight || 240;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    // While the loop is paused (hidden tab, reduced motion) a resize must
    // still leave a correctly framed frame on screen, not a stretched one.
    renderer.render(scene, camera);
  }
  resize();
  let observer = null;
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(resize);
    observer.observe(container);
  }

  function applyPose(pose, dt, instant) {
    if (instant) {
      for (const key of Object.keys(dof)) {
        if (key in pose) dof[key] = pose[key];
      }
      dof.antennaL = pose.antennaL; dof.antennaR = pose.antennaR;
      dof.voice = pose.voice;
    } else {
      const speeds = {
        headYaw: 6, headPitch: 6, headRoll: 6,
        eyeOpen: 10, eyeGlow: 8, antennaL: 9, antennaR: 9, voice: 18,
      };
      for (const key of Object.keys(speeds)) {
        const target = key in pose ? pose[key] : 0;
        const s = spring(dof[key], vel[key], target, speeds[key], dt);
        dof[key] = s[0]; vel[key] = s[1];
      }
    }

    parts.head.rotation.set(dof.headPitch + wander.pitch, dof.headYaw + wander.yaw, dof.headRoll);
    parts.robot.rotation.y = dof.headYaw * 0.25;

    const breathe = 1 + Math.sin(breathPhase) * pose.breathAmp;
    parts.torso.scale.set(breathe, 1 / Math.max(breathe, 0.0001), breathe);

    for (const eye of [parts.eyeL, parts.eyeR]) {
      eye.group.scale.y = Math.max(0.05, dof.eyeOpen);
      eye.iris.material.opacity = 0.15 + dof.eyeGlow * 0.85;
      eye.iris.material.color.copy(eyeColor);
    }
    parts.antL.group.rotation.z = dof.antennaL;
    parts.antR.group.rotation.z = dof.antennaR;
    parts.voice.material.opacity = Math.min(1, dof.voice * 1.6);
    parts.voice.scale.x = 1 + dof.voice * 2.2;
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
    const pose = poseFor(state, { user: state.userEnergy, parker: state.parkerEnergy });

    // Idle/listening micro-gaze: a slow, bounded wander — attention, not
    // random jitter. Never during hearing/talking (real energy owns those).
    if (state.phase === 'idle' || state.phase === 'listening') {
      if (timeMs >= wander.nextAt) {
        wander.nextAt = timeMs + 2400 + rand() * 2600;
        wander.yaw = (rand() - 0.5) * 0.16;
        wander.pitch = (rand() - 0.5) * 0.06;
      }
    } else {
      wander.yaw *= 0.9; wander.pitch *= 0.9;
    }

    breathPhase += dt * Math.PI * 2 * pose.breathRate;

    // The work overlay: the right antenna tip glows in a slow pulse while
    // real background work is outstanding — and only then.
    if (state.work.length > 0) {
      workPulse += dt * 3.2;
      parts.antR.tip.material.emissiveIntensity = 0.7 + Math.sin(workPulse) * 0.5;
      const tipScale = 1 + Math.max(0, Math.sin(workPulse)) * 0.25;
      parts.antR.tip.scale.setScalar(tipScale);
    } else {
      parts.antR.tip.scale.setScalar(1);
      workPulse = 0;
      parts.antR.tip.material.emissiveIntensity = Math.max(
        0, parts.antR.tip.material.emissiveIntensity - dt * 2
      );
    }

    const targetColor = new THREE.Color(pose.eyeColor);
    eyeColor.lerp(targetColor, Math.min(1, dt * 5));

    applyPose(pose, dt, false);
    renderer.render(scene, camera);
  }

  let unsubscribe = null;
  let reducedTimer = null;

  function renderStatic() {
    if (disposed) return;
    const state = controller.getState();
    // Meaning stays, motion goes: real energies still shape the pose and
    // voice light, but nothing oscillates.
    const pose = poseFor(state, { user: state.userEnergy, parker: state.parkerEnergy });
    pose.breathAmp = 0; // reduced motion: no continuous oscillation
    eyeColor.set(pose.eyeColor);
    parts.antR.tip.material.emissiveIntensity = state.work.length > 0 ? 0.9 : 0;
    parts.antR.tip.scale.setScalar(state.work.length > 0 ? 1.15 : 1);
    breathPhase = 0;
    applyPose(pose, 0, true);
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
    // One static pose per semantic change; a slow interval lets TTL/dwell
    // housekeeping settle without a render loop.
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
      renderer.dispose();
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    },
  };
}
