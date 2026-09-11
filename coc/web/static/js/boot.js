/**
 * Application entry point.
 *
 * Wires the DOM chrome to the 3D world: authentication at the vault door,
 * navigation as camera flights between regions, uploads as objects being
 * forged and locked away, and the contextual panel that carries the text the
 * scenes deliberately do not draw.
 */

import { World, THREE } from './world.js';
import { api } from './api.js';
import { GateScene } from './scenes/gate.js';
import { VaultScene } from './scenes/vault.js';
import { ForgeScene } from './scenes/forge.js';
import { WebScene } from './scenes/web.js';
import { LedgerScene } from './scenes/ledger.js';
import { SealScene } from './scenes/seal.js';
import { mountEvidenceMode } from './evidencemode.js';
import { escapeHtml, formatStamp, humanBytes, shortHash } from './util.js';

const dom = {
  canvas:   document.getElementById('stage'),
  labels:   document.getElementById('labels'),
  curtain:  document.getElementById('curtain'),
  ui:       document.getElementById('ui'),
  gate:     document.getElementById('gate'),
  gateUser: document.getElementById('gate-user'),
  gateKey:  document.getElementById('gate-key'),
  gateErr:  document.getElementById('gate-error'),
  gateTurn: document.getElementById('gate-turn'),
  panel:    document.getElementById('panel'),
  nav:      document.getElementById('nav'),
  whoami:   document.getElementById('whoami'),
  signOut:  document.getElementById('sign-out'),
  flat:     document.getElementById('toggle-flat'),
  chip:     document.getElementById('chainstatus'),
  toasts:   document.getElementById('toasts'),
};

const state = {
  examiner: null,
  overview: null,
  cases: [],
  activeCase: null,
  caseDetail: null,
  region: 'gate',
  flat: false,
};

let world = null;
const scenes = {};

/* ------------------------------------------------------------------ *
 * chrome helpers
 * ------------------------------------------------------------------ */

function toast(message, kind = 'info', timeout = 5200) {
  const element = document.createElement('div');
  element.className = `toast ${kind}`;
  element.textContent = message;
  dom.toasts.append(element);
  requestAnimationFrame(() => element.classList.add('in'));
  setTimeout(() => {
    element.classList.remove('in');
    setTimeout(() => element.remove(), 400);
  }, timeout);
}

function setChain(chain) {
  if (!chain) return;
  dom.chip.hidden = false;
  const ok = chain.ok !== false;
  dom.chip.className = `chip ${ok ? 'ok' : 'bad'}`;
  dom.chip.querySelector('.chip-text').textContent = ok
    ? `chain intact · ${(chain.entries_checked ?? 0).toLocaleString()} entries`
    : `chain broken at #${chain.first_broken_seq}`;
}

function setPanel(html) {
  if (!html) {
    dom.panel.hidden = true;
    dom.panel.innerHTML = '';
    return;
  }
  dom.panel.hidden = false;
  dom.panel.innerHTML = html;
}

function markNav(region) {
  for (const button of dom.nav.querySelectorAll('.nav-btn')) {
    button.setAttribute('aria-current', button.dataset.goto === region ? 'true' : 'false');
  }
}

/* ------------------------------------------------------------------ *
 * data loading
 * ------------------------------------------------------------------ */

async function refreshOverview() {
  const payload = await api.overview();
  state.overview = payload.statistics;
  state.cases = payload.cases;
  scenes.vault.setCases(payload.cases);
  scenes.vault.setChain(payload.statistics.chain, payload.statistics);
  setChain(payload.statistics.chain);
  return payload;
}

async function loadCase(caseId) {
  state.activeCase = caseId;
  const [detail, graph] = await Promise.all([api.readCase(caseId), api.caseGraph(caseId)]);
  state.caseDetail = detail;
  scenes.web.setGraph(graph);
  return detail;
}

async function loadLedger() {
  const payload = await api.chain(
    state.activeCase ? { case_id: state.activeCase } : {},
  );
  scenes.ledger.setEntries(payload.entries, payload.verification);
  setChain(payload.verification);
  return payload;
}

