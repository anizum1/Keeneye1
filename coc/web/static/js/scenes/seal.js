/**
 * The Seal — report generation.
 *
 * Pages fly together into a stack and a sigil presses down over them like a
 * wax seal, its face cut from the chain's head hash. The animation runs while
 * the PDF is actually being generated server-side, and the download starts
 * when the seal lands.
 *
 * The report itself is deliberately plain. The spectacle belongs to the
 * interface; the document that leaves the building is an exhibit.
 */

import * as THREE from 'three';

import { PALETTE } from '../palette.js';
import { glow, metal, motes, ring, tickAll } from '../materials.js';
import { clamp, damp, digestToUnits, easeInOutCubic, shortHash, TAU } from '../util.js';

const PAGE_COUNT = 14;

export class SealScene {
  constructor() {
    this.pickable = [];
    this.pages = [];
    this.state = 'idle';
    this.press = 0;
  }

  build(group, world) {
    this.world = world;
    this.root = group;

    group.add(motes(Math.round(200 * world.tier.particles), 22, PALETTE.gold));

    this.stack = new THREE.Group();
    group.add(this.stack);

    const geometry = new THREE.BoxGeometry(3.4, 0.03, 4.4);
    for (let index = 0; index < PAGE_COUNT; index += 1) {
      const page = new THREE.Mesh(
        geometry,
        new THREE.MeshStandardMaterial({
          color: 0xdfe7f5, roughness: 0.9, metalness: 0.0,
          transparent: true, opacity: 0.85,
        }),
      );
      page.userData.home = new THREE.Vector3(0, index * 0.045 - 0.3, 0);
      page.userData.scatter = new THREE.Vector3(
        (Math.random() - 0.5) * 22,
        (Math.random() - 0.5) * 14 + 2,
        (Math.random() - 0.5) * 22,
      );
      page.userData.spin = new THREE.Vector3(Math.random(), Math.random(), Math.random())
        .multiplyScalar(Math.random() * 2 + 0.5);
      page.position.copy(page.userData.scatter);
      this.stack.add(page);
      this.pages.push(page);
    }

    // The sigil: a disc whose rim is cut from the chain head hash.
    this.sigil = new THREE.Group();
    this.sigil.position.set(0, 6, 0);
    group.add(this.sigil);

    const disc = new THREE.Mesh(new THREE.CylinderGeometry(1.2, 1.35, 0.34, 40), glow(PALETTE.gold, 1.1));
    this.sigil.add(disc);
    this.disc = disc;

    this.teeth = new THREE.Group();
    this.sigil.add(this.teeth);
    this._cutSigil('0'.repeat(64));

    const halo = ring(PALETTE.gold, { inner: 1.5, outer: 1.56, opacity: 0.6 });
    halo.rotation.x = -Math.PI / 2;
    halo.userData.tick = (dt) => { halo.rotation.z += dt * 0.5; };
    this.sigil.add(halo);

    this.caption = world.label(
      '<b>Court report</b><span class="tag-k">select a case, then seal it</span>',
      { className: 'tag big' },
    );
    this.caption.position.set(0, 9, 0);
    group.add(this.caption);
  }

  /** Cut the sigil's rim from a digest, so the seal encodes the chain head. */
  _cutSigil(digest) {
    for (const child of [...this.teeth.children]) {
      this.teeth.remove(child);
      child.geometry.dispose();
      child.material.dispose();
    }
    const units = digestToUnits(digest, 24);
    units.forEach((unit, index) => {
      const angle = (index / units.length) * TAU;
      const height = 0.1 + unit * 0.42;
      const tooth = new THREE.Mesh(new THREE.BoxGeometry(0.12, height, 0.14), glow(PALETTE.gold, 1.6));
      tooth.position.set(Math.cos(angle) * 1.12, -0.1 + height / 2, Math.sin(angle) * 1.12);
      tooth.rotation.y = -angle;
      this.teeth.add(tooth);
    });
  }

  /** Begin sealing. Resolves when the press lands. */
  seal(caseRecord, chain) {
    this._cutSigil(chain?.head_hash ?? '0'.repeat(64));
    this.caption.element.innerHTML = `
      <b>${caseRecord?.case_number ?? 'case'}</b>
      <span class="tag-k">assembling report…</span>
      <code>head ${shortHash(chain?.head_hash, 8)}</code>`;

    this.state = 'gather';
    this.gather = 0;
    this.press = 0;
    return new Promise((resolve) => { this._onSealed = resolve; });
  }

  finish(message) {
    this.caption.element.innerHTML = message;
    this.state = 'idle';
  }

  update(dt, time, active) {
    tickAll(this.root, dt, time);
    this.sigil.rotation.y += dt * (this.state === 'press' ? 2.4 : 0.35);

    if (this.state === 'idle') {
      for (const page of this.pages) {
        page.position.lerp(page.userData.scatter, 1 - Math.exp(-1.2 * dt));
        page.rotation.x += dt * page.userData.spin.x * 0.2;
        page.rotation.z += dt * page.userData.spin.z * 0.2;
      }
      this.sigil.position.y = damp(this.sigil.position.y, 6, 3, dt);
      return;
    }

    if (this.state === 'gather') {
      this.gather = Math.min(1, this.gather + dt * 0.7);
      const eased = easeInOutCubic(this.gather);
      this.pages.forEach((page, index) => {
        const delay = clamp((eased - index / (PAGE_COUNT * 2)) * 1.6, 0, 1);
        page.position.lerpVectors(page.userData.scatter, page.userData.home, delay);
        page.rotation.x = (1 - delay) * page.userData.spin.x * 3;
        page.rotation.y = (1 - delay) * page.userData.spin.y * 3;
        page.rotation.z = (1 - delay) * page.userData.spin.z * 3;
      });
      if (this.gather >= 1) this.state = 'press';
      return;
    }

    if (this.state === 'press') {
      this.press = Math.min(1, this.press + dt * 1.4);
      this.sigil.position.y = 6 - easeInOutCubic(this.press) * 5.3;
      this.disc.material.emissiveIntensity = 1.1 + this.press * 2.4;

      if (this.press >= 1) {
        this.state = 'sealed';
        const resolve = this._onSealed;
        this._onSealed = null;
        resolve?.();
      }
      return;
    }

    if (this.state === 'sealed') {
      this.sigil.position.y = 0.7 + Math.sin(time * 1.6) * 0.06;
      this.disc.material.emissiveIntensity = damp(this.disc.material.emissiveIntensity, 1.6, 2, dt);
    }
  }
}
