/* Reachy Mini's published CAD shells, with Parker's presentation materials.
 * Upstream source, license, hashes, and reproducible packing instructions:
 * ../vendor/reachy-mini/README.md. All assets are served from this engine.
 */
import * as THREE from '../vendor/three/three.module.min.js';

const asset = name => new URL('../vendor/reachy-mini/' + name, import.meta.url);
async function readAsset(name, type) {
  const response = await fetch(asset(name));
  if (!response.ok) throw new Error('Reachy geometry unavailable');
  return response[type]();
}
// The page already awaits the renderer module before replacing its accessible
// fallback. No incomplete robot is ever reported as a loaded WebGL scene.
const [manifest, data] = await Promise.all([
  readAsset('reachy-mini.json', 'json'), readAsset('reachy-mini.bin', 'arrayBuffer'),
]);
const SCALE = 7;
export const HEAD_HEIGHT = 1.855;

function cadGeometry(name) {
  const entry = manifest.meshes[name];
  const coordinates = new Int16Array(data, entry.position_offset, entry.vertex_count * 3);
  const positions = new Float32Array(coordinates.length);
  const k = manifest.coordinate_scale * SCALE;
  // CAD: X right, Y rear, Z up. Three: X right, Y up, Z front.
  for (let i = 0; i < coordinates.length; i += 3) {
    positions[i] = coordinates[i] * k;
    positions[i + 1] = coordinates[i + 2] * k;
    positions[i + 2] = -coordinates[i + 1] * k;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(data, entry.index_offset, entry.index_count), 1));
  geometry.computeVertexNormals();
  return geometry;
}

