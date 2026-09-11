/**
 * Shaders and reusable materials.
 *
 * The circuit-board floor, the glowing filaments and the scan rings are all
 * generated in GLSL rather than loaded as textures. That keeps the repository
 * small, lets every surface animate, and means the look survives on a machine
 * that never downloads an asset.
 */

import * as THREE from 'three';
import { PALETTE } from './palette.js';

const asColor = (hex) => new THREE.Color(hex);

/* ------------------------------------------------------------------ *
 * Circuit-board floor
 * ------------------------------------------------------------------ */

const PCB_VERTEX = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vWorld;
  void main() {
    vUv = uv;
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const PCB_FRAGMENT = /* glsl */ `
  precision highp float;
  uniform float uTime;
  uniform vec3  uTrace;
  uniform vec3  uAccent;
  uniform float uScale;
  uniform float uFade;
  varying vec2 vUv;
  varying vec3 vWorld;

  float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
  }

  // Distance to the nearest axis-aligned track in a cell, so traces meet at
  // right angles the way etched copper does rather than wandering.
  float traces(vec2 uv, float width) {
    vec2 cell = floor(uv);
    vec2 local = fract(uv) - 0.5;
    float r = hash(cell);

    float horizontal = abs(local.y + (r - 0.5) * 0.55);
    float vertical   = abs(local.x + (hash(cell + 7.3) - 0.5) * 0.55);

    float track = 1.0;
    if (r < 0.40)      track = horizontal;
    else if (r < 0.72) track = vertical;
    else               track = min(horizontal, vertical);   // a junction

    return 1.0 - smoothstep(0.0, width, track);
  }

  // Solder pads sit on a sparse subset of cell centres.
  float pads(vec2 uv) {
    vec2 cell = floor(uv);
    if (hash(cell + 19.1) > 0.13) return 0.0;
    float d = length(fract(uv) - 0.5);
    return (1.0 - smoothstep(0.06, 0.16, d)) * 0.9;
  }

  void main() {
    vec2 uv = vWorld.xz / uScale;

    float fine   = traces(uv * 2.0, 0.035) * 0.35;
    float coarse = traces(uv, 0.028) * 0.75;
    float pad    = pads(uv);

    // A charge travelling outward, so the board reads as powered.
    float radius = length(vWorld.xz);
    float pulse  = exp(-abs(fract(radius * 0.012 - uTime * 0.06) - 0.5) * 14.0);

    vec3 colour = uTrace * (coarse + fine);
    colour += uAccent * pad;
    colour += uAccent * pulse * (coarse + pad) * 1.6;

    float distance = length(vWorld.xz - cameraPosition.xz);
    float falloff  = 1.0 - smoothstep(uFade * 0.35, uFade, distance);

    float alpha = clamp((coarse + fine + pad + pulse * 0.4) * falloff, 0.0, 1.0);
    if (alpha < 0.004) discard;
    gl_FragColor = vec4(colour * falloff, alpha);
  }
`;

export function circuitFloor({ size = 320, scale = 7.0, trace = PALETTE.cyan, accent = PALETTE.gold, fade = 150 } = {}) {
  const material = new THREE.ShaderMaterial({
    uniforms: {
      uTime:   { value: 0 },
      uTrace:  { value: asColor(trace) },
      uAccent: { value: asColor(accent) },
      uScale:  { value: scale },
      uFade:   { value: fade },
    },
    vertexShader: PCB_VERTEX,
    fragmentShader: PCB_FRAGMENT,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
  });

  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(size, size, 1, 1), material);
  mesh.rotation.x = -Math.PI / 2;
  mesh.renderOrder = -1;
  mesh.userData.tick = (_dt, time) => { material.uniforms.uTime.value = time; };
  return mesh;
}

/* ------------------------------------------------------------------ *
 * Filaments — the links in the case web and the ledger
 * ------------------------------------------------------------------ */