/* ------------------------------------------------------------------ *
 * panels
 * ------------------------------------------------------------------ */

function caseOptions(selected) {
  return state.cases.map((record) =>
    `<option value="${record.id}" ${Number(selected) === record.id ? 'selected' : ''}>
       ${escapeHtml(record.case_number)} — ${escapeHtml(record.title)}
     </option>`).join('');
}

/**
 * Admin-only examiner management.
 *
 * Accounts are deactivated, never deleted: every custody entry is attributed to
 * a person, and removing the person would leave entries pointing at nobody.
 */
async function renderExaminers() {
  const host = dom.panel.querySelector('#examiners');
  if (!host) return;

  try {
    const { users } = await api.users();
    host.innerHTML = `
      <h3>Examiners</h3>
      <ul class="people">
        ${users.map((person) => `
          <li class="${person.active ? '' : 'inactive'}">
            <span class="who">
              <b>${escapeHtml(person.full_name || person.username)}</b>
              <span>${escapeHtml(person.username)} · ${escapeHtml(person.role)}${person.active ? '' : ' · inactive'}</span>
            </span>
            ${person.id === state.examiner.id ? '<span class="you">you</span>'
              : `<button class="ghost small" data-user="${person.id}" data-active="${person.active ? '0' : '1'}">
                   ${person.active ? 'Deactivate' : 'Restore'}
                 </button>`}
          </li>`).join('')}
      </ul>
      <form id="add-examiner" class="stack">
        <input name="username" placeholder="Username" required spellcheck="false" autocomplete="off">
        <input name="full_name" placeholder="Full name">
        <select name="role"><option value="examiner">Examiner</option><option value="admin">Admin</option></select>
        <input name="access_key" type="password" placeholder="Access key" required autocomplete="new-password">
        <button type="submit">Add examiner</button>
      </form>`;

    host.querySelectorAll('[data-user]').forEach((button) => {
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          await api.setUserActive(Number(button.dataset.user), button.dataset.active === '1');
          await renderExaminers();
          toast('Examiner access updated and logged', 'ok');
        } catch (error) {
          toast(error.message, 'bad');
          button.disabled = false;
        }
      });
    });

    host.querySelector('#add-examiner')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        const created = await api.addUser(Object.fromEntries(new FormData(event.target)));
        toast(`${created.user.username} can now sign in`, 'ok');
        await renderExaminers();
      } catch (error) {
        toast(error.message, 'bad');
      }
    });
  } catch (error) {
    host.innerHTML = `<p class="hint">Could not load examiners: ${escapeHtml(error.message)}</p>`;
  }
}

function renderVaultPanel() {
  const stats = state.overview ?? {};
  setPanel(`
    <div class="panel-head">
      <h2>Vault</h2>
      <p>${stats.evidence ?? 0} evidence items across ${stats.cases ?? 0} cases · ${humanBytes(stats.vault_bytes ?? 0)} stored</p>
    </div>
    <div class="stats">
      <div class="stat"><b>${stats.open_cases ?? 0}</b><span>open cases</span></div>
      <div class="stat"><b>${stats.evidence ?? 0}</b><span>evidence</span></div>
      <div class="stat"><b>${stats.attachments ?? 0}</b><span>attachments</span></div>
      <div class="stat"><b>${(stats.custody_entries ?? 0).toLocaleString()}</b><span>log entries</span></div>
    </div>
    <p class="hint">Click a case core to open its web. Last verification:
      ${formatStamp(stats.last_verification?.timestamp_utc) || 'never'}
      ${stats.last_verification?.result ? `(${escapeHtml(stats.last_verification.result)})` : ''}
    </p>
    ${state.examiner?.role === 'admin' ? '<div id="examiners"></div>' : ''}
    <form id="new-case" class="stack">
      <h3>Open a case</h3>
      <input name="case_number" placeholder="Case number, e.g. 2026-014" required spellcheck="false">
      <input name="title" placeholder="Title" required>
      <input name="notes" placeholder="Notes (optional)">
      <button type="submit">Open case</button>
    </form>`);

  if (state.examiner?.role === 'admin') renderExaminers();

  dom.panel.querySelector('#new-case')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    try {
      const created = await api.openCase(Object.fromEntries(form));
      toast(`Case ${created.case.case_number} opened`, 'ok');
      await refreshOverview();
      renderVaultPanel();
    } catch (error) {
      toast(error.message, 'bad');
    }
  });
}

