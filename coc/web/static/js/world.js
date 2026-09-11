/**
 * The persistent world.
 *
 * There is one WebGL context and one THREE.Scene for the entire application.
 * The six views are not separate scenes that get swapped — they are regions
 * laid out at fixed positions in a single continuous space, and navigating
 * means physically flying the camera from one to the next. That is why the
 * browser never reloads: a page navigation would tear down the context and
 * with it the sense that the vault is a place rather than a set of screens.
 */

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { VignetteShader } from 'three/addons/shaders/VignetteShader.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

/**
 * Forces the final frame opaque.
 *
 * Additively-blended materials — the filaments, the motes, the HUD rings —
 * accumulate colour but leave the framebuffer's alpha channel near zero. The
 * browser composites a WebGL canvas by un-premultiplying, so those low alphas
 * turn into a pale wash over the whole page. The scene is never meant to be
 * see-through, so the last thing the composer does is pin alpha to 1.
 */
const OpaqueShader = {
  uniforms: { tDiffuse: { value: null } },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }`,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    varying vec2 vUv;
    void main() {
      gl_FragColor = vec4(texture2D(tDiffuse, vUv).rgb, 1.0);
    }`,
};
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

import { FOG, PALETTE } from './palette.js';
import { clamp, damp, easeInOutCubic } from './util.js';

/**
 * Where each region sits in world space, and where the camera parks to look at
 * it. Regions are far enough apart that fog hides the neighbours, close enough
 * that the flight between them reads as travel rather than a cut.
 */
export const STATIONS = {
  gate:   { camera: new THREE.Vector3(0, 1.6, 11),     target: new THREE.Vector3(0, 1.4, 0) },
  vault:  { camera: new THREE.Vector3(0, 14, -36),     target: new THREE.Vector3(0, 3, -66) },
  forge:  { camera: new THREE.Vector3(-139, 5.5, -46), target: new THREE.Vector3(-140, 2.5, -72) },
  web:    { camera: new THREE.Vector3(126, 10, -44),   target: new THREE.Vector3(148, 4, -66) },
  ledger: { camera: new THREE.Vector3(0, -54, -108),   target: new THREE.Vector3(0, -56, -142) },
  seal:   { camera: new THREE.Vector3(0, 50.5, -112),  target: new THREE.Vector3(0, 48, -128) },
};

export const REGION_ORIGIN = {
  gate:   new THREE.Vector3(0, 0, 0),
  vault:  new THREE.Vector3(0, 0, -66),
  forge:  new THREE.Vector3(-140, 0, -66),
  web:    new THREE.Vector3(148, 0, -66),
  ledger: new THREE.Vector3(0, -56, -142),
  seal:   new THREE.Vector3(0, 48, -128),
};

/**
 * Quality tiers. Picked once at boot from what the GPU reports, then adjusted
 * downward if frames come in slow. Nothing about the interface changes between
 * tiers except how expensive it is to draw.
 */
const TIERS = {
  high:     { dpr: 2.0, bloom: true, bloomStrength: 0.62, particles: 1.0, shadow: true },
  balanced: { dpr: 1.5, bloom: true, bloomStrength: 0.5, particles: 0.6, shadow: false },
  low:      { dpr: 1.0, bloom: false, bloomStrength: 0.0, particles: 0.3, shadow: false },
};

function detectTier(renderer) {
  // An explicit override, used for screenshots and for exercising the bloom
  // path under a software rasteriser where it would otherwise be skipped.
  const forced = new URLSearchParams(window.location.search).get('quality');
  if (forced && TIERS[forced]) return forced;

  const gl = renderer.getContext();
  const debug = gl.getExtension('WEBGL_debug_renderer_info');
  const name = debug ? String(gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) || '') : '';

  // Software rasterisers announce themselves. Bloom on llvmpipe is a slideshow.
  if (/swiftshader|llvmpipe|software|basic render/i.test(name)) return 'low';
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return 'low';
  if (renderer.capabilities.maxTextureSize < 4096) return 'low';
  if (window.devicePixelRatio > 1.5 && window.innerWidth > 1600) return 'high';
  return 'balanced';
}

