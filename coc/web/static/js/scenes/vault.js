/**
 * The Vault — the dashboard.
 *
 * Cases orbit as luminous cores above a circuit-board floor. Running through
 * the space is the chain spine: one linked ring per custody entry, green while
 * the log verifies and fractured red at the exact entry that broke. The whole
 * state of the workspace is meant to be readable from the far end of the room
 * before a single label is read.
 */

import * as THREE from 'three';

import { PALETTE } from '../palette.js';
import { circuitFloor, glow, metal, motes, ring, tickAll } from '../materials.js';
import { damp, humanBytes, shortHash, TAU } from '../util.js';

const MAX_SPINE_LINKS = 90;

export class VaultScene {
  constructor() {
    this.pickable = [];
    this.cases = [];
    this.links = [];
    this.chain = { ok: true, first_broken_seq: null, entries_checked: 0 };
    this.spinAngle = 0;
  }

  build(group, world) {
    this.world = world;
    this.root = group;

    const floor = circuitFloor({ size: 300, scale: 8, trace: PALETTE.cyan, accent: PALETTE.gold, fade: 130 });
    floor.position.y = -8;
    group.add(floor);
    group.add(motes(Math.round(420 * world.tier.particles), 60, PALETTE.cyan));

    this.orbit = new THREE.Group();
    this.orbit.position.set(0, 4, 0);
    group.add(this.orbit);

    // The chain spine runs along the floor beneath the cases.
    this.spine = new THREE.Group();
    this.spine.position.set(0, -7.5, -10);
    group.add(this.spine);

    this.spineLabel = world.label('<span class="tag-k">chain</span><b>—</b>', { className: 'tag' });
    this.spineLabel.position.set(0, 2.2, 0);
    this.spine.add(this.spineLabel);

    // A slow beacon at the centre so the region has a focal point when empty.
    const beacon = new THREE.Mesh(new THREE.IcosahedronGeometry(1.1, 1), glow(PALETTE.gold, 1.2));
    beacon.position.set(0, 4, 0);
    beacon.userData.tick = (dt, time) => {
      beacon.rotation.y += dt * 0.22;
      beacon.rotation.x = Math.sin(time * 0.3) * 0.2;
      beacon.scale.setScalar(1 + Math.sin(time * 1.4) * 0.04);
    };
    group.add(beacon);
    this.beacon = beacon;

    for (let index = 0; index < 3; index += 1) {
      const halo = ring(PALETTE.cyan, { inner: 2.4 + index * 1.1, outer: 2.44 + index * 1.1, opacity: 0.3 });
      halo.rotation.x = -Math.PI / 2;
      halo.position.set(0, 4 - index * 0.2, 0);
      halo.userData.tick = (dt) => { halo.rotation.z += dt * 0.1 * (index % 2 ? -1 : 1); };
      group.add(halo);
    }

    this.empty = world.label(
      '<b>No cases yet</b><span class="tag-k">open one from Intake to begin</span>',
      { className: 'tag big' },
    );
    this.empty.position.set(0, 8.5, 0);
    group.add(this.empty);
  }

  /** Rebuild the orbiting case cores from an overview payload. */
  setCases(cases) {
    for (const entry of this.cases) {
      this.orbit.remove(entry.group);
      entry.group.traverse((node) => {
        node.geometry?.dispose?.();
        if (node.material && node.material !== this.sharedMaterial) node.material.dispose?.();
      });
    }
    this.cases = [];
    this.pickable = [];
    this.empty.visible = cases.length === 0;

    const count = Math.max(cases.length, 1);
    cases.forEach((record, index) => {
      const angle = (index / count) * TAU;
      const radius = 9 + (index % 3) * 2.6;
      const height = ((index % 4) - 1.5) * 1.9;

      const holder = new THREE.Group();
      holder.position.set(Math.cos(angle) * radius, height, Math.sin(angle) * radius);
      holder.userData.pickId = `case:${record.id}`;

      const open = record.status === 'open';
      const colour = open ? PALETTE.cyan : PALETTE.slate;

      const core = new THREE.Mesh(new THREE.IcosahedronGeometry(0.95, 1), glow(colour, open ? 1.5 : 0.5));
      holder.add(core);

      const shell = new THREE.Mesh(
        new THREE.IcosahedronGeometry(1.5, 1),
        new THREE.MeshBasicMaterial({
          color: colour, wireframe: true, transparent: true, opacity: 0.28,
        }),
      );
      holder.add(shell);

      // Evidence count shown as a band of satellites rather than a number.
      const satellites = Math.min(record.evidence_count ?? 0, 12);
      for (let s = 0; s < satellites; s += 1) {
        const a = (s / Math.max(satellites, 1)) * TAU;
        const pip = new THREE.Mesh(new THREE.SphereGeometry(0.11, 10, 8), glow(PALETTE.gold, 1.6));
        pip.position.set(Math.cos(a) * 2.15, Math.sin(a * 2) * 0.3, Math.sin(a) * 2.15);
        holder.add(pip);
      }

      const label = this.world.label(
        `<b>${record.case_number}</b>
         <span class="tag-k">${record.title || ''}</span>
         <span class="tag-m">${record.evidence_count ?? 0} items · ${record.attachment_count ?? 0} files</span>`,
        { className: `tag case ${open ? '' : 'muted'}` },
      );
      label.position.set(0, 2.3, 0);
      holder.add(label);

      this.orbit.add(holder);
      this.cases.push({ group: holder, core, shell, record, angle, radius, height });
      this.pickable.push(holder);
    });
  }