function renderForgePanel() {
  setPanel(`
    <div class="panel-head">
      <h2>Intake</h2>
      <p>Files are hashed as they arrive, sealed into the vault, and logged. Drop anywhere on this page.</p>
    </div>
    <form id="intake" class="stack">
      <label>Case
        <select name="case_id" required>${caseOptions(state.activeCase)}</select>
      </label>
      <label>Admit as
        <select name="mode">
          <option value="evidence">Evidence item</option>
          <option value="photo">Photograph</option>
          <option value="document">Document</option>
          <option value="raw">Raw file</option>
          <option value="other">Other attachment</option>
        </select>
      </label>
      <label>Relates to
        <select name="evidence_id">
          <option value="">— the case itself —</option>
          ${(state.caseDetail?.evidence ?? []).map((item) =>
            `<option value="${item.id}">${escapeHtml(item.item_number)} · ${escapeHtml(item.original_filename)}</option>`).join('')}
        </select>
      </label>
      <input name="description" placeholder="Description / caption">
      <input name="source_device" placeholder="Source device (evidence only)">
      <label class="filepick">
        <input type="file" name="file" required>
        <span>Choose a file…</span>
      </label>
      <button type="submit">Admit and seal</button>
    </form>
    <p class="hint">Evidence items get an item number and a SHA-256 recorded at intake.
       Photographs and documents attach to an item as supporting material — both are hashed the same way.</p>`);

  const form = dom.panel.querySelector('#intake');
  form?.addEventListener('change', async (event) => {
    if (event.target.name === 'case_id') {
      await loadCase(Number(event.target.value));
      renderForgePanel();
    }
  });
  form?.addEventListener('submit', (event) => {
    event.preventDefault();
    const data = new FormData(event.target);
    const file = data.get('file');
    if (!file || !file.size) return toast('Choose a file first', 'bad');
    admit(file, {
      caseId: Number(data.get('case_id')),
      mode: String(data.get('mode')),
      evidenceId: data.get('evidence_id') || '',
      description: String(data.get('description') || ''),
      sourceDevice: String(data.get('source_device') || ''),
    });
  });
}

