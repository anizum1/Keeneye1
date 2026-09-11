/**
 * The Ledger — the custody log as a physical chain.
 *
 * Every entry is a block on a descending helix, carrying its timestamp and the
 * two hashes that bind it to its neighbours. Running a verification fires a
 * pulse of light down the chain from the genesis block: through an intact log
 * it travels the whole way and the head lights up, and at a broken link it
 * stops dead and the fracture opens.
 *
 * This is the scene that makes the tamper-evidence argument visible rather
 * than merely asserted.
 */

import * as THREE from 'three';

import { PALETTE } from '../palette.js';
import { filamentMaterial, glow, metal, motes, tickAll } from '../materials.js';
import { clamp, damp, formatStamp, shortHash, TAU } from '../util.js';

const RISE = 1.35;       // vertical spacing between blocks
const SWAY = 4.2;        // how far the chain wanders side to side
const DEPTH = 1.9;       // how far it wanders toward and away from the viewer
const TWIST = 0.55;      // radians of wander per block
const MAX_BLOCKS = 140;

/** How tall the helix is allowed to be on screen, in world units. */
const FRAME_HEIGHT = 25;

/** Labels are DOM and do not shrink with the helix, so they get thinned. */
const MAX_LABELS = 16;

export class LedgerScene {
  constructor() {
    this.pickable = [];
    this.blocks = [];
    this.pulse = null;
    this.verification = null;
  }

  build(group, world) {
    this.world = world;
    this.root = group;

    this.helix = new THREE.Group();
    group.add(this.helix);

    group.add(motes(Math.round(240 * world.tier.particles), 26, PALETTE.green));

    this.thread = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      filamentMaterial({ colour: PALETTE.green, speed: 0.14, opacity: 0.55 }),
    );
    this.thread.frustumCulled = false;
    this.helix.add(this.thread);

    this.header = world.label(
      '<b>Custody ledger</b><span class="tag-k">every action, hash-linked</span>',
      { className: 'tag big' },
    );
    this.header.position.set(0, 18, 0);
    group.add(this.header);

