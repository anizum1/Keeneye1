/**
 * The Ledger — the custody log.
 *
 * Deliberately the calmest scene in the application. An earlier version drew
 * this as a swaying helix with a label on every block; it looked like more, and
 * communicated less — an examiner could not read a hash off it or tell at a
 * glance what had happened to a case.
 *
 * So the work is split. The 3D column shows the *shape* of the chain: one rung
 * per entry, evenly spaced, colour-coded by what kind of action it was, with a
 * verification pulse that travels down and stops dead at a fracture. The panel
 * beside it holds the *content* — the full log as readable, selectable text.
 * The colours are the same in both, so the two read as one thing.
 */

import * as THREE from 'three';

import { PALETTE } from '../palette.js';
import { filamentMaterial, glow, metal, motes, tickAll } from '../materials.js';
import { clamp, damp, shortHash } from '../util.js';

const RUNG_GAP = 1.05;        // vertical spacing between entries
const COLUMN_HEIGHT = 26;     // how tall the column is allowed to be on screen
const MAX_RUNGS = 160;
const MAX_LABELS = 12;        // 3D labels are markers, not the reading surface

/**
 * What kind of thing an entry is. Drives colour in both the column and the
 * panel, so "gold means something entered the vault" is learnable once.
 */
export const CATEGORIES = {
  intake:  { label: 'Evidence in',   colour: PALETTE.gold,
             actions: ['evidence_ingested', 'attachment_uploaded'] },
  check:   { label: 'Integrity check', colour: PALETTE.green,
             actions: ['evidence_verified'] },
  custody: { label: 'Custody',       colour: PALETTE.cyan,
             actions: ['custody_transferred', 'custody_accepted'] },
  access:  { label: 'Access',        colour: PALETTE.violet,
             actions: ['evidence_viewed', 'evidence_downloaded'] },
  admin:   { label: 'Case & people', colour: PALETTE.slate,
             actions: ['case_opened', 'case_closed', 'report_generated',
                       'user_created', 'user_login', 'user_deactivated', 'user_reactivated'] },
};

export function categoryOf(action) {
  for (const [name, definition] of Object.entries(CATEGORIES)) {
    if (definition.actions.includes(action)) return name;
  }
  return 'admin';
}

/** Colour for one entry, accounting for failures and breaks. */
export function colourFor(entry, broken) {
  if (broken) return PALETTE.crimson;
  if (entry.hash_check_result === 'fail') return PALETTE.crimson;
  return CATEGORIES[categoryOf(entry.action)].colour;
}

export class LedgerScene {
  constructor() {
    this.pickable = [];
    this.rungs = [];
    this.pulse = null;
    this.verification = null;
    this.selected = null;
  }

  build(group, world) {
    this.world = world;
    this.root = group;

    this.column = new THREE.Group();
    group.add(this.column);

    group.add(motes(Math.round(140 * world.tier.particles), 22, PALETTE.cyan));

    // The spine every rung hangs from — this is the chain itself.
    this.spine = new THREE.Mesh(
      new THREE.CylinderGeometry(0.05, 0.05, 1, 8),
      glow(PALETTE.green, 0.5),
    );
    this.column.add(this.spine);

    this.thread = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      filamentMaterial({ colour: PALETTE.green, speed: 0.1, opacity: 0.4 }),
    );
    this.thread.frustumCulled = false;
    this.column.add(this.thread);

    this.header = world.label(
      '<b>Custody ledger</b><span class="tag-k">every action, hash-linked</span>',
      { className: 'tag big' },
    );
    group.add(this.header);