function renderWebPanel(selection) {
  const detail = state.caseDetail;
  if (!detail) {
    return setPanel(`<div class="panel-head"><h2>Case web</h2>
      <p>Open a case from the Vault to see how its evidence, photographs and documents connect.</p></div>`);
  }

  const record = detail.case;
  let focus = '';
  if (selection?.startsWith('evidence:')) {
    const id = Number(selection.split(':')[1]);
    const item = detail.evidence.find((entry) => entry.id === id);
    if (item) {
      const supporting = detail.attachments.filter((a) => a.evidence_id === id);
      focus = `
        <div class="focus">
          <h3>${escapeHtml(item.item_number)}</h3>
          <dl>
            <dt>File</dt><dd>${escapeHtml(item.original_filename)}</dd>
            <dt>Size</dt><dd>${humanBytes(item.byte_size)}</dd>
            <dt>SHA-256</dt><dd><code class="full">${escapeHtml(item.sha256)}</code></dd>
            <dt>Source</dt><dd>${escapeHtml(item.source_device || '—')}</dd>
            <dt>Acquired</dt><dd>${escapeHtml(item.acquisition_date)}</dd>
            ${sourceTimeRows(item)}
            <dt>Holder</dt><dd>${escapeHtml(item.holder ?? '—')}</dd>
          </dl>
          ${supporting.length ? `<h4>Supporting material (${supporting.length})</h4>
            <ul class="thumbs">${supporting.map(attachmentTile).join('')}</ul>` : ''}
          <div class="row">
            <button data-verify="${id}">Re-verify hash</button>
            <a class="button" href="/files/evidence/${id}">Download</a>
          </div>
          ${item.pending_transfer
            ? `<p class="hint warn">Transfer to <b>${escapeHtml(item.pending_transfer.to)}</b> awaiting acceptance.
               <button data-accept="${id}">Accept custody</button></p>`
            : `<form class="row transfer" data-transfer="${id}">
                 <input name="to" placeholder="Transfer to examiner" required spellcheck="false">
                 <button type="submit">Transfer</button>
               </form>`}
        </div>`;
    }
  } else if (selection?.startsWith('attachment:')) {
    const id = Number(selection.split(':')[1]);
    const attachment = detail.attachments.find((entry) => entry.id === id);
    if (attachment) {
      focus = `<div class="focus">
        <h3>${escapeHtml(attachment.original_filename)}</h3>
        <dl>
          <dt>Kind</dt><dd>${escapeHtml(attachment.kind)}</dd>
          <dt>Caption</dt><dd>${escapeHtml(attachment.caption || '—')}</dd>
          <dt>Size</dt><dd>${humanBytes(attachment.byte_size)}</dd>
          <dt>SHA-256</dt><dd><code class="full">${escapeHtml(attachment.sha256)}</code></dd>
          <dt>Uploaded</dt><dd>${formatStamp(attachment.uploaded_at)}</dd>
        </dl>
        <ul class="thumbs">${attachmentTile(attachment)}</ul>
      </div>`;
    }
  }

  setPanel(`
    <div class="panel-head">
      <h2>${escapeHtml(record.case_number)}</h2>
      <p>${escapeHtml(record.title)} · ${detail.evidence.length} items · ${detail.attachments.length} supporting files</p>
    </div>
    ${focus || '<p class="hint">Click any node to inspect it.</p>'}`);

  wireFocusActions();
}

/**
 * The source file's own timestamps.
 *
 * Always labelled with where the value came from. These are claims made by the
 * machine the file arrived from — trivially set with `touch` — so presenting
 * them as plain facts alongside a verified hash would be misleading.
 */
function sourceTimeRows(record) {
  if (!record.source_modified_at && !record.source_created_at) return '';
  const origin = record.source_reported_by === 'browser' ? 'reported by browser' : 'read from source';
  const row = (label, value) => (value
    ? `<dt>${label}</dt><dd>${formatStamp(value)} <span class="claimed">${origin}</span></dd>`
    : '');
  return row('File modified', record.source_modified_at)
       + row('File created', record.source_created_at);
}

function attachmentTile(attachment) {
  const isImage = /^image\/(jpeg|png|gif|webp)$/.test(attachment.mime_type || '');
  return `<li>
    ${isImage
      ? `<img src="/files/attachment/${attachment.id}?inline=1" alt="${escapeHtml(attachment.caption || attachment.original_filename)}" loading="lazy">`
      : `<span class="doc" aria-hidden="true">${escapeHtml((attachment.original_filename.split('.').pop() || 'file').slice(0, 4))}</span>`}
    <a href="/files/attachment/${attachment.id}">${escapeHtml(attachment.original_filename)}</a>
    <code>${shortHash(attachment.sha256, 5)}</code>
  </li>`;
}

function wireFocusActions() {
  dom.panel.querySelector('[data-verify]')?.addEventListener('click', async (event) => {
    const id = Number(event.target.dataset.verify);
    event.target.disabled = true;
    try {
      const { verification } = await api.verify(id);
      toast(
        verification.passed
          ? `${verification.item_number}: hash matches — integrity confirmed`
          : `${verification.item_number}: HASH MISMATCH — ${verification.reason}`,
        verification.passed ? 'ok' : 'bad',
        verification.passed ? 5000 : 12000,
      );
      await loadCase(state.activeCase);
      renderWebPanel(`evidence:${id}`);
    } catch (error) {
      toast(error.message, 'bad');
    }
  });

  dom.panel.querySelector('[data-accept]')?.addEventListener('click', async (event) => {
    const id = Number(event.target.dataset.accept);
    try {
      await api.accept(id);
      toast('Custody accepted and logged', 'ok');
      await loadCase(state.activeCase);
      renderWebPanel(`evidence:${id}`);
    } catch (error) {
      toast(error.message, 'bad');
    }
  });

  dom.panel.querySelector('[data-transfer]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const id = Number(event.target.dataset.transfer);
    const to = new FormData(event.target).get('to');
    try {
      await api.transfer(id, String(to), 'transferred from the case web');
      toast(`Transfer to ${to} recorded — awaiting their acceptance`, 'ok');
      await loadCase(state.activeCase);
      renderWebPanel(`evidence:${id}`);
    } catch (error) {
      toast(error.message, 'bad');
    }
  });
}

