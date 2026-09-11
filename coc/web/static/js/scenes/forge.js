/**
 * The Forge — evidence intake and the locking mechanism.
 *
 * A file dropped onto the canvas materialises as a slab. A ring wraps it while
 * it is hashed; that ring is driven by real upload progress, not a timer
 * pretending to be one. When the digest comes back the hash is stamped onto
 * the slab's face, the slab flies into a free cell in the vault lattice, the
 * cage closes over it and the lock seats. After that the slab is read-only in
 * the interface exactly as it is read-only on disk.
 */

import * as THREE from 'three';

import { PALETTE } from '../palette.js';
import { circuitFloor, glow, metal, motes, ring, tickAll } from '../materials.js';
import { clamp, damp, digestToUnits, easeInOutCubic, humanBytes, shortHash, TAU } from '../util.js';

const COLUMNS = 8;
const ROWS = 4;
const CELL = 2.1;

export class ForgeScene {
  constructor() {
    this.pickable = [];
    this.cells = [];
    this.slabs = [];
    this.pending = null;
  }

  build(group, world) {
    this.world = world;
    this.root = group;

    const floor = circuitFloor({ size: 200, scale: 6, trace: PALETTE.gold, accent: PALETTE.cyan, fade: 90 });
    floor.position.y = -6;
    group.add(floor);
    group.add(motes(Math.round(260 * world.tier.particles), 34, PALETTE.gold));

    // -- the lattice wall --------------------------------------------
    const wall = new THREE.Group();
    wall.position.set(0, 3.4, -12);
    group.add(wall);
    this.wall = wall;

    for (let row = 0; row < ROWS; row += 1) {
      for (let column = 0; column < COLUMNS; column += 1) {
        const cell = new THREE.Group();
        cell.position.set(
          (column - (COLUMNS - 1) / 2) * CELL,
          ((ROWS - 1) / 2 - row) * CELL,
          0,
        );

        // The cell recess.
        const recess = new THREE.Mesh(
          new THREE.BoxGeometry(CELL * 0.88, CELL * 0.88, 0.9),
          metal(0x0d1424, { roughness: 0.8, metalness: 0.3 }),
        );
        recess.position.z = -0.5;
        cell.add(recess);

        const frame = new THREE.Mesh(
          new THREE.TorusGeometry(CELL * 0.5, 0.035, 6, 4),
          new THREE.MeshBasicMaterial({ color: PALETTE.slate, transparent: true, opacity: 0.5 }),
        );
        frame.rotation.z = Math.PI / 4;
        cell.add(frame);

        // The cage that closes once a slab is seated.
        const cage = new THREE.Group();
        for (let bar = 0; bar < 4; bar += 1) {
          const rod = new THREE.Mesh(
            new THREE.CylinderGeometry(0.028, 0.028, CELL * 0.86, 6),
            glow(PALETTE.gold, 0.8),
          );
          rod.position.x = -CELL * 0.33 + bar * (CELL * 0.22);
          cage.add(rod);
        }
        cage.visible = false;
        cage.scale.y = 0;
        cell.add(cage);

        wall.add(cell);
        this.cells.push({ group: cell, cage, occupied: false, close: 0 });
      }
    }

    // -- the intake pedestal -----------------------------------------
    const pedestal = new THREE.Group();
    pedestal.position.set(0, -1.6, 2.2);
    group.add(pedestal);
    this.pedestal = pedestal;

    const plate = new THREE.Mesh(
      new THREE.CylinderGeometry(2.4, 2.7, 0.24, 48),
      metal(0x121a2c, { roughness: 0.5, metalness: 0.85 }),
    );
    pedestal.add(plate);

    const halo = ring(PALETTE.cyan, { inner: 2.5, outer: 2.56, opacity: 0.5 });
    halo.rotation.x = -Math.PI / 2;
    halo.position.y = 0.14;
    halo.userData.tick = (dt) => { halo.rotation.z += dt * 0.4; };
    pedestal.add(halo);
    this.halo = halo;

    // The hash ring: fills as bytes are actually transferred and hashed.
    const progress = ring(PALETTE.gold, { inner: 1.9, outer: 2.12, segments: 180, opacity: 0.95 });
    progress.rotation.x = -Math.PI / 2;
    progress.position.y = 0.2;
    progress.visible = false;
    pedestal.add(progress);
    this.progressRing = progress;

    this.prompt = world.label(
      '<b>Drop a file to admit it</b><span class="tag-k">it is hashed on arrival, then sealed</span>',
      { className: 'tag big' },
    );
    this.prompt.position.set(0, 2.6, 0);
    pedestal.add(this.prompt);

    this.readout = world.label('', { className: 'tag mono' });
    this.readout.position.set(0, 1.1, 0);
    this.readout.visible = false;
    pedestal.add(this.readout);
  }

