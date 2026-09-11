/**
 * The Web — the case graph.
 *
 * Every document, photograph and evidence item hangs off its case on a glowing
 * filament, and the whole thing is run as a live force simulation so it drifts
 * and breathes rather than sitting still.
 *
 * "Chaotic but harmonious" is implemented literally. Three forces act at once:
 * nodes repel each other (the chaos), links pull like springs (the structure),
 * and a harmonic term pulls each node toward the radius of the shell its kind
 * belongs to — case at the centre, evidence around it, attachments outside
 * those, examiners on the rim. The result writhes but always resolves into
 * concentric order instead of collapsing into a knot or flying apart.
 */

import * as THREE from 'three';

import { GROUP_COLOUR, PALETTE } from '../palette.js';
import { filamentMaterial, glow, motes, tickAll } from '../materials.js';
import { hashSeed, seededRandom, truncate, TAU } from '../util.js';

/** The radius each kind of node settles onto. */
const SHELL = { case: 0, evidence: 7.5, attachment: 13.5, examiner: 19 };
const NODE_SIZE = { case: 1.3, evidence: 0.72, attachment: 0.44, examiner: 0.58 };

const MAX_NODES = 320;

export class WebScene {
  constructor() {
    this.pickable = [];
    this.nodes = [];
    this.links = [];
    this.byId = new Map();
    this.selected = null;
    this.caseId = null;
  }

  build(group, world) {
    this.world = world;
    this.root = group;

    this.field = new THREE.Group();
    this.field.position.set(0, 4, 0);
    group.add(this.field);

    group.add(motes(Math.round(300 * world.tier.particles), 40, PALETTE.violet));

    this.lineMaterial = filamentMaterial({ colour: PALETTE.cyan, speed: 0.2, opacity: 0.7 });
    this.lineGeometry = new THREE.BufferGeometry();
    this.lineMesh = new THREE.LineSegments(this.lineGeometry, this.lineMaterial);
    this.lineMesh.frustumCulled = false;
    this.field.add(this.lineMesh);

    this.title = world.label('<b>No case selected</b><span class="tag-k">choose one in the Vault</span>',
      { className: 'tag big' });
    this.title.position.set(0, SHELL.examiner + 7, 0);
    group.add(this.title);
  }

  clear() {
    for (const node of this.nodes) this.world.discard(node.group);
    this.nodes = [];
    this.links = [];
    this.byId.clear();
    this.pickable = [];
  }

  /** Lay out a case graph returned by /api/cases/<id>/graph. */
  setGraph(payload) {
    this.clear();
    this.lastPayload = payload;
    this.caseId = payload?.case?.id ?? null;

    const record = payload?.case ?? {};
    this.title.element.innerHTML = `
      <b>${record.case_number ?? '—'}</b>
      <span class="tag-k">${record.title ?? ''}</span>
      <span class="tag-m">${(payload?.nodes?.length ?? 0)} nodes · drag-free live layout</span>`;

    // Seeding from the case number means the same case always lays out the
    // same way. An examiner has to be able to say "the node on the left" twice.
    const random = seededRandom(hashSeed(String(record.case_number ?? 'keeneye')));

    const incoming = (payload?.nodes ?? []).slice(0, MAX_NODES);
    for (const definition of incoming) {
      const shell = SHELL[definition.group] ?? 10;
      const size = NODE_SIZE[definition.group] ?? 0.5;
      const colour = GROUP_COLOUR[definition.group] ?? PALETTE.bone;

      const holder = new THREE.Group();
      const theta = random() * TAU;
      const phi = Math.acos(2 * random() - 1);
      const jitter = shell === 0 ? 0 : shell * (0.85 + random() * 0.3);
      holder.position.set(
        Math.sin(phi) * Math.cos(theta) * jitter,
        Math.cos(phi) * jitter * 0.55,
        Math.sin(phi) * Math.sin(theta) * jitter,
      );
      holder.userData.pickId = definition.id;

      const detail = definition.group === 'case' ? 1 : 0;
      const core = new THREE.Mesh(
        new THREE.IcosahedronGeometry(size, detail),
        glow(colour, definition.group === 'case' ? 1.6 : 1.2),
      );
      holder.add(core);

      if (definition.group === 'case' || definition.group === 'evidence') {
        const shellMesh = new THREE.Mesh(
          new THREE.IcosahedronGeometry(size * 1.7, 0),
          new THREE.MeshBasicMaterial({ color: colour, wireframe: true, transparent: true, opacity: 0.25 }),
        );
        holder.add(shellMesh);
        holder.userData.shell = shellMesh;
      }

      const label = this.world.label(this._labelFor(definition), {
        className: `tag node ${definition.group}`,
      });
      label.position.set(0, size + 0.7, 0);
      // Attachments are the numerous ones and they crowd the centre: a DOM
      // label has no depth, so one sitting behind the case core still draws on
      // top of it. Their names appear on hover or selection instead — the panel
      // carries the detail either way.
      if (definition.group === 'attachment') label.visible = false;
      holder.add(label);

      this.field.add(holder);
      this.pickable.push(holder);

      const node = {
        id: definition.id,
        definition,
        label,
        group: holder,
        core,
        shell,
        size,
        colour,
        velocity: new THREE.Vector3(),
        phase: random() * TAU,
      };
      this.nodes.push(node);
      this.byId.set(definition.id, node);
    }

    for (const link of payload?.links ?? []) {
      const source = this.byId.get(link.source);
      const target = this.byId.get(link.target);
      if (source && target) this.links.push({ source, target, kind: link.kind });
    }

    this._buildLineBuffers();
  }