function renderLedgerPanel() {
  setPanel(`
    <div class="panel-head">
      <h2>Ledger</h2>
      <p>${state.activeCase ? 'Entries for the selected case.' : 'Every entry in the workspace.'}
         Each block carries the hash of the one before it.</p>
    </div>
    <div class="row">
      <button id="run-verify">Run chain verification</button>
      <button id="scope-toggle" class="ghost">${state.activeCase ? 'Show all cases' : 'Scope to case'}</button>
    </div>
    <p class="hint">The pulse travels from the genesis block. If it stops, it stops exactly at the first
       entry whose contents no longer hash to the value recorded in the chain.</p>`);

  dom.panel.querySelector('#run-verify')?.addEventListener('click', async (event) => {
    event.target.disabled = true;
    try {
      const { verification } = await api.verifyChain();
      scenes.ledger.runVerification(verification);
      setChain(verification);
      scenes.vault.setChain(verification, state.overview);
      toast(
        verification.ok
          ? `Chain intact — ${verification.entries_checked} entries verified`
          : `CHAIN BROKEN at entry ${verification.first_broken_seq}: ${verification.reason}`,
        verification.ok ? 'ok' : 'bad',
        verification.ok ? 5000 : 15000,
      );
    } catch (error) {
      toast(error.message, 'bad');
    } finally {
      event.target.disabled = false;
    }
  });

  dom.panel.querySelector('#scope-toggle')?.addEventListener('click', async () => {
    state.activeCase = state.activeCase ? null : (state.cases[0]?.id ?? null);
    if (state.activeCase) await loadCase(state.activeCase);
    await loadLedger();
    renderLedgerPanel();
  });
}

function renderSealPanel() {
  setPanel(`
    <div class="panel-head">
      <h2>Report</h2>
      <p>A plain, printable chain-of-custody record: evidence inventory with hashes, supporting material,
         the full custody log, and the chain verification result.</p>
    </div>
    <form id="seal-form" class="stack">
      <label>Case <select name="case_id" required>${caseOptions(state.activeCase)}</select></label>
      <button type="submit">Seal and download</button>
    </form>
    <p class="hint">The report pins the chain head at the moment it is generated. A hash chain proves nothing
       inside the log was edited; recording the head in a dated document that has left the system is what
       shows nothing was later removed from the end of it.</p>`);

  dom.panel.querySelector('#seal-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const caseId = Number(new FormData(event.target).get('case_id'));
    const record = state.cases.find((entry) => entry.id === caseId);
    const chain = state.overview?.chain;

    const sealed = scenes.seal.seal(record, chain);
    try {
      await sealed;
      // A same-origin navigation; the response is Content-Disposition: attachment.
      window.location.assign(`/files/case/${caseId}/report.pdf`);
      scenes.seal.finish(`<b>${escapeHtml(record?.case_number ?? '')}</b>
        <span class="tag-k ok">sealed — download started</span>`);
      toast('Report generated and logged', 'ok');
      setTimeout(() => refreshOverview().catch(() => {}), 1200);
    } catch (error) {
      toast(error.message, 'bad');
    }
  });
}

/* ------------------------------------------------------------------ *
 * navigation
 * ------------------------------------------------------------------ */