export function buildReachyModel(groundTexture) {
  const robot = new THREE.Group();
  const torso = new THREE.Group();
  const head = new THREE.Group();
  head.position.y = HEAD_HEIGHT;
  robot.add(torso, head);
  const shell = new THREE.MeshPhysicalMaterial({
    color: 0xe8e7e2, roughness: 0.3, metalness: 0,
    clearcoat: 0.22, clearcoatRoughness: 0.3,
  });
  const graphite = new THREE.MeshStandardMaterial({color: 0x171b21, roughness: 0.32, metalness: 0.18});
  const rubber = new THREE.MeshStandardMaterial({color: 0x101316, roughness: 0.8});
  const steel = new THREE.MeshStandardMaterial({color: 0x9ca7ad, roughness: 0.22, metalness: 0.92});
  const lensGlass = new THREE.MeshPhysicalMaterial({
    color: 0x060c14, metalness: 0.35, roughness: 0.055,
    clearcoat: 1, clearcoatRoughness: 0.025, envMapIntensity: 1.8,
  });
  function cad(name, parent, material, y = 0) {
    const mesh = new THREE.Mesh(cadGeometry(name), material);
    mesh.name = name;
    mesh.position.y = y;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  }
  cad('body_foot_3dprint', torso, rubber, 1.61);
  cad('body_down_3dprint', torso, shell, 1.61);
  cad('body_top_3dprint', torso, shell, 1.61);
  cad('head_back_3dprint', head, shell);
  cad('head_front_3dprint', head, shell);
  cad('head_mic_3dprint', head, shell);
  // The downloadable holder/caps are INTERNAL camera mounts. The visible
  // optical bezel sits in front of the shell, covering its mounting holes.
  const bridge = new THREE.Mesh(new THREE.BoxGeometry(0.26, 0.045, 0.018), graphite);
  bridge.position.set(0.015, 0, 0.342);
  head.add(bridge);

  // Real lenses stay circular even while resting. Their machined concentric
  // rings, convex glass, and room reflections carry the face; light is a
  // subtle Parker status cue, never a cartoon pupil or a fake display.
  function eye(x, radius) {
    const group = new THREE.Group();
    group.position.set(x, 0, 0.338);
    head.add(group);
    const barrel = new THREE.Mesh(new THREE.CylinderGeometry(radius * 1.14, radius * 1.16, 0.035, 64), graphite);
    barrel.rotation.x = Math.PI / 2;
    group.add(barrel);
    const glass = new THREE.Mesh(new THREE.SphereGeometry(radius * 0.92, 48, 32), lensGlass);
    glass.scale.z = 0.28;
    glass.position.z = 0.025;
    group.add(glass);
    for (const factor of [0.91, 0.98]) {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(radius * factor, 0.003, 8, 64), graphite);
      ring.position.z = 0.028;
      group.add(ring);
    }
    const gaze = new THREE.Group();
    group.add(gaze);
    const iris = new THREE.Mesh(new THREE.RingGeometry(radius * 0.34, radius * 0.37, 48),
      new THREE.MeshBasicMaterial({color: 0x8fd2ff, transparent: true, opacity: 0.1, depthWrite: false}));
    iris.position.z = radius * 0.28 + 0.029;
    gaze.add(iris);
    const halo = new THREE.Mesh(new THREE.RingGeometry(radius * 0.91, radius * 0.94, 64),
      new THREE.MeshBasicMaterial({color: 0x8fd2ff, transparent: true, opacity: 0.03, depthWrite: false}));
    halo.position.z = 0.045;
    group.add(halo);
    return {group, gaze, iris, halo};
  }
  const eyeL = eye(-0.0328 * SCALE, 0.022 * SCALE);
  const eyeR = eye(0.0322 * SCALE, 0.018 * SCALE);

  // Six articulated rods connect the open torso to the moving head. Each
  // endpoint follows the same head matrix as the shell, including sleep.
  const rods = [];
  const rodGeometry = new THREE.CylinderGeometry(0.009, 0.009, 1, 12);
  const ballGeometry = new THREE.SphereGeometry(0.022, 16, 12);
  for (let pair = 0; pair < 3; pair++) {
    for (const side of [-1, 1]) {
      const angle = pair * Math.PI * 2 / 3 + 0.25;
      const low = angle + side * 0.30;
      const high = angle - side * 0.26;
      const bottom = new THREE.Vector3(Math.sin(low) * 0.31, 0.91, Math.cos(low) * 0.31);
      const top = new THREE.Vector3(Math.sin(high) * 0.23, -0.32, Math.cos(high) * 0.23);
      const rod = new THREE.Mesh(rodGeometry, steel);
      const ball = new THREE.Mesh(ballGeometry, steel);
      const base = new THREE.Mesh(ballGeometry, graphite);
      base.position.copy(bottom);
      robot.add(rod, ball, base);
      rods.push({rod, ball, bottom, top});
    }
  }
  const endpoint = new THREE.Vector3();
  const direction = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);
  function updateNeck() {
    head.updateMatrix();
    for (const link of rods) {
      endpoint.copy(link.top).applyMatrix4(head.matrix);
      link.ball.position.copy(endpoint);
      direction.subVectors(endpoint, link.bottom);
      link.rod.position.copy(link.bottom).addScaledVector(direction, 0.5);
      link.rod.scale.y = direction.length();
      link.rod.quaternion.setFromUnitVectors(up, direction.normalize());
    }
  }

  function antenna(side) {
    const pivot = new THREE.Group();
    pivot.position.set(side * 0.044 * SCALE, 0.037 * SCALE, -0.08);
    head.add(pivot);
    const arm = new THREE.Group();
    pivot.add(arm);
    const base = new THREE.Mesh(new THREE.CylinderGeometry(0.027, 0.032, 0.065, 24), graphite);
    arm.add(base);
    const points = [];
    for (let i = 0; i <= 128; i++) {
      const t = i / 128;
      const a = t * 7 * Math.PI * 2;
      points.push(new THREE.Vector3(Math.cos(a) * 0.021, 0.025 + t * 0.12, Math.sin(a) * 0.021));
    }
    arm.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points), 128, 0.005, 6, false), steel));
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.005, 0.008, 0.60, 12), graphite);
    stem.position.y = 0.43;
    arm.add(stem);
    const tip = new THREE.Mesh(new THREE.CapsuleGeometry(0.009, 0.025, 4, 8),
      new THREE.MeshStandardMaterial({color: 0x222831, roughness: 0.35, emissive: 0xffd166, emissiveIntensity: 0}));
    tip.position.y = 0.74;
    arm.add(tip);
    return {pivot, arm, tip};
  }
  const antL = antenna(-1), antR = antenna(1);
  const voice = new THREE.Mesh(new THREE.CircleGeometry(0.012, 24),
    new THREE.MeshBasicMaterial({color: 0x8fd2ff, transparent: true, opacity: 0}));
  voice.position.set(0, 0.33, 0.536);
  torso.add(voice);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(2.5, 2.5),
    new THREE.MeshBasicMaterial({map: groundTexture, transparent: true, depthWrite: false, opacity: 0.65}));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = 0.004;
  robot.add(ground);
  updateNeck();
  return {robot, torso, head, eyeL, eyeR, voice, antL, antR, updateNeck};
}