const FILAMENT_VERTEX = /* glsl */ `
  attribute float aSpan;      // 0..1 along the filament
  attribute float aOffset;    // per-link phase, so pulses do not march in step
  varying float vSpan;
  varying float vOffset;
  void main() {
    vSpan = aSpan;
    vOffset = aOffset;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const FILAMENT_FRAGMENT = /* glsl */ `
  precision highp float;
  uniform float uTime;
  uniform vec3  uColour;
  uniform float uSpeed;
  uniform float uOpacity;
  varying float vSpan;
  varying float vOffset;

  void main() {
    float head  = fract(uTime * uSpeed + vOffset);
    float along = abs(vSpan - head);
    along = min(along, 1.0 - along);                 // wrap at the ends
    float pulse = exp(-along * 26.0);

    float body = 0.16 + 0.10 * sin((vSpan + vOffset) * 12.0);
    gl_FragColor = vec4(uColour * (body + pulse * 2.4), (body + pulse) * uOpacity);
  }
`;

export function filamentMaterial({ colour = PALETTE.cyan, speed = 0.22, opacity = 0.85 } = {}) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uTime:    { value: 0 },
      uColour:  { value: asColor(colour) },
      uSpeed:   { value: speed },
      uOpacity: { value: opacity },
    },
    vertexShader: FILAMENT_VERTEX,
    fragmentShader: FILAMENT_FRAGMENT,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
}

/* ------------------------------------------------------------------ *
 * Simple emissive helpers
 * ------------------------------------------------------------------ */

export function glow(colour, intensity = 1.4) {
  return new THREE.MeshStandardMaterial({
    color: asColor(colour),
    emissive: asColor(colour),
    emissiveIntensity: intensity,
    roughness: 0.32,
    metalness: 0.65,
  });
}

export function metal(colour = 0x1b2436, { roughness = 0.42, metalness = 0.9 } = {}) {
  return new THREE.MeshStandardMaterial({
    color: asColor(colour), roughness, metalness,
  });
}

export function wire(colour, opacity = 0.45) {
  return new THREE.LineBasicMaterial({
    color: asColor(colour), transparent: true, opacity, blending: THREE.AdditiveBlending,
  });
}

/**
 * A flat additive ring. Used for the HUD rings around the lock and the hash
 * progress ring in the intake scene.
 */
export function ring(colour, { inner = 1.0, outer = 1.06, segments = 128, opacity = 0.9 } = {}) {
  const geometry = new THREE.RingGeometry(inner, outer, segments);
  const material = new THREE.MeshBasicMaterial({
    color: asColor(colour),
    transparent: true,
    opacity,
    side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  return new THREE.Mesh(geometry, material);
}

/**
 * A point cloud drawn from a mesh's surface.
 *
 * The hooded figure in the login scene is rendered this way on purpose: a
 * solid model of a person reads as clip art, whereas the same silhouette made
 * of drifting points reads as data, which is what the figure is standing in
 * for.
 */
export function pointCloud(geometry, { colour = PALETTE.cyan, size = 0.035, opacity = 0.8 } = {}) {
  const material = new THREE.PointsMaterial({
    color: asColor(colour),
    size,
    transparent: true,
    opacity,
    sizeAttenuation: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  return new THREE.Points(geometry, material);
}

/** Drifting motes, so empty space never looks like a dead viewport. */
export function motes(count, radius, colour = PALETTE.cyan) {
  const positions = new Float32Array(count * 3);
  const drift = new Float32Array(count);
  for (let index = 0; index < count; index += 1) {
    positions[index * 3]     = (Math.random() - 0.5) * radius * 2;
    positions[index * 3 + 1] = Math.random() * radius * 0.7;
    positions[index * 3 + 2] = (Math.random() - 0.5) * radius * 2;
    drift[index] = Math.random();
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));

  const cloud = pointCloud(geometry, { colour, size: 0.09, opacity: 0.45 });
  cloud.userData.tick = (dt) => {
    const array = geometry.attributes.position.array;
    for (let index = 0; index < count; index += 1) {
      array[index * 3 + 1] += dt * (0.25 + drift[index] * 0.6);
      if (array[index * 3 + 1] > radius * 0.7) array[index * 3 + 1] = 0;
    }
    geometry.attributes.position.needsUpdate = true;
  };
  return cloud;
}

/** Walk a group and run every `userData.tick` it contains. */
export function tickAll(group, dt, time) {
  group.traverse((node) => node.userData?.tick?.(dt, time));
}