async function goto(region, { immediate = false } = {}) {
  state.region = region;
  markNav(region);

  try {
    if (region === 'vault') { await refreshOverview(); renderVaultPanel(); }
    if (region === 'forge') { if (!state.cases.length) await refreshOverview(); renderForgePanel(); }
    if (region === 'web') {
      // Arriving with nothing selected should still show something. Falling
      // back to the most recent case beats an empty field and a sentence
      // telling the examiner to go somewhere else first.
      if (!state.activeCase) {
        if (!state.cases.length) await refreshOverview();
        state.activeCase = state.cases[0]?.id ?? null;
      }
      if (state.activeCase) await loadCase(state.activeCase);
      renderWebPanel();
    }
    if (region === 'ledger') {
      if (!state.cases.length) await refreshOverview();
      await loadLedger();
      renderLedgerPanel();
    }
    if (region === 'seal') { if (!state.cases.length) await refreshOverview(); renderSealPanel(); }
  } catch (error) {
    if (error.status === 401) return showGate();
    toast(error.message, 'bad');
  }

  await world.flyTo(region, { immediate });
}

/* ------------------------------------------------------------------ *
 * uploads
 * ------------------------------------------------------------------ */

async function admit(file, options) {
  const caseId = options.caseId ?? state.activeCase ?? state.cases[0]?.id;
  if (!caseId) return toast('Open a case before admitting files', 'bad');

  if (state.region !== 'forge') await goto('forge');

  const handle = scenes.forge.begin(file, options.mode === 'evidence' ? 'evidence' : 'attachment');
  try {
    let payload;
    if (options.mode === 'evidence') {
      payload = await api.ingest(caseId, file, {
        description: options.description,
        source_device: options.sourceDevice,
        // The only trace of the original file's age that survives an upload.
        last_modified: file.lastModified,
      }, handle.progress);
      handle.complete(payload.evidence);
      toast(`${payload.evidence.item_number} admitted · SHA-256 ${shortHash(payload.evidence.sha256, 6)}`, 'ok');
    } else {
      payload = await api.attach(caseId, file, {
        kind: options.mode,
        caption: options.description,
        evidence_id: options.evidenceId,
        last_modified: file.lastModified,
      }, handle.progress);
      handle.complete(payload.attachment);
      toast(`${payload.attachment.original_filename} attached · SHA-256 ${shortHash(payload.attachment.sha256, 6)}`, 'ok');
    }
    state.activeCase = caseId;
    await Promise.all([refreshOverview(), loadCase(caseId)]);
    renderForgePanel();
  } catch (error) {
    handle.fail(error.message);
    toast(error.message, 'bad', 9000);
  }
}

function wireDragAndDrop() {
  let depth = 0;
  const show = (on) => document.body.classList.toggle('dropping', on);

  window.addEventListener('dragenter', (event) => {
    if (!state.examiner) return;
    event.preventDefault();
    depth += 1;
    show(true);
  });
  window.addEventListener('dragover', (event) => event.preventDefault());
  window.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (depth === 0) show(false);
  });
  window.addEventListener('drop', async (event) => {
    event.preventDefault();
    depth = 0;
    show(false);
    if (!state.examiner) return;

    const files = [...(event.dataTransfer?.files ?? [])];
    for (const file of files) {
      // Images arriving by drag are almost always scene photographs; anything
      // else defaults to evidence, which the examiner can change in the panel.
      const mode = /^image\//.test(file.type) ? 'photo' : 'evidence';
      // eslint-disable-next-line no-await-in-loop
      await admit(file, { mode, description: '', sourceDevice: '', evidenceId: '' });
    }
  });
}

/* ------------------------------------------------------------------ *
 * authentication
 * ------------------------------------------------------------------ */

function showGate() {
  state.examiner = null;
  dom.ui.hidden = true;
  dom.gate.hidden = false;
  dom.chip.hidden = true;
  setPanel(null);
  world.flyTo('gate', { duration: 1.4 });
  scenes.gate.enter();
  dom.gateUser.focus();
}

async function enterVault(examiner) {
  state.examiner = examiner;
  dom.gate.hidden = true;
  dom.ui.hidden = false;
  dom.whoami.textContent = `${examiner.full_name || examiner.username} · ${examiner.role}`;
  await goto('vault');
}