    this.verdict = world.label('', { className: 'tag' });
    this.verdict.position.set(0, 15, 0);
    group.add(this.verdict);
  }

  clear() {
    for (const block of this.blocks) {
      this.helix.remove(block.group);
      block.group.traverse((child) => {
        child.geometry?.dispose?.();
        child.material?.dispose?.();
      });
    }
    this.blocks = [];
    this.pickable = [];
  }

  /** Rebuild the helix from a custody log and its verification result. */
  setEntries(entries, verification) {
    this.clear();
    this.verification = verification;

    // Long logs are sampled, but a broken entry is never sampled away.
    const all = entries ?? [];
    const brokenSeq = verification?.first_broken_seq ?? null;
    let shown = all;
    if (all.length > MAX_BLOCKS) {
      const stride = all.length / MAX_BLOCKS;
      const picked = new Map();
      for (let index = 0; index < MAX_BLOCKS; index += 1) {
        const entry = all[Math.min(all.length - 1, Math.round(index * stride))];
        picked.set(entry.seq, entry);
      }
      for (const entry of all) {
        if (brokenSeq !== null && entry.seq >= brokenSeq - 1 && entry.seq <= brokenSeq + 1) {
          picked.set(entry.seq, entry);
        }
      }
      shown = [...picked.values()].sort((a, b) => a.seq - b.seq);
    }

    // Fit the helix to the frame rather than letting it run off into the fog,
    // and thin the labels: they are DOM elements that do not scale with the
    // geometry, so past a couple of dozen they stop being readable and start
    // being a smear. Anything broken keeps its label regardless.
    const span = Math.max((shown.length - 1) * RISE, 0.001);
    const scale = Math.min(1, FRAME_HEIGHT / span);
    const labelStep = Math.max(1, Math.ceil(shown.length / MAX_LABELS));
    this.helix.scale.setScalar(scale);
    this.helix.position.y = (span * scale) / 2;

    const top = (span * scale) / 2;
    this.header.position.set(0, top + 5.5, 0);
    this.verdict.position.set(0, top + 2.6, 0);

    shown.forEach((entry, index) => {
      const broken = brokenSeq !== null && entry.seq >= brokenSeq;
      const failed = entry.hash_check_result === 'fail';
      const colour = broken ? PALETTE.crimson : failed ? PALETTE.gold : PALETTE.green;

      // A descending chain that sways rather than a true helix. A helix looks
      // better in a still, but half its blocks end up edge-on to the camera and
      // an examiner cannot read a hash off a sliver. These stay face-on.
      const angle = index * TWIST;
      const holder = new THREE.Group();
      holder.position.set(Math.sin(angle) * SWAY, -index * RISE, Math.cos(angle * 0.7) * DEPTH);
      holder.rotation.y = Math.sin(angle) * 0.18;
      holder.userData.pickId = `entry:${entry.seq}`;

      const body = new THREE.Mesh(
        new THREE.BoxGeometry(3.0, 0.86, 0.32),
        metal(0x121a2c, { roughness: 0.4, metalness: 0.85 }),
      );
      holder.add(body);

      const edge = new THREE.Mesh(
        new THREE.BoxGeometry(3.1, 0.94, 0.26),
        new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity: 0.26 }),
      );
      edge.position.z = -0.06;
      holder.add(edge);

      // The link to the previous block.
      const shackle = new THREE.Mesh(
        new THREE.TorusGeometry(0.26, 0.055, 8, 20),
        glow(colour, broken ? 2.4 : 1.0),
      );
      shackle.position.set(0, 0.62, 0);
      shackle.rotation.x = Math.PI / 2;
      holder.add(shackle);

      const worthLabelling =
        broken || failed || index % labelStep === 0 || index === shown.length - 1;
      if (worthLabelling) {
        const label = this.world.label(
          `<span class="tag-s">#${entry.seq}</span>
           <b>${String(entry.action ?? '').replace(/_/g, ' ')}</b>
           <span class="tag-k">${entry.actor_username ?? ''} · ${formatStamp(entry.timestamp_utc)}</span>
           <code>${shortHash(entry.prev_hash, 5)} → ${shortHash(entry.entry_hash, 5)}</code>`,
          { className: `tag entry ${broken ? 'bad' : failed ? 'warn' : ''}` },
        );
        // Alternating sides: two labelled blocks in a row would otherwise
        // overlap wherever the chain sways back on itself.
        const side = (index / labelStep) % 2 === 0 ? 1 : -1;
        label.element.classList.add(side > 0 ? 'right' : 'left');
        label.position.set(side * 3.4, 0, 0.5);
        holder.add(label);
      }

      this.helix.add(holder);
      this.pickable.push(holder);
      this.blocks.push({ group: holder, entry, colour, broken, shackle, edge, index });
    });

    this._updateThread();
    this._updateVerdict();
  }

  _updateThread() {
    const SEGMENTS = 6;
    const pairs = Math.max(this.blocks.length - 1, 0);
    const count = pairs * SEGMENTS * 2;
    const positions = new Float32Array(count * 3);
    const spans = new Float32Array(count);
    const offsets = new Float32Array(count);

    let cursor = 0;
    for (let index = 0; index < pairs; index += 1) {
      const a = this.blocks[index].group.position;
      const b = this.blocks[index + 1].group.position;
      for (let segment = 0; segment < SEGMENTS; segment += 1) {
        for (const t of [segment / SEGMENTS, (segment + 1) / SEGMENTS]) {
          positions[cursor * 3]     = a.x + (b.x - a.x) * t;
          positions[cursor * 3 + 1] = a.y + (b.y - a.y) * t;
          positions[cursor * 3 + 2] = a.z + (b.z - a.z) * t;
          spans[cursor] = (index + t) / Math.max(pairs, 1);
          offsets[cursor] = 0;
          cursor += 1;
        }
      }
    }

    this.thread.geometry.dispose();
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('aSpan', new THREE.BufferAttribute(spans, 1));
    geometry.setAttribute('aOffset', new THREE.BufferAttribute(offsets, 1));
    this.thread.geometry = geometry;
  }

  _updateVerdict() {
    const result = this.verification;
    const ok = result?.ok !== false;
    this.verdict.element.className = `label3d tag ${ok ? 'ok' : 'bad'}`;
    this.verdict.element.innerHTML = ok
      ? `<b>INTACT</b><span class="tag-k">${(result?.entries_checked ?? 0).toLocaleString()} entries verified</span>
         <code>head ${shortHash(result?.head_hash, 8)}</code>`
      : `<b>BROKEN</b><span class="tag-k">${result?.reason ?? 'chain verification failed'}</span>`;
    this.thread.material.uniforms.uColour.value.setHex(ok ? PALETTE.green : PALETTE.crimson);
  }

  /**
   * Fire the verification pulse.
   *
   * The pulse travels at a fixed rate down the chain and halts at the first
   * broken link, which is the whole point: the examiner watches where it stops.
   */
  runVerification(verification) {
    this.verification = verification;
    const brokenIndex = verification?.first_broken_seq
      ? this.blocks.findIndex((block) => block.entry.seq >= verification.first_broken_seq)
      : -1;

    this.pulse = {
      position: -1,
      stopAt: brokenIndex >= 0 ? brokenIndex : this.blocks.length,
      done: false,
      hold: 0,
    };
    this._updateVerdict();

    for (const block of this.blocks) {
      block.lit = 0;
      block.shackle.material.emissiveIntensity = 0.4;
    }
  }

  update(dt, time, active) {
    tickAll(this.root, dt, time);
    this.thread.material.uniforms.uTime.value = time;
    this.helix.rotation.y = Math.sin(time * 0.08) * 0.12;

    for (const block of this.blocks) {
      block.group.position.x += Math.sin(time * 0.7 + block.index) * dt * 0.06;
      if (block.broken) {
        block.shackle.rotation.z += dt * 3;
        block.shackle.material.emissiveIntensity = 1.8 + Math.sin(time * 8) * 1.0;
        // The fracture: a broken link is visibly pulled off the thread.
        block.group.position.z += Math.sin(time * 5 + block.index) * dt * 0.3;
      }
    }

    const pulse = this.pulse;
    if (!pulse || pulse.done) return;

    pulse.position += dt * 22;
    const reached = Math.min(Math.floor(pulse.position), pulse.stopAt);

    for (let index = 0; index <= reached && index < this.blocks.length; index += 1) {
      const block = this.blocks[index];
      const distance = Math.abs(pulse.position - index);
      const brightness = Math.max(0.6, 3.4 * Math.exp(-distance * 0.55));
      block.shackle.material.emissiveIntensity = brightness;
      block.edge.material.opacity = clamp(0.22 + Math.exp(-distance * 0.5) * 0.6, 0.22, 0.9);
    }

    if (pulse.position >= pulse.stopAt) {
      pulse.hold += dt;
      if (pulse.stopAt < this.blocks.length) {
        // Stopped at a fracture: hammer on it so it cannot be missed.
        const block = this.blocks[pulse.stopAt];
        block.shackle.material.emissiveIntensity = 2 + Math.sin(time * 18) * 1.8;
        block.group.position.z += Math.sin(time * 22) * dt * 1.2;
      }
      if (pulse.hold > 2.4) pulse.done = true;
    }
  }
}