  /**
   * Redraw the chain spine.
   *
   * Long chains are sampled rather than drawn link-for-link, but a broken
   * entry is always included in the sample — the fracture is the one thing
   * that must never be summarised away.
   */
  setChain(chain, stats) {
    this.chain = chain ?? this.chain;
    const total = Math.max(chain?.entries_checked ?? 0, 0);

    for (const link of this.links) {
      this.spine.remove(link.mesh);
      link.mesh.geometry.dispose();
      link.mesh.material.dispose();
    }
    this.links = [];

    const shown = Math.min(total, MAX_SPINE_LINKS);
    const step = total > MAX_SPINE_LINKS ? total / MAX_SPINE_LINKS : 1;
    const brokenSeq = chain?.first_broken_seq ?? null;

    for (let index = 0; index < shown; index += 1) {
      const seq = Math.round(index * step) + 1;
      const broken = brokenSeq !== null && seq >= brokenSeq;
      const colour = broken ? PALETTE.crimson : PALETTE.green;

      const mesh = new THREE.Mesh(
        new THREE.TorusGeometry(0.36, 0.07, 8, 20),
        glow(colour, broken ? 2.0 : 0.75),
      );
      mesh.position.set((index - shown / 2) * 0.54, 0, 0);
      mesh.rotation.y = index % 2 ? Math.PI / 2 : 0;
      mesh.userData.seq = seq;
      mesh.userData.broken = broken;

      // The link at the break is pulled apart, so the fracture is literal.
      if (brokenSeq !== null && seq === brokenSeq) {
        mesh.userData.fracture = true;
        mesh.scale.setScalar(1.5);
      }

      this.spine.add(mesh);
      this.links.push({ mesh, seq, broken });
    }

    const text = this.spineLabel.element;
    const ok = chain?.ok !== false;
    text.className = `label3d tag ${ok ? 'ok' : 'bad'}`;
    text.innerHTML = ok
      ? `<span class="tag-k">custody chain</span><b>INTACT</b>
         <span class="tag-m">${total.toLocaleString()} entries · head ${shortHash(chain?.head_hash, 6)}</span>`
      : `<span class="tag-k">custody chain</span><b>BROKEN</b>
         <span class="tag-m">first break at entry ${chain?.first_broken_seq}</span>`;

    if (stats) {
      this.beacon.material.color.setHex(ok ? PALETTE.gold : PALETTE.crimson);
      this.beacon.material.emissive.setHex(ok ? PALETTE.gold : PALETTE.crimson);
      this.vaultBytes = stats.vault_bytes ?? 0;
    }
  }

  summary(stats) {
    if (!stats) return '';
    return `${stats.evidence} items · ${humanBytes(stats.vault_bytes)}`;
  }

  update(dt, time, active) {
    tickAll(this.root, dt, time);

    this.spinAngle += dt * 0.045;
    this.orbit.rotation.y = this.spinAngle;

    for (const entry of this.cases) {
      entry.core.rotation.y += dt * 0.6;
      entry.shell.rotation.y -= dt * 0.3;
      entry.shell.rotation.x += dt * 0.12;
      entry.group.position.y = damp(
        entry.group.position.y,
        entry.height + Math.sin(time * 0.7 + entry.angle * 2) * 0.5,
        3, dt,
      );
      // Labels counter-rotate so they stay legible as the orbit turns.
      entry.group.rotation.y = -this.spinAngle;
    }

    for (const link of this.links) {
      const wobble = Math.sin(time * 1.6 + link.seq * 0.4);
      link.mesh.position.y = wobble * (link.broken ? 0.28 : 0.06);
      if (link.mesh.userData.fracture) {
        link.mesh.rotation.x += dt * 2.4;
        link.mesh.material.emissiveIntensity = 1.6 + Math.sin(time * 9) * 0.9;
      }
    }

    if (active) this.spine.rotation.y = Math.sin(time * 0.12) * 0.08;
  }
}