function wireGate() {
  let timer = null;
  const recut = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      scenes.gate.setCredential(dom.gateKey.value).catch(() => {});
    }, 90);
  };
  dom.gateKey.addEventListener('input', recut);
  dom.gateKey.addEventListener('paste', () => setTimeout(recut, 0));

  dom.gate.addEventListener('submit', async (event) => {
    event.preventDefault();
    dom.gateErr.hidden = true;
    dom.gateTurn.disabled = true;
    dom.gateTurn.textContent = 'turning…';

    // The key starts moving immediately; the credential is checked while it
    // travels, so the animation is never waiting on a spinner.
    const turning = scenes.gate.unlock();
    try {
      const payload = await api.signIn(dom.gateUser.value, dom.gateKey.value);
      await turning;
      await enterVault(payload.examiner);
      dom.gateKey.value = '';
    } catch (error) {
      scenes.gate.reject();
      dom.gateErr.textContent = error.message || 'Key rejected.';
      dom.gateErr.hidden = false;
    } finally {
      dom.gateTurn.disabled = false;
      dom.gateTurn.textContent = 'Turn key';
    }
  });
}

/* ------------------------------------------------------------------ *
 * boot
 * ------------------------------------------------------------------ */

function webglAvailable() {
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2') || canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

async function boot() {
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const forced = new URLSearchParams(window.location.search).get('view') === 'flat';
  const stored = localStorage.getItem('keeneye.flat') === '1';

  if (!webglAvailable() || forced || stored || reduced) {
    dom.curtain.remove();
    return mountEvidenceMode({ api, reason: webglAvailable() ? 'preference' : 'no-webgl' });
  }

  world = new World(dom.canvas, dom.labels);
  scenes.gate = world.add('gate', new GateScene());
  scenes.vault = world.add('vault', new VaultScene());
  scenes.forge = world.add('forge', new ForgeScene());
  scenes.web = world.add('web', new WebScene());
  scenes.ledger = world.add('ledger', new LedgerScene());
  scenes.seal = world.add('seal', new SealScene());

  world.on('pick', async (hit) => {
    if (!hit) return;
    if (hit.id.startsWith('case:')) {
      const id = Number(hit.id.split(':')[1]);
      try {
        await loadCase(id);
        await goto('web');
        toast(`Opened ${state.caseDetail.case.case_number}`, 'info', 3000);
      } catch (error) {
        toast(error.message, 'bad');
      }
      return;
    }
    if (state.region === 'web') {
      scenes.web.select(hit.id);
      renderWebPanel(hit.id);
    }
  });

  world.on('tier', (tier) => {
    if (tier === 'low') toast('Reduced visual quality to keep the frame rate steady', 'info');
  });

  // A handle for the browser console and for the end-to-end tests. Read-only
  // in spirit: nothing in the application reads it back.
  window.keeneye = { world, scenes, state, goto, api };

  world.flyTo('gate', { immediate: true });
  world.start();

  // Only lift the curtain once a frame has genuinely rendered.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    dom.curtain.classList.add('lift');
    setTimeout(() => dom.curtain.remove(), 700);
    document.body.dataset.ready = '1';
  }));

  wireGate();
  wireDragAndDrop();

  dom.nav.addEventListener('click', (event) => {
    const button = event.target.closest('[data-goto]');
    if (button) goto(button.dataset.goto);
  });

  dom.signOut.addEventListener('click', async () => {
    try { await api.signOut(); } catch { /* the session is going away regardless */ }
    showGate();
  });

  dom.flat.addEventListener('click', () => {
    localStorage.setItem('keeneye.flat', '1');
    window.location.search = '?view=flat';
  });

  try {
    const session = await api.session();
    api.setToken(session.csrf_token);
    if (session.authenticated) await enterVault(session.examiner);
    else showGate();
  } catch {
    showGate();
  }
}

boot().catch((error) => {
  console.error(error);
  document.getElementById('curtain')?.remove();
  document.body.insertAdjacentHTML(
    'afterbegin',
    `<div class="fatal">KEENEYE failed to start: ${escapeHtml(error.message)}.
     The command line interface is unaffected.</div>`,
  );
});
