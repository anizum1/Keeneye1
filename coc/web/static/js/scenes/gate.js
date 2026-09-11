/**
 * The Vault Door — the login scene.
 *
 * A hooded figure sits at a laptop in the dark, lit only by its screen. In
 * front of the viewer floats a key whose teeth are cut from a SHA-256 hash of
 * whatever has been typed into the access-key field, so the key visibly takes
 * shape as the credential is pasted in. Turning it drops the tumblers, aligns
 * the rings and opens the shackle; the camera then flies through the door.
 *
 * The hash used to cut the key is computed in the browser and never sent
 * anywhere. It is a visualisation of the credential, not the credential.
 */

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

import { PALETTE } from '../palette.js';
import { circuitFloor, glow, metal, motes, pointCloud, ring, tickAll } from '../materials.js';
import { clamp, damp, digestToUnits, easeOutBack, TAU } from '../util.js';

const RING_COUNT = 4;
const TUMBLER_COUNT = 6;

/** SHA-256 in the browser, so the key's cut genuinely encodes what was typed. */
async function digestOf(text) {
  if (!text) return '0'.repeat(64);
  if (!window.crypto?.subtle) {
    // Insecure origins have no SubtleCrypto. The key still animates; it just
    // falls back to a non-cryptographic fingerprint for its shape.
    let hash = 2166136261;
    for (const character of text) {
      hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
    }
    return (hash >>> 0).toString(16).padStart(8, '0').repeat(8);
  }
  const bytes = new TextEncoder().encode(text);
  const buffer = await window.crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Build the key's silhouette: a ring bow, a shaft, and teeth cut from a hash. */
function keyGeometry(digest) {
  const units = digestToUnits(digest, 9);
  const shape = new THREE.Shape();

  const shaftTop = 0.16;
  const bladeStart = 0.55;
  const bladeEnd = 2.45;
  const toothWidth = (bladeEnd - bladeStart) / units.length;

  shape.moveTo(-0.55, -shaftTop);
  shape.lineTo(bladeStart, -shaftTop);

  // Each pair of hash bytes sets one tooth's depth. A changed credential
  // recuts the whole blade, which is the point.
  units.forEach((unit, index) => {
    const x0 = bladeStart + index * toothWidth;
    const x1 = x0 + toothWidth * 0.62;
    const x2 = x0 + toothWidth;
    const depth = -shaftTop - 0.10 - unit * 0.34;
    shape.lineTo(x0, -shaftTop);
    shape.lineTo(x0 + toothWidth * 0.12, depth);
    shape.lineTo(x1, depth);
    shape.lineTo(x2, -shaftTop);
  });

  shape.lineTo(bladeEnd, -shaftTop);
  shape.lineTo(bladeEnd, shaftTop);
  shape.lineTo(-0.55, shaftTop);
  shape.closePath();

  const blade = new THREE.ExtrudeGeometry(shape, {
    depth: 0.1, bevelEnabled: true, bevelThickness: 0.02, bevelSize: 0.02, bevelSegments: 2,
  });
  blade.translate(0, 0, -0.05);

  const bow = new THREE.TorusGeometry(0.46, 0.11, 12, 40);
  bow.translate(-1.0, 0, 0);

  // ExtrudeGeometry comes back non-indexed while TorusGeometry is indexed, and
  // mergeGeometries refuses a mix. Normalising both to non-indexed is cheaper
  // than indexing the extrusion and keeps the merge total.
  return mergeGeometries([blade.toNonIndexed(), bow.toNonIndexed()], false) ?? blade;
}

/**
 * The hooded figure, as a point cloud.
 *
 * Rendered from the vertices of a handful of primitives rather than a loaded
 * model: a solid figure would read as clip art, while the same silhouette made
 * of drifting points reads as data, which is what it is standing in for.
 */
function figureGeometry() {
  const parts = [];

  const hood = new THREE.SphereGeometry(0.62, 34, 26, 0, TAU, 0, Math.PI * 0.62);
  hood.scale(1.0, 1.15, 0.92);
  hood.translate(0, 1.62, -0.08);
  parts.push(hood);

  const face = new THREE.SphereGeometry(0.4, 20, 16);
  face.scale(1, 1.1, 0.8);
  face.translate(0, 1.48, 0.1);
  parts.push(face);

  const torso = new THREE.CylinderGeometry(0.52, 0.86, 1.3, 26, 6, true);
  torso.translate(0, 0.72, 0);
  parts.push(torso);

  const shoulders = new THREE.SphereGeometry(0.86, 26, 14, 0, TAU, 0, Math.PI * 0.5);
  shoulders.scale(1.25, 0.55, 0.9);
  shoulders.translate(0, 1.05, 0);
  parts.push(shoulders);

  // Both arms reaching forward to the laptop.
  for (const side of [-1, 1]) {
    const arm = new THREE.CylinderGeometry(0.16, 0.13, 1.05, 12, 4, true);
    arm.rotateZ(side * 0.42);
    arm.rotateX(-1.02);
    arm.translate(side * 0.58, 0.92, 0.5);
    parts.push(arm);
  }

  // Normalised for the same reason as the key: the open-ended cylinders and
  // the spheres do not agree about indexing.
  return mergeGeometries(parts.map((part) => part.toNonIndexed()), false) ?? parts[0];
}

export class GateScene {
  constructor() {
    this.pickable = [];
    this.state = 'sealed';     // sealed → turning → open | rejected
    this.ringSpin = new Array(RING_COUNT).fill(0).map((_, i) => (i + 1) * 0.22 * (i % 2 ? -1 : 1));
    this.ringAngle = new Array(RING_COUNT).fill(0).map(() => Math.random() * TAU);
    this.shackle = 0;          // 0 closed, 1 open
    this.shackleTarget = 0;
    this.keyInsert = 0;
    this.keyInsertTarget = 0;
    this.keyTurn = 0;
    this.keyTurnTarget = 0;
    this.shake = 0;
    this.currentDigest = '0'.repeat(64);
  }

  build(group, world) {
    this.world = world;
    const origin = new THREE.Group();
    group.add(origin);
    this.origin = origin;

    // -- ground ------------------------------------------------------
    const floor = circuitFloor({ size: 180, scale: 5.5, trace: PALETTE.cyan, accent: PALETTE.violet, fade: 70 });
    floor.position.y = -1.6;
    origin.add(floor);
    origin.add(motes(Math.round(320 * world.tier.particles), 26, PALETTE.cyan));

    // -- the figure and the laptop -----------------------------------
    const figure = pointCloud(figureGeometry(), { colour: PALETTE.cyan, size: 0.045, opacity: 0.75 });
    figure.position.set(5.6, -1.7, -2.6);
    figure.rotation.y = -0.62;
    figure.scale.setScalar(1.45);
    origin.add(figure);
    this.figure = figure;

    const laptop = new THREE.Group();
    const base = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.07, 1.02), metal(0x141c2c));
    laptop.add(base);

    const lid = new THREE.Mesh(new THREE.BoxGeometry(1.5, 1.0, 0.05), metal(0x141c2c));
    lid.position.set(0, 0.48, -0.5);
    lid.rotation.x = -0.28;
    laptop.add(lid);

    const panel = new THREE.Mesh(
      new THREE.PlaneGeometry(1.38, 0.88),
      new THREE.MeshBasicMaterial({ color: PALETTE.cyan, transparent: true, opacity: 0.5 }),
    );
    panel.position.set(0, 0.48, -0.47);
    panel.rotation.x = -0.28;
    laptop.add(panel);
    this.screen = panel;

    // The only light on the figure comes from the screen it is facing.
    const screenLight = new THREE.PointLight(PALETTE.cyan, 7, 11, 2);
    screenLight.position.set(0, 0.6, 0.2);
    laptop.add(screenLight);
    this.screenLight = screenLight;

    laptop.position.set(4.5, -0.75, -1.5);
    laptop.rotation.y = -0.62;
    laptop.scale.setScalar(1.25);
    origin.add(laptop);

    // -- the door ----------------------------------------------------
    const door = new THREE.Group();
    door.position.set(0.6, 1.3, -1.2);
    origin.add(door);
    this.door = door;

    // Concentric HUD rings, scattered until the key turns.
    this.rings = [];
    for (let index = 0; index < RING_COUNT; index += 1) {
      const radius = 1.5 + index * 0.55;
      const mesh = ring(index % 2 ? PALETTE.cyan : PALETTE.gold, {
        inner: radius, outer: radius + 0.035, segments: 160, opacity: 0.55,
      });
      // Gaps in the ring, so alignment is visible when it happens.
      mesh.geometry = new THREE.RingGeometry(radius, radius + 0.035, 80, 1, 0, TAU * (0.62 + index * 0.08));
      door.add(mesh);
      this.rings.push(mesh);
    }

    const dial = new THREE.Mesh(new THREE.TorusGeometry(1.18, 0.07, 16, 96), glow(PALETTE.gold, 0.9));
    door.add(dial);

    // -- the padlock -------------------------------------------------
    const lock = new THREE.Group();
    lock.scale.setScalar(1.25);
    door.add(lock);
    this.lock = lock;

    const body = new THREE.Mesh(new THREE.BoxGeometry(1.18, 0.94, 0.44), glow(PALETTE.gold, 0.55));
    body.geometry.translate(0, -0.1, 0);
    lock.add(body);

    const shackle = new THREE.Mesh(
      new THREE.TorusGeometry(0.34, 0.085, 14, 40, Math.PI),
      metal(0xb8c4dc, { roughness: 0.25, metalness: 1.0 }),
    );
    shackle.position.set(0, 0.4, 0);
    lock.add(shackle);
    this.shackleMesh = shackle;

    // Keyway and tumblers.
    const keyway = new THREE.Mesh(
      new THREE.CylinderGeometry(0.2, 0.2, 0.5, 24),
      metal(0x0a0f1a, { roughness: 0.6, metalness: 0.4 }),
    );
    keyway.rotation.x = Math.PI / 2;
    keyway.position.set(0, -0.12, 0.22);
    lock.add(keyway);
    this.keyway = keyway;

    this.tumblers = [];
    for (let index = 0; index < TUMBLER_COUNT; index += 1) {
      const pin = new THREE.Mesh(
        new THREE.BoxGeometry(0.05, 0.3, 0.05),
        glow(PALETTE.cyan, 1.1),
      );
      pin.position.set(-0.22 + index * 0.088, 0.16, 0.22);
      pin.userData.rest = pin.position.y;
      lock.add(pin);
      this.tumblers.push(pin);
    }

    // -- the key -----------------------------------------------------
    const key = new THREE.Mesh(keyGeometry(this.currentDigest), glow(PALETTE.gold, 0.9));
    key.scale.setScalar(0.3);
    key.position.set(0, -2.35, 3.4);
    door.add(key);
    this.key = key;

    this.keyLabel = world.label(
      '<span class="tag-k">key cut from</span><code>—</code>',
      { className: 'tag key-tag' },
    );
    this.keyLabel.position.set(0, -3.0, 3.4);
    door.add(this.keyLabel);

    this.floor = floor;
  }

  enter() {
    this.state = 'sealed';
    this.shackleTarget = 0;
    this.keyInsertTarget = 0;
    this.keyTurnTarget = 0;
  }

  /** Recut the key from a new credential. Debounced by the caller. */
  async setCredential(text) {
    const digest = await digestOf(text);
    if (digest === this.currentDigest) return;
    this.currentDigest = digest;

    const geometry = keyGeometry(digest);
    this.key.geometry.dispose();
    this.key.geometry = geometry;

    const element = this.keyLabel.element.querySelector('code');
    if (element) element.textContent = text ? `${digest.slice(0, 24)}…` : '—';

    // A short flare so the recut is felt, not just seen.
    this.key.material.emissiveIntensity = 2.0;
  }

  /** Drive the unlock. Resolves once the door is open. */
  unlock() {
    this.state = 'turning';
    this.keyInsertTarget = 1;
    return new Promise((resolve) => { this._onOpen = resolve; });
  }

  /** Slam shut. Used when the server rejects the credential. */
  reject() {
    this.state = 'rejected';
    this.keyInsertTarget = 0;
    this.keyTurnTarget = 0;
    this.shackleTarget = 0;
    this.shake = 1;
    for (const pin of this.tumblers) pin.material.color.setHex(PALETTE.crimson);
    for (const mesh of this.rings) mesh.material.color.setHex(PALETTE.crimson);
    setTimeout(() => {
      if (this.state !== 'rejected') return;
      this.state = 'sealed';
      for (const pin of this.tumblers) pin.material.color.setHex(PALETTE.cyan);
      this.rings.forEach((mesh, index) => {
        mesh.material.color.setHex(index % 2 ? PALETTE.cyan : PALETTE.gold);
      });
    }, 1400);
  }

  update(dt, time, active) {
    tickAll(this.origin, dt, time);

    // The laptop screen flickers the way a screen does.
    const flicker = 0.42 + Math.sin(time * 7.3) * 0.05 + Math.sin(time * 2.1) * 0.06;
    this.screen.material.opacity = flicker;
    this.screenLight.intensity = 6 + flicker * 4;

    this.figure.rotation.y = -0.62 + Math.sin(time * 0.5) * 0.035;
    this.key.material.emissiveIntensity = damp(this.key.material.emissiveIntensity, 0.9, 3, dt);

    if (!active) return;

    // -- key travel and turn ----------------------------------------
    this.keyInsert = damp(this.keyInsert, this.keyInsertTarget, 4.5, dt);
    this.keyTurn = damp(this.keyTurn, this.keyTurnTarget, 6.0, dt);

    const rest = -2.35;
    const seated = -0.15;
    this.key.position.z = 3.4 - this.keyInsert * 3.08;
    this.key.position.y = rest + (seated - rest) * this.keyInsert
      + (1 - this.keyInsert) * Math.sin(time * 1.1) * 0.09;
    this.key.rotation.z = this.keyTurn * (Math.PI / 2);
    this.key.rotation.y = (1 - this.keyInsert) * Math.sin(time * 1.4) * 0.14;
    this.key.scale.setScalar(0.3 + this.keyInsert * 0.05);

    if (this.state === 'turning' && this.keyInsert > 0.96 && this.keyTurnTarget === 0) {
      this.keyTurnTarget = 1;
    }

    // -- tumblers drop in sequence as the key turns ------------------
    this.tumblers.forEach((pin, index) => {
      const threshold = 0.15 + index * 0.13;
      const dropped = this.keyTurn > threshold;
      const target = pin.userData.rest - (dropped ? 0.2 : 0);
      pin.position.y = damp(pin.position.y, target, 12, dt);
      pin.material.emissiveIntensity = dropped ? 2.2 : 0.7;
    });

    // -- rings: free-spinning until the lock gives, then aligned -----
    const aligning = this.keyTurn > 0.82;
    this.rings.forEach((mesh, index) => {
      if (aligning) {
        this.ringAngle[index] = damp(this.ringAngle[index], 0, 5, dt);
        mesh.material.opacity = damp(mesh.material.opacity, 0.95, 4, dt);
      } else {
        this.ringAngle[index] += this.ringSpin[index] * dt * (this.state === 'turning' ? 3.2 : 1);
      }
      mesh.rotation.z = this.ringAngle[index];
      mesh.scale.setScalar(1 + Math.sin(time * 0.9 + index) * 0.012);
    });

    if (aligning && this.shackleTarget === 0) this.shackleTarget = 1;

    // -- the shackle -------------------------------------------------
    this.shackle = damp(this.shackle, this.shackleTarget, 5, dt);
    const lift = easeOutBack(clamp(this.shackle, 0, 1));
    this.shackleMesh.position.y = 0.4 + lift * 0.42;
    this.shackleMesh.rotation.z = lift * 0.7;

    if (this.state === 'turning' && this.shackle > 0.9 && this._onOpen) {
      const resolve = this._onOpen;
      this._onOpen = null;
      this.state = 'open';
      resolve();
    }

    // -- rejection shake ---------------------------------------------
    if (this.shake > 0) {
      this.shake = Math.max(0, this.shake - dt * 2.2);
      const amount = this.shake * this.shake * 0.16;
      this.door.position.x = 0.6 + (Math.random() - 0.5) * amount * 4;
      this.door.position.y = 1.3 + (Math.random() - 0.5) * amount * 4;
    } else {
      this.door.position.x = damp(this.door.position.x, 0.6, 6, dt);
      this.door.position.y = damp(this.door.position.y, 1.3, 6, dt);
    }

    this.lock.rotation.y = Math.sin(time * 0.35) * 0.08;
  }
}