export class World {
  constructor(canvas, labelHost) {
    this.canvas = canvas;
    this.clock = new THREE.Clock();
    this.regions = new Map();
    this.current = null;
    this.flight = null;
    this.running = false;
    this.elapsed = 0;
    this.frameTimes = [];
    this.pointer = new THREE.Vector2(-2, -2);
    this.labelsDirty = true;
    this.regionLayers = new Map();
    this.raycaster = new THREE.Raycaster();
    this.listeners = new Map();

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance',
      failIfMajorPerformanceCaveat: false,
    });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.setClearColor(PALETTE.void, 1);

    this.tierName = detectTier(this.renderer);
    this.tier = TIERS[this.tierName];
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, this.tier.dpr));

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(PALETTE.void);
    this.scene.fog = new THREE.Fog(FOG.color, FOG.near, FOG.far);

    this.camera = new THREE.PerspectiveCamera(52, 1, 0.1, 600);
    this.camera.position.copy(STATIONS.gate.camera);
    this.cameraTarget = STATIONS.gate.target.clone();
    this.desiredPosition = this.camera.position.clone();
    this.desiredTarget = this.cameraTarget.clone();

    this.labelRenderer = new CSS2DRenderer({ element: labelHost });
    this.labelRenderer.domElement.style.position = 'fixed';
    this.labelRenderer.domElement.style.top = '0';
    this.labelRenderer.domElement.style.left = '0';
    this.labelRenderer.domElement.style.pointerEvents = 'none';

    this._buildLighting();
    this._buildComposer();
    this._bindEvents();
    this.resize();
  }

  _buildLighting() {
    this.scene.add(new THREE.AmbientLight(0x223355, 0.55));

    const key = new THREE.DirectionalLight(PALETTE.cyan, 0.8);
    key.position.set(6, 18, 10);
    this.scene.add(key);

    const rim = new THREE.DirectionalLight(PALETTE.violet, 0.45);
    rim.position.set(-12, 6, -14);
    this.scene.add(rim);

    // Travels with the camera so the examiner always carries a little light.
    this.lamp = new THREE.PointLight(PALETTE.cyan, 18, 90, 2);
    this.scene.add(this.lamp);
  }

  _buildComposer() {
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));

    if (this.tier.bloom) {
      // Every reference image for this project lives on neon glow; bloom is
      // what turns emissive materials into that rather than flat bright paint.
      // Threshold matters more than strength here. Low thresholds make every
      // lit surface bloom, which turns the whole frame into a grey wash; at
      // 0.72 only genuinely emissive things glow and the blacks stay black.
      this.bloom = new UnrealBloomPass(
        new THREE.Vector2(window.innerWidth, window.innerHeight),
        this.tier.bloomStrength, 0.48, 0.72,
      );
      this.composer.addPass(this.bloom);
    }

    // darkness must not exceed 1.0. The shader mixes toward vec3(1.0 - darkness),
    // so anything above 1 drives the corners negative — harmless in a clamped
    // 8-bit buffer, but the composer's half-float target keeps the negative and
    // the tone-mapper in OutputPass turns it into a pale grey wash across the
    // whole frame. Depth of the vignette comes from `offset` instead.
    const vignette = new ShaderPass(VignetteShader);
    vignette.uniforms.offset.value = 1.25;
    vignette.uniforms.darkness.value = 1.0;
    this.composer.addPass(vignette);

    this.composer.addPass(new OutputPass());
    this.composer.addPass(new ShaderPass(OpaqueShader));
  }

  _bindEvents() {
    this._onResize = () => this.resize();
    window.addEventListener('resize', this._onResize, { passive: true });

    // Nothing should burn a GPU while the tab is in the background.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.pause();
      else this.start();
    });

    this.canvas.addEventListener('pointermove', (event) => {
      this.pointer.x = (event.clientX / window.innerWidth) * 2 - 1;
      this.pointer.y = -(event.clientY / window.innerHeight) * 2 + 1;
      this.parallax = {
        x: (event.clientX / window.innerWidth - 0.5) * 2,
        y: (event.clientY / window.innerHeight - 0.5) * 2,
      };
    }, { passive: true });

    this.canvas.addEventListener('pointerdown', (event) => {
      if (event.button === 0) this.emit('pick', this.pick());
    });
  }

  // -- region registry -------------------------------------------------

  /**
   * Register a region. Each one owns a Group positioned at its world origin
   * and is asked to update only while it is the destination or in view.
   */
  add(name, region) {
    const group = new THREE.Group();
    group.position.copy(REGION_ORIGIN[name] ?? new THREE.Vector3());
    group.visible = false;
    this.scene.add(group);

    region.name = name;
    region.group = group;
    region.world = this;
    // Layer 0 stays the default for meshes; each region's DOM labels get a
    // layer of their own so the camera can switch between them.
    this.regionLayers.set(name, this.regions.size + 1);
    this.regions.set(name, region);
    region.build?.(group, this);
    return region;
  }

  get(name) { return this.regions.get(name); }

  /** Fly the camera from wherever it is to a region's station. */
  flyTo(name, { duration = 1.9, immediate = false } = {}) {
    const station = STATIONS[name];
    if (!station) return Promise.resolve();

    const region = this.regions.get(name);
    const previous = this.current;

    if (region) region.group.visible = true;
    this.current = name;
    this.labelsDirty = true;
    this.emit('region', { name, previous });
    region?.enter?.();

    if (immediate) {
      this.camera.position.copy(station.camera);
      this.cameraTarget.copy(station.target);
      this.desiredPosition.copy(station.camera);
      this.desiredTarget.copy(station.target);
      this._hideDistantRegions();
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      this.flight = {
        fromPosition: this.camera.position.clone(),
        fromTarget: this.cameraTarget.clone(),
        toPosition: station.camera.clone(),
        toTarget: station.target.clone(),
        startedAt: performance.now(),
        duration,
        previous,
        resolve,
      };
    });
  }

  /** Keep only the active region (and, mid-flight, the one being left) drawn. */
  _hideDistantRegions() {
    for (const [name, region] of this.regions) {
      const keep = name === this.current || name === this.flight?.previous;
      if (region.group.visible !== keep) {
        region.group.visible = keep;
        if (!keep) region.exit?.();
      }
    }
    this.labelsDirty = true;
    this._applyCameraLayers();
  }

  /**
   * Confine each region's DOM labels to that region's layer.
   *
   * CSS2DRenderer tests an object's own `visible` flag and ignores its
   * ancestors', so a label inside a hidden group would otherwise keep drawing
   * over whatever the camera is actually looking at. Layers are the right lever
   * here rather than forcing `visible` off: the renderer already tests them,
   * and leaving `visible` alone means a scene that deliberately hid one of its
   * own labels keeps that decision.
   */
  _syncLabelLayers() {
    for (const [name, region] of this.regions) {
      const layer = this.regionLayers.get(name);
      region.group.traverse((node) => {
        if (node.isCSS2DObject) node.layers.set(layer);
      });
    }
    this.labelsDirty = false;
    this._applyCameraLayers();
  }

  /** Show only the labels of the region in view (plus the one being left). */
  _applyCameraLayers() {
    this.camera.layers.set(0);
    for (const name of [this.current, this.flight?.previous]) {
      const layer = name && this.regionLayers.get(name);
      if (layer) this.camera.layers.enable(layer);
    }
  }

  /**
   * Remove an object from the scene and release everything it holds.
   *
   * The subtle part is the DOM labels. CSS2DRenderer appends each
   * CSS2DObject's element to the overlay and only takes it back out in
   * response to that object's own `removed` event — which fires when the label
   * itself is detached from its parent, NOT when some ancestor group is. So
   * removing a node group leaves its label elements in the overlay forever:
   * they pile up on every rebuild and, because the overlay is fixed over the
   * whole viewport, they bleed across every region.
   *
   * Always route disposal through here rather than calling parent.remove().
   */
  discard(object) {
    if (!object) return;

    const labels = [];
    object.traverse((node) => {
      if (node.isCSS2DObject) labels.push(node);
    });
    for (const label of labels) {
      label.removeFromParent();   // fires 'removed' → the renderer drops the element
      label.element?.remove();    // and belt-and-braces, in case it was already detached
    }

    object.traverse((node) => {
      node.geometry?.dispose?.();
      const material = node.material;
      if (Array.isArray(material)) material.forEach((entry) => entry?.dispose?.());
      else material?.dispose?.();
    });

    object.removeFromParent();
    this.labelsDirty = true;
  }

  /** How many label elements are currently in the overlay. Used by tests. */
  get labelCount() {
    return this.labelRenderer.domElement.childElementCount;
  }

  // -- interaction -----------------------------------------------------

  /** What is under the cursor in the active region, if anything. */
  pick() {
    const region = this.regions.get(this.current);
    if (!region?.pickable?.length) return null;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects(region.pickable, true);
    for (const hit of hits) {
      let node = hit.object;
      while (node && !node.userData?.pickId) node = node.parent;
      if (node?.userData?.pickId) return { id: node.userData.pickId, object: node, point: hit.point };
    }
    return null;
  }

  on(event, handler) {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event).add(handler);
    return () => this.listeners.get(event)?.delete(handler);
  }

  emit(event, payload) {
    for (const handler of this.listeners.get(event) ?? []) handler(payload);
  }

  // -- labels ----------------------------------------------------------

  /**
   * A DOM label tracked to a 3D position.
   *
   * Text is never drawn into the WebGL canvas. Hashes have to stay selectable
   * and the interface has to remain readable by a screen reader, and rendering
   * glyphs as geometry throws both of those away for no gain.
   */
  label(html, { className = 'tag', position = null, centre = true } = {}) {
    const element = document.createElement('div');
    element.className = `label3d ${className}`;
    element.innerHTML = html;
    const object = new CSS2DObject(element);
    object.center.set(centre ? 0.5 : 0, centre ? 0.5 : 0);
    if (position) object.position.copy(position);
    // A scene may build labels while its region is off screen; sync on the
    // next frame so they are never briefly drawn over another region.
    this.labelsDirty = true;
    return object;
  }

  // -- frame loop ------------------------------------------------------

  resize() {
    const width = window.innerWidth;
    const height = window.innerHeight;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.composer.setSize(width, height);
    this.labelRenderer.setSize(width, height);
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.clock.getDelta();
    this._loop();
  }

  pause() {
    this.running = false;
    if (this._frame) cancelAnimationFrame(this._frame);
  }

  _loop = () => {
    if (!this.running) return;
    this._frame = requestAnimationFrame(this._loop);

    const dt = Math.min(this.clock.getDelta(), 0.05);
    this.elapsed += dt;

    this._advanceFlight(dt);
    this._updateCamera(dt);

    for (const [name, region] of this.regions) {
      if (region.group.visible) region.update?.(dt, this.elapsed, name === this.current);
    }

    if (this.labelsDirty) this._syncLabelLayers();

    this.composer.render(dt);
    this.labelRenderer.render(this.scene, this.camera);
    this._sampleFrameTime(dt);
  };

  _advanceFlight(dt) {
    if (!this.flight) return;

    // Wall-clock, not accumulated frame deltas. Per-frame dt is clamped to
    // 0.05s so a stalled tab cannot teleport the simulation, but that clamp
    // would also stretch a 1.9s flight into ten seconds on a slow renderer —
    // and until a flight ends, the region being left stays drawn on top of the
    // one being flown to. Navigation has to finish on time regardless of how
    // fast the machine can draw.
    const t = clamp(
      (performance.now() - this.flight.startedAt) / (this.flight.duration * 1000),
      0, 1,
    );
    const eased = easeInOutCubic(t);

    this.desiredPosition.lerpVectors(this.flight.fromPosition, this.flight.toPosition, eased);
    this.desiredTarget.lerpVectors(this.flight.fromTarget, this.flight.toTarget, eased);

    // A gentle arc so long hops do not read as a dolly down a rail.
    this.desiredPosition.y += Math.sin(eased * Math.PI) * 6;

    if (t >= 1) {
      const { resolve } = this.flight;
      this.flight = null;
      this._hideDistantRegions();
      resolve?.();
    }
  }

  _updateCamera(dt) {
    if (!this.flight) {
      const station = STATIONS[this.current] ?? STATIONS.gate;
      this.desiredPosition.copy(station.camera);
      this.desiredTarget.copy(station.target);

      // A little head movement so a still scene is never actually still.
      if (this.parallax) {
        this.desiredPosition.x += this.parallax.x * 1.6;
        this.desiredPosition.y += -this.parallax.y * 0.9;
      }
      this.desiredPosition.y += Math.sin(this.elapsed * 0.42) * 0.22;
    }

    this.camera.position.x = damp(this.camera.position.x, this.desiredPosition.x, 6, dt);
    this.camera.position.y = damp(this.camera.position.y, this.desiredPosition.y, 6, dt);
    this.camera.position.z = damp(this.camera.position.z, this.desiredPosition.z, 6, dt);

    this.cameraTarget.x = damp(this.cameraTarget.x, this.desiredTarget.x, 6, dt);
    this.cameraTarget.y = damp(this.cameraTarget.y, this.desiredTarget.y, 6, dt);
    this.cameraTarget.z = damp(this.cameraTarget.z, this.desiredTarget.z, 6, dt);

    this.camera.lookAt(this.cameraTarget);
    this.lamp.position.copy(this.camera.position);
  }

  /** Drop a quality tier if the first seconds of rendering come in slow. */
  _sampleFrameTime(dt) {
    if (this.tierName === 'low' || this.frameTimes.length > 90) return;
    this.frameTimes.push(dt);
    if (this.frameTimes.length !== 90) return;

    const sorted = [...this.frameTimes].sort((a, b) => a - b);
    const median = sorted[45];
    if (median > 1 / 30) this.downgrade();
  }

  downgrade() {
    this.tierName = this.tierName === 'high' ? 'balanced' : 'low';
    this.tier = TIERS[this.tierName];
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, this.tier.dpr));
    if (this.bloom) this.bloom.strength = this.tier.bloomStrength;
    this.emit('tier', this.tierName);
  }

  dispose() {
    this.pause();
    window.removeEventListener('resize', this._onResize);
    this.renderer.dispose();
  }
}

export { THREE };