  /**
   * Labels are kept short on purpose.
   *
   * Every node used to carry its filename, caption, hash and size — four lines
   * each, which turns any real case into a wall of overlapping text around the
   * centre. The panel already shows all of that for whatever is selected, so
   * the field only needs enough to tell nodes apart.
   */
  _labelFor(definition) {
    if (definition.group === 'case') {
      return `<b>${definition.label}</b><span class="tag-k">${definition.detail ?? ''}</span>`;
    }
    if (definition.group === 'examiner') {
      return `<span class="tag-k">examiner</span><b>${definition.label}</b>`;
    }
    if (definition.group === 'evidence') {
      return `<b>${definition.label}</b><span class="tag-k">${definition.detail ?? ''}</span>`;
    }
    // Attachments are the most numerous and the least individually important.
    return `<span class="tag-k">${truncate(definition.label, 26)}</span>`;
  }

  /**
   * One LineSegments for every filament.
   *
   * Each link is subdivided so the travelling pulse in the shader has somewhere
   * to live; a two-vertex line would only be able to fade, not flow.
   */
  _buildLineBuffers() {
    const SEGMENTS = 10;
    const count = this.links.length * SEGMENTS * 2;

    const positions = new Float32Array(count * 3);
    const spans = new Float32Array(count);
    const offsets = new Float32Array(count);

    let cursor = 0;
    this.links.forEach((link, index) => {
      const offset = (index * 0.137) % 1;
      for (let segment = 0; segment < SEGMENTS; segment += 1) {
        for (const end of [segment / SEGMENTS, (segment + 1) / SEGMENTS]) {
          spans[cursor] = end;
          offsets[cursor] = offset;
          cursor += 1;
        }
      }
    });

    this.lineGeometry.dispose();
    this.lineGeometry = new THREE.BufferGeometry();
    this.lineGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    this.lineGeometry.setAttribute('aSpan', new THREE.BufferAttribute(spans, 1));
    this.lineGeometry.setAttribute('aOffset', new THREE.BufferAttribute(offsets, 1));
    this.lineMesh.geometry = this.lineGeometry;
    this._segments = SEGMENTS;
  }

  _updateLines() {
    if (!this.links.length) return;
    const positions = this.lineGeometry.attributes.position.array;
    const segments = this._segments;
    let cursor = 0;

    for (const link of this.links) {
      const a = link.source.group.position;
      const b = link.target.group.position;
      for (let segment = 0; segment < segments; segment += 1) {
        for (const t of [segment / segments, (segment + 1) / segments]) {
          // A slight sag, so filaments hang rather than reading as rulers.
          const sag = Math.sin(t * Math.PI) * 0.45;
          positions[cursor * 3]     = a.x + (b.x - a.x) * t;
          positions[cursor * 3 + 1] = a.y + (b.y - a.y) * t - sag;
          positions[cursor * 3 + 2] = a.z + (b.z - a.z) * t;
          cursor += 1;
        }
      }
    }
    this.lineGeometry.attributes.position.needsUpdate = true;
  }