    this.verdict = world.label('', { className: 'tag' });
    group.add(this.verdict);
  }

  clear() {
    for (const rung of this.rungs) this.world.discard(rung.group);
    this.rungs = [];
    this.pickable = [];
  }

  /**
   * Rebuild the column.
   *
   * Long logs are sampled, but the entries around a break are always kept: the
   * fracture is the one thing that must never be summarised away.
   */
  setEntries(entries, verification) {
    this.clear();
    this.verification = verification;
    this.entries = entries ?? [];

    const brokenSeq = verification?.first_broken_seq ?? null;
    let shown = this.entries;
    if (shown.length > MAX_RUNGS) {
      const stride = shown.length / MAX_RUNGS;
      const picked = new Map();
      for (let index = 0; index < MAX_RUNGS; index += 1) {
        const entry = shown[Math.min(shown.length - 1, Math.round(index * stride))];
        picked.set(entry.seq, entry);
      }
      for (const entry of this.entries) {
        if (brokenSeq !== null && Math.abs(entry.seq - brokenSeq) <= 1) picked.set(entry.seq, entry);
      }
      shown = [...picked.values()].sort((a, b) => a.seq - b.seq);
    }

    const span = Math.max((shown.length - 1) * RUNG_GAP, 0.001);
    const scale = Math.min(1, COLUMN_HEIGHT / span);
    const labelStep = Math.max(1, Math.ceil(shown.length / MAX_LABELS));

    this.column.scale.setScalar(scale);
    this.column.position.y = (span * scale) / 2;
    this.spine.scale.y = span + RUNG_GAP;
    this.spine.position.y = -span / 2;

    const top = (span * scale) / 2;
    this.header.position.set(0, top + 5.0, 0);
    this.verdict.position.set(0, top + 2.4, 0);

    shown.forEach((entry, index) => {
      const broken = brokenSeq !== null && entry.seq >= brokenSeq;
      const colour = colourFor(entry, broken);

      // A straight column, front-facing. No sway, no twist: every rung has to
      // be readable from the same viewpoint.
      const holder = new THREE.Group();
      holder.position.set(0, -index * RUNG_GAP, 0);
      holder.userData.pickId = `entry:${entry.seq}`;

      const bar = new THREE.Mesh(new THREE.BoxGeometry(3.2, 0.16, 0.16), glow(colour, broken ? 2.0 : 0.9));
      holder.add(bar);

      // A cap at each end, so a rung reads as a link rather than a tick.
      for (const side of [-1, 1]) {
        const cap = new THREE.Mesh(new THREE.SphereGeometry(0.13, 10, 8), glow(colour, broken ? 2.2 : 1.1));
        cap.position.x = side * 1.6;
        holder.add(cap);
      }

      this.column.add(holder);
      this.pickable.push(holder);
      this.rungs.push({ group: holder, bar, entry, colour, broken, index });

      if (index % labelStep === 0 || broken || index === shown.length - 1) {
        // Just the sequence number. The action, the actor and the hashes are
        // all in the panel; repeating them here only creates overlap, because
        // consecutive rungs are closer together than two lines of text.
        const label = this.world.label(
          `<span class="tag-s">#${entry.seq}</span>`,
          { className: `tag rung ${broken ? 'bad' : ''}` },
        );
        label.position.set(2.35, 0, 0);
        holder.add(label);
      }
    });

    this._updateThread(span);
    this._updateVerdict();
  }

  _updateThread(span) {
    const SEGMENTS = Math.max(this.rungs.length * 2, 2);
    const positions = new Float32Array(SEGMENTS * 2 * 3);
    const spans = new Float32Array(SEGMENTS * 2);
    const offsets = new Float32Array(SEGMENTS * 2);

    let cursor = 0;
    for (let index = 0; index < SEGMENTS; index += 1) {
      for (const t of [index / SEGMENTS, (index + 1) / SEGMENTS]) {
        positions[cursor * 3] = 0;
        positions[cursor * 3 + 1] = -t * span;
        positions[cursor * 3 + 2] = 0.1;
        spans[cursor] = t;
        offsets[cursor] = 0;
        cursor += 1;
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
      : `<b>BROKEN</b><span class="tag-k">first break at entry ${result?.first_broken_seq}</span>`;

    const colour = ok ? PALETTE.green : PALETTE.crimson;
    this.thread.material.uniforms.uColour.value.setHex(colour);
    this.spine.material.color.setHex(colour);
    this.spine.material.emissive.setHex(colour);
  }

  /** Highlight one entry — called when its row in the panel is clicked. */
  select(seq) {
    this.selected = seq;
    for (const rung of this.rungs) {
      const chosen = rung.entry.seq === seq;
      rung.group.scale.setScalar(chosen ? 1.18 : 1);
      rung.bar.material.emissiveIntensity = chosen ? 3.0 : (rung.broken ? 2.0 : 0.9);
      rung.group.children.forEach((child) => {
        if (child.element) child.element.classList.toggle('selected', chosen);
      });
    }
  }

  /** Fire the verification pulse from the genesis entry downward. */
  runVerification(verification) {
    this.verification = verification;
    const brokenIndex = verification?.first_broken_seq
      ? this.rungs.findIndex((rung) => rung.entry.seq >= verification.first_broken_seq)
      : -1;

    this.pulse = {
      position: -1,
      stopAt: brokenIndex >= 0 ? brokenIndex : this.rungs.length,
      done: false,
      hold: 0,
    };
    this._updateVerdict();
    for (const rung of this.rungs) rung.bar.material.emissiveIntensity = 0.35;
  }

  update(dt, time, active) {
    tickAll(this.root, dt, time);
    this.thread.material.uniforms.uTime.value = time;

    // A slow breath, and nothing more. The point of this scene is legibility.
    this.column.rotation.y = Math.sin(time * 0.15) * 0.05;

    for (const rung of this.rungs) {
      if (!rung.broken) continue;
      // A broken rung is visibly shaken loose from the spine.
      rung.group.position.x = Math.sin(time * 9 + rung.index) * 0.12;
      rung.bar.material.emissiveIntensity = 1.6 + Math.sin(time * 8) * 0.8;
    }

    const pulse = this.pulse;
    if (!pulse || pulse.done) return;

    pulse.position += dt * 26;
    const reached = Math.min(Math.floor(pulse.position), pulse.stopAt);

    for (let index = 0; index <= reached && index < this.rungs.length; index += 1) {
      const rung = this.rungs[index];
      const distance = Math.abs(pulse.position - index);
      rung.bar.material.emissiveIntensity = Math.max(0.9, 3.6 * Math.exp(-distance * 0.5));
    }

    if (pulse.position >= pulse.stopAt) {
      pulse.hold += dt;
      if (pulse.stopAt < this.rungs.length) {
        const rung = this.rungs[pulse.stopAt];
        rung.bar.material.emissiveIntensity = 2.2 + Math.sin(time * 18) * 1.6;
      }
      if (pulse.hold > 2.2) pulse.done = true;
    }
  }
}