  _freeCell() {
    return this.cells.find((cell) => !cell.occupied) ?? null;
  }

  /** Build a slab whose face carries a hash-derived relief. */
  _slabMesh(digest, kind = 'evidence') {
    const colour = kind === 'evidence' ? PALETTE.gold : PALETTE.violet;
    const slab = new THREE.Group();

    const body = new THREE.Mesh(
      new THREE.BoxGeometry(1.5, 1.05, 0.18),
      metal(0x151d30, { roughness: 0.35, metalness: 0.9 }),
    );
    slab.add(body);

    const face = new THREE.Mesh(
      new THREE.PlaneGeometry(1.42, 0.97),
      new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity: 0.16 }),
    );
    face.position.z = 0.095;
    slab.add(face);

    // The relief is cut from the digest: same idea as the key's teeth, so a
    // sealed item carries its hash in its shape rather than only in a caption.
    const units = digestToUnits(digest, 16);
    units.forEach((unit, index) => {
      const bar = new THREE.Mesh(
        new THREE.BoxGeometry(0.06, 0.1 + unit * 0.62, 0.03),
        glow(colour, 1.5),
      );
      bar.position.set(-0.66 + index * 0.088, -0.36 + (0.1 + unit * 0.62) / 2, 0.1);
      slab.add(bar);
    });

    return slab;
  }

  /**
   * Begin an intake. Returns a handle the uploader drives.
   *
   * Splitting this from the network call keeps the animation honest: the ring
   * only advances when `progress` is called with real numbers.
   */
  begin(file, kind = 'evidence') {
    this.prompt.visible = false;
    this.readout.visible = true;

    const slab = this._slabMesh('0'.repeat(64), kind);
    slab.position.set(0, 0.9, 0);
    slab.scale.setScalar(0.01);
    this.pedestal.add(slab);

    this.progressRing.visible = true;
    this.progressRing.geometry.dispose();
    this.progressRing.geometry = new THREE.RingGeometry(1.9, 2.12, 180, 1, 0, 0.001);

    this.pending = {
      slab, kind, file,
      fraction: 0,
      shown: 0,
      appear: 0,
      state: 'hashing',
    };

    this._setReadout(`
      <span class="tag-k">${kind === 'evidence' ? 'admitting evidence' : 'attaching file'}</span>
      <b>${file.name}</b>
      <span class="tag-m">${humanBytes(file.size)} · computing SHA-256…</span>`);

    return {
      progress: (fraction) => {
        if (this.pending) this.pending.fraction = clamp(fraction, 0, 1);
      },
      complete: (record) => this._seal(record),
      fail: (message) => this._fail(message),
    };
  }

  _setReadout(html) {
    this.readout.element.innerHTML = html;
  }

  /** The moment the server confirms the digest: stamp, fly, cage, lock. */
  _seal(record) {
    if (!this.pending) return;
    const digest = String(record?.sha256 ?? '');
    const cell = this._freeCell();

    // Recut the slab's relief from the digest the server actually recorded.
    const replacement = this._slabMesh(digest, this.pending.kind);
    replacement.position.copy(this.pending.slab.position);
    replacement.scale.copy(this.pending.slab.scale);
    this.pedestal.remove(this.pending.slab);
    this.pedestal.add(replacement);
    this.pending.slab = replacement;

    this._setReadout(`
      <span class="tag-k ok">sealed</span>
      <b>${record?.item_number ?? record?.original_filename ?? 'stored'}</b>
      <code>${digest}</code>`);

    if (!cell) {
      // The wall is a fixed size; the vault is not. Fade the slab out rather
      // than pretend there is nowhere for the file to go.
      this.pending.state = 'dissolve';
      return;
    }

    cell.occupied = true;
    const destination = new THREE.Vector3();
    cell.group.getWorldPosition(destination);
    this.root.worldToLocal(destination);

    const start = new THREE.Vector3();
    this.pending.slab.getWorldPosition(start);
    this.root.worldToLocal(start);

    // Re-parent to the region root so the flight path is a straight line in
    // world space rather than a curve through the pedestal's transform.
    this.pedestal.remove(this.pending.slab);
    this.root.add(this.pending.slab);
    this.pending.slab.position.copy(start);

    Object.assign(this.pending, {
      state: 'flying',
      travel: 0,
      from: start,
      to: destination,
      cell,
      digest,
      record,
    });
  }

  _fail(message) {
    if (!this.pending) return;
    this.pending.state = 'reject';
    this._setReadout(`<span class="tag-k bad">refused</span><b>${message}</b>`);
    this.progressRing.material.color.setHex(PALETTE.crimson);
  }

  _finish() {
    this.pending = null;
    this.progressRing.visible = false;
    this.progressRing.material.color.setHex(PALETTE.gold);
    this.prompt.visible = true;
    setTimeout(() => { if (!this.pending) this.readout.visible = false; }, 4200);
  }

  update(dt, time, active) {
    tickAll(this.root, dt, time);
    this.wall.rotation.y = Math.sin(time * 0.18) * 0.04;

    // Cages close over seated slabs.
    for (const cell of this.cells) {
      const target = cell.occupied ? 1 : 0;
      if (cell.close !== target || cell.cage.scale.y !== target) {
        cell.close = damp(cell.close, target, 4, dt);
        cell.cage.visible = cell.close > 0.01;
        cell.cage.scale.y = cell.close;
      }
    }

    const job = this.pending;
    if (!job) return;

    // The ring only ever shows progress it was actually told about.
    job.shown = damp(job.shown, job.fraction, 7, dt);
    const sweep = Math.max(job.shown * TAU, 0.001);
    job.slab.rotation.y += dt * (job.state === 'hashing' ? 1.6 : 0.4);

    if (job.state === 'hashing' || job.state === 'reject') {
      this.progressRing.geometry.dispose();
      this.progressRing.geometry = new THREE.RingGeometry(1.9, 2.12, 180, 1, Math.PI / 2, -sweep);
      job.appear = damp(job.appear, 1, 5, dt);
      job.slab.scale.setScalar(job.appear);
      job.slab.position.y = 0.9 + Math.sin(time * 2) * 0.08;
    }

    if (job.state === 'reject') {
      job.appear = damp(job.appear, 0, 3, dt);
      job.slab.scale.setScalar(Math.max(job.appear, 0.001));
      if (job.appear < 0.02) {
        this.root.remove(job.slab);
        this.pedestal.remove(job.slab);
        this._finish();
      }
      return;
    }

    if (job.state === 'dissolve') {
      job.appear = damp(job.appear, 0, 2, dt);
      job.slab.scale.setScalar(Math.max(job.appear, 0.001));
      if (job.appear < 0.02) {
        this.pedestal.remove(job.slab);
        this._finish();
      }
      return;
    }

    if (job.state === 'flying') {
      job.travel = Math.min(1, job.travel + dt * 0.85);
      const eased = easeInOutCubic(job.travel);
      job.slab.position.lerpVectors(job.from, job.to, eased);
      job.slab.position.y += Math.sin(eased * Math.PI) * 2.6;   // an arc, not a rail
      job.slab.scale.setScalar(1 - eased * 0.42);
      job.slab.rotation.z = eased * TAU;

      if (job.travel >= 1) {
        job.state = 'locking';
        job.lock = 0;
        this.slabs.push({ slab: job.slab, record: job.record, digest: job.digest });

        // The padlock that seats over the closed cage.
        const padlock = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.26, 0.12), glow(PALETTE.gold, 1.8));
        padlock.position.set(0, -0.75, 0.55);
        job.slab.add(padlock);
        job.padlock = padlock;
      }
      return;
    }

    if (job.state === 'locking') {
      job.lock = Math.min(1, job.lock + dt * 1.6);
      job.slab.rotation.z = damp(job.slab.rotation.z, 0, 8, dt);
      job.padlock.scale.setScalar(0.4 + job.lock * 0.6);
      job.padlock.material.emissiveIntensity = 1.8 + (1 - job.lock) * 3;

      if (job.lock >= 1) {
        this._setReadout(`
          <span class="tag-k ok">locked in vault</span>
          <b>${job.record?.item_number ?? job.record?.original_filename ?? 'stored'}</b>
          <code>${shortHash(job.digest, 16)}</code>`);
        this._finish();
      }
    }
  }
}