  /** Reveal an attachment's name while the cursor is on it. */
  _updateHover() {
    const hit = this.world.pick();
    const id = hit?.id ?? null;
    if (id === this.hovered) return;
    this.hovered = id;

    for (const node of this.nodes) {
      if (node.definition.group !== 'attachment') continue;
      node.label.visible = node.id === id || node.id === this.selected;
    }
    this.world.labelsDirty = true;
  }

  select(id) {
    this.selected = id;
    for (const node of this.nodes) {
      const chosen = node.id === id;
      if (node.definition.group === 'attachment') node.label.visible = chosen;
      node.core.material.emissiveIntensity = chosen ? 3.2 : (node.definition.group === 'case' ? 1.6 : 1.2);
      node.group.userData.shell?.material.color.setHex(chosen ? PALETTE.bone : node.colour);
      node.group.children.forEach((child) => {
        if (child.element) child.element.classList.toggle('selected', chosen);
      });
    }
  }

  /**
   * One step of the force simulation.
   *
   * Deliberately simple and unconditionally stable: velocities are damped hard
   * every frame and displacement per step is clamped, so the layout can never
   * explode no matter how many nodes a case accumulates.
   */
  _simulate(dt, time) {
    const step = Math.min(dt, 1 / 30);

    for (let i = 0; i < this.nodes.length; i += 1) {
      const node = this.nodes[i];
      if (node.definition.group === 'case') continue;   // the case is the anchor

      const force = new THREE.Vector3();

      // 1. Repulsion — the chaos.
      for (let j = 0; j < this.nodes.length; j += 1) {
        if (i === j) continue;
        const other = this.nodes[j];
        const delta = new THREE.Vector3().subVectors(node.group.position, other.group.position);
        const distance = Math.max(delta.length(), 0.35);
        if (distance > 22) continue;
        force.addScaledVector(delta.normalize(), 12 / (distance * distance));
      }

      // 2. Harmonic shell — the order. A restoring force toward the radius
      //    this kind of node belongs on, not toward a fixed point, so nodes
      //    stay free to slide around their shell.
      const radius = node.group.position.length();
      const wanted = node.shell + Math.sin(time * 0.6 + node.phase) * 0.45;
      if (radius > 0.01) {
        force.addScaledVector(node.group.position.clone().normalize(), (wanted - radius) * 2.6);
      }

      // Flatten slightly toward the plane so the web reads as a web.
      force.y -= node.group.position.y * 0.85;

      node.velocity.addScaledVector(force, step);
      node.velocity.multiplyScalar(0.86);                  // damping
      node.velocity.clampLength(0, 9);
      node.group.position.addScaledVector(node.velocity, step);
    }

    // 3. Springs along the filaments — the structure.
    for (const link of this.links) {
      const delta = new THREE.Vector3().subVectors(link.target.group.position, link.source.group.position);
      const distance = Math.max(delta.length(), 0.001);
      const rest = link.kind === 'supports' ? 4.2 : 6.4;
      const pull = (distance - rest) * 0.55 * step;
      delta.normalize().multiplyScalar(pull);

      if (link.target.definition.group !== 'case') link.target.group.position.sub(delta);
      if (link.source.definition.group !== 'case') link.source.group.position.add(delta);
    }
  }

  update(dt, time, active) {
    tickAll(this.root, dt, time);
    this.lineMaterial.uniforms.uTime.value = time;

    if (!active && this.nodes.length > 60) return;    // idle regions stop simulating

    this._simulate(dt, time);
    this._updateLines();
    if (active) this._updateHover();

    this.field.rotation.y += dt * 0.035;

    for (const node of this.nodes) {
      node.core.rotation.y += dt * 0.5;
      node.group.userData.shell?.rotation.set(
        time * 0.2 + node.phase, time * 0.15, 0,
      );
      // Labels always face the viewer regardless of the field's rotation.
      node.group.rotation.y = -this.field.rotation.y;
    }
  }
}
